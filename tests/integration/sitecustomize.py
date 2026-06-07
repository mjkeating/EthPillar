"""Python startup hook: transparent binary download cache for integration tests.

Loaded via ``PYTHONPATH`` when ``ENABLE_EP_CACHE=1``. Patches ``requests.get`` so
release tarballs/binaries may be served from disk after a live ``HEAD`` check.
GitHub API and other metadata requests are never cached.
"""
import os
import re
import sys
from typing import Any, Callable
from urllib.parse import unquote, urlparse

if os.environ.get("ENABLE_EP_CACHE") == "1":
    try:
        import hashlib
        import tempfile

        import requests

        CACHE_DIR = "/ethpillar/tests/integration/cache"
        LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

        original_get: Callable = requests.get

        def should_cache(url: str) -> bool:
            """Cache remote HTTP(S) downloads used during integration tests."""
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            return (parsed.hostname or "").lower() not in LOCAL_HOSTS

        def is_binary_request(url: str, kwargs: dict[str, Any]) -> bool:
            """Binary assets are streamed; metadata/API/HTML responses are not."""
            if kwargs.get("stream"):
                return True
            path = unquote(urlparse(url).path).lower()
            return path.endswith(
                (".tar.gz", ".tar.xz", ".tar.zst", ".tgz", ".zip", ".bin", ".deb", ".rpm")
            )

        def cache_file_path(url: str) -> str:
            """Build a stable, human-readable cache path from the URL."""
            key = hashlib.md5(url.encode()).hexdigest()
            path = unquote(urlparse(url).path).rstrip("/")
            basename = path.split("/")[-1] if path else ""
            if basename:
                prefix = basename
            else:
                segments = [segment for segment in path.split("/") if segment]
                prefix = segments[-1] if segments else key[:8]
            prefix = re.sub(r"[^\w.\-+]", "_", prefix)[:120]
            return os.path.join(CACHE_DIR, f"{prefix}_{key}.bin")

        def validate_binary_cache(url: str, cache_file: str) -> bool:
            """Confirm the remote URL is live and still matches the cached artifact."""
            if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
                return False

            try:
                head_resp = requests.head(url, allow_redirects=True, timeout=15)
            except Exception as exc:
                print(f"[CACHE] Invalidation: could not reach {url}: {exc}")
                return False

            if head_resp.status_code != 200:
                print(
                    f"[CACHE] Invalidation: remote returned {head_resp.status_code} for {url}"
                )
                return False

            remote_size = head_resp.headers.get("content-length")
            local_size = os.path.getsize(cache_file)
            if remote_size and int(remote_size) != local_size:
                print(
                    f"[CACHE] Invalidation: size mismatch for {url} "
                    f"(local: {local_size}, remote: {remote_size})"
                )
                return False

            return True

        class MockStreamResponse:
            """Minimal response object replaying a cached binary from disk."""

            def __init__(self, filepath: str, size: int):
                self.filepath = filepath
                self.status_code = 200
                self.headers = {"content-length": str(size)}
                self.raw = open(filepath, "rb")

            def iter_content(self, chunk_size=1024):
                self.raw.seek(0)
                yield self.raw.read()

            def raise_for_status(self):
                pass

        def read_cached_response(cache_file: str) -> Any:
            """Open a cached download for streaming via ``iter_content``."""
            size = os.path.getsize(cache_file)
            return MockStreamResponse(cache_file, size)

        def write_cached_response(response: Any, cache_file: str, stream: bool) -> None:
            """Persist a network response to *cache_file* (streaming or buffered)."""
            if stream:
                original_iter = response.iter_content
                temp_fd, temp_path = tempfile.mkstemp(dir=CACHE_DIR)
                temp_file = os.fdopen(temp_fd, "wb")

                def tee_iter_content(chunk_size=1024):
                    try:
                        for chunk in original_iter(chunk_size):
                            temp_file.write(chunk)
                            yield chunk
                    finally:
                        temp_file.close()
                        os.rename(temp_path, cache_file)

                response.iter_content = tee_iter_content
                return

            with tempfile.NamedTemporaryFile("wb", delete=False, dir=CACHE_DIR) as handle:
                handle.write(response.content)
                temp_name = handle.name
            os.rename(temp_name, cache_file)

        def cached_get(url: str, *args: Any, **kwargs: Any) -> Any:
            """Intercept requests.get; only binary release assets may be served from cache."""
            if not should_cache(url):
                return original_get(url, *args, **kwargs)

            # Release metadata / GitHub API responses always hit the network.
            if not is_binary_request(url, kwargs):
                return original_get(url, *args, **kwargs)

            os.makedirs(CACHE_DIR, exist_ok=True)
            cache_file = cache_file_path(url)

            if validate_binary_cache(url, cache_file):
                print(f"[CACHE] Hit for {url}")
                return read_cached_response(cache_file)

            print(f"[CACHE] Miss for {url}, downloading...")
            response = original_get(url, *args, **kwargs)
            response.raise_for_status()
            if response.status_code == 200:
                write_cached_response(response, cache_file, kwargs.get("stream", False))
            return response

        requests.get = cached_get
    except ImportError:
        pass
