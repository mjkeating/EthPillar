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
For every supported client, ``test_client_release_info_live`` verifies
three scenarios that mirror the update menus:

1. **LATEST** — same as choosing "install latest release"
2. **Explicit version** — re-query using the version string returned for LATEST
3. **Older release** — same as picking a non-latest tag from the numbered list

For each scenario we assert:

* ``get_client_release_info()`` returns ``version``, ``download_urls``,
  ``filenames``, and ``commit`` (git SHA) with consistent URL/filename lengths
* ``commit`` is a non-empty hex string (7–40 chars) suitable for prefix matching
* Every download URL responds (HEAD, or ranged GET fallback)

Geth is special: binaries are scraped from geth.ethereum.org, not GitHub
releases. Older versions are discovered from that page rather than GitHub tags,
and only for the architecture ``get_release_info`` will look up.

Requirements
------------
* Network access to GitHub and client CDNs
* ``GITHUB_TOKEN`` for GitHub API calls (read-only public repo access is enough).
  Download URLs are checked *without* the token — sending it causes 401/403.

Run via ``bash tests/run_live_release_tests.sh`` (skipped in the default unit run).
"""

from __future__ import annotations

import os
import platform
import re
import sys
import time
from functools import lru_cache
from unittest.mock import patch

import pytest
import requests

_tests_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_tests_dir)
sys.path.insert(0, _repo_root)

from deploy.common import _github_api_headers, get_client_release_info

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
    ("charon", "ObolNetwork/charon"),
    ("ethrex", "lambdaclass/ethrex"),
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


def _older_release_tag(client: str, repo: str, latest_version: str) -> str | None:
    """First older GitHub release whose assets resolve via ``get_client_release_info``.

    Releases are returned newest-first. Special tags (for example Nethermind's
    ``zisk-guest-r7``) may appear before or after the real ``/releases/latest``
    tag; only releases that actually resolve to installable binaries count.
    """
    latest_norm = _normalize_version(latest_version)
    passed_latest = False
    for release in _github_releases(repo):
        tag = release["tag_name"]
        if not passed_latest:
            if _normalize_version(tag) == latest_norm:
                passed_latest = True
            continue
        try:
            _release_info(client, tag)
        except ValueError:
            continue
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


def _geth_release_arch() -> str:
    """Linux arch ``get_release_info`` searches for on this machine.

    Same rule as ``get_client_release_info``: ``x86_64`` and ``amd64`` map to
    amd64, and every other ``platform.machine()`` value maps to arm64.
    ``get_machine_architecture`` agrees on the hosts EthPillar supports
    (``x86_64`` → amd64, ``aarch64`` → arm64).
    """
    raw_arch = platform.machine().lower()
    return "amd64" if raw_arch in ("x86_64", "amd64") else "arm64"


def _older_geth_version(latest_version: str, arch: str | None = None) -> str | None:
    """First Geth build for *arch* on the downloads page that is not *latest_version*.

    The downloads page lists linux-amd64 and linux-arm64 together, and a version
    can exist for only one of them. ``get_release_info`` looks up only the
    resolved architecture, so discovery must use that same arch. Passing
    ``arch`` overrides the host (used by unit tests).
    """
    resolved_arch = arch or _geth_release_arch()
    latest_norm = _normalize_version(latest_version)
    versions: list[str] = []
    pattern = rf"geth-linux-{re.escape(resolved_arch)}-([0-9.]+)-[a-f0-9]+\.tar\.gz"
    for match in re.finditer(pattern, _geth_downloads_page()):
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
    commit = info.get("commit")
    assert commit, f"{client}: missing commit"
    assert re.fullmatch(r"[0-9a-fA-F]{6,40}", str(commit)), (
        f"{client}: commit is not a hex SHA: {commit!r}"
    )


def _download_check_headers() -> dict:
    """Headers for probing release/CDN URLs (no GitHub API token)."""
    # GITHUB_TOKEN is for api.github.com only. Sending Authorization to
    # release asset URLs returns 401; Azure blob HEAD may return 403.
    return {"User-Agent": "ethpillar-live-release-test/1.0"}


_URL_REACHABILITY_ATTEMPTS = 4
_URL_REACHABILITY_BACKOFF_SECONDS = (1, 2, 4)
_URL_SUCCESS_STATUSES = frozenset({200, 206})
_URL_HARD_FAIL_STATUSES = frozenset({401, 403, 404})


def _is_transient_http_status(status: int) -> bool:
    """True for retryable CDN/rate-limit responses (HTTP 429 or 5xx)."""
    return status == 429 or status >= 500


def _probe_download_url(session: requests.Session, url: str) -> int:
    """HEAD *url*, then a 1-byte ranged GET if HEAD was not 200/206.

    Returns the status used to decide reachability. HEAD-only success skips GET
    (same as the original probe). Azure blob HEAD may return 403 even for a
    valid object, so 401/403/404 on HEAD still fall through to GET.
    """
    headers = _download_check_headers()
    response = session.head(
        url, allow_redirects=True, timeout=45, headers=headers
    )
    if response.status_code in _URL_SUCCESS_STATUSES:
        return response.status_code
    response = session.get(
        url,
        headers={**headers, "Range": "bytes=0-0"},
        stream=True,
        allow_redirects=True,
        timeout=45,
    )
    response.close()
    return response.status_code


def _assert_url_reachable(url: str, client: str) -> None:
    """Confirm *url* exists using HEAD, falling back to a 1-byte ranged GET.

    Transient CDN/network failures (HTTP 5xx, 429, timeouts, connection errors)
    are retried with short exponential backoff. Persistent 5xx/429 after
    retries skip so Nightly does not stay red on asset blips. HTTP 401/403/404
    fail immediately (bad URL or auth). Other unexpected codes fail without
    retry.
    """
    last_status: int | None = None
    last_error: BaseException | None = None
    attempts = _URL_REACHABILITY_ATTEMPTS

    with requests.Session() as session:
        for attempt in range(attempts):
            try:
                status = _probe_download_url(session, url)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                last_status = None
            else:
                if status in _URL_SUCCESS_STATUSES:
                    return
                if status in _URL_HARD_FAIL_STATUSES or not _is_transient_http_status(
                    status
                ):
                    assert status in _URL_SUCCESS_STATUSES, (
                        f"{client}: URL not reachable ({status}): {url}"
                    )
                last_status = status
                last_error = None

            if attempt < attempts - 1:
                time.sleep(_URL_REACHABILITY_BACKOFF_SECONDS[attempt])

    if last_status is not None and _is_transient_http_status(last_status):
        pytest.skip(
            f"{client}: transient CDN/asset HTTP {last_status} after "
            f"{attempts} probes of {url}"
        )
    if last_error is not None:
        pytest.skip(
            f"{client}: transient CDN/network error after {attempts} probes "
            f"of {url}: {type(last_error).__name__}: {last_error}"
        )
    assert last_status in _URL_SUCCESS_STATUSES, (
        f"{client}: URL not reachable ({last_status}): {url}"
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


def _mock_nethermind_release_info(client: str, tag: str) -> dict:
    if tag.startswith("zisk-guest"):
        raise ValueError("No Linux amd64 Nethermind asset found")
    return {
        "version": tag,
        "download_urls": [f"https://example.com/{tag}.zip"],
        "filenames": [f"nethermind-{tag}.zip"],
        "commit": "c07a4d65abcdef",
    }


@patch("tests.test_release_info_live._release_info", side_effect=_mock_nethermind_release_info)
@patch("tests.test_release_info_live._github_releases")
def test_older_release_tag_skips_newer_special_releases(
    mock_releases, _mock_release_info
) -> None:
    """Regression: do not treat a newer non-latest release as the older tag."""
    mock_releases.return_value = (
        {"tag_name": "zisk-guest-r7"},
        {"tag_name": "1.38.1"},
        {"tag_name": "1.38.0"},
    )
    assert _older_release_tag("nethermind", "NethermindEth/nethermind", "1.38.1") == "1.38.0"


@patch("tests.test_release_info_live._release_info", side_effect=_mock_nethermind_release_info)
@patch("tests.test_release_info_live._github_releases")
def test_older_release_tag_skips_special_release_between_versions(
    mock_releases, _mock_release_info
) -> None:
    """Regression: special releases sandwiched between real versions are not older."""
    mock_releases.return_value = (
        {"tag_name": "zisk-guest-r8"},
        {"tag_name": "1.39.0"},
        {"tag_name": "zisk-guest-r7"},
        {"tag_name": "1.38.1"},
    )
    assert _older_release_tag("nethermind", "NethermindEth/nethermind", "1.39.0") == "1.38.1"


# Page order matches geth.ethereum.org when 1.17.7 exists only for arm64.
_GETH_ARCH_MISMATCH_PAGE = (
    "https://gethstore.blob.core.windows.net/builds/"
    "geth-linux-amd64-1.17.8-a5790770.tar.gz "
    "https://gethstore.blob.core.windows.net/builds/"
    "geth-linux-arm64-1.17.8-a5790770.tar.gz "
    "https://gethstore.blob.core.windows.net/builds/"
    "geth-linux-arm64-1.17.7-3d858f85.tar.gz "
    "https://gethstore.blob.core.windows.net/builds/"
    "geth-linux-amd64-1.17.6-3d84c6b2.tar.gz "
    "https://gethstore.blob.core.windows.net/builds/"
    "geth-linux-arm64-1.17.6-3d84c6b2.tar.gz"
)


@patch(
    "tests.test_release_info_live._geth_downloads_page",
    return_value=_GETH_ARCH_MISMATCH_PAGE,
)
def test_older_geth_version_skips_other_arch_only_release(_mock_page) -> None:
    """Do not pick a build that exists only for the other architecture.

    Combined amd64|arm64 discovery would return v1.17.7, which
    ``get_release_info`` cannot resolve on amd64.
    """
    assert _older_geth_version("v1.17.8", arch="amd64") == "v1.17.6"
    assert _older_geth_version("1.17.8", arch="arm64") == "v1.17.7"


@patch(
    "tests.test_release_info_live._geth_downloads_page",
    return_value=_GETH_ARCH_MISMATCH_PAGE,
)
@patch("tests.test_release_info_live._geth_release_arch", return_value="amd64")
def test_older_geth_version_defaults_to_resolved_arch(_mock_arch, _mock_page) -> None:
    """When *arch* is omitted, discovery uses the host arch being resolved."""
    assert _older_geth_version("v1.17.8") == "v1.17.6"


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "amd64"),
        ("amd64", "amd64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
    ],
)
def test_geth_release_arch_matches_release_lookup(machine: str, expected: str) -> None:
    """Match ``get_client_release_info``: amd64 names stay amd64, others are arm64.

    ``x86_64`` and ``aarch64`` are also what ``get_machine_architecture`` maps.
    """
    with patch("tests.test_release_info_live.platform.machine", return_value=machine):
        assert _geth_release_arch() == expected


@pytest.mark.live
@pytest.mark.parametrize("client,repo", CLIENT_REPOS, ids=[c for c, _ in CLIENT_REPOS])
def test_client_release_info_live(client: str, repo: str | None) -> None:
    """End-to-end release resolution for one client: LATEST, explicit, and older tag.

    This is the production update path:

    * ``update_*.sh`` calls ``python3 -m deploy.common release_info <Client> <tag>``
    * The returned ``download_urls`` are passed to ``wget``

    Steps:

    1. Resolve LATEST and verify each download URL responds.
    2. Resolve again using the version string from step 1 (explicit tag form).
    3. Resolve an older release — Geth from the downloads page for this host's
       architecture, others from the next published GitHub release — and verify
       URLs. For Geth, also assert the resolved URL appears on
       geth.ethereum.org/downloads.
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
        older_tag = _older_release_tag(client, repo, latest["version"])

    if older_tag is None:
        pytest.skip(f"{client}: no older release found")

    older = _release_info(client, older_tag)
    assert _normalize_version(older["version"]) == _normalize_version(older_tag)
    _assert_release_info(older, client)

    if client == "geth":
        assert older["download_urls"][0] in _geth_downloads_page()
