"""Unit tests for GitHub release tag resolution in deploy.common."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.common import (
    _fetch_github_release_by_tag,
    _find_github_release_by_normalized_tag,
    _github_release_tag_candidates,
    _normalize_release_version_key,
    get_github_release,
)


class TestNormalizeReleaseVersionKey:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("v1.11.0", "1.11"),
            ("v1.11", "1.11"),
            ("1.11.0", "1.11"),
            ("v1.12-alpha1", "1.12-alpha1"),
            ("V2.0.0", "2.0"),
        ],
    )
    def test_normalizes_patch_zero_and_prefix(self, tag: str, expected: str):
        assert _normalize_release_version_key(tag) == expected


class TestGithubReleaseTagCandidates:
    def test_v1_11_0_includes_major_minor_aliases(self):
        assert _github_release_tag_candidates("v1.11.0") == [
            "v1.11.0",
            "1.11.0",
            "v1.11",
            "1.11",
        ]

    def test_preserves_exact_tag_first(self):
        assert _github_release_tag_candidates("v1.12") == ["v1.12", "1.12"]


class TestGetGithubRelease:
    @patch("deploy.common._fetch_github_release_by_tag")
    def test_returns_exact_tag_match(self, mock_fetch):
        mock_fetch.return_value = {"tag_name": "v1.12", "assets": []}
        release = get_github_release("flashbots/mev-boost", "v1.12")
        assert release["tag_name"] == "v1.12"
        mock_fetch.assert_called_once_with("flashbots/mev-boost", "v1.12")

    @patch("deploy.common._find_github_release_by_normalized_tag")
    @patch("deploy.common._fetch_github_release_by_tag")
    def test_resolves_v1_11_0_to_v1_11_release(self, mock_fetch, mock_find):
        mock_fetch.side_effect = [None, None, {"tag_name": "v1.11", "assets": []}, None]
        release = get_github_release("flashbots/mev-boost", "v1.11.0")
        assert release["tag_name"] == "v1.11"
        mock_find.assert_not_called()

    @patch("deploy.common._find_github_release_by_normalized_tag")
    @patch("deploy.common._fetch_github_release_by_tag")
    def test_falls_back_to_release_scan(self, mock_fetch, mock_find):
        mock_fetch.return_value = None
        mock_find.return_value = {"tag_name": "v1.11", "assets": []}
        release = get_github_release("flashbots/mev-boost", "v1.11.0")
        assert release["tag_name"] == "v1.11"
        mock_find.assert_called_once_with("flashbots/mev-boost", "v1.11.0")

    @patch("deploy.common.requests.get")
    def test_latest_uses_latest_endpoint(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"tag_name": "v1.12"})
        release = get_github_release("flashbots/mev-boost", "LATEST")
        assert release["tag_name"] == "v1.12"
        mock_get.assert_called_once()
        assert mock_get.call_args.args[0].endswith("/releases/latest")


class TestFindGithubReleaseByNormalizedTag:
    @patch("deploy.common.requests.get")
    def test_skips_drafts_and_matches_normalized_tag(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"tag_name": "v1.12", "draft": False},
                {"tag_name": "v1.11", "draft": False},
                {"tag_name": "v1.10.0", "draft": True},
            ],
        )
        release = _find_github_release_by_normalized_tag("flashbots/mev-boost", "v1.11.0")
        assert release["tag_name"] == "v1.11"
