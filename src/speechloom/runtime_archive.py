"""Deterministic creation and safe extraction of native runtime archives."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from typing import BinaryIO, Iterable

from .errors import SetupError


RUNTIME_ARCHIVE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RuntimeArchiveMetadata:
    backend: str
    system: str
    architecture: str
    revision: str
    features: tuple[str, ...]
    executable: str = "bin/nemo-speech"
    schema_version: int = RUNTIME_ARCHIVE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["features"] = list(self.features)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RuntimeArchiveMetadata":
        if payload.get("schema_version") != RUNTIME_ARCHIVE_SCHEMA_VERSION:
            raise SetupError("Unsupported Speechloom runtime archive schema")
        try:
            features = payload["features"]
            if not isinstance(features, list):
                raise TypeError("features must be a list")
            return cls(
                backend=str(payload["backend"]),
                system=str(payload["system"]),
                architecture=str(payload["architecture"]),
                revision=str(payload["revision"]),
                features=tuple(str(item) for item in features),
                executable=str(payload["executable"]),
                schema_version=int(payload["schema_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SetupError("Runtime archive metadata is incomplete") from exc


def build_runtime_archive(
    prefix: Path,
    output: Path,
    metadata: RuntimeArchiveMetadata,
    *,
    source_date_epoch: int = 0,
) -> str:
    """Create a reproducible ``tar.gz`` and return its SHA-256 checksum."""

    prefix = prefix.resolve()
    executable = prefix / metadata.executable
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SetupError(f"Runtime executable is missing or not executable: {executable}")
    licenses = prefix / "share/licenses"
    if not licenses.is_dir() or not any(path.is_file() for path in licenses.rglob("*")):
        raise SetupError("Runtime prefix does not contain third-party license files")
    output.parent.mkdir(parents=True, exist_ok=True)
    root_name = output.name.removesuffix(".tar.gz")

    with tempfile.TemporaryDirectory(prefix="speechloom-runtime-package-") as temporary:
        staging_root = Path(temporary) / root_name
        shutil.copytree(prefix, staging_root, symlinks=True)
        (staging_root / "runtime.json").write_text(
            json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_output = output.with_name(f".{output.name}.tmp")
        try:
            with temporary_output.open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=source_date_epoch) as compressed:
                    with tarfile.open(
                        fileobj=compressed,
                        mode="w",
                        format=tarfile.PAX_FORMAT,
                    ) as archive:
                        for path in _archive_paths(staging_root):
                            relative = path.relative_to(staging_root.parent).as_posix()
                            info = archive.gettarinfo(str(path), arcname=relative)
                            info.uid = 0
                            info.gid = 0
                            info.uname = "root"
                            info.gname = "root"
                            info.mtime = source_date_epoch
                            if info.isfile():
                                with path.open("rb") as source:
                                    archive.addfile(info, source)
                            else:
                                archive.addfile(info)
            os.replace(temporary_output, output)
        finally:
            temporary_output.unlink(missing_ok=True)
    return _sha256(output)


def read_runtime_metadata(archive_path: Path) -> RuntimeArchiveMetadata:
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = archive.getmembers()
        root = _validated_root(members)
        try:
            metadata_member = archive.getmember(f"{root}/runtime.json")
        except KeyError as exc:
            raise SetupError("Runtime archive does not contain runtime.json") from exc
        source = archive.extractfile(metadata_member)
        if source is None:
            raise SetupError("Runtime archive metadata is unreadable")
        try:
            payload = json.loads(source.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SetupError("Runtime archive metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise SetupError("Runtime archive metadata must be an object")
    return RuntimeArchiveMetadata.from_dict(payload)


def extract_runtime_archive(
    archive_path: Path,
    destination: Path,
    expected: RuntimeArchiveMetadata,
) -> Path:
    """Validate and atomically extract an archive without traversal or link escapes."""

    if destination.exists():
        raise SetupError(f"Runtime destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    actual = read_runtime_metadata(archive_path)
    if actual != expected:
        raise SetupError("Runtime archive metadata does not match the selected profile")

    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = archive.getmembers()
        root = _validated_root(members)
        with tempfile.TemporaryDirectory(
            prefix=".speechloom-runtime-extract-", dir=destination.parent
        ) as temporary:
            extraction_root = Path(temporary)
            _extract_members(archive, members, extraction_root, root)
            staged = extraction_root / root
            executable = staged / expected.executable
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise SetupError("Runtime archive does not contain an executable nemo-speech")
            os.replace(staged, destination)
    return destination / expected.executable


def _archive_paths(root: Path) -> Iterable[Path]:
    yield root
    yield from sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())


def _validated_root(members: list[tarfile.TarInfo]) -> str:
    if not members:
        raise SetupError("Runtime archive is empty")
    roots: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise SetupError(f"Unsafe path in runtime archive: {member.name}")
        roots.add(path.parts[0])
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise SetupError(f"Unsupported entry in runtime archive: {member.name}")
        if member.issym():
            _validated_link(path.parent, member.linkname, path.parts[0])
        elif member.islnk():
            _validated_link(PurePosixPath(), member.linkname, path.parts[0])
    if len(roots) != 1:
        raise SetupError("Runtime archive must contain exactly one top-level directory")
    return roots.pop()


def _validated_link(parent: PurePosixPath, target: str, root: str) -> None:
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        raise SetupError(f"Unsafe absolute link in runtime archive: {target}")
    parts: list[str] = []
    for part in (parent / target_path).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise SetupError(f"Link escapes runtime archive: {target}")
            parts.pop()
        else:
            parts.append(part)
    if not parts or parts[0] != root:
        raise SetupError(f"Link escapes runtime archive: {target}")


def _extract_members(
    archive: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    destination: Path,
    root: str,
) -> None:
    directories = sorted(
        (member for member in members if member.isdir()),
        key=lambda member: len(PurePosixPath(member.name).parts),
    )
    regular = [member for member in members if member.isfile()]
    links = [member for member in members if member.issym() or member.islnk()]

    for member in directories:
        target = destination / PurePosixPath(member.name)
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(member.mode & 0o777)
    for member in regular:
        target = destination / PurePosixPath(member.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SetupError(f"Runtime archive entry is unreadable: {member.name}")
        with target.open("wb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(member.mode & 0o777)
    for member in links:
        target = destination / PurePosixPath(member.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if member.issym():
            _validated_link(PurePosixPath(member.name).parent, member.linkname, root)
            target.symlink_to(member.linkname)
        else:
            _validated_link(PurePosixPath(), member.linkname, root)
            link_target = destination / PurePosixPath(member.linkname)
            if not link_target.is_file():
                raise SetupError(f"Runtime archive hard-link target is missing: {member.linkname}")
            os.link(link_target, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
