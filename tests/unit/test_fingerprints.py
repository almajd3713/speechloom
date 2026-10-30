from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from speechloom.artifacts import file_identity, sha256_file
from speechloom.fingerprints import FingerprintCache


class FingerprintCacheTests(unittest.TestCase):
    def test_second_lookup_reuses_cached_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.gguf"
            source.write_bytes(b"model")
            cache = FingerprintCache(root / "cache" / "fingerprints.json")

            with patch("speechloom.fingerprints.sha256_file", wraps=sha256_file) as hashing:
                first = cache.identity(source)
                second = cache.identity(source)

            self.assertEqual(hashing.call_count, 1)
            self.assertEqual(first, file_identity(source))
            self.assertEqual(second, file_identity(source))

    def test_cache_persists_between_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.gguf"
            source.write_bytes(b"model")
            path = root / "fingerprints.json"
            first = FingerprintCache(path).identity(source)

            with patch("speechloom.fingerprints.sha256_file", wraps=sha256_file) as hashing:
                second = FingerprintCache(path).identity(source)

            self.assertEqual(hashing.call_count, 0)
            self.assertEqual(second, first)

    def test_changed_file_is_rehashed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.gguf"
            source.write_bytes(b"model")
            cache = FingerprintCache(root / "fingerprints.json")

            with patch("speechloom.fingerprints.sha256_file", wraps=sha256_file) as hashing:
                first = cache.identity(source)
                stat = source.stat()
                source.write_bytes(b"a longer model")
                os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
                second = cache.identity(source)

            self.assertEqual(hashing.call_count, 2)
            self.assertNotEqual(second["sha256"], first["sha256"])

    def test_corrupt_cache_file_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.gguf"
            source.write_bytes(b"model")
            path = root / "fingerprints.json"
            path.write_text("not json", encoding="utf-8")

            identity = FingerprintCache(path).identity(source)

            self.assertEqual(identity, file_identity(source))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(len(payload["entries"]), 1)

    def test_cache_keeps_at_most_max_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / f"file-{index}.bin" for index in range(3)]
            for source in sources:
                source.write_bytes(source.name.encode())
            path = root / "fingerprints.json"
            cache = FingerprintCache(path)

            with (
                patch("speechloom.fingerprints.MAX_FINGERPRINT_ENTRIES", 2),
                patch("speechloom.fingerprints.time.time_ns", side_effect=[1, 2, 3]),
            ):
                for source in sources:
                    cache.identity(source)

            entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
            self.assertEqual(len(entries), 2)
            self.assertNotIn(str(sources[0].resolve()), entries)

    def test_unwritable_cache_does_not_fail_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.gguf"
            source.write_bytes(b"model")
            blocker = root / "blocker"
            blocker.write_bytes(b"file")
            cache = FingerprintCache(blocker / "fingerprints.json")

            identity = cache.identity(source)

            self.assertEqual(identity, file_identity(source))


if __name__ == "__main__":
    unittest.main()
