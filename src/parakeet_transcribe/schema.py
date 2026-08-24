"""Versioned canonical transcript schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from .errors import InferenceError


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    confidence: float | None = None
    speaker: int | str | None = None
    language: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Word":
        return cls(
            text=str(data["text"]),
            start=float(data["start"]),
            end=float(data["end"]),
            confidence=_optional_float(data.get("confidence")),
            speaker=data.get("speaker"),
            language=_optional_string(data.get("language")),
        )


@dataclass(frozen=True)
class Segment:
    text: str
    start: float
    end: float
    speaker: int | str | None = None
    word_start: int = 0
    word_end: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Segment":
        return cls(
            text=str(data["text"]),
            start=float(data["start"]),
            end=float(data["end"]),
            speaker=data.get("speaker"),
            word_start=int(data.get("word_start", 0)),
            word_end=int(data.get("word_end", 0)),
        )


@dataclass
class Transcript:
    text: str
    words: list[Word]
    duration: float | None = None
    language: str | None = None
    segments: list[Segment] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise InferenceError(f"Unsupported transcript schema version: {self.schema_version}")
        previous_start = -1.0
        for index, word in enumerate(self.words):
            if not word.text.strip():
                raise InferenceError(f"Word {index} has empty text")
            if word.start < 0 or word.end < word.start:
                raise InferenceError(f"Word {index} has invalid timestamps")
            if word.start < previous_start:
                raise InferenceError("Word timestamps are not monotonic")
            if self.duration is not None and word.end > self.duration + 1.0:
                raise InferenceError(f"Word {index} extends beyond media duration")
            previous_start = word.start

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = self.schema_version
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Transcript":
        transcript = cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            text=str(data.get("text", "")),
            words=[Word.from_dict(item) for item in data.get("words", [])],
            duration=_optional_float(data.get("duration")),
            language=_optional_string(data.get("language")),
            segments=[Segment.from_dict(item) for item in data.get("segments", [])],
            provenance=dict(data.get("provenance", {})),
            warnings=[str(item) for item in data.get("warnings", [])],
        )
        transcript.validate()
        return transcript

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InferenceError(f"Could not read transcript JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise InferenceError("Canonical transcript JSON must be an object")
        return cls.from_dict(payload)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)

