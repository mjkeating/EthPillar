"""Tests for binary cache access tracking and pruning."""

from __future__ import annotations

import os
import tempfile
import unittest

from tests.integration.binary_cache_common import (
    ACCESS_LOG_NAME,
    ensure_binary_cache_dir_writable,
    load_accessed_basenames,
    prepare_binary_cache_dir,
    prune_unaccessed_binary_cache,
    record_cache_access,
    reset_access_log,
)


class BinaryCachePruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_prune_keeps_accessed_and_removes_stale(self) -> None:
        reset_access_log(self.cache_dir)
        kept = os.path.join(self.cache_dir, "geth-latest_abc.bin")
        stale = os.path.join(self.cache_dir, "besu-old_def.bin")
        with open(kept, "wb") as handle:
            handle.write(b"keep")
        with open(stale, "wb") as handle:
            handle.write(b"stale")

        record_cache_access(kept, self.cache_dir)

        deleted_count, deleted = prune_unaccessed_binary_cache(self.cache_dir)
        self.assertEqual(deleted_count, 1)
        self.assertEqual(deleted, ["besu-old_def.bin"])
        self.assertTrue(os.path.isfile(kept))
        self.assertFalse(os.path.exists(stale))

    def test_prune_skips_when_access_log_empty(self) -> None:
        stale = os.path.join(self.cache_dir, "orphan_abc.bin")
        with open(stale, "wb") as handle:
            handle.write(b"x")

        deleted_count, deleted = prune_unaccessed_binary_cache(self.cache_dir)
        self.assertEqual(deleted_count, 0)
        self.assertEqual(deleted, [])
        self.assertTrue(os.path.isfile(stale))

    def test_access_log_dedupes_on_read(self) -> None:
        record_cache_access("a.bin", self.cache_dir)
        record_cache_access("a.bin", self.cache_dir)
        record_cache_access("b.bin", self.cache_dir)
        self.assertEqual(load_accessed_basenames(self.cache_dir), {"a.bin", "b.bin"})
        self.assertTrue(os.path.isfile(os.path.join(self.cache_dir, ACCESS_LOG_NAME)))

    def test_record_cache_access_writes_after_root_only_dir_mode(self) -> None:
        os.chmod(self.cache_dir, 0o755)
        log_path = os.path.join(self.cache_dir, ACCESS_LOG_NAME)
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("stale.bin\n")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chmod(log_path, 0o644)
            os.chmod(self.cache_dir, 0o777)
        prepare_binary_cache_dir(self.cache_dir)
        record_cache_access("fresh.bin", self.cache_dir)
        self.assertIn("fresh.bin", load_accessed_basenames(self.cache_dir))

    def test_ensure_writable_preserves_access_log_across_tests(self) -> None:
        """Per-test prep must not wipe the matrix access log (run_test.sh)."""
        record_cache_access("erigon_v3.bin", self.cache_dir)
        ensure_binary_cache_dir_writable(self.cache_dir)
        record_cache_access("reth_v2.bin", self.cache_dir)
        self.assertEqual(
            load_accessed_basenames(self.cache_dir),
            {"erigon_v3.bin", "reth_v2.bin"},
        )
        prepare_binary_cache_dir(self.cache_dir)
        self.assertEqual(load_accessed_basenames(self.cache_dir), set())


if __name__ == "__main__":
    unittest.main()
