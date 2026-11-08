"""Bounded retries for flaky GitHub and release-asset HTTP.

This module is the only retry implementation. Shell callers use
``python -m deploy.http_retry`` (see ``helpers/github_fetch.sh``).

Policy:

* 4 attempts
* 10s connect timeout and 60s read timeout per attempt
* release-archive attempts also stop after 3600s overall
* backoff of 1s, 2s, then 4s between attempts
* retry timeouts, connection errors, HTTP 429, and HTTP 5xx
* fail immediately on HTTP 404 and every other 4xx

Checksum checks stay with the caller. A failed attempt must not be treated
as a successful download. ``api.github.com`` requests send ``GITHUB_TOKEN``
when it is set; release-asset hosts do not, because GitHub rejects that
header with HTTP 401.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

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


def headers_for_url(url: str, extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Headers for one GitHub request.

    ``api.github.com`` receives ``Authorization: Bearer $GITHUB_TOKEN`` when
    the variable is set. Release-asset and raw-content hosts do not.
    ``extra`` overrides either value, including User-Agent.

    Args:
        url: Request URL.
        extra: Caller headers such as ``Accept``.

    Returns:
        Header mapping for the request.
    """
    headers = {"User-Agent": USER_AGENT}
    host = (urlparse(url).hostname or "").lower()
    if host == "api.github.com":
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def download_to_path(
    url: str,
    dest_path: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    label: Optional[str] = None,
    show_progress: bool = False,
    on_attempt: Optional[Callable[[], None]] = None,
    on_chunk: Optional[Callable[[bytes], None]] = None,
    before_commit: Optional[Callable[[], None]] = None,
    get: Optional[Callable[..., Any]] = None,
) -> None:
    """Download ``url`` to ``dest_path`` with the shared retry policy.

    The body is written to ``dest_path + ".partial"`` and renamed into place
    only after the body is complete and ``before_commit`` returns. A timeout,
    connection error, HTTP 429, or HTTP 5xx retries the whole download. HTTP
    404 and other 4xx fail immediately. Each failed attempt deletes the
    partial file. ``dest_path`` is left unchanged until the rename.

    Args:
        url: URL to download.
        dest_path: Final file path.
        headers: Request headers. Defaults to :func:`headers_for_url`.
        label: Text included in errors. Defaults to the URL.
        show_progress: Draw a byte progress bar on stderr.
        on_attempt: Called at the start of every attempt.
        on_chunk: Called with each non-empty body chunk of the current attempt.
        before_commit: Called after a complete body is on disk, before rename.
        get: Callable with the ``requests.get`` signature. Defaults to
            :func:`requests.get`.

    Raises:
        AttemptsExhausted: Every attempt failed with a retryable error.
        requests.RequestException: A non-retryable HTTP or network error.
    """
    getter = get or requests.get
    request_headers = dict(headers) if headers is not None else headers_for_url(url)
    request_headers.setdefault("User-Agent", USER_AGENT)
    description = label or url
    partial = dest_path + ".partial"
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _remove_file(partial)
    progress = None
    if show_progress:
        from tqdm import tqdm as progress

    def _past_deadline(started: float) -> None:
        if time.monotonic() - started >= HTTP_STREAM_DEADLINE:
            raise requests.Timeout(
                f"exceeded {HTTP_STREAM_DEADLINE}s overall download timeout"
            )

    def _attempt() -> None:
        if on_attempt is not None:
            on_attempt()
        _remove_file(partial)
        response = None
        bar = None
        try:
            response = getter(
                url,
                stream=True,
                headers=request_headers,
                timeout=HTTP_TIMEOUT,
            )
            status = getattr(response, "status_code", None)
            if is_retryable_status(status):
                raise requests.HTTPError(f"HTTP {status} for {url}", response=response)
            response.raise_for_status()
            total = 0
            try:
                total = int(response.headers.get("content-length", 0) or 0)
            except (TypeError, ValueError):
                total = 0
            if progress is not None:
                bar = progress(total=total, unit="B", unit_scale=True)
            started = time.monotonic()
            _past_deadline(started)
            with open(partial, "wb") as handle:
                for chunk in response.iter_content(1024 * 64):
                    _past_deadline(started)
                    if not chunk:
                        continue
                    if bar is not None:
                        bar.update(len(chunk))
                    handle.write(chunk)
                    if on_chunk is not None:
                        on_chunk(chunk)
            _past_deadline(started)
            if before_commit is not None:
                before_commit()
            os.replace(partial, dest_path)
        finally:
            if bar is not None:
                bar.close()
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            if os.path.exists(partial):
                _remove_file(partial)

    retry_call(_attempt, description=f"{description} ({url})" if label else url)


def fetch_to_stdout(url: str, *, extra_headers: Optional[Mapping[str, str]] = None) -> None:
    """Write the successful response body to stdout.

    Retries happen before any bytes are written, so a later attempt cannot
    append to a partial body already printed.

    Args:
        url: Request URL.
        extra_headers: Caller headers merged over :func:`headers_for_url`.
    """
    data = read_url_bytes(requests.get, url, headers=headers_for_url(url, extra_headers))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _parse_headers(items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise argparse.ArgumentTypeError(f"header must be 'Name: value', got {item!r}")
        name, value = item.split(":", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            raise argparse.ArgumentTypeError(f"header must be 'Name: value', got {item!r}")
        headers[name] = value
    return headers


def _cli_failure(exc: BaseException, url: str) -> int:
    """Print a final error to stderr and return a non-zero status."""
    if isinstance(exc, AttemptsExhausted):
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int):
            print(f"ERROR: GitHub request failed (HTTP {status}) for {url}", file=sys.stderr)
            return 1
    print(f"ERROR: GitHub request failed for {url}: {exc}", file=sys.stderr)
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point for ``python -m deploy.http_retry``.

    ``fetch URL`` writes the body to stdout. ``download URL DEST`` renames a
    ``.partial`` file onto ``DEST`` only after the body is complete. Retry
    notices and the final error go to stderr.

    Args:
        argv: Arguments excluding the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit status. ``0`` on success, ``1`` on a request failure.
    """
    parser = argparse.ArgumentParser(prog="python -m deploy.http_retry")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_parser = sub.add_parser("fetch", help="Print a URL body to stdout")
    fetch_parser.add_argument("url")
    fetch_parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        metavar="NAME: VALUE",
        help="Extra request header. Repeat for more than one.",
    )

    download_parser = sub.add_parser("download", help="Save a URL to DEST")
    download_parser.add_argument("url")
    download_parser.add_argument("dest")
    download_parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        metavar="NAME: VALUE",
        help="Extra request header. Repeat for more than one.",
    )

    try:
        args = parser.parse_args(argv)
        extra = _parse_headers(args.header)
    except (argparse.ArgumentError, argparse.ArgumentTypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "fetch":
            fetch_to_stdout(args.url, extra_headers=extra)
        else:
            download_to_path(
                args.url,
                args.dest,
                headers=headers_for_url(args.url, extra),
                label=os.path.basename(args.dest) or args.url,
            )
    except (AttemptsExhausted, requests.RequestException, OSError) as exc:
        return _cli_failure(exc, args.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
