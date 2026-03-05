import sys
import os

if os.environ.get('ENABLE_EP_CACHE') == '1':
    try:
        import requests
        import json
        import hashlib
        import tempfile
        import shutil

        original_get = requests.get

        def cached_get(url, *args, **kwargs):
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
                
                if os.path.exists(cache_file):
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
                                self.raw.seek(0)
                                while True:
                                    chunk = self.raw.read(chunk_size)
                                    if not chunk:
                                        break
                                    yield chunk
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
