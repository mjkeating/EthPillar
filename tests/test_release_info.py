"""
Unit tests for client release info functions.

These tests verify that each deploy module's get_release_info function returns
correctly structured data with valid URLs and filenames, without making real
network requests.

For live checks against upstream APIs (catches regex/page drift like the Geth
pinned-version bug), run: bash tests/run_live_release_tests.sh
"""
import json
import sys
import os
import hashlib
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.common import get_client_release_info


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _geth_downloads_page(rows):
    """Build a downloads page whose table rows are ``(archive_url, md5)`` pairs."""
    payload = {
        "props": {
            "pageProps": {
                "data": {
                    "ALL_LINUX_STABLE_RELEASES": [
                        {
                            "release": {"url": url},
                            "checksum": md5,
                            "signature": {"url": f"{url}.asc"},
                        }
                        for url, md5 in rows
                    ]
                }
            }
        }
    }
    links = "\n".join(url for url, _md5 in rows)
    body = json.dumps(payload)
    return f'<script id="__NEXT_DATA__" type="application/json">{body}</script>\n{links}'


def _make_github_release(tag_name, asset_names, commit="abcdef0123456789abcdef0123456789abcdef01"):
    """Build a realistic GitHub release JSON structure (plus optional peel commit)."""
    data = {
        "tag_name": tag_name,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://github.com/fake/repo/releases/download/{tag_name}/{name}",
                "digest": f"sha256:{hashlib.sha256(name.encode()).hexdigest()}",
            }
            for name in asset_names
        ]
    }
    if commit:
        data["commit"] = commit
    return data


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
        assert info["sha256"] == [hashlib.sha256(b"besu-26.5.0.tar.gz").hexdigest()]
        assert info["commit"] == "abcdef0123456789abcdef0123456789abcdef01"

    @patch("deploy.common.get_github_release")
    def test_reth_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v2.2.0", ["reth-v2.2.0-x86_64-unknown-linux-gnu.tar.gz"])
        info = get_client_release_info("reth", "LATEST")
        assert info["version"] == "v2.2.0"
        assert len(info["download_urls"]) == 1
        assert info["download_urls"][0].endswith("reth-v2.2.0-x86_64-unknown-linux-gnu.tar.gz")
        assert info["commit"].startswith("abcdef")

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
        assert info["commit"] == "abcdef0123456789abcdef0123456789abcdef01"

    @patch("deploy.common.requests.get")
    @patch("deploy.common.get_github_release")
    def test_teku_returns_expected_structure(self, mock_gh, mock_get):
        mock_gh.return_value = {
            "tag_name": "26.5.0",
            "assets": [],
            "body": (
                "[tar.gz](https://artifacts.consensys.net/public/teku/raw/names/"
                "teku.tar.gz/versions/26.5.0/teku-26.5.0.tar.gz)"
            ),
        }
        mock_get.return_value = MagicMock(
            text=f"{'ab' * 32}  teku-26.5.0.tar.gz\n",
            raise_for_status=lambda: None,
        )
        info = get_client_release_info("teku", "LATEST")
        assert info["version"] == "26.5.0"
        assert info["filenames"][0] == "teku-26.5.0.tar.gz"
        assert info["download_urls"][0].endswith("teku-26.5.0.tar.gz")
        assert info["sha256"] == ["ab" * 32]
        assert mock_get.call_args.args[0].endswith("teku-26.5.0.tar.gz.sha256")

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
    def test_charon_returns_expected_structure(self, mock_gh):
        mock_gh.return_value = _make_github_release("v1.10.3", [
            "charon-v1.10.3-linux-amd64.tar.gz",
            "charon-v1.10.3-linux-arm64.tar.gz",
            "checksums.txt",
        ])
        info = get_client_release_info("charon", "LATEST")
        assert info["version"] == "v1.10.3"
        assert info["filenames"][0] == "charon-v1.10.3-linux-amd64.tar.gz"
        mock_gh.assert_called_once_with("ObolNetwork/charon", "LATEST")

    @patch("deploy.common.get_github_release")
    def test_ssv_node_source_build_release(self, mock_gh):
        mock_gh.return_value = _make_github_release("v2.5.1", [])
        mock_gh.return_value["tarball_url"] = (
            "https://api.github.com/repos/ssvlabs/ssv/tarball/v2.5.1"
        )
        info = get_client_release_info("ssv", "LATEST")
        assert info["version"] == "v2.5.1"
        assert info.get("source_build") is True
        mock_gh.assert_called_once_with("ssvlabs/ssv", "LATEST")

    @patch("deploy.common.get_github_release")
    def test_ssv_dkg_uses_dkg_repo_assets(self, mock_gh):
        mock_gh.return_value = _make_github_release("v3.1.1", [
            "ssv-dkg-v3.1.1-linux-amd64.zip",
            "ssv-dkg-v3.1.1-linux-arm64.zip",
        ])
        info = get_client_release_info("ssv-dkg", "LATEST")
        assert info["version"] == "v3.1.1"
        assert "ssv-dkg" in info["filenames"][0]
        mock_gh.assert_called_once_with("ssvlabs/ssv-dkg", "LATEST")

    @patch("deploy.common.get_github_release")
    def test_anchor_uses_sigp_linux_gnu_tarball(self, mock_gh):
        mock_gh.return_value = _make_github_release("v1.4.1", [
            "anchor-v1.4.1-aarch64-apple-darwin.tar.gz",
            "anchor-v1.4.1-x86_64-unknown-linux-gnu.tar.gz",
            "anchor-v1.4.1-aarch64-unknown-linux-gnu.tar.gz",
            "anchor-v1.4.1-x86_64-unknown-linux-gnu.tar.gz.asc",
        ])
        info = get_client_release_info("anchor", "LATEST")
        assert info["version"] == "v1.4.1"
        assert "linux-gnu" in info["filenames"][0]
        assert info["filenames"][0].startswith("anchor-")
        assert not info["filenames"][0].endswith(".asc")
        mock_gh.assert_called_once_with("sigp/anchor", "LATEST")

    @patch("deploy.common.requests.get")
    @patch("deploy.common.get_github_release")
    def test_web3signer_prefers_tarball_over_checksum(self, mock_gh, mock_get):
        filename = "web3signer-26.9.0.tar.gz"
        digest = hashlib.sha256(filename.encode()).hexdigest()
        mock_gh.return_value = _make_github_release("26.9.0", [
            "web3signer-26.9.0.tar.gz.sha256",
            "web3signer-26.9.0.zip",
            filename,
        ])
        mock_get.return_value = MagicMock(
            text=f"{digest}  {filename}\n",
            raise_for_status=lambda: None,
        )
        info = get_client_release_info("web3signer", "LATEST")
        assert info["version"] == "26.9.0"
        assert info["filenames"][0] == filename
        assert info["sha256"] == [digest]
        mock_gh.assert_called_once_with("Consensys-Incorporated/web3signer", "LATEST")
        mock_get.assert_called_once()

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
        url = "https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.14.0-abcdef.tar.gz"
        mock_req.return_value = MagicMock(
            status_code=200,
            text=_geth_downloads_page([(url, "ab" * 16)]),
        )
        info = get_client_release_info("geth", "LATEST")
        assert info["version"] == "v1.14.0"
        assert "geth-linux-amd64" in info["download_urls"][0]
        assert info["filenames"][0].endswith(".tar.gz")
        assert info["commit"] == "abcdef"
        assert info["md5"] == ["ab" * 16]
        assert info["signature_urls"] == [f"{url}.asc"]
        assert info["signature_key"] == "geth"

    @patch("requests.get")
    def test_geth_specific_version_tag(self, mock_req):
        older = "https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.17.3-117e067f.tar.gz"
        wanted = "https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.17.1-16783c16.tar.gz"
        mock_req.return_value = MagicMock(
            status_code=200,
            text=_geth_downloads_page([
                (older, "11111111111111111111111111111111"),
                (wanted, "22222222222222222222222222222222"),
            ]),
        )
        info = get_client_release_info("geth", "v1.17.1")
        assert info["version"] == "v1.17.1"
        assert "1.17.1-16783c16" in info["download_urls"][0]
        assert info["commit"] == "16783c16"
        assert info["md5"] == ["22222222222222222222222222222222"]

    @patch("requests.get")
    def test_geth_raises_when_no_match(self, mock_req):
        mock_req.return_value = MagicMock(status_code=200, text="no valid links here")
        with pytest.raises(ValueError):
            get_client_release_info("geth", "LATEST")

    @patch("requests.get")
    def test_geth_explicit_version_requires_host_arch_build(self, mock_req):
        """A version published only for the other arch is not installable here.

        Geth 1.17.7 was listed for linux-arm64 and not linux-amd64. LATEST still
        resolves the first build for the requested arch; an explicit lookup of
        the other arch's version raises.
        """
        from deploy.geth import get_release_info

        mock_req.return_value = MagicMock(
            status_code=200,
            text=_geth_downloads_page([
                ("https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.17.8-a5790770.tar.gz", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                ("https://gethstore.blob.core.windows.net/builds/geth-linux-arm64-1.17.8-a5790770.tar.gz", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                ("https://gethstore.blob.core.windows.net/builds/geth-linux-arm64-1.17.7-3d858f85.tar.gz", "cccccccccccccccccccccccccccccccc"),
                ("https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.17.6-3d84c6b2.tar.gz", "dddddddddddddddddddddddddddddddd"),
            ]),
        )
        latest = get_release_info("LATEST", True)
        assert latest["version"] == "v1.17.8"
        assert "linux-amd64-1.17.8" in latest["download_urls"][0]
        with pytest.raises(ValueError, match=r"linux-amd64 and version v1\.17\.7"):
            get_release_info("v1.17.7", True)
        arm_only = get_release_info("v1.17.7", False)
        assert arm_only["version"] == "v1.17.7"
        assert "linux-arm64-1.17.7" in arm_only["download_urls"][0]

    @patch("requests.get")
    def test_geth_checksum_is_not_taken_from_a_neighboring_build(self, mock_req):
        amd64 = "https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.17.8-a5790770.tar.gz"
        i386 = "https://gethstore.blob.core.windows.net/builds/geth-linux-386-1.17.8-a5790770.tar.gz"
        mock_req.return_value = MagicMock(
            status_code=200,
            text=amd64 + "\n" + _geth_downloads_page([
                (i386, "11111111111111111111111111111111"),
                (amd64, "22222222222222222222222222222222"),
            ]),
        )
        info = get_client_release_info("geth", "LATEST")
        assert info["download_urls"] == [amd64]
        assert info["md5"] == ["22222222222222222222222222222222"]
        assert info["signature_urls"] == [f"{amd64}.asc"]

    @patch("requests.head")
    @patch("requests.get")
    def test_geth_uses_blob_content_md5_when_table_row_is_missing(self, mock_get, mock_head):
        url = "https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-1.17.8-a5790770.tar.gz"
        payload = {
            "props": {"pageProps": {"data": {"ALL_LINUX_STABLE_RELEASES": []}}}
        }
        mock_get.return_value = MagicMock(
            status_code=200,
            text=(
                f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>\n'
                f"{url}"
            ),
        )
        mock_head.return_value = MagicMock(
            status_code=200,
            headers={"Content-MD5": "Ck4iFeKe5dhd8betChS9lA=="},
            raise_for_status=lambda: None,
        )
        info = get_client_release_info("geth", "LATEST")
        assert info["download_urls"] == [url]
        assert info["md5"] == ["0a4e2215e29ee5d85df1b7ad0a14bd94"]
        assert info["signature_urls"] == [f"{url}.asc"]
        mock_head.assert_called_once()

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
