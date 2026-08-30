#!/usr/bin/env python3
"""Show, validate, or update Speechloom's synchronized package version."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PACKAGE_INIT = PROJECT_ROOT / "src/speechloom/__init__.py"
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:[a-z]+[0-9]+)?"
)
PYPROJECT_PATTERN = re.compile(
    r'^(?P<prefix>version\s*=\s*")(?P<value>[^"]+)(?P<suffix>"\s*)$',
    flags=re.MULTILINE,
)
PACKAGE_PATTERN = re.compile(
    r'^(?P<prefix>__version__\s*=\s*")(?P<value>[^"]+)(?P<suffix>"\s*)$',
    flags=re.MULTILINE,
)


class VersionError(RuntimeError):
    """Raised when package version files are invalid or inconsistent."""


def current_version(
    pyproject: Path = PYPROJECT,
    package_init: Path = PACKAGE_INIT,
) -> str:
    pyproject_version = _extract_version(pyproject, PYPROJECT_PATTERN)
    package_version = _extract_version(package_init, PACKAGE_PATTERN)
    if pyproject_version != package_version:
        raise VersionError(
            "Package versions are not synchronized: "
            f"pyproject.toml={pyproject_version}, __init__.py={package_version}"
        )
    _validate_version(pyproject_version)
    return pyproject_version


def set_version(
    version: str,
    pyproject: Path = PYPROJECT,
    package_init: Path = PACKAGE_INIT,
) -> tuple[str, str]:
    _validate_version(version)
    pyproject_text = pyproject.read_text(encoding="utf-8")
    package_text = package_init.read_text(encoding="utf-8")
    previous = (
        _extract_from_text(pyproject, pyproject_text, PYPROJECT_PATTERN),
        _extract_from_text(package_init, package_text, PACKAGE_PATTERN),
    )
    updated_pyproject = _replace_version(
        pyproject,
        pyproject_text,
        PYPROJECT_PATTERN,
        version,
    )
    updated_package = _replace_version(
        package_init,
        package_text,
        PACKAGE_PATTERN,
        version,
    )
    if updated_pyproject == pyproject_text and updated_package == package_text:
        return previous

    _atomic_write(pyproject, updated_pyproject)
    try:
        _atomic_write(package_init, updated_package)
    except OSError:
        _atomic_write(pyproject, pyproject_text)
        raise
    if current_version(pyproject, package_init) != version:
        raise VersionError("Version update did not produce synchronized files")
    return previous


def _validate_version(version: str) -> None:
    if VERSION_PATTERN.fullmatch(version) is None:
        raise VersionError(
            f"Invalid package version {version!r}; expected MAJOR.MINOR.PATCH"
            " with an optional suffix such as rc1"
        )


def _extract_version(path: Path, pattern: re.Pattern[str]) -> str:
    return _extract_from_text(path, path.read_text(encoding="utf-8"), pattern)


def _extract_from_text(path: Path, text: str, pattern: re.Pattern[str]) -> str:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise VersionError(
            f"Expected exactly one package version assignment in {path}, "
            f"found {len(matches)}"
        )
    return matches[0].group("value")


def _replace_version(
    path: Path,
    text: str,
    pattern: re.Pattern[str],
    version: str,
) -> str:
    _extract_from_text(path, text, pattern)
    return pattern.sub(
        lambda match: f'{match.group("prefix")}{version}{match.group("suffix")}',
        text,
        count=1,
    )


def _atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="Print the synchronized package version")
    commands.add_parser("check", help="Validate that package versions match")
    setter = commands.add_parser("set", help="Set the package version in both files")
    setter.add_argument("version")
    args = parser.parse_args(argv)

    try:
        if args.command == "set":
            previous = set_version(args.version)
            if previous == (args.version, args.version):
                print(f"Speechloom is already at {args.version}")
            else:
                print(
                    "Updated Speechloom version: "
                    f"pyproject.toml={previous[0]}, __init__.py={previous[1]} "
                    f"-> {args.version}"
                )
        else:
            version = current_version()
            if args.command == "show":
                print(version)
            else:
                print(f"Speechloom version {version} is synchronized")
    except (OSError, VersionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
