#!/usr/bin/env python3
"""Validate Speechloom wheel and source-distribution contents before publishing."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
from pathlib import Path, PurePosixPath
import tarfile
from typing import Iterable
import zipfile


class DistributionError(ValueError):
    pass


WHEEL_REQUIRED = {
    "speechloom/__init__.py",
    "speechloom/__main__.py",
    "speechloom/cli.py",
    "speechloom/api/server.py",
    "speechloom/data/registry.json",
    "speechloom/py.typed",
}
SDIST_REQUIRED = {
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "scripts/bootstrap_runtime.sh",
    "src/speechloom/api/server.py",
    "src/speechloom/data/registry.json",
    "src/speechloom/py.typed",
}
BANNED_PARTS = {
    ".cache",
    ".git",
    ".runtime",
    ".venv",
    "__pycache__",
    "models",
    "transcripts",
}
BANNED_NAMES = {"AGENTS.md", "config.ini"}
BANNED_SUFFIXES = {
    ".a",
    ".dll",
    ".dylib",
    ".exe",
    ".gguf",
    ".nemo",
    ".o",
    ".pyc",
    ".so",
}


def validate_distributions(paths: Iterable[Path]) -> tuple[Path, ...]:
    archives = tuple(sorted((path.resolve() for path in paths), key=lambda path: path.name))
    wheels = tuple(path for path in archives if path.suffix == ".whl")
    sdists = tuple(path for path in archives if path.name.endswith(".tar.gz"))
    unexpected = [path.name for path in archives if path not in wheels and path not in sdists]
    if unexpected:
        raise DistributionError(f"Unsupported distribution files: {', '.join(unexpected)}")
    if len(wheels) != 1 or len(sdists) != 1:
        raise DistributionError("Expected exactly one wheel and one .tar.gz source distribution")
    _validate_wheel(wheels[0])
    _validate_sdist(sdists[0])
    return archives


def write_checksums(paths: Iterable[Path], destination: Path) -> None:
    lines = [f"{_sha256(path)}  {path.name}" for path in sorted(paths, key=lambda item: item.name)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_wheel(path: Path) -> None:
    if not path.name.endswith("-py3-none-any.whl"):
        raise DistributionError(f"Wheel is not platform-independent: {path.name}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            _validate_names(names, strip_root=False)
            missing = sorted(WHEEL_REQUIRED - names)
            if missing:
                raise DistributionError(f"Wheel is missing: {', '.join(missing)}")
            if not any(
                name.endswith((".dist-info/LICENSE", ".dist-info/licenses/LICENSE"))
                for name in names
            ):
                raise DistributionError("Wheel does not include the project license")
            metadata_name = _one(names, ".dist-info/METADATA")
            wheel_name = _one(names, ".dist-info/WHEEL")
            entry_points_name = _one(names, ".dist-info/entry_points.txt")
            metadata = BytesParser().parsebytes(archive.read(metadata_name))
            wheel_metadata = archive.read(wheel_name).decode("utf-8")
            entry_points = archive.read(entry_points_name).decode("utf-8")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise DistributionError(f"Could not inspect wheel: {path.name}") from exc

    if metadata.get("Name", "").lower() != "speechloom":
        raise DistributionError("Wheel project name is not speechloom")
    if metadata.get("Requires-Python") != ">=3.10":
        raise DistributionError("Wheel must retain Requires-Python: >=3.10")
    for requirement in metadata.get_all("Requires-Dist", []):
        if "extra ==" not in requirement:
            raise DistributionError(f"Mandatory runtime dependency found: {requirement}")
    if "Root-Is-Purelib: true" not in wheel_metadata or "Tag: py3-none-any" not in wheel_metadata:
        raise DistributionError("Wheel metadata does not declare a pure Python wheel")
    if "speechloom = speechloom.cli:main" not in entry_points:
        raise DistributionError("Wheel lacks the speechloom console entry point")


def _validate_sdist(path: Path) -> None:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            raw_names = {member.name for member in archive.getmembers() if member.isfile()}
    except (OSError, tarfile.TarError) as exc:
        raise DistributionError(f"Could not inspect source distribution: {path.name}") from exc
    normalized = {_strip_sdist_root(name) for name in raw_names}
    _validate_names(normalized, strip_root=False)
    missing = sorted(SDIST_REQUIRED - normalized)
    if missing:
        raise DistributionError(f"Source distribution is missing: {', '.join(missing)}")


def _validate_names(names: Iterable[str], *, strip_root: bool) -> None:
    for raw_name in names:
        name = _strip_sdist_root(raw_name) if strip_root else raw_name
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise DistributionError(f"Distribution contains an unsafe path: {name}")
        if path.name in BANNED_NAMES:
            raise DistributionError(f"Distribution contains private file: {name}")
        if BANNED_PARTS.intersection(path.parts):
            raise DistributionError(f"Distribution contains excluded path: {name}")
        if path.suffix.lower() in BANNED_SUFFIXES or ".so." in path.name.lower():
            raise DistributionError(f"Distribution contains native/model/cache data: {name}")


def _strip_sdist_root(name: str) -> str:
    parts = PurePosixPath(name).parts
    return PurePosixPath(*parts[1:]).as_posix() if len(parts) > 1 else name


def _one(names: Iterable[str], suffix: str) -> str:
    matches = sorted(name for name in names if name.endswith(suffix))
    if len(matches) != 1:
        raise DistributionError(f"Expected one wheel member ending in {suffix}")
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", type=Path, nargs="+")
    parser.add_argument("--checksums", type=Path)
    args = parser.parse_args()
    try:
        archives = validate_distributions(args.archives)
        if args.checksums:
            write_checksums(archives, args.checksums)
    except DistributionError as exc:
        parser.error(str(exc))
    for archive in archives:
        print(f"verified {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
