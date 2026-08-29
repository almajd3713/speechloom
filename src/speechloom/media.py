"""Media discovery, probing, and normalization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable

from .errors import MediaError
from .process import CommandFailed, CommandResult, run_command


MEDIA_EXTENSIONS = {
    ".aac", ".avi", ".flac", ".m4a", ".mka", ".mkv", ".mov", ".mp3",
    ".mp4", ".mpeg", ".mpg", ".oga", ".ogg", ".opus", ".wav", ".webm",
}


@dataclass(frozen=True)
class AudioStream:
    index: int
    codec: str
    sample_rate: int | None
    channels: int | None


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float | None
    audio_streams: tuple[AudioStream, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Runner = Callable[..., CommandResult]


def discover_inputs(paths: list[Path], *, recursive: bool) -> list[Path]:
    discovered: list[Path] = []
    for candidate in paths:
        path = candidate.expanduser()
        if not path.exists():
            raise MediaError(f"Input does not exist: {path}")
        if path.is_file():
            discovered.append(path.resolve())
            continue
        iterator = path.rglob("*") if recursive else path.glob("*")
        discovered.extend(
            item.resolve()
            for item in iterator
            if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
        )
    unique = sorted(dict.fromkeys(discovered), key=lambda item: str(item).casefold())
    if not unique:
        raise MediaError("No media files were found")
    return unique


def probe_media(path: Path, ffprobe: str = "ffprobe", *, runner: Runner = run_command) -> MediaInfo:
    argv = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration:stream=index,codec_type,codec_name,sample_rate,channels",
        "-of", "json",
        str(path),
    ]
    try:
        result = runner(argv)
        payload = json.loads(result.stdout)
    except CommandFailed as exc:
        raise MediaError(f"Could not inspect media: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MediaError(f"FFprobe returned invalid JSON for: {path}") from exc

    streams: list[AudioStream] = []
    for stream in payload.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        streams.append(
            AudioStream(
                index=int(stream["index"]),
                codec=str(stream.get("codec_name", "unknown")),
                sample_rate=_optional_int(stream.get("sample_rate")),
                channels=_optional_int(stream.get("channels")),
            )
        )
    if not streams:
        raise MediaError(f"Media has no audio stream: {path}")
    duration = _optional_float(payload.get("format", {}).get("duration"))
    return MediaInfo(str(path.resolve()), duration, tuple(streams))


def select_audio_stream(info: MediaInfo, requested_index: int | None) -> AudioStream:
    if requested_index is None:
        return info.audio_streams[0]
    for stream in info.audio_streams:
        if stream.index == requested_index:
            return stream
    available = ", ".join(str(stream.index) for stream in info.audio_streams)
    raise MediaError(f"Audio stream {requested_index} is unavailable; available indexes: {available}")


def can_passthrough_wav(path: Path, stream: AudioStream) -> bool:
    return (
        path.suffix.lower() == ".wav"
        and stream.codec in {"pcm_s16le", "pcm_f32le"}
        and stream.sample_rate is not None
        and 8_000 <= stream.sample_rate <= 96_000
        and stream.channels in {1, 2}
    )


def normalize_audio(
    source: Path,
    destination: Path,
    stream: AudioStream,
    ffmpeg: str = "ffmpeg",
    *,
    runner: Runner = run_command,
) -> CommandResult:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.stem}.part{destination.suffix}")
    argv = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-map", f"0:{stream.index}",
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(temporary),
    ]
    try:
        result = runner(argv)
        os.replace(temporary, destination)
        return result
    except CommandFailed as exc:
        temporary.unlink(missing_ok=True)
        raise MediaError(f"FFmpeg normalization failed for {source}: {exc}") from exc


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

