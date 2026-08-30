#!/usr/bin/env python3
"""Verify release archives in a clean Linux environment."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from speechloom.runtime_archive import extract_runtime_archive, read_runtime_metadata
from speechloom.cuda_runtime import dependency_issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", type=Path, nargs="+")
    args = parser.parse_args()

    for archive in args.archives:
        metadata = read_runtime_metadata(archive)
        with tempfile.TemporaryDirectory(prefix="speechloom-runtime-verify-") as temporary:
            executable = extract_runtime_archive(
                archive,
                Path(temporary) / "runtime",
                metadata,
            )
            runtime_root = executable.parents[1]
            issues = dependency_issues(
                runtime_root,
                allow_host_driver=metadata.backend == "cuda",
            )
            if issues:
                parser.error(
                    f"{archive.name} has unbundled dependencies: {', '.join(issues)}"
                )
            if metadata.backend == "cpu":
                subprocess.run([str(executable), "--version"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
