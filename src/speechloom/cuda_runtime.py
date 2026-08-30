"""Shared dependency rules for self-contained CUDA runtime archives."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Iterable

from .errors import SetupError


CUDA_REDISTRIBUTABLE_PREFIXES = (
    "libcublas",
    "libcudart",
    "libcufft",
    "libcurand",
    "libcusolver",
    "libcusparse",
    "libnccl",
    "libnpp",
    "libnvJitLink",
    "libnvrtc",
    "libnvToolsExt",
)
HOST_DRIVER_LIBRARIES = frozenset({"libcuda.so.1", "libnvidia-ml.so.1"})


@dataclass(frozen=True)
class LinkedDependency:
    name: str
    path: Path | None


def is_cuda_redistributable(name: str) -> bool:
    return any(
        name == f"{prefix}.so" or name.startswith(f"{prefix}.so.")
        for prefix in CUDA_REDISTRIBUTABLE_PREFIXES
    )


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def iter_elf_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and is_elf(path):
            yield path


def linked_dependencies(path: Path, library_dir: Path) -> tuple[LinkedDependency, ...]:
    environment = os.environ.copy()
    existing = environment.get("LD_LIBRARY_PATH")
    environment["LD_LIBRARY_PATH"] = str(library_dir) + (f":{existing}" if existing else "")
    result = subprocess.run(
        ["ldd", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(
            f"Could not inspect shared-library dependencies for {path}: "
            f"{result.stdout.strip()}"
        )
    dependencies: list[LinkedDependency] = []
    for line in result.stdout.splitlines():
        if "=>" not in line:
            continue
        name, target = (part.strip() for part in line.split("=>", 1))
        if target.startswith("not found"):
            dependencies.append(LinkedDependency(name, None))
            continue
        resolved = target.split(" (", 1)[0].strip()
        if resolved.startswith("/"):
            dependencies.append(LinkedDependency(name, Path(resolved)))
    return tuple(dependencies)


def dependency_issues(
    runtime_root: Path,
    *,
    allow_host_driver: bool,
) -> tuple[str, ...]:
    """Return missing or externally resolved CUDA runtime dependencies."""

    library_dir = (runtime_root / "lib").resolve()
    issues: set[str] = set()
    elf_files = tuple(iter_elf_files(runtime_root))
    if not elf_files:
        return ("archive contains no ELF runtime files",)
    for path in elf_files:
        for dependency in linked_dependencies(path, library_dir):
            if dependency.path is None:
                if allow_host_driver and dependency.name in HOST_DRIVER_LIBRARIES:
                    continue
                issues.add(dependency.name)
                continue
            if is_cuda_redistributable(dependency.name):
                try:
                    dependency.path.resolve().relative_to(library_dir)
                except ValueError:
                    issues.add(dependency.name)
    return tuple(sorted(issues))
