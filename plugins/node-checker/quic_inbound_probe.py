#!/usr/bin/env python3
"""Active inbound QUIC probe for EthPillar node-checker.

Adapted from bojanisc/quicmap (quicmap.py) by Fran Cutura and Bojan Zdrnja,
Copyright (c) 2024 INFIGO IS d.o.o. (https://www.infigo.is)
https://github.com/bojanisc/quicmap
Licensed under the Apache License, Version 2.0.

EthPillar changes: single host:port, libp2p-relevant ALPN, JSON on stdout,
no ALPN brute-force and no tqdm. aioquic is an optional dependency — it is
not part of the default EthPillar venv.

PASS (ok=true) means the target answered QUIC: server_versions in the
handshake log, a libp2p-relevant ALPN, a successful ping, or a QUIC
connection_close that is not the generic aioquic application miss (376).
That is the same class of signal eth-docker's quicmap one-liner uses
("server_versions appearing shows the forward works").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

# Dummy QUIC version that forces Version Negotiation (from quicmap).
_FORCE_VERSION_NEGOTIATION = 0x1A2B3D4A
# aioquic APPLICATION_ERROR-style miss used by quicmap as "probably not this ALPN".
_AIOQUIC_APP_MISS = 376
_DEFAULT_ALPN = "libp2p"


def classify_quic_logger_events(
    events: list[dict[str, Any]] | None,
    *,
    ping_ok: bool = False,
    offered_alpn: list[str] | None = None,
) -> dict[str, Any]:
    """Interpret aioquic QuicLogger events the way quicmap's test_alpn does.

    Returns a JSON-ready dict. ``ok`` is true when the peer spoke QUIC.
    """
    server_versions: list[Any] = []
    saw_quic_close = False
    for event in events or []:
        name = event.get("name")
        data = event.get("data") or {}
        if name == "transport:version_information" and data.get("server_versions"):
            server_versions = list(data["server_versions"])
        elif name == "transport:packet_received":
            frames = data.get("frames") or []
            if (
                frames
                and frames[0].get("frame_type") == "connection_close"
                and frames[0].get("error_code") != _AIOQUIC_APP_MISS
            ):
                saw_quic_close = True

    versions_hex = [hex(v) if isinstance(v, int) else str(v) for v in server_versions]
    alpn = list(offered_alpn or []) if (ping_ok or saw_quic_close) else []
    if ping_ok:
        reason = "ping"
    elif server_versions:
        reason = "server_versions"
    elif saw_quic_close:
        reason = "connection_close"
    else:
        reason = "no_quic_response"
    return {
        "ok": bool(ping_ok or server_versions or saw_quic_close),
        "reason": reason,
        "server_versions": versions_hex,
        "alpn": alpn,
    }


def _logger_events(configuration: Any) -> list[dict[str, Any]]:
    logger = getattr(configuration, "quic_logger", None)
    if logger is None:
        return []
    try:
        traces = logger.to_dict().get("traces") or []
        if not traces:
            return []
        return list(traces[0].get("events") or [])
    except Exception:
        return []


async def probe_host_port(
    host: str,
    port: int,
    *,
    timeout: float,
    alpn: list[str],
) -> dict[str, Any]:
    """Send one QUIC Initial (libp2p ALPN + version negotiation) to host:port."""
    try:
        from aioquic.asyncio import connect
        from aioquic.quic.configuration import QuicConfiguration
        from aioquic.quic.logger import QuicLogger
    except ImportError:
        return {
            "ok": False,
            "reason": "aioquic_missing",
            "host": host,
            "port": port,
            "server_versions": [],
            "alpn": [],
        }

    configuration = QuicConfiguration(
        alpn_protocols=alpn,
        quic_logger=QuicLogger(),
        verify_mode=False,
    )
    # Forces Version Negotiation so a CL that speaks QUIC still leaves a trace
    # even when the libp2p application handshake does not complete.
    configuration.supported_versions.insert(0, _FORCE_VERSION_NEGOTIATION)
    ping_ok = False

    async def _once() -> None:
        nonlocal ping_ok
        async with connect(host, port, configuration=configuration) as quic_connection:
            await quic_connection.ping()
            ping_ok = True

    try:
        await asyncio.wait_for(_once(), timeout=timeout)
    except Exception:
        pass

    result = classify_quic_logger_events(
        _logger_events(configuration),
        ping_ok=ping_ok,
        offered_alpn=alpn,
    )
    result["host"] = host
    result["port"] = port
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe one host:port for an inbound QUIC/libp2p handshake (quicmap-style)."
    )
    parser.add_argument("--host", required=True, help="Public IPv4 (or hostname) to probe")
    parser.add_argument("--port", required=True, type=int, help="CL QUIC UDP port")
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for a QUIC response (default: 5)",
    )
    parser.add_argument(
        "--alpn",
        default=_DEFAULT_ALPN,
        help="Comma-separated ALPNs to offer (default: libp2p)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    alpn = [item.strip() for item in args.alpn.split(",") if item.strip()]
    if not alpn:
        alpn = [_DEFAULT_ALPN]
    result = asyncio.run(
        probe_host_port(args.host, args.port, timeout=args.timeout, alpn=alpn)
    )
    json.dump(result, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    if result.get("reason") == "aioquic_missing":
        return 2
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
