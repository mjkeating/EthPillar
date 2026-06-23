"""Snapshot GitHub LATEST client tags at install time for integration tests.

Deploy and verify can span several minutes. Re-querying LATEST during verify can
fail if a new release ships mid-test while the binary cache still serves the
version that was current when install started.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SNAPSHOT_PATH = "/tmp/ethpillar-integration-latest-snapshot.json"
ENV_VAR = "ETHPILLAR_INTEGRATION_LATEST_SNAPSHOT"

# Clients that implement deploy.<name>.get_release_info (lowercase module keys).
_SNAPSHOT_CLIENTS = (
    "Reth",
    "Besu",
    "Erigon",
    "Nethermind",
    "Geth",
    "Ethrex",
    "Lighthouse",
    "Teku",
    "Lodestar",
    "Nimbus",
    "Grandine",
    "Prysm",
    "mevboost",
)


def _release_info_module():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from deploy.common import get_client_release_info

    return get_client_release_info


def write_snapshot(path: str = SNAPSHOT_PATH) -> dict[str, str]:
    """Record LATEST release tag per client and return the snapshot mapping."""
    get_client_release_info = _release_info_module()
    snapshot: dict[str, str] = {}
    for client in _SNAPSHOT_CLIENTS:
        key = client.lower()
        try:
            snapshot[key] = get_client_release_info(client, "LATEST")["version"]
        except Exception:
            continue
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, sort_keys=True)
        handle.write("\n")
    return snapshot


def clear_snapshot(path: str = SNAPSHOT_PATH) -> None:
    """Remove the install-time snapshot so checks use live LATEST (e.g. after updates)."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage integration LATEST version snapshots")
    parser.add_argument(
        "action",
        choices=("write", "clear"),
        help="write: snapshot GitHub LATEST tags; clear: remove snapshot file",
    )
    parser.add_argument(
        "--path",
        default=SNAPSHOT_PATH,
        help=f"Snapshot file path (default: {SNAPSHOT_PATH})",
    )
    args = parser.parse_args()
    if args.action == "write":
        write_snapshot(args.path)
    else:
        clear_snapshot(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
