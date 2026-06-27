"""Parse ``ss -lntu`` output and verify RPC/P2P port bind addresses."""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

LOCALHOST_ADDRS = frozenset({"127.0.0.1", "::1", "[::1]"})
PUBLIC_ADDRS = frozenset({"0.0.0.0", "*", "[::]", "::"})


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


def is_localhost_address(address: str) -> bool:
    """Return True when *address* is loopback-only."""
    return address in LOCALHOST_ADDRS


def is_public_address(address: str) -> bool:
    """Return True when *address* accepts connections on all interfaces."""
    if address in PUBLIC_ADDRS:
        return True
    match = re.fullmatch(r"\[(.+)\]", address)
    if match and match.group(1) in ("::", "*"):
        return True
    return False


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
        if any(is_public_address(addr) for addr in addresses):
            return False, f"{name} (:{port}) is exposed on {addresses} (expected localhost only)"
        if not any(is_localhost_address(addr) for addr in addresses):
            return False, f"{name} (:{port}) is bound to {addresses} (expected localhost only)"
        return True, ""

    if scope == "public":
        if any(is_public_address(addr) for addr in addresses):
            return True, ""
        return False, f"{name} (:{port}) is bound to {addresses} (expected all interfaces)"

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


def default_port_expectations(
    *,
    el_p2p_port: int = 30303,
    el_rpc_port: int = 8545,
    cl_p2p_port: int = 9000,
    cl_rest_port: int = 5052,
    engine_port: int = 8551,
    has_execution: bool = False,
    has_consensus: bool = False,
    has_caplin: bool = False,
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
    elif has_caplin:
        expectations.extend(
            [
                PortExpectation(cl_p2p_port, "public", ("tcp", "udp"), "Caplin P2P"),
                PortExpectation(cl_rest_port, "localhost", ("tcp",), "Caplin REST"),
            ]
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
        "cl_rest": 5052,
    }
    mapping = {
        "EL_P2P_PORT": "el_p2p",
        "EL_RPC_PORT": "el_rpc",
        "CL_P2P_PORT": "cl_p2p",
        "CL_REST_PORT": "cl_rest",
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
    return cl_name in {"Nimbus", "Lodestar", "Lighthouse", "Prysm", "Teku"}


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
