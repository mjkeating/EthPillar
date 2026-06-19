#!/usr/bin/env python3
"""Write small root-owned cache fixtures for act smoke tests (no network)."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time

BINARY_CACHE_DIR = os.environ.get(
    "ACT_SMOKE_BINARY_CACHE_DIR",
    "/ethpillar/tests/integration/act-smoke/cache",
)
CHECKPOINT_ROOT = os.environ.get(
    "ACT_SMOKE_CHECKPOINT_CACHE_DIR",
    "/ethpillar/tests/integration/act-smoke/checkpoint_cache",
)
BINARY_FIXTURE = "fixture_act_smoke.bin"
BODY_BYTES = 4096


def write_root_owned_file(path: str, data: bytes) -> None:
    """Write *data* like sitecustomize mkstemp — root-owned mode 0600, no chmod."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".fixture_", suffix=".tmp")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(temp_path, path)


def write_binary_fixture() -> None:
    write_root_owned_file(
        os.path.join(BINARY_CACHE_DIR, BINARY_FIXTURE),
        secrets.token_bytes(BODY_BYTES),
    )
    print(f"[act-fixture] Wrote root-owned binary cache: {BINARY_FIXTURE}")


def write_checkpoint_fixture() -> None:
    now = int(time.time())
    manifest = {"version": 1, "networks": {}}

    for network in ("SEPOLIA", "HOODI"):
        key = f"fixture_{network.lower()}"
        base = os.path.join(CHECKPOINT_ROOT, network, "entries", key)
        body_path = f"{base}.body"
        meta_path = f"{base}.json"
        body = secrets.token_bytes(BODY_BYTES)
        write_root_owned_file(body_path, body)
        meta = {
            "method": "GET",
            "path": "/eth/v2/debug/beacon/states/finalized",
            "accept": "application/octet-stream",
            "status": 200,
            "headers": {"content-type": "application/octet-stream"},
            "size": len(body),
        }
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2)
            handle.write("\n")
        os.chmod(meta_path, 0o600)
        manifest["networks"][network] = {
            "upstream": f"https://checkpoint-sync.{network.lower()}.ethpandaops.io",
            "warmed_at": now,
            "max_age_sec": 7200,
            "entries": {
                "GET|/eth/v2/debug/beacon/states/finalized|application/octet-stream": key
            },
        }
        print(f"[act-fixture] Wrote root-owned checkpoint entry: {network}/{key}.body")

    manifest_path = os.path.join(CHECKPOINT_ROOT, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    os.chmod(manifest_path, 0o600)
    print(f"[act-fixture] Wrote checkpoint manifest: {manifest_path}")


def main() -> int:
    write_binary_fixture()
    write_checkpoint_fixture()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
