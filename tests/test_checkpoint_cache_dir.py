"""Unit tests for checkpoint cache directory helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "integration"))

from checkpoint_cache_common import ensure_directory, get_cache_root  # noqa: E402


def test_ensure_directory_replaces_file_at_path(tmp_path):
    blocker = tmp_path / "checkpoint_cache"
    blocker.write_text("not-a-dir", encoding="utf-8")
    ensure_directory(str(blocker))
    assert blocker.is_dir()


def test_get_cache_root_honors_env_override(tmp_path, monkeypatch):
    override = tmp_path / "sidecar"
    monkeypatch.setenv("ETHPILLAR_CHECKPOINT_CACHE_DIR", str(override))
    assert get_cache_root() == str(override.resolve())
