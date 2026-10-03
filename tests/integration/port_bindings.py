"""Parse ``ss -lntu`` output and verify RPC/P2P port bind addresses."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

@dataclass(frozen=True)
class PortBinding:
    """One local socket endpoint from ``ss -lntu``."""

    protocol: str
    address: str
    port: int


@dataclass(frozen=True)
class PortExpectation:
    """Expected bind scope for a port."""

    port: int
    scope: str  # "localhost" or "public"
    protocols: Tuple[str, ...] = ("tcp",)
    label: str = ""


def parse_ss_listeners(ss_output: str) -> List[PortBinding]:
    """Return parsed TCP/UDP listeners from ``ss -lntu`` text."""
    bindings: List[PortBinding] = []
    for line in ss_output.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0] not in ("tcp", "udp"):
            continue
        if parts[1] not in ("LISTEN", "UNCONN"):
            continue
        local = parts[4]
        if ":" not in local:
            continue
        addr, port_str = local.rsplit(":", 1)
        port_str = port_str.split("%", 1)[0]
        try:
            port = int(port_str)
        except ValueError:
            continue
        bindings.append(PortBinding(protocol=parts[0], address=addr, port=port))
    return bindings


def read_ss_listeners() -> List[PortBinding]:
    """Run ``ss -lntu`` and return parsed listeners."""
    result = subprocess.run(["ss", "-lntu"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return parse_ss_listeners(result.stdout)


def normalize_bind_address(address: str) -> str:
    """Normalize a ``ss`` local address for scope checks."""
    if address.startswith("[::ffff:") and address.endswith("]"):
        return address[len("[::ffff:"):-1]
    if address.startswith("[") and address.endswith("]"):
        inner = address[1:-1]
        return inner.split("%", 1)[0]
    return address.split("%", 1)[0]


def is_loopback_address(address: str) -> bool:
    """Return True when *address* is loopback-only."""
    return normalize_bind_address(address) in {"127.0.0.1", "::1", "localhost"}


def is_all_interfaces_address(address: str) -> bool:
    """Return True when *address* listens on every interface."""
    return normalize_bind_address(address) in {"0.0.0.0", "*", "::"}


def is_public_bind_address(address: str) -> bool:
    """Return True when *address* is reachable beyond loopback (P2P-style bind)."""
    if is_all_interfaces_address(address):
        return True
    return not is_loopback_address(address)


def is_localhost_bind_address(address: str) -> bool:
    """Return True when *address* is loopback-only (RPC-safe bind)."""
    return is_loopback_address(address) and not is_all_interfaces_address(address)


def listeners_for_port(
    bindings: Sequence[PortBinding],
    port: int,
    protocols: Iterable[str] = ("tcp", "udp"),
) -> List[PortBinding]:
    """Filter *bindings* to entries matching *port* and *protocols*."""
    allowed = set(protocols)
    return [b for b in bindings if b.port == port and b.protocol in allowed]


def check_port_scope(
    bindings: Sequence[PortBinding],
    port: int,
    scope: str,
    protocols: Iterable[str] = ("tcp",),
    label: str = "",
) -> Tuple[bool, str]:
    """Verify *port* is bound with the expected *scope* (``localhost`` or ``public``)."""
    name = label or str(port)
    entries = listeners_for_port(bindings, port, protocols)
    if not entries:
        return False, f"{name} (:{port}) is not listening"

    addresses = [entry.address for entry in entries]
    if scope == "localhost":
        if any(is_public_bind_address(addr) for addr in addresses):
            return False, f"{name} (:{port}) is exposed on {addresses} (expected localhost only)"
        if all(is_localhost_bind_address(addr) for addr in addresses):
            return True, ""
        return False, f"{name} (:{port}) is bound to {addresses} (expected localhost only)"

    if scope == "public":
        if any(is_public_bind_address(addr) for addr in addresses):
            return True, ""
        return False, f"{name} (:{port}) is bound to {addresses} (expected reachable, not localhost-only)"

    raise ValueError(f"Unknown scope: {scope}")


def client_from_service(service: str) -> str:
    """Read the client name from a systemd unit Description= line."""
    path = f"/etc/systemd/system/{service}.service"
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("Description="):
                    parts = line.split("=", 1)[1].strip().split()
                    return parts[0] if parts else ""
    except OSError:
        pass
    return ""


def has_caplin_execution() -> bool:
    """Return True when execution.service runs integrated Caplin."""
    path = "/etc/systemd/system/execution.service"
    try:
        with open(path, encoding="utf-8") as handle:
            return "caplin" in handle.read().lower()
    except OSError:
        return False


# Beacon clients that enable libp2p QUIC. Value is the ExecStart flag EthPillar
# pins (empty = client default only; Teku). Caplin's native QUIC default is UDP
# 4001; EthPillar pins --caplin.discovery.quicport to CL_P2P+1 (9001), matching
# eth-docker #2836.
CL_QUIC_UNIT_FLAGS: Dict[str, str] = {
    "Lighthouse": "--quic-port=",
    "Nimbus": "--quic-port=",
    "Grandine": "--quic-port=",
    "Lodestar": "--quicPort=",
    "Prysm": "--p2p-quic-port=",
    "Teku": "",
    "Caplin": "--caplin.discovery.quicport=",
}

# Version floors from comments in ethpillar.sh (UFW QUIC notes). Backup only
# when help is awkward, and a Nimbus hard floor when help is ambiguous/lying.
# Verified: Nimbus v26.7.0-4110bc ``--help`` does **not** list ``--quic-port``.
CL_QUIC_VERSION_FLOORS: Dict[str, str] = {
    "Nimbus": "26.8.0",
    "Teku": "26.7.0",
    "Lodestar": "1.42.0",
    "Prysm": "5.2.0",
    # Erigon v3.7.1 is the first release that binds Caplin QUIC.
    "Caplin": "3.7.1",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SEMVER_RE = re.compile(
    r"v?(\d+\.\d+\.\d+(?:-(?:rc|alpha|beta|dev)[0-9A-Za-z.]*)?)",
    re.IGNORECASE,
)
_HELP_FLAG_RE = re.compile(r"(?m)(?:^|[\s])--[A-Za-z][A-Za-z0-9_-]*")
_WRAPPER_BINS = {"env", "nice", "ionice", "sudo"}
_SHELL_BINS = {"bash", "sh", "dash"}


@dataclass(frozen=True)
class QuicCapability:
    """Result of a CL QUIC listen-assert capability probe."""

    expect_listen: bool
    reason: str
    help_advertised: Optional[bool] = None


def cl_enables_quic_by_default(cl_name: str) -> bool:
    """Return True when *cl_name* listens for QUIC without an opt-in flag."""
    return cl_name in CL_QUIC_UNIT_FLAGS


def expected_cl_quic_unit_flag(cl_name: str, quic_port: int) -> Optional[str]:
    """Return the consensus.service substring EthPillar should pin for QUIC.

    Teku enables QUIC by default without a pinned EthPillar flag, so this
    returns ``None``. Unknown clients also return ``None``.
    """
    prefix = CL_QUIC_UNIT_FLAGS.get(cl_name)
    if prefix is None or prefix == "":
        return None
    return f"{prefix}{quic_port}"


def cl_quic_help_flag(cl_name: str) -> Optional[str]:
    """Return the help-token EthPillar pins for *cl_name*, or ``None``.

    Teku enables QUIC without a pinned flag, so this returns ``None`` and
    callers fall back to the version floor / inconclusive path.
    """
    prefix = CL_QUIC_UNIT_FLAGS.get(cl_name)
    if prefix is None or prefix == "":
        return None
    return prefix.rstrip("=")


def strip_ansi(text: str) -> str:
    """Remove CSI color sequences (Nimbus ``--help`` is colorized)."""
    return _ANSI_RE.sub("", text or "")


def parse_cl_version_from_text(text: str) -> str:
    """Extract the first ``v?X.Y.Z`` (optional prerelease) from *text*."""
    match = _SEMVER_RE.search(text or "")
    return match.group(0) if match else ""


def parse_major_minor_patch(version: str) -> Optional[Tuple[int, int, int]]:
    """Return ``(major, minor, patch)`` from *version*, ignoring prerelease."""
    match = re.search(r"v?(\d+)\.(\d+)(?:\.(\d+))?", version or "", re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def version_meets_quic_floor(cl_name: str, version: str) -> Optional[bool]:
    """Compare *version* to the ethpillar.sh QUIC floor for *cl_name*.

    Uses base semver only (``26.8.0-rc.1`` meets ``26.8.0``). Returns
    ``None`` when the client has no floor or *version* cannot be parsed.
    """
    floor = CL_QUIC_VERSION_FLOORS.get(cl_name)
    if not floor:
        return None
    got = parse_major_minor_patch(version)
    need = parse_major_minor_patch(floor)
    if got is None or need is None:
        return None
    return got >= need


def help_advertises_quic_flag(help_text: str, flag: str) -> Optional[bool]:
    """Return whether *help_text* lists *flag*.

    ``True`` / ``False`` when the text looks like real ``--help``.
    ``None`` when help is empty, tiny, or otherwise awkward to parse.
    """
    if not flag:
        return None
    text = strip_ansi(help_text)
    if not text.strip():
        return None
    escaped = re.escape(flag)
    if re.search(rf"(?m)(?:^|[\s|/]){escaped}(?:[=:\s,|/]|$)", text):
        return True
    if len(_HELP_FLAG_RE.findall(text)) >= 5:
        return False
    return None


def consensus_exec_argv_from_unit(unit_text: str) -> List[str]:
    """Return ExecStart argv (binary + optional subcommand) from unit text."""
    first = ""
    for line in (unit_text or "").splitlines():
        if line.startswith("ExecStart="):
            payload = line[len("ExecStart=") :].rstrip()
            if payload.endswith("\\"):
                payload = payload[:-1].rstrip()
            first = payload.strip()
            break
    if not first:
        return []
    try:
        parts = shlex.split(first)
    except ValueError:
        parts = first.split()
    if not parts:
        return []
    basename = os.path.basename(parts[0])
    if basename in _WRAPPER_BINS:
        index = 1
        while index < len(parts) and "=" in parts[index]:
            index += 1
        parts = parts[index:]
        if not parts:
            return []
        basename = os.path.basename(parts[0])
    if basename in _SHELL_BINS:
        return []
    return parts


def read_consensus_exec_argv(
    unit_path: str = "/etc/systemd/system/consensus.service",
) -> List[str]:
    """Read consensus ExecStart argv from *unit_path*."""
    try:
        with open(unit_path, encoding="utf-8") as handle:
            return consensus_exec_argv_from_unit(handle.read())
    except OSError:
        return []


def run_cli_output(argv: Sequence[str], extra: Sequence[str], timeout: int = 30) -> str:
    """Run *argv* + *extra* from ``/tmp`` and return combined stdout/stderr."""
    if not argv:
        return ""
    try:
        result = subprocess.run(
            list(argv) + list(extra),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",
            check=False,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "") + (result.stderr or "")


def collect_installed_cl_probe(
    unit_path: str = "/etc/systemd/system/consensus.service",
) -> Tuple[str, str]:
    """Probe the installed consensus binary for ``--help`` and a version.

    Returns:
        ``(help_text, version)``. Either may be empty when the binary cannot
        be resolved or the probe fails (awkward → version-floor backup).
    """
    argv = read_consensus_exec_argv(unit_path)
    if not argv:
        return "", ""
    help_text = run_cli_output(argv, ["--help"])
    if not strip_ansi(help_text).strip():
        help_text = run_cli_output(argv, ["-h"])
    version_text = run_cli_output(argv, ["--version"])
    version = parse_cl_version_from_text(version_text) or parse_cl_version_from_text(help_text)
    return help_text, version


def decide_cl_quic_listen(
    cl_name: str,
    *,
    help_text: str = "",
    version: str = "",
) -> QuicCapability:
    """Decide whether to require a public UDP QUIC listen for *cl_name*.

    Primary: help advertises the EthPillar-pinned flag for this client.
    Backup: version floor from ``ethpillar.sh`` when help is awkward.
    Nimbus below v26.8.0 skips even if help advertises ``--quic-port``
    (QUIC gossip landed in v26.8.0; help-lying defense).
    """
    if not cl_enables_quic_by_default(cl_name):
        return QuicCapability(False, f"{cl_name} is not a QUIC-by-default client", None)

    flag = cl_quic_help_flag(cl_name)
    advertised = help_advertises_quic_flag(help_text, flag) if flag else None
    meets_floor = version_meets_quic_floor(cl_name, version)
    floor = CL_QUIC_VERSION_FLOORS.get(cl_name, "")

    # Nimbus v26.7.0 never binds UDP 9001. Skip even if help were lying.
    if cl_name == "Nimbus" and meets_floor is False:
        shown = version or "unknown"
        return QuicCapability(
            False,
            (
                f"INFO: skip CL QUIC listen: Nimbus {shown} is below QUIC floor "
                f"{floor} (QUIC gossip is v{floor}+)"
            ),
            advertised,
        )

    if advertised is True:
        return QuicCapability(True, f"{cl_name} help advertises {flag}", True)
    if advertised is False:
        return QuicCapability(
            False,
            f"INFO: skip CL QUIC listen: {cl_name} help does not advertise {flag}",
            False,
        )

    if meets_floor is True:
        return QuicCapability(
            True,
            (
                f"INFO: {cl_name} help awkward; version {version} meets "
                f"QUIC floor {floor}"
            ),
            None,
        )
    if meets_floor is False:
        return QuicCapability(
            False,
            (
                f"INFO: skip CL QUIC listen: {cl_name} help awkward; version "
                f"{version} is below QUIC floor {floor}"
            ),
            None,
        )

    return QuicCapability(
        True,
        f"INFO: {cl_name} QUIC capability probe inconclusive; expecting listen",
        advertised,
    )


def probe_cl_quic_capability(
    cl_name: str,
    *,
    unit_path: str = "/etc/systemd/system/consensus.service",
    help_text: Optional[str] = None,
    version: Optional[str] = None,
) -> QuicCapability:
    """Live wrapper: probe the installed binary, then :func:`decide_cl_quic_listen`.

    Pass *help_text* / *version* to skip the live subprocess (unit tests).
    """
    if help_text is None and version is None:
        help_text, version = collect_installed_cl_probe(unit_path)
    return decide_cl_quic_listen(
        cl_name,
        help_text=help_text or "",
        version=version or "",
    )


def verify_cl_quic_unit_flag(cl_name: str, quic_port: int) -> Tuple[bool, str]:
    """Check the CL unit pins the EthPillar QUIC port when required.

    Caplin's flag lives on ``execution.service``. Other clients use
    ``consensus.service``.

    Returns:
        ``(True, "")`` when no pin is required or the flag is present.
        ``(False, message)`` when a pin is required but missing.
    """
    expected = expected_cl_quic_unit_flag(cl_name, quic_port)
    if expected is None:
        return True, ""
    path = (
        "/etc/systemd/system/execution.service"
        if cl_name == "Caplin"
        else "/etc/systemd/system/consensus.service"
    )
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        return False, f"CL QUIC unit flag: cannot read {path}: {exc}"
    if expected in content:
        return True, ""
    unit_name = os.path.basename(path)
    return False, f"CL QUIC unit flag missing: expected {expected!r} in {unit_name}"


def default_port_expectations(
    *,
    el_p2p_port: int = 30303,
    el_rpc_port: int = 8545,
    cl_p2p_port: int = 9000,
    cl_p2p_port_2: int = 9001,
    cl_rest_port: int = 5052,
    engine_port: int = 8551,
    charon_p2p_port: int = 3610,
    has_execution: bool = False,
    has_consensus: bool = False,
    has_caplin: bool = False,
    has_charon: bool = False,
    expect_cl_quic: bool = False,
) -> List[PortExpectation]:
    """Build default bind expectations for a deployed node."""
    expectations: List[PortExpectation] = []
    if has_execution:
        expectations.extend(
            [
                PortExpectation(el_p2p_port, "public", ("tcp", "udp"), "EL P2P"),
                PortExpectation(el_rpc_port, "localhost", ("tcp",), "EL RPC"),
                PortExpectation(engine_port, "localhost", ("tcp",), "EL Engine"),
            ]
        )
    if has_consensus:
        expectations.extend(
            [
                PortExpectation(cl_p2p_port, "public", ("tcp", "udp"), "CL P2P"),
                PortExpectation(cl_rest_port, "localhost", ("tcp",), "CL REST"),
            ]
        )
        if expect_cl_quic:
            expectations.append(
                PortExpectation(cl_p2p_port_2, "public", ("udp",), "CL QUIC"),
            )
    elif has_caplin:
        expectations.extend(
            [
                PortExpectation(cl_p2p_port, "public", ("tcp", "udp"), "Caplin P2P"),
                PortExpectation(cl_rest_port, "localhost", ("tcp",), "Caplin REST"),
            ]
        )
        if expect_cl_quic:
            expectations.append(
                PortExpectation(cl_p2p_port_2, "public", ("udp",), "CL QUIC"),
            )
    if has_charon:
        expectations.append(
            PortExpectation(charon_p2p_port, "public", ("tcp",), "Charon P2P")
        )
    return expectations


def verify_port_expectations(
    expectations: Sequence[PortExpectation],
    *,
    attempts: int = 6,
    interval_sec: int = 5,
) -> Tuple[bool, List[str]]:
    """Poll ``ss`` until all *expectations* pass or time out."""
    errors: List[str] = []
    for attempt in range(1, attempts + 1):
        bindings = read_ss_listeners()
        errors = []
        for item in expectations:
            ok, message = check_port_scope(
                bindings,
                item.port,
                item.scope,
                item.protocols,
                item.label,
            )
            if not ok:
                errors.append(message)
        if not errors:
            return True, []
        if attempt < attempts:
            time.sleep(interval_sec)
    return False, errors


def read_env_ports(env_path: str) -> Dict[str, int]:
    """Load port numbers from an EthPillar env file."""
    defaults = {
        "el_p2p": 30303,
        "el_rpc": 8545,
        "cl_p2p": 9000,
        "cl_p2p_2": 9001,
        "cl_rest": 5052,
        "charon_p2p": 3610,
    }
    mapping = {
        "EL_P2P_PORT": "el_p2p",
        "EL_RPC_PORT": "el_rpc",
        "CL_P2P_PORT": "cl_p2p",
        "CL_P2P_PORT_2": "cl_p2p_2",
        "CL_REST_PORT": "cl_rest",
        "CHARON_P2P_PORT": "charon_p2p",
        "CHARON_PORT_P2P_TCP": "charon_p2p",
    }
    ports = dict(defaults)
    try:
        with open(env_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in mapping:
                    continue
                ports[mapping[key]] = int(value.strip().strip('"').strip("'"))
    except OSError:
        pass
    return ports


def cl_supports_rpc_expose(cl_name: str) -> bool:
    """Return True when exposeRpcCL supports this consensus client."""
    return cl_name in {"Nimbus", "Lodestar", "Lighthouse", "Grandine", "Prysm", "Teku"}


def el_supports_rpc_expose(el_name: str) -> bool:
    """Return True when exposeRpcEL supports this execution client."""
    if el_name == "Erigon-Caplin":
        el_name = "Erigon"
    return el_name in {"Nethermind", "Besu", "Erigon", "Geth", "Reth", "Ethrex"}


def wait_for_port_scope(
    port: int,
    scope: str,
    *,
    protocols: Iterable[str] = ("tcp",),
    label: str = "",
    attempts: int = 24,
    interval_sec: int = 5,
) -> Tuple[bool, str]:
    """Poll until *port* matches *scope* or time out."""
    for _ in range(attempts):
        bindings = read_ss_listeners()
        ok, message = check_port_scope(bindings, port, scope, protocols, label)
        if ok:
            return True, ""
        time.sleep(interval_sec)
    bindings = read_ss_listeners()
    _, message = check_port_scope(bindings, port, scope, protocols, label)
    return False, message
