"""
Live release-info integration tests.

Why these exist
---------------
The mocked tests in ``test_release_info.py`` only check that ``get_release_info()``
parses canned HTML/JSON correctly. They cannot catch real-world breakage such as:

* Geth's pinned-version regex diverging from geth.ethereum.org URL layout
* GitHub release pages changing asset naming conventions
* Constructed download URLs that parse fine but 404/401 at download time

These tests exercise the same code path used by ``update_execution.sh``,
``update_consensus.sh``, and ``update_mevboost.sh`` when resolving binaries.

What each test does
-------------------
For every supported client (12 total), ``test_client_release_info_live`` verifies
three scenarios that mirror the update menus:

1. **LATEST** — same as choosing "install latest release"
2. **Explicit version** — re-query using the version string returned for LATEST
3. **Older release** — same as picking a non-latest tag from the numbered list

For each scenario we assert:

* ``get_client_release_info()`` returns ``version``, ``download_urls``, and
  ``filenames`` with consistent lengths
* Every download URL responds (HEAD, or ranged GET fallback)

Geth is special: binaries are scraped from geth.ethereum.org, not GitHub
releases. Older versions are discovered from that page rather than GitHub tags.

Requirements
------------
* Network access to GitHub and client CDNs
* ``GITHUB_TOKEN`` for GitHub API calls (read-only public repo access is enough).
  Download URLs are checked *without* the token — sending it causes 401/403.

Run via ``bash tests/run_live_release_tests.sh`` (skipped in the default unit run).
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.common import _github_api_headers, get_client_release_info

pytestmark = pytest.mark.live

# EthPillar client name -> GitHub repo used to find an older *published* release.
# Geth download URLs come from geth.ethereum.org; the repo is only used as a
# secondary check that tag names align with upstream.
CLIENT_REPOS: list[tuple[str, str | None]] = [
    ("besu", "besu-eth/besu"),
    ("reth", "paradigmxyz/reth"),
    ("erigon", "erigontech/erigon"),
    ("nethermind", "NethermindEth/nethermind"),
    ("geth", "ethereum/go-ethereum"),
    ("lighthouse", "sigp/lighthouse"),
    ("lodestar", "ChainSafe/lodestar"),
    ("teku", "ConsenSys/teku"),
    ("nimbus", "status-im/nimbus-eth2"),
    ("grandine", "grandinetech/grandine"),
    ("prysm", "prysmaticlabs/prysm"),
    ("mevboost", "flashbots/mev-boost"),
]

_GETH_DOWNLOADS_URL = "https://geth.ethereum.org/downloads"


def _normalize_version(version: str) -> str:
    """Strip leading ``v``/``V`` so ``v1.17.1`` and ``1.17.1`` compare equal."""
    return version.removeprefix("v").removeprefix("V")


@lru_cache(maxsize=None)
def _github_releases(repo: str) -> tuple[dict, ...]:
    """Return non-draft GitHub releases for *repo*, newest first (cached per run)."""
    response = requests.get(
        f"https://api.github.com/repos/{repo}/releases",
        params={"per_page": 30},
        headers=_github_api_headers(),
        timeout=30,
    )
    if response.status_code == 403 and "rate limit" in response.text.lower():
        pytest.skip("GitHub API rate limit exceeded; set GITHUB_TOKEN and retry")
    response.raise_for_status()
    return tuple(
        release
        for release in response.json()
        if not release.get("draft")
    )


def _older_release_tag(repo: str, latest_version: str) -> str | None:
    """First published GitHub release tag that differs from *latest_version*."""
    latest_norm = _normalize_version(latest_version)
    for release in _github_releases(repo):
        tag = release["tag_name"]
        if _normalize_version(tag) != latest_norm:
            return tag
    return None


@lru_cache(maxsize=1)
def _geth_downloads_page() -> str:
    """HTML of geth.ethereum.org/downloads (cached for the test session)."""
    response = requests.get(
        _GETH_DOWNLOADS_URL,
        headers=_github_api_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def _older_geth_version(latest_version: str) -> str | None:
    """First Geth version on the downloads page that is not *latest_version*."""
    latest_norm = _normalize_version(latest_version)
    versions: list[str] = []
    for match in re.finditer(
        r"geth-linux-(?:amd64|arm64)-([0-9.]+)-[a-f0-9]+\.tar\.gz",
        _geth_downloads_page(),
    ):
        ver = match.group(1)
        if ver not in versions:
            versions.append(ver)
    for ver in versions:
        if _normalize_version(ver) != latest_norm:
            return f"v{ver}"
    return None


def _assert_release_info_shape(info: dict, client: str) -> None:
    """Assert ``get_client_release_info()`` returned the expected dict shape."""
    assert info.get("version"), f"{client}: missing version"
    assert info.get("download_urls"), f"{client}: missing download_urls"
    assert info.get("filenames"), f"{client}: missing filenames"
    assert len(info["download_urls"]) == len(info["filenames"]), client
    for url in info["download_urls"]:
        assert url.startswith("http"), f"{client}: invalid URL {url!r}"


def _download_check_headers() -> dict:
    """Headers for probing release/CDN URLs (no GitHub API token)."""
    # GITHUB_TOKEN is for api.github.com only. Sending Authorization to
    # release asset URLs returns 401; Azure blob HEAD may return 403.
    return {"User-Agent": "ethpillar-live-release-test/1.0"}


def _assert_url_reachable(url: str, client: str) -> None:
    """Confirm *url* exists using HEAD, falling back to a 1-byte ranged GET."""
    headers = _download_check_headers()
    response = requests.head(
        url, allow_redirects=True, timeout=45, headers=headers
    )
    if response.status_code not in (200, 206):
        response = requests.get(
            url,
            headers={**headers, "Range": "bytes=0-0"},
            stream=True,
            allow_redirects=True,
            timeout=45,
        )
        response.close()
    assert response.status_code in (200, 206), (
        f"{client}: URL not reachable ({response.status_code}): {url}"
    )


def _assert_release_info(info: dict, client: str) -> None:
    """Validate structure and reachability for every URL in *info*."""
    _assert_release_info_shape(info, client)
    for url in info["download_urls"]:
        _assert_url_reachable(url, client)


def _release_info(client: str, version_tag: str) -> dict:
    """Call ``get_client_release_info()``, skipping (not failing) on API rate limits."""
    try:
        return get_client_release_info(client, version_tag)
    except requests.HTTPError as exc:
        if (
            exc.response is not None
            and exc.response.status_code == 403
            and "rate limit" in exc.response.text.lower()
        ):
            pytest.skip("GitHub API rate limit exceeded; set GITHUB_TOKEN and retry")
        raise


@pytest.mark.parametrize("client,repo", CLIENT_REPOS, ids=[c for c, _ in CLIENT_REPOS])
def test_client_release_info_live(client: str, repo: str | None) -> None:
    """End-to-end release resolution for one client: LATEST, explicit, and older tag.

    This is the production update path:

    * ``update_*.sh`` calls ``python3 -m deploy.common release_info <Client> <tag>``
    * The returned ``download_urls`` are passed to ``wget``

    Steps:

    1. Resolve LATEST and verify each download URL responds.
    2. Resolve again using the version string from step 1 (explicit tag form).
    3. Resolve an older release — Geth from the downloads page, others from the
       next published GitHub release — and verify URLs. For Geth, also assert
       the resolved URL appears on geth.ethereum.org/downloads.
    """
    latest = _release_info(client, "LATEST")
    _assert_release_info(latest, client)

    explicit = _release_info(client, latest["version"])
    assert _normalize_version(explicit["version"]) == _normalize_version(latest["version"])
    _assert_release_info(explicit, client)

    if client == "geth":
        older_tag = _older_geth_version(latest["version"])
    else:
        assert repo is not None
        older_tag = _older_release_tag(repo, latest["version"])

    if older_tag is None:
        pytest.skip(f"{client}: no older release found")

    older = _release_info(client, older_tag)
    assert _normalize_version(older["version"]) == _normalize_version(older_tag)
    _assert_release_info(older, client)

    if client == "geth":
        assert older["download_urls"][0] in _geth_downloads_page()
