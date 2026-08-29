#!/usr/bin/env python3
"""Create a deterministic Speechloom native-runtime release archive."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from speechloom.runtime_archive import RuntimeArchiveMetadata, build_runtime_archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--features", default="asr")
    args = parser.parse_args()

    features = tuple(sorted({item.strip() for item in args.features.split(",") if item.strip()}))
    short_revision = args.revision[:12]
    filename = f"speechloom-runtime-{short_revision}-{args.backend}-linux-x86_64.tar.gz"
    output = args.output_dir / filename
    metadata = RuntimeArchiveMetadata(
        backend=args.backend,
        system="linux",
        architecture="x86_64",
        revision=args.revision,
        features=features,
    )
    checksum = build_runtime_archive(
        args.prefix,
        output,
        metadata,
        source_date_epoch=int(os.environ.get("SOURCE_DATE_EPOCH", "0")),
    )
    checksum_file = output.with_suffix(output.suffix + ".sha256")
    checksum_file.write_text(f"{checksum}  {output.name}\n", encoding="utf-8")
    print(output)
    print(checksum_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
