"""Unit tests for integration FAIL summary extraction."""
from pathlib import Path

from tests.integration.run_docker_tests import extract_failure_reason


def test_extract_version_mismatch(tmp_path: Path):
    log = tmp_path / "grandine.log"
    log.write_text(
        "\n".join(
            [
                "Starting test...",
                "✅ EL Nethermind matches LATEST (1.39.2)",
                "✅ CL Grandine version: v2.0.5",
                "❌ CL Grandine mismatch: installed 2.0.5, LATEST 2.0.6",
                "✅ VC Lighthouse matches LATEST (8.2.1)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert extract_failure_reason(str(log)) == (
        "CL Grandine mismatch: installed 2.0.5, LATEST 2.0.6"
    )


def test_extract_multiple_markers_keeps_last_three(tmp_path: Path):
    log = tmp_path / "multi.log"
    log.write_text(
        "\n".join(
            [
                "❌ Binary: missing-a",
                "❌ Binary: missing-b",
                "❌ Binary: missing-c",
                "❌ Binary: missing-d",
                "FAIL: Service consensus journal contains fatal error: panic",
            ]
        ),
        encoding="utf-8",
    )
    reason = extract_failure_reason(str(log), max_reasons=3)
    assert reason == (
        "Binary: missing-c; "
        "Binary: missing-d; "
        "Service consensus journal contains fatal error: panic"
    )


def test_extract_container_start_failure(tmp_path: Path):
    log = tmp_path / "boot.log"
    log.write_text(
        "Starting test...\n\nFailed to start container. Exit code 125\n",
        encoding="utf-8",
    )
    assert extract_failure_reason(str(log)) == "Failed to start container. Exit code 125"


def test_extract_systemctl_start_includes_journal_err(tmp_path: Path):
    log = tmp_path / "nimbus.log"
    log.write_text(
        "\n".join(
            [
                "Starting test Nimbus-Nethermind_HOODI...",
                "  ❌ systemctl start consensus failed: "
                "Job for consensus.service failed. | "
                'ERR Failed to obtain genesis state err="Could not read response '
                'headers: Buffer limit reached"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    reason = extract_failure_reason(str(log))
    assert "systemctl start consensus failed" in reason
    assert "Buffer limit reached" in reason


def test_extract_missing_log():
    assert extract_failure_reason(None) == "no log available"
    assert extract_failure_reason("/no/such/file.log") == "no log available"


def test_extract_fallback_last_line(tmp_path: Path):
    log = tmp_path / "opaque.log"
    log.write_text("something happened\nthen it stopped unexpectedly\n", encoding="utf-8")
    assert extract_failure_reason(str(log)) == "then it stopped unexpectedly"
