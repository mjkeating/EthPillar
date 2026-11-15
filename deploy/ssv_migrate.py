"""Plan and run a legacy systemd SSV → EthPillar migration.

Discovers ``ssvnode`` / ``ssvdkg`` unit name variants, preserves multi-endpoint
BN/EL settings from the live YAML configs, quarantines legacy units as
``*.service.bak``, and installs EthPillar ``ssv-node`` / ``ssv-dkg`` with
secrets and DB under ``/var/lib/ssv/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from deploy.common import (
    DOWNLOAD_DIR,
    _normalize_release_version_key,
    download_release_file,
    extract_and_install,
    get_machine_architecture,
    write_service_file,
)
from deploy.systemd_units import (
    retire_systemd_unit,
    restore_retired_unit,
)
from deploy.ssv import (
    DEFAULT_API_PORT,
    DEFAULT_DKG_PORT,
    DEFAULT_EL_WS,
    DEFAULT_METRICS_PORT,
    DEFAULT_P2P_TCP_PORT,
    DEFAULT_P2P_UDP_PORT,
    SSV_CONFIG_FILE,
    SSV_DATA_DIR,
    SSV_DB_DIR,
    SSV_DKG_BIN,
    SSV_DKG_CONFIG_FILE,
    SSV_DKG_OUTPUT_DIR,
    SSV_DKG_SERVICE_PATH,
    SSV_DKG_SSL_DIR,
    SSV_DKG_TLS_CERT,
    SSV_DKG_TLS_KEY,
    SSV_NODE_BIN,
    SSV_NODE_SERVICE_PATH,
    SSV_PASSWORD_FILE,
    SSV_PRIVATE_KEY_FILE,
    SSV_USER,
    build_ssvnode_from_source,
    ensure_ssv_datadir,
    generate_ssv_config,
    generate_ssv_dkg_config,
    generate_ssv_dkg_service,
    generate_ssv_node_service,
    get_dkg_release_info,
    get_release_info,
    import_operator_keystore_files,
    install_binary_file,
    operator_keystore_present,
    path_is_dir,
    path_is_file,
    _sudo_write_text,
)

_SERVICE_BAK_RE = re.compile(r"^(.+\.service)\.bak(?:\.\d+)?$")

SYSTEMD_UNIT_DIR = "/etc/systemd/system"
ETHPILLAR_NODE_UNIT = "ssv-node.service"
ETHPILLAR_DKG_UNIT = "ssv-dkg.service"

_NODE_NAME_RE = re.compile(
    r"^(ssvnode|ssv[-_]?node|ssv)\.service$",
    re.IGNORECASE,
)
_DKG_NAME_RE = re.compile(
    r"^(ssvdkg|ssv[-_]?dkg)\.service$",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"v?\d+\.\d+(?:\.\d+)?", re.IGNORECASE)


@dataclass
class DiscoveredUnit:
    """One legacy systemd unit involved in the migrate."""

    role: str  # "node" | "dkg"
    unit_name: str
    unit_path: str
    working_directory: str = ""
    exec_start: str = ""
    binary_path: str = ""
    config_path: str = ""


@dataclass
class BinaryPlan:
    """How migrate will obtain an EthPillar-managed binary."""

    role: str
    action: str  # copy | rebuild | download | missing
    source_path: str = ""
    installed_version: str = ""
    latest_version: str = ""
    note: str = ""


@dataclass
class SsvMigratePlan:
    """Full migration plan for display / JSON."""

    node_unit: Optional[DiscoveredUnit] = None
    dkg_unit: Optional[DiscoveredUnit] = None
    eth_network: str = "mainnet"
    beacon_node_addr: str = ""
    eth1_ws_addr: str = DEFAULT_EL_WS
    eth1_http_addr: str = "http://127.0.0.1:8545"
    p2p_tcp_port: int = DEFAULT_P2P_TCP_PORT
    p2p_udp_port: int = DEFAULT_P2P_UDP_PORT
    metrics_port: int = DEFAULT_METRICS_PORT
    api_port: int = DEFAULT_API_PORT
    dkg_port: int = DEFAULT_DKG_PORT
    p2p_external_ip: str = ""
    operator_id: str = ""
    private_key_path: str = ""
    password_path: str = ""
    db_path: str = ""
    dkg_output_path: str = ""
    dkg_script_path: str = ""
    tls_cert_path: str = ""
    tls_key_path: str = ""
    node_binary: Optional[BinaryPlan] = None
    dkg_binary: Optional[BinaryPlan] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable plan text for TUI / CLI."""
        lines = ["SSV systemd → EthPillar migration plan", ""]
        # Data + BN/EL first so whiptail shows them without scrolling past unit/binary noise.
        lines.append("Data → /var/lib/ssv/ (shared by ssv-node + ssv-dkg):")
        lines.append(f"  keystore:  {self.private_key_path or '(missing)'}")
        lines.append(f"  password:  {self.password_path or '(missing)'}")
        lines.append(f"  db:        {self.db_path or '(missing)'} → {SSV_DB_DIR}")
        if self.dkg_output_path:
            lines.append(
                f"  dkg out:   {self.dkg_output_path} → {SSV_DKG_OUTPUT_DIR}"
            )
        else:
            lines.append(f"  dkg out:   (none found; EthPillar uses {SSV_DKG_OUTPUT_DIR})")
        if self.tls_cert_path or self.tls_key_path:
            lines.append(
                f"  tls cert:  {self.tls_cert_path or '(missing)'} → {SSV_DKG_TLS_CERT}"
            )
            lines.append(
                f"  tls key:   {self.tls_key_path or '(missing)'} → {SSV_DKG_TLS_KEY}"
            )
        lines.append(
            "  note: keystore+password+db are shared; DKG also gets operator.yaml"
        )
        lines.append("")
        lines.append(f"Network:     {self.eth_network}")
        lines.append(f"BeaconNode:  {self.beacon_node_addr or '(empty)'}")
        lines.append(f"ETH1 WS:     {self.eth1_ws_addr}")
        lines.append(f"ETH1 HTTP:   {self.eth1_http_addr}")
        lines.append(
            f"Ports:       p2p_tcp={self.p2p_tcp_port} p2p_udp={self.p2p_udp_port} "
            f"metrics={self.metrics_port} api={self.api_port} dkg={self.dkg_port}"
        )
        if self.p2p_external_ip:
            lines.append(f"HostAddress: {self.p2p_external_ip}")
        if self.operator_id:
            lines.append(f"operatorID:  {self.operator_id}")
        else:
            lines.append("operatorID:  (unset — edit /var/lib/ssv/operator.yaml after)")
        lines.append("")
        if self.node_unit:
            lines.append(
                f"Node unit: {self.node_unit.unit_name} → {ETHPILLAR_NODE_UNIT}"
            )
            lines.append(f"  config: {self.node_unit.config_path or '(none)'}")
            lines.append(f"  binary: {self.node_unit.binary_path or '(none)'}")
        else:
            lines.append("Node unit: (not found)")
        if self.dkg_unit:
            lines.append(
                f"DKG unit:  {self.dkg_unit.unit_name} → {ETHPILLAR_DKG_UNIT}"
            )
            dkg_cfg = self.dkg_unit.config_path or "(none — will synthesize operator.yaml)"
            lines.append(f"  config: {dkg_cfg}")
            lines.append(f"  binary: {self.dkg_unit.binary_path or '(none)'}")
        else:
            lines.append("DKG unit:  (not found — node-only migrate)")
        lines.append("")
        if self.node_binary:
            lines.append(
                f"ssvnode:  {self.node_binary.action} "
                f"(installed={self.node_binary.installed_version or '?'} "
                f"latest={self.node_binary.latest_version or '?'})"
            )
            if self.node_binary.note:
                lines.append(f"          {self.node_binary.note}")
        if self.dkg_binary:
            lines.append(
                f"ssv-dkg:  {self.dkg_binary.action} "
                f"(installed={self.dkg_binary.installed_version or '?'} "
                f"latest={self.dkg_binary.latest_version or '?'})"
            )
            if self.dkg_binary.note:
                lines.append(f"          {self.dkg_binary.note}")
        lines.append("")
        lines.append(
            "Legacy units: stop → disable → rename *.service.bak → "
            "daemon-reload → verify absent (shared retire helper)"
        )
        lines.append(
            f"EthPillar datadir: {SSV_DATA_DIR} (configs keep BN/EL endpoints; "
            "no .env.overrides)"
        )
        lines.append(
            "Rollback: python3 -m deploy.ssv_migrate rollback "
            "(retires EthPillar ssv-* first, then restores legacy *.bak)"
        )
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"  - {w}" for w in self.warnings)
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"  - {e}" for e in self.errors)
        return "\n".join(lines)


def _looks_like_ethpillar_ssv_unit(unit_text: str, *, role: str) -> bool:
    """True when unit body matches EthPillar-managed SSV layout markers."""
    text = unit_text or ""
    if f"WorkingDirectory={SSV_DATA_DIR}" not in text and f"WorkingDirectory={SSV_DATA_DIR}/" not in text:
        # Allow WorkingDirectory=/var/lib/ssv without trailing slash only.
        if "WorkingDirectory=/var/lib/ssv" not in text:
            return False
    if role == "node":
        return (
            "/usr/local/bin/ssvnode" in text
            or f"--config={SSV_CONFIG_FILE}" in text
            or "--config=/var/lib/ssv/config.yaml" in text
        )
    return (
        "/usr/local/bin/ssv-dkg" in text
        or "--configPath=./operator.yaml" in text
        or f"--configPath={SSV_DKG_CONFIG_FILE}" in text
    )


def classify_unit_name(
    filename: str,
    *,
    unit_text: str = "",
) -> Optional[str]:
    """Return ``node``, ``dkg``, or None for a systemd unit basename.

    EthPillar-managed ``ssv-node.service`` / ``ssv-dkg.service`` (datadir under
    ``/var/lib/ssv`` + EthPillar binaries) are skipped. A *legacy* unit that
    happens to use those names but points elsewhere is still classified so
    migrate can take it over.
    """
    name = filename.strip()
    if not name.endswith(".service"):
        name = f"{name}.service"
    lower = name.lower()
    if _DKG_NAME_RE.match(name) or lower == ETHPILLAR_DKG_UNIT:
        if lower == ETHPILLAR_DKG_UNIT and _looks_like_ethpillar_ssv_unit(
            unit_text, role="dkg"
        ):
            return None
        if lower == ETHPILLAR_DKG_UNIT and not unit_text:
            # Without body, treat the EthPillar name as already-managed.
            return None
        if "dkg" in lower or _DKG_NAME_RE.match(name):
            return "dkg"
        return None
    if "dkg" in lower:
        return None
    if _NODE_NAME_RE.match(name) or lower == ETHPILLAR_NODE_UNIT:
        if lower == ETHPILLAR_NODE_UNIT and (
            not unit_text
            or _looks_like_ethpillar_ssv_unit(unit_text, role="node")
        ):
            return None
        if _NODE_NAME_RE.match(name):
            return "node"
    return None


def _join_unit_continuations(text: str) -> str:
    """Join systemd line continuations ending with ``\\``."""
    lines: List[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped_right = raw.rstrip()
        if stripped_right.endswith("\\"):
            buf += stripped_right[:-1] + " "
            continue
        buf += stripped_right
        lines.append(buf)
        buf = ""
    if buf:
        lines.append(buf)
    return "\n".join(lines)


def _parse_unit_file(text: str) -> Dict[str, str]:
    """Parse simple key=value pairs from a systemd unit body."""
    values: Dict[str, str] = {}
    for raw in _join_unit_continuations(text).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        # Last assignment wins (common for multi ExecStart=).
        values[key.strip()] = val.strip()
    return values


def _first_token(exec_start: str) -> str:
    """Return the binary path from an ExecStart line (strip systemd prefixes)."""
    text = exec_start.strip()
    for prefix in ("-", "@", ":", "+", "!"):
        while text.startswith(prefix):
            text = text[1:].lstrip()
    if not text:
        return ""
    # Handle quoted path.
    if text[0] in ("'", '"'):
        quote = text[0]
        end = text.find(quote, 1)
        if end > 0:
            return text[1:end]
    return text.split()[0]


def _config_from_exec(exec_start: str, *, flags: Sequence[str]) -> str:
    """Extract a config path after one of *flags* in ExecStart."""
    tokens = exec_start.split()
    for i, tok in enumerate(tokens):
        for flag in flags:
            if tok == flag and i + 1 < len(tokens):
                return tokens[i + 1].strip("'\"")
            if tok.startswith(f"{flag}="):
                return tok.split("=", 1)[1].strip("'\"")
            # Short -c /path used by some wrappers.
            if flag in ("--config", "--configPath") and tok in ("-c", "-config"):
                if i + 1 < len(tokens):
                    return tokens[i + 1].strip("'\"")
    return ""


_ENV_CONFIG_KEYS = {
    "CONFIG",
    "CONFIG_PATH",
    "CONFIGPATH",
    "SSV_CONFIG",
    "SSV_CONFIG_PATH",
    "OPERATOR_CONFIG",
    "OPERATOR_CONFIG_PATH",
}


def _config_from_env_assignments(text: str) -> str:
    """Return a config path from KEY=value assignments in *text*."""
    for raw in text.replace("\t", " ").splitlines():
        for part in raw.replace(";", " ").split():
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            if key.upper().lstrip("-") in _ENV_CONFIG_KEYS:
                return val.strip("'\"")
    # Also scan space-separated Environment=FOO=bar BAR=baz forms.
    for part in text.replace("\t", " ").split():
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key.upper() in _ENV_CONFIG_KEYS:
            return val.strip("'\"")
    return ""


def _config_from_environment_file(
    fields: Dict[str, str],
    *,
    unit_dir: str = SYSTEMD_UNIT_DIR,
    workdir: str = "",
) -> str:
    """Load EnvironmentFile= / EnvironmentFile=- paths and dig for config keys."""
    # _parse_unit_file keeps last assignment only; also scan raw if present.
    env_file = fields.get("EnvironmentFile", "")
    if not env_file:
        return ""
    path = env_file.lstrip("-").strip().strip("'\"")
    if not path:
        return ""
    resolved = _resolve_path(path, workdir=workdir, unit_dir=unit_dir)
    content = _read_text(resolved)
    if not content:
        return ""
    return _config_from_env_assignments(content)


def _config_from_environment(
    fields: Dict[str, str],
    *,
    unit_dir: str = SYSTEMD_UNIT_DIR,
    workdir: str = "",
) -> str:
    """Pick a config path from Environment= and EnvironmentFile= keys."""
    env_val = fields.get("Environment", "")
    found = _config_from_env_assignments(env_val)
    if found:
        return found
    return _config_from_environment_file(
        fields, unit_dir=unit_dir, workdir=workdir
    )


def _flatten_shell_continuations(text: str) -> str:
    """Join ``\\`` line continuations in a shell script into single lines."""
    return re.sub(r"\\\s*\n", " ", text)


def _parse_cli_flags(text: str) -> Dict[str, str]:
    """Parse ``--flag value`` / ``--flag=value`` pairs from script or ExecStart text."""
    flat = _flatten_shell_continuations(text)
    # Drop comments.
    flat = re.sub(r"(?m)#.*$", "", flat)
    tokens = flat.split()
    flags: Dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--") and "=" in tok:
            key, val = tok[2:].split("=", 1)
            flags[key] = val.strip("'\"")
            i += 1
            continue
        if tok.startswith("--") and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
            flags[tok[2:]] = tokens[i + 1].strip("'\"")
            i += 2
            continue
        if tok.startswith("--"):
            flags[tok[2:]] = "true"
            i += 1
            continue
        i += 1
    return flags


def _config_from_script(script_path: str) -> str:
    """Extract --config / --configPath from a shell wrapper script."""
    if not script_path or not path_is_file(script_path):
        return ""
    # Never decode ELF/Go binaries as UTF-8 (ExecStart is often ssvnode itself).
    if _looks_like_binary(script_path):
        return ""
    if not script_path.endswith((".sh", ".bash")):
        # Allow extensionless text wrappers; skip large non-scripts.
        try:
            if os.path.getsize(script_path) > 512_000:
                return ""
        except OSError:
            return ""
    text = _read_text(script_path)
    if not text:
        return ""
    flags = _parse_cli_flags(text)
    for key in ("configPath", "config"):
        if flags.get(key):
            return flags[key]
    found = _config_from_exec(
        _flatten_shell_continuations(text),
        flags=("--configPath", "--config", "-config"),
    )
    if found:
        return found
    # Bare path assignment: CONFIG=/... or configPath: /...
    for match in re.finditer(
        r"""(?im)(?:CONFIG(?:_PATH)?|configPath|config)\s*=\s*['\"]?([^\s'\"]+)""",
        text,
    ):
        return match.group(1)
    for match in re.finditer(
        r"""(?im)(?:configPath|config)\s*[:=]\s*['\"]?([^\s'\"]+\.ya?ml)""",
        text,
    ):
        return match.group(1)
    return ""


def _find_dkg_wrapper_script(unit: Optional[DiscoveredUnit]) -> str:
    """Locate run_ssv-dkg.sh (or ExecStart script) for CLI-flag parsing."""
    if not unit:
        return ""
    raw = _first_token(unit.exec_start)
    if raw:
        raw_abs = _resolve_path(
            raw, workdir=unit.working_directory, unit_dir=SYSTEMD_UNIT_DIR
        )
        if raw_abs.endswith((".sh", ".bash")) and path_is_file(raw_abs):
            return raw_abs
    for root in _search_roots_for_unit(unit):
        for name in ("run_ssv-dkg.sh", "run-ssv-dkg.sh", "start-dkg.sh"):
            path = os.path.join(root, name)
            if path_is_file(path):
                return path
    return ""


def _dkg_settings_from_wrapper(
    script_path: str,
    *,
    workdir: str = "",
) -> Dict[str, str]:
    """Resolve DKG settings from a start-operator CLI wrapper script."""
    text = _read_text(script_path)
    if not isinstance(text, str) or not text.strip():
        return {}
    flags = _parse_cli_flags(text)
    script_dir = os.path.dirname(script_path)
    bases = [workdir, script_dir]
    out: Dict[str, str] = {"script_path": script_path}
    mapping = {
        "privKey": "private_key_path",
        "privKeyPassword": "password_path",
        "operatorID": "operator_id",
        "port": "dkg_port",
        "ethEndpointURL": "eth1_http_addr",
        "outputPath": "dkg_output_path",
        "serverTLSCertPath": "tls_cert_path",
        "serverTLSKeyPath": "tls_key_path",
        "logFilePath": "dkg_log_path",
    }
    for flag, dest in mapping.items():
        raw = flags.get(flag, "")
        if not raw:
            continue
        if dest in {
            "private_key_path",
            "password_path",
            "dkg_output_path",
            "tls_cert_path",
            "tls_key_path",
            "dkg_log_path",
        }:
            out[dest] = _resolve_path(raw, workdir=workdir, bases=bases)
        else:
            out[dest] = raw
    return out


def _search_roots_for_unit(unit: DiscoveredUnit) -> List[str]:
    """Candidate directories near a unit's workdir / ExecStart path."""
    roots: List[str] = []
    if unit.working_directory:
        roots.append(unit.working_directory)
    if unit.binary_path:
        bin_dir = os.path.dirname(unit.binary_path)
        roots.append(bin_dir)
        parent = os.path.dirname(bin_dir)
        if parent:
            roots.append(parent)
            # bin/ → repo root already; also grandparent for nested layouts
            roots.append(os.path.dirname(parent))
    # Dedupe preserving order.
    seen: set[str] = set()
    ordered: List[str] = []
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        ordered.append(root)
    return ordered


def _resolve_real_binary(path: str, *, role: str) -> str:
    """If ExecStart is a wrapper script, prefer a nearby real binary."""
    if not path:
        return ""
    if path_is_file(path) and not path.endswith((".sh", ".bash")):
        base = os.path.basename(path)
        if role == "node" and "ssvnode" in base.lower():
            return path
        if role == "dkg" and "dkg" in base.lower() and not base.endswith(".sh"):
            return path
        # Non-script executable — trust ExecStart.
        return path
    roots = [os.path.dirname(path)]
    parent = os.path.dirname(os.path.dirname(path))
    if parent:
        roots.append(parent)
        roots.append(os.path.join(parent, "bin"))
    names = ("ssvnode",) if role == "node" else ("ssv-dkg", "ssvdkg")
    for root in roots:
        for name in names:
            candidate = os.path.join(root, name)
            if path_is_file(candidate):
                return candidate
            candidate = os.path.join(root, "bin", name)
            if path_is_file(candidate):
                return candidate
    return path


def _find_node_config(unit: DiscoveredUnit) -> str:
    """Resolve node config.yaml from ExecStart or checkout heuristics."""
    if unit.config_path and path_is_file(unit.config_path):
        return unit.config_path
    for root in _search_roots_for_unit(unit):
        for rel in (
            "config/config.yaml",
            "config.yaml",
            "config/config.yml",
            "config.yml",
        ):
            path = os.path.join(root, rel)
            if path_is_file(path):
                return path
    return unit.config_path or ""


def _resolve_path(
    path: str,
    *,
    workdir: str = "",
    unit_dir: str = "",
    bases: Optional[Sequence[str]] = None,
) -> str:
    """Resolve relative paths against preferred bases, then workdir/unit_dir."""
    if not path:
        return ""
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    candidates: List[str] = []
    if bases:
        candidates.extend(b for b in bases if b)
    if workdir:
        candidates.append(workdir)
    if unit_dir:
        candidates.append(unit_dir)
    if not candidates:
        candidates.append(os.getcwd())
    # Prefer a base where the path already exists (db dirs, key files).
    for base in candidates:
        joined = os.path.normpath(os.path.join(base, expanded))
        if os.path.exists(joined):
            return joined
    return os.path.normpath(os.path.join(candidates[0], expanded))


def _eth1_ws_to_http(eth1_ws: str) -> str:
    """Derive DKG ethEndpointURL list from node ETH1Addr WebSocket URLs.

    Only rewrites the scheme (``ws→http``, ``wss→https``). Ports are preserved
    so non-standard WS/HTTP layouts are not silently remapped.
    """
    if not eth1_ws.strip():
        return "http://127.0.0.1:8545"
    sep = ";" if ";" in eth1_ws else ","
    parts = [p.strip() for p in re.split(r"[;,]", eth1_ws) if p.strip()]
    converted: List[str] = []
    for part in parts:
        url = part.replace("ws://", "http://").replace("wss://", "https://")
        converted.append(url)
    return sep.join(converted) if converted else "http://127.0.0.1:8545"


def _looks_like_binary(path: str) -> bool:
    """Return True when *path* is an ELF/ Mach-O / PE binary (not a text wrapper)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError:
        result = subprocess.run(
            ["sudo", "head", "-c", "4", path],
            capture_output=True,
            check=False,
        )
        head = result.stdout if result.returncode == 0 else b""
    if head.startswith(b"\x7fELF") or head.startswith(b"\xcf\xfa\xed\xfe"):
        return True
    if head[:2] in (b"MZ", b"\xfe\xed"):
        return True
    return False


def _read_text(path: str) -> str:
    """Read a text file; return empty string on missing/binary/undecodable data."""
    if not path:
        return ""
    if os.path.isfile(path) and _looks_like_binary(path):
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except UnicodeDecodeError:
        try:
            with open(path, encoding="latin-1") as handle:
                return handle.read()
        except OSError:
            return ""
    except OSError:
        result = subprocess.run(
            ["sudo", "cat", path],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or result.stdout in (None, b"", ""):
            return ""
        raw = result.stdout
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, (bytes, bytearray)):
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")


def _yaml_scalar_or_list(content: str, key: str) -> str:
    """Return a YAML mapping value as a string (join list items with commas).

    Preserves multi-endpoint scalars verbatim. For block lists, joins with ``,``.
    """
    lines = content.splitlines()
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.*)$")
    for i, line in enumerate(lines):
        match = key_re.match(line)
        if not match:
            continue
        rest = match.group(1).strip()
        if rest and rest not in ("|", ">", ">-", "|-"):
            # Strip optional quotes.
            if (rest.startswith('"') and rest.endswith('"')) or (
                rest.startswith("'") and rest.endswith("'")
            ):
                return rest[1:-1]
            return rest
        # Block list under this key.
        items: List[str] = []
        for follow in lines[i + 1 :]:
            stripped = follow.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Dedent: stop when indentation returns to key level or less
            # without a list marker — approximate with leading spaces count.
            if follow[:1] not in (" ", "\t") and not stripped.startswith("-"):
                break
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip("\"'")
                if item:
                    items.append(item)
            elif items and follow.startswith((" ", "\t")):
                continue
            else:
                break
        if items:
            return ",".join(items)
        return ""
    return ""


def _yaml_int(content: str, key: str, default: int) -> int:
    raw = _yaml_scalar_or_list(content, key)
    if not raw:
        return default
    try:
        value = int(raw.split()[0])
    except ValueError:
        return default
    return value if 1 <= value <= 65535 else default


def parse_node_config(content: str) -> Dict[str, object]:
    """Extract migrate-relevant fields from an SSV node config.yaml body."""
    network_raw = _yaml_scalar_or_list(content, "Network") or "mainnet"
    network = network_raw.strip().lower()
    # EthPillar uses hoodi/sepolia/mainnet; SSV may already match.
    eth_network = network if network else "mainnet"
    return {
        "eth_network": eth_network,
        "beacon_node_addr": _yaml_scalar_or_list(content, "BeaconNodeAddr"),
        "eth1_ws_addr": _yaml_scalar_or_list(content, "ETH1Addr") or DEFAULT_EL_WS,
        "p2p_tcp_port": _yaml_int(content, "TcpPort", DEFAULT_P2P_TCP_PORT),
        "p2p_udp_port": _yaml_int(content, "UdpPort", DEFAULT_P2P_UDP_PORT),
        "metrics_port": _yaml_int(content, "MetricsAPIPort", DEFAULT_METRICS_PORT),
        "api_port": _yaml_int(content, "SSVAPIPort", DEFAULT_API_PORT),
        "p2p_external_ip": _yaml_scalar_or_list(content, "HostAddress"),
        "db_path": _yaml_scalar_or_list(content, "Path"),
        "private_key_path": _yaml_scalar_or_list(content, "PrivateKeyFile"),
        "password_path": _yaml_scalar_or_list(content, "PasswordFile"),
    }


def parse_dkg_config(content: str) -> Dict[str, object]:
    """Extract migrate-relevant fields from a DKG operator config body."""
    return {
        "private_key_path": _yaml_scalar_or_list(content, "privKey"),
        "password_path": _yaml_scalar_or_list(content, "privKeyPassword"),
        "operator_id": _yaml_scalar_or_list(content, "operatorID"),
        "dkg_port": _yaml_int(content, "port", DEFAULT_DKG_PORT),
        "eth1_http_addr": _yaml_scalar_or_list(content, "ethEndpointURL")
        or "http://127.0.0.1:8545",
        "dkg_output_path": _yaml_scalar_or_list(content, "outputPath"),
    }


def discover_units(
    unit_dir: str = SYSTEMD_UNIT_DIR,
) -> Tuple[Optional[DiscoveredUnit], Optional[DiscoveredUnit]]:
    """Find legacy node/DKG units under *unit_dir* (EthPillar units skipped)."""
    node: Optional[DiscoveredUnit] = None
    dkg: Optional[DiscoveredUnit] = None
    try:
        names = sorted(os.listdir(unit_dir))
    except OSError:
        return None, None
    for name in names:
        if not name.endswith(".service"):
            continue
        path = os.path.join(unit_dir, name)
        text = _read_text(path)
        role = classify_unit_name(name, unit_text=text)
        if not role:
            continue
        if not text:
            continue
        fields = _parse_unit_file(text)
        workdir = fields.get("WorkingDirectory", "")
        exec_start = fields.get("ExecStart", "")
        raw_bin = _resolve_path(
            _first_token(exec_start), workdir=workdir, unit_dir=unit_dir
        )
        binary = _resolve_real_binary(raw_bin, role=role)
        if role == "node":
            config = _config_from_exec(
                exec_start, flags=("--config", "-config")
            )
        else:
            config = _config_from_exec(
                exec_start, flags=("--configPath", "--config")
            )
        if not config:
            config = _config_from_environment(
                fields, unit_dir=unit_dir, workdir=workdir
            )
        if not config and raw_bin:
            config = _config_from_script(raw_bin)
        config_path = _resolve_path(config, workdir=workdir, unit_dir=unit_dir)
        discovered = DiscoveredUnit(
            role=role,
            unit_name=name,
            unit_path=path,
            working_directory=workdir,
            exec_start=exec_start,
            binary_path=binary,
            config_path=config_path,
        )
        # Fill config via checkout heuristics when ExecStart omits --config.
        if role == "node":
            discovered.config_path = _find_node_config(discovered)
        else:
            yaml_cfg = _find_dkg_config(discovered)
            if yaml_cfg:
                discovered.config_path = yaml_cfg
            elif not _looks_like_yaml_config(discovered.config_path):
                # Drop logFilePath / non-yaml false positives (e.g. debug.log).
                discovered.config_path = ""
        if role == "node" and node is None:
            node = discovered
        elif role == "dkg" and dkg is None:
            dkg = discovered
    return node, dkg


def _find_fallback_secrets(
    *,
    workdir: str,
    config_path: str,
    extra_roots: Optional[Sequence[str]] = None,
) -> Tuple[str, str]:
    """Locate encrypted_private_key.json + password near workdir/config."""
    candidates: List[str] = []
    if config_path:
        candidates.append(os.path.dirname(config_path))
        # e.g. ~/ssv/config/config.yaml → ~/ssv
        parent = os.path.dirname(os.path.dirname(config_path))
        if parent:
            candidates.append(parent)
    if workdir:
        candidates.append(workdir)
    if extra_roots:
        candidates.extend(extra_roots)
    for base in candidates:
        if not base:
            continue
        key = os.path.join(base, "encrypted_private_key.json")
        pw = os.path.join(base, "password")
        if path_is_file(key) and path_is_file(pw):
            return key, pw
    return "", ""


def _looks_like_yaml_config(path: str) -> bool:
    """True when *path* is an existing ``.yaml`` / ``.yml`` file (not a log)."""
    if not path or not path_is_file(path):
        return False
    lower = path.lower()
    if lower.endswith((".yaml", ".yml")):
        return True
    # Reject logs / TLS / binaries that sometimes land next to operator-config/.
    if lower.endswith((".log", ".crt", ".key", ".pem", ".json", ".txt")):
        return False
    return False


def _find_dkg_config(
    unit: Optional[DiscoveredUnit],
) -> str:
    """Resolve DKG YAML config path from unit or operator-config/ heuristics.

    CLI-only wrappers (``run_ssv-dkg.sh`` with flags, no ``--configPath``) often
    write ``--logFilePath ./operator-config/debug.log`` — never treat that as
    the operator config.
    """
    if unit and _looks_like_yaml_config(unit.config_path):
        return unit.config_path
    if not unit:
        return ""
    # Wrapper script (e.g. run_ssv-dkg.sh) often embeds --configPath.
    raw = _first_token(unit.exec_start)
    if raw:
        raw_abs = _resolve_path(
            raw, workdir=unit.working_directory, unit_dir=SYSTEMD_UNIT_DIR
        )
        script_cfg = _config_from_script(raw_abs)
        if script_cfg:
            resolved = _resolve_path(
                script_cfg,
                workdir=unit.working_directory
                or os.path.dirname(raw_abs)
                or os.path.dirname(unit.binary_path),
                unit_dir=SYSTEMD_UNIT_DIR,
            )
            if _looks_like_yaml_config(resolved):
                return resolved
    for root in _search_roots_for_unit(unit):
        op_dir = os.path.join(root, "operator-config")
        if path_is_dir(op_dir):
            yaml_files = sorted(
                n
                for n in os.listdir(op_dir)
                if n.endswith((".yaml", ".yml"))
            )
            # Prefer names that look like operator config.
            for preferred in (
                "operator.yaml",
                "operator.yml",
                "config.yaml",
                "config.yml",
            ):
                if preferred in yaml_files:
                    return os.path.join(op_dir, preferred)
            if yaml_files:
                return os.path.join(op_dir, yaml_files[0])
            # Do not fall back to debug.log / other non-yaml files here.
        for name in ("operator.yaml", "config.yaml", "operator-config.yaml"):
            path = os.path.join(root, name)
            if path_is_file(path):
                return path
    if unit and _looks_like_yaml_config(unit.config_path):
        return unit.config_path
    return ""


def probe_binary_version(binary_path: str, *, role: str) -> str:
    """Return a semver-ish version string from a binary, or empty."""
    if not binary_path or not path_is_file(binary_path):
        return ""
    commands = (
        [binary_path, "version"],
        [binary_path, "--version"],
        [binary_path, "version", "--output", "simple"],
    )
    output = ""
    for cmd in commands:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        blob = (result.stdout or "") + "\n" + (result.stderr or "")
        if blob.strip():
            output = blob
            break
    match = _VERSION_RE.search(output)
    if not match:
        return ""
    ver = match.group(0)
    if not ver.lower().startswith("v"):
        ver = f"v{ver}"
    return ver


def versions_match(installed: str, latest: str) -> bool:
    """True when normalized release keys match."""
    if not installed or not latest:
        return False
    return _normalize_release_version_key(installed) == _normalize_release_version_key(
        latest
    )


def _plan_node_binary(
    source_path: str,
    *,
    rebuild: bool,
    allow_older: bool,
) -> BinaryPlan:
    arch_amd64 = get_machine_architecture() == "amd64"
    latest = ""
    try:
        latest = get_release_info("LATEST", arch_amd64)["version"]
    except Exception as exc:  # noqa: BLE001 — plan should still render
        return BinaryPlan(
            role="node",
            action="rebuild",
            source_path=source_path,
            latest_version="",
            note=f"could not resolve LATEST ({exc}); will rebuild",
        )
    installed = probe_binary_version(source_path, role="node")
    if rebuild:
        return BinaryPlan(
            role="node",
            action="rebuild",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="--rebuild-ssvnode forced",
        )
    if source_path and path_is_file(source_path) and versions_match(installed, latest):
        return BinaryPlan(
            role="node",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="legacy binary matches LATEST",
        )
    if source_path and path_is_file(source_path) and allow_older and installed:
        return BinaryPlan(
            role="node",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="WARNING: allowing older binary via --allow-older-binary",
        )
    note = "missing or not LATEST — will rebuild from source"
    if installed and latest and not versions_match(installed, latest):
        note = f"legacy {installed} != LATEST {latest} — will rebuild"
    elif not source_path or not path_is_file(source_path):
        note = "legacy binary missing — will rebuild"
    elif not installed:
        note = "could not parse legacy version — will rebuild"
    return BinaryPlan(
        role="node",
        action="rebuild",
        source_path=source_path,
        installed_version=installed,
        latest_version=latest,
        note=note,
    )


def _plan_dkg_binary(
    source_path: str,
    *,
    allow_older: bool,
    want_dkg: bool,
) -> Optional[BinaryPlan]:
    if not want_dkg:
        return None
    arch_amd64 = get_machine_architecture() == "amd64"
    latest = ""
    try:
        latest = get_dkg_release_info("LATEST", arch_amd64)["version"]
    except Exception as exc:  # noqa: BLE001
        return BinaryPlan(
            role="dkg",
            action="download",
            source_path=source_path,
            note=f"could not resolve DKG LATEST ({exc}); will download",
        )
    installed = probe_binary_version(source_path, role="dkg")
    if source_path and path_is_file(source_path) and versions_match(installed, latest):
        return BinaryPlan(
            role="dkg",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="legacy binary matches LATEST",
        )
    if source_path and path_is_file(source_path) and allow_older and installed:
        return BinaryPlan(
            role="dkg",
            action="copy",
            source_path=source_path,
            installed_version=installed,
            latest_version=latest,
            note="WARNING: allowing older binary via --allow-older-binary",
        )
    note = "will download GitHub release zip"
    if installed and latest and not versions_match(installed, latest):
        note = f"legacy {installed} != LATEST {latest} — will download"
    return BinaryPlan(
        role="dkg",
        action="download",
        source_path=source_path,
        installed_version=installed,
        latest_version=latest,
        note=note,
    )


def ethpillar_ssv_already_installed(unit_dir: str = SYSTEMD_UNIT_DIR) -> bool:
    """True when EthPillar ``ssv-node.service`` + keystore are already in place."""
    path = os.path.join(unit_dir, ETHPILLAR_NODE_UNIT)
    if not path_is_file(path):
        return False
    text = _read_text(path)
    if not _looks_like_ethpillar_ssv_unit(text, role="node"):
        return False
    return operator_keystore_present()


def ethpillar_dkg_unit_present(unit_dir: str = SYSTEMD_UNIT_DIR) -> bool:
    """True when EthPillar ``ssv-dkg.service`` exists and looks managed."""
    path = os.path.join(unit_dir, ETHPILLAR_DKG_UNIT)
    if not path_is_file(path):
        return False
    return _looks_like_ethpillar_ssv_unit(_read_text(path), role="dkg")


def rewrite_legacy_node_config_for_ethpillar(
    legacy_content: str,
    *,
    private_key_file: str = SSV_PRIVATE_KEY_FILE,
    password_file: str = SSV_PASSWORD_FILE,
    db_path: str = SSV_DB_DIR,
    log_file: str = f"{SSV_DATA_DIR}/debug.log",
) -> str:
    """Preserve legacy YAML knobs; rewrite only EthPillar-owned paths."""
    content = legacy_content
    replacements = (
        (r"(?m)^(\s*PrivateKeyFile:\s*).*$", rf"\g<1>{private_key_file}"),
        (r"(?m)^(\s*PasswordFile:\s*).*$", rf"\g<1>{password_file}"),
        (r"(?m)^(\s*Path:\s*).*$", rf"\g<1>{db_path}"),
        (r"(?m)^(\s*LogFilePath:\s*).*$", rf"\g<1>{log_file}"),
    )
    for pattern, repl in replacements:
        content, n = re.subn(pattern, repl, content, count=1)
        if n == 0 and "PrivateKeyFile" in pattern:
            # Ensure KeyStore block exists when legacy omitted it.
            content = (
                content.rstrip()
                + "\n\nKeyStore:\n"
                + f"  PrivateKeyFile: {private_key_file}\n"
                + f"  PasswordFile: {password_file}\n"
            )
    if not re.search(r"(?m)^\s*Path:\s*", content):
        content = content.rstrip() + f"\n\ndb:\n  Path: {db_path}\n"
    if not content.endswith("\n"):
        content += "\n"
    return content


def endpoints_look_local(*urls: str) -> bool:
    """True when every non-empty URL targets localhost / 127.0.0.1 / ::1."""
    local_hosts = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
    found_any = False
    for raw in urls:
        for part in re.split(r"[;,]", raw or ""):
            part = part.strip()
            if not part:
                continue
            found_any = True
            lower = part.lower()
            if not any(h in lower for h in local_hosts):
                return False
    return found_any


def execution_unit_exposes_ws(unit_text: str) -> bool:
    """True when an execution unit body enables eth1 WebSocket for SSV.

    Matches Geth/Reth/Erigon ``--ws``, ethrex ``--ws.enabled``, Besu
    ``--rpc-ws-enabled``, and Nethermind ``JsonRpc.WebSocketsPort``.
    """
    text = unit_text or ""
    if re.search(r"(?m)(?:^|[\s\\])--ws(?:[=\s.\\]|$)", text):
        return True
    if "--ws.enabled" in text or "--rpc-ws-enabled" in text:
        return True
    if "WebSocketsPort" in text:
        return True
    return False


def local_ethpillar_el_missing_ws(
    eth1_ws_addr: str,
    unit_dir: str = SYSTEMD_UNIT_DIR,
) -> Optional[str]:
    """Return an error message when local EthPillar EL lacks WS for SSV migrate.

    Safer than rewriting a live ``execution.service`` mid-migrate: fail clearly
    and ask the operator to enable WS (fresh SSV install passes ``enable_ws``)
    before retrying. Remote ETH1 endpoints and non-EthPillar EL units are ignored.
    """
    if not endpoints_look_local(eth1_ws_addr):
        return None
    path = os.path.join(unit_dir, "execution.service")
    if not path_is_file(path):
        return None
    text = _read_text(path)
    # EthPillar EL units always run as User=execution.
    if "User=execution" not in text:
        return None
    if execution_unit_exposes_ws(text):
        return None
    return (
        "Local EthPillar execution.service does not expose WebSocket on :8546 "
        "(required for SSV ETH1Addr). Geth and ethrex already enable WS; for "
        "Besu/Nethermind/Reth/Erigon, regenerate the EL unit with enable_ws "
        "(a fresh SSV install does this automatically), then re-run migrate. "
        "Refusing to mutate a running EL during SSV migrate."
    )


def plan_ssv_migration(
    *,
    unit_dir: str = SYSTEMD_UNIT_DIR,
    rebuild_ssvnode: bool = False,
    allow_older_binary: bool = False,
    allow_empty_db: bool = False,
) -> SsvMigratePlan:
    """Build a migration plan from discovered systemd units and YAML configs."""
    plan = SsvMigratePlan()
    node, dkg = discover_units(unit_dir)
    plan.node_unit = node
    plan.dkg_unit = dkg

    if not node:
        if ethpillar_ssv_already_installed(unit_dir):
            plan.warnings.append(
                "Already migrated to EthPillar "
                f"({ETHPILLAR_NODE_UNIT} + keystore present); nothing to do"
            )
            plan.eth_network = "mainnet"
            if not ethpillar_dkg_unit_present(unit_dir):
                plan.warnings.append(
                    f"{ETHPILLAR_DKG_UNIT} missing — run will repair when "
                    f"{SSV_DKG_CONFIG_FILE} or keystore is available"
                )
            return plan
        plan.errors.append(
            "No legacy SSV node unit found "
            "(expected ssvnode / ssv-node / ssv_node / ssv.service, or a "
            "non-EthPillar unit using those names)"
        )
        return plan

    node_cfg_path = _find_node_config(node)
    node.config_path = node_cfg_path
    node_roots = _search_roots_for_unit(node)
    node_bases: List[str] = []
    if node_cfg_path:
        cfg_dir = os.path.dirname(node_cfg_path)
        node_bases.append(cfg_dir)
        # config/config.yaml → repo root
        node_bases.append(os.path.dirname(cfg_dir))
    node_bases.extend(node_roots)

    node_cfg = _read_text(node_cfg_path) if node_cfg_path else ""
    if not node_cfg:
        plan.errors.append(f"Could not read node config: {node_cfg_path or '(empty)'}")
    else:
        parsed = parse_node_config(node_cfg)
        plan.eth_network = str(parsed["eth_network"])
        plan.beacon_node_addr = str(parsed["beacon_node_addr"])
        plan.eth1_ws_addr = str(parsed["eth1_ws_addr"])
        plan.p2p_tcp_port = int(parsed["p2p_tcp_port"])
        plan.p2p_udp_port = int(parsed["p2p_udp_port"])
        plan.metrics_port = int(parsed["metrics_port"])
        plan.api_port = int(parsed["api_port"])
        plan.p2p_external_ip = str(parsed["p2p_external_ip"])
        plan.db_path = _resolve_path(
            str(parsed["db_path"]),
            workdir=node.working_directory,
            unit_dir=unit_dir,
            bases=node_bases,
        )
        plan.private_key_path = _resolve_path(
            str(parsed["private_key_path"]),
            workdir=node.working_directory,
            unit_dir=unit_dir,
            bases=node_bases,
        )
        plan.password_path = _resolve_path(
            str(parsed["password_path"]),
            workdir=node.working_directory,
            unit_dir=unit_dir,
            bases=node_bases,
        )

    if not plan.private_key_path or not plan.password_path:
        fb_key, fb_pw = _find_fallback_secrets(
            workdir=node.working_directory,
            config_path=node_cfg_path,
            extra_roots=node_bases,
        )
        plan.private_key_path = plan.private_key_path or fb_key
        plan.password_path = plan.password_path or fb_pw

    # Default DKG HTTP from node WS (keystore is shared; operator.yaml is separate).
    plan.eth1_http_addr = _eth1_ws_to_http(plan.eth1_ws_addr)

    # Prefer CLI flags from run_ssv-dkg.sh (common upstream layout; no YAML).
    if dkg:
        wrapper = _find_dkg_wrapper_script(dkg)
        if wrapper:
            plan.dkg_script_path = wrapper
            parsed_wrap = _dkg_settings_from_wrapper(
                wrapper, workdir=dkg.working_directory
            )
            if parsed_wrap.get("operator_id"):
                plan.operator_id = parsed_wrap["operator_id"]
            if parsed_wrap.get("dkg_port"):
                try:
                    plan.dkg_port = int(parsed_wrap["dkg_port"])
                except ValueError:
                    pass
            if parsed_wrap.get("eth1_http_addr"):
                plan.eth1_http_addr = parsed_wrap["eth1_http_addr"]
            if parsed_wrap.get("dkg_output_path"):
                plan.dkg_output_path = parsed_wrap["dkg_output_path"]
            if parsed_wrap.get("tls_cert_path"):
                plan.tls_cert_path = parsed_wrap["tls_cert_path"]
            if parsed_wrap.get("tls_key_path"):
                plan.tls_key_path = parsed_wrap["tls_key_path"]
            # DKG wrapper may point at its own copy of the keystore under ./data/.
            if parsed_wrap.get("private_key_path") and path_is_file(
                parsed_wrap["private_key_path"]
            ):
                # Prefer node keystore when both exist; keep wrapper path as fallback.
                if not plan.private_key_path or not path_is_file(plan.private_key_path):
                    plan.private_key_path = parsed_wrap["private_key_path"]
            if parsed_wrap.get("password_path") and path_is_file(
                parsed_wrap["password_path"]
            ):
                if not plan.password_path or not path_is_file(plan.password_path):
                    plan.password_path = parsed_wrap["password_path"]

    dkg_cfg_path = _find_dkg_config(dkg) if dkg else ""
    if dkg and dkg_cfg_path and _looks_like_yaml_config(dkg_cfg_path):
        dkg.config_path = dkg_cfg_path
        dkg_bases = list(_search_roots_for_unit(dkg))
        dkg_bases.append(os.path.dirname(dkg_cfg_path))
        dkg_cfg = _read_text(dkg_cfg_path)
        if dkg_cfg:
            parsed_dkg = parse_dkg_config(dkg_cfg)
            # YAML wins when present, but do not clobber wrapper values with empties.
            if parsed_dkg.get("operator_id"):
                plan.operator_id = str(parsed_dkg["operator_id"])
            if parsed_dkg.get("dkg_port"):
                try:
                    plan.dkg_port = int(parsed_dkg["dkg_port"])
                except (TypeError, ValueError):
                    pass
            if parsed_dkg.get("eth1_http_addr"):
                plan.eth1_http_addr = str(parsed_dkg["eth1_http_addr"])
            if parsed_dkg.get("dkg_output_path"):
                plan.dkg_output_path = _resolve_path(
                    str(parsed_dkg["dkg_output_path"]),
                    workdir=dkg.working_directory,
                    unit_dir=unit_dir,
                    bases=dkg_bases,
                )
            if not plan.private_key_path and parsed_dkg.get("private_key_path"):
                plan.private_key_path = _resolve_path(
                    str(parsed_dkg["private_key_path"]),
                    workdir=dkg.working_directory,
                    unit_dir=unit_dir,
                    bases=dkg_bases + node_bases,
                )
            if not plan.password_path and parsed_dkg.get("password_path"):
                plan.password_path = _resolve_path(
                    str(parsed_dkg["password_path"]),
                    workdir=dkg.working_directory,
                    unit_dir=unit_dir,
                    bases=dkg_bases + node_bases,
                )
        else:
            plan.warnings.append(
                f"DKG config unreadable at {dkg_cfg_path}; "
                "will synthesize operator.yaml (shared keystore + ETH1 from node WS)"
            )
            dkg.config_path = ""
    elif dkg and plan.dkg_script_path:
        # CLI-flag wrapper with no operator YAML (e.g. logFilePath → debug.log only).
        dkg.config_path = f"{plan.dkg_script_path} (CLI flags)"
        plan.warnings.append(
            f"DKG settings from wrapper {plan.dkg_script_path} "
            f"(will write {SSV_DKG_CONFIG_FILE}; TLS → {SSV_DKG_SSL_DIR}/ if present)"
        )
    elif dkg:
        plan.warnings.append(
            "No separate DKG operator config found; will synthesize "
            f"{SSV_DKG_CONFIG_FILE} sharing node keystore + ETH1 HTTP derived from WS"
        )
        if dkg.config_path and not _looks_like_yaml_config(dkg.config_path):
            dkg.config_path = ""

    # Heuristic DKG output dir when not set in config.
    if dkg and not plan.dkg_output_path:
        for root in _search_roots_for_unit(dkg):
            for rel in ("data", "output", "output/data"):
                candidate = os.path.join(root, rel)
                if path_is_dir(candidate):
                    plan.dkg_output_path = candidate
                    break
            if plan.dkg_output_path:
                break

    # Common relative db layout when Path was empty or unresolved.
    if not plan.db_path or not path_is_dir(plan.db_path):
        for root in node_bases:
            for rel in ("data/db", "db", "data"):
                candidate = os.path.join(root, rel)
                if path_is_dir(candidate):
                    plan.db_path = candidate
                    break
            if plan.db_path and path_is_dir(plan.db_path):
                break

    if not plan.beacon_node_addr:
        plan.errors.append("BeaconNodeAddr missing from node config")
    if not plan.private_key_path or not path_is_file(plan.private_key_path):
        plan.errors.append("Operator encrypted_private_key.json not found")
    if not plan.password_path or not path_is_file(plan.password_path):
        plan.errors.append("Operator password file not found")
    if not plan.db_path or not path_is_dir(plan.db_path):
        msg = (
            f"DB path missing or not a directory: {plan.db_path or '(empty)'} "
            "(refusing to create an empty EthPillar db/ — pass --allow-empty-db "
            "only if you intentionally have no legacy operator DB)"
        )
        if allow_empty_db:
            plan.warnings.append(msg)
        else:
            plan.errors.append(msg)

    if path_is_file(os.path.join(unit_dir, ETHPILLAR_NODE_UNIT)) and operator_keystore_present():
        plan.warnings.append(
            "EthPillar ssv-node.service already present with keystore; "
            "run with --force to overwrite"
        )

    plan.node_binary = _plan_node_binary(
        node.binary_path,
        rebuild=rebuild_ssvnode,
        allow_older=allow_older_binary,
    )
    plan.dkg_binary = _plan_dkg_binary(
        dkg.binary_path if dkg else "",
        allow_older=allow_older_binary,
        want_dkg=dkg is not None,
    )

    missing_ws = local_ethpillar_el_missing_ws(plan.eth1_ws_addr, unit_dir=unit_dir)
    if missing_ws:
        plan.errors.append(missing_ws)

    return plan


def quarantine_unit(
    unit_path: str,
    *,
    dry_run: bool = False,
    verify: bool = True,
) -> str:
    """Retire a unit via shared ``retire_systemd_unit`` (stop/disable/bak/verify)."""
    return retire_systemd_unit(
        unit_path,
        dry_run=dry_run,
        verify=verify,
    )


def restore_quarantined_unit(
    bak_path: str,
    *,
    dry_run: bool = False,
) -> str:
    """Restore a retired unit via shared ``restore_retired_unit``."""
    return restore_retired_unit(bak_path, dry_run=dry_run, enable=True)


def _same_realpath(a: str, b: str) -> bool:
    """True when both paths name the same location.

    Uses ``normpath`` first so mode-700 parents (unreadable to the TUI user)
    still compare equal when the string paths match after normalization.
    """
    if not a or not b:
        return False
    if os.path.normpath(a) == os.path.normpath(b):
        return True
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def _copy_tree(src: str, dest: str) -> None:
    """Copy directory contents into *dest* using ``cp -a`` (no rsync required).

    Skips when *src* and *dest* are the same path (already under EthPillar).
    """
    if _same_realpath(src, dest):
        print(f"Skipping copy (source is already {dest})")
        subprocess.run(
            ["sudo", "chown", "-R", f"{SSV_USER}:{SSV_USER}", dest],
            check=True,
        )
        return
    subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
    # cp -a src/. dest/ copies contents including hidden files.
    subprocess.run(
        ["sudo", "cp", "-a", f"{src.rstrip('/')}/.", f"{dest.rstrip('/')}/"],
        check=True,
    )
    subprocess.run(["sudo", "chown", "-R", f"{SSV_USER}:{SSV_USER}", dest], check=True)


def _install_node_binary(plan: SsvMigratePlan, *, dry_run: bool) -> str:
    assert plan.node_binary is not None
    bp = plan.node_binary
    version = bp.latest_version or get_release_info(
        "LATEST", get_machine_architecture() == "amd64"
    )["version"]
    if dry_run:
        return version
    if bp.action == "copy":
        install_binary_file(bp.source_path, SSV_NODE_BIN)
        return bp.installed_version or version
    build_ssvnode_from_source(version, SSV_NODE_BIN)
    return version


def _install_dkg_binary(plan: SsvMigratePlan, *, dry_run: bool) -> None:
    if plan.dkg_binary is None:
        return
    bp = plan.dkg_binary
    if dry_run:
        return
    if bp.action == "copy":
        install_binary_file(bp.source_path, SSV_DKG_BIN)
        return
    arch_amd64 = get_machine_architecture() == "amd64"
    dkg_info = get_dkg_release_info("LATEST", arch_amd64)
    download_path = download_release_file(dkg_info, DOWNLOAD_DIR, "ssv-dkg")
    extract_and_install(
        download_path,
        "ssv-dkg",
        SSV_DKG_BIN,
        "binary",
        0,
        binary_name="ssv-dkg",
    )


def _stop_unit_keep_file(unit_path: str) -> None:
    """Stop a unit without retiring the file (used before consistent DB copy)."""
    stem = os.path.basename(unit_path)
    if stem.endswith(".service"):
        stem = stem[: -len(".service")]
    subprocess.run(["sudo", "systemctl", "stop", stem], check=False)


def _start_unit_by_path(unit_path: str) -> None:
    """Best-effort start for rollback-on-failure of a still-present unit file."""
    stem = os.path.basename(unit_path)
    if stem.endswith(".service"):
        stem = stem[: -len(".service")]
    subprocess.run(["sudo", "systemctl", "start", stem], check=False)


def _ssv_unit_after(plan: SsvMigratePlan) -> tuple:
    """Return (node_after, dkg_after) lists — omit local Wants when remote BN/EL.

    Empty lists mean “no local consensus/execution Wants” (pass through to
    ``generate_ssv_*_service(unit_after=[])``). Do not return ``None`` — that
    would restore the generator defaults.
    """
    local = endpoints_look_local(plan.beacon_node_addr, plan.eth1_ws_addr)
    if local:
        return (
            ["consensus.service", "execution.service"],
            ["execution.service"],
        )
    return [], []


def _retire_same_name_legacy_before_write(
    unit: Optional[DiscoveredUnit],
    *,
    ethpillar_name: str,
    role: str,
) -> bool:
    """Retire a same-named legacy unit before writing the EthPillar unit.

    Returns True when the path was retired (caller should write into the vacancy).
    Never quarantines a file that already looks EthPillar-owned.
    """
    if not unit:
        return False
    if os.path.basename(unit.unit_path) != ethpillar_name:
        return False
    if not path_is_file(unit.unit_path):
        return False
    text = _read_text(unit.unit_path)
    if _looks_like_ethpillar_ssv_unit(text, role=role):
        print(
            f"Skipping quarantine of {unit.unit_name}: already EthPillar-owned"
        )
        return False
    bak = quarantine_unit(unit.unit_path, dry_run=False, verify=True)
    print(f"Retired same-named legacy {unit.unit_name} → {bak} (before write)")
    return True


def _should_quarantine_legacy_unit(
    unit: Optional[DiscoveredUnit],
    *,
    role: str,
) -> bool:
    """True when *unit* still exists on disk and is not EthPillar-owned."""
    if not unit or not path_is_file(unit.unit_path):
        return False
    return not _looks_like_ethpillar_ssv_unit(_read_text(unit.unit_path), role=role)


def repair_missing_ethpillar_dkg(
    *,
    unit_dir: str = SYSTEMD_UNIT_DIR,
    dry_run: bool = False,
) -> bool:
    """Write EthPillar ``ssv-dkg.service`` when node+keystore exist but DKG unit is missing.

    Returns True when a repair was performed (or would be, in dry-run).
    """
    if ethpillar_dkg_unit_present(unit_dir):
        return False
    if not ethpillar_ssv_already_installed(unit_dir):
        return False
    if not path_is_file(SSV_DKG_BIN):
        print(
            f"Cannot repair missing {ETHPILLAR_DKG_UNIT}: {SSV_DKG_BIN} not found"
        )
        return False
    eth_network = "mainnet"
    if path_is_file(SSV_CONFIG_FILE):
        parsed = parse_node_config(_read_text(SSV_CONFIG_FILE))
        eth_network = str(parsed.get("eth_network") or eth_network)
    if not path_is_file(SSV_DKG_CONFIG_FILE):
        # Synthesize a minimal operator.yaml from node ETH1 WS → HTTP.
        eth1_ws = DEFAULT_EL_WS
        if path_is_file(SSV_CONFIG_FILE):
            eth1_ws = str(
                parse_node_config(_read_text(SSV_CONFIG_FILE)).get("eth1_ws_addr")
                or eth1_ws
            )
        body = generate_ssv_dkg_config(eth_network, _eth1_ws_to_http(eth1_ws))
        if dry_run:
            print(f"[dry-run] Would write {SSV_DKG_CONFIG_FILE}")
        else:
            ensure_ssv_datadir()
            _sudo_write_text(SSV_DKG_CONFIG_FILE, body, "600")
            print(f"Wrote {SSV_DKG_CONFIG_FILE}")
    unit_body = generate_ssv_dkg_service(eth_network)
    dest = os.path.join(unit_dir, ETHPILLAR_DKG_UNIT)
    if dry_run:
        print(f"[dry-run] Would write {dest} and enable ssv-dkg")
        return True
    # write_service_file always targets /etc/systemd/system; for tests with a
    # custom unit_dir, write directly when dest differs.
    if os.path.abspath(dest) == os.path.abspath(SSV_DKG_SERVICE_PATH):
        write_service_file(unit_body, SSV_DKG_SERVICE_PATH, "ssv_dkg_repair_temp.service")
    else:
        with open(dest, "w", encoding="utf-8") as handle:
            handle.write(unit_body)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
    subprocess.run(["sudo", "systemctl", "enable", "ssv-dkg"], check=False)
    print(f"Repaired missing EthPillar unit → {dest}")
    return True


def run_ssv_migration(
    *,
    dry_run: bool = False,
    force: bool = False,
    start: bool = False,
    rebuild_ssvnode: bool = False,
    allow_older_binary: bool = False,
    allow_empty_db: bool = False,
    unit_dir: str = SYSTEMD_UNIT_DIR,
) -> SsvMigratePlan:
    """Execute (or dry-run) the SSV systemd migration.

    Order (safety):
    1. Install EthPillar binaries (legacy may still run).
    2. Stop legacy units (files kept) so DB/keystore copy is consistent.
    3. Copy secrets/DB + write EthPillar configs.
    4. Retire same-named legacy units *before* writing EthPillar unit files.
    5. Write EthPillar units; quarantine remaining non-EthPillar legacy units.
    6. Only then enable/start EthPillar units.
    """
    plan = plan_ssv_migration(
        unit_dir=unit_dir,
        rebuild_ssvnode=rebuild_ssvnode,
        allow_older_binary=allow_older_binary,
        allow_empty_db=allow_empty_db,
    )
    # Already on EthPillar with no legacy unit → short-circuit (repair DKG if needed).
    if (
        not plan.node_unit
        and not plan.errors
        and any("Already migrated" in w for w in plan.warnings)
    ):
        print(plan.summary())
        repaired = repair_missing_ethpillar_dkg(unit_dir=unit_dir, dry_run=dry_run)
        if repaired:
            print("\nAlready migrated; repaired missing EthPillar ssv-dkg.service.")
        else:
            print("\nAlready migrated to EthPillar; no changes made.")
        return plan
    if plan.errors:
        raise ValueError("; ".join(plan.errors))

    ethpillar_unit = os.path.join(unit_dir, ETHPILLAR_NODE_UNIT)
    if (
        not force
        and path_is_file(ethpillar_unit)
        and operator_keystore_present()
        and plan.node_unit
        and os.path.basename(plan.node_unit.unit_path) != ETHPILLAR_NODE_UNIT
    ):
        raise RuntimeError(
            "EthPillar SSV already installed with keystore; pass --force to overwrite"
        )

    print(plan.summary())
    print("")

    eth_network = plan.eth_network or "mainnet"
    legacy_cfg = ""
    if plan.node_unit and plan.node_unit.config_path:
        legacy_cfg = _read_text(plan.node_unit.config_path)
    if legacy_cfg.strip():
        config_body = rewrite_legacy_node_config_for_ethpillar(legacy_cfg)
    else:
        config_body = generate_ssv_config(
            eth_network,
            plan.beacon_node_addr,
            plan.eth1_ws_addr,
            p2p_tcp_port=plan.p2p_tcp_port,
            p2p_udp_port=plan.p2p_udp_port,
            metrics_port=plan.metrics_port,
            api_port=plan.api_port,
            p2p_external_ip=plan.p2p_external_ip,
        )
        config_body = re.sub(
            r"(?m)^(\s*Network:\s*).*$",
            rf"\g<1>{eth_network}",
            config_body,
            count=1,
        )

    tls_cert_dest = ""
    tls_key_dest = ""
    if plan.tls_cert_path and plan.tls_key_path:
        tls_cert_dest = SSV_DKG_TLS_CERT
        tls_key_dest = SSV_DKG_TLS_KEY

    dkg_body = generate_ssv_dkg_config(
        eth_network,
        plan.eth1_http_addr,
        operator_id=plan.operator_id,
        dkg_port=plan.dkg_port,
        tls_cert_path=tls_cert_dest,
        tls_key_path=tls_key_dest,
    )
    node_after, dkg_after = _ssv_unit_after(plan)

    if dry_run:
        if plan.node_unit:
            print(
                f"[dry-run] Would stop then retire {plan.node_unit.unit_name} → "
                f"{plan.node_unit.unit_path}.bak (verify absent)"
            )
        if plan.dkg_unit:
            print(
                f"[dry-run] Would stop then retire {plan.dkg_unit.unit_name} → "
                f"{plan.dkg_unit.unit_path}.bak (verify absent)"
            )
        print(
            f"[dry-run] ssvnode action={plan.node_binary.action if plan.node_binary else '?'} "
            f"→ {SSV_NODE_BIN}"
        )
        if plan.dkg_unit:
            print(
                f"[dry-run] ssv-dkg action={plan.dkg_binary.action if plan.dkg_binary else '?'} "
                f"→ {SSV_DKG_BIN}"
            )
        print(f"[dry-run] Would write {SSV_CONFIG_FILE}")
        if plan.dkg_unit:
            print(f"[dry-run] Would write {SSV_DKG_CONFIG_FILE}")
        print(f"[dry-run] Would import keystore from {plan.private_key_path}")
        if plan.db_path and path_is_dir(plan.db_path):
            if _same_realpath(plan.db_path, SSV_DB_DIR):
                print(f"[dry-run] DB already at {SSV_DB_DIR} (skip copy)")
            else:
                print(f"[dry-run] Would copy DB {plan.db_path} → {SSV_DB_DIR}")
        if plan.dkg_output_path and path_is_dir(plan.dkg_output_path):
            print(
                f"[dry-run] Would copy DKG output {plan.dkg_output_path} → {SSV_DKG_OUTPUT_DIR}"
            )
        if tls_cert_dest:
            print(f"[dry-run] Would copy TLS cert {plan.tls_cert_path} → {tls_cert_dest}")
            print(f"[dry-run] Would copy TLS key  {plan.tls_key_path} → {tls_key_dest}")
        if plan.node_unit and os.path.basename(plan.node_unit.unit_path) == ETHPILLAR_NODE_UNIT:
            print(
                f"[dry-run] Would retire same-named legacy {plan.node_unit.unit_name} "
                "before writing EthPillar unit"
            )
        print(f"[dry-run] Would write {SSV_NODE_SERVICE_PATH}")
        if plan.dkg_unit:
            if os.path.basename(plan.dkg_unit.unit_path) == ETHPILLAR_DKG_UNIT:
                print(
                    f"[dry-run] Would retire same-named legacy {plan.dkg_unit.unit_name} "
                    "before writing EthPillar unit"
                )
            print(f"[dry-run] Would write {SSV_DKG_SERVICE_PATH}")
        return plan

    ensure_ssv_datadir()

    version = _install_node_binary(plan, dry_run=False)
    print(
        f"ssvnode action={plan.node_binary.action if plan.node_binary else '?'} "
        f"→ {SSV_NODE_BIN} ({version})"
    )
    if plan.dkg_unit:
        _install_dkg_binary(plan, dry_run=False)
        print(
            f"ssv-dkg action={plan.dkg_binary.action if plan.dkg_binary else '?'} "
            f"→ {SSV_DKG_BIN}"
        )

    # Consistent cutover: stop legacy before copying the live DB/keystore.
    stopped: List[str] = []
    try:
        if plan.node_unit and path_is_file(plan.node_unit.unit_path):
            print(f"Stopping {plan.node_unit.unit_name} for consistent data copy…")
            _stop_unit_keep_file(plan.node_unit.unit_path)
            stopped.append(plan.node_unit.unit_path)
        if plan.dkg_unit and path_is_file(plan.dkg_unit.unit_path):
            print(f"Stopping {plan.dkg_unit.unit_name} for consistent data copy…")
            _stop_unit_keep_file(plan.dkg_unit.unit_path)
            stopped.append(plan.dkg_unit.unit_path)

        if not _same_realpath(plan.private_key_path, SSV_PRIVATE_KEY_FILE):
            import_operator_keystore_files(plan.private_key_path, plan.password_path)
        else:
            print(f"Keystore already at {SSV_PRIVATE_KEY_FILE}")
            subprocess.run(
                [
                    "sudo",
                    "chown",
                    f"{SSV_USER}:{SSV_USER}",
                    SSV_PRIVATE_KEY_FILE,
                    SSV_PASSWORD_FILE,
                ],
                check=False,
            )

        if plan.db_path and path_is_dir(plan.db_path):
            print(f"Copying DB {plan.db_path} → {SSV_DB_DIR}")
            _copy_tree(plan.db_path, SSV_DB_DIR)
        if plan.dkg_output_path and path_is_dir(plan.dkg_output_path):
            print(f"Copying DKG output {plan.dkg_output_path} → {SSV_DKG_OUTPUT_DIR}")
            _copy_tree(plan.dkg_output_path, SSV_DKG_OUTPUT_DIR)
        if plan.tls_cert_path and plan.tls_key_path:
            subprocess.run(["sudo", "mkdir", "-p", SSV_DKG_SSL_DIR], check=True)
            if not _same_realpath(plan.tls_cert_path, SSV_DKG_TLS_CERT):
                subprocess.run(
                    ["sudo", "cp", "-f", plan.tls_cert_path, SSV_DKG_TLS_CERT],
                    check=True,
                )
            if not _same_realpath(plan.tls_key_path, SSV_DKG_TLS_KEY):
                subprocess.run(
                    ["sudo", "cp", "-f", plan.tls_key_path, SSV_DKG_TLS_KEY],
                    check=True,
                )
            subprocess.run(
                ["sudo", "chown", "-R", f"{SSV_USER}:{SSV_USER}", SSV_DKG_SSL_DIR],
                check=True,
            )
            subprocess.run(["sudo", "chmod", "700", SSV_DKG_SSL_DIR], check=True)
            subprocess.run(
                ["sudo", "chmod", "600", SSV_DKG_TLS_CERT, SSV_DKG_TLS_KEY],
                check=True,
            )

        _sudo_write_text(SSV_CONFIG_FILE, config_body, "600")
        node_unit_body = generate_ssv_node_service(
            eth_network, unit_after=node_after
        )
        # Same-named legacy must be retired *before* writing EthPillar units,
        # otherwise write overwrites the legacy body and quarantine bakes the new one.
        _retire_same_name_legacy_before_write(
            plan.node_unit, ethpillar_name=ETHPILLAR_NODE_UNIT, role="node"
        )
        write_service_file(
            node_unit_body, SSV_NODE_SERVICE_PATH, "ssv_node_migrate_temp.service"
        )

        if plan.dkg_unit:
            _sudo_write_text(SSV_DKG_CONFIG_FILE, dkg_body, "600")
            dkg_unit_body = generate_ssv_dkg_service(
                eth_network, unit_after=dkg_after
            )
            _retire_same_name_legacy_before_write(
                plan.dkg_unit, ethpillar_name=ETHPILLAR_DKG_UNIT, role="dkg"
            )
            write_service_file(
                dkg_unit_body, SSV_DKG_SERVICE_PATH, "ssv_dkg_migrate_temp.service"
            )

        # Quarantine remaining differently-named legacy units. Never quarantine
        # a file that already looks EthPillar-owned (just written above).
        if _should_quarantine_legacy_unit(plan.node_unit, role="node"):
            bak = quarantine_unit(plan.node_unit.unit_path, dry_run=False, verify=True)
            print(f"Retired {plan.node_unit.unit_name} → {bak}")
        if plan.dkg_unit and _should_quarantine_legacy_unit(plan.dkg_unit, role="dkg"):
            if plan.node_unit and _same_realpath(
                plan.dkg_unit.unit_path, plan.node_unit.unit_path
            ):
                pass
            else:
                bak = quarantine_unit(
                    plan.dkg_unit.unit_path, dry_run=False, verify=True
                )
                print(f"Retired {plan.dkg_unit.unit_name} → {bak}")
    except Exception:
        # Restart any legacy units we stopped but did not retire.
        for path in stopped:
            if path_is_file(path):
                print(f"Migrate failed — restarting {os.path.basename(path)}")
                _start_unit_by_path(path)
        raise

    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "ssv-node"], check=False)
    if plan.dkg_unit:
        subprocess.run(["sudo", "systemctl", "enable", "ssv-dkg"], check=False)

    if start:
        subprocess.run(["sudo", "systemctl", "start", "ssv-node"], check=True)
        if plan.dkg_unit:
            op_id = (plan.operator_id or "").strip()
            if not op_id or op_id.upper() == "YOUR_OPERATOR_ID":
                print(
                    "Skipping ssv-dkg start: operatorID unset "
                    f"(edit {SSV_DKG_CONFIG_FILE} then start manually)"
                )
            else:
                subprocess.run(["sudo", "systemctl", "start", "ssv-dkg"], check=True)
        print("Started EthPillar ssv-node" + (" + ssv-dkg" if plan.dkg_unit else ""))
    else:
        print(
            "EthPillar units enabled but not started. When ready:\n"
            "  sudo systemctl start ssv-node"
            + ("\n  sudo systemctl start ssv-dkg" if plan.dkg_unit else "")
        )

    print(
        "\nNext steps:\n"
        "  - EthPillar → Security → UFW → SSV ports if needed\n"
        "  - Verify BN/EL endpoints in /var/lib/ssv/config.yaml\n"
        "  - Rollback: PYTHONPATH=. python3 -m deploy.ssv_migrate rollback\n"
        "    (retires EthPillar ssv-* to *.bak, restores legacy *.service.bak)"
    )
    return plan


def _bak_role_from_name(bak_name: str) -> str:
    """Guess node vs dkg role from a ``*.service.bak`` basename."""
    lower = bak_name.lower()
    if "dkg" in lower:
        return "dkg"
    return "node"


def _is_legacy_service_bak(bak_path: str, *, skip_realpaths: Sequence[str]) -> bool:
    """True when *bak_path* should be restored as a legacy unit.

    Skips baks created in this rollback (EthPillar bodies just retired) and any
    bak whose content already looks EthPillar-owned. Same-named legacy
    ``ssv-dkg.service.bak`` / ``ssv-node.service.bak`` are restored when their
    body is non-EthPillar.
    """
    try:
        real = os.path.realpath(bak_path)
    except OSError:
        real = bak_path
    if real in skip_realpaths:
        return False
    name = os.path.basename(bak_path)
    if not _SERVICE_BAK_RE.match(name):
        return False
    role = _bak_role_from_name(name)
    text = _read_text(bak_path)
    if _looks_like_ethpillar_ssv_unit(text, role=role):
        return False
    return True


def rollback_ssv_migration(
    *,
    unit_dir: str = SYSTEMD_UNIT_DIR,
    dry_run: bool = False,
    start: bool = False,
) -> None:
    """Retire EthPillar SSV units, restore legacy ``*.service.bak``, optionally start.

    EthPillar ``ssv-node`` / ``ssv-dkg`` are retired with the shared helper
    (stop+disable+bak+verify) *before* any legacy bak is restored, so two
    operators cannot be loadable at once. Legacy same-named baks
    (``ssv-dkg.service.bak``) are restored when their body is not EthPillar.
    """
    eth_node = os.path.join(unit_dir, ETHPILLAR_NODE_UNIT)
    eth_dkg = os.path.join(unit_dir, ETHPILLAR_DKG_UNIT)
    ethpillar_baks_this_run: List[str] = []

    for path in (eth_dkg, eth_node):
        if path_is_file(path):
            if dry_run:
                print(f"[dry-run] Would retire {path} → {path}.bak")
            else:
                bak = retire_systemd_unit(path, unit_dir=unit_dir, verify=True)
                print(f"Retired EthPillar unit → {bak}")
                try:
                    ethpillar_baks_this_run.append(os.path.realpath(bak))
                except OSError:
                    ethpillar_baks_this_run.append(bak)

    # Restore legacy bak files (content-based; include same-named legacy baks).
    restored: List[str] = []
    try:
        names = sorted(os.listdir(unit_dir))
    except OSError as exc:
        raise RuntimeError(f"Cannot list {unit_dir}: {exc}") from exc

    # Prefer plain ``.service.bak`` before numbered ``.bak.N``.
    candidates = sorted(
        (n for n in names if _SERVICE_BAK_RE.match(n)),
        key=lambda n: (0 if n.endswith(".service.bak") else 1, n),
    )
    for name in candidates:
        bak_path = os.path.join(unit_dir, name)
        if not _is_legacy_service_bak(
            bak_path, skip_realpaths=ethpillar_baks_this_run
        ):
            continue
        if dry_run:
            print(f"[dry-run] Would restore {bak_path}")
            continue
        # If the live unit path already exists (shouldn't after EthPillar retire),
        # restore_retired_unit raises — surface clearly.
        path = restore_retired_unit(bak_path, enable=True)
        print(f"Restored {path}")
        restored.append(path)

    if dry_run:
        return
    if not restored:
        print(
            "No legacy *.service.bak files restored "
            "(EthPillar units were retired if present)."
        )
        return
    if start:
        for path in restored:
            _start_unit_by_path(path)
            print(f"Started {os.path.basename(path)}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry for plan / run / rollback."""
    parser = argparse.ArgumentParser(description="Migrate legacy systemd SSV to EthPillar")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Print migration plan")
    p_plan.add_argument("--json", action="store_true", help="Emit JSON")
    p_plan.add_argument("--unit-dir", default=SYSTEMD_UNIT_DIR)
    p_plan.add_argument("--rebuild-ssvnode", action="store_true")
    p_plan.add_argument("--allow-older-binary", action="store_true")
    p_plan.add_argument(
        "--allow-empty-db",
        action="store_true",
        help="Allow migrate when no legacy operator DB is found (dangerous)",
    )

    p_run = sub.add_parser("run", help="Apply migration")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--start", action="store_true", help="Start EthPillar units after migrate")
    p_run.add_argument("--unit-dir", default=SYSTEMD_UNIT_DIR)
    p_run.add_argument("--rebuild-ssvnode", action="store_true")
    p_run.add_argument("--allow-older-binary", action="store_true")
    p_run.add_argument(
        "--allow-empty-db",
        action="store_true",
        help="Allow migrate when no legacy operator DB is found (dangerous)",
    )

    p_rb = sub.add_parser(
        "rollback",
        help="Retire EthPillar ssv-* then restore legacy *.service.bak",
    )
    p_rb.add_argument("--dry-run", action="store_true")
    p_rb.add_argument("--start", action="store_true", help="Start restored legacy units")
    p_rb.add_argument("--unit-dir", default=SYSTEMD_UNIT_DIR)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "plan":
            plan = plan_ssv_migration(
                unit_dir=args.unit_dir,
                rebuild_ssvnode=args.rebuild_ssvnode,
                allow_older_binary=args.allow_older_binary,
                allow_empty_db=args.allow_empty_db,
            )
            if args.json:
                print(json.dumps(asdict(plan), indent=2))
            else:
                print(plan.summary())
            # Already-migrated is success (exit 0) even with the warning.
            if plan.errors:
                return 1
            return 0
        if args.cmd == "run":
            run_ssv_migration(
                dry_run=args.dry_run,
                force=args.force,
                start=args.start,
                rebuild_ssvnode=args.rebuild_ssvnode,
                allow_older_binary=args.allow_older_binary,
                allow_empty_db=args.allow_empty_db,
                unit_dir=args.unit_dir,
            )
            return 0
        if args.cmd == "rollback":
            rollback_ssv_migration(
                unit_dir=args.unit_dir,
                dry_run=args.dry_run,
                start=args.start,
            )
            return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
