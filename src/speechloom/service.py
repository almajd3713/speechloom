"""Application service shared by the CLI, Python callers, and future adapters."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .artifacts import atomic_write_json, atomic_write_text, sha256_file
from .config import Settings, load_managed_settings
from .contracts import (
    CancellationToken,
    JobDetails,
    SegmentEdit,
    ServiceCapabilities,
    StageEvent,
    TranscriptionRequest,
)
from .doctor import DoctorReport, run_doctor
from .errors import ArtifactConflictError, ConfigurationError
from .fingerprints import FINGERPRINT_CACHE_FILENAME
from .jobs import (
    EDITED_ARTIFACTS,
    JobResult,
    Pipeline,
    PipelineOptions,
    _artifact_manifest,
    inspect_job,
)
from .languages import ASR_LANGUAGES, TRANSLATION_LANGUAGES
from .media import discover_inputs
from .process import CommandResult, run_command
from .renderers import build_segments, render_segment_text, render_srt, render_vtt
from .runtime import AppPaths
from .schema import Transcript


Runner = Callable[..., CommandResult]
EventSink = Callable[[StageEvent], None]


class TranscriptionService:
    """Stable application boundary over configuration, diagnostics, and jobs."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner = run_command,
        fingerprint_cache: Path | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self._fingerprint_cache = fingerprint_cache

    @classmethod
    def from_default_config(
        cls,
        *,
        config_path: Path | None = None,
        env: Mapping[str, str] | None = None,
        runner: Runner = run_command,
    ) -> "TranscriptionService":
        return cls(
            load_managed_settings(config_path=config_path, env=env),
            runner=runner,
            fingerprint_cache=AppPaths.from_environment(env).cache_dir
            / FINGERPRINT_CACHE_FILENAME,
        )

    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        on_event: EventSink | None = None,
        cancellation: CancellationToken | None = None,
        inference_gate: AbstractContextManager[Any] | None = None,
    ) -> list[JobResult]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if not self._settings.model:
            raise ConfigurationError(
                "No ASR model is configured; run 'speechloom setup' or pass --model"
            )
        if request.diarize and not self._settings.diar_model:
            raise ConfigurationError(
                "--diarize requires --diar-model or SPEECHLOOM_DIAR_MODEL"
            )
        source_language = request.source_language or self._settings.source_language
        translate_to = request.translate_to or self._settings.translate_to
        if (source_language or translate_to) and not self._settings.translation_model:
            raise ConfigurationError(
                "Translation was requested but no translation model is configured or managed"
            )
        if bool(source_language) != bool(translate_to):
            raise ConfigurationError(
                "Translation requires both a source language and a target language"
            )

        sources = discover_inputs(list(request.inputs), recursive=request.recursive)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        options = self._pipeline_options(request)
        return Pipeline(
            options,
            runner=self._runner,
            on_event=on_event,
            cancellation=cancellation,
            inference_gate=inference_gate,
        ).run(sources)

    def inspect(self, job: Path | str) -> JobDetails:
        return JobDetails.from_manifest(inspect_job(Path(job)))

    def doctor(self, *, output_dir: Path | None = None) -> DoctorReport:
        return run_doctor(
            self._settings,
            output_dir=output_dir,
            runner=self._runner,
        )

    def capabilities(self) -> ServiceCapabilities:
        settings = self._settings
        translation = _model_installed(settings.translation_model)
        return ServiceCapabilities(
            diarization=_model_installed(settings.diar_model),
            translation=translation,
            formats=("json", "txt", "srt", "vtt"),
            asr_languages=ASR_LANGUAGES,
            translation_languages=TRANSLATION_LANGUAGES if translation else (),
        )

    def save_edits(
        self,
        job: Path | str,
        edits: Sequence[SegmentEdit],
        *,
        merge: bool = False,
    ) -> JobDetails:
        job_dir, manifest = _editable_job(job)
        if not edits:
            raise ConfigurationError("At least one segment edit is required")
        canonical_path = job_dir / "transcript.json"
        transcript = Transcript.load(canonical_path)
        segments = transcript.segments or build_segments(transcript)
        seen: set[int] = set()
        for edit in edits:
            if edit.index >= len(segments):
                raise ConfigurationError(
                    f"Segment {edit.index} does not exist; the transcript has {len(segments)} segments"
                )
            if edit.index in seen:
                raise ConfigurationError(f"Segment {edit.index} is edited more than once")
            seen.add(edit.index)

        canonical_sha = sha256_file(canonical_path)
        texts: dict[int, str] = _existing_edits(job_dir, canonical_sha) if merge else {}
        for edit in edits:
            if edit.text is None:
                texts.pop(edit.index, None)
            else:
                texts[edit.index] = edit.text
        if not texts:
            return self.clear_edits(job)

        edited = [
            replace(segment, text=texts.get(index, " ".join(segment.text.split())))
            for index, segment in enumerate(segments)
        ]
        now = datetime.now(timezone.utc).isoformat()
        atomic_write_json(
            job_dir / EDITED_ARTIFACTS["edited.json"],
            {
                "schema_version": 1,
                "source_transcript_sha256": canonical_sha,
                "edited_indices": sorted(texts),
                "updated_at": now,
                "segments": [
                    {
                        "index": index,
                        "text": segment.text,
                        "start": segment.start,
                        "end": segment.end,
                        "speaker": segment.speaker,
                    }
                    for index, segment in enumerate(edited)
                ],
            },
        )
        requested = manifest.get("options", {}).get("requested_formats")
        formats = set(requested) if isinstance(requested, list) else {"json", "txt", "srt", "vtt"}
        if "txt" in formats:
            atomic_write_text(job_dir / EDITED_ARTIFACTS["edited.txt"], render_segment_text(edited))
        if "srt" in formats:
            atomic_write_text(job_dir / EDITED_ARTIFACTS["edited.srt"], render_srt(edited))
        if "vtt" in formats:
            atomic_write_text(job_dir / EDITED_ARTIFACTS["edited.vtt"], render_vtt(edited))
        manifest["artifacts"] = _artifact_manifest(job_dir)
        manifest["edits"] = {"updated_at": now, "edited_segments": len(texts)}
        manifest["updated_at"] = now
        atomic_write_json(job_dir / "manifest.json", manifest)
        return JobDetails.from_manifest(manifest)

    def clear_edits(self, job: Path | str) -> JobDetails:
        job_dir, manifest = _editable_job(job)
        for filename in EDITED_ARTIFACTS.values():
            (job_dir / filename).unlink(missing_ok=True)
        manifest.pop("edits", None)
        manifest["artifacts"] = _artifact_manifest(job_dir)
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(job_dir / "manifest.json", manifest)
        return JobDetails.from_manifest(manifest)

    def _pipeline_options(self, request: TranscriptionRequest) -> PipelineOptions:
        settings = self._settings
        assert settings.model is not None
        source_language = request.source_language or settings.source_language
        translate_to = request.translate_to or settings.translate_to
        return PipelineOptions(
            output_dir=request.output_dir or Path(settings.output_dir),
            model=Path(settings.model).expanduser(),
            ffmpeg=settings.ffmpeg,
            ffprobe=settings.ffprobe,
            nemo_speech=settings.nemo_speech,
            device=settings.device,
            formats=request.formats or settings.formats,
            audio_stream=request.audio_stream,
            diarize=request.diarize,
            diar_model=Path(settings.diar_model).expanduser() if settings.diar_model else None,
            translation_model=(
                Path(settings.translation_model).expanduser()
                if settings.translation_model and source_language and translate_to
                else None
            ),
            source_language=source_language,
            translate_to=translate_to,
            keep_audio=(
                request.keep_audio if request.keep_audio is not None else settings.keep_audio
            ),
            resume=request.resume if request.resume is not None else settings.resume,
            force=request.force,
            workers=request.workers if request.workers is not None else settings.workers,
            fail_fast=request.fail_fast,
            shared_model=settings.shared_model,
            fingerprint_cache=self._fingerprint_cache,
        )


def _model_installed(model: str | None) -> bool:
    return model is not None and bool(model) and Path(model).expanduser().is_file()


def _editable_job(job: Path | str) -> tuple[Path, dict[str, Any]]:
    path = Path(job)
    job_dir = path.parent if path.is_file() else path
    manifest = inspect_job(job_dir)
    if manifest.get("state") != "completed":
        raise ArtifactConflictError(f"Only completed jobs can be edited: {job_dir}")
    return job_dir, manifest


def _existing_edits(job_dir: Path, canonical_sha: str) -> dict[int, str]:
    path = job_dir / EDITED_ARTIFACTS["edited.json"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("source_transcript_sha256") != canonical_sha
    ):
        return {}
    indices = payload.get("edited_indices")
    segments = payload.get("segments")
    if not isinstance(indices, list) or not isinstance(segments, list):
        return {}
    wanted = {index for index in indices if isinstance(index, int)}
    return {
        item["index"]: item["text"]
        for item in segments
        if isinstance(item, dict)
        and item.get("index") in wanted
        and isinstance(item.get("text"), str)
    }
