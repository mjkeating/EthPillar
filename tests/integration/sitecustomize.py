import sys
import os
from typing import Any, Callable, Dict, Optional

if os.environ.get('ENABLE_EP_CACHE') == '1':
    try:
        import requests
        import json
        import hashlib
        import tempfile
        import shutil
        import subprocess

        original_get: Callable = requests.get
        original_subprocess_run = subprocess.run

        def hook_subprocess_run(*args, **kwargs):
            try:
                import extract_cache
                return extract_cache.intercept_subprocess_run(*args, **kwargs)
            except ImportError:
                return original_subprocess_run(*args, **kwargs)

        subprocess.run = hook_subprocess_run

        def validate_cache(url: str, cache_file: str, is_github_api: bool) -> bool:
            """Return True if cache is valid, False if it should be invalidated."""
            if not os.path.exists(cache_file):
                return False
            
            # API responses are small, assume valid to avoid rate limit
            if is_github_api:
                return True
                
            # For binary downloads, do a HEAD request to check size
            try:
                # We use original requests API to avoid intercepting our own HEAD request
                head_resp = requests.head(url, allow_redirects=True, timeout=5)
                if head_resp.status_code == 200:
                    remote_size = head_resp.headers.get('content-length')
                    local_size = os.path.getsize(cache_file)
                    if remote_size and int(remote_size) != local_size:
                        print(f"[CACHE] Invalidation: Size mismatch for {url} (Local: {local_size}, Remote: {remote_size})")
                        return False
            except Exception as e:
                print(f"[CACHE] Warning: Could not validate cache for {url}: {e}")
                # If we can't validate (e.g., offline), assume valid if it exists and is >0 bytes
                return os.path.getsize(cache_file) > 0
                
            return True

        def cached_get(url: str, *args: Any, **kwargs: Any) -> Any:
            """
            Intercepts requests.get calls and serves content from a local cache 
            if the URL points to GitHub API or release assets.  This is mainly to avoid 
            Github's rate limits.
            """
            cache_dir = "/ethpillar/tests/integration/cache"
            os.makedirs(cache_dir, exist_ok=True)
            
            is_github_api = url.startswith("https://api.github.com")
            is_github_download = "github.com" in url and ("releases/download" in url or "archive" in url)
            
            if is_github_api or is_github_download:
                key = hashlib.md5(url.encode()).hexdigest()
                ext = ".json" if is_github_api else ".bin"
                
                # Prefix with repo name to make cache contents understandable
                prefix = url.split("/")[-1] if is_github_download else url.split("/")[-3]
                cache_file = os.path.join(cache_dir, f"{prefix}_{key}{ext}")
                
                if validate_cache(url, cache_file, is_github_api):
                    print(f"[CACHE] Hit for {url}")
                    if is_github_api:
                        class MockResponse:
                            def __init__(self, text):
                                self.text = text
                                self.content = text.encode('utf-8')
                                self.status_code = 200
                                self.headers = {'content-length': str(len(self.content))}
                            def json(self): return json.loads(self.text)
                            def raise_for_status(self): pass
                        with open(cache_file, "r") as f:
                            return MockResponse(f.read())
                    else:
                        class MockStreamResponse:
                            def __init__(self, filepath, size):
                                self.filepath = filepath
                                self.status_code = 200
                                self.headers = {'content-length': str(size)}
                                self.raw = open(filepath, "rb")
                            def iter_content(self, chunk_size=1024):
                                # Yield entire file at once since we're reading from
                                # local cache — no need to simulate network I/O
                                self.raw.seek(0)
                                yield self.raw.read()
                            def raise_for_status(self): pass
                        size = os.path.getsize(cache_file)
                        return MockStreamResponse(cache_file, size)
                
                print(f"[CACHE] Miss for {url}, downloading...")
                response = original_get(url, *args, **kwargs)
                
                if response.status_code == 200:
                    if is_github_api:
                        # cache json
                        with tempfile.NamedTemporaryFile("w", delete=False, dir=cache_dir) as f:
                            f.write(response.text)
                            temp_name = f.name
                        os.rename(temp_name, cache_file)
                    else:
                        # For stream=True requests, intercept iter_content
                        if kwargs.get('stream', False):
                            original_iter = response.iter_content
                            
                            temp_fd, temp_path = tempfile.mkstemp(dir=cache_dir)
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
                return response
            
            return original_get(url, *args, **kwargs)

        requests.get = cached_get
    except ImportError:
        pass
