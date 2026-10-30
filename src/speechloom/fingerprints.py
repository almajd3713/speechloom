"""Persistent file fingerprints keyed by path, size and modification time."""

from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
from typing import Any

from .artifacts import atomic_write_json, read_json, sha256_file


FINGERPRINT_CACHE_FILENAME = "fingerprints.json"
FINGERPRINT_CACHE_SCHEMA_VERSION = 1
MAX_FINGERPRINT_ENTRIES = 1000


class FingerprintCache:
    """Cache file identities so unchanged files are not hashed again."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] | None = None

    def identity(self, path: Path) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
        key = str(resolved)
        with self._lock:
            cached = self._load().get(key)
            if (
                cached is not None
                and cached["size"] == stat.st_size
                and cached["mtime_ns"] == stat.st_mtime_ns
                and re.fullmatch(r"[0-9a-f]{64}", cached["sha256"])
            ):
                return _identity(key, stat.st_size, stat.st_mtime_ns, cached["sha256"])
        digest = sha256_file(resolved)
        with self._lock:
            entries = self._load()
            entries[key] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest,
                "recorded_ns": time.time_ns(),
            }
            while len(entries) > MAX_FINGERPRINT_ENTRIES:
                del entries[min(entries, key=lambda name: entries[name]["recorded_ns"])]
            try:
                atomic_write_json(
                    self._path,
                    {"schema_version": FINGERPRINT_CACHE_SCHEMA_VERSION, "entries": entries},
                )
            except OSError:
                pass
        return _identity(key, stat.st_size, stat.st_mtime_ns, digest)

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, dict[str, Any]] = {}
        try:
            payload = read_json(self._path)
        except (OSError, ValueError, json.JSONDecodeError):
            payload = {}
        stored = payload.get("entries")
        if payload.get("schema_version") == FINGERPRINT_CACHE_SCHEMA_VERSION and isinstance(
            stored, dict
        ):
            for name, entry in stored.items():
                if _valid_entry(entry):
                    entries[name] = entry
        self._entries = entries
        return entries


def _identity(path: str, size: int, mtime_ns: int, sha256: str) -> dict[str, Any]:
    return {"path": path, "size": size, "mtime_ns": mtime_ns, "sha256": sha256}


def _valid_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("size"), int)
        and isinstance(entry.get("mtime_ns"), int)
        and isinstance(entry.get("sha256"), str)
        and isinstance(entry.get("recorded_ns"), int)
    )
