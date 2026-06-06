import os
import re
import sys
from typing import Any, Callable
from urllib.parse import unquote, urlparse

if os.environ.get("ENABLE_EP_CACHE") == "1":
    try:
        import hashlib
        import json
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

        def cache_file_path(url: str, is_binary: bool) -> str:
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
            ext = ".bin" if is_binary else ".txt"
            return os.path.join(CACHE_DIR, f"{prefix}_{key}{ext}")

        def validate_cache(url: str, cache_file: str, is_binary: bool) -> bool:
            """Return True if cache is valid, False if it should be invalidated."""
            if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
                return False

            # Small text/JSON/HTML responses: trust the cache to avoid extra API calls.
            if not is_binary:
                return True

            try:
                head_resp = requests.head(url, allow_redirects=True, timeout=5)
                if head_resp.status_code == 200:
                    remote_size = head_resp.headers.get("content-length")
                    local_size = os.path.getsize(cache_file)
                    if remote_size and int(remote_size) != local_size:
                        print(
                            f"[CACHE] Invalidation: Size mismatch for {url} "
                            f"(Local: {local_size}, Remote: {remote_size})"
                        )
                        return False
            except Exception as exc:
                print(f"[CACHE] Warning: Could not validate cache for {url}: {exc}")
                return os.path.getsize(cache_file) > 0

            return True

        class MockTextResponse:
            def __init__(self, text: str):
                self.text = text
                self.content = text.encode("utf-8")
                self.status_code = 200
                self.headers = {"content-length": str(len(self.content))}

            def json(self):
                return json.loads(self.text)

            def raise_for_status(self):
                pass

        class MockStreamResponse:
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

        def read_cached_response(cache_file: str, is_binary: bool) -> Any:
            if is_binary:
                size = os.path.getsize(cache_file)
                return MockStreamResponse(cache_file, size)
            with open(cache_file, "r", encoding="utf-8") as handle:
                return MockTextResponse(handle.read())

        def write_cached_response(response: Any, cache_file: str, is_binary: bool, stream: bool) -> None:
            if is_binary:
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
                return

            with tempfile.NamedTemporaryFile("w", delete=False, dir=CACHE_DIR, encoding="utf-8") as handle:
                handle.write(response.text)
                temp_name = handle.name
            os.rename(temp_name, cache_file)

        def cached_get(url: str, *args: Any, **kwargs: Any) -> Any:
            """Intercept requests.get and serve integration-test downloads from a local cache."""
            if not should_cache(url):
                return original_get(url, *args, **kwargs)

            os.makedirs(CACHE_DIR, exist_ok=True)
            is_binary = is_binary_request(url, kwargs)
            cache_file = cache_file_path(url, is_binary)

            if validate_cache(url, cache_file, is_binary):
                print(f"[CACHE] Hit for {url}")
                return read_cached_response(cache_file, is_binary)

            print(f"[CACHE] Miss for {url}, downloading...")
            response = original_get(url, *args, **kwargs)
            if response.status_code == 200:
                write_cached_response(response, cache_file, is_binary, kwargs.get("stream", False))
            return response

        requests.get = cached_get
    except ImportError:
        pass
