"""Checkpoint sync cache paths, manifest I/O, and URL selection for integration tests.

Warmed responses live under a host cache directory (default
``~/.cache/ethpillar/checkpoint_cache`` when using the WSL orchestrator, or
``tests/integration/checkpoint_cache`` in-repo on native Linux). Each test
container mounts that directory at :data:`CONTAINER_CACHE_PATH`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from typing import Any, Optional

CACHE_MAX_AGE_SEC = 20 * 60 * 60

# In-container mount point (host cache is bind-mounted here).
CONTAINER_CACHE_PATH = "/ethpillar/tests/integration/checkpoint_cache"

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


def _repo_default_cache_root() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoint_cache")


def _wsl_sidecar_cache_root() -> str:
    return os.path.expanduser("~/.cache/ethpillar/checkpoint_cache")


def _should_use_sidecar_cache(repo_default: str) -> bool:
    """Docker Desktop bind mounts break mkdir/rmdir under the repo on WSL."""
    if os.environ.get("ETHPILLAR_CHECKPOINT_CACHE_DIR"):
        return False
    probes = (
        os.path.abspath(repo_default),
        os.getcwd(),
        os.path.abspath(__file__),
    )
    return any("docker-desktop-bind-mounts" in probe for probe in probes)


def get_cache_root() -> str:
    """Return the host directory used to store warmed checkpoint responses."""
    override = os.environ.get("ETHPILLAR_CHECKPOINT_CACHE_DIR")
    if override:
        return os.path.abspath(override)
    repo_default = _repo_default_cache_root()
    if _should_use_sidecar_cache(repo_default):
        return _wsl_sidecar_cache_root()
    return repo_default


def get_manifest_path() -> str:
    """Absolute path to the checkpoint cache manifest on the host."""
    return os.path.join(get_cache_root(), "manifest.json")


def entry_key(method: str, path: str, accept: Optional[str]) -> str:
    """Stable cache lookup key for an HTTP method, path, and Accept header."""
    raw = f"{method.upper()}|{path}|{accept or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def network_cache_dir(network: str) -> str:
    """Absolute directory holding cached entries for *network* (e.g. ``SEPOLIA``)."""
    return os.path.join(get_cache_root(), network.upper())


def entry_paths(network: str, key: str) -> dict[str, str]:
    """Return on-disk paths for one cached response's metadata and body files."""
    base = os.path.join(network_cache_dir(network), "entries", key)
    return {"meta": f"{base}.json", "body": f"{base}.body"}


def load_manifest() -> dict[str, Any]:
    """Load ``manifest.json``; return an empty manifest if missing or corrupt."""
    manifest_path = get_manifest_path()
    if not os.path.isfile(manifest_path):
        return {"version": 1, "networks": {}}
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "networks": {}}


def ensure_directory(path: str) -> None:
    """Create *path* as a directory, clearing broken prior entries if needed."""
    if os.path.isdir(path):
        return
    if os.path.lexists(path):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.islink(path):
                os.unlink(path)
            else:
                os.remove(path)
        except OSError:
            shutil.rmtree(path, ignore_errors=True)
            if os.path.lexists(path) and not os.path.isdir(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
    try:
        os.makedirs(path, exist_ok=True)
    except FileExistsError:
        if os.path.isdir(path):
            return
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, exist_ok=True)


def ensure_cache_root() -> str:
    """Ensure the host cache directory exists and return its path."""
    cache_root = get_cache_root()
    ensure_directory(cache_root)
    return cache_root


def save_manifest(manifest: dict[str, Any]) -> None:
    """Persist the checkpoint cache manifest to disk."""
    ensure_cache_root()
    manifest_path = get_manifest_path()
    with open(manifest_path, "w", encoding="utf-8") as handle:
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
