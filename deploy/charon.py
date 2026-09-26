"""Obol Charon DVT middleware: release lookup, systemd unit, install, and CDVN migrate helpers."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

from deploy.common import (
    BASE_DATA_DIR,
    DOWNLOAD_DIR,
    INSTALL_DIR,
    download_file,
    extract_and_install,
    get_machine_architecture,
    setup_client_user_and_dir,
    write_service_file,
)
from deploy.service_generators import form_exec_start, generate_systemd_template

CHARON_USER = "charon"
CHARON_DATA_DIR = f"{BASE_DATA_DIR}/charon"
CHARON_CLUSTER_DIR = f"{CHARON_DATA_DIR}/.charon"
CHARON_LOCK_FILE = f"{CHARON_CLUSTER_DIR}/cluster-lock.json"
CHARON_PRIVATE_KEY_FILE = f"{CHARON_CLUSTER_DIR}/charon-enr-private-key"
CHARON_VALIDATOR_KEYS_DIR = f"{CHARON_CLUSTER_DIR}/validator_keys"
CHARON_SERVICE_PATH = "/etc/systemd/system/charon.service"
VC_RUN_USER = "validator"


def path_exists(path: str, *, directory: bool = False) -> bool:
    """Return True when *path* exists, including root-owned Charon datadir paths."""
    if directory:
        if os.path.isdir(path):
            return True
        flag = "-d"
    else:
        if os.path.isfile(path):
            return True
        flag = "-f"
    return subprocess.run(["sudo", "test", flag, path], check=False).returncode == 0


def list_dir_basenames(path: str) -> List[str]:
    """List directory entries, using sudo when the path is not readable as the current user."""
    try:
        return os.listdir(path)
    except OSError:
        result = subprocess.run(
            ["sudo", "find", path, "-mindepth", "1", "-maxdepth", "1", "-printf", "%f\n"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line for line in (result.stdout or "").splitlines() if line]


def count_charon_keystores(keys_dir: str) -> int:
    """Return the number of ``keystore-*.json`` files under *keys_dir*."""
    return len(_list_charon_keystores(keys_dir))


def _stage_charon_keys_for_vc(keys_dir: str) -> str:
    """Stage Charon key shares where the VC service user can read them.

    Charon cluster data under ``/var/lib/charon/.charon`` is ``charon:charon`` mode
    ``700``, so VCs cannot traverse it directly for import. Uses a unique
    ``mkdtemp`` directory (mode ``700``) rather than a predictable ``/tmp`` path.
    """
    src = os.path.abspath(keys_dir)
    staging = tempfile.mkdtemp(prefix="ethpillar-charon-key-import-")
    os.chmod(staging, 0o700)
    subprocess.run(["sudo", "cp", "-a", f"{src}/.", f"{staging}/"], check=True)
    subprocess.run(["sudo", "chown", "-R", f"{VC_RUN_USER}:{VC_RUN_USER}", staging], check=True)
    subprocess.run(["sudo", "chmod", "-R", "700", staging], check=True)
    return staging


# EthPillar VC paths keyed by deploy client name (CDVN vc-* → same names).
VC_COPY_KEY_DIRS: Dict[str, str] = {
    "Teku": f"{BASE_DATA_DIR}/teku_validator/validator_keys",
    "Grandine": f"{BASE_DATA_DIR}/grandine/validator_keys",
}
VC_IMPORT_DATA_DIRS: Dict[str, str] = {
    "Lighthouse": f"{BASE_DATA_DIR}/lighthouse_validator",
    "Lodestar": f"{BASE_DATA_DIR}/lodestar_validator",
    "Nimbus": f"{BASE_DATA_DIR}/nimbus_validator",
    "Prysm": f"{BASE_DATA_DIR}/prysm_validator",
}
# Backwards-compatible alias used in tests.
VC_KEYSTORE_DIRS = VC_COPY_KEY_DIRS

DEFAULT_VALIDATOR_API_ADDRESS = "127.0.0.1:3600"
DEFAULT_MONITORING_ADDRESS = "127.0.0.1:3620"
DEFAULT_P2P_TCP_ADDRESS = "0.0.0.0:3610"
DEFAULT_P2P_TCP_PORT = 3610
DEFAULT_VALIDATOR_API_URL = f"http://{DEFAULT_VALIDATOR_API_ADDRESS}"

_P2P_TCP_ADDRESS_RE = re.compile(r"--p2p-tcp-address=(?P<bind>[^\s\\]+)")
_MONITORING_ADDRESS_RE = re.compile(r"--monitoring-address=(?P<bind>[^\s\\]+)")


def parse_p2p_tcp_port(
    service_content: str = "",
    *,
    service_path: str = CHARON_SERVICE_PATH,
    default: int = DEFAULT_P2P_TCP_PORT,
) -> int:
    """Return Charon libp2p TCP port from a unit file or ExecStart text.

    Args:
        service_content: Optional unit body; when empty, reads ``service_path``.
        service_path: Systemd unit to read when ``service_content`` is empty.
        default: Port when the flag is missing or invalid.

    Returns:
        TCP port from ``--p2p-tcp-address=host:port`` (1–65535), else *default*.
    """
    content = service_content
    if not content:
        try:
            with open(service_path, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            return default
    match = _P2P_TCP_ADDRESS_RE.search(content)
    if not match:
        return default
    bind = match.group("bind")
    port_part = bind.rsplit(":", 1)[-1]
    try:
        port = int(port_part)
    except ValueError:
        return default
    return port if 1 <= port <= 65535 else default


def parse_monitoring_port(
    service_content: str = "",
    *,
    service_path: str = CHARON_SERVICE_PATH,
    default: int = 3620,
) -> int:
    """Return Charon metrics port from ``--monitoring-address=host:port``.

    Args:
        service_content: Optional unit body; when empty, reads ``service_path``.
        service_path: Systemd unit to read when ``service_content`` is empty.
        default: Port when the flag is missing or invalid.

    Returns:
        Metrics port (1–65535), else *default* (``3620``).
    """
    content = service_content
    if not content:
        try:
            with open(service_path, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            result = subprocess.run(
                ["sudo", "cat", service_path],
                capture_output=True,
                text=True,
                check=False,
            )
            content = result.stdout if result.returncode == 0 else ""
            if not content:
                return default
    match = _MONITORING_ADDRESS_RE.search(content)
    if not match:
        return default
    bind = match.group("bind")
    port_part = bind.rsplit(":", 1)[-1]
    try:
        port = int(port_part)
    except ValueError:
        return default
    return port if 1 <= port <= 65535 else default


_BEACON_ENDPOINTS_RE = re.compile(
    r"--beacon-node-endpoints=(?P<url>https?://[^\s\\]+(?:,https?://[^\s\\]+)*)"
)
# Matches the BN readiness ExecStartPre generated by :func:`build_beacon_wait_exec_start_pre`.
_BN_WAIT_PRE_RE = re.compile(
    r"^ExecStartPre=/bin/bash -c .+eth/v1/node/version.*$",
    re.MULTILINE,
)
_NETWORK_FROM_DESC_RE = re.compile(
    r"Obol Charon DVT middleware for (?P<net>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_ENV_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)

# Docker Compose service hostnames used in CDVN beacon / EL URLs.
_DOCKER_LOCAL_HOSTS = frozenset(
    {
        "lighthouse",
        "cl-lighthouse",
        "teku",
        "cl-teku",
        "nimbus",
        "cl-nimbus",
        "lodestar",
        "cl-lodestar",
        "prysm",
        "cl-prysm",
        "grandine",
        "cl-grandine",
        "beacon",
        "beacon-node",
        "host.docker.internal",
        "nethermind",
        "reth",
        "geth",
        "besu",
        "erigon",
        "el-nethermind",
        "el-reth",
    }
)

# CHARON_* keys we intentionally do not map into systemd ExecStart.
_SKIP_ENV_KEYS = frozenset(
    {
        "CHARON_VERSION",
        "CHARON_DOCKER_NETWORK",
        "CHARON_ALLOY_MONITORED",
        "CHARON_LOKI_ADDRESSES",
        "CHARON_LOKI_SERVICE",
    }
)


def charon_validator_api_url(
    host: str = "127.0.0.1",
    port: str = "3600",
) -> str:
    """Return the VC-facing Charon beacon API URL."""
    host = (host or "127.0.0.1").strip()
    port = (port or "3600").strip()
    return f"http://{host}:{port}"


def build_beacon_wait_exec_start_pre(beacon_node_endpoints: str) -> str:
    """Build ``ExecStartPre`` that waits until a beacon REST endpoint answers.

    Loops until ``curl`` succeeds against ``/eth/v1/node/version`` on any of the
    comma-separated ``beacon_node_endpoints``. Avoids starting Charon while its
    beacon node is still coming up (local or remote).

    Workaround for Charon startup hang when the BN is unreachable at boot:
    https://github.com/ObolNetwork/charon/issues/4689

    Args:
        beacon_node_endpoints: Comma-separated upstream beacon REST base URLs.

    Returns:
        Full ``ExecStartPre=`` value (command only, without the key).

    Raises:
        ValueError: No usable endpoint URLs were provided.
    """
    urls = [
        u.strip().rstrip("/")
        for u in (beacon_node_endpoints or "").split(",")
        if u.strip()
    ]
    if not urls:
        raise ValueError("beacon_node_endpoints empty")
    curls = " || ".join(
        f"curl -sf -m 2 {shlex.quote(f'{url}/eth/v1/node/version')}" for url in urls
    )
    script = f"until {curls}; do sleep 5; done"
    return f"/bin/bash -c {shlex.quote(script)}"


def generate_charon_service(
    eth_network: str,
    beacon_node_endpoints: str,
    *,
    builder_api: bool = False,
    p2p_external_ip: str = "",
    validator_api_address: str = DEFAULT_VALIDATOR_API_ADDRESS,
    monitoring_address: str = DEFAULT_MONITORING_ADDRESS,
    p2p_tcp_address: str = DEFAULT_P2P_TCP_ADDRESS,
    feature_set_enable: str = "",
    extra_args: Optional[Sequence[str]] = None,
) -> str:
    """Generate Charon systemd service file content.

    Args:
        eth_network: Network name (e.g. ``mainnet``).
        beacon_node_endpoints: Comma-separated upstream beacon REST URLs.
        builder_api: Enable Charon builder API (use with MEV-Boost on the BN).
        p2p_external_ip: Optional public IP advertised to cluster peers.
        validator_api_address: Listen address for the VC-facing BN API proxy.
        monitoring_address: Listen address for ``/metrics`` / ``/readyz``.
        p2p_tcp_address: Listen address for Charon libp2p (must be public).
        feature_set_enable: Optional Charon feature set (e.g. ``json_requests``
            when the upstream beacon node is Nimbus).
        extra_args: Additional ``charon run`` flags (e.g. from a CDVN ``.env``).

    Returns:
        Service file content as a string.
    """
    _args = [
        f"{INSTALL_DIR}/charon run",
        f"--beacon-node-endpoints={beacon_node_endpoints}",
        f"--validator-api-address={validator_api_address}",
        f"--monitoring-address={monitoring_address}",
        f"--p2p-tcp-address={p2p_tcp_address}",
        f"--lock-file={CHARON_LOCK_FILE}",
        f"--private-key-file={CHARON_PRIVATE_KEY_FILE}",
    ]
    if builder_api:
        _args.append("--builder-api")
    if p2p_external_ip:
        _args.append(f"--p2p-external-ip={p2p_external_ip.strip()}")
    if feature_set_enable:
        _args.append(f"--feature-set-enable={feature_set_enable.strip()}")
    if extra_args:
        _args.extend(a for a in extra_args if a)

    _exec_start = form_exec_start(_args)
    wait_pre = build_beacon_wait_exec_start_pre(beacon_node_endpoints)

    return generate_systemd_template(
        description=f"Obol Charon DVT middleware for {eth_network.upper()}",
        user=CHARON_USER,
        exec_start=_exec_start,
        extra_env=None,
        working_dir=CHARON_DATA_DIR,
        timeout_stop_sec=120,
        limit_nofile=65536,
        exec_start_pre=[wait_pre],
        # Workaround for https://github.com/ObolNetwork/charon/issues/4689 —
        # ExecStartPre may wait indefinitely while the BN finishes starting.
        timeout_start_sec="infinity",
    )


def parse_dotenv(path: str) -> Dict[str, str]:
    """Parse a CDVN-style ``.env`` file into key/value pairs.

    Commented lines and blank lines are ignored. Values may be optionally
    quoted. Inline ``#`` comments are stripped when unquoted.
    """
    result: Dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _ENV_LINE_RE.match(line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            else:
                # Drop unquoted trailing comments: VALUE # comment
                if " #" in value:
                    value = value.split(" #", 1)[0].rstrip()
            result[key] = value
    return result


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _charon_env_to_flag(key: str) -> str:
    """Map ``CHARON_FOO_BAR`` to ``--foo-bar``."""
    body = key[len("CHARON_") :] if key.startswith("CHARON_") else key
    return "--" + body.lower().replace("_", "-")


def rewrite_docker_url(url: str, local_host: str = "127.0.0.1") -> Tuple[str, Optional[str]]:
    """Rewrite CDVN Docker hostnames to a host-local address.

    Returns:
        ``(rewritten_url, warning_or_none)``
    """
    url = url.strip()
    if not url:
        return url, None
    if "${" in url:
        return url, f"Unresolved variable in URL: {url}"

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return url, f"Could not parse URL host: {url}"

    if host in _DOCKER_LOCAL_HOSTS or host.endswith(".internal"):
        port = parsed.port
        netloc = f"{local_host}:{port}" if port else local_host
        rewritten = urlunparse(parsed._replace(netloc=netloc))
        return rewritten, f"Rewrote Docker host {host!r} → {local_host} in {url}"

    return url, None


def rewrite_endpoint_list(
    value: str,
    local_host: str = "127.0.0.1",
) -> Tuple[str, List[str]]:
    """Rewrite a comma-separated URL list; collect warnings."""
    warnings: List[str] = []
    parts: List[str] = []
    for piece in value.split(","):
        piece = piece.strip()
        if not piece:
            continue
        rewritten, warn = rewrite_docker_url(piece, local_host=local_host)
        parts.append(rewritten)
        if warn:
            warnings.append(warn)
    return ",".join(parts), warnings


def _localhost_bind(address: str, default: str) -> Tuple[str, Optional[str]]:
    """Prefer loopback binds for VC API / metrics on bare-metal EthPillar."""
    address = (address or "").strip() or default
    if address.startswith("0.0.0.0:"):
        rewritten = "127.0.0.1:" + address.split(":", 1)[1]
        return rewritten, f"Bound {address} → {rewritten} (EthPillar localhost default)"
    return address, None


@dataclass
class MappingRow:
    """One paired line for side-by-side .env → systemd preview."""

    env_line: str
    systemd_line: str
    kind: str = "mapped"  # mapped | skipped | note | default


@dataclass
class CharonEnvImportPlan:
    """Result of mapping a CDVN ``.env`` onto an EthPillar Charon unit."""

    network: str
    beacon_node_endpoints: str
    builder_api: bool = False
    p2p_external_ip: str = ""
    validator_api_address: str = DEFAULT_VALIDATOR_API_ADDRESS
    monitoring_address: str = DEFAULT_MONITORING_ADDRESS
    p2p_tcp_address: str = DEFAULT_P2P_TCP_ADDRESS
    feature_set_enable: str = ""
    extra_args: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    mapped: List[str] = field(default_factory=list)
    rows: List[MappingRow] = field(default_factory=list)

    def add_row(self, env_line: str, systemd_line: str, kind: str = "mapped") -> None:
        """Append a paired preview row and keep legacy mapped/skipped lists in sync."""
        self.rows.append(MappingRow(env_line=env_line, systemd_line=systemd_line, kind=kind))
        if kind == "mapped":
            self.mapped.append(f"{env_line} → {systemd_line}")
        elif kind == "skipped":
            self.skipped.append(f"{env_line} ({systemd_line.lstrip('# ').strip()})")

    def service_content(self) -> str:
        """Render a full ``charon.service`` from this plan."""
        return generate_charon_service(
            self.network,
            self.beacon_node_endpoints,
            builder_api=self.builder_api,
            p2p_external_ip=self.p2p_external_ip,
            validator_api_address=self.validator_api_address,
            monitoring_address=self.monitoring_address,
            p2p_tcp_address=self.p2p_tcp_address,
            feature_set_enable=self.feature_set_enable,
            extra_args=self.extra_args,
        )

    def pane_texts(self) -> Tuple[str, str]:
        """Return ``(left_env_text, right_systemd_text)`` with aligned line counts."""
        left_lines = [
            "# CDVN .env (active settings)",
            "# Left pane — source values",
            "",
        ]
        right_lines = [
            "# charon.service ExecStart flags",
            "# Right pane — EthPillar systemd mapping",
            "",
        ]
        for row in self.rows:
            left_lines.append(row.env_line)
            right_lines.append(row.systemd_line)
        if self.warnings:
            left_lines.append("")
            right_lines.append("")
            left_lines.append("# warnings")
            right_lines.append("# warnings")
            for warn in self.warnings:
                left_lines.append(f"# ! {warn}")
                right_lines.append(f"# ! {warn}")
        # Keep panes equal length for tmeld alignment
        while len(left_lines) < len(right_lines):
            left_lines.append("")
        while len(right_lines) < len(left_lines):
            right_lines.append("")
        return "\n".join(left_lines) + "\n", "\n".join(right_lines) + "\n"

    def summary(self) -> str:
        """Human-readable preview (fallback when tmeld is unavailable)."""
        left, right = self.pane_texts()
        left_rows = left.splitlines()
        right_rows = right.splitlines()
        width = max((len(line) for line in left_rows), default=40)
        width = min(max(width, 24), 56)
        lines = [
            f"{'CDVN .env':<{width}} | systemd",
            f"{'-' * width}-+-{'-' * 40}",
        ]
        for l, r in zip(left_rows, right_rows):
            lines.append(f"{l:<{width}} | {r}")
        return "\n".join(lines)


def write_import_preview_panes(
    plan: CharonEnvImportPlan,
    workdir: str,
) -> Tuple[str, str]:
    """Write side-by-side preview files; return ``(left_path, right_path)``."""
    os.makedirs(workdir, exist_ok=True)
    left_path = os.path.join(workdir, "01_cdvn.env")
    right_path = os.path.join(workdir, "02_systemd.flags")
    left_text, right_text = plan.pane_texts()
    with open(left_path, "w", encoding="utf-8") as handle:
        handle.write(left_text)
    with open(right_path, "w", encoding="utf-8") as handle:
        handle.write(right_text)
    return left_path, right_path


def launch_import_preview_tmeld(workdir: str) -> int:
    """Open tmeld on the import preview panes (left=.env, right=systemd)."""
    from manage.config_compare import find_tmeld

    left = os.path.join(workdir, "01_cdvn.env")
    right = os.path.join(workdir, "02_systemd.flags")
    if not (os.path.isfile(left) and os.path.isfile(right)):
        raise FileNotFoundError(f"Preview panes missing in {workdir}")

    tmeld = find_tmeld()
    if not tmeld:
        raise RuntimeError(
            "tmeld is not installed. Run EthPillar once to bootstrap deps, "
            "or: pip install tmeld"
        )
    cmd = [tmeld, left, right, "--show-line-numbers"]
    print(f"Launching: {' '.join(cmd)}")
    print("Left = CDVN .env settings | Right = systemd flag mapping")
    print("Esc / Ctrl+Q to quit when done reviewing.")
    return subprocess.call(cmd)


def plan_cdvn_env_import(
    env: Dict[str, str],
    *,
    fallback_network: str = "mainnet",
    local_host: str = "127.0.0.1",
    prefer_localhost_binds: bool = True,
) -> CharonEnvImportPlan:
    """Build a Charon systemd plan from parsed CDVN ``.env`` values."""
    network = (env.get("NETWORK") or fallback_network).strip() or fallback_network
    plan = CharonEnvImportPlan(network=network, beacon_node_endpoints="")

    if env.get("NETWORK"):
        plan.add_row(
            f"NETWORK={env['NETWORK'].strip()}",
            f"# unit Description network → {network.upper()}",
        )
    else:
        plan.add_row(
            f"# NETWORK unset (fallback {fallback_network})",
            f"# unit Description network → {network.upper()}",
            kind="note",
        )

    raw_bn = (env.get("CHARON_BEACON_NODE_ENDPOINTS") or "").strip()
    bn_env_line = ""
    if raw_bn:
        bn_env_line = f"CHARON_BEACON_NODE_ENDPOINTS={raw_bn}"
    else:
        cl = (env.get("CL") or "").strip()
        if cl and cl != "cl-none":
            raw_bn = f"http://{cl}:5052"
            bn_env_line = f"CL={cl}  # CHARON_BEACON_NODE_ENDPOINTS unset"
            plan.warnings.append(
                "CHARON_BEACON_NODE_ENDPOINTS unset; derived from CL="
                f"{cl} → {raw_bn}"
            )
        else:
            raw_bn = f"http://{local_host}:5052"
            bn_env_line = f"# CHARON_BEACON_NODE_ENDPOINTS unset → default {raw_bn}"
            plan.warnings.append(
                "CHARON_BEACON_NODE_ENDPOINTS unset; defaulting to "
                f"{raw_bn}"
            )

    bn, bn_warns = rewrite_endpoint_list(raw_bn, local_host=local_host)
    plan.beacon_node_endpoints = bn
    plan.warnings.extend(bn_warns)
    plan.add_row(bn_env_line, f"--beacon-node-endpoints={bn}")

    builder_key = "CHARON_BUILDER_API" if env.get("CHARON_BUILDER_API") else "BUILDER_API_ENABLED"
    builder_raw = (env.get("CHARON_BUILDER_API") or env.get("BUILDER_API_ENABLED") or "").strip()
    if builder_raw:
        plan.builder_api = _truthy(builder_raw)
        systemd_builder = "--builder-api" if plan.builder_api else "# (builder-api omitted)"
        plan.add_row(f"{builder_key}={builder_raw}", systemd_builder)

    p2p_port = (env.get("CHARON_PORT_P2P_TCP") or "").strip()
    p2p_tcp = (env.get("CHARON_P2P_TCP_ADDRESS") or "").strip()
    if p2p_tcp:
        plan.p2p_tcp_address = p2p_tcp
        plan.add_row(f"CHARON_P2P_TCP_ADDRESS={p2p_tcp}", f"--p2p-tcp-address={p2p_tcp}")
    elif p2p_port:
        plan.p2p_tcp_address = f"0.0.0.0:{p2p_port}"
        plan.add_row(
            f"CHARON_PORT_P2P_TCP={p2p_port}",
            f"--p2p-tcp-address={plan.p2p_tcp_address}",
        )
    else:
        plan.add_row(
            "# CHARON_PORT_P2P_TCP unset",
            f"--p2p-tcp-address={plan.p2p_tcp_address}",
            kind="default",
        )

    raw_validator = (env.get("CHARON_VALIDATOR_API_ADDRESS") or "").strip()
    raw_monitoring = (env.get("CHARON_MONITORING_ADDRESS") or "").strip()
    validator_api = raw_validator or DEFAULT_VALIDATOR_API_ADDRESS
    monitoring = raw_monitoring or DEFAULT_MONITORING_ADDRESS
    if prefer_localhost_binds:
        validator_api, v_warn = _localhost_bind(validator_api, DEFAULT_VALIDATOR_API_ADDRESS)
        monitoring, m_warn = _localhost_bind(monitoring, DEFAULT_MONITORING_ADDRESS)
        if v_warn:
            plan.warnings.append(v_warn)
        if m_warn:
            plan.warnings.append(m_warn)
    plan.validator_api_address = validator_api
    plan.monitoring_address = monitoring
    if raw_validator:
        plan.add_row(
            f"CHARON_VALIDATOR_API_ADDRESS={raw_validator}",
            f"--validator-api-address={validator_api}",
        )
    else:
        plan.add_row(
            "# CHARON_VALIDATOR_API_ADDRESS unset",
            f"--validator-api-address={validator_api}",
            kind="default",
        )
    if raw_monitoring:
        plan.add_row(
            f"CHARON_MONITORING_ADDRESS={raw_monitoring}",
            f"--monitoring-address={monitoring}",
        )
    else:
        plan.add_row(
            "# CHARON_MONITORING_ADDRESS unset",
            f"--monitoring-address={monitoring}",
            kind="default",
        )

    ext_ip = (env.get("CHARON_P2P_EXTERNAL_IP") or "").strip()
    if ext_ip:
        plan.p2p_external_ip = ext_ip
        plan.add_row(
            f"CHARON_P2P_EXTERNAL_IP={ext_ip}",
            f"--p2p-external-ip={ext_ip}",
        )

    feature = (env.get("CHARON_FEATURE_SET_ENABLE") or "").strip()
    if feature:
        plan.feature_set_enable = feature
        plan.add_row(
            f"CHARON_FEATURE_SET_ENABLE={feature}",
            f"--feature-set-enable={feature}",
        )

    handled = {
        "CHARON_BEACON_NODE_ENDPOINTS",
        "CHARON_BUILDER_API",
        "CHARON_P2P_TCP_ADDRESS",
        "CHARON_PORT_P2P_TCP",
        "CHARON_VALIDATOR_API_ADDRESS",
        "CHARON_MONITORING_ADDRESS",
        "CHARON_P2P_EXTERNAL_IP",
        "CHARON_FEATURE_SET_ENABLE",
    }
    extra_flag_keys = (
        "CHARON_P2P_RELAYS",
        "CHARON_P2P_EXTERNAL_HOSTNAME",
        "CHARON_LOG_LEVEL",
        "CHARON_LOG_FORMAT",
        "CHARON_FALLBACK_BEACON_NODE_ENDPOINTS",
        "CHARON_BEACON_NODE_TIMEOUT",
        "CHARON_BEACON_NODE_SUBMIT_TIMEOUT",
        "CHARON_BEACON_NODE_HEADERS",
        "CHARON_NICKNAME",
        "CHARON_EXECUTION_CLIENT_RPC_ENDPOINT",
    )
    for key in extra_flag_keys:
        value = (env.get(key) or "").strip()
        if not value:
            continue
        display_value = value
        if key in (
            "CHARON_FALLBACK_BEACON_NODE_ENDPOINTS",
            "CHARON_EXECUTION_CLIENT_RPC_ENDPOINT",
        ):
            value, warns = rewrite_endpoint_list(value, local_host=local_host)
            plan.warnings.extend(warns)
        flag = _charon_env_to_flag(key)
        arg = f"{flag}={value}"
        plan.extra_args.append(arg)
        plan.add_row(f"{key}={display_value}", arg)
        handled.add(key)

    for key, value in sorted(env.items()):
        if not key.startswith("CHARON_"):
            continue
        if key in handled:
            continue
        if not value.strip():
            continue
        if key in _SKIP_ENV_KEYS:
            plan.add_row(
                f"{key}={value}",
                "# skipped: not applicable to EthPillar systemd",
                kind="skipped",
            )
            continue
        plan.add_row(
            f"{key}={value}",
            "# skipped: unmapped — add manually if needed",
            kind="skipped",
        )

    plan.add_row("# --- EthPillar always sets ---", "# --- EthPillar always sets ---", kind="note")
    plan.add_row("# (lock + private key paths)", f"--lock-file={CHARON_LOCK_FILE}", kind="default")
    plan.add_row("#", f"--private-key-file={CHARON_PRIVATE_KEY_FILE}", kind="default")

    if not plan.beacon_node_endpoints:
        raise ValueError("No beacon node endpoints resolved from .env")

    return plan


def import_cdvn_env_to_service(
    env_path: str,
    service_path: str = CHARON_SERVICE_PATH,
    *,
    prefer_localhost_binds: bool = True,
    local_host: str = "127.0.0.1",
    apply: bool = False,
    preserve_beacon_endpoints: bool = False,
) -> CharonEnvImportPlan:
    """Parse a CDVN ``.env`` and optionally write ``charon.service``.

    Args:
        env_path: Path to the CDVN ``.env`` file.
        service_path: Target systemd unit path.
        prefer_localhost_binds: Remap ``0.0.0.0`` VC/metrics binds to loopback.
        local_host: Host used when rewriting Docker Compose DNS names.
        apply: When True, write the unit (and ``daemon-reload`` for /etc paths).
        preserve_beacon_endpoints: Keep ``--beacon-node-endpoints`` already on
            *service_path* (EthPillar local CL REST URL) instead of rewriting
            from CDVN ``CHARON_BEACON_NODE_ENDPOINTS`` / ``CL=:5052`` defaults.

    Returns:
        The import plan (always), whether or not ``apply`` was set.
    """
    if not os.path.isfile(env_path):
        raise FileNotFoundError(f"CDVN .env not found: {env_path}")

    fallback_network = "mainnet"
    existing = ""
    if os.path.isfile(service_path):
        with open(service_path, encoding="utf-8") as handle:
            existing = handle.read()
        match = _NETWORK_FROM_DESC_RE.search(existing)
        if match:
            fallback_network = match.group("net").lower()

    env = parse_dotenv(env_path)
    plan = plan_cdvn_env_import(
        env,
        fallback_network=fallback_network,
        local_host=local_host,
        prefer_localhost_binds=prefer_localhost_binds,
    )
    if preserve_beacon_endpoints and existing:
        current_bn = scrape_beacon_endpoints(existing)
        if current_bn:
            plan.beacon_node_endpoints = current_bn
            plan.warnings.append(
                f"Preserved existing --beacon-node-endpoints={current_bn} "
                "(local EthPillar CL REST URL)"
            )

    if apply:
        content = plan.service_content()
        if service_path.startswith("/etc/"):
            write_service_file(content, service_path, temp_filename="charon_temp.service")
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
        else:
            with open(service_path, "w", encoding="utf-8") as handle:
                handle.write(content)

    return plan


def copy_charon_cluster(
    src_charon_dir: str,
    dest_charon_dir: str = CHARON_CLUSTER_DIR,
    *,
    force: bool = False,
) -> Dict[str, str]:
    """Copy a CDVN/DKG ``.charon`` tree into EthPillar's Charon datadir.

    Returns:
        Dict with ``src``, ``dest``, and ``status`` (``copied`` / ``skipped``).
    """
    import shutil

    src = os.path.realpath(os.path.abspath(os.path.expanduser(src_charon_dir)))
    dest = os.path.abspath(dest_charon_dir)
    if not os.path.isdir(src):
        raise FileNotFoundError(f".charon directory not found: {src}")
    lock_src = os.path.join(src, "cluster-lock.json")
    if not os.path.isfile(lock_src):
        raise ValueError(f"Missing cluster-lock.json in {src}")

    dest_lock = os.path.join(dest, "cluster-lock.json")
    if os.path.isfile(dest_lock) and not force:
        return {
            "src": src,
            "dest": dest,
            "status": "skipped",
            "reason": f"Destination already has cluster-lock.json ({dest_lock}); pass force=True to overwrite",
        }

    try:
        os.makedirs(dest, mode=0o700, exist_ok=True)
        # Copy tree contents into dest
        for name in os.listdir(src):
            s_item = os.path.join(src, name)
            d_item = os.path.join(dest, name)
            if os.path.isdir(s_item):
                if os.path.isdir(d_item):
                    shutil.rmtree(d_item)
                shutil.copytree(s_item, d_item)
            else:
                shutil.copy2(s_item, d_item)
        os.chmod(dest, 0o700)
    except PermissionError:
        subprocess.run(["sudo", "mkdir", "-p", dest], check=True)
        subprocess.run(["sudo", "cp", "-a", f"{src}/.", f"{dest}/"], check=True)
        subprocess.run(
            ["sudo", "chown", "-R", f"{CHARON_USER}:{CHARON_USER}", CHARON_DATA_DIR],
            check=True,
        )
        subprocess.run(["sudo", "chmod", "700", CHARON_DATA_DIR], check=True)
        subprocess.run(["sudo", "chmod", "700", dest], check=True)
    else:
        # Best-effort ownership when running as root/install path
        if dest.startswith(BASE_DATA_DIR) or dest.startswith("/var/lib/"):
            subprocess.run(
                ["sudo", "chown", "-R", f"{CHARON_USER}:{CHARON_USER}", CHARON_DATA_DIR],
                check=False,
            )

    return {"src": src, "dest": dest, "status": "copied"}


def resolve_charon_cluster_dir(checkout_root: str) -> Dict[str, object]:
    """Locate CDVN ``.charon`` (symlink or directory) and follow to the real cluster tree.

    Args:
        checkout_root: CDVN checkout directory containing ``.charon``.

    Returns:
        Dict with ``link`` (checkout-relative path), ``resolved`` (realpath),
        ``is_symlink``, ``has_lock``, and ``has_keyshares``.
    """
    root = os.path.abspath(os.path.expanduser(checkout_root))
    link = os.path.join(root, ".charon")
    if not os.path.lexists(link):
        return {
            "link": None,
            "resolved": None,
            "is_symlink": False,
            "has_lock": False,
            "has_keyshares": False,
        }
    resolved = os.path.realpath(link)
    if not os.path.isdir(resolved):
        return {
            "link": link,
            "resolved": None,
            "is_symlink": os.path.islink(link),
            "has_lock": False,
            "has_keyshares": False,
        }
    keys = os.path.join(resolved, "validator_keys")
    lock = os.path.join(resolved, "cluster-lock.json")
    has_keyshares = path_exists(keys, directory=True) and bool(_list_charon_keystores(keys))
    return {
        "link": link,
        "resolved": resolved,
        "is_symlink": os.path.islink(link),
        "has_lock": path_exists(lock),
        "has_keyshares": has_keyshares,
    }


def charon_cluster_copy_only(checkout_root: str, resolved_charon_dir: str) -> bool:
    """Return True when ``.charon`` must be copied, not moved, into EthPillar.

    Copy is required when ``.charon`` is a symlink or resolves outside the CDVN
    checkout (shared cluster dir on the host).

    Args:
        checkout_root: CDVN checkout root passed to migrate.
        resolved_charon_dir: Real path from :func:`resolve_charon_cluster_dir`.

    Returns:
        True when migrate should call :func:`copy_charon_cluster` only.
    """
    link = os.path.join(os.path.abspath(checkout_root), ".charon")
    if os.path.islink(link):
        return True
    try:
        return os.path.commonpath(
            [os.path.abspath(resolved_charon_dir), os.path.abspath(checkout_root)]
        ) != os.path.abspath(checkout_root)
    except ValueError:
        return True


def sync_charon_keyshares_to_vc(
    vc_name: str,
    *,
    keys_dir: str = CHARON_VALIDATOR_KEYS_DIR,
    force: bool = False,
) -> Dict[str, object]:
    """Install Charon DKG key shares into the EthPillar VC layout.

    Teku/Grandine: copy ``keystore-*`` files into the VC keystore directory.
    Lighthouse/Lodestar/Prysm: run the client import CLI (non-interactive
    when ``keystore-*.txt`` passphrase files are present).
    Nimbus: write ``validators/<pubkey>/keystore.json`` and ``secrets/<pubkey>``
    (``deposits import`` is interactive-only).

    Args:
        vc_name: EthPillar validator client name (e.g. ``Teku``, ``Lodestar``).
        keys_dir: Source directory (default EthPillar ``.charon/validator_keys``).
        force: Re-import even when the destination already has keys.

    Returns:
        Result dict with ``status`` (``copied`` / ``skipped`` / ``failed`` /
        ``unsupported``), optional ``reason``, ``dest``, ``count``, and
        ``method`` (``copy`` or ``import``).
    """
    if vc_name in VC_COPY_KEY_DIRS:
        return _sync_copy_keyshares(vc_name, keys_dir=keys_dir, force=force)
    if vc_name in VC_IMPORT_DATA_DIRS:
        return _sync_import_keyshares(vc_name, keys_dir=keys_dir, force=force)
    return {"status": "unsupported", "vc": vc_name}


def _list_charon_keystores(keys_dir: str) -> List[str]:
    """Return sorted ``keystore-*.json`` basenames under *keys_dir*."""
    src = os.path.abspath(keys_dir)
    if not path_exists(src, directory=True):
        return []
    return sorted(
        name
        for name in list_dir_basenames(src)
        if name.startswith("keystore-") and name.endswith(".json")
    )


def _find_passphrase_file(keys_dir: str) -> Optional[str]:
    """Return the first ``keystore-*.txt`` path under *keys_dir*, if any."""
    if not path_exists(keys_dir, directory=True):
        return None
    for name in sorted(list_dir_basenames(keys_dir)):
        if name.startswith("keystore-") and name.endswith(".txt"):
            return os.path.join(keys_dir, name)
    return None


def _passphrase_path_for_keystore(keys_dir: str, keystore_name: str) -> str:
    """Return the ``keystore-N.txt`` path paired with ``keystore-N.json``."""
    return os.path.join(keys_dir, keystore_name.replace(".json", ".txt"))


def _has_per_keystore_passphrase_files(keys_dir: str, keystores: List[str]) -> bool:
    """Return True when every keystore has a matching ``keystore-N.txt`` file."""
    return all(path_exists(_passphrase_path_for_keystore(keys_dir, name)) for name in keystores)


def _read_passphrase(path: str) -> str:
    """Read and normalize a Charon keystore passphrase file."""
    if os.path.isfile(path) and os.access(path, os.R_OK):
        with open(path, encoding="utf-8") as handle:
            return handle.read().rstrip("\n")
    return _sudo_read_text(path).rstrip("\n")


def _passphrases_all_equal(keys_dir: str, keystores: List[str]) -> bool:
    """Return True when all paired ``keystore-N.txt`` files contain the same password."""
    passphrases = {
        _read_passphrase(_passphrase_path_for_keystore(keys_dir, name)) for name in keystores
    }
    return len(passphrases) <= 1


def _stage_single_charon_keystore(import_dir: str, keystore_name: str) -> str:
    """Stage one Charon keystore ``.json`` + passphrase ``.txt`` pair for VC import CLI (single-key directory)."""
    txt_name = keystore_name.replace(".json", ".txt")
    staging = tempfile.mkdtemp(prefix="ethpillar-charon-key-import-one-")
    os.chmod(staging, 0o700)
    subprocess.run(
        [
            "sudo",
            "cp",
            "-a",
            os.path.join(import_dir, keystore_name),
            os.path.join(staging, keystore_name),
        ],
        check=True,
    )
    subprocess.run(
        [
            "sudo",
            "cp",
            "-a",
            os.path.join(import_dir, txt_name),
            os.path.join(staging, txt_name),
        ],
        check=True,
    )
    subprocess.run(["sudo", "chown", "-R", f"{VC_RUN_USER}:{VC_RUN_USER}", staging], check=True)
    subprocess.run(["sudo", "chmod", "-R", "700", staging], check=True)
    return staging


def _import_lodestar_charon_keyshares(
    data_dir: str,
    import_dir: str,
    keystores: List[str],
    *,
    keys_dir: str,
) -> None:
    """Import Charon DKG shares into Lodestar.

    Lodestar bulk import applies one ``--passphraseFile`` to every keystore in the
    directory. Charon DKG emits ``keystore-N.json`` + ``keystore-N.txt`` pairs that
    often use different passwords, so import one keystore at a time when needed
    (same approach as Obol CDVN).
    """
    lodestar_bin = f"{INSTALL_DIR}/lodestar"
    per_key_passphrases = _has_per_keystore_passphrase_files(keys_dir, keystores)
    bulk_passphrase = _find_passphrase_file(keys_dir)

    if per_key_passphrases and not _passphrases_all_equal(keys_dir, keystores):
        for name in keystores:
            txt_name = name.replace(".json", ".txt")
            single_dir = _stage_single_charon_keystore(import_dir, name)
            try:
                subprocess.run(
                    [
                        "sudo",
                        "-u",
                        VC_RUN_USER,
                        lodestar_bin,
                        "validator",
                        "import",
                        f"--dataDir={data_dir}",
                        f"--keystore={single_dir}",
                        f"--passphraseFile={os.path.join(single_dir, txt_name)}",
                    ],
                    check=True,
                )
            finally:
                subprocess.run(["sudo", "rm", "-rf", single_dir], check=False)
        return

    if not bulk_passphrase and not per_key_passphrases:
        raise FileNotFoundError(
            "Lodestar import needs keystore-*.txt passphrase files in .charon/validator_keys"
        )

    passphrase = os.path.join(
        import_dir,
        os.path.basename(bulk_passphrase or _passphrase_path_for_keystore(keys_dir, keystores[0])),
    )
    subprocess.run(
        [
            "sudo",
            "-u",
            VC_RUN_USER,
            lodestar_bin,
            "validator",
            "import",
            f"--dataDir={data_dir}",
            f"--keystore={import_dir}",
            f"--passphraseFile={passphrase}",
        ],
        check=True,
    )


def _import_lighthouse_charon_keyshares(
    data_dir: str,
    import_dir: str,
    keystores: List[str],
    *,
    keys_dir: str,
) -> None:
    """Import Charon DKG shares into Lighthouse (per-keystore passwords when needed)."""
    lighthouse_bin = f"{INSTALL_DIR}/lighthouse"
    per_key_passphrases = _has_per_keystore_passphrase_files(keys_dir, keystores)
    bulk_passphrase = _find_passphrase_file(keys_dir)

    if per_key_passphrases and not _passphrases_all_equal(keys_dir, keystores):
        for name in keystores:
            txt_name = name.replace(".json", ".txt")
            single_dir = _stage_single_charon_keystore(import_dir, name)
            try:
                subprocess.run(
                    [
                        "sudo",
                        "-u",
                        VC_RUN_USER,
                        lighthouse_bin,
                        "account",
                        "validator",
                        "import",
                        f"--datadir={data_dir}",
                        f"--directory={single_dir}",
                        "--reuse-password",
                        f"--password-file={os.path.join(single_dir, txt_name)}",
                    ],
                    check=True,
                )
            finally:
                subprocess.run(["sudo", "rm", "-rf", single_dir], check=False)
        return

    if not bulk_passphrase and not per_key_passphrases:
        raise FileNotFoundError(
            "Lighthouse import needs keystore-*.txt passphrase files in .charon/validator_keys"
        )

    passphrase = os.path.join(
        import_dir,
        os.path.basename(bulk_passphrase or _passphrase_path_for_keystore(keys_dir, keystores[0])),
    )
    subprocess.run(
        [
            "sudo",
            "-u",
            VC_RUN_USER,
            lighthouse_bin,
            "account",
            "validator",
            "import",
            f"--datadir={data_dir}",
            f"--directory={import_dir}",
            "--reuse-password",
            f"--password-file={passphrase}",
        ],
        check=True,
    )


def _prysm_wallet_exists(wallet_dir: str) -> bool:
    """Return True when a Prysm direct wallet already exists under *wallet_dir*."""
    if path_exists(os.path.join(wallet_dir, "auth-token")):
        return True
    return _dir_nonempty(wallet_dir)


def _import_prysm_charon_keyshares(
    data_dir: str,
    import_dir: str,
    keystores: List[str],
    *,
    keys_dir: str,
) -> None:
    """Import Charon DKG shares into Prysm (per-keystore passwords when needed)."""
    wallet_dir = f"{BASE_DATA_DIR}/prysm_validator/validator_keys"
    prysm_bin = f"{INSTALL_DIR}/prysm-validator"
    per_key_passphrases = _has_per_keystore_passphrase_files(keys_dir, keystores)
    bulk_passphrase = _find_passphrase_file(keys_dir)

    if per_key_passphrases and not _passphrases_all_equal(keys_dir, keystores):
        wallet_exists = _prysm_wallet_exists(wallet_dir)
        wallet_passphrase = os.path.join(
            import_dir,
            os.path.basename(_passphrase_path_for_keystore(keys_dir, keystores[0])),
        )
        for name in keystores:
            txt_name = name.replace(".json", ".txt")
            single_dir = _stage_single_charon_keystore(import_dir, name)
            account_passphrase = os.path.join(single_dir, txt_name)
            try:
                subprocess.run(
                    [
                        "sudo",
                        "-u",
                        VC_RUN_USER,
                        prysm_bin,
                        "accounts",
                        "import",
                        "--accept-terms-of-use",
                        f"--wallet-dir={wallet_dir}",
                        f"--keys-dir={single_dir}",
                        f"--account-password-file={account_passphrase}",
                        f"--wallet-password-file={wallet_passphrase if wallet_exists else account_passphrase}",
                    ],
                    check=True,
                )
                wallet_exists = True
            finally:
                subprocess.run(["sudo", "rm", "-rf", single_dir], check=False)
        return

    if not bulk_passphrase and not per_key_passphrases:
        raise FileNotFoundError(
            "Prysm import needs keystore-*.txt passphrase files in .charon/validator_keys"
        )

    passphrase = os.path.join(
        import_dir,
        os.path.basename(bulk_passphrase or _passphrase_path_for_keystore(keys_dir, keystores[0])),
    )
    subprocess.run(
        [
            "sudo",
            "-u",
            VC_RUN_USER,
            prysm_bin,
            "accounts",
            "import",
            "--accept-terms-of-use",
            f"--wallet-dir={wallet_dir}",
            f"--keys-dir={import_dir}",
            f"--account-password-file={passphrase}",
            f"--wallet-password-file={passphrase}",
        ],
        check=True,
    )


def _sudo_read_text(path: str) -> str:
    """Read a root-owned file via ``sudo cat``."""
    result = subprocess.run(
        ["sudo", "cat", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _sudo_write_text(path: str, content: str, *, mode: str = "0600") -> None:
    """Write *content* to *path* as root with secure permissions."""
    subprocess.run(
        ["sudo", "tee", path],
        input=content,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(["sudo", "chown", f"{VC_RUN_USER}:{VC_RUN_USER}", path], check=True)
    subprocess.run(["sudo", "chmod", mode, path], check=True)


def _normalize_eth_pubkey(pubkey: str) -> str:
    """Return a lower-case ``0x``-prefixed BLS pubkey string."""
    value = pubkey.strip().lower()
    if not value.startswith("0x"):
        value = f"0x{value}"
    return value


def read_cluster_lock_text(cluster_dir: str = CHARON_CLUSTER_DIR) -> Optional[str]:
    """Read ``cluster-lock.json`` from a Charon cluster directory.

    Args:
        cluster_dir: Path to ``.charon`` (default EthPillar datadir).

    Returns:
        Lock file text, or None when missing / unreadable.
    """
    lock_path = os.path.join(cluster_dir, "cluster-lock.json")
    if not path_exists(lock_path):
        return None
    try:
        if os.path.isfile(lock_path) and os.access(lock_path, os.R_OK):
            with open(lock_path, encoding="utf-8") as handle:
                return handle.read()
        return _sudo_read_text(lock_path)
    except (OSError, subprocess.CalledProcessError):
        return None


def list_distributed_validator_pubkeys(cluster_dir: str = CHARON_CLUSTER_DIR) -> List[str]:
    """Return composite DV pubkeys from ``cluster-lock.json``.

    Key shares imported into the signer VC expose *share* pubkeys via client
    CLIs; beacon APIs and explorers use ``distributed_public_key`` from the lock.

    Args:
        cluster_dir: Path to ``.charon`` (default EthPillar datadir).

    Returns:
        Normalized ``0x`` pubkeys for each ``distributed_validators`` entry.
    """
    text = read_cluster_lock_text(cluster_dir)
    if not text:
        return []
    try:
        lock = json.loads(text)
    except json.JSONDecodeError:
        return []
    pubkeys: List[str] = []
    for dv in lock.get("distributed_validators") or []:
        if not isinstance(dv, dict):
            continue
        pk = dv.get("distributed_public_key")
        if not isinstance(pk, str) or not pk.strip():
            # Older / alternate lock shapes
            deposit = dv.get("partial_deposit_data") or dv.get("deposit_data")
            if isinstance(deposit, list) and deposit:
                deposit = deposit[0]
            if isinstance(deposit, dict):
                pk = deposit.get("pubkey")
        if isinstance(pk, str) and pk.strip():
            pubkeys.append(_normalize_eth_pubkey(pk))
    return pubkeys


def lookup_validator_indices(
    pubkeys: Sequence[str],
    beacon_base: str,
    *,
    timeout: float = 20.0,
    chunk_size: int = 50,
) -> Dict[str, object]:
    """Resolve validator indices via the beacon node REST API (batch ``id=``).

    Args:
        pubkeys: Validator pubkeys (``0x``-prefixed).
        beacon_base: Beacon REST base URL (e.g. ``http://127.0.0.1:5052``).
        timeout: Per-request timeout seconds.
        chunk_size: Max ``id`` query params per request.

    Returns:
        Dict with ``indices`` (pubkey → index str), ``beacon``, ``error`` (optional),
        and ``found`` count.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    normalized = [_normalize_eth_pubkey(pk) for pk in pubkeys if pk and str(pk).strip()]
    base = (beacon_base or "").strip().rstrip("/")
    result: Dict[str, object] = {
        "beacon": base,
        "indices": {},
        "found": 0,
        "queried": len(normalized),
    }
    if not normalized:
        return result
    if not base.startswith("http"):
        result["error"] = f"invalid beacon URL: {beacon_base!r}"
        return result

    indices: Dict[str, str] = {}
    last_err = ""
    for offset in range(0, len(normalized), max(1, chunk_size)):
        chunk = normalized[offset : offset + max(1, chunk_size)]
        query = urllib.parse.urlencode([("id", pk) for pk in chunk])
        url = f"{base}/eth/v1/beacon/states/head/validators?{query}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            last_err = f"HTTP {exc.code} from {base}: {(body or exc.reason)[:200]}"
            # Fall back to per-pubkey path for this chunk (some BNs dislike large id lists)
            for pk in chunk:
                single = f"{base}/eth/v1/beacon/states/head/validators/{urllib.parse.quote(pk, safe='')}"
                try:
                    with urllib.request.urlopen(
                        urllib.request.Request(single, headers={"Accept": "application/json"}),
                        timeout=timeout,
                    ) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                except Exception as single_exc:  # noqa: BLE001
                    last_err = f"{single_exc}"
                    continue
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict) and data.get("index") is not None:
                    indices[pk] = str(data["index"])
            continue
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            result["error"] = last_err
            result["indices"] = indices
            result["found"] = len(indices)
            return result

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            last_err = f"non-JSON response from {base}"
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            last_err = f"unexpected validators payload from {base}"
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            validator = item.get("validator") if isinstance(item.get("validator"), dict) else {}
            pk = validator.get("pubkey") if isinstance(validator, dict) else None
            if idx is None or not isinstance(pk, str):
                continue
            indices[_normalize_eth_pubkey(pk)] = str(idx)

    result["indices"] = indices
    result["found"] = len(indices)
    if last_err and not indices:
        result["error"] = last_err
    elif last_err and indices:
        result["warning"] = last_err
    return result


def _cmd_lookup_indices(args: argparse.Namespace) -> int:
    """CLI: resolve indices for pubkeys (one per stdin line or --pubkey)."""
    pubkeys: List[str] = list(args.pubkey or [])
    if not pubkeys and not sys.stdin.isatty():
        pubkeys.extend(line.strip() for line in sys.stdin if line.strip())
    result = lookup_validator_indices(pubkeys, args.beacon)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        indices = result.get("indices") or {}
        if not isinstance(indices, dict):
            indices = {}
        for pk in pubkeys:
            norm = _normalize_eth_pubkey(pk)
            idx = indices.get(norm, "")
            if idx:
                print(idx)
            else:
                print("")
        err = result.get("error")
        if err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        if result.get("found", 0) == 0 and pubkeys:
            print(
                f"WARNING: no indices for {len(pubkeys)} pubkey(s) via {result.get('beacon')}",
                file=sys.stderr,
            )
    return 0


def _keystore_pubkey(keystore_path: str) -> str:
    """Extract the validator pubkey from an EIP-2335 keystore JSON file."""
    payload = json.loads(_sudo_read_text(keystore_path))
    pubkey = payload.get("pubkey")
    if not isinstance(pubkey, str) or not pubkey.strip():
        raise ValueError(f"missing pubkey in {keystore_path}")
    return _normalize_eth_pubkey(pubkey)


def _import_nimbus_keyshares_layout(data_dir: str, import_dir: str, keystores: List[str]) -> None:
    """Install Charon keystores into Nimbus ``validators/`` + ``secrets/`` layout.

    ``nimbus_beacon_node deposits import`` is interactive-only (no password-file
    flag). Nimbus VC reads the same on-disk layout that import would produce, so
    we write ``validators/<pubkey>/keystore.json`` and ``secrets/<pubkey>`` directly.
    """
    validators_root = os.path.join(data_dir, "validators")
    secrets_root = os.path.join(data_dir, "secrets")
    subprocess.run(["sudo", "mkdir", "-p", validators_root, secrets_root], check=True)
    for name in keystores:
        keystore_path = os.path.join(import_dir, name)
        passphrase_path = os.path.join(import_dir, name.replace(".json", ".txt"))
        if not path_exists(passphrase_path):
            raise FileNotFoundError(f"missing passphrase file for {name}: {passphrase_path}")
        pubkey = _keystore_pubkey(keystore_path)
        validator_dir = os.path.join(validators_root, pubkey)
        secret_path = os.path.join(secrets_root, pubkey)
        subprocess.run(["sudo", "mkdir", "-p", validator_dir], check=True)
        subprocess.run(
            ["sudo", "cp", "-a", keystore_path, os.path.join(validator_dir, "keystore.json")],
            check=True,
        )
        _sudo_write_text(secret_path, _sudo_read_text(passphrase_path).rstrip("\n"))
    subprocess.run(["sudo", "chmod", "700", validators_root, secrets_root], check=False)


def _dir_has_keystore_json(path: str) -> bool:
    """Return True when *path* contains at least one ``keystore-*.json`` file."""
    if not path_exists(path, directory=True):
        return False
    return any(
        name.startswith("keystore-") and name.endswith(".json")
        for name in list_dir_basenames(path)
    )


def _lighthouse_has_imported_keys(base: str) -> bool:
    """True when Lighthouse has imported keystores (not merely an empty datadir)."""
    for sub in ("validators", "accounts"):
        root = os.path.join(base, sub)
        if _dir_has_keystore_json(root):
            return True
        for name in list_dir_basenames(root):
            child = os.path.join(root, name)
            if name.endswith("keystore.json") or name.endswith("voting-keystore.json"):
                return True
            if path_exists(os.path.join(child, "voting-keystore.json")):
                return True
            if path_exists(os.path.join(child, "keystore.json")):
                return True
    return False


def _nimbus_has_imported_keys(base: str) -> bool:
    """True when Nimbus has imported validator key material (not slashing-only)."""
    validators = os.path.join(base, "validators")
    for name in list_dir_basenames(validators):
        if name.startswith("keystore-") and name.endswith(".json"):
            return True
        child = os.path.join(validators, name)
        if path_exists(os.path.join(child, "keystore.json")):
            return True
    return False


def _vc_already_has_keys(vc_name: str) -> bool:
    """Return True when the EthPillar VC datadir already holds imported keystores.

    Slashing / state dirs (Lodestar ``validator-db``, Nimbus ``secrets`` alone,
    an empty Lighthouse datadir) do **not** count — those are created by a CDVN
    datadir merge and must not skip ``.charon`` key-share import.

    Args:
        vc_name: EthPillar validator client name.

    Returns:
        True when copy/import can be skipped (unless ``force=True`` on sync).
    """
    if vc_name in VC_COPY_KEY_DIRS:
        return _dir_has_keystore_json(VC_COPY_KEY_DIRS[vc_name])
    if vc_name == "Lighthouse":
        return _lighthouse_has_imported_keys(VC_IMPORT_DATA_DIRS["Lighthouse"])
    if vc_name == "Lodestar":
        base = VC_IMPORT_DATA_DIRS["Lodestar"]
        return _dir_has_keystore_json(os.path.join(base, "keystores"))
    if vc_name == "Nimbus":
        return _nimbus_has_imported_keys(VC_IMPORT_DATA_DIRS["Nimbus"])
    if vc_name == "Prysm":
        wallet = os.path.join(VC_IMPORT_DATA_DIRS["Prysm"], "validator_keys")
        if not path_exists(wallet, directory=True):
            return False
        return any(
            name.endswith(".json") and not name.startswith("direct")
            for name in list_dir_basenames(wallet)
        )
    return False


def _dir_nonempty(path: str) -> bool:
    """Return True when *path* is a directory with at least one entry."""
    if not os.path.isdir(path):
        return False
    try:
        return any(os.scandir(path))
    except OSError:
        return False


def _chown_vc_tree(vc_name: str, path: str) -> None:
    """Best-effort ``chown``/``chmod`` on a VC datadir tree after migrate."""
    owner = "consensus" if vc_name == "Grandine" else "validator"
    subprocess.run(["sudo", "chown", "-R", f"{owner}:{owner}", path], check=False)
    subprocess.run(["sudo", "chmod", "-R", "700", path], check=False)


def _sync_copy_keyshares(
    vc_name: str,
    *,
    keys_dir: str,
    force: bool,
) -> Dict[str, object]:
    """Copy Charon key shares into a Teku/Grandine keystore directory.

    Args:
        vc_name: Must be a key in :data:`VC_COPY_KEY_DIRS`.
        keys_dir: Source ``.charon/validator_keys`` directory.
        force: Overwrite when destination already has keystores.

    Returns:
        Sync result dict (see :func:`sync_charon_keyshares_to_vc`).
    """
    dest_dir = VC_COPY_KEY_DIRS[vc_name]
    src = os.path.abspath(keys_dir)
    if not path_exists(src, directory=True):
        return {"status": "skipped", "reason": f"no validator_keys dir at {src}"}
    keystores = _list_charon_keystores(src)
    if not keystores:
        return {"status": "skipped", "reason": "no keystore-*.json files"}

    if not force and _vc_already_has_keys(vc_name):
        return {
            "status": "skipped",
            "reason": f"destination already has keystores ({dest_dir})",
            "dest": dest_dir,
            "count": len(keystores),
        }

    subprocess.run(["sudo", "mkdir", "-p", dest_dir], check=True)
    for name in keystores:
        txt = name.replace(".json", ".txt")
        subprocess.run(
            ["sudo", "cp", "-a", os.path.join(src, name), os.path.join(dest_dir, name)],
            check=True,
        )
        if path_exists(os.path.join(src, txt)):
            subprocess.run(
                ["sudo", "cp", "-a", os.path.join(src, txt), os.path.join(dest_dir, txt)],
                check=True,
            )
    _chown_vc_tree(vc_name, os.path.dirname(dest_dir))

    return {
        "status": "copied",
        "vc": vc_name,
        "src": src,
        "dest": dest_dir,
        "count": len(keystores),
        "method": "copy",
    }


def _sync_import_keyshares(
    vc_name: str,
    *,
    keys_dir: str,
    force: bool,
) -> Dict[str, object]:
    """Import Charon key shares via the VC client CLI.

    Args:
        vc_name: Lighthouse, Lodestar, Nimbus, or Prysm.
        keys_dir: Source ``.charon/validator_keys`` directory.
        force: Re-import when destination already has keys.

    Returns:
        Sync result dict (see :func:`sync_charon_keyshares_to_vc`).
    """
    src = os.path.abspath(keys_dir)
    if not path_exists(src, directory=True):
        return {"status": "skipped", "reason": f"no validator_keys dir at {src}"}
    keystores = _list_charon_keystores(src)
    if not keystores:
        return {"status": "skipped", "reason": "no keystore-*.json files"}

    data_dir = VC_IMPORT_DATA_DIRS[vc_name]
    if not force and _vc_already_has_keys(vc_name):
        return {
            "status": "skipped",
            "reason": f"destination already has validator keys ({data_dir})",
            "dest": data_dir,
            "count": len(keystores),
        }

    passphrase_src = _find_passphrase_file(src)
    import_dir = _stage_charon_keys_for_vc(src)
    subprocess.run(["sudo", "mkdir", "-p", data_dir], check=True)
    passphrase = (
        os.path.join(import_dir, os.path.basename(passphrase_src))
        if passphrase_src
        else None
    )
    try:
        if vc_name == "Lighthouse":
            if not passphrase and not _has_per_keystore_passphrase_files(src, keystores):
                return {
                    "status": "skipped",
                    "reason": "Lighthouse import needs keystore-*.txt passphrase files in .charon/validator_keys",
                }
            _import_lighthouse_charon_keyshares(data_dir, import_dir, keystores, keys_dir=src)
        elif vc_name == "Lodestar":
            if not passphrase and not _has_per_keystore_passphrase_files(src, keystores):
                return {
                    "status": "skipped",
                    "reason": "Lodestar import needs keystore-*.txt passphrase files in .charon/validator_keys",
                }
            _import_lodestar_charon_keyshares(data_dir, import_dir, keystores, keys_dir=src)
        elif vc_name == "Nimbus":
            if not passphrase:
                return {
                    "status": "skipped",
                    "reason": "Nimbus import needs keystore-*.txt passphrase files in .charon/validator_keys",
                }
            _import_nimbus_keyshares_layout(data_dir, import_dir, keystores)
        elif vc_name == "Prysm":
            if not passphrase and not _has_per_keystore_passphrase_files(src, keystores):
                return {
                    "status": "skipped",
                    "reason": "Prysm import needs keystore-*.txt passphrase files in .charon/validator_keys",
                }
            _import_prysm_charon_keyshares(data_dir, import_dir, keystores, keys_dir=src)
        else:
            return {"status": "unsupported", "vc": vc_name}
    except subprocess.CalledProcessError as exc:
        return {
            "status": "failed",
            "vc": vc_name,
            "reason": f"{vc_name} key import failed (exit {exc.returncode})",
            "dest": data_dir,
        }
    finally:
        subprocess.run(["sudo", "rm", "-rf", import_dir], check=False)

    _chown_vc_tree(vc_name, data_dir)
    return {
        "status": "copied",
        "vc": vc_name,
        "src": src,
        "dest": data_dir,
        "count": len(keystores),
        "method": "import",
    }


def resolve_cdvn_checkout(path: str) -> Dict[str, object]:
    """Resolve a CDVN checkout path (directory or ``.env`` file) to migrate assets.

    Args:
        path: CDVN checkout directory or path to a ``.env`` file inside one.

    Returns:
        Dict with ``root``, ``env_path``, ``charon_dir`` (resolved realpath),
        ``charon_link``, ``charon_is_symlink``, ``has_lock``, ``has_keyshares``,
        and ``compose_file``.
    """
    raw = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.exists(raw):
        raise FileNotFoundError(f"CDVN path not found: {raw}")

    if os.path.isfile(raw):
        root = os.path.dirname(raw)
        env_path: Optional[str] = raw if os.path.basename(raw) == ".env" or raw.endswith(".env") else None
        if env_path is None and os.path.isfile(os.path.join(root, ".env")):
            env_path = os.path.join(root, ".env")
    else:
        root = raw
        env_candidate = os.path.join(root, ".env")
        env_path = env_candidate if os.path.isfile(env_candidate) else None

    charon_info = resolve_charon_cluster_dir(root)
    charon_dir_opt = charon_info.get("resolved")
    has_lock = bool(charon_info.get("has_lock"))
    has_keyshares = bool(charon_info.get("has_keyshares"))

    compose = None
    for name in ("docker-compose.yml", "compose.yml"):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            compose = candidate
            break

    return {
        "root": root,
        "env_path": env_path,
        "charon_dir": charon_dir_opt,
        "charon_link": charon_info.get("link"),
        "charon_is_symlink": bool(charon_info.get("is_symlink")),
        "has_lock": has_lock,
        "has_keyshares": has_keyshares,
        "compose_file": compose,
    }


def _cmd_resolve_cdvn(args: argparse.Namespace) -> int:
    try:
        info = resolve_cdvn_checkout(args.path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    import json

    print(json.dumps(info, indent=2))
    return 0


def _cmd_copy_charon(args: argparse.Namespace) -> int:
    try:
        result = copy_charon_cluster(args.src, args.dest, force=args.force)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(result["status"])
    for key, value in result.items():
        if key != "status":
            print(f"{key}={value}")
    return 0 if result["status"] == "copied" else (0 if result["status"] == "skipped" else 1)


def get_release_info(version_tag: str, arch_amd64: bool) -> dict:
    """Get Charon release version, download URL, and filename.

    Args:
        version_tag: ``LATEST`` or a specific version tag.
        arch_amd64: True if the architecture is amd64/x86_64, False for arm64.

    Returns:
        A dictionary with keys ``version``, ``download_urls``, and ``filenames``.
    """
    from deploy.common import get_github_release, pick_github_release_asset, release_info_from_github

    repo = "ObolNetwork/charon"
    data = get_github_release(repo, version_tag)
    filename, download_url = pick_github_release_asset(
        data.get("assets", []),
        arch_amd64,
        name_contains=("charon",),
        name_excludes=("checksums", "cli-reference"),
        client_label="Charon",
    )
    return release_info_from_github(data, [download_url], [filename])


def install_charon(
    eth_network: str,
    beacon_node_endpoints: str,
    *,
    builder_api: bool = False,
    p2p_external_ip: str = "",
    validator_api_address: str = DEFAULT_VALIDATOR_API_ADDRESS,
    monitoring_address: str = DEFAULT_MONITORING_ADDRESS,
    p2p_tcp_address: str = DEFAULT_P2P_TCP_ADDRESS,
    feature_set_enable: str = "",
) -> Tuple[str, str]:
    """Install Charon binary, datadir, and systemd unit.

    Returns:
        ``(charon_version, service_file_path)``
    """
    setup_client_user_and_dir(CHARON_USER, "charon")
    subprocess.run(["sudo", "mkdir", "-p", CHARON_CLUSTER_DIR], check=True)
    subprocess.run(
        ["sudo", "chown", "-R", f"{CHARON_USER}:{CHARON_USER}", CHARON_DATA_DIR],
        check=True,
    )
    subprocess.run(["sudo", "chmod", "700", CHARON_DATA_DIR], check=True)
    subprocess.run(["sudo", "chmod", "700", CHARON_CLUSTER_DIR], check=True)

    arch_amd64 = get_machine_architecture() == "amd64"
    info = get_release_info("LATEST", arch_amd64)
    charon_version = info["version"]

    download_url = info["download_urls"][0]
    filename = info["filenames"][0]
    download_path = f"{DOWNLOAD_DIR}/{filename}"
    download_file(download_url, download_path, "charon")

    extract_and_install(
        download_path,
        "charon",
        os.path.join(INSTALL_DIR, "charon"),
        "binary",
        0,
        binary_name="charon",
    )

    service_content = generate_charon_service(
        eth_network,
        beacon_node_endpoints,
        builder_api=builder_api,
        p2p_external_ip=p2p_external_ip,
        validator_api_address=validator_api_address,
        monitoring_address=monitoring_address,
        p2p_tcp_address=p2p_tcp_address,
        feature_set_enable=feature_set_enable,
    )
    write_service_file(service_content, CHARON_SERVICE_PATH, "charon_temp.service")
    return charon_version, CHARON_SERVICE_PATH


def scrape_beacon_endpoints(content: str) -> Optional[str]:
    """Extract ``--beacon-node-endpoints`` from a Charon unit file."""
    match = _BEACON_ENDPOINTS_RE.search(content)
    return match.group("url") if match else None


def patch_beacon_endpoints(service_path: str, new_endpoints: str) -> bool:
    """Replace Charon ``--beacon-node-endpoints`` in a systemd unit.

    Also refreshes the BN readiness ``ExecStartPre`` curl wait (and ensures
    ``TimeoutStartSec=infinity``) so patched endpoints stay in sync.

    Returns:
        True if the file was updated.

    Raises:
        FileNotFoundError: service_path does not exist.
        ValueError: Flag missing or invalid URL.
    """
    if not os.path.isfile(service_path):
        raise FileNotFoundError(f"Charon service file not found: {service_path}")

    new_endpoints = new_endpoints.strip()
    if not re.match(r"^https?://", new_endpoints):
        raise ValueError(f"Invalid beacon endpoint URL: {new_endpoints!r}")

    with open(service_path, encoding="utf-8") as fh:
        content = fh.read()
    if not _BEACON_ENDPOINTS_RE.search(content):
        raise ValueError(f"--beacon-node-endpoints not found in {service_path}")

    new_content = _BEACON_ENDPOINTS_RE.sub(
        f"--beacon-node-endpoints={new_endpoints}",
        content,
        count=1,
    )
    wait_pre = f"ExecStartPre={build_beacon_wait_exec_start_pre(new_endpoints)}"
    if _BN_WAIT_PRE_RE.search(new_content):
        new_content = _BN_WAIT_PRE_RE.sub(wait_pre, new_content, count=1)
    else:
        # Older units without a BN wait: insert before ExecStart.
        new_content = re.sub(
            r"(?m)^(ExecStart=)",
            wait_pre + r"\n\1",
            new_content,
            count=1,
        )
    if "TimeoutStartSec=" not in new_content:
        new_content = re.sub(
            r"(?m)^(TimeoutStopSec=\d+\s*)$",
            r"\1\nTimeoutStartSec=infinity",
            new_content,
            count=1,
        )

    if new_content == content:
        return False

    if service_path.startswith("/etc/"):
        write_service_file(new_content, service_path, temp_filename="charon_temp.service")
    else:
        with open(service_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
    return True


def _cmd_patch(args: argparse.Namespace) -> int:
    try:
        updated = patch_beacon_endpoints(args.service_path, args.endpoint)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if updated:
        print(f"Patched Charon beacon endpoints in {args.service_path} → {args.endpoint}")
    else:
        print(f"No change needed in {args.service_path}")
    return 0


def _cmd_import_env(args: argparse.Namespace) -> int:
    try:
        plan = import_cdvn_env_to_service(
            args.env,
            service_path=args.service_path,
            prefer_localhost_binds=not args.keep_docker_binds,
            local_host=args.local_host,
            apply=args.apply,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    preview_dir = args.preview_dir
    if args.tmeld or preview_dir:
        preview_dir = preview_dir or tempfile.mkdtemp(prefix="ethpillar-charon-env-")
        left, right = write_import_preview_panes(plan, preview_dir)
        print(f"Preview panes:\n  left:  {left}\n  right: {right}")
        if args.tmeld:
            try:
                launch_import_preview_tmeld(preview_dir)
            except RuntimeError as exc:
                print(f"WARNING: {exc}", file=sys.stderr)
                print(plan.summary())
    else:
        print(plan.summary())

    print()
    if args.apply:
        print(f"Wrote {args.service_path}")
    else:
        print("Dry run only. Re-run with --apply to write charon.service.")
        if args.print_unit:
            print()
            print(plan.service_content())
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Charon systemd utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    patch_parser = subparsers.add_parser(
        "patch_beacon", help="Update --beacon-node-endpoints in charon.service"
    )
    patch_parser.add_argument("--endpoint", required=True, help="Upstream beacon REST URL")
    patch_parser.add_argument(
        "--service-path",
        default=CHARON_SERVICE_PATH,
        help="Path to charon.service",
    )
    patch_parser.set_defaults(func=_cmd_patch)

    import_parser = subparsers.add_parser(
        "import_env",
        help="Convert a CDVN .env into charon.service flags",
    )
    import_parser.add_argument("--env", required=True, help="Path to CDVN .env file")
    import_parser.add_argument(
        "--service-path",
        default=CHARON_SERVICE_PATH,
        help="Path to charon.service",
    )
    import_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the systemd unit (default: preview only)",
    )
    import_parser.add_argument(
        "--print-unit",
        action="store_true",
        help="Print full unit content during dry run",
    )
    import_parser.add_argument(
        "--preview-dir",
        default="",
        help="Write side-by-side preview panes into this directory",
    )
    import_parser.add_argument(
        "--tmeld",
        action="store_true",
        help="Open tmeld side-by-side preview (left=.env, right=systemd flags)",
    )
    import_parser.add_argument(
        "--local-host",
        default="127.0.0.1",
        help="Host used when rewriting Docker Compose beacon URLs",
    )
    import_parser.add_argument(
        "--keep-docker-binds",
        action="store_true",
        help="Keep 0.0.0.0 validator-api/monitoring binds from .env",
    )
    import_parser.set_defaults(func=_cmd_import_env)

    resolve_parser = subparsers.add_parser(
        "resolve_cdvn",
        help="Inspect a CDVN checkout for .env / .charon assets (JSON)",
    )
    resolve_parser.add_argument("--path", required=True, help="CDVN directory or .env path")
    resolve_parser.set_defaults(func=_cmd_resolve_cdvn)

    copy_parser = subparsers.add_parser(
        "copy_charon",
        help="Copy a .charon cluster folder into /var/lib/charon/.charon",
    )
    copy_parser.add_argument("--src", required=True, help="Source .charon directory")
    copy_parser.add_argument(
        "--dest",
        default=CHARON_CLUSTER_DIR,
        help="Destination .charon directory",
    )
    copy_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite destination even if cluster-lock.json exists",
    )
    copy_parser.set_defaults(func=_cmd_copy_charon)

    indices_parser = subparsers.add_parser(
        "lookup_indices",
        help="Resolve validator indices from a beacon node REST API",
    )
    indices_parser.add_argument(
        "--beacon",
        required=True,
        help="Beacon REST base URL (e.g. http://127.0.0.1:5052)",
    )
    indices_parser.add_argument(
        "--pubkey",
        action="append",
        default=[],
        help="Validator pubkey (repeatable); else read pubkeys from stdin",
    )
    indices_parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON result instead of one index per pubkey line",
    )
    indices_parser.set_defaults(func=_cmd_lookup_indices)

    parsed = parser.parse_args(argv)
    return parsed.func(parsed)


if __name__ == "__main__":
    sys.exit(main())
