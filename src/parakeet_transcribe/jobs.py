"""Resumable single-file and batch pipeline orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable

from .artifacts import atomic_write_json, atomic_write_text, file_identity, read_json, sha256_file
from .errors import ArtifactConflictError, ConfigurationError, PipelineError
from .media import MediaInfo, can_passthrough_wav, normalize_audio, probe_media, select_audio_stream
from .nemo import NemoOptions, transcribe
from .process import CommandResult, run_command
from .renderers import build_segments, render_srt, render_text, render_vtt
from .schema import Transcript


MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PipelineOptions:
    output_dir: Path
    model: Path
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    nemo_speech: str = "nemo-speech"
    device: str = "auto"
    formats: tuple[str, ...] = ("json", "txt", "srt", "vtt")
    audio_stream: int | None = None
    diarize: bool = False
    diar_model: Path | None = None
    keep_audio: bool = False
    resume: bool = True
    force: bool = False
    workers: int = 1
    fail_fast: bool = False


@dataclass(frozen=True)
class JobResult:
    source: str
    job_dir: str
    state: str
    skipped: bool = False
    error: str | None = None


class Pipeline:
    def __init__(self, options: PipelineOptions, *, runner: Callable[..., CommandResult] = run_command) -> None:
        if options.workers < 1:
            raise ConfigurationError("workers must be at least 1")
        self.options = options
        self.runner = runner
        self._identity_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._tool_versions: dict[str, str] | None = None

    def run(self, sources: list[Path]) -> list[JobResult]:
        if self.options.workers == 1 or len(sources) <= 1:
            results: list[JobResult] = []
            for source in sources:
                result = self.run_one(source)
                results.append(result)
                if result.error and self.options.fail_fast:
                    break
            return results

        results_by_source: dict[str, JobResult] = {}
        with ThreadPoolExecutor(max_workers=self.options.workers, thread_name_prefix="parakeet") as executor:
            future_map = {executor.submit(self.run_one, source): source for source in sources}
            for future in as_completed(future_map):
                source = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # defensive isolation at the batch boundary
                    result = JobResult(str(source), "", "failed", error=str(exc))
                results_by_source[str(source)] = result
                if result.error and self.options.fail_fast:
                    for pending in future_map:
                        pending.cancel()
                    break
        return [results_by_source[str(source)] for source in sources if str(source) in results_by_source]

    def run_one(self, source: Path) -> JobResult:
        source = source.expanduser().resolve()
        started = time.monotonic()
        manifest: dict[str, Any] | None = None
        manifest_path: Path | None = None
        try:
            source_identity = self._identity(source)
            model_identity = self._identity(self.options.model)
            diar_identity = self._identity(self.options.diar_model) if self.options.diar_model else None
            semantic = self._semantic_options(model_identity, diar_identity)
            job_id = _job_id(source_identity, semantic)
            job_dir = self.options.output_dir.expanduser().resolve() / _job_dir_name(source, job_id)
            work_dir = job_dir / ".work"
            manifest_path = job_dir / "manifest.json"
            canonical_path = job_dir / "transcript.json"

            existing = _load_manifest_if_present(manifest_path)
            if existing and existing.get("state") == "completed" and self.options.resume and not self.options.force:
                return JobResult(str(source), str(job_dir), "completed", skipped=True)
            if existing and not self.options.resume and not self.options.force:
                raise ArtifactConflictError(
                    f"Job output already exists: {job_dir}; use --resume or --force"
                )

            job_dir.mkdir(parents=True, exist_ok=True)
            work_dir.mkdir(parents=True, exist_ok=True)
            manifest = existing if existing and not self.options.force else _new_manifest(
                job_id=job_id,
                source=source_identity,
                model=model_identity,
                diar_model=diar_identity,
                options=semantic,
                tools=self._versions(),
            )
            manifest["state"] = "processing"
            manifest["updated_at"] = _now()
            atomic_write_json(manifest_path, manifest)

            media = probe_media(source, self.options.ffprobe, runner=self.runner)
            stream = select_audio_stream(media, self.options.audio_stream)
            manifest["media"] = media.to_dict()
            manifest["selected_audio_stream"] = asdict(stream)
            atomic_write_json(manifest_path, manifest)

            normalized_path = work_dir / "audio.wav"
            passthrough = can_passthrough_wav(source, stream)
            audio_path = source if passthrough else normalized_path
            can_resume_audio = (
                not self.options.force
                and manifest.get("state_detail") in {"normalized", "transcribed"}
                and normalized_path.is_file()
            )
            if not passthrough and not can_resume_audio:
                step_started = time.monotonic()
                normalize_audio(source, normalized_path, stream, self.options.ffmpeg, runner=self.runner)
                manifest.setdefault("timings", {})["normalization_seconds"] = time.monotonic() - step_started
            manifest["state_detail"] = "normalized"
            manifest["normalized_audio"] = {
                "path": str(audio_path),
                "passthrough": passthrough,
            }
            atomic_write_json(manifest_path, manifest)

            can_resume_transcript = (
                not self.options.force
                and canonical_path.is_file()
                and manifest.get("state_detail") == "transcribed"
            )
            if can_resume_transcript:
                transcript = Transcript.load(canonical_path)
            else:
                step_started = time.monotonic()
                transcript = transcribe(
                    audio_path,
                    NemoOptions(
                        executable=self.options.nemo_speech,
                        model=self.options.model,
                        device=self.options.device,
                        diarize=self.options.diarize,
                        diar_model=self.options.diar_model,
                    ),
                    duration=media.duration,
                    runner=self.runner,
                )
                transcript.segments = build_segments(transcript)
                transcript.provenance.update(
                    {
                        "source_sha256": source_identity["sha256"],
                        "model_sha256": model_identity["sha256"],
                    }
                )
                if diar_identity:
                    transcript.provenance["diar_model_sha256"] = diar_identity["sha256"]
                atomic_write_json(canonical_path, transcript.to_dict())
                manifest.setdefault("timings", {})["inference_seconds"] = time.monotonic() - step_started
            manifest["state_detail"] = "transcribed"
            atomic_write_json(manifest_path, manifest)

            self._render(job_dir, transcript)
            artifacts = _artifact_manifest(job_dir, self.options.formats)
            manifest["artifacts"] = artifacts
            manifest["state"] = "completed"
            manifest["state_detail"] = "completed"
            manifest["updated_at"] = _now()
            manifest.setdefault("timings", {})["total_seconds"] = time.monotonic() - started
            atomic_write_json(manifest_path, manifest)

            if not passthrough and not self.options.keep_audio:
                normalized_path.unlink(missing_ok=True)
                try:
                    work_dir.rmdir()
                except OSError:
                    pass
            return JobResult(str(source), str(job_dir), "completed")
        except KeyboardInterrupt:
            if manifest is not None and manifest_path is not None:
                manifest["state"] = "interrupted"
                manifest["updated_at"] = _now()
                atomic_write_json(manifest_path, manifest)
            raise
        except Exception as exc:
            if manifest is not None and manifest_path is not None:
                manifest["state"] = "failed"
                manifest["updated_at"] = _now()
                manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
                manifest.setdefault("timings", {})["total_seconds"] = time.monotonic() - started
                atomic_write_json(manifest_path, manifest)
            return JobResult(
                str(source),
                str(manifest_path.parent) if manifest_path else "",
                "failed",
                error=str(exc),
            )

    def _render(self, job_dir: Path, transcript: Transcript) -> None:
        formats = set(self.options.formats)
        if "json" in formats:
            atomic_write_json(job_dir / "transcript.json", transcript.to_dict())
        if "txt" in formats:
            atomic_write_text(job_dir / "transcript.txt", render_text(transcript))
        segments = transcript.segments or build_segments(transcript)
        if "srt" in formats:
            atomic_write_text(job_dir / "subtitles.srt", render_srt(segments))
        if "vtt" in formats:
            atomic_write_text(job_dir / "subtitles.vtt", render_vtt(segments))

    def _semantic_options(
        self,
        model_identity: dict[str, Any],
        diar_identity: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "model_sha256": model_identity["sha256"],
            "diar_model_sha256": diar_identity["sha256"] if diar_identity else None,
            "device": self.options.device,
            "audio_stream": self.options.audio_stream,
            "diarize": self.options.diarize,
            "formats": list(self.options.formats),
        }

    def _identity(self, path: Path | None) -> dict[str, Any]:
        if path is None:
            raise ValueError("Cannot fingerprint a missing path")
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
        key = (str(resolved), stat.st_size, stat.st_mtime_ns)
        if key not in self._identity_cache:
            self._identity_cache[key] = file_identity(resolved)
        return self._identity_cache[key]

    def _versions(self) -> dict[str, str]:
        if self._tool_versions is None:
            self._tool_versions = {
                "ffmpeg": _tool_version([self.options.ffmpeg, "-version"], self.runner),
                "ffprobe": _tool_version([self.options.ffprobe, "-version"], self.runner),
                "nemo_speech": _tool_version([self.options.nemo_speech, "--version"], self.runner),
            }
        return dict(self._tool_versions)


def inspect_job(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json" if path.is_dir() else path
    if not manifest_path.is_file():
        raise PipelineError(f"Manifest does not exist: {manifest_path}")
    try:
        return read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Could not read manifest: {manifest_path}") from exc


def _new_manifest(
    *,
    job_id: str,
    source: dict[str, Any],
    model: dict[str, Any],
    diar_model: dict[str, Any] | None,
    options: dict[str, Any],
    tools: dict[str, str],
) -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "job_id": job_id,
        "state": "created",
        "state_detail": "created",
        "created_at": now,
        "updated_at": now,
        "source": source,
        "model": model,
        "diar_model": diar_model,
        "options": options,
        "tools": tools,
        "timings": {},
        "artifacts": {},
        "warnings": [],
    }


def _load_manifest_if_present(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ArtifactConflictError(f"Existing manifest is unreadable: {path}") from exc
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ArtifactConflictError(f"Unsupported manifest version: {path}")
    return payload


def _artifact_manifest(job_dir: Path, formats: tuple[str, ...]) -> dict[str, Any]:
    names = {
        "json": "transcript.json",
        "txt": "transcript.txt",
        "srt": "subtitles.srt",
        "vtt": "subtitles.vtt",
    }
    output: dict[str, Any] = {}
    for format_name in dict.fromkeys(("json", *formats)):
        path = job_dir / names[format_name]
        if path.is_file():
            output[format_name] = {
                "path": path.name,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    return output


def _job_id(source: dict[str, Any], options: dict[str, Any]) -> str:
    payload = json.dumps(
        {"source": source["sha256"], "options": options},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _job_dir_name(source: Path, job_id: str) -> str:
    safe_stem = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE).strip("-.") or "media"
    return f"{safe_stem}-{job_id[:10]}"


def _tool_version(argv: list[str], runner: Callable[..., CommandResult]) -> str:
    try:
        result = runner(argv)
    except Exception as exc:
        return f"unavailable: {exc}"
    line = (result.stdout.strip() or result.stderr.strip()).splitlines()
    return line[0] if line else "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
