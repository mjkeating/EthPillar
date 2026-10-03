"""Unit tests for integration port binding helpers."""
from tests.integration.port_bindings import (
    check_port_scope,
    cl_enables_quic_by_default,
    cl_quic_help_flag,
    cl_supports_rpc_expose,
    consensus_exec_argv_from_unit,
    decide_cl_quic_listen,
    default_port_expectations,
    expected_cl_quic_unit_flag,
    help_advertises_quic_flag,
    parse_cl_version_from_text,
    parse_ss_listeners,
    probe_cl_quic_capability,
    version_meets_quic_floor,
    PortBinding,
)


SS_SAMPLE = """
Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port
udp   UNCONN 0      0      0.0.0.0:30303      0.0.0.0:*
udp   UNCONN 0      0      0.0.0.0:9000       0.0.0.0:*
udp   UNCONN 0      0      0.0.0.0:9001       0.0.0.0:*
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
    ok, _ = check_port_scope(bindings, 9001, "public", protocols=("udp",), label="CL QUIC")
    assert ok


def test_cl_supports_rpc_expose_includes_grandine():
    assert cl_supports_rpc_expose("Grandine")


def test_cl_enables_quic_by_default_covers_deployed_beacon_clients():
    for name in ("Lighthouse", "Teku", "Nimbus", "Lodestar", "Grandine", "Prysm", "Caplin"):
        assert cl_enables_quic_by_default(name)


def test_expected_cl_quic_unit_flag_pins_ethpillar_flags():
    assert expected_cl_quic_unit_flag("Lighthouse", 9001) == "--quic-port=9001"
    assert expected_cl_quic_unit_flag("Nimbus", 9001) == "--quic-port=9001"
    assert expected_cl_quic_unit_flag("Grandine", 9001) == "--quic-port=9001"
    assert expected_cl_quic_unit_flag("Lodestar", 9001) == "--quicPort=9001"
    assert expected_cl_quic_unit_flag("Prysm", 9001) == "--p2p-quic-port=9001"
    assert expected_cl_quic_unit_flag("Caplin", 9001) == "--caplin.discovery.quicport=9001"
    assert expected_cl_quic_unit_flag("Teku", 9001) is None


def test_default_port_expectations_include_cl_quic_when_requested():
    items = default_port_expectations(has_consensus=True, expect_cl_quic=True)
    quic = [i for i in items if i.label == "CL QUIC"]
    assert len(quic) == 1
    assert quic[0].port == 9001
    assert quic[0].protocols == ("udp",)
    assert quic[0].scope == "public"

    without = default_port_expectations(has_consensus=True, expect_cl_quic=False)
    assert all(i.label != "CL QUIC" for i in without)


def test_check_port_scope_detects_public_rpc_binding():
    bindings = [
        PortBinding("tcp", "0.0.0.0", 8545),
    ]
    ok, message = check_port_scope(bindings, 8545, "localhost", label="EL RPC")
    assert not ok
    assert "expected localhost only" in message


def test_check_port_scope_accepts_ipv4_mapped_addresses():
    bindings = [
        PortBinding("tcp", "[::ffff:127.0.0.1]", 8545),
        PortBinding("tcp", "[::ffff:0.0.0.0]", 30303),
        PortBinding("udp", "[::ffff:0.0.0.0]", 30303),
        PortBinding("tcp", "172.17.0.2", 30303),
    ]
    ok, _ = check_port_scope(bindings, 8545, "localhost", label="EL RPC")
    assert ok
    ok, _ = check_port_scope(bindings, 30303, "public", protocols=("tcp", "udp"), label="EL P2P")
    assert ok


# Real Nimbus v26.7.0-4110bc --help lists many flags and no --quic-port.
_NIMBUS_26_7_HELP = """
Nimbus beacon node v26.7.0-4110bc-stateofus
Usage:
nimbus_beacon_node [OPTIONS]... command
     --help                  Show this help message and exit.
     --version               Show program's version and exit.
     --network               Consensus network to join.
     --data-dir              Directory for blockchain data.
     --tcp-port              Listening TCP port for Ethereum LibP2P traffic [=9000].
     --udp-port              Listening UDP port for node discovery [=9000].
     --max-peers             The target number of peers to connect to [=160].
     --listen-address        Listening address for LibP2P and Discovery v5.
"""

_HELP_WITH_QUIC_PORT = """
Usage: lighthouse bn [OPTIONS]
     --port <PORT>
     --quic-port <PORT>      UDP port for QUIC
     --http-port <PORT>
     --target-peers <N>
     --datadir <PATH>
     --network <NETWORK>
"""


def test_cl_quic_help_flag_matches_ethpillar_pins():
    assert cl_quic_help_flag("Lighthouse") == "--quic-port"
    assert cl_quic_help_flag("Nimbus") == "--quic-port"
    assert cl_quic_help_flag("Lodestar") == "--quicPort"
    assert cl_quic_help_flag("Prysm") == "--p2p-quic-port"
    assert cl_quic_help_flag("Teku") is None
    assert cl_quic_help_flag("Caplin") == "--caplin.discovery.quicport"


def test_help_advertises_quic_flag_true_when_present():
    assert help_advertises_quic_flag(_HELP_WITH_QUIC_PORT, "--quic-port") is True
    lodestar_help = (
        "--quicPort=9001 --rest.port=5052 --port=9000 "
        "--network=mainnet --dataDir=x --targetPeers=1"
    )
    assert help_advertises_quic_flag(lodestar_help, "--quicPort") is True


def test_help_advertises_quic_flag_false_when_omitted_from_real_help():
    assert help_advertises_quic_flag(_NIMBUS_26_7_HELP, "--quic-port") is False


def test_help_advertises_quic_flag_none_when_awkward():
    assert help_advertises_quic_flag("", "--quic-port") is None
    assert help_advertises_quic_flag("not a help page", "--quic-port") is None


def test_decide_cl_quic_listen_help_advertises_flag_expects_listen():
    result = decide_cl_quic_listen("Lighthouse", help_text=_HELP_WITH_QUIC_PORT, version="8.2.1")
    assert result.expect_listen is True
    assert result.help_advertised is True


def test_decide_cl_quic_listen_help_omits_flag_skips():
    result = decide_cl_quic_listen("Lighthouse", help_text=_NIMBUS_26_7_HELP, version="8.2.1")
    assert result.expect_listen is False
    assert result.help_advertised is False
    assert "does not advertise --quic-port" in result.reason


def test_decide_cl_quic_listen_nimbus_26_7_help_omits_flag():
    result = decide_cl_quic_listen("Nimbus", help_text=_NIMBUS_26_7_HELP, version="26.7.0")
    assert result.expect_listen is False
    assert "skip CL QUIC listen" in result.reason


def test_decide_cl_quic_listen_nimbus_below_floor_skips_even_if_help_lies():
    result = decide_cl_quic_listen("Nimbus", help_text=_HELP_WITH_QUIC_PORT, version="26.7.0")
    assert result.expect_listen is False
    assert result.help_advertised is True
    assert "below QUIC floor 26.8.0" in result.reason


def test_decide_cl_quic_listen_nimbus_meets_floor_and_help_expects_listen():
    result = decide_cl_quic_listen("Nimbus", help_text=_HELP_WITH_QUIC_PORT, version="26.8.0")
    assert result.expect_listen is True
    assert result.help_advertised is True


def test_decide_cl_quic_listen_awkward_help_uses_version_floor():
    below = decide_cl_quic_listen("Nimbus", help_text="", version="26.7.0")
    assert below.expect_listen is False
    assert "below QUIC floor" in below.reason

    above = decide_cl_quic_listen("Nimbus", help_text="", version="26.8.0-rc.1")
    assert above.expect_listen is True
    assert "meets QUIC floor" in above.reason


def test_decide_cl_quic_listen_inconclusive_keeps_candidate_expect():
    result = decide_cl_quic_listen("Lighthouse", help_text="", version="")
    assert result.expect_listen is True
    assert "inconclusive" in result.reason


def test_probe_cl_quic_capability_accepts_injected_help_and_version():
    result = probe_cl_quic_capability(
        "Nimbus",
        help_text=_NIMBUS_26_7_HELP,
        version="26.7.0",
    )
    assert result.expect_listen is False


def test_version_meets_quic_floor_nimbus():
    assert version_meets_quic_floor("Nimbus", "26.7.0") is False
    assert version_meets_quic_floor("Nimbus", "v26.8.0") is True
    assert version_meets_quic_floor("Nimbus", "26.8.0-rc.1") is True
    assert version_meets_quic_floor("Lighthouse", "8.2.1") is None


def test_parse_cl_version_from_text():
    assert parse_cl_version_from_text("Nimbus beacon node v26.7.0-4110bc-stateofus") == "v26.7.0"
    assert parse_cl_version_from_text("lighthouse 8.2.2") == "8.2.2"


def test_consensus_exec_argv_from_unit_reads_binary_and_subcommand():
    unit = (
        "[Service]\n"
        "ExecStart=/usr/local/bin/lighthouse bn \\\n"
        "    --network=hoodi \\\n"
        "    --quic-port=9001\n"
    )
    assert consensus_exec_argv_from_unit(unit) == ["/usr/local/bin/lighthouse", "bn"]

    nimbus = "[Service]\nExecStart=/usr/local/bin/nimbus_beacon_node \\\n    --quic-port=9001\n"
    assert consensus_exec_argv_from_unit(nimbus) == ["/usr/local/bin/nimbus_beacon_node"]
