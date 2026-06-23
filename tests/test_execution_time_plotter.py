import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "logging" / "plotExecutionTimes" / "plotProcessingTimes.py"
SYSTEM_INFO_PATH = Path(__file__).resolve().parents[1] / "logging" / "plotExecutionTimes" / "system_info.py"
SPEC = importlib.util.spec_from_file_location("plot_processing_times", MODULE_PATH)
plotter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = plotter
SPEC.loader.exec_module(plotter)

SYSTEM_INFO_SPEC = importlib.util.spec_from_file_location("plot_system_info", SYSTEM_INFO_PATH)
system_info = importlib.util.module_from_spec(SYSTEM_INFO_SPEC)
assert SYSTEM_INFO_SPEC.loader is not None
sys.modules[SYSTEM_INFO_SPEC.name] = system_info
SYSTEM_INFO_SPEC.loader.exec_module(system_info)


def test_geth_parser_normalizes_milliseconds():
    parser = plotter.ExecutionLogParser("geth")

    point = parser.parse_line("Imported new potential chain segment number=1 mgas=20.42 elapsed=125.573305ms")

    assert point.gas_used_mgas == 20.42
    assert point.elapsed_time_ms == 125.573305


def test_reth_parser_converts_seconds_to_milliseconds():
    parser = plotter.ExecutionLogParser("reth")

    point = parser.parse_line("INFO number=23493228 gas_used=8.50Mgas elapsed=1.25s")

    assert point.gas_used_mgas == 8.50
    assert point.elapsed_time_ms == 1250.0


def test_besu_parser_reads_modern_imported_line():
    parser = plotter.ExecutionLogParser("besu")
    line = (
        'Imported #25,268,932 (571ca.....32896)| 292 tx ( 62.0% parallel)| 16 ws| 3 blobs| '
        "117.07 mwei bfee| 34,833,694 ( 58.1%) gas used| 671.1ms exec| 51.91 Mgas/s| 1 peers"
    )

    point = parser.parse_line(line)

    assert point.gas_used_mgas == pytest.approx(34.833694)
    assert point.elapsed_time_ms == pytest.approx(671.1)


def test_besu_parser_reads_legacy_imported_line():
    parser = plotter.ExecutionLogParser("besu")
    line = (
        "Imported #15,788,198 / 130 tx / base fee 18.16 gwei / "
        "18,250,609 (60.8%) gas / (0x3e0340d5c9681e76d9f99fc79304d0853063a754a7cd347b464101eff0e1c5f5) "
        "in 1.507s. Peers: 17"
    )

    point = parser.parse_line(line)

    assert point.gas_used_mgas == pytest.approx(18.250609)
    assert point.elapsed_time_ms == pytest.approx(1507.0)


def test_erigon_parser_reads_head_updated_line():
    parser = plotter.ExecutionLogParser("erigon")
    line = (
        "head updated hash=0x33b4bc15574ed67e4993af30571e613474129036c0c55804dc9ad9790d646441 "
        "number=21851697 age=4s execution=932.476215ms mgas/s=16.23 average mgas/s=18.07"
    )

    point = parser.parse_line(line)

    assert point.elapsed_time_ms == pytest.approx(932.476215)
    assert point.gas_used_mgas == pytest.approx(16.23 * 0.932476215)


def test_erigon_parser_reads_metric_throughput_line():
    parser = plotter.ExecutionLogParser("erigon")
    line = "[METRIC] BLOCK EXECUTION THROUGHPUT (123): 0.642 Ggas/s TIME SPENT: 76 ms. Gas Used: 0.049 (82%), #Txs: 1023."

    point = parser.parse_line(line)

    assert point.elapsed_time_ms == 76.0
    assert point.gas_used_mgas == 49.0


def test_nethermind_parser_combines_elapsed_and_following_gas_line():
    parser = plotter.ExecutionLogParser("nethermind")

    assert parser.parse_line("Processed 123 | 345.6 ms") is None
    point = parser.parse_line("Block 123 17.42 MGas")

    assert point.gas_used_mgas == 17.42
    assert point.elapsed_time_ms == 345.6


def test_ethrex_parser_reads_metric_block_summary_line():
    parser = plotter.ExecutionLogParser("ethrex")
    metric_line = (
        "2026-06-23T01:16:49.665989Z  INFO [METRIC] BLOCK 25376969 "
        "0xa93276b044a54cb69d8dadf7ae66520e6f1637930b81cfb716493f8955f9174b "
        "| 0.052 Ggas/s | 1151.59 ms | 1067 txs | 60 Mgas (99%)"
    )

    point = parser.parse_line(metric_line)

    assert point.gas_used_mgas == 60.0
    assert point.elapsed_time_ms == 1151.59


def test_ethrex_parser_ignores_pipeline_breakdown_lines():
    parser = plotter.ExecutionLogParser("ethrex")

    assert parser.parse_line("2026-06-23T01:17:12.528276Z  INFO   |- validate:    0.50 ms  ( 1%)") is None
    assert parser.parse_line("2026-06-23T01:17:12.528281Z  INFO   |- exec:       31.79 ms  (89%) << BOTTLENECK") is None


def test_tier_percentages():
    assert plotter.calculate_tier_percentages([100, 500, 1000, 200]) == (50.0, 25.0, 25.0)


def test_auto_source_uses_journalctl_reader():
    producer = plotter.choose_producer("auto", "execution", 0)

    assert producer.__class__.__name__ == "JournalctlProducer"


def test_resolve_journalctl_cmd_uses_unprivileged_journalctl(monkeypatch):
    from producers import resolve_journalctl_cmd

    monkeypatch.setattr(
        "producers.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )

    assert resolve_journalctl_cmd() == ("journalctl",)


def test_resolve_journalctl_cmd_falls_back_to_sudo(monkeypatch):
    from producers import resolve_journalctl_cmd

    monkeypatch.setattr("producers.os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        "producers.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 1})(),
    )

    assert resolve_journalctl_cmd() == ("sudo", "journalctl")


def test_parse_execution_client_version_strips_ethrex_rpc_noise():
    raw = (
        "ethrex:v17.0.0-HEAD-d7492778060776019f49dad9b5b2acff82a0a007/"
        "x86_64-unknown-linux-gnu/rustc-v1.91.0"
    )

    assert system_info.parse_execution_client_version("ethrex", raw) == "17.0.0"
    assert system_info.format_client_version_label("ethrex", raw) == "Ethrex v17.0.0"


def test_parse_execution_client_version_strips_reth_and_nethermind_rpc_noise():
    reth_raw = "reth:v2.3.0-9384bc5/x86_64-unknown-linux-gnu"
    nethermind_raw = "Nethermind:v1.38.0+c07a4d65/linux-x64/dotnet10.0.7"

    assert system_info.format_client_version_label("reth", reth_raw) == "Reth v2.3.0"
    assert system_info.format_client_version_label("Nethermind", nethermind_raw) == "Nethermind v1.38.0"


def test_detect_client_info_auto_detects_ethrex_from_rpc(monkeypatch):
    ethrex_version = (
        "ethrex:v17.0.0-HEAD-d7492778060776019f49dad9b5b2acff82a0a007/"
        "x86_64-unknown-linux-gnu/rustc-v1.91.0"
    )

    monkeypatch.setattr(system_info, "detect_execution_rpc", lambda _endpoint: ("ethrex", "Ethrex v17.0.0"))

    client_name, client_version = system_info.detect_client_info("http://127.0.0.1:8545")

    assert client_name == "ethrex"
    assert client_version == "Ethrex v17.0.0"


def test_detect_client_info_falls_back_to_ethrex_service_file(monkeypatch, tmp_path):
    service_file = tmp_path / "execution.service"
    service_file.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Ethrex Execution Layer Client service for MAINNET",
                "",
                "[Service]",
                "ExecStart=/usr/local/bin/ethrex --datadir /var/lib/ethrex",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(system_info, "detect_execution_rpc", lambda _endpoint: None)
    monkeypatch.setenv("EXECUTION_SERVICE_FILE", str(service_file))

    client_name, client_version = system_info.detect_client_info("http://127.0.0.1:8545")

    assert client_name == "ethrex"
    assert client_version == "Ethrex Unknown"
