"""Unit tests for GitHub release asset selection in deploy.common."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.common import (
    _asset_extension_rank,
    _asset_is_linux_candidate,
    _asset_matches_arch,
    _asset_name_excluded,
    pick_github_release_asset,
)


def _asset(name: str, tag: str = "v1.0.0") -> dict:
    return {
        "name": name,
        "browser_download_url": f"https://github.com/example/repo/releases/download/{tag}/{name}",
    }


class TestAssetNameExcluded:
    def test_rejects_checksum_sidecar(self):
        assert _asset_name_excluded("besu-26.6.0.tar.gz.sha256") is True

    def test_rejects_windows_binary(self):
        assert _asset_name_excluded("grandine-2.0.4-win-x64.exe") is True

    def test_accepts_linux_tarball(self):
        assert _asset_name_excluded("lodestar-v1.0.0-linux-amd64.tar.gz") is False


class TestAssetMatchesArch:
    @pytest.mark.parametrize(
        "name",
        [
            "reth-v1.0.0-x86_64-unknown-linux-gnu.tar.gz",
            "grandine-2.0.4-linux-x64",
            "grandine-2.0.1-amd64",
        ],
    )
    def test_amd64_markers(self, name: str):
        assert _asset_matches_arch(name, True) is True

    @pytest.mark.parametrize(
        "name",
        [
            "reth-v1.0.0-aarch64-unknown-linux-gnu.tar.gz",
            "grandine-2.0.1-arm64",
        ],
    )
    def test_arm64_markers(self, name: str):
        assert _asset_matches_arch(name, False) is True

    def test_rejects_opposite_arch(self):
        assert _asset_matches_arch("grandine-2.0.1-arm64", True) is False
        assert _asset_matches_arch("grandine-2.0.1-amd64", False) is False


class TestAssetIsLinuxCandidate:
    def test_accepts_linux_in_name(self):
        assert _asset_is_linux_candidate("erigon_v3.0.0_linux_amd64.tar.gz") is True

    def test_accepts_bare_arch_suffix(self):
        assert _asset_is_linux_candidate("grandine-2.0.1-amd64") is True

    def test_rejects_windows_build(self):
        assert _asset_is_linux_candidate("client-win-x64.exe") is False


class TestAssetExtensionRank:
    def test_prefers_tarball_over_zip(self):
        assert _asset_extension_rank("besu-1.0.0.tar.gz", (".tar.gz", ".zip")) == 0
        assert _asset_extension_rank("besu-1.0.0.zip", (".tar.gz", ".zip")) == 1

    def test_supports_extensionless_binary(self):
        assert _asset_extension_rank("grandine-2.0.4-linux-x64", (".tar.gz", "")) == 1

    def test_returns_none_for_unwanted_extension(self):
        assert _asset_extension_rank("checksums.txt", (".tar.gz", ".zip")) is None


class TestPickGithubReleaseAsset:
    def test_returns_filename_and_published_url(self):
        assets = [_asset("lodestar-v1.0.0-linux-amd64.tar.gz")]
        name, url = pick_github_release_asset(
            assets, True, name_contains=("lodestar",), client_label="Lodestar"
        )
        assert name == "lodestar-v1.0.0-linux-amd64.tar.gz"
        assert url.endswith("/lodestar-v1.0.0-linux-amd64.tar.gz")

    def test_prefers_tarball_when_both_archive_types_exist(self):
        assets = [
            _asset("besu-26.6.0.zip"),
            _asset("besu-26.6.0.tar.gz"),
        ]
        name, _url = pick_github_release_asset(
            assets, None, name_contains=("besu",), client_label="Besu"
        )
        assert name == "besu-26.6.0.tar.gz"

    def test_role_contains_filters_multi_binary_release(self):
        assets = [
            _asset("beacon-chain-v7.0.0-linux-amd64"),
            _asset("validator-v7.0.0-linux-amd64"),
        ]
        bn_name, _bn_url = pick_github_release_asset(
            assets, True, role_contains="beacon-chain", client_label="Prysm BN"
        )
        vc_name, _vc_url = pick_github_release_asset(
            assets, True, role_contains="validator", client_label="Prysm VC"
        )
        assert bn_name.startswith("beacon-chain")
        assert vc_name.startswith("validator")

    def test_name_contains_requires_all_substrings(self):
        assets = [_asset("nimbus-eth2_Linux_amd64_26.5.0_abc.tar.gz")]
        name, _url = pick_github_release_asset(
            assets,
            True,
            name_contains=("nimbus", "_linux_"),
            client_label="Nimbus",
        )
        assert "nimbus" in name.lower()

    def test_arch_neutral_archive_without_linux_marker(self):
        assets = [
            _asset("besu-26.6.0.tar.gz"),
            _asset("besu-26.6.0.tar.gz.sha256"),
        ]
        name, _url = pick_github_release_asset(
            assets, None, name_contains=("besu",), client_label="Besu"
        )
        assert name == "besu-26.6.0.tar.gz"

    def test_selects_arm64_asset_on_arm_hosts(self):
        assets = [
            _asset("erigon_v3.0.0_linux_amd64.tar.gz"),
            _asset("erigon_v3.0.0_linux_arm64.tar.gz"),
        ]
        name, _url = pick_github_release_asset(
            assets, False, name_contains=("erigon",), client_label="Erigon"
        )
        assert "arm64" in name

    def test_skips_assets_missing_name_or_url(self):
        assets = [
            {"name": "", "browser_download_url": "https://example.com/empty"},
            {"name": "lodestar-v1.0.0-linux-amd64.tar.gz", "browser_download_url": ""},
            _asset("lodestar-v1.0.0-linux-amd64.tar.gz"),
        ]
        name, _url = pick_github_release_asset(
            assets, True, name_contains=("lodestar",), client_label="Lodestar"
        )
        assert name == "lodestar-v1.0.0-linux-amd64.tar.gz"

    def test_raises_when_only_non_linux_assets_present(self):
        assets = [_asset("grandine-2.0.4-win-x64.exe"), _asset("checksums.txt")]
        with pytest.raises(ValueError, match="No Linux amd64 Lighthouse"):
            pick_github_release_asset(
                assets, True, name_contains=("lighthouse",), client_label="Lighthouse"
            )

    def test_raises_when_role_filter_has_no_match(self):
        assets = [_asset("beacon-chain-v7.0.0-linux-amd64")]
        with pytest.raises(ValueError, match="matching 'validator'"):
            pick_github_release_asset(
                assets, True, role_contains="validator", client_label="Prysm validator"
            )
