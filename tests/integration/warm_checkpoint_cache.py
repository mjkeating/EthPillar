#!/usr/bin/env python3
"""Prefetch Beacon checkpoint API responses for integration test networks."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from checkpoint_cache_common import (
    CACHE_MAX_AGE_SEC,
    get_manifest_path,
    OPTIONAL_PREFETCH_REQUESTS,
    REQUIRED_PREFETCH_REQUESTS,
    UPSTREAM_URLS,
    entry_key,
    entry_paths,
    load_manifest,
    network_cache_dir,
    network_needs_refresh,
    save_manifest,
)


def _fetch(
    upstream: str,
    path: str,
    accept: Optional[str],
) -> tuple[int, dict[str, str], bytes]:
    """Download one Beacon API response from *upstream*; raise on non-200."""
    url = f"{upstream.rstrip('/')}{path}"
    headers: dict[str, str] = {"User-Agent": "ethpillar-checkpoint-cache/1.0"}
    if accept:
        headers["Accept"] = accept

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            status = response.status
            response_headers = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "transfer-encoding"}
            }
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = {
            key.lower(): value
            for key, value in exc.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding"}
        }
        body = exc.read()

    if status != 200:
        preview = body[:200].decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {status}: {preview}")

    return status, response_headers, body


def _store_entry(
    network: str,
    method: str,
    path: str,
    accept: Optional[str],
    status: int,
    response_headers: dict[str, str],
    body: bytes,
) -> str:
    """Write one response to disk; return its manifest lookup key."""
    key = entry_key(method, path, accept)
    paths = entry_paths(network, key)
    os.makedirs(os.path.dirname(paths["meta"]), exist_ok=True)

    meta = {
        "method": method.upper(),
        "path": path,
        "accept": accept or "",
        "status": status,
        "headers": response_headers,
        "size": len(body),
    }
    with open(paths["meta"], "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")
    with open(paths["body"], "wb") as handle:
        handle.write(body)
    return key


def warm_network(network: str, manifest: dict[str, Any]) -> None:
    """Prefetch required (and best-effort optional) checkpoint paths for *network*."""
    network = network.upper()
    upstream = UPSTREAM_URLS[network]
    print(f"[checkpoint] Warming {network} from {upstream}")

    entries: dict[str, str] = {}
    prefetch_plan = [
        (True, path, accept) for path, accept in REQUIRED_PREFETCH_REQUESTS
    ] + [
        (False, path, accept) for path, accept in OPTIONAL_PREFETCH_REQUESTS
    ]
    for required, path, accept in prefetch_plan:
        label = accept or "default"
        print(f"[checkpoint]   GET {path} (Accept: {label})")
        try:
            status, headers, body = _fetch(upstream, path, accept)
        except RuntimeError as exc:
            if required:
                raise
            print(f"[checkpoint]   optional skip: {exc}")
            continue
        key = _store_entry(network, "GET", path, accept, status, headers, body)
        entries[f"GET|{path}|{accept or ''}"] = key
        print(f"[checkpoint]   cached {len(body)} bytes")

    manifest.setdefault("networks", {})[network] = {
        "upstream": upstream,
        "warmed_at": int(time.time()),
        "max_age_sec": CACHE_MAX_AGE_SEC,
        "entries": entries,
    }
    save_manifest(manifest)
    print(f"[checkpoint] {network} cache written under {network_cache_dir(network)}")


def main() -> int:
    """Refresh stale network caches; skip networks still within the weekly TTL."""
    manifest = load_manifest()
    refreshed = False

    for network in UPSTREAM_URLS:
        if network_needs_refresh(network, manifest):
            warm_network(network, manifest)
            manifest = load_manifest()
            refreshed = True
        else:
            warmed_at = manifest["networks"][network]["warmed_at"]
            age_days = (time.time() - warmed_at) / 86400
            print(
                f"[checkpoint] {network} cache is fresh "
                f"({age_days:.1f}d old, expires after 7d) — skipping"
            )

    if not refreshed:
        print(f"[checkpoint] All networks fresh; manifest at {get_manifest_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
