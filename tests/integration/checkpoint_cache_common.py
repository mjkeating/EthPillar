"""Checkpoint sync cache paths, manifest I/O, and URL selection for integration tests.

Warmed responses live under ``checkpoint_cache/`` (gitignored). Each test container
mounts the cache read-only and may start :mod:`checkpoint_proxy` to serve them on
localhost, avoiding repeated WAN downloads during the integration matrix.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

CACHE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoint_cache")
MANIFEST_PATH = os.path.join(CACHE_ROOT, "manifest.json")
CACHE_MAX_AGE_SEC = 7 * 24 * 60 * 60

CHECKPOINT_PROXY_HOST = "127.0.0.1"
CHECKPOINT_PROXY_PORT = 19595

UPSTREAM_URLS = {
    "SEPOLIA": "https://checkpoint-sync.sepolia.ethpandaops.io",
    "HOODI": "https://checkpoint-sync.hoodi.ethpandaops.io",
}

REQUIRED_PREFETCH_REQUESTS = [
    ("/eth/v1/node/version", None),
    ("/eth/v1/beacon/genesis", "application/json"),
    ("/eth/v1/config/spec", "application/json"),
    ("/eth/v2/debug/beacon/states/finalized", "application/octet-stream"),
]

# Not all checkpoint providers expose every path; proxy falls back to upstream on miss.
OPTIONAL_PREFETCH_REQUESTS = [
    ("/eth/v1/beacon/states/finalized", "application/json"),
    ("/eth/v1/beacon/blocks/finalized", "application/json"),
    ("/eth/v2/debug/beacon/blocks/finalized", "application/octet-stream"),
]

HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
})


def entry_key(method: str, path: str, accept: Optional[str]) -> str:
    """Stable cache lookup key for an HTTP method, path, and Accept header."""
    raw = f"{method.upper()}|{path}|{accept or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def network_cache_dir(network: str) -> str:
    """Absolute directory holding cached entries for *network* (e.g. ``SEPOLIA``)."""
    return os.path.join(CACHE_ROOT, network.upper())


def entry_paths(network: str, key: str) -> dict[str, str]:
    """Return on-disk paths for one cached response's metadata and body files."""
    base = os.path.join(network_cache_dir(network), "entries", key)
    return {"meta": f"{base}.json", "body": f"{base}.body"}


def load_manifest() -> dict[str, Any]:
    """Load ``manifest.json``; return an empty manifest if missing or corrupt."""
    if not os.path.isfile(MANIFEST_PATH):
        return {"version": 1, "networks": {}}
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "networks": {}}


def save_manifest(manifest: dict[str, Any]) -> None:
    """Persist the checkpoint cache manifest to ``MANIFEST_PATH``."""
    os.makedirs(CACHE_ROOT, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")


def network_needs_refresh(network: str, manifest: Optional[dict[str, Any]] = None) -> bool:
    """Return True when *network* cache is missing, empty, or older than ``CACHE_MAX_AGE_SEC``."""
    manifest = manifest or load_manifest()
    info = manifest.get("networks", {}).get(network.upper())
    if not info:
        return True
    if time.time() - info.get("warmed_at", 0) > CACHE_MAX_AGE_SEC:
        return True
    if not info.get("entries"):
        return True
    return False


def network_cache_usable(network: str) -> bool:
    """Return True when the local proxy should be used for *network* checkpoint sync."""
    if os.environ.get("ENABLE_CHECKPOINT_CACHE") != "1":
        return False
    manifest = load_manifest()
    info = manifest.get("networks", {}).get(network.upper())
    if not info or not info.get("entries"):
        return False
    if time.time() - info.get("warmed_at", 0) > CACHE_MAX_AGE_SEC:
        return False
    return True


def proxy_base_url() -> str:
    """Base URL of the per-container checkpoint proxy."""
    return f"http://{CHECKPOINT_PROXY_HOST}:{CHECKPOINT_PROXY_PORT}"


def checkpoint_sync_url_for_network(network: str) -> str:
    """Checkpoint sync URL for deploy: local proxy when cache is usable, else ethpandaops."""
    if network_cache_usable(network.upper()):
        return proxy_base_url()
    return UPSTREAM_URLS.get(network.upper(), "")
