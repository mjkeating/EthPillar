"""Tests for binary cache access tracking and pruning."""

from __future__ import annotations

import os
import tempfile
import unittest

from tests.integration.binary_cache_common import (
    ACCESS_LOG_NAME,
    load_accessed_basenames,
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


if __name__ == "__main__":
    unittest.main()
