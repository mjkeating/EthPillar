"""ePBS / Glamsterdam MEV migration helpers.

Two-step operator flow (EthStaker Glamsterdam guidance):

1. **prepare** — copy mev-boost relays (and min-bid where the VC supports it)
   onto the validator client. Keep ``mevboost.service`` and BN sidecar flags so
   pre-Gloas proposals still work.
2. **complete** — stop/disable MEV-Boost and strip BN flags that pointed at
   ``http://127.0.0.1:18550``. Keep the VC builder list from step 1 (Prysm
   ``builders`` entries, Lodestar ``--builder.urls``). Refused unless the VC
   already has that list (or ``--force`` / ``--remote-vc-prepared`` for split
   LXC).

**Split LXC:** when CC/MEV and VC (or Charon+VC) live on different hosts,
``export`` writes a ``.ethpillar.epbs-migration`` file from MEV relays and
``import`` applies that payload as prepare on the VC host. Complete on the
MEV/CC host uses ``--remote-vc-prepared``; complete on the VC/Charon host
only strips Charon ``--builder-api`` when present.

**Obol Charon DVT:** when ``charon.service`` is installed on the same host as
MEV, Charon owns the builder path (``--builder-api`` → MEV-Boost). Prepare
keeps that flag and does **not** write VC relay lists (which would bypass
Charon). Complete is allowed while ``--builder-api`` remains, and strips it
with the BN sidecar. Split-LXC ``import`` is refused while Charon lacks ePBS
support (same gate as ``charonEpbsSupported`` in the TUI).

Support levels:

* ``full`` — Prysm v7.2.0+ (proposer-settings schema v2 ``builders`` list),
  Lodestar v1.47.0+ (VC ``--builder.urls`` / ``--builder.minBid``), and
  Erigon-Caplin v3.7.1+ (``caplin-builders.json`` plus the existing
  ``--caplin.mev-relay-url`` sidecar). Lodestar prepare still probes
  ``lodestar validator --help`` and Caplin prepare still probes
  ``erigon --version`` so older binaries are skipped.
* ``placeholder`` — Lighthouse, Teku, Nimbus, Grandine: no released VC relay
  list; prepare is a documented no-op. Complete is refused without
  ``--force``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from client_requirements import compare_versions, parse_version
from deploy.common import BASE_DATA_DIR, write_service_file
from manage.service_parse import (
    SERVICE_FILES,
    flag_name,
    get_flag_value,
    get_flag_values,
    has_flag,
    normalize_cli_args,
    parse_unit,
    read_text_file,
    rebuild_service_content,
    unit_exists,
)

SIDECAR_MARKERS = ("127.0.0.1:18550", "localhost:18550", "[::1]:18550")
GWEI_PER_ETH = Decimal("1000000000")
PRYSM_SETTINGS_PATH = f"{BASE_DATA_DIR}/prysm_validator/proposer-settings.json"
# Erigon datadir is ``/var/lib/erigon``. Caplin has no multi-relay CLI flag;
# this file is the prepared builder list (see :func:`apply_relays_caplin`).
CAPLIN_BUILDERS_PATH = f"{BASE_DATA_DIR}/erigon/caplin-builders.json"
CAPLIN_BUILDERS_MIN_VERSION = "3.7.1"
MIGRATION_FORMAT = "ethpillar.epbs-migration"
MIGRATION_VERSION = 1
MIGRATION_EXTENSION = ".ethpillar.epbs-migration"


def complete_rollback_hint(fs: "EpbsFilesystem") -> str:
    """Build a complete-step rollback hint that only names installed units.

    Args:
        fs: Filesystem used to test whether consensus/charon/validator/mevboost
            units exist.

    Returns:
        Operator-facing rollback text. Restore/restart lists omit missing units.
    """
    restore: List[str] = []
    if fs.exists(fs.unit_path("consensus")):
        restore.append("consensus.service.bak.epbs.* over consensus.service")
    if _execution_has_caplin(fs):
        restore.append("execution.service.bak.epbs.* over execution.service")
    if fs.exists(fs.unit_path("charon")):
        restore.append("charon.service.bak.epbs.* over charon.service")
    restore_txt = " and ".join(restore) if restore else "the newest *.bak.epbs.* backups"

    steps: List[str] = []
    if fs.exists(fs.unit_path("mevboost")):
        steps.append("sudo systemctl enable --now mevboost")
    steps.append("sudo systemctl daemon-reload")
    restart = [
        name
        for name in ("execution", "consensus", "charon", "validator")
        if fs.exists(fs.unit_path(name))
        and (name != "execution" or _execution_has_caplin(fs))
    ]
    if restart:
        steps.append("sudo systemctl restart " + " ".join(restart))
    return (
        "If you completed too early: restore the newest "
        f"{restore_txt}, then: " + " && ".join(steps)
    )


CHARON_EPBS_NOTE = (
    "Obol Charon has no stable ePBS/Gloas release yet; EthPillar removes "
    "--builder-api on complete (MEV-Boost proxy path). Watch "
    "https://github.com/ObolNetwork/charon/releases for upstream support."
)
CHARON_IMPORT_REFUSED = (
    "Import refused: Charon is installed and does not support ePBS yet "
    "(builder path is Charon's, not the signer VC's). Wait for an Obol "
    "release with Gloas/ePBS support — see "
    "https://github.com/ObolNetwork/charon/releases. Co-located Charon "
    "hosts use prepare/complete via CLI only after upstream support; "
    "split-LXC import will appear under the Charon menu then."
)
COMPLETE_REFUSED = (
    "Complete refused: this validator has no relay list to replace MEV-Boost "
    "(and Charon has no --builder-api). Run prepare first, pass "
    "--remote-vc-prepared if the VC on another host already imported, or pass "
    "--force to stop MEV-Boost and use local EL + P2P bids only."
)

# BN flags whose *value* is a builder/relay URL (strip only sidecar URLs).
BN_URL_FLAGS: Dict[str, Tuple[str, ...]] = {
    "Lighthouse": ("--builder",),
    "Prysm": ("--http-mev-relay",),
    "Teku": ("--builder-endpoint",),
    "Lodestar": ("--builder.urls",),
    "Nimbus": ("--payload-builder-url",),
    "Grandine": ("--builder-url", "--builder-api-url"),
    "Erigon-Caplin": ("--caplin.mev-relay-url",),
}

# BN boolean enable flags that only exist to talk to mev-boost.
BN_BOOL_FLAGS: Dict[str, Tuple[str, ...]] = {
    "Lodestar": ("--builder",),
}

# Prysm v7.2.0 ships Sepolia Gloas without the upstream 200M schedule.
# Operators who want 200M set this themselves; EthPillar does not write it.
SEPOLIA_GLOAS_GAS_LIMIT = "200000000"
SEPOLIA_GAS_LIMIT_NOTE = (
    "Sepolia on Prysm v7.2.0 defaults Gloas proposer gas limit to 60M "
    "(this release has no 200M GAS_LIMIT_SCHEDULE). To propose at 200M, set "
    f"\"gas_limit\": \"{SEPOLIA_GLOAS_GAS_LIMIT}\" on default_config or a "
    "proposer_config key. EthPillar does not write that value. "
    "--suggested-gas-limit only applies to pre-Gloas mev-boost registrations."
)

# v7.2.0 still accepts these builder keys but ignores or warns on them.
# ``relays`` is unread; ``enabled`` is legacy mev-boost content dropped at
# the fork; ``builders_set`` is an internal marker that fails strict load.
_PRYSM_STALE_BUILDER_KEYS = ("enabled", "relays", "builders_set")

SUPPORT_NOTES: Dict[str, str] = {
    "Prysm": (
        "Full: MEV relay URLs go in proposer-settings.json as "
        "default_config.builder.builders (schema v2). A nonempty list opts "
        "into pre-Gloas mev-boost registration and is the Gloas builder list. "
        "Requires Prysm v7.2.0+. Prepare removes deprecated --enable-builder."
    ),
    "Lodestar": (
        "Full: VC flags --builder.urls / --builder.minBid (v1.47.0+). "
        "Prepare writes them only when `lodestar validator --help` lists "
        "--builder.urls."
    ),
    "Lighthouse": (
        "Placeholder: VC has --builder-proposals only; no released relay-list "
        "flag. Prepare is a no-op. Complete is refused without --force "
        "(would stop MEV-Boost with no VC relay replacement)."
    ),
    "Teku": (
        "Placeholder: Staked Builder API REST client (Consensys/teku#11026) is "
        "not wired into proposing. Prepare is a no-op. Complete is refused "
        "without --force."
    ),
    "Nimbus": (
        "Placeholder: VC has --payload-builder=true only. Prepare is a no-op. "
        "Complete is refused without --force."
    ),
    "Grandine": (
        "Placeholder: integrated client; --builder-url takes a single sidecar. "
        "Prepare is a no-op. Complete is refused without --force."
    ),
    "Erigon-Caplin": (
        "Full: Erigon/Caplin v3.7.1+ (first tagged Caplin release with Sepolia "
        "Gloas). Prepare writes caplin-builders.json (builders[].url, "
        "max_execution_payment \"0\", min_bid in Gwei) when `erigon --version` "
        "is at least v3.7.1. --caplin.mev-relay-url stays the pre-Gloas sidecar "
        "until complete, which removes it and switches Caplin to the Gloas "
        "dynamic builder client. v3.7.1 already schedules Sepolia's 200M gas "
        "limit; EthPillar does not set one."
    ),
}


class EpbsError(Exception):
    """Operator-facing ePBS migration error.

    Raised for missing units, invalid proposer-settings JSON, an empty
    relay list, or a refused ``complete`` when the VC has no relays.
    Caught by :func:`main` and printed to stderr.
    """


@dataclass
class RelaysConfig:
    """Relays and min-bid scraped from ``mevboost.service``.

    Attributes:
        urls: Relay URLs from ``-relay`` / ``--relay`` (comma lists expanded).
        min_bid: Optional ``-min-bid`` value in ETH; empty if unset.
        network: Network name parsed from the unit Description, if present.
    """

    urls: List[str]
    min_bid: str = ""
    network: str = ""


@dataclass
class PlanAction:
    """One planned file or systemd change.

    Attributes:
        target: Path or short label (unit file, settings JSON, or summary).
        detail: Human-readable description of the change.
    """

    target: str
    detail: str


@dataclass
class MigrationPlan:
    """Result of prepare/complete (dry-run or applied).

    Attributes:
        command: ``prepare``, ``complete``, or ``import``.
        client: Detected validator (or BN for integrated Grandine). On
            ``complete`` without a local validator client it falls back to the
            consensus client name, then ``mevboost``; on a host with no local
            BN or MEV-Boost it is ``Charon`` or ``unknown``.
        support: ``full`` or ``placeholder``.
        notes: Per-client support blurb from :data:`SUPPORT_NOTES`.
        actions: Files or systemd operations that would change (or did).
        warnings: Operator cautions (do not complete pre-fork, unknown flags).
        services_to_restart: Unit names to bounce after ``--apply``.
        disable_mevboost: True when complete will stop/disable MEV-Boost.
        applied: True after a successful ``--apply`` write.
        rollback_hint: Complete-only operator rollback text (installed units only).
    """

    command: str
    client: str
    support: str  # full | placeholder
    notes: str
    actions: List[PlanAction] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    services_to_restart: List[str] = field(default_factory=list)
    disable_mevboost: bool = False
    applied: bool = False
    rollback_hint: str = ""

    def format_text(self) -> str:
        """Render a TUI/CLI dry-run or apply summary.

        Returns:
            Multi-line text ending with a newline.
        """
        lines = [
            f"ePBS {self.command}: {self.client} ({self.support})",
            self.notes,
            "",
        ]
        if self.actions:
            lines.append("Changes:")
            for action in self.actions:
                lines.append(f"  - {action.target}: {action.detail}")
            lines.append("")
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings:
                lines.append(f"  - {warning}")
            lines.append("")
        if self.disable_mevboost:
            lines.append("MEV-Boost will be stopped and disabled (unit file kept).")
            lines.append("")
        if self.command == "complete" and self.rollback_hint:
            lines.append(self.rollback_hint)
            lines.append("")
        if self.services_to_restart:
            lines.append("Restart after apply: " + ", ".join(self.services_to_restart))
            lines.append("")
        lines.append("Applied." if self.applied else "Dry-run (no files written).")
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class EpbsFilesystem:
    """Injectable IO so unit tests do not need sudo or ``/etc``.

    Production uses :func:`read_text_file` / :func:`write_service_file` and
    sudo copies. Tests swap in plain pathlib writers and a fake mev-boost
    stopper.

    Attributes:
        systemd_dir: Directory containing ``*.service`` files.
        prysm_settings_path: Default Prysm proposer-settings JSON path.
        caplin_builders_path: Default Caplin builder-list JSON path.
        read_text: Read a file; return None if missing.
        exists: True when the path is a regular file.
        write_unit: Optional override for writing systemd units.
        write_data: Optional override for writing JSON/data files.
        stop_disable_mevboost: Optional override for ``systemctl stop/disable``.
        run_help: Optional ``argv -> help text`` probe (Lodestar ``--help``).
        run_version: Optional ``argv -> version text`` probe (``erigon --version``).
    """

    systemd_dir: str = "/etc/systemd/system"
    prysm_settings_path: str = PRYSM_SETTINGS_PATH
    caplin_builders_path: str = CAPLIN_BUILDERS_PATH
    read_text: Callable[[str], Optional[str]] = read_text_file
    exists: Callable[[str], bool] = unit_exists
    write_unit: Optional[Callable[[str, str], None]] = None
    write_data: Optional[Callable[[str, str], None]] = None
    stop_disable_mevboost: Optional[Callable[[], None]] = None
    run_help: Optional[Callable[[Sequence[str]], str]] = None
    run_version: Optional[Callable[[Sequence[str]], str]] = None

    def unit_path(self, key: str) -> str:
        """Return the systemd unit path for *key*.

        Args:
            key: Logical name such as ``validator``, ``consensus``, ``mevboost``.

        Returns:
            Absolute path. Uses :data:`SERVICE_FILES` when ``systemd_dir`` is
            the production directory; otherwise ``{systemd_dir}/{key}.service``.
        """
        if self.systemd_dir == "/etc/systemd/system":
            return SERVICE_FILES[key]
        return os.path.join(self.systemd_dir, f"{key}.service")


def _default_write_unit(path: str, content: str) -> None:
    """Write a systemd unit via the production sudo temp-file helper.

    Args:
        path: Destination unit path (typically under ``/etc/systemd/system``).
        content: Full unit file text.
    """
    write_service_file(content, path, temp_filename="epbs_temp.service")


def _default_write_data(path: str, content: str) -> None:
    """Write proposer-settings JSON (or a backup) as validator-owned 0644.

    Args:
        path: Destination path (for example ``/var/lib/prysm_validator/...``).
        content: File text. A trailing newline is added if missing.
    """
    import tempfile

    directory = os.path.dirname(path)
    if directory:
        subprocess.run(["sudo", "mkdir", "-p", directory], check=True)
    fd, tmp = tempfile.mkstemp(prefix="epbs_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
        subprocess.run(["sudo", "cp", tmp, path], check=True)
        subprocess.run(["sudo", "chmod", "644", path], check=False)
        subprocess.run(["sudo", "chown", "validator:validator", path], check=False)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _default_stop_disable_mevboost() -> None:
    """Stop and disable ``mevboost.service`` without deleting the unit file."""
    subprocess.run(
        ["sudo", "systemctl", "stop", "mevboost"],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["sudo", "systemctl", "disable", "mevboost"],
        check=False,
        capture_output=True,
    )


def _backup(path: str, fs: EpbsFilesystem) -> str:
    """Copy *path* to ``{path}.bak.epbs.<timestamp>`` before overwrite.

    Args:
        path: Existing unit or data file to snapshot.
        fs: IO adapter (uses ``write_unit`` or ``write_data``).

    Returns:
        Backup path that was written.

    Raises:
        EpbsError: If *path* cannot be read.
    """
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup_path = f"{path}.bak.epbs.{stamp}"
    content = fs.read_text(path)
    if content is None:
        raise EpbsError(f"Cannot backup missing file: {path}")
    writer = fs.write_unit if path.endswith(".service") else fs.write_data
    if writer is None:
        writer = _default_write_unit if path.endswith(".service") else _default_write_data
    writer(backup_path, content)
    return backup_path


def upsert_flag(args: Sequence[str], name: str, value: Optional[str] = None) -> List[str]:
    """Set *name* to *value* (or as a boolean flag when *value* is None).

    Existing occurrences of *name* are replaced; duplicates after the first
    are dropped. The flag is appended if it was not present.

    Args:
        args: Normalized CLI tokens from a unit ``ExecStart``.
        name: Flag including leading dashes (for example ``--enable-builder``).
        value: If set, written as ``name=value``; otherwise a bare boolean flag.

    Returns:
        New argument list.
    """
    key = name.lower()
    out: List[str] = []
    found = False
    replacement = f"{name}={value}" if value is not None else name
    for arg in args:
        if flag_name(arg).lower() != key:
            out.append(arg)
            continue
        if found:
            continue
        out.append(replacement)
        found = True
    if not found:
        out.append(replacement)
    return out


def remove_flags(
    args: Sequence[str],
    *names: str,
    value_contains: Optional[str] = None,
) -> List[str]:
    """Drop flags matching *names*, optionally only when the value contains a marker.

    Args:
        args: Normalized CLI tokens.
        *names: Flag names to remove (for example ``--http-mev-relay``).
        value_contains: If set, keep the flag unless this substring appears in
            the value (used so non-sidecar builder URLs survive).

    Returns:
        New argument list with matching flags removed.
    """
    want = {n.lower() for n in names}
    out: List[str] = []
    for arg in args:
        if flag_name(arg).lower() not in want:
            out.append(arg)
            continue
        if value_contains:
            val = arg.split("=", 1)[1] if "=" in arg else ""
            if value_contains not in val:
                out.append(arg)
        # else: drop
    return out


def is_sidecar_url(url: str) -> bool:
    """Return True if *url* points at the local mev-boost listen address.

    Args:
        url: Single URL or a comma-separated list fragment.

    Returns:
        True when any :data:`SIDECAR_MARKERS` substring is present
        (``127.0.0.1:18550``, ``localhost:18550``, ``[::1]:18550``).
    """
    lowered = (url or "").lower()
    return any(marker in lowered for marker in SIDECAR_MARKERS)


def parse_mevboost_relays(content: str) -> RelaysConfig:
    """Extract ``-relay`` URLs and ``-min-bid`` from a mevboost unit.

    Args:
        content: Full ``mevboost.service`` text.

    Returns:
        Parsed relay URLs (comma lists expanded), min-bid, and network.
    """
    unit = parse_unit(content)
    args = normalize_cli_args(unit.exec_args)
    urls = get_flag_values(args, "-relay", "--relay", "-relays", "--relays")
    expanded: List[str] = []
    for item in urls:
        expanded.extend(part.strip() for part in item.split(",") if part.strip())
    return RelaysConfig(
        urls=expanded,
        min_bid=get_flag_value(args, "-min-bid", "--min-bid"),
        network=unit.network,
    )


def _read_required_unit(fs: EpbsFilesystem, key: str) -> Tuple[str, str]:
    """Read a systemd unit that must exist.

    Args:
        fs: IO adapter.
        key: Logical unit name (``validator``, ``consensus``, ``mevboost``).

    Returns:
        Tuple of ``(absolute_path, file_contents)``.

    Raises:
        EpbsError: If the unit is missing or unreadable.
    """
    path = fs.unit_path(key)
    if not fs.exists(path):
        raise EpbsError(f"Missing {key} unit: {path}")
    content = fs.read_text(path)
    if content is None:
        raise EpbsError(f"Unable to read {path}")
    return path, content


def _execution_has_caplin(fs: EpbsFilesystem) -> bool:
    """Return True when ``execution.service`` is integrated Erigon-Caplin.

    Args:
        fs: IO adapter.
    """
    path = fs.unit_path("execution")
    if not fs.exists(path):
        return False
    content = fs.read_text(path) or ""
    client = parse_unit(content).client
    if client in ("Erigon-Caplin", "Caplin"):
        return True
    return "caplin" in content.lower()


def _bn_service_key(bn_name: str) -> str:
    """Return the systemd unit key that holds this beacon node's sidecar flag.

    Args:
        bn_name: Beacon-node client name from :func:`detect_clients`.

    Returns:
        ``execution`` for integrated Caplin, otherwise ``consensus``.
    """
    if bn_name == "Erigon-Caplin":
        return "execution"
    return "consensus"


def detect_clients(fs: EpbsFilesystem) -> Tuple[str, str, str]:
    """Detect validator and beacon-node client names from systemd units.

    Grandine with ``keystore-dir`` on the consensus unit is treated as
    integrated (no separate ``validator.service``). Erigon-Caplin with no
    separate validator unit is ``integrated_caplin`` (the builder list lives
    on ``execution.service``'s datadir).

    Args:
        fs: IO adapter.

    Returns:
        ``(vc_name, bn_name, validator_mode)`` where *validator_mode* is
        ``separate``, ``integrated_grandine``, ``integrated_caplin``, or
        ``none``. Names are empty strings when the corresponding unit is
        absent.
    """
    bn_name = ""
    consensus_path = fs.unit_path("consensus")
    if fs.exists(consensus_path):
        content = fs.read_text(consensus_path) or ""
        bn_name = parse_unit(content).client
        if "keystore-dir" in content:
            return "Grandine", bn_name or "Grandine", "integrated_grandine"

    caplin = _execution_has_caplin(fs)
    if caplin and not bn_name:
        bn_name = "Erigon-Caplin"

    vc_path = fs.unit_path("validator")
    if fs.exists(vc_path):
        content = fs.read_text(vc_path) or ""
        return parse_unit(content).client, bn_name, "separate"

    if caplin and bn_name == "Erigon-Caplin":
        return "Erigon-Caplin", "Erigon-Caplin", "integrated_caplin"

    return "", bn_name, "none"


def support_level(client: str) -> str:
    """Return the VC relay-list support level for *client*.

    Args:
        client: Validator client name (``Prysm``, ``Lodestar``, …).

    Returns:
        ``full`` (Prysm, Lodestar, Erigon-Caplin) or ``placeholder``.
        The MEV-Boost TUI (``epbsTuiSupported`` in ``functions.sh``) mirrors
        this for local validators (shown for Prysm, Lodestar, and integrated
        Caplin), but is always shown on MEV hosts without a local validator
        (split LXC) and hidden when Charon is enabled.
    """
    if client in ("Prysm", "Lodestar", "Erigon-Caplin"):
        return "full"
    return "placeholder"


def _rebuild_unit(content: str, args: Sequence[str]) -> str:
    """Rewrite ``ExecStart`` in *content* using *args*.

    Args:
        content: Original unit file text.
        args: Replacement ExecStart tokens (binary plus flags).

    Returns:
        Full unit file with the ExecStart block replaced.
    """
    unit = parse_unit(content)
    return rebuild_service_content(
        content, unit.exec_start_index, unit.exec_start_end_index, list(args)
    )


def _write_unit_if_changed(
    fs: EpbsFilesystem,
    path: str,
    old_content: str,
    new_content: str,
    apply: bool,
) -> bool:
    """Write *new_content* when it differs and *apply* is True.

    Args:
        fs: IO adapter.
        path: Unit file path.
        old_content: Current on-disk text.
        new_content: Proposed text.
        apply: If False, still report whether a change exists without writing.

    Returns:
        True if *new_content* differs from *old_content* (a write happened
        only when *apply* is True).
    """
    if old_content == new_content:
        return False
    if apply:
        _backup(path, fs)
        writer = fs.write_unit or _default_write_unit
        writer(path, new_content)
    return True


def _prysm_builder_config(relays: RelaysConfig) -> dict:
    """Build Prysm v7.2.0 ``default_config.builder`` (schema version 2).

    A nonempty ``builders`` list opts the key into pre-Gloas mev-boost
    registration and is the post-Gloas direct-builder list. Each entry is a
    ``BuilderEntry`` with ``url`` set to the MEV-Boost relay URL. ``auth_data``
    is omitted so Prysm signs the UTF-8 bytes of that URL (its default).
    ``enabled`` and ``relays`` are legacy and are not written.

    ``max_execution_payment`` ``"0"`` is an explicit trustless-only cap: the
    collateral-backed bid value still counts, and a builder's promised
    execution-layer payment does not. Unset is the same effective cap but
    logs a warning.

    ``min_bid`` is MEV-Boost ``-min-bid`` converted from ETH to integer Gwei,
    matching Prysm's ``BuilderConfig.min_bid`` (Gwei).

    Args:
        relays: Relay URLs and optional MEV-Boost min-bid (ETH).

    Returns:
        Builder object with ``builders``, ``max_execution_payment``, and
        ``min_bid`` when MEV-Boost set a min-bid.
    """
    config: dict = {
        "builders": [{"url": url} for url in relays.urls],
        "max_execution_payment": "0",
    }
    if relays.min_bid:
        config["min_bid"] = eth_min_bid_to_gwei(relays.min_bid)
    return config


def _prysm_explicit_gas_limit(data: dict) -> bool:
    """Return True when any option already sets a non-zero ``gas_limit``.

    Args:
        data: Proposer-settings object.

    Returns:
        True if ``default_config`` or any ``proposer_config`` entry has a
        gas limit other than empty or ``0``.
    """

    def _set(option: object) -> bool:
        if not isinstance(option, dict):
            return False
        value = option.get("gas_limit")
        if value is None:
            return False
        return str(value).strip() not in ("", "0")

    if _set(data.get("default_config")):
        return True
    proposer = data.get("proposer_config")
    if not isinstance(proposer, dict):
        return False
    return any(_set(opt) for opt in proposer.values())


def _prysm_builder_dict(data: dict) -> dict:
    """Return ``default_config.builder`` when it is an object.

    Args:
        data: Proposer-settings object.

    Returns:
        The builder object, or an empty dict when absent or the wrong type.
    """
    default = data.get("default_config")
    if not isinstance(default, dict):
        return {}
    builder = default.get("builder")
    return builder if isinstance(builder, dict) else {}


def prysm_has_builder_list(data: dict) -> bool:
    """Return True when default builders include a non-sidecar URL.

    Prysm v7.2.0 ignores legacy ``builder.relays``. Only a nonempty
    ``builders`` list counts as prepared. An explicit empty list is an opt-out.

    Args:
        data: Proposer-settings object.

    Returns:
        True when some ``builders[].url`` is set and is not the local sidecar.
    """
    entries = _prysm_builder_dict(data).get("builders")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if isinstance(url, str) and url.strip() and not is_sidecar_url(url):
            return True
    return False


def prysm_legacy_relays_only(data: dict) -> bool:
    """Return True when the file still has ``builder.relays`` and no builders.

    Args:
        data: Proposer-settings object.

    Returns:
        True for a v7.1-style relay list that v7.2.0 will ignore.
    """
    relays = _prysm_builder_dict(data).get("relays")
    has_legacy = isinstance(relays, list) and any(str(url).strip() for url in relays)
    return has_legacy and not prysm_has_builder_list(data)


def _prysm_flag_action(old_unit: str, new_unit: str) -> str:
    """Describe Prysm VC flag edits between two unit files.

    Args:
        old_unit: Unit text before prepare.
        new_unit: Unit text after prepare.

    Returns:
        Short action detail for the migration plan.
    """
    old_args = normalize_cli_args(parse_unit(old_unit).exec_args)
    new_args = normalize_cli_args(parse_unit(new_unit).exec_args)
    parts: List[str] = []
    if get_flag_value(old_args, "--proposer-settings-file") != get_flag_value(
        new_args, "--proposer-settings-file"
    ):
        parts.append("set --proposer-settings-file")
    if has_flag(old_args, "--enable-builder") and not has_flag(new_args, "--enable-builder"):
        parts.append("remove deprecated --enable-builder")
    return "; ".join(parts) if parts else "update validator flags"


def apply_relays_prysm(
    vc_content: str,
    relays: RelaysConfig,
    existing_settings: Optional[str],
    settings_path: str = PRYSM_SETTINGS_PATH,
) -> Tuple[str, str, str]:
    """Merge mev-boost relays into Prysm v7.2.0 proposer settings and VC flags.

    Sets schema version 2, writes ``default_config.builder.builders`` from the
    relay URLs, copies ``--suggested-fee-recipient`` into ``fee_recipient``
    when missing, sets ``--proposer-settings-file``, and removes deprecated
    ``--enable-builder``. Does not write ``gas_limit`` (including the Sepolia
    200M value) or ``--suggested-gas-limit``.

    Existing ``proposer_config`` entries, graffiti, and option-level gas limits
    are left in place. Legacy ``builder.enabled``, ``builder.relays``, and
    ``builders_set`` are removed from ``default_config.builder``.

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays scraped from MEV-Boost.
        existing_settings: Current proposer-settings JSON, or None.
        settings_path: Default JSON path if the VC flag is absent.

    Returns:
        ``(new_vc_unit, proposer_settings_json, settings_path_used)``.

    Raises:
        EpbsError: If existing settings are invalid JSON or the wrong shape.
    """
    unit = parse_unit(vc_content)
    args = normalize_cli_args(unit.exec_args)
    fee = get_flag_value(args, "--suggested-fee-recipient")
    settings_path = get_flag_value(args, "--proposer-settings-file") or settings_path

    data: dict
    if existing_settings:
        try:
            data = json.loads(existing_settings)
        except json.JSONDecodeError as exc:
            raise EpbsError(f"Invalid proposer-settings JSON: {exc}") from exc
    else:
        data = {}

    if not isinstance(data, dict):
        raise EpbsError("proposer-settings.json must be a JSON object")

    data["version"] = 2
    default = data.setdefault("default_config", {})
    if not isinstance(default, dict):
        raise EpbsError("default_config must be an object")
    if fee and not default.get("fee_recipient"):
        default["fee_recipient"] = fee
    builder = default.setdefault("builder", {})
    if not isinstance(builder, dict):
        raise EpbsError("default_config.builder must be an object")
    for stale in _PRYSM_STALE_BUILDER_KEYS:
        builder.pop(stale, None)
    # Mirror the current MEV-Boost min-bid; drop a stale value when unset.
    builder.pop("min_bid", None)
    builder.update(_prysm_builder_config(relays))

    args = remove_flags(args, "--enable-builder")
    args = upsert_flag(args, "--proposer-settings-file", settings_path)
    new_unit = _rebuild_unit(vc_content, args)
    settings_json = json.dumps(data, indent=2) + "\n"
    return new_unit, settings_json, settings_path


def eth_min_bid_to_gwei(eth: str) -> str:
    """Convert MEV-Boost ``-min-bid`` (ETH) to Lodestar ``--builder.minBid`` (Gwei).

    Lodestar ``parseBuilderMinBid`` rejects decimals. Flashbots MEV-Boost
    ``-min-bid`` is ETH (EthPillar default ``0.006`` → ``6000000`` Gwei).

    Args:
        eth: Non-negative ETH amount as a decimal string.

    Returns:
        Integer Gwei string with no decimal point.

    Raises:
        EpbsError: If *eth* is not a non-negative number.
    """
    try:
        value = Decimal(str(eth).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise EpbsError(f"Cannot convert MEV-Boost min-bid {eth!r} to Gwei") from exc
    if value < 0:
        raise EpbsError(f"MEV-Boost min-bid must be non-negative, got {eth!r}")
    gwei = (value * GWEI_PER_ETH).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(int(gwei))


def apply_relays_lodestar(vc_content: str, relays: RelaysConfig) -> str:
    """Add Lodestar VC builder URL / min-bid flags (v1.47.0+).

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays and optional min-bid from MEV-Boost. ``min_bid`` is ETH
            and is converted to integer Gwei for ``--builder.minBid``.

    Returns:
        Unit text with ``--builder``, ``--builder.urls``, and optional
        ``--builder.minBid``. Older Lodestar may reject these flags.
    """
    unit = parse_unit(vc_content)
    args = normalize_cli_args(unit.exec_args)
    args = upsert_flag(args, "--builder")
    args = upsert_flag(args, "--builder.urls", ",".join(relays.urls))
    if relays.min_bid:
        args = upsert_flag(args, "--builder.minBid", eth_min_bid_to_gwei(relays.min_bid))
    return _rebuild_unit(vc_content, args)


def _command_help(fs: EpbsFilesystem, argv: Sequence[str]) -> str:
    """Return ``--help`` text for *argv*, or empty string on failure.

    Args:
        fs: IO adapter; ``run_help`` short-circuits subprocess in tests.
        argv: Command line including the binary and trailing ``--help``.

    Returns:
        Combined stdout and stderr, or ``""`` if the binary cannot be run.
    """
    if fs.run_help is not None:
        return fs.run_help(argv)
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "") + (result.stderr or "")


def lodestar_has_builder_urls_flag(fs: EpbsFilesystem, vc_content: str) -> bool:
    """True when the Lodestar VC binary documents ``--builder.urls``.

    Args:
        fs: IO adapter used to run ``lodestar validator --help``.
        vc_content: Current ``validator.service`` text (binary + subcommand).

    Returns:
        True if help text contains ``--builder.urls`` (Lodestar v1.47.0+).
    """
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    if not args:
        return False
    # First ExecStart token is the binary, or "binary subcommand" (Lodestar).
    help_cmd: List[str] = list(args[0].split())
    help_cmd.append("--help")
    return "--builder.urls" in _command_help(fs, help_cmd)


def _version_meets_floor(text: str, minimum: str) -> bool:
    """Return True when the first ``X.Y.Z`` in *text* is at least *minimum*.

    Prerelease suffixes do not lower the binary: ``3.7.1-rc.0`` meets floor
    ``3.7.1``. The base triple is compared with
    :func:`client_requirements.compare_versions`.

    Args:
        text: Version command output or a tag.
        minimum: Floor such as ``3.7.1``.

    Returns:
        False when either side has no ``X.Y.Z``.
    """
    def base(raw: str) -> Optional[str]:
        match = re.search(r"(\d+\.\d+\.\d+)", raw or "")
        if not match:
            return None
        major, minor, patch, _prerelease = parse_version(match.group(1))
        return f"{major}.{minor}.{patch}"

    got = base(text)
    need = base(minimum)
    if got is None or need is None:
        return False
    return compare_versions(got, need) >= 0


def _binary_version(fs: EpbsFilesystem, argv: Sequence[str]) -> str:
    """Return version text for *argv*, or empty string on failure.

    Args:
        fs: IO adapter; ``run_version`` short-circuits subprocess in tests.
        argv: Command line, typically ``[binary, "--version"]``.
    """
    if fs.run_version is not None:
        return fs.run_version(argv)
    return _command_help(fs, argv)


def caplin_supports_epbs(fs: EpbsFilesystem, unit_content: str) -> bool:
    """True when the Erigon binary is at least v3.7.1 (Sepolia Gloas Caplin).

    Args:
        fs: IO adapter used to run ``erigon --version``.
        unit_content: ``execution.service`` text (binary path).

    Returns:
        True for Erigon v3.7.1 or newer. Older or unreadable binaries are False
        so prepare does not pretend a builder list the release cannot use.
    """
    args = normalize_cli_args(parse_unit(unit_content).exec_args)
    if not args:
        return False
    binary = args[0].split()[0]
    text = _binary_version(fs, [binary, "--version"])
    return _version_meets_floor(text, CAPLIN_BUILDERS_MIN_VERSION)


def apply_relays_caplin(relays: RelaysConfig, existing: Optional[str]) -> str:
    """Build Caplin's prepared builder-list JSON.

    Erigon v3.7.1 has a single ``--caplin.mev-relay-url`` (the pre-Gloas
    mev-boost sidecar) and no multi-relay flag. Post-Gloas, Caplin's dynamic
    builder client uses URLs a validator sends on each block-production
    request. This file is the relay list EthPillar records so complete can
    drop the sidecar without forgetting which relays were in use.
    ``max_execution_payment`` ``"0"`` is trustless-only, matching Prysm.
    ``min_bid`` is MEV-Boost ``-min-bid`` in integer Gwei when set.

    Args:
        relays: Relay URLs and optional MEV-Boost min-bid (ETH).
        existing: Current JSON, or None.

    Returns:
        Canonical JSON text.

    Raises:
        EpbsError: If *existing* is not a JSON object.
    """
    data: dict = {}
    if existing:
        try:
            loaded = json.loads(existing)
        except json.JSONDecodeError as exc:
            raise EpbsError(f"Invalid caplin-builders.json: {exc}") from exc
        if not isinstance(loaded, dict):
            raise EpbsError("caplin-builders.json must be a JSON object")
        data = loaded
    seen = set()
    builders: List[dict] = []
    for url in relays.urls:
        if url in seen:
            continue
        seen.add(url)
        builders.append({"url": url, "max_execution_payment": "0"})
    out: dict = {"version": 1, "builders": builders}
    if relays.min_bid:
        out["min_bid"] = eth_min_bid_to_gwei(relays.min_bid)
    known = {"version", "builders", "min_bid"}
    for key, value in data.items():
        if key not in known:
            out[key] = value
    return json.dumps(out, indent=2) + "\n"


def caplin_has_builder_list(data: dict) -> bool:
    """Return True when Caplin builders include a non-sidecar URL.

    Args:
        data: Parsed ``caplin-builders.json`` object.
    """
    entries = data.get("builders")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if isinstance(url, str) and url.strip() and not is_sidecar_url(url):
            return True
    return False


def _load_caplin_builders(fs: EpbsFilesystem) -> Optional[dict]:
    """Load ``caplin-builders.json``, or None when missing or invalid.

    Args:
        fs: IO adapter.
    """
    raw = fs.read_text(fs.caplin_builders_path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_execution_data(path: str, content: str) -> None:
    """Write a data file under the Erigon datadir as the execution user.

    Args:
        path: Destination path.
        content: File text. A trailing newline is added if missing.
    """
    import tempfile

    directory = os.path.dirname(path)
    if directory:
        subprocess.run(["sudo", "mkdir", "-p", directory], check=True)
        subprocess.run(["sudo", "chown", "execution:execution", directory], check=False)
    fd, tmp = tempfile.mkstemp(prefix="epbs_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
        subprocess.run(["sudo", "cp", tmp, path], check=True)
        subprocess.run(["sudo", "chmod", "644", path], check=False)
        subprocess.run(["sudo", "chown", "execution:execution", path], check=False)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def apply_relays_placeholder(client: str) -> str:
    """Return a planned-flag blurb; do not mutate units.

    Args:
        client: Placeholder VC name (Lighthouse, Teku, Nimbus, Grandine).

    Returns:
        Human-readable description of the unreleased relay-list surface.
    """
    planned = {
        "Lighthouse": "--builder-relays=<urls> (not shipped; VC still --builder-proposals)",
        "Teku": "--validators-builder-relays=<urls> (not shipped; #11026 REST client unwired)",
        "Nimbus": "--payload-builder-relays=<urls> (not shipped; VC still --payload-builder=true)",
        "Grandine": "multi --builder-url list (not shipped; single --builder-url today)",
    }
    return planned.get(client, "no VC relay-list flag shipped")


def strip_bn_sidecar(bn_content: str, bn_client: str) -> str:
    """Remove BN flags whose value is the local mev-boost listen address.

    Non-sidecar builder URLs are kept. Lodestar's boolean ``--builder`` is
    dropped only when no builder URL remains.

    Args:
        bn_content: Current ``consensus.service`` text.
        bn_client: Beacon-node client name (keys :data:`BN_URL_FLAGS`).

    Returns:
        Unit text with sidecar URL flags removed.
    """
    unit = parse_unit(bn_content)
    args = normalize_cli_args(unit.exec_args)
    for flag in BN_URL_FLAGS.get(bn_client, ()):
        kept: List[str] = []
        key = flag.lower()
        for arg in args:
            if flag_name(arg).lower() != key:
                kept.append(arg)
                continue
            val = arg.split("=", 1)[1] if "=" in arg else ""
            urls = [part.strip() for part in val.split(",") if part.strip()]
            remaining = [u for u in urls if not is_sidecar_url(u)]
            if not remaining:
                continue
            kept.append(f"{flag}={','.join(remaining)}")
        args = kept
    for flag in BN_BOOL_FLAGS.get(bn_client, ()):
        # Only drop the BN builder-enable flag when no builder URL remains.
        url_flags = BN_URL_FLAGS.get(bn_client, ())
        still_has_url = any(
            flag_name(a).lower() == name.lower() for a in args for name in url_flags
        )
        if not still_has_url:
            args = remove_flags(args, flag)
    return _rebuild_unit(bn_content, args)


def charon_installed(fs: EpbsFilesystem) -> bool:
    """Return True when ``charon.service`` exists.

    Args:
        fs: IO adapter used to locate systemd units.
    """
    return fs.exists(fs.unit_path("charon"))


def charon_epbs_supported(fs: Optional[EpbsFilesystem] = None) -> bool:
    """Return True when Charon has shipped Gloas/ePBS support.

    Mirrors ``charonEpbsSupported`` in ``functions.sh``. Stub: always False
    until Obol publishes a stable ePBS release
    (https://github.com/ObolNetwork/charon/releases). When True, split-LXC
    import is allowed on Charon hosts and the Charon TUI menu owns it.

    Args:
        fs: Unused today; reserved for a future version-gate probe.
    """
    _ = fs
    return False


def charon_has_builder_api(charon_content: str) -> bool:
    """Return True when ``charon.service`` ExecStart includes ``--builder-api``.

    Args:
        charon_content: Full ``charon.service`` unit file body.
    """
    args = normalize_cli_args(parse_unit(charon_content).exec_args)
    return has_flag(args, "--builder-api")


def charon_ready_for_complete(fs: EpbsFilesystem) -> bool:
    """True when Charon still has ``--builder-api`` (prepare kept the MEV path).

    Args:
        fs: IO adapter used to read ``charon.service``.
    """
    path = fs.unit_path("charon")
    if not fs.exists(path):
        return False
    return charon_has_builder_api(fs.read_text(path) or "")


def strip_charon_builder_api(charon_content: str) -> str:
    """Remove ``--builder-api`` from Charon (MEV-Boost builder proxy until Gloas).

    Args:
        charon_content: Full ``charon.service`` unit file body.

    Returns:
        Updated unit content with ``--builder-api`` removed from ``ExecStart``.
    """
    unit = parse_unit(charon_content)
    args = normalize_cli_args(unit.exec_args)
    args = remove_flags(args, "--builder-api")
    return _rebuild_unit(charon_content, args)


def _complete_charon_builder_api(
    fs: EpbsFilesystem,
    plan: MigrationPlan,
    apply: bool,
) -> None:
    """Strip Charon ``--builder-api`` on ePBS complete when ``charon.service`` exists.

    Args:
        fs: Filesystem abstraction (production or test double).
        plan: Migration plan to append actions to.
        apply: When False, record planned changes only.
    """
    charon_path = fs.unit_path("charon")
    if not fs.exists(charon_path):
        return
    ch_content = fs.read_text(charon_path) or ""
    if not charon_has_builder_api(ch_content):
        plan.actions.append(
            PlanAction(charon_path, "charon: no --builder-api present")
        )
        return
    new_ch = strip_charon_builder_api(ch_content)
    if _write_unit_if_changed(fs, charon_path, ch_content, new_ch, apply):
        plan.actions.append(
            PlanAction(charon_path, "remove --builder-api (MEV-Boost proxy path)")
        )
        if "charon" not in plan.services_to_restart:
            plan.services_to_restart.append("charon")
    plan.warnings.append(CHARON_EPBS_NOTE)
    plan.warnings.append(
        "After complete, restart order: consensus → charon → validator."
    )


def _load_relays(fs: EpbsFilesystem) -> RelaysConfig:
    """Read and validate relays from ``mevboost.service``.

    Args:
        fs: IO adapter.

    Returns:
        Parsed :class:`RelaysConfig` with a non-empty URL list.

    Raises:
        EpbsError: If the unit is missing, unreadable, or has no ``-relay`` URLs.
    """
    path = fs.unit_path("mevboost")
    if not fs.exists(path):
        raise EpbsError(
            "mevboost.service not found. Install MEV-Boost first, or complete "
            "migration only after relays are already on the VC."
        )
    content = fs.read_text(path)
    if content is None:
        raise EpbsError(f"Unable to read {path}")
    cfg = parse_mevboost_relays(content)
    if not cfg.urls:
        raise EpbsError("No -relay URLs found in mevboost.service")
    return cfg


def default_migration_filename(hostname: Optional[str] = None) -> str:
    """Build ``{hostname}-{YYYYMMDD-HHMMSS}.ethpillar.epbs-migration``.

    Args:
        hostname: Override for tests; defaults to ``socket.gethostname()``.
    """
    host = hostname or socket.gethostname() or "host"
    # Keep filenames path-safe.
    host = "".join(c if c.isalnum() or c in "-._" else "-" for c in host)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{host}-{stamp}{MIGRATION_EXTENSION}"


def migration_payload(relays: RelaysConfig, hostname: Optional[str] = None) -> dict:
    """Build the JSON object written by :func:`export_migration`.

    Args:
        relays: Relays scraped from MEV-Boost.
        hostname: Override for tests; defaults to ``socket.gethostname()``.
    """
    return {
        "format": MIGRATION_FORMAT,
        "version": MIGRATION_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hostname": hostname or socket.gethostname() or "host",
        "network": relays.network or "",
        "relays": list(relays.urls),
        "min_bid": relays.min_bid or "",
    }


def load_migration_file(path: str, read_text: Optional[Callable[[str], Optional[str]]] = None) -> RelaysConfig:
    """Parse and validate a ``.ethpillar.epbs-migration`` file.

    Args:
        path: Path to the migration JSON file.
        read_text: Optional reader (tests); defaults to open().

    Returns:
        :class:`RelaysConfig` from the file payload.

    Raises:
        EpbsError: If the file is missing, invalid, or has no relays.
    """
    reader = read_text or _read_plain
    raw = reader(path)
    if raw is None:
        raise EpbsError(f"Migration file not found: {path}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EpbsError(f"Invalid migration JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EpbsError("Migration file must be a JSON object")
    if data.get("format") != MIGRATION_FORMAT:
        raise EpbsError(
            f"Unknown migration format {data.get('format')!r}; "
            f"expected {MIGRATION_FORMAT!r}"
        )
    version = data.get("version")
    if version != MIGRATION_VERSION:
        raise EpbsError(
            f"Unsupported migration version {version!r}; "
            f"expected {MIGRATION_VERSION}"
        )
    relays = data.get("relays")
    if not isinstance(relays, list) or not relays:
        raise EpbsError("Migration file has no relays")
    urls = [str(u).strip() for u in relays if str(u).strip()]
    if not urls:
        raise EpbsError("Migration file has no relays")
    min_bid = data.get("min_bid") or ""
    network = data.get("network") or ""
    if not isinstance(min_bid, str):
        min_bid = str(min_bid)
    if not isinstance(network, str):
        network = str(network)
    return RelaysConfig(urls=urls, min_bid=min_bid, network=network)


def export_migration(
    fs: Optional[EpbsFilesystem] = None,
    output: Optional[str] = None,
    hostname: Optional[str] = None,
) -> Tuple[str, dict]:
    """Write MEV-Boost relays to a portable ``.ethpillar.epbs-migration`` file.

    Args:
        fs: IO adapter; production defaults if omitted.
        output: Destination path. When omitted, writes
            ``{cwd}/{hostname}-{stamp}.ethpillar.epbs-migration``.
        hostname: Override embedded hostname / default filename host.

    Returns:
        ``(path_written, payload_dict)``.

    Raises:
        EpbsError: If MEV-Boost relays cannot be loaded.
        OSError: If the output directory or file cannot be written.
    """
    fs = fs or EpbsFilesystem()
    relays = _load_relays(fs)
    payload = migration_payload(relays, hostname=hostname)
    path = output or os.path.join(os.getcwd(), default_migration_filename(hostname))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    writer = fs.write_data or _write_plain
    writer(path, text)
    return path, payload


def _apply_vc_relays(
    fs: EpbsFilesystem,
    relays: RelaysConfig,
    apply: bool,
    plan: MigrationPlan,
    vc_name: str,
    mode: str,
    relays_source: str,
) -> None:
    """Write relay config onto the local validator client.

    Shared by :func:`prepare` (after loading MEV) and :func:`import_migration`.

    Args:
        fs: IO adapter.
        relays: Relay URLs and optional min-bid.
        apply: If True, write units/settings.
        plan: Plan to append actions/warnings/restarts.
        vc_name: Detected validator client name.
        mode: ``separate``, ``integrated_grandine``, or ``integrated_caplin``.
        relays_source: Short label for the plan action (e.g. ``mevboost.service``).
    """
    if relays.min_bid:
        plan.actions.append(PlanAction("mevboost min-bid", relays.min_bid))
    plan.actions.append(
        PlanAction("relays", f"{len(relays.urls)} URL(s) from {relays_source}")
    )

    if mode == "integrated_grandine":
        vc_key = "consensus"
    elif mode == "integrated_caplin" or vc_name == "Erigon-Caplin":
        vc_key = "execution"
    else:
        vc_key = "validator"
    vc_path, vc_content = _read_required_unit(fs, vc_key)

    if vc_name == "Prysm":
        vc_args = normalize_cli_args(parse_unit(vc_content).exec_args)
        settings_path = (
            get_flag_value(vc_args, "--proposer-settings-file") or fs.prysm_settings_path
        )
        existing = fs.read_text(settings_path)
        new_vc, settings_json, settings_path = apply_relays_prysm(
            vc_content, relays, existing, settings_path=settings_path
        )
        changed_vc = new_vc != vc_content
        changed_json = (existing or "") != settings_json
        if changed_vc:
            plan.actions.append(PlanAction(vc_path, _prysm_flag_action(vc_content, new_vc)))
        plan.actions.append(
            PlanAction(settings_path, "write default_config.builder.builders (schema v2)")
        )
        if relays.network == "sepolia":
            try:
                written = json.loads(settings_json)
            except json.JSONDecodeError:
                written = {}
            if isinstance(written, dict) and not _prysm_explicit_gas_limit(written):
                plan.warnings.append(SEPOLIA_GAS_LIMIT_NOTE)
        if apply:
            if changed_vc:
                _write_unit_if_changed(fs, vc_path, vc_content, new_vc, True)
            writer = fs.write_data or _default_write_data
            if existing:
                _backup(settings_path, fs)
            writer(settings_path, settings_json)
        if changed_vc or changed_json:
            plan.services_to_restart.append("validator")
        else:
            plan.warnings.append("Prysm VC already has these relays; nothing to change.")
    elif vc_name == "Lodestar":
        if not lodestar_has_builder_urls_flag(fs, vc_content):
            plan.actions.append(
                PlanAction(
                    "Lodestar VC",
                    "skipped: binary --help has no --builder.urls (need v1.47.0+)",
                )
            )
            plan.warnings.append(
                "Prepare: no-op on this Lodestar build — Complete will stop "
                "MEV-Boost without a VC relay replacement. Install Lodestar "
                "v1.47.0 or later."
            )
        else:
            new_vc = apply_relays_lodestar(vc_content, relays)
            if _write_unit_if_changed(fs, vc_path, vc_content, new_vc, apply):
                plan.actions.append(
                    PlanAction(
                        vc_path,
                        "add --builder --builder.urls --builder.minBid",
                    )
                )
                plan.services_to_restart.append("validator")
            else:
                plan.warnings.append(
                    "Lodestar VC already has builder.urls; nothing to change."
                )
    elif vc_name == "Erigon-Caplin":
        if not caplin_supports_epbs(fs, vc_content):
            plan.actions.append(
                PlanAction(
                    "Erigon-Caplin",
                    "skipped: erigon --version is older than "
                    f"{CAPLIN_BUILDERS_MIN_VERSION} (no caplin-builders.json)",
                )
            )
            plan.warnings.append(
                "Prepare: no-op on this Erigon build — Complete will stop "
                "MEV-Boost without a builder list. Install Erigon/Caplin "
                f"{CAPLIN_BUILDERS_MIN_VERSION} or later (first tagged Caplin "
                "release that is Gloas-ready on Sepolia)."
            )
        else:
            builders_path = fs.caplin_builders_path
            existing = fs.read_text(builders_path)
            new_json = apply_relays_caplin(relays, existing)
            changed = (existing or "") != new_json
            plan.actions.append(
                PlanAction(
                    builders_path,
                    "write builders[].url (caplin-builders.json); keep "
                    "--caplin.mev-relay-url until complete",
                )
            )
            if relays.network == "sepolia":
                plan.warnings.append(
                    "Sepolia on Erigon/Caplin v3.7.1 already schedules a 200M "
                    "gas limit at Gloas. EthPillar does not set a gas-limit flag."
                )
            if apply and changed:
                if existing:
                    _backup(builders_path, fs)
                writer = fs.write_data or _write_execution_data
                writer(builders_path, new_json)
            if changed:
                plan.services_to_restart.append("execution")
            else:
                plan.warnings.append(
                    "Caplin already has these builders; nothing to change."
                )
    else:
        planned = apply_relays_placeholder(vc_name)
        plan.actions.append(PlanAction(f"{vc_name} VC (placeholder)", planned))
        plan.warnings.append(
            f"Prepare: no-op on this client — Complete will stop MEV-Boost "
            f"without a VC relay replacement. Complete is refused unless you "
            f"pass --force (local EL + P2P bids only)."
        )
        if mode == "integrated_grandine":
            plan.warnings.append(
                "Grandine is integrated; there is no separate validator.service."
            )


def prepare(fs: Optional[EpbsFilesystem] = None, apply: bool = False) -> MigrationPlan:
    """Copy mev-boost relays onto the VC. Keep the sidecar running.

    Prysm writes proposer-settings JSON and VC flags. Lodestar gets
    ``--builder.urls`` when the binary documents that flag. Erigon-Caplin
    writes ``caplin-builders.json`` when ``erigon --version`` is at least
    v3.7.1 and leaves ``--caplin.mev-relay-url`` in place. Other VCs are
    a documented no-op. When Charon is installed, VC relay writes are skipped
    (Charon ``--builder-api`` owns the MEV path until complete).
    Beacon-node sidecar flags are not touched.

    Args:
        fs: IO adapter; production defaults if omitted.
        apply: If True, write units/settings; otherwise dry-run.

    Returns:
        Plan including actions, warnings, and services to restart.

    Raises:
        EpbsError: If no validator unit exists or MEV-Boost relays cannot
            be loaded.
    """
    fs = fs or EpbsFilesystem()
    vc_name, bn_name, mode = detect_clients(fs)
    if mode == "none" or not vc_name:
        raise EpbsError("No validator client unit found.")

    relays = _load_relays(fs)
    level = support_level(vc_name)
    plan = MigrationPlan(
        command="prepare",
        client=vc_name,
        support=level,
        notes=SUPPORT_NOTES.get(vc_name, ""),
    )
    plan.warnings.append(
        "Do not stop MEV-Boost yet. Pre-Gloas proposals still use the sidecar."
    )
    via_charon = charon_installed(fs)
    if via_charon:
        charon_path = fs.unit_path("charon")
        ch_content = fs.read_text(charon_path) or ""
        if charon_has_builder_api(ch_content):
            plan.actions.append(
                PlanAction(
                    charon_path,
                    "unchanged: keep --builder-api until complete (MEV-Boost path)",
                )
            )
        else:
            plan.actions.append(
                PlanAction(
                    charon_path,
                    "warning: Charon has no --builder-api (MEV builder path unset)",
                )
            )
        plan.actions.append(
            PlanAction(
                "validator",
                "skipped: Charon DVT owns builder path (no VC relay list)",
            )
        )
        plan.warnings.append(CHARON_EPBS_NOTE)
        if relays.min_bid:
            plan.actions.append(PlanAction("mevboost min-bid", relays.min_bid))
        plan.actions.append(
            PlanAction("relays", f"{len(relays.urls)} URL(s) from mevboost.service")
        )
        plan.applied = apply
        _ = bn_name  # BN sidecar stays until complete()
        return plan

    _apply_vc_relays(
        fs, relays, apply, plan, vc_name, mode, relays_source="mevboost.service"
    )
    plan.applied = apply
    _ = bn_name  # BN sidecar stays until complete()
    return plan


def import_migration(
    path: str,
    fs: Optional[EpbsFilesystem] = None,
    apply: bool = False,
) -> MigrationPlan:
    """Apply a portable migration file onto the local validator client.

    Does not require local ``mevboost.service``. When Charon is installed,
    import is refused until :func:`charon_epbs_supported` is True (same gate
    as the Charon TUI menu). Writing VC relays while Charon still owns the
    builder path would bypass the middleware.

    Args:
        path: Path to a ``.ethpillar.epbs-migration`` file.
        fs: IO adapter; production defaults if omitted.
        apply: If True, write units/settings; otherwise dry-run.

    Returns:
        Plan including actions, warnings, and services to restart.

    Raises:
        EpbsError: If no validator unit exists, Charon blocks import, or the
            migration file is invalid.
    """
    fs = fs or EpbsFilesystem()
    if charon_installed(fs) and not charon_epbs_supported(fs):
        raise EpbsError(CHARON_IMPORT_REFUSED)

    vc_name, bn_name, mode = detect_clients(fs)
    if mode == "none" or not vc_name:
        raise EpbsError("No validator client unit found.")

    relays = load_migration_file(path, read_text=fs.read_text)
    level = support_level(vc_name)
    plan = MigrationPlan(
        command="import",
        client=vc_name,
        support=level,
        notes=SUPPORT_NOTES.get(vc_name, ""),
    )
    plan.warnings.append(
        "Imported relays for the VC. Do not complete on the MEV/CC host until "
        "after the Gloas fork. Keep MEV-Boost running there until then."
    )
    if charon_installed(fs):
        plan.warnings.append(CHARON_EPBS_NOTE)
        plan.warnings.append(
            "After Gloas: run Complete on this host to strip Charon "
            "--builder-api, and Complete on the MEV/CC host to stop MEV-Boost."
        )

    _apply_vc_relays(
        fs,
        relays,
        apply,
        plan,
        vc_name,
        mode,
        relays_source=os.path.basename(path) or path,
    )
    plan.applied = apply
    _ = bn_name
    return plan


def _load_prysm_settings(fs: EpbsFilesystem, vc_content: str) -> Optional[dict]:
    """Load Prysm proposer-settings JSON, or None when missing or invalid.

    Args:
        fs: IO adapter.
        vc_content: ``validator.service`` text (for ``--proposer-settings-file``).

    Returns:
        Parsed object, or None if the file is absent, unreadable, or not a
        JSON object.
    """
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    settings_path = (
        get_flag_value(args, "--proposer-settings-file") or fs.prysm_settings_path
    )
    raw = fs.read_text(settings_path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _vc_has_relays(fs: EpbsFilesystem, vc_name: str, vc_content: str) -> bool:
    """Return True if the VC already has a non-sidecar relay list.

    Args:
        fs: IO adapter (Prysm reads proposer-settings via this).
        vc_name: Validator client name.
        vc_content: ``validator.service`` text.

    Returns:
        True for Prysm when ``default_config.builder.builders`` has a
        non-sidecar URL, for Lodestar when ``--builder.urls`` is set and
        is not the sidecar, or for Erigon-Caplin when ``caplin-builders.json``
        has a non-sidecar ``builders[].url`` and the binary is v3.7.1+.
        Legacy ``builder.relays`` does not count.
        Always False for placeholder clients and for an Erigon binary older
        than v3.7.1.
    """
    if vc_name == "Prysm":
        data = _load_prysm_settings(fs, vc_content)
        return bool(data) and prysm_has_builder_list(data)
    if vc_name == "Lodestar":
        args = normalize_cli_args(parse_unit(vc_content).exec_args)
        urls = get_flag_value(args, "--builder.urls")
        return bool(urls) and not is_sidecar_url(urls)
    if vc_name == "Erigon-Caplin":
        if not caplin_supports_epbs(fs, vc_content):
            return False
        loaded = _load_caplin_builders(fs)
        return bool(loaded) and caplin_has_builder_list(loaded)
    return False


def complete(
    fs: Optional[EpbsFilesystem] = None,
    apply: bool = False,
    force: bool = False,
    remote_vc_prepared: bool = False,
) -> MigrationPlan:
    """Disable MEV-Boost and remove BN sidecar builder flags.

    Does not rewrite VC relay config from prepare. Refused when the VC has no
    relay list unless *force*, *remote_vc_prepared*, or Charon ``--builder-api``
    is True.

    Split LXC:

    * **MEV/CC host** (no local VC): pass *remote_vc_prepared* after the other
      host imported; strips BN sidecar and stops MEV.
    * **VC/Charon host** (no consensus/MEV): strips Charon ``--builder-api``
      only; solo VC is a documented no-op.

    Args:
        fs: IO adapter; production defaults if omitted.
        apply: If True, write units and stop/disable mevboost.
        force: Allow complete without a VC relay list (local EL + P2P only).
        remote_vc_prepared: Allow complete when the VC lives on another host
            and already imported the migration file.

    Returns:
        Plan including BN strip actions and/or Charon strip / MEV disable.

    Raises:
        EpbsError: If neither a BN/MEV complete nor a Charon/VC-only complete
            can run, or the VC gate fails.
    """
    fs = fs or EpbsFilesystem()
    vc_name, bn_name, mode = detect_clients(fs)
    has_bn = bool(bn_name) or mode == "integrated_grandine"
    has_mev = fs.exists(fs.unit_path("mevboost"))
    has_charon = charon_installed(fs)

    # Machine B: VC and/or Charon only — no BN/MEV to tear down locally.
    if not has_bn and not has_mev:
        if mode == "integrated_grandine":
            vc_name = vc_name or "Grandine"
        level = support_level(vc_name or "unknown")
        plan = MigrationPlan(
            command="complete",
            client=vc_name or ("Charon" if has_charon else "unknown"),
            support=level,
            notes=SUPPORT_NOTES.get(vc_name or "", ""),
            disable_mevboost=False,
        )
        if has_charon:
            _complete_charon_builder_api(fs, plan, apply)
            # Restart hint for Charon-only hosts omits consensus.
            plan.warnings = [
                w
                for w in plan.warnings
                if "restart order: consensus" not in w.lower()
            ]
            if any("remove --builder-api" in a.detail for a in plan.actions):
                plan.warnings.append(
                    "After complete, restart order: charon → validator."
                )
        else:
            plan.actions.append(
                PlanAction(
                    "complete",
                    "nothing local to complete (VC relays already applied via import)",
                )
            )
            plan.warnings.append(
                "MEV-Boost stop and BN sidecar strip run on the CC/MEV host."
            )
        plan.applied = apply
        plan.rollback_hint = complete_rollback_hint(fs)
        return plan

    if mode == "integrated_grandine":
        vc_name = vc_name or "Grandine"
        bn_name = bn_name or "Grandine"

    level = support_level(vc_name or bn_name or "unknown")
    plan = MigrationPlan(
        command="complete",
        client=vc_name or bn_name or "mevboost",
        support=level,
        notes=SUPPORT_NOTES.get(vc_name or bn_name or "", ""),
        disable_mevboost=True,
    )

    vc_content = ""
    has_relays = False
    if mode == "separate":
        _, vc_content = _read_required_unit(fs, "validator")
        has_relays = _vc_has_relays(fs, vc_name, vc_content)
    elif mode == "integrated_caplin":
        _, vc_content = _read_required_unit(fs, "execution")
        has_relays = _vc_has_relays(fs, vc_name or "Erigon-Caplin", vc_content)
    via_charon = charon_ready_for_complete(fs)
    if not has_relays and not via_charon and not force and not remote_vc_prepared:
        raise EpbsError(COMPLETE_REFUSED)
    if remote_vc_prepared and not has_relays:
        plan.warnings.append(
            "Remote VC prepared: trusting that the other host already imported "
            "the ePBS migration file."
        )
    if via_charon and not has_relays:
        plan.warnings.append(
            "Charon DVT path: complete strips --builder-api (no VC relay list)."
        )
    elif not has_relays and not remote_vc_prepared:
        plan.warnings.append(
            "Prepare was a no-op / this VC has no relay list. After this step "
            "the node will use local EL + P2P builder bids only (no off-protocol "
            "relays)."
        )

    if has_bn:
        bn_key = _bn_service_key(bn_name or vc_name)
        bn_path, bn_content = _read_required_unit(fs, bn_key)
        new_bn = strip_bn_sidecar(bn_content, bn_name or vc_name)
        if _write_unit_if_changed(fs, bn_path, bn_content, new_bn, apply):
            plan.actions.append(
                PlanAction(bn_path, f"remove mev-boost sidecar flags from {bn_name}")
            )
            plan.services_to_restart.append(
                "execution" if bn_key == "execution" else "consensus"
            )
        else:
            plan.actions.append(PlanAction(bn_path, "no sidecar builder URL present"))
    else:
        plan.warnings.append(
            "consensus.service not installed; skipping BN sidecar strip."
        )

    _complete_charon_builder_api(fs, plan, apply)

    mev_path = fs.unit_path("mevboost")
    if fs.exists(mev_path):
        plan.actions.append(
            PlanAction(mev_path, "stop and disable mevboost (unit file kept)")
        )
        if apply:
            stopper = fs.stop_disable_mevboost or _default_stop_disable_mevboost
            stopper()
    else:
        plan.warnings.append("mevboost.service not installed; skipping disable.")

    if "consensus" in plan.services_to_restart and mode == "integrated_grandine":
        # Integrated Grandine restarts with consensus.service only.
        pass
    elif vc_name in ("Prysm", "Lodestar", "Erigon-Caplin"):
        # VC flags / builder file do not change on complete; BN restart is enough.
        pass

    plan.applied = apply
    plan.rollback_hint = complete_rollback_hint(fs)
    return plan


def status(fs: Optional[EpbsFilesystem] = None) -> str:
    """Human-readable snapshot of ePBS migration state.

    Args:
        fs: IO adapter; production defaults if omitted.

    Returns:
        Multi-line status (VC/BN names, support level, MEV-Boost relay
        count, whether the VC has relays, whether BN sidecar flags remain).
    """
    fs = fs or EpbsFilesystem()
    vc_name, bn_name, mode = detect_clients(fs)
    level = support_level(vc_name) if vc_name else ""
    lines = [
        f"Validator: {vc_name or '(none)'}  mode={mode}",
        f"Beacon node: {bn_name or '(none)'}",
        f"Support: {level or 'n/a'}",
    ]
    if vc_name:
        lines.append(SUPPORT_NOTES.get(vc_name, ""))

    mev_path = fs.unit_path("mevboost")
    if fs.exists(mev_path):
        content = fs.read_text(mev_path) or ""
        cfg = parse_mevboost_relays(content)
        lines.append(
            f"MEV-Boost: installed  relays={len(cfg.urls)}  min-bid={cfg.min_bid or '(unset)'}"
        )
    else:
        lines.append("MEV-Boost: not installed")

    if mode == "none" and fs.exists(mev_path):
        lines.append(
            "Split LXC (no local VC): export a .ethpillar.epbs-migration file "
            "for the VC/Charon host. Complete here after that host imports "
            "(--remote-vc-prepared)."
        )
    elif mode == "separate" and not bn_name and not fs.exists(mev_path):
        lines.append(
            "Split LXC (VC/Charon host): import a .ethpillar.epbs-migration "
            "file from the MEV/CC host. Complete here strips Charon "
            "--builder-api when present."
        )

    if mode == "separate":
        _, vc_content = _read_required_unit(fs, "validator")
        has_relays = _vc_has_relays(fs, vc_name, vc_content)
        if vc_name == "Prysm":
            loaded = _load_prysm_settings(fs, vc_content)
            if loaded and prysm_legacy_relays_only(loaded):
                lines.append(
                    "VC relays: no (legacy builder.relays is ignored on Prysm "
                    "v7.2.0; re-run prepare)"
                )
            else:
                lines.append("VC relays: " + ("yes" if has_relays else "no"))
        else:
            lines.append("VC relays: " + ("yes" if has_relays else "no"))
        if charon_installed(fs):
            if charon_ready_for_complete(fs):
                lines.append(
                    "Complete: allowed via Charon --builder-api "
                    "(DVT owns builder path; no VC relay list required)."
                )
            elif not has_relays:
                lines.append(
                    "Charon --builder-api: already removed (or never set). "
                    "Complete needs a VC relay list or --force."
                )
        elif not has_relays and bn_name:
            if level == "placeholder":
                lines.append(
                    "Prepare: no-op on this client — Complete will stop "
                    "MEV-Boost without a VC relay replacement."
                )
            lines.append(
                "Complete: refused until Prepare writes a VC relay list "
                "(or --force for local EL + P2P only)."
            )
    elif mode == "integrated_grandine":
        lines.append(
            "Prepare: no-op on this client — Complete will stop MEV-Boost "
            "without a VC relay replacement."
        )
        lines.append(
            "Complete: refused unless --force (local EL + P2P only)."
        )
    elif mode == "integrated_caplin":
        _, vc_content = _read_required_unit(fs, "execution")
        has_relays = _vc_has_relays(fs, vc_name or "Erigon-Caplin", vc_content)
        lines.append("VC relays: " + ("yes" if has_relays else "no"))
        if not caplin_supports_epbs(fs, vc_content):
            lines.append(
                "Erigon binary: older than "
                f"{CAPLIN_BUILDERS_MIN_VERSION}; caplin-builders.json "
                "is not used. Prepare is a no-op."
            )
        elif not has_relays:
            lines.append(
                "Complete: refused until Prepare writes caplin-builders.json "
                "(or --force for local EL + P2P only)."
            )
    if bn_name:
        bn_key = _bn_service_key(bn_name)
        _, bn_content = _read_required_unit(fs, bn_key)
        stripped = strip_bn_sidecar(bn_content, bn_name)
        lines.append(
            "BN sidecar flags: "
            + ("present" if stripped != bn_content else "already removed")
        )
    charon_path = fs.unit_path("charon")
    if fs.exists(charon_path):
        ch_content = fs.read_text(charon_path) or ""
        lines.append(
            "Charon: installed  builder-api="
            + ("yes" if charon_has_builder_api(ch_content) else "no")
        )
        lines.append(CHARON_EPBS_NOTE)
    return "\n".join(lines).rstrip() + "\n"


def _print_plan(plan: MigrationPlan, as_json: bool) -> None:
    """Print *plan* as JSON or :meth:`MigrationPlan.format_text`.

    Args:
        plan: Prepare or complete result.
        as_json: If True, emit a JSON object to stdout.
    """
    if as_json:
        payload = {
            "command": plan.command,
            "client": plan.client,
            "support": plan.support,
            "notes": plan.notes,
            "actions": [{"target": a.target, "detail": a.detail} for a in plan.actions],
            "warnings": plan.warnings,
            "services_to_restart": plan.services_to_restart,
            "disable_mevboost": plan.disable_mevboost,
            "applied": plan.applied,
        }
        if plan.command == "complete" and plan.rollback_hint:
            payload["rollback"] = plan.rollback_hint
        print(json.dumps(payload, indent=2))
        return
    print(plan.format_text(), end="")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry for ``python -m manage.epbs``.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` on :class:`EpbsError`.
    """
    parser = argparse.ArgumentParser(
        prog="python -m manage.epbs",
        description=(
            "Prepare a VC for ePBS, complete MEV-Boost teardown after Gloas, "
            "or export/import a split-LXC migration file."
        ),
    )
    parser.add_argument(
        "command",
        choices=("status", "prepare", "complete", "export", "import"),
        help=(
            "status | prepare (before fork) | complete (after fork) | "
            "export (MEV host) | import (VC/Charon host)"
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Migration file path (required for import; optional for export).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Export destination path (default: ./hostname-timestamp.ethpillar.epbs-migration).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write unit/settings changes (default is dry-run; export always writes).",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow complete without a VC relay list (local EL + P2P bids only).",
    )
    parser.add_argument(
        "--remote-vc-prepared",
        action="store_true",
        help=(
            "Allow complete on a MEV/CC host when the VC on another host "
            "already imported the migration file."
        ),
    )
    parser.add_argument(
        "--systemd-dir",
        default=None,
        help="Override /etc/systemd/system (tests).",
    )
    parser.add_argument(
        "--prysm-settings",
        default=None,
        help="Override Prysm proposer-settings.json path (tests).",
    )
    parser.add_argument(
        "--caplin-builders",
        default=None,
        help="Override Caplin caplin-builders.json path (tests).",
    )
    args = parser.parse_args(argv)

    fs = EpbsFilesystem()
    if args.systemd_dir:
        fs.systemd_dir = args.systemd_dir
        fs.write_unit = lambda path, content: _write_plain(path, content)
        fs.write_data = lambda path, content: _write_plain(path, content)
        fs.stop_disable_mevboost = lambda: None
        fs.read_text = _read_plain
        fs.exists = os.path.isfile
    if args.prysm_settings:
        fs.prysm_settings_path = args.prysm_settings
    if args.caplin_builders:
        fs.caplin_builders_path = args.caplin_builders

    try:
        if args.command == "status":
            text = status(fs)
            if args.json:
                print(json.dumps({"status": text}))
            else:
                print(text, end="")
            return 0
        if args.command == "export":
            out = args.output or args.path
            path, payload = export_migration(fs, output=out)
            if args.json:
                print(json.dumps({"path": path, "payload": payload}, indent=2))
            else:
                print(f"Wrote ePBS migration file:\n  {path}\n")
                print(json.dumps(payload, indent=2))
            return 0
        if args.command == "import":
            if not args.path:
                raise EpbsError("import requires a migration file path")
            _print_plan(
                import_migration(args.path, fs, apply=args.apply), args.json
            )
            return 0
        if args.command == "prepare":
            _print_plan(prepare(fs, apply=args.apply), args.json)
            return 0
        _print_plan(
            complete(
                fs,
                apply=args.apply,
                force=args.force,
                remote_vc_prepared=args.remote_vc_prepared,
            ),
            args.json,
        )
        return 0
    except EpbsError as exc:
        print(f"ePBS: {exc}", file=sys.stderr)
        return 1


def _read_plain(path: str) -> Optional[str]:
    """Read UTF-8 text without sudo (used with ``--systemd-dir`` in tests).

    Args:
        path: File to read.

    Returns:
        File contents, or None if the path does not exist.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _write_plain(path: str, content: str) -> None:
    """Write UTF-8 text without sudo (used with ``--systemd-dir`` in tests).

    Args:
        path: Destination file; parent directories are created.
        content: File text. A trailing newline is added if missing.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")


if __name__ == "__main__":
    sys.exit(main())
