"""Unit tests for the quicmap-adapted inbound QUIC classifier.

Does not import aioquic and does not send packets.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_probe():
    path = Path(__file__).resolve().parents[1] / "plugins/node-checker/quic_inbound_probe.py"
    spec = importlib.util.spec_from_file_location("quic_inbound_probe", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_server_versions_is_ok():
    probe = _load_probe()
    result = probe.classify_quic_logger_events(
        [
            {
                "name": "transport:version_information",
                "data": {"server_versions": [1, 0xFF00001D]},
            }
        ]
    )
    assert result["ok"] is True
    assert result["reason"] == "server_versions"
    assert result["server_versions"] == ["0x1", "0xff00001d"]


def test_classify_empty_trace_is_not_ok():
    probe = _load_probe()
    result = probe.classify_quic_logger_events([])
    assert result["ok"] is False
    assert result["reason"] == "no_quic_response"
    assert result["alpn"] == []


def test_classify_connection_close_without_app_miss_is_ok():
    probe = _load_probe()
    result = probe.classify_quic_logger_events(
        [
            {
                "name": "transport:packet_received",
                "data": {"frames": [{"frame_type": "connection_close", "error_code": 12}]},
            }
        ],
        offered_alpn=["libp2p"],
    )
    assert result["ok"] is True
    assert result["reason"] == "connection_close"
    assert result["alpn"] == ["libp2p"]


def test_classify_aioquic_app_miss_alone_is_not_ok():
    probe = _load_probe()
    result = probe.classify_quic_logger_events(
        [
            {
                "name": "transport:packet_received",
                "data": {"frames": [{"frame_type": "connection_close", "error_code": 376}]},
            }
        ]
    )
    assert result["ok"] is False
    assert result["reason"] == "no_quic_response"


def test_classify_ping_ok_is_ok():
    probe = _load_probe()
    result = probe.classify_quic_logger_events([], ping_ok=True, offered_alpn=["libp2p"])
    assert result["ok"] is True
    assert result["reason"] == "ping"
    assert result["alpn"] == ["libp2p"]
