"""Plan and run a CDVN → EthPillar full-stack migration.

CDVN is Obol's docker-based charon-distributed-validator-node
(https://github.com/ObolNetwork/charon-distributed-validator-node).

Detects EL/CL/VC/MEV profiles from CDVN ``.env``, maps them to EthPillar
clients, moves or merges Docker datadirs, copies ``.charon`` (following
symlinks), writes ``charon.service`` from ``CHARON_*`` env vars, syncs DKG key
shares into the VC, and optionally preserves CDVN Grafana port.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from deploy.charon import (
    VC_COPY_KEY_DIRS,
    VC_IMPORT_DATA_DIRS,
    charon_cluster_copy_only,
    copy_charon_cluster,
    count_charon_keystores,
    import_cdvn_env_to_service,
    list_dir_basenames,
    parse_dotenv,
    path_exists,
    resolve_cdvn_checkout,
    rewrite_endpoint_list,
    sync_charon_keyshares_to_vc,
)
from deploy.orchestrator import lodestar_bn_vc_incompatibility_message
from deploy.common import BASE_DATA_DIR, setup_client_user_and_dir
from manage.grafana import (
    apply_grafana_http_port,
    read_grafana_http_port,
    read_grafana_ini,
)

# Stock CDVN compose profile tokens → EthPillar client names for *local* migrate.
# Upstream CDVN compose-el.yml only defines el-nethermind and el-reth; other EL=
# values (custom/external) are treated as no local EL. EthPillar itself installs
# Besu, Geth, Erigon, Ethrex, etc. via deploy/orchestrator — expand this map when
# CDVN adds matching compose profiles.
EL_PROFILE_MAP: Dict[str, str] = {
    "el-nethermind": "Nethermind",
    "el-reth": "Reth",
}
CL_PROFILE_MAP: Dict[str, str] = {
    "cl-lighthouse": "Lighthouse",
    "cl-teku": "Teku",
    "cl-lodestar": "Lodestar",
    "cl-prysm": "Prysm",
    "cl-nimbus": "Nimbus",
    "cl-grandine": "Grandine",
}
VC_PROFILE_MAP: Dict[str, str] = {
    "vc-lodestar": "Lodestar",
    "vc-lighthouse": "Lighthouse",
    "vc-teku": "Teku",
    "vc-prysm": "Prysm",
    "vc-nimbus": "Nimbus",
}
MEV_LOCAL = frozenset({"mev-mevboost"})
MEV_NONE = frozenset({"mev-none", "none", ""})
# CDVN VC datadirs merged (not moved wholesale); keys come from .charon/validator_keys.
VC_DATADIR_RELS = frozenset({
    "data/vc-teku",
    "data/vc-nimbus",
    "data/vc-prysm",
    "data/vc-lighthouse",
    "data/lodestar",
})
VC_DATADIR_SKIP_NAMES = frozenset({"logs", "run.sh"})
VC_DATADIR_SKIP_SUFFIXES = (".log",)
VC_DATADIR_SKIP_FILES = frozenset({".log_rotate_audit.json"})

# Relative CDVN ./data path → (EthPillar datadir under BASE_DATA_DIR, systemd user)
DATADIR_MOVES: Dict[str, Tuple[str, str]] = {
    "data/nethermind": ("nethermind", "execution"),
    "data/reth": ("reth", "execution"),
    "data/lighthouse": ("lighthouse", "consensus"),
    "data/cl-teku": ("teku", "consensus"),
    "data/cl-lodestar": ("lodestar", "consensus"),
    "data/cl-prysm": ("prysm", "consensus"),
    "data/cl-nimbus": ("nimbus", "consensus"),
    "data/cl-grandine": ("grandine", "consensus"),
    "data/lodestar": ("lodestar_validator", "validator"),
    "data/vc-teku": ("teku_validator", "validator"),
    "data/vc-nimbus": ("nimbus_validator", "validator"),
    "data/vc-prysm": ("prysm_validator", "validator"),
    "data/vc-lighthouse": ("lighthouse_validator", "validator"),
}

# Soft-warn dirs when the EL/CL profile is *-none or external/unmapped but data still exists.
_ORPHAN_DATA_HINTS: Dict[str, str] = {
    "data/nethermind": "EL",
    "data/reth": "EL",
    "data/lighthouse": "CL",
    "data/cl-teku": "CL",
    "data/cl-lodestar": "CL",
    "data/cl-prysm": "CL",
    "data/cl-nimbus": "CL",
    "data/cl-grandine": "CL",
}


@dataclass
class DatadirMove:
    """One proposed CDVN → EthPillar datadir move."""

    relative_src: str
    src: str
    dest: str
    owner: str
    skip_reason: str = ""

    @property
    def will_move(self) -> bool:
        """True when this datadir move is eligible and not skipped."""
        return not self.skip_reason


@dataclass
class CdvnMigrationPlan:
    """Resolved migration plan from a CDVN checkout."""

    root: str
    env_path: Optional[str]
    network: str
    role: str
    ec_name: Optional[str]
    cc_name: Optional[str]
    vc_name: Optional[str]
    with_charon: bool
    with_mevboost: bool
    with_builder_api: bool
    bn_address: str
    charon_dir: Optional[str]
    has_lock: bool
    has_keyshares: bool
    compose_file: Optional[str]
    docker_running: bool
    docker_check_error: str = ""
    charon_link: Optional[str] = None
    charon_is_symlink: bool = False
    datadir_moves: List[DatadirMove] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    el_profile: str = ""
    cl_profile: str = ""
    vc_profile: str = ""
    mev_profile: str = ""
    grafana_port: Optional[int] = None
    fee_recipient: str = ""
    # EthPillar datadir root used when planning dest checks (injectable for tests).
    base_data_dir: str = BASE_DATA_DIR

    def summary(self) -> str:
        """Human-readable plan for confirmation / dry-run.

        Returns:
            Multi-line text describing role, clients, moves, and deploy argv.
        """
        arrow = "->"
        lines = [
            f"CDVN {arrow} EthPillar migration plan",
            f"  Checkout:     {self.root}",
            f"  .env:         {self.env_path or '(none)'}",
            f"  Network:      {self.network}",
            f"  Role:         {self.role}",
            f"  EL profile:   {self.el_profile or '(unset)'} {arrow} {self.ec_name or 'none'}",
            f"  CL profile:   {self.cl_profile or '(unset)'} {arrow} {self.cc_name or 'none'}",
            f"  VC profile:   {self.vc_profile or '(unset)'} {arrow} {self.vc_name or 'none'}",
            f"  MEV profile:  {self.mev_profile or '(unset)'} "
            f"(local_mevboost={self.with_mevboost}, builder_api={self.with_builder_api})",
            f"  Charon:       {self.with_charon} (lock={self.has_lock}, keys={self.has_keyshares})",
            (
                f"  Charon path:  {self.charon_link} {arrow} {self.charon_dir}"
                if self.charon_is_symlink and self.charon_link and self.charon_dir
                else f"  Charon path:  {self.charon_dir or '(none)'}"
            ),
            (
                f"  Key shares:   auto-sync to {self.vc_name} on migrate"
                if self.has_keyshares and self.vc_name
                else "  Key shares:   (none)"
            ),
            f"  BN address:   {self.bn_address or '(local via EthPillar CC)'}",
            f"  Fee recipient:{(' ' + self.fee_recipient) if self.fee_recipient else ' (unset)'}",
            f"  Grafana port: {self.grafana_port or '(EthPillar default 3000)'}",
            f"  Compose:      {self.compose_file or '(none)'}",
            f"  Docker up:    {self.docker_running}"
            + (f" ({self.docker_check_error})" if self.docker_check_error else ""),
            "",
            "Optional datadir moves (EL/CL/VC Docker data/):",
        ]
        if not self.datadir_moves:
            lines.append("  (none)")
        for move in self.datadir_moves:
            if move.will_move:
                lines.append(f"  MOVE  {move.src}")
                lines.append(f"    {arrow}   {move.dest}  (owner={move.owner})")
            else:
                lines.append(f"  SKIP  {move.src}  ({move.skip_reason})")
        if self.has_lock and self.charon_dir:
            dest_charon = os.path.join(self.base_data_dir, "charon", ".charon")
            lines.extend(
                [
                    "",
                    "Charon cluster overlay (always copied; CDVN checkout preserved):",
                    f"  COPY  {self.charon_dir}",
                    f"    {arrow}   {dest_charon}  (owner=charon)",
                ]
            )
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for warn in self.warnings:
                lines.append(f"  ! {warn}")
        lines.append("")
        lines.append("Deploy argv:")
        lines.append("  " + " ".join(self.deploy_argv()))
        return "\n".join(lines)

    def deploy_argv(self) -> List[str]:
        """Build ``deploy-node.py`` arguments for this plan.

        Returns:
            argv tokens (no executable), including ``--skip_prompts true``.
        """
        argv = [
            "--skip_prompts",
            "true",
            "--install_config",
            self.role,
            "--network",
            self.network.upper(),
            "--with_charon",
            "--vc",
            self.vc_name or "Lodestar",
        ]
        if self.role == "Custom Setup":
            if self.ec_name:
                argv.extend(["--ec", self.ec_name])
            if self.cc_name:
                argv.extend(["--cc", self.cc_name])
            if self.with_mevboost:
                argv.append("--with_mevboost")
        if self.with_builder_api and not self.with_mevboost:
            argv.append("--with_builder_api")
        if self.role == "Validator Client Only" and self.bn_address:
            argv.extend(["--vc_only_bn_address", self.bn_address])
        if self.fee_recipient:
            argv.extend(["--fee_address", self.fee_recipient])
        return argv


def _read_charon_file_text(path: str) -> str:
    """Read a file under ``.charon``, using ``sudo cat`` when not user-readable.

    Args:
        path: Absolute path to a cluster file (e.g. ``cluster-lock.json``).

    Returns:
        File text.

    Raises:
        OSError: When the file cannot be read even via sudo.
    """
    if os.path.isfile(path) and os.access(path, os.R_OK):
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    result = subprocess.run(
        ["sudo", "cat", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or f"cannot read {path}")
    return result.stdout


def _fee_recipient_from_ethpillar_env() -> str:
    """Resolve fee recipient from EthPillar ``env`` / ``.env.overrides``.

    Returns:
        ``0x`` address, or ``""`` when unset/invalid.
    """
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        from dotenv import dotenv_values
    except ImportError:
        dotenv_values = None  # type: ignore[assignment,misc]

    for name in ("env", ".env.overrides"):
        env_path = os.path.join(repo, name)
        if not os.path.isfile(env_path):
            continue
        values: Dict[str, str] = {}
        if dotenv_values is not None:
            loaded = dotenv_values(env_path)
            values = {str(k): str(v) for k, v in loaded.items() if k and v is not None}
        else:
            values = parse_dotenv(env_path)
        for key in ("FEE_RECIPIENT_ADDRESS", "FEE_RECIPIENT"):
            addr = _valid_eth_address(values.get(key, ""))
            if addr:
                return addr

    return _valid_eth_address(os.getenv("FEE_RECIPIENT_ADDRESS", ""))


def _resolve_fee_recipient(env: Dict[str, str], charon_dir: Optional[str]) -> str:
    """Resolve fee recipient from CDVN assets and EthPillar env overrides.

    Args:
        env: Parsed CDVN ``.env`` map.
        charon_dir: Path to ``.charon`` (cluster-lock / deposit-data), or None.

    Returns:
        ``0x`` address, or ``""`` when none could be resolved.
    """
    addr = _fee_recipient_from_cdvn(env, charon_dir)
    if addr:
        return addr
    return _fee_recipient_from_ethpillar_env()


def _require_fee_recipient(fee_recipient: str, vc_name: Optional[str]) -> None:
    """Raise when a VC deploy is planned but no fee recipient could be resolved.

    Args:
        fee_recipient: Address from :func:`_resolve_fee_recipient` (may be empty).
        vc_name: Planned signer VC, or None when no VC will be installed.

    Raises:
        ValueError: When *vc_name* is set and *fee_recipient* is empty.
    """
    if vc_name and not fee_recipient:
        raise ValueError(
            "Fee recipient address is required for validator deploy. Set "
            "FEE_RECIPIENT_ADDRESS in EthPillar .env.overrides or CDVN .env, or "
            "include fee_recipient in cluster-lock.json / deposit-data.json "
            "(root-owned .charon files are read via sudo during migrate)."
        )


def _valid_eth_address(val: object) -> str:
    """Return *val* when it looks like a 20-byte hex address, else ``""``.

    Args:
        val: Candidate value from JSON or env (any type).

    Returns:
        Normalized ``0x`` + 40 hex chars, or ``""``.
    """
    if not isinstance(val, str):
        return ""
    candidate = val.strip().strip('"')
    if not candidate.lower().startswith("0x") or len(candidate) != 42:
        return ""
    try:
        int(candidate[2:], 16)
    except ValueError:
        return ""
    return candidate


def _fee_recipient_from_cluster_lock(charon_dir: str) -> str:
    """Read fee recipient from Obol ``cluster-lock.json`` (primary CDVN source).

    Args:
        charon_dir: Path to the ``.charon`` directory.

    Returns:
        ``0x`` address from the lock file, or ``""`` when missing/unreadable.
    """
    lock_path = os.path.join(charon_dir, "cluster-lock.json")
    if not path_exists(lock_path):
        return ""
    try:
        lock = json.loads(_read_charon_file_text(lock_path))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""

    cluster_def = lock.get("cluster_definition") or {}
    if isinstance(cluster_def, dict):
        for validator in cluster_def.get("validators") or []:
            if not isinstance(validator, dict):
                continue
            for key in ("fee_recipient_address", "fee_recipient"):
                addr = _valid_eth_address(validator.get(key))
                if addr:
                    return addr
        for key in ("fee_recipient_address", "fee_recipient"):
            addr = _valid_eth_address(cluster_def.get(key))
            if addr:
                return addr
        fee_addrs = cluster_def.get("fee_recipient_addresses")
        if isinstance(fee_addrs, list):
            for item in fee_addrs:
                addr = _valid_eth_address(item)
                if addr:
                    return addr

    for dv in lock.get("distributed_validators") or []:
        if not isinstance(dv, dict):
            continue
        registration = dv.get("builder_registration") or {}
        message = registration.get("message") or {}
        addr = _valid_eth_address(message.get("fee_recipient"))
        if addr:
            return addr
    return ""


def _fee_recipient_from_cdvn(env: Dict[str, str], charon_dir: Optional[str]) -> str:
    """Resolve fee recipient from CDVN ``.env``, ``cluster-lock.json``, or ``deposit-data.json``.

    Args:
        env: Parsed CDVN ``.env`` map.
        charon_dir: Path to ``.charon``, or None.

    Returns:
        First valid ``0x`` address found, or ``""``.
    """
    for key in ("FEE_RECIPIENT_ADDRESS", "FEE_RECIPIENT", "CHARON_FEE_RECIPIENT"):
        addr = _valid_eth_address(env.get(key))
        if addr:
            return addr
    if not charon_dir:
        return ""
    addr = _fee_recipient_from_cluster_lock(charon_dir)
    if addr:
        return addr
    dep_path = os.path.join(charon_dir, "deposit-data.json")
    if not path_exists(dep_path):
        return ""
    try:
        data = json.loads(_read_charon_file_text(dep_path))
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            addr = _valid_eth_address(item.get("fee_recipient"))
            if addr:
                return addr
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    return ""


def _norm_profile(value: str) -> str:
    """Normalize a CDVN compose profile token for comparison.

    Args:
        value: Raw ``EL=`` / ``CL=`` / ``VC=`` / ``MEV=`` string.

    Returns:
        Lowercased, stripped token (empty string when *value* is None/blank).
    """
    return (value or "").strip().lower()


def _is_none_profile(value: str, kind: str) -> bool:
    """Return True when a profile means “no local client” (``el-none``, empty, …).

    Args:
        value: Raw profile token from CDVN ``.env``.
        kind: Prefix such as ``el``, ``cl``, or ``mev``.

    Returns:
        True for empty, ``none``, or ``{kind}-none``.
    """
    v = _norm_profile(value)
    return not v or v in {f"{kind}-none", "none"}


def _truthy(value: str) -> bool:
    """Return True for common truthy env strings.

    Args:
        value: Raw env value (e.g. ``BUILDER_API_ENABLED``).

    Returns:
        True for ``1``, ``true``, ``yes``, or ``on`` (case-insensitive).
    """
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_ec_name(el_raw: str) -> Tuple[Optional[str], List[str]]:
    """Map CDVN ``EL`` profile to a local EthPillar EC name, or none (external).

    Stock CDVN compose may list profiles EthPillar does not install locally;
    unmapped values are treated as external EL with a plan warning.

    Args:
        el_raw: Raw ``EL=`` value from CDVN ``.env``.

    Returns:
        ``(ec_name, warnings)`` — ``ec_name`` is None for external/unmapped profiles.
    """
    warnings: List[str] = []
    if _is_none_profile(el_raw, "el"):
        return None, warnings
    mapped = EL_PROFILE_MAP.get(el_raw)
    if mapped:
        return mapped, warnings
    warnings.append(
        f"EL profile {el_raw!r} is not a supported local client "
        f"({', '.join(sorted(EL_PROFILE_MAP))}); treating as external (no local EL)."
    )
    return None, warnings


def _resolve_cc_name(cl_raw: str) -> Tuple[Optional[str], List[str]]:
    """Map CDVN ``CL`` profile to a local EthPillar CC name, or none (external).

    Args:
        cl_raw: Raw ``CL=`` value from CDVN ``.env``.

    Returns:
        ``(cc_name, warnings)`` — ``cc_name`` is None for external/unmapped profiles.
    """
    warnings: List[str] = []
    if _is_none_profile(cl_raw, "cl"):
        return None, warnings
    mapped = CL_PROFILE_MAP.get(cl_raw)
    if mapped:
        return mapped, warnings
    warnings.append(
        f"CL profile {cl_raw!r} is not a supported local client "
        f"({', '.join(sorted(CL_PROFILE_MAP))}); treating as external (no local CL)."
    )
    return None, warnings


def _resolve_local_mevboost(
    mev_raw: str, *, has_local_el: bool, has_local_cl: bool
) -> Tuple[bool, List[str]]:
    """Return whether to install local ``mevboost.service`` from the MEV profile.

    Args:
        mev_raw: Raw ``MEV=`` value from CDVN ``.env``.
        has_local_el: Whether migrate plans a local execution client.
        has_local_cl: Whether migrate plans a local consensus client.

    Returns:
        ``(with_mevboost, warnings)``.
    """
    warnings: List[str] = []
    if mev_raw in MEV_LOCAL:
        return has_local_el and has_local_cl, warnings
    if mev_raw in MEV_NONE or _is_none_profile(mev_raw, "mev"):
        return False, warnings
    warnings.append(
        f"MEV profile {mev_raw!r} is not local mev-mevboost; "
        "treating as external/no local MEV-Boost."
    )
    return False, warnings


def grafana_port_from_env(env: Dict[str, str]) -> Optional[int]:
    """Parse CDVN ``MONITORING_PORT_GRAFANA`` when set to a valid TCP port.

    Args:
        env: Parsed CDVN ``.env`` key/value map.

    Returns:
        Port number 1–65535, or None when unset/invalid.
    """
    raw = (env.get("MONITORING_PORT_GRAFANA") or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    if port < 1 or port > 65535:
        return None
    return port


def apply_cdvn_monitoring_from_env(env_path: str) -> Optional[int]:
    """Apply CDVN monitoring settings (Grafana port) after EthPillar monitoring install.

    Args:
        env_path: Path to CDVN ``.env``.

    Returns:
        Effective Grafana ``http_port`` from ``grafana.ini`` when Grafana is
        installed, else None. When ``MONITORING_PORT_GRAFANA`` is set, attempts
        to apply it before reading the configured port back.
    """
    env = parse_dotenv(env_path)
    desired = grafana_port_from_env(env)
    if desired is not None:
        apply_grafana_http_port(desired)
    if read_grafana_ini() is None:
        return None
    return read_grafana_http_port()


def _vc_datadir_useful_entries(src: str) -> List[str]:
    """Return top-level CDVN VC datadir entries worth merging into EthPillar.

    Skips logs, ``run.sh``, ``*.log`` files, and log-rotate audit metadata.

    Args:
        src: CDVN ``data/vc-*`` or ``data/lodestar`` path.

    Returns:
        Basenames to copy (e.g. ``keystores``, ``validator-db``, ``validators``).
    """
    if not os.path.isdir(src):
        return []
    useful: List[str] = []
    for name in list_dir_basenames(src):
        if name in VC_DATADIR_SKIP_NAMES or name in VC_DATADIR_SKIP_FILES:
            continue
        if any(name.endswith(suffix) for suffix in VC_DATADIR_SKIP_SUFFIXES):
            continue
        if os.path.exists(os.path.join(src, name)):
            useful.append(name)
    return useful


def _vc_datadir_skip_reason(src: str) -> str:
    """Return a skip reason when a CDVN VC datadir has nothing useful to merge.

    Args:
        src: CDVN VC datadir path.

    Returns:
        Empty string when merge should proceed; otherwise a human-readable reason.
    """
    if not os.path.isdir(src):
        return "source empty or missing"
    useful = _vc_datadir_useful_entries(src)
    if not useful:
        return "only logs present (keys synced from .charon/validator_keys on migrate)"
    return ""


def merge_cdvn_vc_datadir(src: str, dest: str, owner: str) -> List[str]:
    """Merge useful CDVN VC datadir entries into EthPillar's VC data path.

    Used for ``data/lodestar``, ``data/vc-nimbus``, etc. Keys still sync from
    ``.charon/validator_keys`` when the VC datadir is logs-only (Teku).

    Args:
        src: CDVN VC datadir under the checkout.
        dest: EthPillar path under ``/var/lib`` (e.g. ``lodestar_validator``).
        owner: Systemd service user for ``chown`` (usually ``validator``).

    Returns:
        List of merged entry basenames (empty when *src* is missing or useless).
    """
    merged: List[str] = []
    if not os.path.isdir(src):
        return merged
    subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
    for name in _vc_datadir_useful_entries(src):
        s_item = os.path.join(src, name)
        d_item = os.path.join(dest, name)
        if os.path.isdir(s_item):
            subprocess.run(["sudo", "rm", "-rf", d_item], check=False)
            subprocess.run(["sudo", "cp", "-a", s_item, d_item], check=True)
        else:
            subprocess.run(["sudo", "cp", "-a", s_item, d_item], check=True)
        merged.append(name)
    if merged:
        client_name = os.path.basename(dest.rstrip(os.sep)) or owner
        setup_client_user_and_dir(owner, client_name)
        subprocess.run(["sudo", "chown", "-R", f"{owner}:{owner}", dest], check=True)
    return merged


def _dir_nonempty(path: str) -> bool:
    """Return True when *path* is a directory with at least one entry.

    Uses sudo listing when the path is not readable as the current user (Docker
    volumes are often root- or container-uid-owned).

    Args:
        path: Directory to inspect.

    Returns:
        True when the directory exists and has at least one entry.
    """
    if path_exists(path, directory=True):
        return bool(list_dir_basenames(path))
    if not os.path.isdir(path):
        return False
    try:
        return any(os.scandir(path))
    except OSError:
        return False


def _dest_has_data(path: str) -> bool:
    """True when destination already looks occupied (skip move).

    Args:
        path: EthPillar ``/var/lib/…`` destination directory.

    Returns:
        True when *path* exists and is non-empty (or cannot be listed).
    """
    if path_exists(path, directory=True):
        return bool(list_dir_basenames(path))
    if not os.path.isdir(path):
        return False
    try:
        entries = list(os.scandir(path))
    except OSError:
        return True
    return len(entries) > 0


def detect_docker_compose_status(
    compose_file: Optional[str], root: str
) -> Tuple[bool, str]:
    """Check whether CDVN Docker Compose still has running services.

    Args:
        compose_file: Path to ``docker-compose.yml`` (or None).
        root: CDVN checkout directory used as compose working directory.

    Returns:
        ``(running, error)``. ``error`` is set when the check could not be
        performed (missing CLI, permission denied, timeout). Callers must
        treat a non-empty *error* as unsafe to migrate (same as running).
    """
    if not compose_file:
        return False, ""

    commands: List[List[str]] = []
    if shutil.which("docker"):
        commands.append(
            ["docker", "compose", "-f", compose_file, "ps", "--status", "running", "-q"]
        )
    if shutil.which("docker-compose"):
        commands.append(
            ["docker-compose", "-f", compose_file, "ps", "-q"]
        )
    if not commands:
        # Docker removed or never installed — CDVN Compose cannot be running locally.
        return False, ""

    last_err = ""
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = f"{' '.join(cmd)} failed: {exc}"
            continue
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
            last_err = f"{' '.join(cmd)}: {err}"
            continue
        return bool(result.stdout.strip()), ""
    return False, last_err or "docker compose status check failed"


def detect_docker_compose_running(compose_file: Optional[str], root: str) -> bool:
    """Return True if compose reports running services.

    Args:
        compose_file: Path to ``docker-compose.yml``, or None.
        root: CDVN checkout used as the compose working directory.

    Returns:
        True only when the check succeeded and services are up. False when
        compose is absent or the check could not run (see
        :func:`detect_docker_compose_status` for the error string).
    """
    running, _err = detect_docker_compose_status(compose_file, root)
    return running


def plan_cdvn_migration(
    path: str,
    *,
    local_host: str = "127.0.0.1",
    base_data_dir: Optional[str] = None,
) -> CdvnMigrationPlan:
    """Build a migration plan from a CDVN checkout path or ``.env`` file.

    Args:
        path: CDVN checkout directory or ``.env`` file path.
        local_host: Host used when rewriting Docker Compose service URLs to loopback.
        base_data_dir: EthPillar datadir root for destination occupancy checks
            (default: :data:`BASE_DATA_DIR` / ``/var/lib``). Inject a temp root
            in tests so planner dest checks do not depend on the host.

    Returns:
        :class:`CdvnMigrationPlan` with datadir moves, warnings, and deploy argv.

    Raises:
        ValueError: When ``.env``/profiles are missing or incompatible, or a VC
            is planned but no fee recipient could be resolved.
        FileNotFoundError: When *path* does not exist.
    """
    data_root = base_data_dir if base_data_dir is not None else BASE_DATA_DIR
    info = resolve_cdvn_checkout(path)
    root = str(info["root"])
    env_path = info.get("env_path")
    if not env_path or not os.path.isfile(str(env_path)):
        raise ValueError(
            f"No .env found under {root}. Copy .env.sample.* to .env or pass a path that contains one."
        )

    env = parse_dotenv(str(env_path))
    network = (env.get("NETWORK") or "").strip().lower()
    if not network:
        raise ValueError("NETWORK is unset in CDVN .env")

    el_raw = _norm_profile(env.get("EL", ""))
    cl_raw = _norm_profile(env.get("CL", ""))
    vc_raw = _norm_profile(env.get("VC", ""))
    mev_raw = _norm_profile(env.get("MEV", ""))

    warnings: List[str] = []
    ec_name, el_warns = _resolve_ec_name(el_raw)
    cc_name, cl_warns = _resolve_cc_name(cl_raw)
    warnings.extend(el_warns)
    warnings.extend(cl_warns)

    el_none = ec_name is None
    cl_none = cc_name is None

    if _is_none_profile(vc_raw, "vc"):
        raise ValueError("VC profile is unset or vc-none; Charon migrate requires a signer VC.")
    if vc_raw not in VC_PROFILE_MAP:
        raise ValueError(
            f"Unsupported CDVN VC profile {vc_raw!r}. "
            f"Supported: {', '.join(sorted(VC_PROFILE_MAP))}."
        )
    vc_name = VC_PROFILE_MAP[vc_raw]

    if not el_none and cl_none:
        raise ValueError(
            "Local EL with CL=cl-none is unsupported for migrate. "
            "Use el-none+cl-none (VC-only) or enable both EL and CL."
        )
    if el_none and not cl_none:
        raise ValueError(
            "Local CL with EL=el-none is unsupported for migrate v1. "
            "Use el-none+cl-none (external BN) or enable both EL and CL."
        )

    bn_address = ""
    bn_vc_warn = lodestar_bn_vc_incompatibility_message(cc_name, vc_name)
    if bn_vc_warn:
        warnings.append(bn_vc_warn)
    if el_none and cl_none:
        role = "Validator Client Only"
        raw_bn = (env.get("CHARON_BEACON_NODE_ENDPOINTS") or "").strip()
        if not raw_bn:
            raise ValueError(
                "CL=cl-none requires CHARON_BEACON_NODE_ENDPOINTS for the remote beacon node."
            )
        bn_address, bn_warns = rewrite_endpoint_list(raw_bn, local_host=local_host)
        warnings.extend(bn_warns)
    else:
        role = "Custom Setup"

    charon_dir = info.get("charon_dir")
    charon_link = info.get("charon_link")
    charon_is_symlink = bool(info.get("charon_is_symlink"))
    has_lock = bool(info.get("has_lock"))
    has_keyshares = bool(info.get("has_keyshares"))
    if not charon_dir and not has_lock:
        # Still allow if CHARON_* present / .charon dir without lock yet
        if not any(k.startswith("CHARON_") for k in env):
            raise ValueError(
                "No .charon cluster found and no CHARON_* settings in .env. "
                "This migrate path is for Charon DV nodes."
            )

    with_charon = True
    with_mevboost, mev_warns = _resolve_local_mevboost(
        mev_raw, has_local_el=not el_none, has_local_cl=not cl_none
    )
    warnings.extend(mev_warns)
    builder_raw = env.get("CHARON_BUILDER_API") or env.get("BUILDER_API_ENABLED") or ""
    with_builder_api = with_mevboost or (bool(builder_raw) and _truthy(builder_raw))
    grafana_port = grafana_port_from_env(env)

    compose_file = info.get("compose_file")
    docker_running, docker_check_error = detect_docker_compose_status(
        str(compose_file) if compose_file else None, root
    )
    if compose_file and not docker_check_error and not shutil.which("docker") and not shutil.which(
        "docker-compose"
    ):
        warnings.append(
            "Docker CLI not found; assuming CDVN Compose is stopped "
            "(safe after docker compose down or Docker removal)."
        )

    # Orphan data warnings
    for rel, kind in _ORPHAN_DATA_HINTS.items():
        full = os.path.join(root, rel.replace("/", os.sep))
        if not _dir_nonempty(full):
            continue
        if kind == "EL" and el_none:
            label = el_raw or "unset"
            hint = "el-none" if _is_none_profile(el_raw, "el") else "external/unmapped EL"
            warnings.append(f"Found {rel} but EL={label} ({hint}); not migrating that dir.")
        if kind == "CL" and cl_none:
            label = cl_raw or "unset"
            hint = "cl-none" if _is_none_profile(cl_raw, "cl") else "external/unmapped CL"
            warnings.append(f"Found {rel} but CL={label} ({hint}); not migrating that dir.")

    datadir_moves: List[DatadirMove] = []
    active_rels: List[str] = []
    if ec_name == "Nethermind":
        active_rels.append("data/nethermind")
    elif ec_name == "Reth":
        active_rels.append("data/reth")
    if cc_name == "Lighthouse":
        active_rels.append("data/lighthouse")
    elif cc_name == "Teku":
        active_rels.append("data/cl-teku")
    elif cc_name == "Lodestar":
        active_rels.append("data/cl-lodestar")
    elif cc_name == "Prysm":
        active_rels.append("data/cl-prysm")
    elif cc_name == "Nimbus":
        active_rels.append("data/cl-nimbus")
    elif cc_name == "Grandine":
        active_rels.append("data/cl-grandine")
    if vc_name == "Lodestar":
        active_rels.append("data/lodestar")
    elif vc_name == "Lighthouse":
        active_rels.append("data/vc-lighthouse")
    elif vc_name == "Teku":
        active_rels.append("data/vc-teku")
    elif vc_name == "Nimbus":
        active_rels.append("data/vc-nimbus")
    elif vc_name == "Prysm":
        active_rels.append("data/vc-prysm")

    for rel in active_rels:
        dest_name, owner = DATADIR_MOVES[rel]
        src = os.path.join(root, rel.replace("/", os.sep))
        dest = os.path.join(data_root, dest_name)
        skip = ""
        if rel in VC_DATADIR_RELS:
            skip = _vc_datadir_skip_reason(src)
        elif not _dir_nonempty(src):
            skip = "source empty or missing"
        elif _dest_has_data(dest):
            skip = f"destination already has data ({dest}); clear it manually to move"
        datadir_moves.append(
            DatadirMove(
                relative_src=rel,
                src=src,
                dest=dest,
                owner=owner,
                skip_reason=skip,
            )
        )

    fee_recipient = _resolve_fee_recipient(env, str(charon_dir) if charon_dir else None)
    _require_fee_recipient(fee_recipient, vc_name)

    return CdvnMigrationPlan(
        root=root,
        env_path=str(env_path),
        network=network,
        role=role,
        ec_name=ec_name,
        cc_name=cc_name,
        vc_name=vc_name,
        with_charon=with_charon,
        with_mevboost=with_mevboost,
        with_builder_api=with_builder_api,
        bn_address=bn_address,
        charon_dir=str(charon_dir) if charon_dir else None,
        charon_link=str(charon_link) if charon_link else None,
        charon_is_symlink=charon_is_symlink,
        has_lock=has_lock,
        has_keyshares=has_keyshares,
        compose_file=str(compose_file) if compose_file else None,
        docker_running=docker_running,
        docker_check_error=docker_check_error,
        datadir_moves=datadir_moves,
        warnings=warnings,
        el_profile=el_raw,
        cl_profile=cl_raw,
        vc_profile=vc_raw,
        mev_profile=mev_raw,
        grafana_port=grafana_port,
        fee_recipient=fee_recipient,
        base_data_dir=data_root,
    )


def move_client_datadir(src: str, dest: str, owner: str) -> None:
    """Move ``src`` contents into ``dest`` and chown to ``owner``.

    Args:
        src: CDVN Docker datadir (must exist).
        dest: EthPillar ``/var/lib/…`` destination.
        owner: Systemd service user for ``chown``.

    Raises:
        FileNotFoundError: When *src* is not a directory.
    """
    if not os.path.isdir(src):
        raise FileNotFoundError(src)
    subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
    # Move children into dest (dest may already exist empty from setup)
    for name in list_dir_basenames(src):
        s_item = os.path.join(src, name)
        d_item = os.path.join(dest, name)
        subprocess.run(["sudo", "mv", s_item, d_item], check=True)
    subprocess.run(["sudo", "chown", "-R", f"{owner}:{owner}", dest], check=True)
    # Remove empty source dir if possible
    try:
        os.rmdir(src)
    except OSError:
        subprocess.run(["sudo", "rmdir", src], check=False)


def apply_datadir_moves(plan: CdvnMigrationPlan, selected: Optional[Sequence[str]] = None) -> List[str]:
    """Apply planned CDVN datadir moves and VC merges.

    ``selected``:
      * ``None`` — move all eligible dirs
      * empty sequence — move none
      * otherwise — only listed ``relative_src`` values

    ``.charon`` is handled separately by :func:`run_migration`.

    Args:
        plan: Plan from :func:`plan_cdvn_migration`.
        selected: Optional subset of ``DatadirMove.relative_src`` values.

    Returns:
        ``relative_src`` values that were moved or merged.
    """
    done: List[str] = []
    allow: Optional[set] = None if selected is None else set(selected)
    for move in plan.datadir_moves:
        if move.relative_src == ".charon":
            continue
        if allow is not None and move.relative_src not in allow:
            continue
        if not move.will_move:
            continue
        if move.relative_src in VC_DATADIR_RELS:
            merged = merge_cdvn_vc_datadir(move.src, move.dest, move.owner)
            if merged:
                done.append(move.relative_src)
            continue
        move_client_datadir(move.src, move.dest, move.owner)
        done.append(move.relative_src)
    return done


def detect_ethpillar_vc_name(service_path: str = "/etc/systemd/system/validator.service") -> Optional[str]:
    """Return the EthPillar VC name found in ``validator.service``.

    Returns the first known client name (checked in the fixed order Lodestar,
    Lighthouse, Teku, Nimbus, Prysm, Grandine) that appears anywhere in the
    unit text, case-insensitively.

    Args:
        service_path: Path to ``validator.service`` (overridable in tests).

    Returns:
        Canonical VC name (e.g. ``Lodestar``), or None when the unit is missing
        or the client cannot be identified.
    """
    try:
        text = Path(service_path).read_text(encoding="utf-8")
    except OSError:
        result = subprocess.run(
            ["sudo", "cat", service_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        text = result.stdout
    for name in ("Lodestar", "Lighthouse", "Teku", "Nimbus", "Prysm", "Grandine"):
        if re.search(rf"{name.lower()}", text, re.I):
            return name
    return None


def _repo_root() -> str:
    """Return the EthPillar checkout root (parent of ``deploy/``).

    Returns:
        Absolute path to the repository root.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _charon_cluster_dest() -> str:
    """Return EthPillar's Charon ``.charon`` datadir.

    Returns:
        ``{BASE_DATA_DIR}/charon/.charon``.
    """
    return os.path.join(BASE_DATA_DIR, "charon", ".charon")


def _vc_key_material_paths(vc_name: str) -> List[str]:
    """Return EthPillar VC paths that hold imported key shares for *vc_name*.

    Args:
        vc_name: Signer client (``Lighthouse``, ``Lodestar``, …).

    Returns:
        Paths under ``/var/lib`` that a fresh migrate should clear.
    """
    paths: List[str] = []
    if vc_name in VC_COPY_KEY_DIRS:
        paths.append(VC_COPY_KEY_DIRS[vc_name])
    if vc_name in VC_IMPORT_DATA_DIRS:
        base = VC_IMPORT_DATA_DIRS[vc_name]
        if vc_name == "Lighthouse":
            paths.extend(
                [
                    os.path.join(base, "validators"),
                    os.path.join(base, "accounts"),
                ]
            )
        elif vc_name == "Lodestar":
            paths.extend(
                [
                    os.path.join(base, "keystores"),
                    os.path.join(base, "secrets"),
                    os.path.join(base, "validator-db"),
                ]
            )
        elif vc_name == "Nimbus":
            paths.extend(
                [
                    os.path.join(base, "validators"),
                    os.path.join(base, "secrets"),
                ]
            )
        elif vc_name == "Prysm":
            paths.append(os.path.join(base, "validator_keys"))
    return paths


def reset_cdvn_migration_state(plan: CdvnMigrationPlan) -> None:
    """Stop Charon/VC and clear migrated cluster + VC key material for a clean re-run.

    Args:
        plan: Plan whose ``vc_name`` selects which VC key paths to remove.
    """
    for unit in ("validator", "charon"):
        subprocess.run(["sudo", "systemctl", "stop", unit], check=False)

    cluster = _charon_cluster_dest()
    if path_exists(cluster, directory=True) or os.path.lexists(cluster):
        print(f"Reset: removing Charon cluster at {cluster}")
        subprocess.run(["sudo", "rm", "-rf", cluster], check=True)

    if not plan.vc_name:
        return
    for path in _vc_key_material_paths(plan.vc_name):
        if path_exists(path, directory=True) or path_exists(path):
            print(f"Reset: removing VC key material at {path}")
            subprocess.run(["sudo", "rm", "-rf", path], check=False)


def run_deploy(plan: CdvnMigrationPlan, *, dry_run: bool = False) -> int:
    """Invoke ``deploy/install-node.sh`` with the plan's argv.

    Args:
        plan: Resolved migration plan.
        dry_run: When True, print the command and return 0 without running it.

    Returns:
        Process exit code (0 on dry-run).
    """
    root = _repo_root()
    script = os.path.join(root, "deploy", "install-node.sh")
    cmd = ["bash", script, *plan.deploy_argv()]
    print("Running:", " ".join(cmd))
    if dry_run:
        return 0
    env = os.environ.copy()
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.call(cmd, cwd=root, env=env)


def ensure_ethpillar_installed() -> None:
    """Create ``/usr/local/bin/ethpillar`` symlink if missing (from this checkout)."""
    link = "/usr/local/bin/ethpillar"
    repo = _repo_root()
    target = os.path.join(repo, "ethpillar.sh")
    if os.path.islink(link) or os.path.isfile(link):
        return
    if not os.path.isfile(target):
        raise FileNotFoundError(f"ethpillar.sh not found at {target}")
    print(f"Installing ethpillar symlink → {target}")
    subprocess.run(["sudo", "ln", "-s", target, link], check=True)
    subprocess.run(["bash", os.path.join(repo, "install.sh")], check=False)


def run_migration(
    path: str,
    *,
    dry_run: bool = False,
    apply_moves: Optional[Sequence[str]] = None,
    skip_deploy: bool = False,
    skip_charon_overlay: bool = False,
    fresh: bool = False,
) -> CdvnMigrationPlan:
    """Execute CDVN → EthPillar migration (or dry-run plan only).

    Args:
        path: CDVN checkout or ``.env`` path (same as :func:`plan_cdvn_migration`).
        dry_run: When True, print the plan and return without writing the system.
        apply_moves: Subset of datadir ``relative_src`` to move/merge; ``None`` = all.
        skip_deploy: Skip ``deploy/install-node.sh`` (datadir/charon overlay only).
        skip_charon_overlay: Skip ``.charon`` copy, ``charon.service`` import, and key sync.
        fresh: Stop Charon/VC and clear migrated cluster + VC keys before running.

    Returns:
        The migration plan (same object whether or not ``dry_run``).

    Raises:
        RuntimeError: When Docker Compose is still up or its status cannot be
            verified, deploy fails, key share sync fails or is skipped (other
            than destination already populated), or the Charon overlay fails.
        ValueError: When the plan cannot be built (see :func:`plan_cdvn_migration`)
            or the ``.charon`` source lacks ``cluster-lock.json``.
        FileNotFoundError: When *path*, a datadir move source, the ``.charon``
            source, or the CDVN ``.env`` is missing.
        subprocess.CalledProcessError: When a ``sudo`` reset/datadir/overlay step fails.
    """
    plan = plan_cdvn_migration(path)
    if plan.docker_running:
        raise RuntimeError(
            f"Docker Compose still has running services for {plan.root}. "
            "Stop CDVN (`docker compose down`) before migrating."
        )
    if plan.docker_check_error:
        raise RuntimeError(
            f"Could not verify CDVN Docker is stopped ({plan.docker_check_error}). "
            "Install docker/docker-compose, fix permissions, then re-run, "
            "or stop CDVN manually (`docker compose down` / `docker-compose down`)."
        )
    print(plan.summary())
    if dry_run:
        return plan

    if fresh:
        reset_cdvn_migration_state(plan)

    if not skip_deploy:
        rc = run_deploy(plan, dry_run=False)
        if rc != 0:
            raise RuntimeError(f"deploy/install-node.sh failed with exit code {rc}")

    apply_datadir_moves(plan, selected=apply_moves)

    _apply_charon_cluster_overlay(plan, skip=skip_charon_overlay, force=fresh)
    if plan.env_path and not skip_charon_overlay:
        import_cdvn_env_to_service(
            plan.env_path,
            apply=True,
            preserve_beacon_endpoints=bool(plan.cc_name),
        )

    if plan.vc_name and plan.has_keyshares and not skip_charon_overlay:
        sync = sync_charon_keyshares_to_vc(plan.vc_name, force=fresh)
        if sync.get("status") == "copied":
            print(
                f"Synced {sync.get('count', 0)} key share(s) from .charon/validator_keys "
                f"→ {sync.get('dest')}"
            )
        elif sync.get("status") == "skipped":
            reason = str(sync.get("reason", ""))
            if reason.startswith("destination already has"):
                print(f"Key share sync skipped: {reason}")
            else:
                raise RuntimeError(f"Key share sync skipped: {reason}")
        elif sync.get("status") == "failed":
            raise RuntimeError(f"Key share sync failed: {sync.get('reason')}")
        elif sync.get("status") == "unsupported":
            print(
                f"Key shares present; auto-sync not implemented for {plan.vc_name}. "
                "Use Validator → Import Obol Charon key shares."
            )

    enable_migrated_units(plan)
    return plan


def enable_migrated_units(plan: CdvnMigrationPlan) -> None:
    """Enable systemd units so the migrated stack survives reboot.

    Args:
        plan: Plan used to decide which of execution/consensus/mevboost/charon/validator
            units exist and should be enabled.
    """
    units: List[str] = []
    if plan.ec_name:
        units.append("execution")
    if plan.cc_name:
        units.append("consensus")
    if plan.with_mevboost:
        units.append("mevboost")
    units.append("charon")
    if plan.vc_name:
        units.append("validator")
    for unit in units:
        svc = f"/etc/systemd/system/{unit}.service"
        if path_exists(svc):
            subprocess.run(
                ["sudo", "systemctl", "enable", unit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _apply_charon_cluster_overlay(
    plan: CdvnMigrationPlan,
    *,
    skip: bool = False,
    force: bool = False,
) -> None:
    """Copy CDVN ``.charon`` into EthPillar's Charon datadir.

    Runs unless *skip* is set; an existing EthPillar copy with key shares is
    kept unless *force*. Optional Docker ``data/`` moves (``--moves``) do not
    control this step.

    Args:
        plan: Plan with ``charon_dir`` / lock / keyshare flags.
        skip: When True, print a skip message and return.
        force: When True, overwrite an existing EthPillar cluster copy.

    Raises:
        RuntimeError: When a copy is needed but the plan has no ``.charon``
            cluster lock, the copy is skipped, or ``cluster-lock.json`` is
            missing afterward.
        FileNotFoundError: From :func:`copy_charon_cluster` when the source
            ``.charon`` directory is missing.
        ValueError: From :func:`copy_charon_cluster` when the source has no
            ``cluster-lock.json``.
        subprocess.CalledProcessError: When a ``sudo`` mkdir/copy step fails.
    """
    dest = _charon_cluster_dest()
    dest_lock = os.path.join(dest, "cluster-lock.json")

    if skip:
        print("Charon cluster overlay: skipped (skip_charon_overlay)")
        return

    if path_exists(dest_lock) and not force:
        keys_dir = os.path.join(dest, "validator_keys")
        key_count = count_charon_keystores(keys_dir)
        if key_count > 0 or not plan.has_keyshares:
            print(f"Charon cluster overlay: already present at {dest_lock}")
            print(f"Charon cluster overlay OK: {dest_lock} ({key_count} key share file(s))")
            return
        print(
            f"Charon cluster overlay: {dest_lock} present but no key shares; "
            "re-copying from CDVN checkout"
        )
        force = True

    if not plan.charon_dir or not plan.has_lock:
        raise RuntimeError(
            f"Charon cluster overlay required but {dest_lock} is missing and "
            "no .charon cluster was found in the CDVN checkout. "
            "Restore .charon from your tarball, or copy it back into the checkout."
        )

    copy_only = charon_cluster_copy_only(plan.root, plan.charon_dir)
    # Always copy during migrate — never move — so CDVN checkout stays intact if a
    # later step fails or the operator re-runs migrate.
    subprocess.run(["sudo", "mkdir", "-p", os.path.dirname(dest)], check=True)
    if os.path.exists(dest) and not _dir_nonempty(dest):
        subprocess.run(["sudo", "rmdir", dest], check=False)
    result = copy_charon_cluster(plan.charon_dir, force=force)
    if copy_only:
        print(f"Copied {plan.charon_dir} → {dest} (.charon symlink/outside checkout)")
    else:
        print(f"Copied {plan.charon_dir} → {dest} (CDVN checkout preserved)")

    if result.get("status") == "skipped":
        raise RuntimeError(
            f"Charon cluster overlay failed: {result.get('reason', 'skipped')}"
        )
    if not path_exists(dest_lock):
        raise RuntimeError(
            f"Charon cluster overlay did not produce {dest_lock}. "
            "Check migration log for copy/move errors."
        )
    keys_dir = os.path.join(dest, "validator_keys")
    key_count = count_charon_keystores(keys_dir)
    print(f"Charon cluster overlay OK: {dest_lock} ({key_count} key share file(s))")


def main(argv: Optional[list] = None) -> int:
    """CLI entry for ``python -m deploy.cdvn_migrate``.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` on plan/run error (including ``run`` aborting
        because Docker is up), ``2`` for ``plan`` when Docker is still running
        or its status cannot be verified.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Print migration plan (JSON or text)")
    p_plan.add_argument("--path", required=True, help="CDVN checkout or .env path")
    p_plan.add_argument("--json", action="store_true", help="Emit JSON")

    p_run = sub.add_parser("run", help="Run migration (aborts if Docker is up)")
    p_run.add_argument("--path", required=True, help="CDVN checkout or .env path")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only (for tests); do not write the system",
    )
    p_run.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip deploy-node (datadir/charon only); for advanced use",
    )
    p_run.add_argument(
        "--moves",
        default=None,
        help="Comma-separated relative Docker data/ paths to move/merge. "
        "Omit for all eligible; pass empty string for none. "
        "Does not affect mandatory .charon cluster overlay.",
    )
    p_run.add_argument(
        "--fresh",
        action="store_true",
        help="Stop Charon/VC and clear migrated cluster + VC keys before running",
    )
    p_run.add_argument(
        "--skip-charon-overlay",
        action="store_true",
        help="Skip .charon copy, charon.service import, and key sync (advanced)",
    )

    args = parser.parse_args(argv)
    try:
        if args.cmd == "plan":
            plan = plan_cdvn_migration(args.path)
            if args.json:
                payload = asdict(plan)
                print(json.dumps(payload, indent=2))
            else:
                print(plan.summary())
            if plan.docker_running:
                print("\nERROR: Docker Compose is running — migrate will abort.", file=sys.stderr)
                return 2
            if plan.docker_check_error:
                print(
                    f"\nERROR: Cannot verify CDVN Docker is stopped ({plan.docker_check_error}). "
                    "Migrate will abort.",
                    file=sys.stderr,
                )
                return 2
            return 0
        if args.cmd == "run":
            if args.moves is None:
                moves = None
            else:
                moves = [m.strip() for m in args.moves.split(",") if m.strip()]
            run_migration(
                args.path,
                dry_run=args.dry_run,
                apply_moves=moves,
                skip_deploy=args.skip_deploy,
                skip_charon_overlay=args.skip_charon_overlay,
                fresh=args.fresh,
            )
            return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
