"""Unit tests for checkpoint cache directory helpers."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "integration"))

from checkpoint_cache_common import (  # noqa: E402
    CACHE_MAX_AGE_SEC,
    ensure_directory,
    get_cache_root,
    network_needs_refresh,
    save_manifest,
)


def test_ensure_directory_replaces_file_at_path(tmp_path):
    blocker = tmp_path / "checkpoint_cache"
    blocker.write_text("not-a-dir", encoding="utf-8")
    ensure_directory(str(blocker))
    assert blocker.is_dir()


def test_get_cache_root_honors_env_override(tmp_path, monkeypatch):
    override = tmp_path / "sidecar"
    monkeypatch.setenv("ETHPILLAR_CHECKPOINT_CACHE_DIR", str(override))
    assert get_cache_root() == str(override.resolve())


def test_network_needs_refresh_after_twenty_hours(tmp_path, monkeypatch):
    monkeypatch.setenv("ETHPILLAR_CHECKPOINT_CACHE_DIR", str(tmp_path))
    save_manifest(
        {
            "networks": {
                "SEPOLIA": {
                    "warmed_at": time.time() - CACHE_MAX_AGE_SEC - 1,
                    "entries": {"path": "cached"},
                }
            }
        }
    )
    assert network_needs_refresh("SEPOLIA") is True


def test_network_needs_refresh_within_twenty_hours(tmp_path, monkeypatch):
    monkeypatch.setenv("ETHPILLAR_CHECKPOINT_CACHE_DIR", str(tmp_path))
    save_manifest(
        {
            "networks": {
                "HOODI": {
                    "warmed_at": time.time() - CACHE_MAX_AGE_SEC + 60,
                    "entries": {"path": "cached"},
                }
            }
        }
    )
    assert network_needs_refresh("HOODI") is False
