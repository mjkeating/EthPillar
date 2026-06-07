"""
Unit tests for client release info functions.

These tests verify that each deploy module's get_release_info function returns
correctly structured data with valid URLs and filenames, without making real
network requests.

For live checks against upstream APIs (catches regex/page drift like the Geth
pinned-version bug), run: bash tests/run_live_release_tests.sh
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.common import get_client_release_info


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_github_release(tag_name, asset_names):
    """Build a realistic GitHub release JSON structure."""
    return {
        "tag_name": tag_name,
        "assets": [
            {"name": name, "browser_download_url": f"https://github.com/fake/repo/releases/download/{tag_name}/{name}"}
            for name in asset_names
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# get_client_release_info (orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetClientReleaseInfo:
    @patch("deploy.common.get_github_release")
    def test_besu_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("26.5.0", ["besu-26.5.0.tar.gz"])
        info = get_client_release_info("besu", "LATEST")
        assert info["version"] == "26.5.0"
        assert len(info["download_urls"]) == 1
        assert info["download_urls"][0].endswith("besu-26.5.0.tar.gz")
        assert len(info["filenames"]) == 1
        assert info["filenames"][0] == "besu-26.5.0.tar.gz"

    @patch("deploy.common.get_github_release")
    def test_reth_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v2.2.0", ["reth-v2.2.0-x86_64-unknown-linux-gnu.tar.gz"])
        info = get_client_release_info("reth", "LATEST")
        assert info["version"] == "v2.2.0"
        assert len(info["download_urls"]) == 1
        assert info["download_urls"][0].endswith("reth-v2.2.0-x86_64-unknown-linux-gnu.tar.gz")

    @patch("deploy.common.get_github_release")
    def test_erigon_finds_amd64_asset(self, mock_gh):
        mock_gh.return_value = _make_github_release("v3.4.1", [
            "erigon_v3.4.1_linux_amd64.tar.gz",
            "erigon_v3.4.1_linux_arm64.tar.gz",
            "checksums.txt"
        ])
        info = get_client_release_info("erigon", "LATEST")
        assert info["version"] == "v3.4.1"
        assert info["filenames"][0] == "erigon_v3.4.1_linux_amd64.tar.gz"
        assert "amd64" in info["download_urls"][0]

    @patch("deploy.common.get_github_release")
    def test_erigon_finds_arm64_asset(self, mock_gh):
        mock_gh.return_value = _make_github_release("v3.4.1", [
            "erigon_v3.4.1_linux_amd64.tar.gz",
            "erigon_v3.4.1_linux_arm64.tar.gz"
        ])
        # Simulate arm64 platform
        with patch("platform.machine", return_value="aarch64"):
            info = get_client_release_info("erigon", "LATEST")
            assert info["filenames"][0] == "erigon_v3.4.1_linux_arm64.tar.gz"
            assert "arm64" in info["download_urls"][0]

    @patch("deploy.common.get_github_release")
    def test_nethermind_finds_amd64_asset(self, mock_gh):
        mock_gh.return_value = _make_github_release("1.37.2", [
            "nethermind-1.37.2-8e212be6-linux-x64.zip",
            "nethermind-1.37.2-8e212be6-linux-arm64.zip"
        ])
        info = get_client_release_info("nethermind", "LATEST")
        assert info["version"] == "1.37.2"
        assert info["filenames"][0].endswith("linux-x64.zip")

    @patch("deploy.common.get_github_release")
    def test_lighthouse_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v8.1.3", [
            "lighthouse-v8.1.3-x86_64-unknown-linux-gnu.tar.gz"
        ])
        info = get_client_release_info("lighthouse", "LATEST")
        assert info["version"] == "v8.1.3"
        assert "lighthouse" in info["filenames"][0]

    @patch("deploy.common.get_github_release")
    def test_lodestar_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v1.42.0", [
            "lodestar-v1.42.0-linux-amd64.tar.gz"
        ])
        info = get_client_release_info("lodestar", "LATEST")
        assert info["version"] == "v1.42.0"
        assert "lodestar" in info["filenames"][0]

    @patch("deploy.common.get_github_release")
    def test_teku_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = {
            "tag_name": "26.5.0",
            "assets": [],
            "body": (
                "[tar.gz](https://artifacts.consensys.net/public/teku/raw/names/"
                "teku.tar.gz/versions/26.5.0/teku-26.5.0.tar.gz)"
            ),
        }
        info = get_client_release_info("teku", "LATEST")
        assert info["version"] == "26.5.0"
        assert info["filenames"][0] == "teku-26.5.0.tar.gz"
        assert info["download_urls"][0].endswith("teku-26.5.0.tar.gz")

    @patch("deploy.common.get_github_release")
    def test_nimbus_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v26.5.0", [
            "nimbus-eth2_Linux_amd64_26.5.0_6fb05f36.tar.gz"
        ])
        info = get_client_release_info("nimbus", "LATEST")
        assert info["version"] == "v26.5.0"
        assert "nimbus" in info["filenames"][0]

    @patch("deploy.common.get_github_release")
    def test_grandine_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v2.0.4", [
            "grandine-2.0.4-linux-x64"
        ])
        info = get_client_release_info("grandine", "LATEST")
        assert info["version"] == "v2.0.4"
        assert info["filenames"][0] == "grandine-2.0.4-linux-x64"

    @patch("deploy.common.get_github_release")
    def test_grandine_legacy_asset_names(self, mock_gh):
        mock_gh.return_value = _make_github_release("2.0.1", [
            "grandine-2.0.1-amd64",
            "grandine-2.0.1-arm64",
        ])
        info = get_client_release_info("grandine", "2.0.1")
        assert info["version"] == "2.0.1"
        assert info["filenames"][0] == "grandine-2.0.1-amd64"
        assert info["download_urls"][0].endswith("/grandine-2.0.1-amd64")

    @patch("deploy.common.get_github_release")
    def test_prysm_returns_two_urls(self, mock_gh):
        mock_gh.return_value = _make_github_release("v7.1.3", [
            "beacon-chain-v7.1.3-linux-amd64",
            "validator-v7.1.3-linux-amd64"
        ])
        info = get_client_release_info("prysm", "LATEST")
        assert info["version"] == "v7.1.3"
        assert len(info["download_urls"]) == 2
        assert len(info["filenames"]) == 2
        assert any("beacon-chain" in u for u in info["download_urls"])
        assert any("validator" in u for u in info["download_urls"])

    @patch("deploy.common.get_github_release")
    def test_mevboost_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v1.12", [
            "mev-boost_1.12_linux_amd64.tar.gz"
        ])
        info = get_client_release_info("mevboost", "LATEST")
        assert info["version"] == "v1.12"
        assert "mev-boost" in info["filenames"][0]

    @patch("deploy.common.get_github_release")
    def test_mevboost_via_hyphenated_name(self, mock_gh):
        mock_gh.return_value = _make_github_release("v1.12", [
            "mev-boost_1.12_linux_amd64.tar.gz"
        ])
        info = get_client_release_info("mev-boost", "LATEST")
        assert info["version"] == "v1.12"

    @patch("deploy.common.get_github_release")
    def test_mevboost_v1_11_0_resolves_to_v1_11_release(self, mock_gh):
        mock_gh.return_value = _make_github_release("v1.11", [
            "mev-boost_1.11_linux_amd64.tar.gz"
        ])
        info = get_client_release_info("mevboost", "v1.11.0")
        assert info["version"] == "v1.11"
        assert info["filenames"][0] == "mev-boost_1.11_linux_amd64.tar.gz"
        mock_gh.assert_called_once_with("flashbots/mev-boost", "v1.11.0")

    @patch("requests.get")
    def test_geth_scrapes_download_page(self, mock_req):
        mock_req.return_value = MagicMock(
            status_code=200,
            text='https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.14.0-abcdef.tar.gz'
        )
        info = get_client_release_info("geth", "LATEST")
        assert info["version"] == "v1.14.0"
        assert "geth-linux-amd64" in info["download_urls"][0]
        assert info["filenames"][0].endswith(".tar.gz")

    @patch("requests.get")
    def test_geth_specific_version_tag(self, mock_req):
        mock_req.return_value = MagicMock(
            status_code=200,
            text=(
                "https://gethstore.blob.core.windows.net/builds/"
                "geth-linux-amd64-1.17.3-117e067f.tar.gz "
                "https://gethstore.blob.core.windows.net/builds/"
                "geth-linux-amd64-1.17.1-16783c16.tar.gz"
            ),
        )
        info = get_client_release_info("geth", "v1.17.1")
        assert info["version"] == "v1.17.1"
        assert "1.17.1-16783c16" in info["download_urls"][0]

    @patch("requests.get")
    def test_geth_raises_when_no_match(self, mock_req):
        mock_req.return_value = MagicMock(status_code=200, text="no valid links here")
        with pytest.raises(ValueError):
            get_client_release_info("geth", "LATEST")

    def test_unsupported_client_raises(self):
        with pytest.raises(ValueError):
            get_client_release_info("notarealclient", "LATEST")

    def test_client_name_case_insensitive(self):
        with patch("deploy.common.get_github_release") as mock_gh:
            mock_gh.return_value = _make_github_release("v1.0.0", ["besu-v1.0.0.tar.gz"])
            info_lower = get_client_release_info("besu", "LATEST")
            info_upper = get_client_release_info("BESU", "LATEST")
            assert info_lower == info_upper

    @patch("deploy.common.get_github_release")
    def test_specific_version_tag(self, mock_gh):
        mock_gh.return_value = _make_github_release("v1.2.3", [
            "reth-v1.2.3-x86_64-unknown-linux-gnu.tar.gz"
        ])
        info = get_client_release_info("reth", "v1.2.3")
        assert info["version"] == "v1.2.3"
        mock_gh.assert_called_once_with("paradigmxyz/reth", "v1.2.3")

    @patch("deploy.common.get_github_release")
    def test_lighthouse_raises_without_matching_asset(self, mock_gh):
        mock_gh.return_value = _make_github_release("v8.0.0", ["checksums.txt"])
        with pytest.raises(ValueError, match="Lighthouse"):
            get_client_release_info("lighthouse", "LATEST")
