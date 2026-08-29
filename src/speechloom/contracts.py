"""Framework-neutral contracts for Speechloom's supported Python API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any, Protocol, runtime_checkable

from .errors import CancellationError, ConfigurationError


@dataclass(frozen=True)
class TranscriptionRequest:
    inputs: tuple[Path, ...]
    output_dir: Path | None = None
    formats: tuple[str, ...] | None = None
    recursive: bool = False
    audio_stream: int | None = None
    diarize: bool = False
    source_language: str | None = None
    translate_to: str | None = None
    resume: bool | None = None
    force: bool = False
    keep_audio: bool | None = None
    workers: int | None = None
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if not self.inputs:
            raise ConfigurationError("At least one transcription input is required")
        if self.workers is not None and self.workers < 1:
            raise ConfigurationError("workers must be at least 1")
        if self.formats is not None:
            normalized = tuple(item.strip().lower() for item in self.formats if item.strip())
            invalid = set(normalized) - {"json", "txt", "srt", "vtt"}
            if invalid or not normalized:
                names = ", ".join(sorted(invalid)) or "none"
                raise ConfigurationError(f"Invalid output formats: {names}")
            object.__setattr__(self, "formats", normalized)
        source_language = _normalize_language(self.source_language)
        target_language = _normalize_language(self.translate_to)
        if source_language and source_language == target_language:
            raise ConfigurationError("source_language and translate_to must be different")
        object.__setattr__(self, "source_language", source_language)
        object.__setattr__(self, "translate_to", target_language)


@dataclass(frozen=True)
class StageEvent:
    job_id: str | None
    source: Path | None
    stage: str
    status: str
    message: str
    completed: int | None = None
    total: int | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@runtime_checkable
class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...


class CancellationController:
    """Thread-safe cancellation source that also implements ``CancellationToken``."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise CancellationError("Operation cancelled")


@dataclass(frozen=True)
class ArtifactDetails:
    name: str
    path: str
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class JobDetails:
    schema_version: int
    job_id: str
    state: str
    state_detail: str
    source: Path | None
    artifacts: tuple[ArtifactDetails, ...]
    error: str | None = None
    _payload_json: str = field(default="{}", repr=False, compare=False)

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "JobDetails":
        raw_artifacts = manifest.get("artifacts", {})
        if isinstance(raw_artifacts, dict):
            artifacts = tuple(
                ArtifactDetails(
                    name=str(name),
                    path=str(value.get("path", name)),
                    size=_optional_int(value.get("size")),
                    sha256=_optional_string(value.get("sha256")),
                )
                for name, value in raw_artifacts.items()
                if isinstance(value, dict)
            )
        else:
            artifacts = ()
        source_data = manifest.get("source")
        source_path = source_data.get("path") if isinstance(source_data, dict) else None
        error_data = manifest.get("error")
        if isinstance(error_data, dict):
            error = _optional_string(error_data.get("message"))
        else:
            error = _optional_string(error_data)
        return cls(
            schema_version=int(manifest.get("schema_version", 0)),
            job_id=str(manifest.get("job_id", "")),
            state=str(manifest.get("state", "unknown")),
            state_detail=str(manifest.get("state_detail", manifest.get("state", "unknown"))),
            source=Path(str(source_path)) if source_path else None,
            artifacts=artifacts,
            error=error,
            _payload_json=json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return an independent manifest copy for serialization adapters."""

        payload = json.loads(self._payload_json)
        if not isinstance(payload, dict):  # defensive: only objects are accepted above
            raise ValueError("Job details payload is not an object")
        return payload


def _normalize_language(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", normalized) is None:
        raise ConfigurationError(
            "source_language and translate_to must use codes such as ru, en, or zh-cn"
        )
    return normalized


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
