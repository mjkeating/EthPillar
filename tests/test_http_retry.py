"""Unit tests for GitHub download retries.

These mock HTTP and do not touch the network. The live release probe in
``tests/test_release_info_live.py`` is a separate harness and is not the
retry implementation under test.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy import http_retry
from deploy.common import _sha256_from_sidecar, download_file
from deploy.http_retry import (
    HTTP_ATTEMPTS,
    HTTP_BACKOFF_SECONDS,
    HTTP_CONNECT_TIMEOUT,
    HTTP_READ_TIMEOUT,
    HTTP_STREAM_DEADLINE,
    HTTP_TIMEOUT,
    AttemptsExhausted,
    get_with_retry,
    is_github_host,
    is_retryable_exception,
    is_retryable_status,
    retry_call,
)
from manage.grafana import download_grafana_dashboard

ROOT = Path(__file__).resolve().parents[1]
SLEEP = "deploy.http_retry.time.sleep"


def test_retry_policy_constants():
    assert HTTP_ATTEMPTS == 4
    assert HTTP_CONNECT_TIMEOUT == 10
    assert HTTP_READ_TIMEOUT == 60
    assert HTTP_TIMEOUT == (10, 60)
    assert HTTP_BACKOFF_SECONDS == (1, 2, 4)
    assert HTTP_STREAM_DEADLINE == 3600


def test_shell_helper_uses_the_same_policy():
    text = (ROOT / "helpers" / "github_fetch.sh").read_text(encoding="utf-8")
    assert "GITHUB_FETCH_ATTEMPTS=4" in text
    assert "GITHUB_FETCH_CONNECT_TIMEOUT=10" in text
    assert "GITHUB_FETCH_READ_TIMEOUT=60" in text
    assert "GITHUB_FETCH_API_MAX_TIME=60" in text
    assert "GITHUB_FETCH_DOWNLOAD_MAX_TIME=3600" in text
    assert 'GITHUB_FETCH_BACKOFF="1 2 4"' in text


def test_release_lookups_share_one_requests_get():
    """New GitHub calls belong in github_get / stream_download, not a fresh requests.get."""
    text = (ROOT / "deploy" / "common.py").read_text(encoding="utf-8")
    assert text.count("requests.get(") == 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/org/repo/releases/download/v1/a.tar.gz", True),
        ("https://api.github.com/repos/org/repo/releases/latest", True),
        ("https://raw.githubusercontent.com/org/repo/main/file.json", True),
        ("https://objects.githubusercontent.com/github-production-release-asset-1", True),
        ("https://gist.githubusercontent.com/user/id/raw/dashboard.json", True),
        ("https://release-assets.githubusercontent.com/github-production-release-asset", True),
        ("https://geth.ethereum.org/downloads", False),
        ("https://grafana.com/api/dashboards/1", False),
        ("https://artifacts.consensys.net/teku.tar.gz", False),
    ],
)
def test_is_github_host(url: str, expected: bool):
    assert is_github_host(url) is expected


def test_status_classification():
    assert is_retryable_status(429) is True
    assert is_retryable_status(503) is True
    assert is_retryable_status(500) is True
    assert is_retryable_status(404) is False
    assert is_retryable_status(401) is False
    assert is_retryable_status(200) is False
    assert is_retryable_status(None) is False
    assert is_retryable_status(MagicMock()) is False


def test_exception_classification():
    timeout = requests.Timeout("timed out")
    assert is_retryable_exception(timeout) is True
    assert is_retryable_exception(requests.ConnectionError("reset")) is True
    denied = requests.HTTPError("nope")
    denied.response = MagicMock(status_code=404)
    assert is_retryable_exception(denied) is False
    busy = requests.HTTPError("busy")
    busy.response = MagicMock(status_code=503)
    assert is_retryable_exception(busy) is True
    assert is_retryable_exception(ValueError("checksum")) is False


@patch(SLEEP)
def test_retry_call_retries_timeout_then_succeeds(mock_sleep):
    calls = {"n": 0}

    def operation():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.Timeout("read timed out")
        return "ok"

    assert retry_call(operation, description="sidecar") == "ok"
    assert calls["n"] == 2
    mock_sleep.assert_called_once_with(1)


@patch(SLEEP)
def test_retry_call_fails_fast_on_404(mock_sleep):
    response = MagicMock(status_code=404)
    calls = {"n": 0}

    def operation():
        calls["n"] += 1
        return response

    assert retry_call(operation, description="missing") is response
    assert calls["n"] == 1
    mock_sleep.assert_not_called()
    response.close.assert_not_called()


@patch(SLEEP)
def test_retry_call_retries_503_then_returns_200(mock_sleep):
    bad = MagicMock(status_code=503)
    good = MagicMock(status_code=200)
    responses = [bad, good]

    def operation():
        return responses.pop(0)

    assert retry_call(operation, description="asset") is good
    bad.close.assert_called_once()
    mock_sleep.assert_called_once_with(1)


@patch(SLEEP)
def test_retry_call_exhausts_timeouts(mock_sleep):
    def operation():
        raise requests.ConnectionError("connection reset")

    with pytest.raises(AttemptsExhausted, match="after 4 attempts") as caught:
        retry_call(operation, description="mev-boost")
    assert caught.value.attempts == 4
    assert mock_sleep.call_args_list == [((1,),), ((2,),), ((4,),)]


@patch(SLEEP)
def test_get_with_retry_sets_timeout_and_user_agent(mock_sleep):
    sentinel = MagicMock(status_code=200)
    get = MagicMock(return_value=sentinel)
    assert get_with_retry(get, "https://api.github.com/repos/a/b/releases/latest") is sentinel
    mock_sleep.assert_not_called()
    assert get.call_args.args == ("https://api.github.com/repos/a/b/releases/latest",)
    assert get.call_args.kwargs["timeout"] == (10, 60)
    assert get.call_args.kwargs["headers"]["User-Agent"] == "ethpillar"
    assert "Authorization" not in get.call_args.kwargs["headers"]


@patch(SLEEP)
def test_get_with_retry_keeps_caller_authorization(mock_sleep):
    get = MagicMock(return_value=MagicMock(status_code=200))
    get_with_retry(
        get,
        "https://api.github.com/repos/a/b/releases/latest",
        headers={"Authorization": "Bearer secret", "User-Agent": "ethpillar"},
    )
    headers = get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer secret"
    assert headers["User-Agent"] == "ethpillar"
    mock_sleep.assert_not_called()


class _Body:
    def __init__(self, payload: bytes, *, fail_after: bytes = b""):
        self.headers = {"content-length": str(len(payload))}
        self.status_code = 200
        self._payload = payload
        self._fail_after = fail_after

    def raise_for_status(self):
        return None

    def iter_content(self, block_size):  # noqa: ARG002
        if self._fail_after:
            yield self._fail_after
            raise requests.Timeout("The read operation timed out")
        yield self._payload

    def close(self):
        return None


class _Status:
    def __init__(self, status: int):
        self.status_code = status
        self.headers = {}
        self._error = requests.HTTPError(f"HTTP {status}", response=self)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise self._error

    def iter_content(self, block_size):  # noqa: ARG002
        raise AssertionError("error body must not be saved")

    def close(self):
        return None


@patch(SLEEP)
@patch("deploy.common.requests.get")
def test_download_retries_read_timeout_then_verifies_checksum(mock_get, mock_sleep, tmp_path):
    payload = b"client-bytes"
    mock_get.side_effect = [
        _Body(b"", fail_after=b"partial"),
        _Body(payload),
    ]
    dest = tmp_path / "mev-boost.tar.gz"
    dest.write_bytes(b"previous-good")
    download_file(
        "https://github.com/flashbots/mev-boost/releases/download/v1.12/mev-boost.tar.gz",
        str(dest),
        "mev-boost",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert dest.read_bytes() == payload
    assert not (tmp_path / "mev-boost.tar.gz.partial").exists()
    assert mock_get.call_count == 2
    assert mock_get.call_args.kwargs["timeout"] == (10, 60)
    assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "ethpillar"
    assert "Authorization" not in mock_get.call_args.kwargs["headers"]
    mock_sleep.assert_called_once_with(1)


@patch(SLEEP)
@patch("deploy.common.requests.get")
def test_download_fails_fast_on_404_and_keeps_previous_file(mock_get, mock_sleep, tmp_path):
    mock_get.return_value = _Status(404)
    dest = tmp_path / "client.tar.gz"
    dest.write_bytes(b"previous-good")
    with pytest.raises(SystemExit):
        download_file("https://example/client.tar.gz", str(dest), "Client", expected_sha256="ab" * 32)
    assert dest.read_bytes() == b"previous-good"
    assert not (tmp_path / "client.tar.gz.partial").exists()
    mock_get.assert_called_once()
    mock_sleep.assert_not_called()


@patch(SLEEP)
@patch("deploy.common.requests.get")
def test_checksum_mismatch_is_not_retried(mock_get, mock_sleep, tmp_path):
    mock_get.return_value = _Body(b"tampered")
    dest = tmp_path / "client.tar.gz"
    dest.write_bytes(b"previous-good")
    with pytest.raises(SystemExit):
        download_file(
            "https://example/client.tar.gz",
            str(dest),
            "Client",
            expected_sha256="ab" * 32,
        )
    assert dest.read_bytes() == b"previous-good"
    assert not (tmp_path / "client.tar.gz.partial").exists()
    mock_get.assert_called_once()
    mock_sleep.assert_not_called()


@patch(SLEEP)
@patch("deploy.common.requests.get")
def test_download_reports_final_503(mock_get, mock_sleep, tmp_path, capsys):
    mock_get.return_value = _Status(503)
    dest = tmp_path / "mev-boost.tar.gz"
    with pytest.raises(SystemExit):
        download_file("https://github.com/flashbots/mev-boost/releases/download/v1.12/a.tar.gz", str(dest), "mev-boost")
    assert not dest.exists()
    assert not (tmp_path / "mev-boost.tar.gz.partial").exists()
    assert mock_get.call_count == HTTP_ATTEMPTS
    err = capsys.readouterr().out
    assert "after 4 attempts" in err
    assert "503" in err


@patch(SLEEP)
@patch("deploy.common.requests.get")
def test_sha256_sidecar_retries_503_then_parses(mock_get, mock_sleep):
    digest = "ab" * 32
    bad = MagicMock(status_code=503, text="nope")
    good = MagicMock(status_code=200, text=f"{digest}  besu-26.10.2.tar.gz\n")
    good.raise_for_status.return_value = None
    mock_get.side_effect = [bad, good]
    assert _sha256_from_sidecar(
        "https://github.com/besu-eth/besu/releases/download/26.10.2/besu-26.10.2.tar.gz.sha256",
        "besu-26.10.2.tar.gz",
    ) == digest
    assert mock_get.call_count == 2
    assert mock_get.call_args.kwargs["timeout"] == (10, 60)
    assert "Authorization" not in (mock_get.call_args.kwargs.get("headers") or {})
    mock_sleep.assert_called_once_with(1)


@patch(SLEEP)
@patch("deploy.common.requests.get")
def test_sha256_sidecar_404_is_not_retried(mock_get, mock_sleep):
    missing = MagicMock(status_code=404)
    missing.raise_for_status.side_effect = requests.HTTPError("404", response=missing)
    mock_get.return_value = missing
    with pytest.raises(ValueError, match="checksum sidecar"):
        _sha256_from_sidecar("https://github.com/example/missing.sha256", "client.tar.gz")
    mock_get.assert_called_once()
    mock_sleep.assert_not_called()


@patch(SLEEP)
@patch("manage.grafana.requests.get")
def test_grafana_github_dashboard_retries_then_writes(mock_get, mock_sleep, tmp_path):
    bad = MagicMock(status_code=503)
    good = MagicMock(status_code=200, content=b'{"title":"Charon"}')
    good.raise_for_status.return_value = None
    mock_get.side_effect = [bad, good]
    dest = tmp_path / "charon.json"
    download_grafana_dashboard(
        dest,
        "https://raw.githubusercontent.com/ObolNetwork/charon-distributed-validator-node/main/grafana/dashboards/x.json",
    )
    assert dest.read_bytes() == b'{"title":"Charon"}'
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(1)


@patch(SLEEP)
@patch("manage.grafana.requests.get")
def test_grafana_github_dashboard_404_is_not_retried(mock_get, mock_sleep, tmp_path):
    missing = MagicMock(status_code=404)
    missing.raise_for_status.side_effect = requests.HTTPError("404", response=missing)
    mock_get.return_value = missing
    dest = tmp_path / "missing.json"
    with pytest.raises(RuntimeError, match="404"):
        download_grafana_dashboard(dest, "https://raw.githubusercontent.com/org/repo/main/dash.json")
    assert not dest.exists()
    mock_get.assert_called_once()
    mock_sleep.assert_not_called()


def test_grafana_com_dashboard_stays_a_single_urllib_attempt(monkeypatch, tmp_path):
    seen = {}

    class _Resp:
        def read(self):
            return b'{"from":"grafana"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _Resp()

    def reject_requests(*args, **kwargs):
        raise AssertionError("grafana.com download must not use the GitHub retry helper")

    monkeypatch.setattr("manage.grafana.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("manage.grafana.requests.get", reject_requests)
    dest = tmp_path / "dash.json"
    download_grafana_dashboard(dest, "https://grafana.com/api/dashboards/1/revisions/2/download")
    assert dest.read_bytes() == b'{"from":"grafana"}'
    assert seen["timeout"] == 60
    assert "grafana.com" in seen["url"]
