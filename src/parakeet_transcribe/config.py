"""Configuration loading with CLI > environment > file > defaults precedence."""

from __future__ import annotations

import configparser
from dataclasses import dataclass, fields
import os
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigurationError


ENV_PREFIX = "PARAKEET_TRANSCRIBE_"


@dataclass(frozen=True)
class Settings:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    nemo_speech: str = "nemo-speech"
    model: str | None = None
    diar_model: str | None = None
    device: str = "auto"
    output_dir: str = "transcripts"
    workers: int = 1
    shared_model: bool = True
    keep_audio: bool = False
    resume: bool = True
    formats: tuple[str, ...] = ("json", "txt", "srt", "vtt")


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    environ = env or os.environ
    config_home = environ.get("XDG_CONFIG_HOME")
    root = Path(config_home) if config_home else Path.home() / ".config"
    return root / "parakeet-transcribe" / "config.ini"


def load_settings(
    *,
    config_path: Path | None = None,
    cli_values: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    environ = env or os.environ
    values: dict[str, Any] = {item.name: item.default for item in fields(Settings)}
    path = config_path or default_config_path(environ)
    values.update(_read_config(path))
    values.update(_read_environment(environ))
    if cli_values:
        values.update({key: value for key, value in cli_values.items() if value is not None})
    return _coerce_settings(values)


def _read_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    parser = configparser.ConfigParser()
    try:
        with path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise ConfigurationError(f"Could not read config file: {path}") from exc
    if "parakeet-transcribe" not in parser:
        raise ConfigurationError(f"Config file lacks [parakeet-transcribe]: {path}")
    return dict(parser["parakeet-transcribe"])


def _read_environment(env: Mapping[str, str]) -> dict[str, str]:
    known = {item.name for item in fields(Settings)}
    output: dict[str, str] = {}
    for name in known:
        key = f"{ENV_PREFIX}{name.upper()}"
        if key in env:
            output[name] = env[key]
    return output


def _coerce_settings(values: Mapping[str, Any]) -> Settings:
    allowed_formats = {"json", "txt", "srt", "vtt"}
    formats_value = values.get("formats", Settings.formats)
    if isinstance(formats_value, str):
        formats = tuple(part.strip().lower() for part in formats_value.split(",") if part.strip())
    else:
        formats = tuple(str(part).lower() for part in formats_value)
    invalid = set(formats) - allowed_formats
    if invalid or not formats:
        raise ConfigurationError(f"Invalid output formats: {', '.join(sorted(invalid)) or 'none'}")

    device = str(values.get("device", "auto")).lower()
    if device != "auto" and device != "cpu" and not (
        device.startswith("cuda") or device.startswith("vulkan") or device == "metal"
    ):
        raise ConfigurationError(f"Unsupported device: {device}")

    workers = _as_int(values.get("workers", 1), "workers")
    if workers < 1:
        raise ConfigurationError("workers must be at least 1")

    return Settings(
        ffmpeg=str(values.get("ffmpeg", "ffmpeg")),
        ffprobe=str(values.get("ffprobe", "ffprobe")),
        nemo_speech=str(values.get("nemo_speech", "nemo-speech")),
        model=_none_if_empty(values.get("model")),
        diar_model=_none_if_empty(values.get("diar_model")),
        device=device,
        output_dir=str(values.get("output_dir", "transcripts")),
        workers=workers,
        shared_model=_as_bool(values.get("shared_model", True), "shared_model"),
        keep_audio=_as_bool(values.get("keep_audio", False), "keep_audio"),
        resume=_as_bool(values.get("resume", True), "resume"),
        formats=formats,
    )


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _as_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _none_if_empty(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value)
