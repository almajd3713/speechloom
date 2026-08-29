"""Immutable runtime and model metadata shipped with Speechloom."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import re
from typing import Any

from .errors import ConfigurationError


REGISTRY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RuntimeArchiveSpec:
    backend: str
    system: str
    architecture: str
    filename: str
    url: str
    sha256: str
    features: tuple[str, ...]
    minimum_free_bytes: int


@dataclass(frozen=True)
class RuntimeSpec:
    id: str
    repository: str
    revision: str
    build_script: str
    executable: str
    license: str
    backends: tuple[str, ...]
    features: tuple[str, ...]
    archives: tuple[RuntimeArchiveSpec, ...] = ()


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: str
    upstream_id: str
    revision: str
    filename: str
    sha256: str | None
    url: str | None
    install_script: str
    license: str
    minimum_free_bytes: int


@dataclass(frozen=True)
class Registry:
    runtime: RuntimeSpec
    models: tuple[ModelSpec, ...]

    @classmethod
    def load(cls) -> "Registry":
        resource = files("speechloom").joinpath("data/registry.json")
        try:
            payload = json.loads(resource.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError("Speechloom's bundled artifact registry is invalid") from exc
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Registry":
        if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise ConfigurationError("Unsupported Speechloom artifact registry schema")
        try:
            runtime_data = payload["runtime"]
            runtime = RuntimeSpec(
                id=str(runtime_data["id"]),
                repository=_https_url(runtime_data["repository"], "runtime repository"),
                revision=_revision(runtime_data["revision"]),
                build_script=str(runtime_data["build_script"]),
                executable=str(runtime_data["executable"]),
                license=str(runtime_data["license"]),
                backends=tuple(str(item) for item in runtime_data["backends"]),
                features=tuple(str(item) for item in runtime_data["features"]),
                archives=tuple(
                    _runtime_archive_spec(item) for item in runtime_data.get("archives", [])
                ),
            )
            models = tuple(_model_spec(item) for item in payload["models"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError("Speechloom's bundled artifact registry is incomplete") from exc
        if len({model.id for model in models}) != len(models):
            raise ConfigurationError("Speechloom's artifact registry contains duplicate model IDs")
        archive_keys = {
            (archive.backend, archive.system, archive.architecture, archive.features)
            for archive in runtime.archives
        }
        if len(archive_keys) != len(runtime.archives):
            raise ConfigurationError("Speechloom's artifact registry contains duplicate runtimes")
        return cls(runtime, models)

    def model(self, kind: str) -> ModelSpec:
        matches = [model for model in self.models if model.kind == kind]
        if len(matches) != 1:
            raise ConfigurationError(f"Artifact registry does not define one {kind!r} model")
        return matches[0]

    def runtime_archive(
        self,
        backend: str,
        system: str,
        architecture: str,
        features: tuple[str, ...],
    ) -> RuntimeArchiveSpec | None:
        requested = {"asr", *features}
        matches = [
            archive
            for archive in self.runtime.archives
            if archive.backend == backend
            and archive.system == system.lower()
            and archive.architecture == architecture.lower()
            and requested.issubset(archive.features)
        ]
        if len(matches) > 1:
            raise ConfigurationError("Artifact registry defines ambiguous runtime profiles")
        return matches[0] if matches else None


def _runtime_archive_spec(payload: dict[str, Any]) -> RuntimeArchiveSpec:
    digest = str(payload["sha256"])
    filename = str(payload["filename"])
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ConfigurationError("Invalid runtime archive checksum")
    if filename in {"", ".", ".."} or "/" in filename or "\\" in filename:
        raise ConfigurationError("Runtime archive filename must be a safe path component")
    return RuntimeArchiveSpec(
        backend=str(payload["backend"]),
        system=str(payload["system"]).lower(),
        architecture=str(payload["architecture"]).lower(),
        filename=filename,
        url=_https_url(payload["url"], "runtime archive URL"),
        sha256=digest,
        features=tuple(str(item) for item in payload["features"]),
        minimum_free_bytes=int(payload["minimum_free_bytes"]),
    )


def _model_spec(payload: dict[str, Any]) -> ModelSpec:
    digest = payload.get("sha256")
    if digest is not None and re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None:
        raise ConfigurationError(f"Invalid checksum for model {payload.get('id', '<unknown>')}")
    url = payload.get("url")
    return ModelSpec(
        id=str(payload["id"]),
        kind=str(payload["kind"]),
        upstream_id=str(payload["upstream_id"]),
        revision=_revision(payload["revision"]),
        filename=str(payload["filename"]),
        sha256=str(digest) if digest is not None else None,
        url=_https_url(url, "model URL") if url is not None else None,
        install_script=str(payload["install_script"]),
        license=str(payload["license"]),
        minimum_free_bytes=int(payload["minimum_free_bytes"]),
    )


def _https_url(value: Any, name: str) -> str:
    normalized = str(value)
    if not normalized.startswith("https://"):
        raise ConfigurationError(f"{name} must use HTTPS")
    return normalized


def _revision(value: Any) -> str:
    normalized = str(value)
    if re.fullmatch(r"[0-9a-f]{40}", normalized) is None:
        raise ConfigurationError(f"Invalid pinned revision: {normalized!r}")
    return normalized
