"""ePBS / Glamsterdam MEV migration helpers.

Two-step operator flow (EthStaker Glamsterdam guidance):

1. **prepare** (Before Gloas Fork) — copy mev-boost relays (and min-bid
   where the VC supports it) onto the validator client. Keep
   ``mevboost.service`` and BN sidecar flags. That overlap is only the
   staging window until complete.
2. **complete** (After Gloas Fork) — the operator runs this after Gloas on
   that network. It stops/disables MEV-Boost and strips BN flags that
   pointed at ``http://127.0.0.1:18550``. The fork does not run this step.
   If prepare was never run, classic MEV stays (ePBS is optional). Keep
   the VC builder list from step 1 (Prysm ``builders`` entries, Lodestar
   ``--builder.urls``, Lighthouse ``builder_definitions.yml``). Refused
   unless the VC already has that list (or
   ``--force`` / ``--remote-vc-prepared`` for split LXC).

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

* ``full`` — Prysm v7.2.1+ (VC ``--builder-urls`` / ``--builder-min-bid`` /
  ``--builder-boost-factor``; v7.2.0 falls back to proposer-settings schema v2),
  Lodestar v1.47.0+ (VC ``--builder.urls`` / ``--builder.minBid``),
  Teku v26.11.0+ (VC ``--builder-urls`` / ``--builder-min-bid`` /
  ``--p2p-builder-min-bid`` / ``--builder-boost-factor`` /
  ``--p2p-builder-boost-factor``), Nimbus v26.10.2+ (VC ``--payload-builder`` /
  repeatable ``--payload-builder-url`` / ``--builder-min-bid`` /
  ``--builder-max-execution-payment`` / ``--builder-boost-factor``),
  Erigon-Caplin v3.7.1+ (``caplin-builders.json`` plus the existing
  ``--caplin.mev-relay-url`` sidecar), and Lighthouse v8.3.0+
  (``builder_definitions.yml`` ``builders[].url``). Lodestar prepare still
  probes ``lodestar validator --help``. Prysm, Teku, Nimbus, Caplin, and
  Lighthouse prepare probe ``--version``. Teku 26.11.0 removed the hidden
  ``--Xbuilder-*`` names, so older binaries are skipped. Prysm v7.2.0 stays
  on the settings-file path.
* ``placeholder`` — Grandine: no released VC relay list; prepare is a
  documented no-op. Complete is refused without ``--force``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import ipaddress
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
from urllib.parse import urlparse

import yaml

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
# Lighthouse v8.3.0 reads external builders from builder_definitions.yml
# (from Gloas) and schedules Sepolia/Hoodi 200M gas at the Gloas epoch.
# EthPillar does not write --gas-limit because it would override the schedule.
LIGHTHOUSE_BUILDERS_MIN_VERSION = "8.3.0"
LIGHTHOUSE_BUILDERS_FILENAME = "builder_definitions.yml"
LIGHTHOUSE_VALIDATORS_DIRNAME = "validators"
LIGHTHOUSE_SEPOLIA_GAS_EPOCH = "353024"
LIGHTHOUSE_HOODI_GAS_EPOCH = "132352"
# Teku 26.11.0 promotes ePBS builder flags (Consensys/teku#11563, in the
# 26.11.0 tag's BuilderOptions.java). The hidden --Xbuilder-* names from
# 26.9.0–26.10.x were removed, not kept as aliases. --Xbuilder-min-bid and
# --Xbuilder-boost-factor applied to both configured builders and p2p bids;
# 26.11.0 splits those into --builder-* and --p2p-builder-*.
TEKU_EPBS_MIN_VERSION = "26.11.0"
# (old flag, production flags). A value is copied onto each production flag
# that is not already set, then the --X name is dropped.
TEKU_XBUILDER_REPLACEMENTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("--Xbuilder-urls", ("--builder-urls",)),
    ("--Xbuilder-min-bid", ("--builder-min-bid", "--p2p-builder-min-bid")),
    (
        "--Xbuilder-boost-factor",
        ("--builder-boost-factor", "--p2p-builder-boost-factor"),
    ),
    ("--Xbuilder-max-execution-payment", ("--builder-max-execution-payment",)),
)
# Prysm v7.2.1 is the Sepolia pin: VC --builder-urls / --builder-min-bid /
# --builder-boost-factor, 0x-hex builder auth, and the 200M GAS_LIMIT_SCHEDULE.
# v7.2.0 has the settings-file builders list but none of those.
PRYSM_BUILDER_CLI_MIN_VERSION = "7.2.1"
# Nimbus v26.10.2 makes --payload-builder-url a seq[string] on BN and VC
# (status-im/nimbus-eth2#9336). v26.10.1 had Gloas VC builder flags but only
# one URL; prepare requires 26.10.2 so multiple MEV relays become repeated
# --payload-builder-url=… flags (CLI) / a TOML list (if used).
NIMBUS_EPBS_MIN_VERSION = "26.10.2"
NIMBUS_SUGGESTED_GAS_FLAG = "--suggested-gas-limit"
# Nimbus silent caps in buildBuilderConfig (beacon_chain/spec/mev/gloas_mev.nim
# @ v26.10.2): URLs with len > MAX_BUILDER_URL_SIZE are skipped with no log,
# and builders.add() stops after MAX_BUILDER_ENTRIES with a silent break.
# EthPillar rejects over-limit lists so prepare does not write a partial config.
NIMBUS_MAX_BUILDER_ENTRIES = 64
NIMBUS_MAX_BUILDER_URL_SIZE = 2048
# eth-docker EPBS_BUILD_FACTOR=always → uint64 max (prefer the bid).
BOOST_FACTOR_UINT64_MAX = "18446744073709551615"
TEKU_BOOST_FACTOR_UINT64_MAX = BOOST_FACTOR_UINT64_MAX
# Shared operator override (eth-docker name). Teku-specific env still wins
# for Teku. Neither is a TUI setting; unset omits the flag when builders are on.
EPBS_BUILD_FACTOR_ENV = "EPBS_BUILD_FACTOR"
TEKU_EPBS_BUILD_FACTOR_ENV = "TEKU_EPBS_BUILD_FACTOR"
TEKU_SEPOLIA_GAS_FLAG = "--validators-builder-registration-default-gas-limit"
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
# Nimbus getPayloadBuilderAddress returns Opt.some("") when the enable flag
# is set and the URL list was emptied, so the enable flag goes with the sidecar.
BN_BOOL_FLAGS: Dict[str, Tuple[str, ...]] = {
    "Lodestar": ("--builder",),
    "Nimbus": ("--payload-builder",),
}

# Prysm before v7.2.1 ships Sepolia Gloas without the upstream 200M schedule.
# v7.2.1+ includes GAS_LIMIT_SCHEDULE, so EthPillar does not write a Prysm
# gas limit. Teku still defaults to 60M; prepare writes the explicit flag.
SEPOLIA_GLOAS_GAS_LIMIT = "200000000"
SEPOLIA_GAS_LIMIT_NOTE = (
    "Sepolia on Prysm before v7.2.1 defaults the Gloas proposer gas limit to 60M "
    "(that release has no 200M GAS_LIMIT_SCHEDULE). To propose at 200M, set "
    f"\"gas_limit\": \"{SEPOLIA_GLOAS_GAS_LIMIT}\" on default_config or a "
    "proposer_config key. EthPillar does not write that value. "
    "On this binary, --suggested-gas-limit only applies to pre-Gloas "
    "mev-boost registrations."
)
PRYSM_SUGGESTED_GAS_LIMIT_NOTE = (
    "Prysm --suggested-gas-limit overrides the network gas-limit schedule from "
    "the Gloas fork onward (Sepolia's 200M GAS_LIMIT_SCHEDULE on v7.2.1+). "
    "Remove the flag to follow the schedule. The validator warns at startup "
    "when the flag is set."
)
TEKU_SEPOLIA_GAS_NOTE = (
    "Sepolia on Teku still has no 200M gas-limit schedule (default 60M). "
    "EthPillar sets "
    f"{TEKU_SEPOLIA_GAS_FLAG}={SEPOLIA_GLOAS_GAS_LIMIT} unless that flag is "
    "already present."
)
NIMBUS_SEPOLIA_GAS_NOTE = (
    "Sepolia on Nimbus sends proposer-preferences target_gas_limit from "
    f"{NIMBUS_SUGGESTED_GAS_FLAG} (compile-time default 60000000; that path "
    "does not read GAS_LIMIT_SCHEDULE). EthPillar sets "
    f"{NIMBUS_SUGGESTED_GAS_FLAG}={SEPOLIA_GLOAS_GAS_LIMIT} unless that flag "
    "is already present."
)
LIGHTHOUSE_SEPOLIA_GAS_NOTE = (
    "Sepolia Gloas on Lighthouse v8.3.0+ follows GAS_LIMIT_SCHEDULE "
    f"({SEPOLIA_GLOAS_GAS_LIMIT} at epoch {LIGHTHOUSE_SEPOLIA_GAS_EPOCH}). "
    "EthPillar does not set --gas-limit or validator_definitions.yml "
    "gas_limit; those override the schedule."
)
LIGHTHOUSE_HOODI_GAS_NOTE = (
    "Hoodi Gloas on Lighthouse v8.3.0+ follows GAS_LIMIT_SCHEDULE "
    f"({SEPOLIA_GLOAS_GAS_LIMIT} at epoch {LIGHTHOUSE_HOODI_GAS_EPOCH}). "
    "EthPillar does not set --gas-limit or validator_definitions.yml "
    "gas_limit; those override the schedule."
)
LIGHTHOUSE_GAS_OVERRIDE_NOTE = (
    "Lighthouse gas-limit schedule is "
    f"{SEPOLIA_GLOAS_GAS_LIMIT} gas at the Gloas epoch, "
    "but this validator sets --gas-limit or a per-validator gas_limit, which "
    "overrides that schedule. EthPillar leaves the override in place."
)

# v7.2.0 still accepts these builder keys but ignores or warns on them.
# ``relays`` is unread; ``enabled`` is legacy mev-boost content dropped at
# the fork; ``builders_set`` is an internal marker that fails strict load.
_PRYSM_STALE_BUILDER_KEYS = ("enabled", "relays", "builders_set")

SUPPORT_NOTES: Dict[str, str] = {
    "Prysm": (
        "Full: Prysm v7.2.1+ writes VC --builder-urls, --builder-min-bid "
        "(ETH min-bid as integer Gwei), --builder-boost-factor, and "
        "--builder-max-execution-payment=0. Build factor comes from "
        f"{EPBS_BUILD_FACTOR_ENV}: 0/local → 0, always → uint64 max, "
        "maxprofit → 100, a number as-is (capped). Unset omits the flag "
        "(Prysm default 100) once builders are on; ePBS off uses 0. "
        "An existing proposer-settings file is kept in sync (0x-hex "
        "auth_data / builder_pubkeys; hostname-less URLs are rejected). "
        "v7.2.0 falls back to the settings-file builders list. Prepare "
        "removes deprecated --enable-builder and leaves BN --http-mev-relay. "
        "After Gloas, run complete to strip it. The fork does not."
    ),
    "Lodestar": (
        "Full: VC flags --builder.urls / --builder.minBid (v1.47.0+). "
        "Prepare writes them only when `lodestar validator --help` lists "
        "--builder.urls. The same build-factor tokens map to "
        "--builder.selection / --builder.boostFactor."
    ),
    "Lighthouse": (
        "Full: MEV relay URLs go in <datadir>/validators/builder_definitions.yml "
        "as builders[].url (v8.3.0+). enabled is true and "
        "max_execution_payment is 0 (trustless-only). auth_data is omitted "
        "(Lighthouse defaults it to the URL hostname). Global min_bid is "
        "MEV-Boost -min-bid in integer Gwei. Prepare keeps --builder-proposals. "
        "Does not set --gas-limit; Sepolia/Hoodi use the 200M schedule. Skipped "
        "when lighthouse --version is older than v8.3.0. Web3Signer users need "
        "Web3Signer 26.9.0+ for Gloas signing."
    ),
    "Teku": (
        "Full: VC flags --builder-urls, --builder-min-bid and "
        "--p2p-builder-min-bid (ETH min-bid as integer Gwei), and "
        "--builder-boost-factor / --p2p-builder-boost-factor (Teku "
        f"{TEKU_EPBS_MIN_VERSION}+). Prepare writes them only when "
        f"`teku --version` is at least {TEKU_EPBS_MIN_VERSION}. Teku 26.11.0 "
        "removed the hidden --Xbuilder-* names (not aliases). Boost factor is "
        f"read from {TEKU_EPBS_BUILD_FACTOR_ENV}, else {EPBS_BUILD_FACTOR_ENV}, "
        "else the beacon node's --builder-bid-compare-factor: 0/local → 0, "
        "always/BUILDER_ALWAYS → uint64 max, maxprofit → 100, numeric as-is. "
        "Unset omits the flags (Teku 26.11.0 default 100, was 90). The same "
        "value is written on the builder and p2p flags, matching the old "
        "combined flag. On Sepolia, prepare sets "
        f"{TEKU_SEPOLIA_GAS_FLAG}={SEPOLIA_GLOAS_GAS_LIMIT} unless already set "
        "(Teku has no 200M schedule). EthPillar uses a separate validator "
        "client, so the flags go on validator.service. A unit that still has "
        "--Xbuilder-* is rewritten on prepare, complete, and Teku upgrade. "
        "BN --builder-endpoint stays through prepare; run complete after "
        "Gloas to remove it."
    ),
    "Nimbus": (
        "Full: Nimbus v26.10.2+ writes VC --payload-builder=true, one "
        "--payload-builder-url per non-sidecar relay (repeatable; "
        "status-im/nimbus-eth2#9336), --builder-min-bid (ETH min-bid as "
        "integer Gwei), --builder-max-execution-payment=0 (trustless-only; "
        "Nimbus default is uint64 max), and --builder-boost-factor from "
        f"{EPBS_BUILD_FACTOR_ENV} (same tokens as Prysm; unset omits the flag, "
        "Nimbus default 100, and drops a leftover 0). Prepare writes URLs "
        "only when `nimbus_validator_client --version` is at least 26.10.2. "
        f"At most {NIMBUS_MAX_BUILDER_ENTRIES} URLs and "
        f"{NIMBUS_MAX_BUILDER_URL_SIZE} bytes per URL (Nimbus "
        "MAX_BUILDER_ENTRIES / MAX_BUILDER_URL_SIZE); over-limit lists are "
        "rejected because Nimbus would drop the extras silently. On Sepolia, "
        "sets "
        f"{NIMBUS_SUGGESTED_GAS_FLAG}={SEPOLIA_GLOAS_GAS_LIMIT} unless already "
        "set. BN --payload-builder-url stays until complete, which also drops "
        "BN --payload-builder when no builder URL remains. EthPillar uses "
        "CLI/systemd (repeat the flag); TOML configs must use list form."
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
        lighthouse_builders_path: Override for Lighthouse
            ``builder_definitions.yml``. Empty derives it from the VC unit.
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
    lighthouse_builders_path: str = ""
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
    """Write proposer-settings / builder YAML as validator-owned 0644.

    Creates the parent directory when missing and owns both the directory and
    the file as ``validator:validator``. That matters for Lighthouse
    ``builder_definitions.yml``: prepare may create ``…/validators`` before
    the VC has ever run, and a root-owned validators dir blocks the process
    from writing ``validator_definitions.yml`` or ``logs/``.

    Args:
        path: Destination path (for example ``/var/lib/prysm_validator/...``
            or ``…/lighthouse_validator/validators/builder_definitions.yml``).
        content: File text. A trailing newline is added if missing.
    """
    import tempfile

    directory = os.path.dirname(path)
    if directory:
        subprocess.run(["sudo", "mkdir", "-p", directory], check=True)
        # Own the dir even when mkdir was a no-op on an existing root-owned path.
        subprocess.run(
            ["sudo", "chown", "validator:validator", directory],
            check=False,
        )
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


def replace_repeated_flag(
    args: Sequence[str], name: str, values: Sequence[str]
) -> List[str]:
    """Replace every occurrence of *name* with one ``name=value`` per *values*.

    Used for repeatable flags such as Nimbus ``--payload-builder-url``
    (``seq[string]`` since v26.10.2). Empty *values* removes the flag.
    New values are inserted at the index of the first previous occurrence
    (or appended) so a second prepare keeps a stable ExecStart order.

    Args:
        args: Normalized CLI tokens from a unit ``ExecStart``.
        name: Flag including leading dashes.
        values: Replacement values (order preserved; one flag each).

    Returns:
        New argument list.
    """
    key = name.lower()
    insert_at: Optional[int] = None
    out: List[str] = []
    for arg in args:
        if flag_name(arg).lower() != key:
            out.append(arg)
            continue
        if insert_at is None:
            insert_at = len(out)
    replacements = [f"{name}={value}" for value in values]
    if insert_at is None:
        out.extend(replacements)
    else:
        out[insert_at:insert_at] = replacements
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
        ``full`` (Prysm, Lodestar, Teku, Nimbus, Erigon-Caplin, Lighthouse) or
        ``placeholder``. The MEV-Boost TUI (``epbsTuiSupported`` in
        ``functions.sh``) mirrors this for local validators (shown for
        Prysm, Lodestar, Teku, Nimbus, integrated Caplin, and Lighthouse),
        but is always shown on MEV hosts without a local validator
        (split LXC) and hidden when Charon is enabled.
    """
    if client in (
        "Prysm",
        "Lodestar",
        "Teku",
        "Nimbus",
        "Erigon-Caplin",
        "Lighthouse",
    ):
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


def map_epbs_build_factor(
    raw: str,
    *,
    enabled: bool = True,
    client: str = "ePBS",
    flag: str = "--builder-boost-factor",
) -> Tuple[Optional[str], Optional[str]]:
    """Map an eth-docker build-factor token to a uint64 flag value.

    Shared by Prysm ``--builder-boost-factor``, Teku ``--builder-boost-factor``
    Nimbus ``--builder-boost-factor``, and Lodestar's numeric
    ``--builder.boostFactor``.

    * ``enabled`` False → ``0`` (local blocks; ePBS builders are off)
    * empty, builders on → omit the flag (client default)
    * ``0`` or ``local`` → ``0``
    * ``always`` or Teku's BN value ``BUILDER_ALWAYS`` → uint64 max
    * ``maxprofit`` → ``100``
    * a positive integer → that value, capped at uint64 max

    Leading zeros on a numeric token are stripped (``0100`` → ``100``).
    Keywords are case-sensitive, matching the eth-docker scripts.

    Args:
        raw: Factor token from the environment or a beacon-node flag.
        enabled: False when ePBS builders are not being configured.
        client: Name used in warning text.
        flag: Flag name mentioned when a token is invalid.

    Returns:
        ``(flag_value, warning)``. ``flag_value`` is None when the flag
        should be left unset. ``warning`` is set for an invalid token or a
        value capped at uint64 max.
    """
    if not enabled:
        return "0", None
    text = (raw or "").strip()
    if not text:
        return None, None
    if re.fullmatch(r"0*[0-9]+", text):
        text = str(int(text))
    if text in ("0", "local"):
        return "0", None
    if text in ("always", "BUILDER_ALWAYS"):
        return BOOST_FACTOR_UINT64_MAX, None
    if text == "maxprofit":
        return "100", None
    if re.fullmatch(r"[1-9][0-9]{0,19}", text):
        value = int(text)
        if value > int(BOOST_FACTOR_UINT64_MAX):
            return (
                BOOST_FACTOR_UINT64_MAX,
                f"{client} build factor {text} exceeds the 64-bit maximum; "
                f"capping to {BOOST_FACTOR_UINT64_MAX}",
            )
        return text, None
    return None, (
        f'{client} build factor has an invalid value of "{raw}"; '
        f"leaving {flag} unset"
    )


def builder_url_hostname(url: str, *, client: str = "Prysm v7.2.1") -> str:
    """Return the ASCII hostname used as default builder auth data.

    Prysm v7.2.1 and Nimbus ``get_default_auth_data`` both reject an empty
    or non-ASCII hostname (``https://:8080``). Internationalized names must
    already be punycode. Nimbus lowercases the host and, for IPv6, compresses
    it itself; this helper only checks that a hostname exists.

    Args:
        url: Builder or relay URL.
        client: Name included in the error when the URL is rejected.

    Returns:
        Lowercased hostname, without brackets.

    Raises:
        EpbsError: If the URL has no hostname or the hostname is not ASCII.
    """
    parsed = urlparse((url or "").strip())
    host = parsed.hostname or ""
    if not host:
        raise EpbsError(
            f"Builder URL has no hostname (rejected by {client}): {url}"
        )
    try:
        host.encode("ascii")
    except UnicodeEncodeError as exc:
        raise EpbsError(
            f"Builder URL hostname must be ASCII punycode: {url}"
        ) from exc
    return host.lower()


def default_auth_data_hex(url: str) -> str:
    """Return 0x-hex of Prysm's default ``auth_data`` for *url*.

    Omitted ``auth_data`` is the hostname's UTF-8 bytes (builder-specs#168),
    not the full URL. IPv6 literals use the bracket form Prysm signs.

    Args:
        url: Builder URL with a hostname.

    Returns:
        ``0x``-prefixed hex.
    """
    host = builder_url_hostname(url)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        text = host
    else:
        mapped = getattr(addr, "ipv4_mapped", None)
        if addr.version == 4:
            text = host
        elif mapped is not None:
            raw = addr.packed
            g7 = int.from_bytes(raw[12:14], "big")
            g8 = int.from_bytes(raw[14:16], "big")
            text = f"[::ffff:{g7:x}:{g8:x}]"
        else:
            text = "[" + addr.compressed + "]"
    return "0x" + text.encode("ascii").hex()


def _normalize_hex_or_base64(value: str, *, what: str) -> str:
    """Return *value* as 0x-hex, accepting a legacy base64 encoding.

    Args:
        value: 0x-hex or standard base64.
        what: Field name used in errors.

    Returns:
        Lowercase ``0x`` hex.

    Raises:
        EpbsError: If *value* is neither encoding.
    """
    text = str(value).strip()
    if text.lower().startswith("0x"):
        body = text[2:]
        if body and len(body) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", body):
            return "0x" + body.lower()
        raise EpbsError(f"{what} is not valid 0x-hex: {text}")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EpbsError(f"{what} must be 0x-hex or base64, got {text!r}") from exc
    if not raw:
        raise EpbsError(f"{what} is empty")
    return "0x" + raw.hex()


def _normalize_builder_entry(entry: dict) -> dict:
    """Rewrite one builder entry to the v7.2.1 schema.

    ``pubkeys`` is renamed to ``builder_pubkeys``. Both pubkey and
    ``auth_data`` values are 0x-hex (legacy base64 is converted).
    ``auth_data`` is omitted when absent so Prysm derives it from the
    hostname. Hostname-less URLs are rejected.

    Args:
        entry: One ``builders[]`` object.

    Returns:
        Entry with ``url`` and any normalized key material.

    Raises:
        EpbsError: If the entry is not an object or a field will not decode.
    """
    if not isinstance(entry, dict):
        raise EpbsError("builder entry must be an object")
    url = str(entry.get("url") or "").strip()
    builder_url_hostname(url)
    out: dict = {"url": url}
    raw_keys = entry.get("builder_pubkeys", entry.get("pubkeys"))
    if raw_keys:
        if not isinstance(raw_keys, list):
            raise EpbsError("builder_pubkeys must be a list")
        keys: List[str] = []
        for item in raw_keys:
            encoded = _normalize_hex_or_base64(str(item), what="builder_pubkeys")
            if len(encoded) != 2 + 96:
                raise EpbsError("builder_pubkeys entry is not a 48-byte BLS key")
            keys.append(encoded)
        out["builder_pubkeys"] = keys
    if entry.get("auth_data"):
        out["auth_data"] = _normalize_hex_or_base64(
            str(entry["auth_data"]), what="auth_data"
        )
    return out


def _non_sidecar_urls(urls: Sequence[str]) -> List[str]:
    """Drop blank, duplicate, and local MEV-Boost sidecar URLs.

    Args:
        urls: Relay or builder URLs.

    Returns:
        Unique non-sidecar URLs in input order.
    """
    cleaned: List[str] = []
    seen = set()
    for url in urls:
        item = url.strip()
        if not item or is_sidecar_url(item) or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _prysm_builder_entries(relays: RelaysConfig, existing_builder: dict) -> List[dict]:
    """Build v7.2.1 builder entries from relays, keeping per-URL key material.

    Args:
        relays: Relay URLs (sidecar URLs are dropped).
        existing_builder: Current ``default_config.builder`` object.

    Returns:
        ``builders`` list. Explicit ``auth_data`` / ``builder_pubkeys`` on a
        matching old entry are normalized; omitted auth stays omitted.

    Raises:
        EpbsError: If no URL remains or a URL has no hostname.
    """
    old_by_url: Dict[str, dict] = {}
    previous = existing_builder.get("builders")
    if isinstance(previous, list):
        for entry in previous:
            if isinstance(entry, dict) and str(entry.get("url") or "").strip():
                old_by_url[str(entry["url"]).strip()] = entry
    entries: List[dict] = []
    for url in _non_sidecar_urls(relays.urls):
        source = {"url": url}
        old = old_by_url.get(url)
        if old:
            source.update(old)
            source["url"] = url
        entries.append(_normalize_builder_entry(source))
    if not entries:
        raise EpbsError("No non-sidecar relay URLs to write as Prysm builders")
    return entries


def _prysm_builder_config(
    relays: RelaysConfig,
    existing_builder: Optional[dict] = None,
    boost_factor: Optional[str] = None,
) -> dict:
    """Build Prysm ``default_config.builder`` (schema version 2).

    A nonempty ``builders`` list opts the key into pre-Gloas mev-boost
    registration and is the post-Gloas direct-builder list. ``auth_data`` is
    omitted unless an existing entry set it, so Prysm v7.2.1 signs the
    hostname bytes. ``pubkeys`` is not written; ``builder_pubkeys`` is 0x-hex.

    ``max_execution_payment`` ``"0"`` is an explicit trustless-only cap.
    ``min_bid`` is MEV-Boost ``-min-bid`` in integer Gwei.
    ``builder_boost_factor`` is set only when *boost_factor* is not None.

    Args:
        relays: Relay URLs and optional MEV-Boost min-bid (ETH).
        existing_builder: Current builder object, used to keep per-URL keys.
        boost_factor: Mapped uint64 string, or None to leave the key unset.

    Returns:
        Builder object.

    Raises:
        EpbsError: If a builder URL has no hostname.
    """
    config: dict = {
        "builders": _prysm_builder_entries(relays, existing_builder or {}),
        "max_execution_payment": "0",
    }
    if relays.min_bid:
        config["min_bid"] = eth_min_bid_to_gwei(relays.min_bid)
    if boost_factor is not None:
        config["builder_boost_factor"] = boost_factor
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
    for flag in (
        "--builder-urls",
        "--builder-min-bid",
        "--builder-boost-factor",
        "--builder-max-execution-payment",
        "--proposer-settings-file",
    ):
        if get_flag_value(old_args, flag) != get_flag_value(new_args, flag) or (
            has_flag(new_args, flag) and not has_flag(old_args, flag)
        ):
            if has_flag(new_args, flag):
                parts.append(f"set {flag}")
            elif has_flag(old_args, flag):
                parts.append(f"remove {flag}")
    if has_flag(old_args, "--enable-builder") and not has_flag(new_args, "--enable-builder"):
        parts.append("remove deprecated --enable-builder")
    return "; ".join(parts) if parts else "update validator flags"


def _apply_prysm_boost_flag(args: List[str], raw: str) -> Tuple[List[str], Optional[str]]:
    """Set ``--builder-boost-factor`` for an ePBS-on Prysm VC.

    A mapped value is written. An empty token removes a leftover ``0``
    so Prysm's default of 100 applies. Any other existing value is kept.
    Set ``EPBS_BUILD_FACTOR=0`` or ``local`` to keep local preference
    while builder URLs are configured.

    Args:
        args: Normalized VC arguments.
        raw: Build-factor token, or empty.

    Returns:
        ``(args, warning)``.
    """
    value, warning = map_epbs_build_factor(
        raw, enabled=True, client="Prysm", flag="--builder-boost-factor"
    )
    if value is not None:
        return upsert_flag(args, "--builder-boost-factor", value), warning
    if warning:
        return args, warning
    if get_flag_value(args, "--builder-boost-factor") == "0":
        args = remove_flags(args, "--builder-boost-factor")
    return args, None


def _load_proposer_settings(existing_settings: Optional[str]) -> dict:
    """Parse proposer-settings JSON into an object.

    Args:
        existing_settings: File text, or None when the file is absent.

    Returns:
        A dict (empty when *existing_settings* is None).

    Raises:
        EpbsError: If the text is not a JSON object.
    """
    if not existing_settings:
        return {}
    try:
        data = json.loads(existing_settings)
    except json.JSONDecodeError as exc:
        raise EpbsError(f"Invalid proposer-settings JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EpbsError("proposer-settings.json must be a JSON object")
    return data


def _prysm_settings_document(
    data: dict,
    relays: RelaysConfig,
    fee: str,
    boost_factor: Optional[str],
) -> str:
    """Merge relays into a schema-v2 proposer-settings document.

    Does not write ``gas_limit`` or touch ``--suggested-gas-limit``.
    Existing proposer config, graffiti, and option-level gas limits stay.
    Legacy ``builder.enabled``, ``builder.relays``, and ``builders_set``
    are removed. ``min_bid`` mirrors MEV-Boost. ``builder_boost_factor``
    is written when *boost_factor* is set and otherwise left as-is.

    Args:
        data: Current settings object (mutated).
        relays: Relay URLs and optional min-bid.
        fee: Suggested fee recipient copied when ``fee_recipient`` is missing.
        boost_factor: Mapped factor, or None to keep an existing key.

    Returns:
        JSON text with a trailing newline.
    """
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
    builder.pop("min_bid", None)
    if boost_factor is not None:
        builder.pop("builder_boost_factor", None)
    builder.update(_prysm_builder_config(relays, builder, boost_factor))
    return json.dumps(data, indent=2) + "\n"


def apply_relays_prysm(
    vc_content: str,
    relays: RelaysConfig,
    existing_settings: Optional[str],
    settings_path: str = PRYSM_SETTINGS_PATH,
    *,
    builder_cli: bool = False,
    build_factor: str = "",
) -> Tuple[str, Optional[str], str, Optional[str]]:
    """Merge mev-boost relays onto a Prysm validator client.

    v7.2.1+ (*builder_cli*): write ``--builder-urls``, ``--builder-min-bid``
    (Gwei), ``--builder-boost-factor``, and
    ``--builder-max-execution-payment=0``. Do not create a proposer-settings
    file just for builders. If one is already referenced, rewrite its
    ``builders`` list in the v7.2.1 shape (``builder_pubkeys``, 0x-hex) so
    the file, which replaces the CLI defaults, stays consistent.

    Older binaries: write the settings file and ``--proposer-settings-file``,
    and do not pass flags that v7.2.0 will reject.

    Both paths remove deprecated ``--enable-builder`` and do not write
    ``gas_limit`` or ``--suggested-gas-limit``.

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays scraped from MEV-Boost.
        existing_settings: Current proposer-settings JSON, or None.
        settings_path: Default JSON path if the VC flag is absent.
        builder_cli: True when the binary is Prysm v7.2.1 or newer.
        build_factor: Raw factor token (empty omits a new value).

    Returns:
        ``(new_vc_unit, proposer_settings_json_or_None, settings_path_used, warning)``.
        The JSON is None when the CLI path does not need a settings file.

    Raises:
        EpbsError: If settings are invalid, or a builder URL has no hostname.
    """
    unit = parse_unit(vc_content)
    args = normalize_cli_args(unit.exec_args)
    fee = get_flag_value(args, "--suggested-fee-recipient")
    flagged = get_flag_value(args, "--proposer-settings-file")
    settings_path = flagged or settings_path
    factor_value, warning = map_epbs_build_factor(
        build_factor, enabled=True, client="Prysm", flag="--builder-boost-factor"
    )
    # Invalid tokens return no value, so an existing file factor is kept.
    # A capped value is still written (warning explains the cap).
    file_factor = factor_value

    data = _load_proposer_settings(existing_settings)
    settings_json: Optional[str]
    if builder_cli:
        urls = _non_sidecar_urls(relays.urls)
        for url in urls:
            builder_url_hostname(url)
        if not urls:
            raise EpbsError("No non-sidecar relay URLs to write as --builder-urls")
        args = remove_flags(args, "--enable-builder")
        args = upsert_flag(args, "--builder-urls", ",".join(urls))
        if relays.min_bid:
            args = upsert_flag(args, "--builder-min-bid", eth_min_bid_to_gwei(relays.min_bid))
        else:
            args = remove_flags(args, "--builder-min-bid")
        args = upsert_flag(args, "--builder-max-execution-payment", "0")
        args, boost_warning = _apply_prysm_boost_flag(args, build_factor)
        warning = warning or boost_warning
        if existing_settings is not None or flagged:
            settings_json = _prysm_settings_document(data, relays, fee, file_factor)
            args = upsert_flag(args, "--proposer-settings-file", settings_path)
        else:
            settings_json = None
    else:
        settings_json = _prysm_settings_document(data, relays, fee, file_factor)
        args = remove_flags(args, "--enable-builder")
        args = upsert_flag(args, "--proposer-settings-file", settings_path)

    return _rebuild_unit(vc_content, args), settings_json, settings_path, warning


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


def _apply_lodestar_build_factor(args: List[str], raw: str) -> Tuple[List[str], Optional[str]]:
    """Map a build-factor token onto Lodestar selection / boost flags.

    Matches eth-docker's Lodestar VC entrypoint:

    * empty → leave selection alone (``--builder`` is already set)
    * ``0`` / ``local`` → ``--builder.selection=executionalways``
    * ``always`` / ``BUILDER_ALWAYS`` → ``--builder.selection=builderalways``
    * ``maxprofit`` or a number → ``maxprofit`` plus ``--builder.boostFactor``

    Args:
        args: Normalized VC arguments.
        raw: Factor token, or empty.

    Returns:
        ``(args, warning)``.
    """
    value, warning = map_epbs_build_factor(
        raw, enabled=True, client="Lodestar", flag="--builder.boostFactor"
    )
    if value is None:
        return args, warning
    if value == "0":
        args = upsert_flag(args, "--builder.selection", "executionalways")
        args = remove_flags(args, "--builder.boostFactor")
    elif value == BOOST_FACTOR_UINT64_MAX:
        args = upsert_flag(args, "--builder.selection", "builderalways")
        args = remove_flags(args, "--builder.boostFactor")
    else:
        args = upsert_flag(args, "--builder.selection", "maxprofit")
        args = upsert_flag(args, "--builder.boostFactor", value)
    return args, warning


def apply_relays_lodestar(
    vc_content: str,
    relays: RelaysConfig,
    build_factor: str = "",
) -> Tuple[str, Optional[str]]:
    """Add Lodestar VC builder URL / min-bid / build-factor flags (v1.47.0+).

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays and optional min-bid from MEV-Boost. ``min_bid`` is ETH
            and is converted to integer Gwei for ``--builder.minBid``.
        build_factor: Raw factor token. Empty leaves selection unchanged.

    Returns:
        ``(unit, warning)``. Older Lodestar may reject these flags.
    """
    unit = parse_unit(vc_content)
    args = normalize_cli_args(unit.exec_args)
    urls = _non_sidecar_urls(relays.urls)
    if not urls:
        raise EpbsError("No non-sidecar relay URLs to write as --builder.urls")
    args = upsert_flag(args, "--builder")
    args = upsert_flag(args, "--builder.urls", ",".join(urls))
    if relays.min_bid:
        args = upsert_flag(args, "--builder.minBid", eth_min_bid_to_gwei(relays.min_bid))
    args, warning = _apply_lodestar_build_factor(args, build_factor)
    return _rebuild_unit(vc_content, args), warning


def teku_builder_boost_factor(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Map a Teku build-factor token to ``--builder-boost-factor``.

    Thin wrapper around :func:`map_epbs_build_factor` so Teku and Prysm share
    one token table. Empty omits the flag (Teku 26.11.0 default is 100).
    The same value is also written to ``--p2p-builder-boost-factor``.

    Args:
        raw: Factor from :func:`resolve_teku_build_factor`, or empty.

    Returns:
        ``(flag_value, warning)``.
    """
    return map_epbs_build_factor(
        raw, enabled=True, client="Teku", flag="--builder-boost-factor"
    )


def promote_teku_xbuilder_args(args: Sequence[str]) -> List[str]:
    """Copy removed ``--Xbuilder-*`` values onto Teku 26.11.0 names, then drop them.

    ``--Xbuilder-min-bid`` and ``--Xbuilder-boost-factor`` applied to both
    configured builders and p2p bids through Teku 26.10.x. Each value is
    copied onto both production flags when that flag is absent. A production
    flag that is already set is left as-is. ``--Xbuilder-urls`` and
    ``--Xbuilder-max-execution-payment`` map one-to-one. Teku 26.11.0
    removed the ``--X`` names; they are not aliases (Consensys/teku#11563).

    Args:
        args: Normalized CLI tokens, including the binary.

    Returns:
        Argument list with no ``--Xbuilder-*`` flags.
    """
    out = list(args)
    for old, replacements in TEKU_XBUILDER_REPLACEMENTS:
        if not has_flag(out, old):
            continue
        value = get_flag_value(out, old)
        if value:
            for new in replacements:
                if not has_flag(out, new):
                    out = upsert_flag(out, new, value)
        out = remove_flags(out, old)
    return out


def promote_teku_xbuilder_unit(content: str) -> str:
    """Rewrite ``--Xbuilder-*`` on a Teku unit to the 26.11.0 flag names.

    Args:
        content: ``validator.service`` text.

    Returns:
        The same text when no ``--Xbuilder-*`` flag is present. Otherwise
        the unit with those flags promoted.
    """
    unit = parse_unit(content)
    args = normalize_cli_args(unit.exec_args)
    if not any(has_flag(args, old) for old, _news in TEKU_XBUILDER_REPLACEMENTS):
        return content
    return _rebuild_unit(content, promote_teku_xbuilder_args(args))


def _upsert_teku_pair(args: List[str], builder_flag: str, p2p_flag: str, value: str) -> List[str]:
    """Set the builder flag and its p2p twin to the same value.

    Args:
        args: Normalized CLI tokens.
        builder_flag: Flag for configured ``--builder-urls`` bids.
        p2p_flag: Flag for bids received over p2p.
        value: Shared uint64 value.

    Returns:
        Updated argument list.
    """
    args = upsert_flag(args, builder_flag, value)
    return upsert_flag(args, p2p_flag, value)


def apply_relays_teku(
    vc_content: str,
    relays: RelaysConfig,
    build_factor: str = "",
) -> Tuple[str, Optional[str]]:
    """Add Teku VC production ePBS flags (v26.11.0+).

    Builder URLs are the MEV-Boost relay list (sidecar URLs dropped), written
    as ``--builder-urls``. ``--builder-min-bid`` and ``--p2p-builder-min-bid``
    are MEV-Boost ``-min-bid`` in integer Gwei (the pre-26.11.0
    ``--Xbuilder-min-bid`` covered both). ``--builder-boost-factor`` and
    ``--p2p-builder-boost-factor`` are written only when *build_factor* maps
    to a value; an existing factor is left alone when the source is empty.
    Leftover ``--Xbuilder-*`` flags are promoted first so a unit prepared on
    Teku 26.9–26.10 still starts on 26.11.0.
    On Sepolia, ``--validators-builder-registration-default-gas-limit`` is
    set to 200000000 when that flag is absent (Teku has no 200M schedule).
    Pre-Gloas ``--validators-builder-registration-default-enabled`` is kept.

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays and optional min-bid from MEV-Boost. ``min_bid`` is ETH.
        build_factor: Raw factor token (env, BN compare-factor, or empty).

    Returns:
        ``(new_unit, warning)``. ``warning`` is set when the factor is
        invalid or was capped.

    Raises:
        EpbsError: If no non-sidecar relay URL remains.
    """
    unit = parse_unit(vc_content)
    args = promote_teku_xbuilder_args(normalize_cli_args(unit.exec_args))
    urls = _non_sidecar_urls(relays.urls)
    if not urls:
        raise EpbsError("No non-sidecar relay URLs to write as --builder-urls")
    args = upsert_flag(args, "--builder-urls", ",".join(urls))
    if relays.min_bid:
        gwei = eth_min_bid_to_gwei(relays.min_bid)
        args = _upsert_teku_pair(args, "--builder-min-bid", "--p2p-builder-min-bid", gwei)
    factor, warning = teku_builder_boost_factor(build_factor)
    if factor is not None:
        args = _upsert_teku_pair(
            args, "--builder-boost-factor", "--p2p-builder-boost-factor", factor
        )
    warnings: List[str] = []
    if warning:
        warnings.append(warning)
    if relays.network == "sepolia" and not has_flag(args, TEKU_SEPOLIA_GAS_FLAG):
        args = upsert_flag(args, TEKU_SEPOLIA_GAS_FLAG, SEPOLIA_GLOAS_GAS_LIMIT)
        warnings.append(TEKU_SEPOLIA_GAS_NOTE)
    return _rebuild_unit(vc_content, args), ("; ".join(warnings) or None)


def _apply_nimbus_boost_flag(args: List[str], raw: str) -> Tuple[List[str], Optional[str]]:
    """Set ``--builder-boost-factor`` for an ePBS-on Nimbus VC.

    Same token table as Prysm. A mapped value is written. An empty token
    removes a leftover ``0`` so Nimbus's default of 100 applies. Any other
    existing value is kept.

    Args:
        args: Normalized VC arguments.
        raw: Build-factor token, or empty.

    Returns:
        ``(args, warning)``.
    """
    value, warning = map_epbs_build_factor(
        raw, enabled=True, client="Nimbus", flag="--builder-boost-factor"
    )
    if value is not None:
        return upsert_flag(args, "--builder-boost-factor", value), warning
    if warning:
        return args, warning
    if get_flag_value(args, "--builder-boost-factor") == "0":
        args = remove_flags(args, "--builder-boost-factor")
    return args, None


def validate_nimbus_builder_urls(urls: Sequence[str]) -> None:
    """Reject builder URLs that Nimbus would silently drop.

    Nimbus ``buildBuilderConfig`` (v26.10.2) skips empty URLs and those longer
    than :data:`NIMBUS_MAX_BUILDER_URL_SIZE` with no log, and stops adding
    after :data:`NIMBUS_MAX_BUILDER_ENTRIES` with a silent ``break``. EthPillar
    refuses those lists instead of writing a partial VC config.

    Args:
        urls: Non-sidecar builder URLs (already cleaned).

    Raises:
        EpbsError: If there are more than
            :data:`NIMBUS_MAX_BUILDER_ENTRIES` URLs, or any URL exceeds
            :data:`NIMBUS_MAX_BUILDER_URL_SIZE` UTF-8 bytes, or a URL has no
            hostname (``get_default_auth_data`` would skip that builder).
    """
    if len(urls) > NIMBUS_MAX_BUILDER_ENTRIES:
        raise EpbsError(
            "Nimbus accepts at most "
            f"{NIMBUS_MAX_BUILDER_ENTRIES} --payload-builder-url entries "
            f"(got {len(urls)}; MAX_BUILDER_ENTRIES). Extra URLs would be "
            "dropped silently by buildBuilderConfig."
        )
    client = f"Nimbus v{NIMBUS_EPBS_MIN_VERSION}"
    for url in urls:
        builder_url_hostname(url, client=client)
        size = len(url.encode("utf-8"))
        if size > NIMBUS_MAX_BUILDER_URL_SIZE:
            shown = url if len(url) <= 96 else f"{url[:93]}…"
            raise EpbsError(
                "Nimbus builder URL exceeds "
                f"{NIMBUS_MAX_BUILDER_URL_SIZE} bytes "
                f"(got {size}; MAX_BUILDER_URL_SIZE): {shown}"
            )


def apply_relays_nimbus(
    vc_content: str,
    relays: RelaysConfig,
    build_factor: str = "",
) -> Tuple[str, Optional[str]]:
    """Add Nimbus VC builder flags for one or more relays (v26.10.2+).

    Writes ``--payload-builder=true`` and one ``--payload-builder-url`` per
    non-sidecar relay (repeatable ``seq[string]``; nimbus-eth2#9336).
    ``buildBuilderConfig`` adds each URL as a ``BuilderEntry`` on Gloas
    ``produceBlockV4``. ``--builder-min-bid`` is MEV-Boost ``-min-bid`` in
    integer Gwei. ``--builder-max-execution-payment=0`` caps execution
    payment at zero (trustless-only; the Nimbus default is uint64 max).
    ``--builder-boost-factor`` follows :func:`_apply_nimbus_boost_flag`.
    On Sepolia, ``--suggested-gas-limit`` is set to 200000000 when absent
    because proposer preferences copy that flag into ``target_gas_limit``.

    Args:
        vc_content: Current ``validator.service`` text.
        relays: Relays and optional min-bid from MEV-Boost. ``min_bid`` is ETH.
        build_factor: Raw factor token (empty omits a new value).

    Returns:
        ``(new_unit, warning)``.

    Raises:
        EpbsError: If no non-sidecar relay remains, a URL has no hostname,
            there are more than :data:`NIMBUS_MAX_BUILDER_ENTRIES` URLs, or a
            URL exceeds :data:`NIMBUS_MAX_BUILDER_URL_SIZE` bytes (Nimbus
            would drop those silently).
    """
    urls = _non_sidecar_urls(relays.urls)
    if not urls:
        raise EpbsError(
            "No non-sidecar relay URLs to write as --payload-builder-url"
        )
    validate_nimbus_builder_urls(urls)
    unit = parse_unit(vc_content)
    args = normalize_cli_args(unit.exec_args)
    args = upsert_flag(args, "--payload-builder", "true")
    if relays.min_bid:
        args = upsert_flag(args, "--builder-min-bid", eth_min_bid_to_gwei(relays.min_bid))
    else:
        args = remove_flags(args, "--builder-min-bid")
    args = upsert_flag(args, "--builder-max-execution-payment", "0")
    args, warning = _apply_nimbus_boost_flag(args, build_factor)
    # URLs last among builder flags so re-prepare keeps a stable order.
    args = replace_repeated_flag(args, "--payload-builder-url", urls)
    warnings: List[str] = []
    if warning:
        warnings.append(warning)
    if relays.network == "sepolia" and not has_flag(args, NIMBUS_SUGGESTED_GAS_FLAG):
        args = upsert_flag(args, NIMBUS_SUGGESTED_GAS_FLAG, SEPOLIA_GLOAS_GAS_LIMIT)
        warnings.append(NIMBUS_SEPOLIA_GAS_NOTE)
    return _rebuild_unit(vc_content, args), ("; ".join(warnings) or None)


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


def teku_supports_epbs(fs: EpbsFilesystem, unit_content: str) -> bool:
    """True when the Teku binary is at least v26.11.0 (production ePBS flags).

    Teku 26.9.0–26.10.x only accept hidden ``--Xbuilder-*`` names. 26.11.0
    removed those names (Consensys/teku#11563), so the floor is the first
    release that accepts ``--builder-urls``.

    Args:
        fs: IO adapter used to run ``teku --version``.
        unit_content: ``validator.service`` text (binary path).

    Returns:
        True for Teku v26.11.0 or newer. Older or unreadable binaries are
        False so prepare does not write flags the release will reject.
    """
    args = normalize_cli_args(parse_unit(unit_content).exec_args)
    if not args:
        return False
    binary = args[0].split()[0]
    text = _binary_version(fs, [binary, "--version"])
    return _version_meets_floor(text, TEKU_EPBS_MIN_VERSION)


def promote_installed_teku_flags(fs: EpbsFilesystem, apply: bool = False) -> bool:
    """Rewrite ``--Xbuilder-*`` on ``validator.service`` when Teku is 26.11.0+.

    No-op when the unit is missing, is not Teku, has no ``--Xbuilder-*``
    flag, or ``teku --version`` is older than :data:`TEKU_EPBS_MIN_VERSION`
    (those binaries still require the hidden names).

    Args:
        fs: IO adapter.
        apply: If True, write the unit. If False, only report whether it
            would change.

    Returns:
        True when the unit text differs after promotion.
    """
    path = fs.unit_path("validator")
    if not fs.exists(path):
        return False
    content = fs.read_text(path) or ""
    if not content or parse_unit(content).client != "Teku":
        return False
    if not teku_supports_epbs(fs, content):
        return False
    new_content = promote_teku_xbuilder_unit(content)
    return _write_unit_if_changed(fs, path, content, new_content, apply)


def _unit_version_text(fs: EpbsFilesystem, unit_content: str) -> str:
    """Return ``binary --version`` text for the unit's first ExecStart token.

    Args:
        fs: IO adapter; ``run_version`` short-circuits subprocess in tests.
        unit_content: Unit file text.

    Returns:
        Version command output, or ``""`` when the binary cannot be run.
    """
    args = normalize_cli_args(parse_unit(unit_content).exec_args)
    if not args:
        return ""
    binary = args[0].split()[0]
    return _binary_version(fs, [binary, "--version"])


def prysm_supports_builder_cli(fs: EpbsFilesystem, unit_content: str) -> bool:
    """True when the Prysm VC binary is at least v7.2.1 (builder CLI flags).

    Uses :func:`_version_meets_floor` (the same floor check as Teku and
    Caplin). An unreadable binary is False so prepare does not write flags
    an older release will reject.

    Args:
        fs: IO adapter used to run ``prysm-validator --version``.
        unit_content: ``validator.service`` text.
    """
    return _version_meets_floor(
        _unit_version_text(fs, unit_content), PRYSM_BUILDER_CLI_MIN_VERSION
    )


def nimbus_supports_epbs(fs: EpbsFilesystem, unit_content: str) -> bool:
    """True when the Nimbus VC binary is at least v26.10.2.

    That release makes ``--payload-builder-url`` a repeatable list on the
    external validator client and feeds every URL into Gloas
    ``produceBlockV4`` via ``buildBuilderConfig``. Older or unreadable
    binaries are False so prepare does not write flags a release will
    reject (v26.10.1 accepted only one URL).

    Args:
        fs: IO adapter used to run ``nimbus_validator_client --version``.
        unit_content: ``validator.service`` text.
    """
    return _version_meets_floor(
        _unit_version_text(fs, unit_content), NIMBUS_EPBS_MIN_VERSION
    )


def nimbus_payload_builder_enabled(args: Sequence[str]) -> bool:
    """Return True when ``--payload-builder`` is set and not explicitly off.

    Nimbus defaults the flag to false. ``false``, ``0``, and ``no`` disable
    it. A bare flag or any other value enables it. ``buildBuilderConfig``
    returns no builders when the flag is off, even if URLs are set.

    Args:
        args: Normalized VC arguments.
    """
    if not has_flag(args, "--payload-builder"):
        return False
    value = get_flag_value(args, "--payload-builder").strip().lower()
    return value not in {"false", "0", "no"}


def nimbus_has_builder_url(vc_content: str) -> bool:
    """Return True when the VC has an enabled non-sidecar builder URL.

    Collects every ``--payload-builder-url`` value (repeatable since
    v26.10.2). A comma inside one value is not expanded: each flag is one
    URL, matching Nimbus ``seq[string]`` parsing.

    Args:
        vc_content: ``validator.service`` text.
    """
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    if not nimbus_payload_builder_enabled(args):
        return False
    return any(
        url.strip() and not is_sidecar_url(url)
        for url in get_flag_values(args, "--payload-builder-url")
    )


def resolve_epbs_build_factor(fs: EpbsFilesystem, client: str) -> str:
    """Return the raw build-factor token for *client*, or empty when unset.

    Teku reads ``TEKU_EPBS_BUILD_FACTOR`` first, then the shared
    ``EPBS_BUILD_FACTOR``, then the Teku beacon node's
    ``--builder-bid-compare-factor``. Other clients read only
    ``EPBS_BUILD_FACTOR``.

    Args:
        fs: IO adapter used to read ``consensus.service`` for Teku.
        client: Validator client name (``Teku``, ``Prysm``, ``Lodestar``).
    """
    if client == "Teku":
        specific = os.environ.get(TEKU_EPBS_BUILD_FACTOR_ENV, "").strip()
        if specific:
            return specific
    shared = os.environ.get(EPBS_BUILD_FACTOR_ENV, "").strip()
    if shared:
        return shared
    if client != "Teku":
        return ""
    path = fs.unit_path("consensus")
    if not fs.exists(path):
        return ""
    content = fs.read_text(path) or ""
    unit = parse_unit(content)
    if unit.client != "Teku":
        return ""
    args = normalize_cli_args(unit.exec_args)
    return get_flag_value(args, "--builder-bid-compare-factor").strip()


def resolve_teku_build_factor(fs: EpbsFilesystem) -> str:
    """Return the raw Teku build-factor token, or empty when unset.

    Args:
        fs: IO adapter used to read ``consensus.service``.
    """
    return resolve_epbs_build_factor(fs, "Teku")


def prysm_has_builder_urls(vc_content: str) -> bool:
    """Return True when ``--builder-urls`` includes a non-sidecar URL.

    Args:
        vc_content: ``validator.service`` text.
    """
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    raw = get_flag_value(args, "--builder-urls")
    if not raw:
        return False
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return any(not is_sidecar_url(part) for part in parts)


def teku_has_builder_urls(vc_content: str) -> bool:
    """Return True when Teku builder URLs include a non-sidecar URL.

    Accepts production ``--builder-urls`` and a leftover ``--Xbuilder-urls``
    so a unit prepared before 26.11.0 still counts until it is rewritten.

    Args:
        vc_content: ``validator.service`` text.
    """
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    raw = get_flag_value(args, "--builder-urls") or get_flag_value(args, "--Xbuilder-urls")
    if not raw:
        return False
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return any(not is_sidecar_url(part) for part in parts)


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



def lighthouse_supports_builder_definitions(fs: EpbsFilesystem, vc_content: str) -> bool:
    """True when this Lighthouse binary loads ``builder_definitions.yml``.

    v8.3.0 is the first stable release that reads that file (Gloas builders).
    The feature also shipped in v8.3.0-rc.0; :func:`_version_meets_floor`
    treats that RC as meeting the v8.3.0 base floor.

    Args:
        fs: IO adapter used to run ``lighthouse --version``.
        vc_content: Current ``validator.service`` text (binary path).

    Returns:
        True when ``lighthouse --version`` is at least
        :data:`LIGHTHOUSE_BUILDERS_MIN_VERSION`.
    """
    return _version_meets_floor(
        _unit_version_text(fs, vc_content), LIGHTHOUSE_BUILDERS_MIN_VERSION
    )


def lighthouse_builders_file(vc_content: str, override: str = "") -> str:
    """Return the path of ``builder_definitions.yml`` for this validator unit.

    Lighthouse reads ``<validators-dir>/builder_definitions.yml``. EthPillar
    sets ``--datadir``, and the validators directory is ``<datadir>/validators``
    unless ``--validators-dir`` is set.

    Args:
        vc_content: Current ``validator.service`` text.
        override: Explicit path (tests / ``--lighthouse-builders``). Empty
            derives the path from the unit.

    Returns:
        Absolute or unit-relative path of the builders file.
    """
    if override:
        return override
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    validators_dir = get_flag_value(args, "--validators-dir", "--validator-dir")
    if not validators_dir:
        datadir = get_flag_value(args, "--datadir") or (
            f"{BASE_DATA_DIR}/lighthouse_validator"
        )
        validators_dir = os.path.join(datadir, LIGHTHOUSE_VALIDATORS_DIRNAME)
    return os.path.join(validators_dir, LIGHTHOUSE_BUILDERS_FILENAME)


def _load_yaml_mapping(text: Optional[str], label: str) -> dict:
    """Parse *text* as a YAML mapping.

    Args:
        text: File contents. Empty or missing becomes ``{}``.
        label: Operator-facing name used in errors.

    Returns:
        A dict. ``None`` (empty document) becomes ``{}``.

    Raises:
        EpbsError: If the document is not valid YAML or not a mapping.
    """
    if not text or not text.strip():
        return {}
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EpbsError(f"Invalid {label}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise EpbsError(f"{label} must be a YAML mapping")
    return loaded


def _dump_lighthouse_builders(data: dict) -> str:
    """Serialize a Lighthouse builder config to stable YAML.

    Args:
        data: Mapping written to ``builder_definitions.yml``.

    Returns:
        YAML text ending in a newline. Keys keep insertion order. Long relay
        URLs are not folded.
    """
    dumped = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
        width=4096,
    )
    if not dumped.endswith("\n"):
        dumped += "\n"
    return dumped


def lighthouse_has_builder_list(data: dict) -> bool:
    """Return True when enabled builders include a non-sidecar URL.

    Args:
        data: Parsed ``builder_definitions.yml`` mapping.

    Returns:
        True when some enabled ``builders[].url`` is set and is not the local
        sidecar. Disabled entries and an explicit empty list do not count.
    """
    entries = data.get("builders")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("enabled") is not True:
            continue
        url = entry.get("url")
        if isinstance(url, str) and url.strip() and not is_sidecar_url(url):
            return True
    return False


def _yaml_tree_has_gas_limit(node: object) -> bool:
    """Return True when a YAML tree sets a non-zero ``gas_limit``.

    Args:
        node: Parsed YAML value (mapping, list, or scalar).

    Returns:
        True if any mapping contains ``gas_limit`` other than empty or ``0``.
    """
    if isinstance(node, dict):
        if "gas_limit" in node:
            value = node.get("gas_limit")
            if value is not None and str(value).strip() not in ("", "0"):
                return True
        return any(_yaml_tree_has_gas_limit(value) for value in node.values())
    if isinstance(node, list):
        return any(_yaml_tree_has_gas_limit(value) for value in node)
    return False


def lighthouse_explicit_gas_limit(vc_content: str, definitions_text: Optional[str]) -> bool:
    """Return True when the VC overrides Lighthouse's gas-limit schedule.

    ``--gas-limit`` on the unit and ``gas_limit`` in ``validator_definitions.yml``
    both take precedence over ``GAS_LIMIT_SCHEDULE``.

    Args:
        vc_content: Current ``validator.service`` text.
        definitions_text: ``validator_definitions.yml`` contents, or None.

    Returns:
        True when either override is set to a non-zero gas limit.
    """
    args = normalize_cli_args(parse_unit(vc_content).exec_args)
    if get_flag_value(args, "--gas-limit").strip() not in ("", "0"):
        return True
    if not definitions_text or not definitions_text.strip():
        return False
    try:
        loaded = yaml.safe_load(definitions_text)
    except yaml.YAMLError:
        return False
    return _yaml_tree_has_gas_limit(loaded)


def apply_relays_lighthouse(relays: RelaysConfig, existing_yaml: Optional[str]) -> str:
    """Merge mev-boost relays into Lighthouse ``builder_definitions.yml``.

    Writes a global ``builders`` list. Each entry is ``enabled: true``, ``url``
    set to the relay URL, and ``max_execution_payment: 0`` (trustless-only:
    collateral-backed bid value counts; a builder's promised execution-layer
    payment does not). ``auth_data`` is omitted so Lighthouse signs the URL
    hostname. Global ``min_bid`` is MEV-Boost ``-min-bid`` in integer Gwei, or
    ``0`` when unset. An existing ``builder_boost_factor`` and
    ``validator_configs`` map are kept. Does not write ``gas_limit``.

    Args:
        relays: Relay URLs and optional MEV-Boost min-bid (ETH).
        existing_yaml: Current builders file, or None.

    Returns:
        Canonical YAML for ``builder_definitions.yml``.

    Raises:
        EpbsError: If the existing file is not a YAML mapping or
            ``builder_boost_factor`` is not an integer.
    """
    data = _load_yaml_mapping(existing_yaml, "builder_definitions.yml")
    boost = data.get("builder_boost_factor", 100)
    try:
        boost_int = int(boost)
    except (TypeError, ValueError) as exc:
        raise EpbsError(
            "builder_definitions.yml builder_boost_factor must be an integer, "
            f"got {boost!r}"
        ) from exc
    min_bid = int(eth_min_bid_to_gwei(relays.min_bid)) if relays.min_bid else 0
    seen = set()
    builders: List[dict] = []
    for url in relays.urls:
        if url in seen:
            continue
        seen.add(url)
        builders.append(
            {
                "enabled": True,
                "url": url,
                "max_execution_payment": 0,
            }
        )
    out: dict = {
        "min_bid": min_bid,
        "builder_boost_factor": boost_int,
        "builders": builders,
    }
    validator_configs = data.get("validator_configs")
    if isinstance(validator_configs, dict) and validator_configs:
        out["validator_configs"] = validator_configs
    known = {"min_bid", "builder_boost_factor", "builders", "validator_configs"}
    for key, value in data.items():
        if key not in known:
            out[key] = value
    return _dump_lighthouse_builders(out)


def apply_relays_placeholder(client: str) -> str:
    """Return a planned-flag blurb; do not mutate units.

    Args:
        client: Placeholder VC name (Grandine).

    Returns:
        Human-readable description of the unreleased relay-list surface.
    """
    planned = {
        "Grandine": "multi --builder-url list (not shipped; single --builder-url today)",
    }
    return planned.get(client, "no VC relay-list flag shipped")


def strip_bn_sidecar(bn_content: str, bn_client: str) -> str:
    """Remove BN flags whose value is the local mev-boost listen address.

    Non-sidecar builder URLs are kept. Lodestar's boolean ``--builder`` and
    Nimbus's ``--payload-builder`` are dropped only when no builder URL
    remains.

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
    # Prefer mevboost.service. A native ePBS install has no sidecar unit;
    # export the VC relay list instead of failing closed.
    relays = load_relays_for_tooling(fs)
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
        version_text = _unit_version_text(fs, vc_content)
        builder_cli = _version_meets_floor(version_text, PRYSM_BUILDER_CLI_MIN_VERSION)
        if not version_text.strip():
            plan.warnings.append(
                "Could not read prysm-validator --version; using the "
                "proposer-settings path. Install Prysm "
                f"{PRYSM_BUILDER_CLI_MIN_VERSION}+ for --builder-urls."
            )
        elif not builder_cli:
            plan.warnings.append(
                "Prysm binary is older than "
                f"{PRYSM_BUILDER_CLI_MIN_VERSION}; builder CLI flags are not "
                "written. Upgrade for --builder-urls and the Sepolia 200M "
                "gas schedule."
            )
        vc_args = normalize_cli_args(parse_unit(vc_content).exec_args)
        settings_path = (
            get_flag_value(vc_args, "--proposer-settings-file") or fs.prysm_settings_path
        )
        existing = fs.read_text(settings_path)
        factor_raw = resolve_epbs_build_factor(fs, "Prysm")
        new_vc, settings_json, settings_path, factor_warning = apply_relays_prysm(
            vc_content,
            relays,
            existing,
            settings_path=settings_path,
            builder_cli=builder_cli,
            build_factor=factor_raw,
        )
        if factor_warning:
            plan.warnings.append(factor_warning)
        changed_vc = new_vc != vc_content
        changed_json = settings_json is not None and (existing or "") != settings_json
        if changed_vc:
            plan.actions.append(PlanAction(vc_path, _prysm_flag_action(vc_content, new_vc)))
        if settings_json is not None:
            detail = "write default_config.builder.builders (schema v2)"
            if builder_cli:
                detail += "; kept in sync with --builder-urls (file replaces CLI defaults)"
            plan.actions.append(PlanAction(settings_path, detail))
        if builder_cli and has_flag(
            normalize_cli_args(parse_unit(new_vc).exec_args), "--suggested-gas-limit"
        ):
            plan.warnings.append(PRYSM_SUGGESTED_GAS_LIMIT_NOTE)
        elif not builder_cli and relays.network == "sepolia":
            try:
                written = json.loads(settings_json or "{}")
            except json.JSONDecodeError:
                written = {}
            if isinstance(written, dict) and not _prysm_explicit_gas_limit(written):
                plan.warnings.append(SEPOLIA_GAS_LIMIT_NOTE)
        if apply:
            if changed_vc:
                _write_unit_if_changed(fs, vc_path, vc_content, new_vc, True)
            if settings_json is not None:
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
            factor_raw = resolve_epbs_build_factor(fs, "Lodestar")
            new_vc, factor_warning = apply_relays_lodestar(
                vc_content, relays, factor_raw
            )
            if factor_warning:
                plan.warnings.append(factor_warning)
            if _write_unit_if_changed(fs, vc_path, vc_content, new_vc, apply):
                detail = "add --builder --builder.urls --builder.minBid"
                if factor_raw.strip():
                    detail += " and build-factor selection"
                plan.actions.append(
                    PlanAction(
                        vc_path,
                        detail,
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
    elif vc_name == "Teku":
        if not teku_supports_epbs(fs, vc_content):
            plan.actions.append(
                PlanAction(
                    "Teku VC",
                    "skipped: teku --version is older than "
                    f"{TEKU_EPBS_MIN_VERSION} (no --builder-urls)",
                )
            )
            plan.warnings.append(
                "Prepare: no-op on this Teku build — Complete will stop "
                "MEV-Boost without a VC relay replacement. Install Teku "
                f"{TEKU_EPBS_MIN_VERSION} or later (production --builder-urls "
                "/ --builder-min-bid / --p2p-builder-min-bid / "
                "--builder-boost-factor / --p2p-builder-boost-factor; "
                "the hidden --Xbuilder-* names were removed)."
            )
        else:
            factor_raw = resolve_teku_build_factor(fs)
            new_vc, factor_warning = apply_relays_teku(
                vc_content, relays, factor_raw
            )
            if factor_warning:
                plan.warnings.append(factor_warning)
            factor_value, _ = teku_builder_boost_factor(factor_raw)
            detail_parts = ["add --builder-urls"]
            if relays.min_bid:
                detail_parts.append("--builder-min-bid --p2p-builder-min-bid")
            if factor_value is not None:
                detail_parts.append(
                    f"--builder-boost-factor={factor_value} "
                    f"--p2p-builder-boost-factor={factor_value}"
                )
            if _write_unit_if_changed(fs, vc_path, vc_content, new_vc, apply):
                plan.actions.append(PlanAction(vc_path, " ".join(detail_parts)))
                plan.services_to_restart.append("validator")
            else:
                plan.warnings.append(
                    "Teku VC already has these builder flags; nothing to change."
                )
    elif vc_name == "Nimbus":
        if not nimbus_supports_epbs(fs, vc_content):
            plan.actions.append(
                PlanAction(
                    "Nimbus VC",
                    "skipped: nimbus_validator_client --version is older than "
                    f"{NIMBUS_EPBS_MIN_VERSION} (no multi-URL Gloas builders)",
                )
            )
            plan.warnings.append(
                "Prepare: no-op on this Nimbus build — Complete will stop "
                "MEV-Boost without a VC relay replacement. Install Nimbus "
                f"{NIMBUS_EPBS_MIN_VERSION} or later (first release whose VC "
                "accepts repeated --payload-builder-url)."
            )
        else:
            factor_raw = resolve_epbs_build_factor(fs, "Nimbus")
            new_vc, factor_warning = apply_relays_nimbus(
                vc_content, relays, factor_raw
            )
            if factor_warning:
                plan.warnings.append(factor_warning)
            detail = (
                "add --payload-builder=true --payload-builder-url "
                "(repeatable) --builder-max-execution-payment=0"
            )
            if relays.min_bid:
                detail += " --builder-min-bid"
            factor_value, _ = map_epbs_build_factor(
                factor_raw, enabled=True, client="Nimbus", flag="--builder-boost-factor"
            )
            if factor_value is not None:
                detail += f" --builder-boost-factor={factor_value}"
            if _write_unit_if_changed(fs, vc_path, vc_content, new_vc, apply):
                plan.actions.append(PlanAction(vc_path, detail))
                plan.services_to_restart.append("validator")
            else:
                plan.warnings.append(
                    "Nimbus VC already has these builder URLs; nothing to change."
                )
    elif vc_name == "Lighthouse":
        if not lighthouse_supports_builder_definitions(fs, vc_content):
            plan.actions.append(
                PlanAction(
                    "Lighthouse VC",
                    "skipped: lighthouse --version is older than "
                    f"v{LIGHTHOUSE_BUILDERS_MIN_VERSION} "
                    "(no builder_definitions.yml)",
                )
            )
            plan.warnings.append(
                "Prepare: no-op on this Lighthouse build — Complete will stop "
                "MEV-Boost without a VC builder list. Install Lighthouse "
                f"v{LIGHTHOUSE_BUILDERS_MIN_VERSION} or later."
            )
        else:
            builders_path = lighthouse_builders_file(
                vc_content, fs.lighthouse_builders_path
            )
            existing = fs.read_text(builders_path)
            new_yaml = apply_relays_lighthouse(relays, existing)
            definitions = fs.read_text(
                os.path.join(os.path.dirname(builders_path), "validator_definitions.yml")
            )
            network = (relays.network or "").strip().lower()
            if network in ("sepolia", "hoodi"):
                if lighthouse_explicit_gas_limit(vc_content, definitions):
                    plan.warnings.append(LIGHTHOUSE_GAS_OVERRIDE_NOTE)
                elif network == "sepolia":
                    plan.warnings.append(LIGHTHOUSE_SEPOLIA_GAS_NOTE)
                else:
                    plan.warnings.append(LIGHTHOUSE_HOODI_GAS_NOTE)
            changed = (existing or "") != new_yaml
            plan.actions.append(
                PlanAction(
                    builders_path,
                    "write builders[].url (builder_definitions.yml)",
                )
            )
            if apply and changed:
                if existing:
                    _backup(builders_path, fs)
                writer = fs.write_data or _default_write_data
                writer(builders_path, new_yaml)
            if changed:
                plan.services_to_restart.append("validator")
            else:
                plan.warnings.append(
                    "Lighthouse VC already has these builders; nothing to change."
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


def gwei_min_bid_to_eth(gwei: str) -> str:
    """Convert an integer-Gwei min-bid back to a MEV-Boost ETH string.

    Args:
        gwei: Gwei amount written by prepare (``6000000`` for ``0.006`` ETH).

    Returns:
        ETH decimal string, or ``""`` when *gwei* is not a non-negative number.
    """
    try:
        value = Decimal(str(gwei).strip())
    except (InvalidOperation, AttributeError):
        return ""
    if value < 0:
        return ""
    eth = value / GWEI_PER_ETH
    text = format(eth, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _urls_from_builder_entries(entries: object) -> List[str]:
    """Return non-sidecar ``url`` strings from a builders list."""
    urls: List[str] = []
    if not isinstance(entries, list):
        return urls
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if isinstance(url, str) and url.strip() and not is_sidecar_url(url):
            urls.append(url.strip())
    return urls


def _split_csv_urls(raw: str) -> List[str]:
    """Split a comma-separated URL flag, dropping sidecar URLs."""
    return [
        part.strip()
        for part in (raw or "").split(",")
        if part.strip() and not is_sidecar_url(part.strip())
    ]


def parse_vc_relays(fs: EpbsFilesystem) -> RelaysConfig:
    """Read ePBS relay URLs (and min-bid, as ETH) from the validator client.

    Used when ``mevboost.service`` is absent: a native ePBS install, or a
    node that already completed migration. Min-bid flags stored as integer
    Gwei are converted back to ETH so export matches :class:`RelaysConfig`.

    Args:
        fs: IO adapter.

    Returns:
        Relays configured on the VC.

    Raises:
        EpbsError: No VC unit exists, the client has no relay list, or the
            list is empty.
    """
    vc_name, _bn_name, mode = detect_clients(fs)
    if mode == "none" or not vc_name:
        raise EpbsError(
            "No relays to read: mevboost.service is absent and no validator "
            "client unit was found."
        )
    if vc_name == "Grandine" or mode == "integrated_grandine":
        raise EpbsError("Grandine has no ePBS relay list to read.")

    if mode == "integrated_caplin" or vc_name == "Erigon-Caplin":
        vc_key = "execution"
    else:
        vc_key = "validator"
    _path, content = _read_required_unit(fs, vc_key)
    unit = parse_unit(content)
    args = normalize_cli_args(unit.exec_args)
    urls: List[str] = []
    min_bid = ""

    if vc_name == "Prysm":
        for raw in get_flag_values(args, "--builder-urls"):
            urls.extend(_split_csv_urls(raw))
        gwei = get_flag_value(args, "--builder-min-bid")
        if gwei:
            min_bid = gwei_min_bid_to_eth(gwei)
        if not urls:
            data = _load_prysm_settings(fs, content) or {}
            default = data.get("default_config") if isinstance(data, dict) else None
            builder = default.get("builder") if isinstance(default, dict) else None
            if isinstance(builder, dict):
                urls.extend(_urls_from_builder_entries(builder.get("builders")))
                if not min_bid and builder.get("min_bid") not in (None, ""):
                    min_bid = gwei_min_bid_to_eth(str(builder.get("min_bid")))
    elif vc_name == "Lodestar":
        urls.extend(_split_csv_urls(get_flag_value(args, "--builder.urls")))
        gwei = get_flag_value(args, "--builder.minBid")
        if gwei:
            min_bid = gwei_min_bid_to_eth(gwei)
    elif vc_name == "Teku":
        raw_urls = get_flag_value(args, "--builder-urls") or get_flag_value(
            args, "--Xbuilder-urls"
        )
        urls.extend(_split_csv_urls(raw_urls))
        gwei = get_flag_value(args, "--builder-min-bid") or get_flag_value(
            args, "--Xbuilder-min-bid"
        )
        if gwei:
            min_bid = gwei_min_bid_to_eth(gwei)
    elif vc_name == "Nimbus":
        for raw in get_flag_values(args, "--payload-builder-url"):
            if raw and not is_sidecar_url(raw):
                urls.append(raw.strip())
        gwei = get_flag_value(args, "--builder-min-bid")
        if gwei:
            min_bid = gwei_min_bid_to_eth(gwei)
    elif vc_name == "Lighthouse":
        path = lighthouse_builders_file(content, fs.lighthouse_builders_path)
        raw = fs.read_text(path) or ""
        loaded = _load_yaml_mapping(raw, "builder_definitions.yml") if raw.strip() else {}
        if isinstance(loaded, dict):
            urls.extend(_urls_from_builder_entries(loaded.get("builders")))
            if loaded.get("min_bid") not in (None, ""):
                min_bid = gwei_min_bid_to_eth(str(loaded.get("min_bid")))
    elif vc_name == "Erigon-Caplin":
        loaded = _load_caplin_builders(fs) or {}
        urls.extend(_urls_from_builder_entries(loaded.get("builders")))
        if loaded.get("min_bid") not in (None, ""):
            min_bid = gwei_min_bid_to_eth(str(loaded.get("min_bid")))
    else:
        raise EpbsError(
            f"{vc_name} does not support an ePBS relay list. "
            "Refusing to pretend relays were found."
        )

    if not urls:
        raise EpbsError(
            f"No ePBS relay URLs found on the {vc_name} validator client."
        )
    return RelaysConfig(urls=urls, min_bid=min_bid, network=unit.network)


def load_relays_for_tooling(fs: Optional[EpbsFilesystem] = None) -> RelaysConfig:
    """Return relays from MEV-Boost, or from the VC when that unit is absent.

    Registration, latency, and export checks use this. :func:`prepare` still
    reads ``mevboost.service`` only, via :func:`_load_relays`.

    Args:
        fs: IO adapter; production defaults if omitted.

    Returns:
        Relay URLs and optional min-bid.

    Raises:
        EpbsError: Neither MEV-Boost nor the VC has a relay list.
    """
    fs = fs or EpbsFilesystem()
    path = fs.unit_path("mevboost")
    if fs.exists(path):
        content = fs.read_text(path) or ""
        cfg = parse_mevboost_relays(content)
        if cfg.urls:
            return cfg
    return parse_vc_relays(fs)


def _fresh_install_skip_reason(plan: MigrationPlan) -> str:
    """Return why a fresh ePBS apply did not write relays, or ``""``.

    Version-gate skips and placeholder clients are failures. An idempotent
    "nothing to change" result is success.
    """
    if plan.support != "full":
        return f"{plan.client} support is {plan.support or 'unknown'}"
    client_actions = [
        action
        for action in plan.actions
        if action.target not in ("mevboost min-bid", "relays")
    ]
    if not client_actions:
        return "no validator relay changes were planned"
    for action in client_actions:
        detail = (action.detail or "").lower()
        target = (action.target or "").lower()
        if "skipped" in detail or "placeholder" in target or "no-op" in detail:
            return f"{action.target}: {action.detail}"
    return ""


def apply_fresh_install_relays(
    relays: RelaysConfig,
    fs: Optional[EpbsFilesystem] = None,
) -> MigrationPlan:
    """Write *relays* onto a freshly installed validator client.

    Does not install or require ``mevboost.service`` and does not add beacon
    node sidecar flags. Reuses :func:`_apply_vc_relays` with ``apply=True``
    and ``min_bid`` from the caller (install uses ``MEV_MIN_BID``). A client
    or binary that would only skip prepare raises :class:`EpbsError`.

    Args:
        relays: Selected relay URLs, ETH min-bid, and network.
        fs: IO adapter; production defaults if omitted.

    Returns:
        The applied plan.

    Raises:
        EpbsError: No VC, empty relay list, Charon is installed, the client
            is not ``full`` support, or the version gate skipped the write.
    """
    fs = fs or EpbsFilesystem()
    if charon_installed(fs):
        raise EpbsError(
            "Obol Charon has no ePBS support yet. Refusing to write validator "
            "relays that would bypass Charon."
        )
    if not _non_sidecar_urls(relays.urls):
        raise EpbsError(
            "Native ePBS requires at least one non-sidecar relay URL. "
            "Refusing to install with an empty builder list."
        )
    vc_name, _bn_name, mode = detect_clients(fs)
    if mode == "none" or not vc_name:
        raise EpbsError(
            "Native ePBS requires a validator client unit, but none was found."
        )
    level = support_level(vc_name)
    if level != "full":
        raise EpbsError(
            f"{vc_name} does not support native ePBS (support level: {level}). "
            "Refusing to skip relay setup."
        )
    plan = MigrationPlan(
        command="install",
        client=vc_name,
        support=level,
        notes=SUPPORT_NOTES.get(vc_name, ""),
    )
    _apply_vc_relays(
        fs, relays, True, plan, vc_name, mode, relays_source="install"
    )
    plan.applied = True
    skipped = _fresh_install_skip_reason(plan)
    if skipped:
        warning = "; ".join(plan.warnings)
        message = f"Native ePBS did not write relays ({skipped})."
        if warning:
            message = f"{message} {warning}"
        raise EpbsError(message)
    return plan


def prepare(fs: Optional[EpbsFilesystem] = None, apply: bool = False) -> MigrationPlan:
    """Copy mev-boost relays onto the VC. Keep the sidecar running.

    Prysm v7.2.1+ writes ``--builder-urls`` (and keeps an existing
    proposer-settings file in sync). Older Prysm writes proposer-settings
    JSON. Lodestar gets ``--builder.urls`` when the binary documents that
    flag. Teku gets ``--builder-urls`` (plus builder and p2p min-bid, boost
    factor, and on Sepolia an explicit 200M gas limit) when ``teku --version``
    is at least v26.11.0. Nimbus v26.10.2+ gets repeated ``--payload-builder-url``
    (plus min-bid, max execution payment, boost factor, and on Sepolia an
    explicit 200M gas limit) when ``nimbus_validator_client --version`` is
    new enough. Erigon-Caplin writes ``caplin-builders.json`` when
    ``erigon --version`` is at least v3.7.1 and leaves
    ``--caplin.mev-relay-url`` in place. Lighthouse writes
    ``builder_definitions.yml`` when ``lighthouse --version`` is at least
    v8.3.0. Other VCs are a documented no-op.
    When Charon is installed, VC relay writes are skipped (Charon
    ``--builder-api`` owns the MEV path until complete). Beacon-node
    sidecar flags stay through prepare on every network. The fork does
    not strip them; after prepare, the operator runs complete once Gloas
    is live. If prepare never runs, classic MEV stays.

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
    via_charon = charon_installed(fs)
    plan.warnings.append(
        "Do not stop MEV-Boost yet. Prepare only stages VC relays. "
        "After Gloas on this network, run complete yourself to stop "
        "MEV-Boost and strip the beacon-node sidecar. The fork does not "
        "run complete for you."
    )
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
        True for Prysm when ``--builder-urls`` or
        ``default_config.builder.builders`` has a non-sidecar URL, for
        Lodestar when ``--builder.urls`` is set and is not the sidecar, for
        Teku when ``--builder-urls`` (or a leftover ``--Xbuilder-urls``) has
        a non-sidecar URL and the binary is v26.11.0+, for Nimbus when
        ``--payload-builder`` is enabled,
        ``--payload-builder-url`` has a non-sidecar URL, and the binary is
        v26.10.2+, for Erigon-Caplin when ``caplin-builders.json`` has
        a non-sidecar ``builders[].url`` and the binary is v3.7.1+, or for
        Lighthouse when ``builder_definitions.yml`` has an enabled
        non-sidecar ``builders[].url`` and the binary is v8.3.0+. Legacy
        ``builder.relays`` does not count. Always False for placeholder
        clients and for binaries older than each client's floor.
    """
    if vc_name == "Prysm":
        if prysm_has_builder_urls(vc_content):
            return True
        data = _load_prysm_settings(fs, vc_content)
        return bool(data) and prysm_has_builder_list(data)
    if vc_name == "Lodestar":
        args = normalize_cli_args(parse_unit(vc_content).exec_args)
        urls = get_flag_value(args, "--builder.urls")
        return bool(urls) and not is_sidecar_url(urls)
    if vc_name == "Teku":
        if not teku_supports_epbs(fs, vc_content):
            return False
        return teku_has_builder_urls(vc_content)
    if vc_name == "Nimbus":
        if not nimbus_supports_epbs(fs, vc_content):
            return False
        return nimbus_has_builder_url(vc_content)
    if vc_name == "Erigon-Caplin":
        if not caplin_supports_epbs(fs, vc_content):
            return False
        loaded = _load_caplin_builders(fs)
        return bool(loaded) and caplin_has_builder_list(loaded)
    if vc_name == "Lighthouse":
        if not lighthouse_supports_builder_definitions(fs, vc_content):
            return False
        path = lighthouse_builders_file(vc_content, fs.lighthouse_builders_path)
        raw = fs.read_text(path)
        if not raw:
            return False
        try:
            loaded = _load_yaml_mapping(raw, "builder_definitions.yml")
        except EpbsError:
            return False
        return lighthouse_has_builder_list(loaded)
    return False


def complete(
    fs: Optional[EpbsFilesystem] = None,
    apply: bool = False,
    force: bool = False,
    remote_vc_prepared: bool = False,
) -> MigrationPlan:
    """Disable MEV-Boost and remove BN sidecar builder flags.

    Does not change the VC relay list from prepare. When the Teku binary is
    v26.11.0 or newer and the unit still has removed ``--Xbuilder-*`` names,
    those flags are renamed in place (same values). Refused when the VC has no
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

    if (
        mode == "separate"
        and vc_name == "Teku"
        and vc_content
        and teku_supports_epbs(fs, vc_content)
    ):
        vc_path = fs.unit_path("validator")
        new_vc = promote_teku_xbuilder_unit(vc_content)
        if _write_unit_if_changed(fs, vc_path, vc_content, new_vc, apply):
            plan.actions.append(
                PlanAction(
                    vc_path,
                    "rename removed --Xbuilder-* flags to Teku 26.11.0 names",
                )
            )
            if "validator" not in plan.services_to_restart:
                plan.services_to_restart.append("validator")

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
    elif vc_name in (
        "Prysm",
        "Lodestar",
        "Teku",
        "Nimbus",
        "Erigon-Caplin",
        "Lighthouse",
    ):
        # Relay lists are not rewritten here. Teku --Xbuilder-* promotion, when
        # it changes the unit, already queued a validator restart.
        pass

    plan.applied = apply
    plan.rollback_hint = complete_rollback_hint(fs)
    return plan


def bn_sidecar_status(unit_path: str, content: str, bn_name: str) -> str:
    """Describe beacon-node sidecar flags for ``manage.epbs status``.

    ``present`` when a local MEV-Boost URL is still on the unit.
    ``already removed`` when a ``.bak.epbs.`` backup shows complete stripped
    flags that used to be there. ``not configured`` on a fresh install that
    never had the sidecar (native ePBS, or MEV was never enabled).

    Args:
        unit_path: Consensus or execution unit path.
        content: Current unit text.
        bn_name: Beacon-node client name for :func:`strip_bn_sidecar`.
    """
    if strip_bn_sidecar(content, bn_name) != content:
        return "present"
    directory = os.path.dirname(unit_path) or "."
    prefix = os.path.basename(unit_path) + ".bak.epbs."
    try:
        names = os.listdir(directory)
    except OSError:
        names = []
    if any(name.startswith(prefix) for name in names):
        return "already removed"
    return "not configured"


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
                    "VC relays: no (legacy builder.relays is ignored; "
                    "re-run prepare for --builder-urls on v"
                    f"{PRYSM_BUILDER_CLI_MIN_VERSION}+)"
                )
            else:
                lines.append("VC relays: " + ("yes" if has_relays else "no"))
        else:
            lines.append("VC relays: " + ("yes" if has_relays else "no"))
        if vc_name == "Teku" and not teku_supports_epbs(fs, vc_content):
            lines.append(
                "Teku binary: older than "
                f"{TEKU_EPBS_MIN_VERSION}; production --builder-* flags are not used. "
                "Prepare is a no-op."
            )
        if vc_name == "Nimbus" and not nimbus_supports_epbs(fs, vc_content):
            lines.append(
                "Nimbus binary: older than "
                f"{NIMBUS_EPBS_MIN_VERSION}; repeated --payload-builder-url "
                "is not used. Prepare is a no-op."
            )
        if vc_name == "Lighthouse" and not lighthouse_supports_builder_definitions(
            fs, vc_content
        ):
            lines.append(
                "Lighthouse binary: older than "
                f"v{LIGHTHOUSE_BUILDERS_MIN_VERSION}; builder_definitions.yml "
                "is not used. Prepare is a no-op."
            )
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
        bn_path, bn_content = _read_required_unit(fs, bn_key)
        lines.append("BN sidecar flags: " + bn_sidecar_status(bn_path, bn_content, bn_name))
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
        choices=(
            "status",
            "prepare",
            "complete",
            "export",
            "import",
            "list-relays",
            "promote-teku-flags",
        ),
        help=(
            "status | prepare (before fork) | complete (after fork) | "
            "export (MEV host, or the VC when MEV-Boost is absent) | "
            "import (VC/Charon host) | list-relays (one URL per line) | "
            "promote-teku-flags (rewrite removed --Xbuilder-* names)"
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
    parser.add_argument(
        "--lighthouse-builders",
        default=None,
        help="Override Lighthouse builder_definitions.yml path (tests).",
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
    if args.lighthouse_builders:
        fs.lighthouse_builders_path = args.lighthouse_builders

    try:
        if args.command == "status":
            text = status(fs)
            if args.json:
                print(json.dumps({"status": text}))
            else:
                print(text, end="")
            return 0
        if args.command == "list-relays":
            cfg = load_relays_for_tooling(fs)
            if not cfg.urls:
                raise EpbsError(
                    "No relay URLs found on mevboost.service or the validator client."
                )
            for url in cfg.urls:
                print(url)
            return 0
        if args.command == "promote-teku-flags":
            changed = promote_installed_teku_flags(fs, apply=args.apply)
            if args.json:
                print(json.dumps({"changed": changed, "applied": bool(args.apply)}))
            elif changed and args.apply:
                print("Promoted Teku ePBS flags on validator.service")
            elif changed:
                print("Teku ePBS flags would be promoted (dry-run; pass --apply)")
            else:
                print("Teku ePBS flags already current")
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
