"""Unit tests for integration port binding helpers."""
from tests.integration.port_bindings import (
    check_port_scope,
    cl_supports_rpc_expose,
    parse_ss_listeners,
    PortBinding,
)


SS_SAMPLE = """
Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port
udp   UNCONN 0      0      0.0.0.0:30303      0.0.0.0:*
udp   UNCONN 0      0      0.0.0.0:9000       0.0.0.0:*
tcp   LISTEN 0      4096   127.0.0.1:8545     0.0.0.0:*
tcp   LISTEN 0      4096   127.0.0.1:8551     0.0.0.0:*
tcp   LISTEN 0      4096   127.0.0.1:5052     0.0.0.0:*
tcp   LISTEN 0      4096   0.0.0.0:30303      0.0.0.0:*
tcp   LISTEN 0      4096   0.0.0.0:9000       0.0.0.0:*
"""


def test_parse_ss_listeners_extracts_addresses_and_ports():
    bindings = parse_ss_listeners(SS_SAMPLE)
    assert PortBinding("tcp", "127.0.0.1", 8545) in bindings
    assert PortBinding("udp", "0.0.0.0", 30303) in bindings


def test_check_port_scope_localhost_and_public():
    bindings = parse_ss_listeners(SS_SAMPLE)
    ok, _ = check_port_scope(bindings, 8545, "localhost", label="EL RPC")
    assert ok
    ok, _ = check_port_scope(bindings, 30303, "public", protocols=("tcp", "udp"), label="EL P2P")
    assert ok


def test_cl_supports_rpc_expose_includes_grandine():
    assert cl_supports_rpc_expose("Grandine")


def test_check_port_scope_detects_public_rpc_binding():
    bindings = [
        PortBinding("tcp", "0.0.0.0", 8545),
    ]
    ok, message = check_port_scope(bindings, 8545, "localhost", label="EL RPC")
    assert not ok
    assert "expected localhost only" in message
