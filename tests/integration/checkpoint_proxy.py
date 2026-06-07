#!/usr/bin/env python3
"""Serve cached Beacon checkpoint API responses for one integration test network."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

from checkpoint_cache_common import (
    CHECKPOINT_PROXY_HOST,
    CHECKPOINT_PROXY_PORT,
    HOP_BY_HOP,
    UPSTREAM_URLS,
    entry_key,
    entry_paths,
    load_manifest,
)


class CheckpointProxyHandler(BaseHTTPRequestHandler):
    network: str = "SEPOLIA"
    upstream: str = ""
    entries: dict[str, str] = {}

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[checkpoint-proxy] {self.address_string()} {fmt % args}", flush=True)

    def _accept_candidates(self, accept: str) -> list[str]:
        candidates = [accept, ""]
        lowered = accept.lower()
        if "application/octet-stream" in lowered:
            candidates.append("application/octet-stream")
        if "application/json" in lowered:
            candidates.append("application/json")
        candidates.extend(["application/octet-stream", "application/json"])
        seen: set[str] = set()
        ordered: list[str] = []
        for item in candidates:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _lookup(self, path: str, accept: str) -> Optional[dict[str, Any]]:
        for candidate in self._accept_candidates(accept):
            lookup = f"GET|{path}|{candidate}"
            key = self.entries.get(lookup)
            if not key:
                continue
            paths = entry_paths(self.network, key)
            if not os.path.isfile(paths["meta"]) or not os.path.isfile(paths["body"]):
                continue
            with open(paths["meta"], encoding="utf-8") as handle:
                meta = json.load(handle)
            meta["body_path"] = paths["body"]
            return meta
        return None

    def _proxy_upstream(self, path: str, accept: str) -> tuple[int, dict[str, str], bytes]:
        url = f"{self.upstream.rstrip('/')}{path}"
        headers = {"User-Agent": "ethpillar-checkpoint-proxy/1.0"}
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=300) as response:
            status = response.status
            response_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in HOP_BY_HOP
            }
            return status, response_headers, response.read()

    def _send_response(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes,
        source: str,
    ) -> None:
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-EthPillar-Checkpoint-Cache", source)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        accept = self.headers.get("Accept", "")

        cached = self._lookup(path, accept)
        if cached:
            with open(cached["body_path"], "rb") as handle:
                body = handle.read()
            self._send_response(cached["status"], cached["headers"], body, "hit")
            return

        try:
            status, headers, body = self._proxy_upstream(path, accept)
            self._send_response(status, headers, body, "miss")
        except urllib.error.HTTPError as exc:
            headers = {key: value for key, value in exc.headers.items() if key.lower() not in HOP_BY_HOP}
            self._send_response(exc.code, headers, exc.read(), "miss-error")
        except Exception as exc:
            payload = json.dumps({"code": 502, "message": str(exc)}).encode()
            self._send_response(
                502,
                {"content-type": "application/json"},
                payload,
                "upstream-error",
            )


def build_handler_class(network: str) -> type[CheckpointProxyHandler]:
    manifest = load_manifest()
    info = manifest.get("networks", {}).get(network.upper(), {})
    entries = info.get("entries", {})
    upstream = info.get("upstream") or UPSTREAM_URLS[network.upper()]

    class ConfiguredHandler(CheckpointProxyHandler):
        pass

    ConfiguredHandler.network = network.upper()
    ConfiguredHandler.upstream = upstream
    ConfiguredHandler.entries = entries
    return ConfiguredHandler


def wait_until_ready(network: str, timeout_sec: float = 10.0) -> bool:
    import time
    import urllib.request

    url = f"http://{CHECKPOINT_PROXY_HOST}:{CHECKPOINT_PROXY_PORT}/eth/v1/node/version"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Beacon checkpoint cache proxy")
    parser.add_argument("--network", required=True, help="SEPOLIA or HOODI")
    parser.add_argument("--host", default=CHECKPOINT_PROXY_HOST)
    parser.add_argument("--port", type=int, default=CHECKPOINT_PROXY_PORT)
    args = parser.parse_args()

    network = args.network.upper()
    if network not in UPSTREAM_URLS:
        print(f"Unsupported network: {network}", file=sys.stderr)
        return 1

    handler = build_handler_class(network)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"[checkpoint-proxy] Serving {network} on http://{args.host}:{args.port} "
        f"({len(handler.entries)} cached paths)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
