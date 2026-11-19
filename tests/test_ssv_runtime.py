"""Unit tests for the integration ssv-node runtime/port plan."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "integration"))

from tests.integration.ssv_runtime import (  # noqa: E402
    journal_is_anchor_waiting_for_bn,
    journal_is_go_beacon_timeout,
    keygen_command,
    ssv_port_plan,
)

ANCHOR_PORTS = {"tcp": 13001, "udp": 12001, "quic": 13002, "metrics": 5164, "api": 5062, "dkg": 3030}
GO_PORTS = {"tcp": 13001, "udp": 12001, "quic": 0, "metrics": 15000, "api": 16000, "dkg": 3030}


def test_anchor_plan_requires_local_metrics_api_and_public_p2p():
    plan = ssv_port_plan("anchor", ANCHOR_PORTS)
    assert [(e.port, e.scope, e.protocols) for e in plan.required] == [
        (5164, "localhost", ("tcp",)), (5062, "localhost", ("tcp",))]
    assert [(e.port, e.scope, e.protocols) for e in plan.p2p] == [
        (13001, "public", ("tcp",)), (12001, "public", ("udp",)), (13002, "public", ("udp",))]


def test_go_plan_only_metrics_on_all_interfaces():
    plan = ssv_port_plan("ssv", GO_PORTS)
    assert [(e.port, e.scope) for e in plan.required] == [(15000, "public")]
    assert plan.p2p == ()


def test_go_beacon_timeout_is_a_soft_skip_and_health_exit_is_not():
    assert journal_is_go_beacon_timeout(
        "FATAL timed out awaiting Beacon config initialization, timeout=1m0s"
    )
    assert not journal_is_go_beacon_timeout(
        "FATAL component(s) are not healthy: probe health-check timed out"
    )


def test_anchor_p2p_soft_skip_when_beacon_spec_is_unread():
    assert journal_is_anchor_waiting_for_bn("Unable to read spec from beacon node")
    assert journal_is_anchor_waiting_for_bn("Unable to connect to a beacon node")
    assert not journal_is_anchor_waiting_for_bn("Unable to create syncer: Failed to bind to WS")


def test_keygen_commands():
    a = keygen_command("anchor", "SEPOLIA")
    assert a[:4] == ["sudo", "-u", "ssv", "/usr/local/bin/anchor"]
    assert "--encrypt" in a and "sepolia" in a and "/var/lib/ssv/password" in a
    g = keygen_command("ssv", "sepolia")
    assert "cd /var/lib/ssv && /usr/local/bin/ssvnode generate-operator-keys" in g[-1]
