#!/usr/bin/env python3
"""Create a registry candidate from built runtime release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from speechloom.registry import Registry
from speechloom.runtime_archive import read_runtime_metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()

    if re.fullmatch(r"[A-Za-z0-9._-]+", args.release_tag) is None:
        parser.error("release tag contains unsafe URL characters")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository) is None:
        parser.error("repository must be an owner/name pair")

    payload = json.loads(args.registry.read_text(encoding="utf-8"))
    runtime_revision = str(payload["runtime"]["revision"])
    runtime_executable = str(payload["runtime"]["executable"])
    runtime_features = {str(item) for item in payload["runtime"]["features"]}
    archives: list[dict[str, object]] = []
    for archive_path in sorted(args.release_dir.glob("*.tar.gz")):
        metadata = read_runtime_metadata(archive_path)
        if metadata.revision != runtime_revision:
            parser.error(f"{archive_path.name} was built from the wrong runtime revision")
        if metadata.executable != runtime_executable:
            parser.error(f"{archive_path.name} uses an unexpected executable path")
        if not set(metadata.features).issubset(runtime_features):
            parser.error(f"{archive_path.name} declares unsupported runtime features")
        digest = _sha256(archive_path)
        archives.append(
            {
                "backend": metadata.backend,
                "system": metadata.system,
                "architecture": metadata.architecture,
                "filename": archive_path.name,
                "url": (
                    f"https://github.com/{args.repository}/releases/download/"
                    f"{args.release_tag}/{archive_path.name}"
                ),
                "sha256": digest,
                "features": list(metadata.features),
                "minimum_free_bytes": max(archive_path.stat().st_size * 3, 536_870_912),
            }
        )
    if not archives:
        parser.error("release directory contains no runtime archives")
    payload["runtime"]["archives"] = archives
    Registry.from_dict(payload)

    temporary = args.registry.with_name(f".{args.registry.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, args.registry)
    finally:
        temporary.unlink(missing_ok=True)
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
