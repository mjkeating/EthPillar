"""Tests for the Integration orchestrator matrix membership."""
from tests.integration.run_docker_tests import generate_tests


def test_nimbus_nethermind_hoodi_and_sepolia_are_generated():
    names = [t.log_name for t in generate_tests()]
    assert "Nimbus-Nethermind_HOODI" in names
    assert "Nimbus-Nethermind_SEPOLIA" in names
    assert "Lighthouse-Reth_HOODI" in names
    assert "Teku-Besu_HOODI" in names
    assert "Teku-VC-Only-HOODI" in names
