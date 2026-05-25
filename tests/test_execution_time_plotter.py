import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "logging" / "plotExecutionTimes" / "plotProcessingTimes.py"
SPEC = importlib.util.spec_from_file_location("plot_processing_times", MODULE_PATH)
plotter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = plotter
SPEC.loader.exec_module(plotter)


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


def test_besu_parser_converts_raw_gas_to_mgas():
    parser = plotter.ExecutionLogParser("besu")

    point = parser.parse_line("INFO mwei bfee| 20,237,520 (0.154s exec)")

    assert point.gas_used_mgas == 20.23752
    assert point.elapsed_time_ms == 154.0


def test_nethermind_parser_combines_elapsed_and_following_gas_line():
    parser = plotter.ExecutionLogParser("nethermind")

    assert parser.parse_line("Processed 123 | 345.6 ms") is None
    point = parser.parse_line("Block 123 17.42 MGas")

    assert point.gas_used_mgas == 17.42
    assert point.elapsed_time_ms == 345.6


def test_tier_percentages():
    assert plotter.calculate_tier_percentages([100, 500, 1000, 200]) == (50.0, 25.0, 25.0)
