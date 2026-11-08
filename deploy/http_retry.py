"""Bounded retries for flaky GitHub and release-asset HTTP.

Policy (kept in sync with ``helpers/github_fetch.sh``):

* 4 attempts
* 10s connect timeout and 60s read timeout per attempt
  (curl uses the 60s value as an overall cap for small API bodies, and as a
  stall timeout for release archives)
* release-archive attempts also stop after 3600s overall
* backoff of 1s, 2s, then 4s between attempts
* retry timeouts, connection errors, HTTP 429, and HTTP 5xx
* fail immediately on HTTP 404 and every other 4xx

Checksum checks stay with the caller. A failed attempt must not be treated
as a successful download.
"""

from __future__ import annotations

import sys
import time
import urllib.error
from typing import Any, Callable

import requests

HTTP_ATTEMPTS = 4
HTTP_CONNECT_TIMEOUT = 10
HTTP_READ_TIMEOUT = 60
HTTP_STREAM_DEADLINE = 3600
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
HTTP_BACKOFF_SECONDS = (1, 2, 4)
USER_AGENT = "ethpillar"


class AttemptsExhausted(requests.RequestException):
    """Every retryable attempt failed.

    Attributes:
        attempts: How many attempts ran.
        last: The last error, or an ``HTTP <status>`` description.
    """

    def __init__(self, attempts: int, last: object, description: str = "") -> None:
        self.attempts = attempts
        self.last = last
        message = f"GitHub request failed after {attempts} attempts"
        if description:
            message += f" for {description}"
        message += f": {last}"
        super().__init__(message)


def is_github_host(url: str) -> bool:
    """Return True when ``url`` points at GitHub or a GitHub content host."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return (
        host == "github.com"
        or host.endswith(".github.com")
        or host == "githubusercontent.com"
        or host.endswith(".githubusercontent.com")
    )


def is_retryable_status(status: object) -> bool:
    """Return True for HTTP 429 and 5xx. Other codes, including 404, are not."""
    return isinstance(status, int) and (status == 429 or 500 <= status <= 599)


def is_retryable_exception(exc: BaseException) -> bool:
    """Return True when ``exc`` is a timeout, connection error, 429, or 5xx."""
    if isinstance(exc, urllib.error.HTTPError):
        return is_retryable_status(getattr(exc, "code", None))
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
        return is_retryable_status(status)
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.exceptions.ContentDecodingError)):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return False


def backoff_seconds(failed_attempt: int) -> int:
    """Seconds to wait after 1-based ``failed_attempt`` before trying again."""
    index = min(max(failed_attempt, 1), len(HTTP_BACKOFF_SECONDS)) - 1
    return HTTP_BACKOFF_SECONDS[index]


def _close_quietly(result: object) -> None:
    close = getattr(result, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        pass


def _note_retry(description: str, detail: object, failed_attempt: int) -> None:
    wait = backoff_seconds(failed_attempt)
    print(
        f"GitHub request failed for {description} ({detail}); "
        f"retrying in {wait}s (attempt {failed_attempt}/{HTTP_ATTEMPTS})...",
        file=sys.stderr,
        flush=True,
    )
    time.sleep(wait)


def retry_call(operation: Callable[[], Any], *, description: str) -> Any:
    """Run ``operation`` up to :data:`HTTP_ATTEMPTS` times.

    When ``operation`` returns an object with an integer ``status_code`` of
    429 or 5xx, that result is discarded and retried. The last such response
    raises :class:`AttemptsExhausted` so the caller does not keep a failed
    body. HTTP 404 and other 4xx propagate immediately.

    Args:
        operation: Zero-argument callable that performs one attempt.
        description: Short label included in retry and final error text.

    Returns:
        Whatever ``operation`` returns on a non-retryable success.

    Raises:
        AttemptsExhausted: The retry budget was spent on transient failures.
        Exception: A non-retryable error from ``operation``, unchanged.
    """
    last: object = None
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            result = operation()
        except Exception as exc:
            if not is_retryable_exception(exc):
                raise
            last = exc
            if attempt == HTTP_ATTEMPTS:
                raise AttemptsExhausted(attempt, exc, description) from exc
            _note_retry(description, exc, attempt)
            continue
        status = getattr(result, "status_code", None)
        if is_retryable_status(status):
            last = f"HTTP {status}"
            _close_quietly(result)
            if attempt == HTTP_ATTEMPTS:
                raise AttemptsExhausted(attempt, last, description)
            _note_retry(description, last, attempt)
            continue
        return result
    raise AttemptsExhausted(HTTP_ATTEMPTS, last or "unknown error", description)


def get_with_retry(get: Callable[..., Any], url: str, **kwargs: Any) -> Any:
    """GET ``url`` through ``get`` using the shared retry policy.

    A User-Agent is added when the caller did not set one. Callers that pass
    their own headers (including a GitHub API token) keep them. Timeouts
    default to :data:`HTTP_TIMEOUT` and are not taken from a shorter caller
    value, so every attempt uses the same connect and read limits.

    Args:
        get: Callable with the ``requests.get`` signature.
        url: Request URL.
        **kwargs: Forwarded to ``get`` except for the enforced timeout.

    Returns:
        The successful response object.
    """
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", USER_AGENT)
    kwargs["headers"] = headers
    kwargs["timeout"] = HTTP_TIMEOUT

    def _once() -> Any:
        return get(url, **kwargs)

    return retry_call(_once, description=url)


def read_url_bytes(get: Callable[..., Any], url: str, **kwargs: Any) -> bytes:
    """GET ``url`` and return the body, raising if the status is not successful.

    Args:
        get: Callable with the ``requests.get`` signature.
        url: Request URL.
        **kwargs: Forwarded to :func:`get_with_retry`.

    Returns:
        Response body bytes. Empty when the server sent an empty body.
    """
    response = get_with_retry(get, url, **kwargs)
    response.raise_for_status()
    content = getattr(response, "content", b"")
    return content if isinstance(content, bytes) else bytes(content or b"")
