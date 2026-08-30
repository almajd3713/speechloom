#!/usr/bin/env python3
"""Bundle CUDA user-space libraries needed by a native runtime prefix."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from speechloom.cuda_runtime import (  # noqa: E402
    HOST_DRIVER_LIBRARIES,
    dependency_issues,
    is_cuda_redistributable,
    iter_elf_files,
    linked_dependencies,
)
from speechloom.errors import SetupError  # noqa: E402


REAL_EXECUTABLE = "nemo-speech.bin"


def bundle_cuda_runtime(prefix: Path, licenses: tuple[Path, ...]) -> tuple[Path, ...]:
    prefix = prefix.resolve()
    executable = prefix / "bin/nemo-speech"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SetupError(f"CUDA runtime executable is missing or not executable: {executable}")
    if not licenses:
        raise SetupError("At least one NVIDIA CUDA license file is required")

    library_dir = prefix / "lib"
    library_dir.mkdir(parents=True, exist_ok=True)
    queue = list(iter_elf_files(prefix))
    inspected: set[Path] = set()
    bundled: list[Path] = []
    while queue:
        candidate = queue.pop(0).resolve()
        if candidate in inspected:
            continue
        inspected.add(candidate)
        for dependency in linked_dependencies(candidate, library_dir):
            if dependency.path is None:
                if dependency.name in HOST_DRIVER_LIBRARIES:
                    continue
                raise SetupError(f"Required shared library is unavailable: {dependency.name}")
            if not is_cuda_redistributable(dependency.name):
                continue
            source = dependency.path.resolve()
            try:
                source.relative_to(library_dir.resolve())
                continue
            except ValueError:
                pass
            destination = library_dir / source.name
            if not destination.exists():
                shutil.copy2(source, destination)
                bundled.append(destination)
            alias = library_dir / dependency.name
            if alias != destination:
                if alias.exists() or alias.is_symlink():
                    if alias.resolve() != destination.resolve():
                        raise SetupError(f"Conflicting CUDA library alias: {alias}")
                else:
                    alias.symlink_to(destination.name)
            queue.append(destination)

    issues = dependency_issues(prefix, allow_host_driver=True)
    if issues:
        raise SetupError(f"CUDA runtime still has unbundled dependencies: {', '.join(issues)}")
    _install_licenses(prefix, licenses)
    _install_launcher(executable)
    return tuple(bundled)


def _install_licenses(prefix: Path, licenses: tuple[Path, ...]) -> None:
    destination = prefix / "share/licenses/cuda"
    destination.mkdir(parents=True, exist_ok=True)
    for source in licenses:
        if not source.is_file():
            raise SetupError(f"CUDA runtime license file is missing: {source}")
        name = "NCCL-copyright" if source.name == "copyright" else source.name
        shutil.copy2(source, destination / name)


def _install_launcher(executable: Path) -> None:
    real_executable = executable.with_name(REAL_EXECUTABLE)
    if real_executable.exists():
        raise SetupError(f"CUDA runtime launcher target already exists: {real_executable}")
    executable.rename(real_executable)
    executable.write_text(
        "#!/bin/sh\n"
        "runtime_bin_dir=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
        "LD_LIBRARY_PATH=\"${runtime_bin_dir}/../lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}\"\n"
        "export LD_LIBRARY_PATH\n"
        "exec \"${runtime_bin_dir}/nemo-speech.bin\" \"$@\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--license", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        bundled = bundle_cuda_runtime(args.prefix, tuple(args.license))
    except SetupError as exc:
        parser.error(str(exc))
    for path in bundled:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
