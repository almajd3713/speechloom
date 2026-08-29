"""Resumable single-file and shared-model batch orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Callable

from . import __version__
from .artifacts import atomic_write_json, atomic_write_text, file_identity, read_json, sha256_file
from .contracts import CancellationToken, StageEvent
from .errors import (
    ArtifactConflictError,
    CancellationError,
    ConfigurationError,
    InferenceError,
    PipelineError,
)
from .media import can_passthrough_wav, normalize_audio, probe_media, select_audio_stream
from .nemo import (
    NemoOptions,
    TranslationOptions,
    adapt_payload,
    map_native_failure,
    transcribe,
    transcribe_directory,
    translate_texts,
)
from .process import CommandResult, run_command
from .renderers import (
    build_segments,
    render_srt,
    render_text,
    render_translation_text,
    render_vtt,
)
from .schema import SCHEMA_VERSION, Transcript, TranslatedSegment, Translation


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
    translation_model: Path | None = None
    source_language: str | None = None
    translate_to: str | None = None
    keep_audio: bool = False
    resume: bool = True
    force: bool = False
    workers: int = 1
    fail_fast: bool = False
    shared_model: bool = True


@dataclass(frozen=True)
class JobResult:
    source: str
    job_dir: str
    state: str
    skipped: bool = False
    error: str | None = None


@dataclass
class PreparedJob:
    source: Path
    source_identity: dict[str, Any]
    model_identity: dict[str, Any]
    diar_identity: dict[str, Any] | None
    translation_identity: dict[str, Any] | None
    job_id: str
    job_dir: Path
    work_dir: Path
    manifest_path: Path
    canonical_path: Path
    manifest: dict[str, Any]
    audio_path: Path
    duration: float | None
    passthrough: bool
    started: float
    transcript: Transcript | None = None
    translation: Translation | None = None


class Pipeline:
    def __init__(
        self,
        options: PipelineOptions,
        *,
        runner: Callable[..., CommandResult] = run_command,
        on_event: Callable[[StageEvent], None] | None = None,
        cancellation: CancellationToken | None = None,
        inference_gate: AbstractContextManager[Any] | None = None,
    ) -> None:
        if options.workers < 1:
            raise ConfigurationError("workers must be at least 1")
        translation_values = (
            options.translation_model,
            options.source_language,
            options.translate_to,
        )
        if any(translation_values) and not all(translation_values):
            raise ConfigurationError(
                "translation_model, source_language, and translate_to must be configured together"
            )
        if options.source_language and options.source_language == options.translate_to:
            raise ConfigurationError("source_language and translate_to must be different")
        self.options = options
        self._runner = runner
        self._on_event = on_event
        self._cancellation = cancellation
        self._inference_gate = inference_gate
        self._event_lock = threading.RLock()
        self.runner = self._run_command
        self._identity_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._tool_versions: dict[str, str] | None = None

    def run(self, sources: list[Path]) -> list[JobResult]:
        sources = [source.expanduser().resolve() for source in sources]
        for completed, source in enumerate(sources, start=1):
            self._emit(
                "queued",
                "queued",
                "Input queued",
                source=source,
                completed=completed,
                total=len(sources),
            )
        try:
            self._check_cancelled()
        except CancellationError as exc:
            return [self._cancelled_result(source, exc, None) for source in sources]
        if len(sources) > 1 and self.options.shared_model:
            return self._run_shared_batch(sources)
        return self._run_isolated(sources)

    def _run_command(self, argv, **kwargs) -> CommandResult:
        if self._cancellation is not None:
            kwargs.setdefault("cancellation", self._cancellation)
        return self._runner(argv, **kwargs)

    def _check_cancelled(self) -> None:
        if self._cancellation is not None:
            self._cancellation.raise_if_cancelled()

    def _emit(
        self,
        stage: str,
        status: str,
        message: str,
        *,
        prepared: PreparedJob | None = None,
        source: Path | None = None,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        if self._on_event is None:
            return
        event = StageEvent(
            job_id=prepared.job_id if prepared is not None else None,
            source=prepared.source if prepared is not None else source,
            stage=stage,
            status=status,
            message=message,
            completed=completed,
            total=total,
        )
        with self._event_lock:
            self._on_event(event)

    def _inference(self) -> AbstractContextManager[Any]:
        return self._inference_gate if self._inference_gate is not None else nullcontext()

    def run_one(self, source: Path) -> JobResult:
        prepared: PreparedJob | None = None
        try:
            prepared_or_result = self._prepare(source)
            if isinstance(prepared_or_result, JobResult):
                return prepared_or_result
            prepared = prepared_or_result
            if prepared.transcript is None:
                self._check_cancelled()
                self._emit(
                    "transcribing",
                    "started",
                    "Transcription started",
                    prepared=prepared,
                )
                step_started = time.monotonic()
                with self._inference():
                    prepared.transcript = transcribe(
                        prepared.audio_path,
                        self._nemo_options(),
                        duration=prepared.duration,
                        runner=self.runner,
                    )
                prepared.manifest.setdefault("timings", {})["inference_seconds"] = (
                    time.monotonic() - step_started
                )
            self._commit_transcript(prepared)
            self._emit(
                "transcribing",
                "completed",
                "Canonical transcript committed",
                prepared=prepared,
            )
            self._translate_jobs([prepared])
            return self._complete(prepared)
        except CancellationError as exc:
            return self._cancelled_result(source, exc, prepared)
        except KeyboardInterrupt:
            if prepared is not None:
                self._mark_interrupted(prepared)
            raise
        except Exception as exc:
            return self._failed_result(source, exc, prepared)

    def _run_isolated(self, sources: list[Path]) -> list[JobResult]:
        if self.options.workers == 1 or len(sources) <= 1:
            results: list[JobResult] = []
            for source in sources:
                result = self.run_one(source)
                results.append(result)
                if result.state == "cancelled":
                    for unstarted in sources[len(results) :]:
                        results.append(
                            self._cancelled_result(
                                unstarted,
                                CancellationError("Operation cancelled"),
                                None,
                            )
                        )
                    break
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
                except Exception as exc:  # defensive isolation at the thread boundary
                    result = JobResult(str(source), "", "failed", error=str(exc))
                results_by_source[str(source)] = result
                if result.error and self.options.fail_fast:
                    for pending in future_map:
                        pending.cancel()
                    break
        return [results_by_source[str(source)] for source in sources if str(source) in results_by_source]

    def _run_shared_batch(self, sources: list[Path]) -> list[JobResult]:
        results_by_source, pending, halted = self._prepare_batch(sources)
        if halted:
            if self._cancellation is not None and self._cancellation.is_cancelled():
                results_by_source.update(
                    self._cancel_prepared(pending, CancellationError("Operation cancelled"))
                )
            else:
                for prepared in pending:
                    self._mark_interrupted(prepared)
            return _ordered_results(sources, results_by_source)
        if not pending:
            return _ordered_results(sources, results_by_source)

        try:
            results_by_source.update(self._transcribe_shared(pending))
        except CancellationError as exc:
            results_by_source.update(self._cancel_prepared(pending, exc))
        except KeyboardInterrupt:
            for prepared in pending:
                self._mark_interrupted(prepared)
            raise
        except Exception as exc:
            results_by_source.update(self._fail_prepared(pending, exc))
        return _ordered_results(sources, results_by_source)

    def _prepare_batch(
        self, sources: list[Path]
    ) -> tuple[dict[str, JobResult], list[PreparedJob], bool]:
        results: dict[str, JobResult] = {}
        pending: list[PreparedJob] = []
        for source in sources:
            try:
                self._check_cancelled()
                prepared_or_result = self._prepare(source)
                if isinstance(prepared_or_result, JobResult):
                    result = prepared_or_result
                elif prepared_or_result.transcript is not None:
                    self._commit_transcript(prepared_or_result)
                    self._translate_jobs([prepared_or_result])
                    result = self._complete(prepared_or_result)
                else:
                    pending.append(prepared_or_result)
                    continue
            except CancellationError as exc:
                result = self._cancelled_result(source, exc, None)
            except Exception as exc:
                result = self._failed_result(source, exc, None)
            results[str(source)] = result
            if result.state == "cancelled":
                return results, pending, True
            if result.error and self.options.fail_fast:
                return results, pending, True
        return results, pending, False

    def _transcribe_shared(self, pending: list[PreparedJob]) -> dict[str, JobResult]:
        self._check_cancelled()
        for prepared in pending:
            self._emit(
                "transcribing",
                "started",
                "Shared-model transcription started",
                prepared=prepared,
            )
        output_root = self.options.output_dir.expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".parakeet-batch-", dir=output_root) as temporary:
            staging_root = Path(temporary)
            input_dir = staging_root / "input"
            output_dir = staging_root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            staged = self._stage_batch(pending, input_dir)
            step_started = time.monotonic()
            with self._inference():
                command_result = transcribe_directory(
                    input_dir,
                    output_dir,
                    self._nemo_options(),
                    concurrency=self.options.workers,
                    runner=self.runner,
                )
            elapsed = time.monotonic() - step_started
            return self._collect_batch_results(staged, output_dir, command_result, elapsed)

    @staticmethod
    def _stage_batch(
        pending: list[PreparedJob], input_dir: Path
    ) -> list[tuple[PreparedJob, str]]:
        staged = [
            (prepared, f"{index:06d}-{prepared.job_id}")
            for index, prepared in enumerate(pending)
        ]
        for prepared, native_stem in staged:
            _link_or_copy(prepared.audio_path, input_dir / f"{native_stem}.wav")
        return staged

    def _collect_batch_results(
        self,
        staged: list[tuple[PreparedJob, str]],
        output_dir: Path,
        command_result: CommandResult,
        elapsed: float,
    ) -> dict[str, JobResult]:
        results: dict[str, JobResult] = {}
        native_error = map_native_failure(command_result) if command_result.returncode else None
        completed_asr: list[PreparedJob] = []
        for prepared, native_stem in staged:
            self._check_cancelled()
            result_path = output_dir / f"{native_stem}.json"
            if not result_path.is_file():
                error = native_error or InferenceError(
                    f"NeMo batch produced no result for: {prepared.source}"
                )
                results[str(prepared.source)] = self._failed_result(prepared.source, error, prepared)
                continue
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                prepared.transcript = adapt_payload(payload, duration=prepared.duration)
                timings = prepared.manifest.setdefault("timings", {})
                timings["batch_inference_seconds"] = elapsed
                timings["batch_size"] = len(staged)
                self._commit_transcript(prepared)
                self._emit(
                    "transcribing",
                    "completed",
                    "Canonical transcript committed",
                    prepared=prepared,
                )
                completed_asr.append(prepared)
            except CancellationError:
                raise
            except Exception as exc:
                results[str(prepared.source)] = self._failed_result(prepared.source, exc, prepared)
        try:
            self._translate_jobs(completed_asr)
        except CancellationError as exc:
            results.update(self._cancel_prepared(completed_asr, exc))
            return results
        except Exception as exc:
            results.update(self._fail_prepared(completed_asr, exc))
            return results
        for prepared in completed_asr:
            try:
                results[str(prepared.source)] = self._complete(prepared)
            except CancellationError as exc:
                results[str(prepared.source)] = self._cancelled_result(
                    prepared.source, exc, prepared
                )
            except Exception as exc:
                results[str(prepared.source)] = self._failed_result(prepared.source, exc, prepared)
        return results

    def _fail_prepared(
        self, pending: list[PreparedJob], exc: Exception
    ) -> dict[str, JobResult]:
        return {
            str(prepared.source): self._failed_result(prepared.source, exc, prepared)
            for prepared in pending
        }

    def _cancel_prepared(
        self, pending: list[PreparedJob], exc: CancellationError
    ) -> dict[str, JobResult]:
        return {
            str(prepared.source): self._cancelled_result(prepared.source, exc, prepared)
            for prepared in pending
        }

    def _prepare(self, source: Path) -> PreparedJob | JobResult:
        source = source.expanduser().resolve()
        self._check_cancelled()
        self._emit("validating", "started", "Validating input and models", source=source)
        started = time.monotonic()
        source_identity = self._identity(source)
        model_identity = self._identity(self.options.model)
        diar_identity = self._identity(self.options.diar_model) if self.options.diar_model else None
        translation_identity = (
            self._identity(self.options.translation_model)
            if self.options.translation_model is not None
            else None
        )
        tool_versions = self._versions()
        semantic = self._semantic_options(model_identity, diar_identity, tool_versions)
        job_id = _job_id(source_identity, semantic)
        job_dir = self.options.output_dir.expanduser().resolve() / _job_dir_name(source, job_id)
        work_dir = job_dir / ".work"
        manifest_path = job_dir / "manifest.json"
        canonical_path = job_dir / "transcript.json"

        existing = _load_manifest_if_present(manifest_path)
        if existing and existing.get("state") == "completed" and self.options.resume and not self.options.force:
            source_ready = _requested_artifacts_exist(job_dir, self.options.formats)
            translation_matches = self._translation_matches(existing, translation_identity)
            translation_ready = not self._translation_requested() or (
                translation_matches
                and _requested_translation_artifacts_exist(
                    job_dir, self.options.formats, self.options.translate_to or ""
                )
            )
            if source_ready and translation_ready:
                self._emit(
                    "completed",
                    "skipped",
                    "Requested artifacts are already complete",
                    source=source,
                )
                return JobResult(str(source), str(job_dir), "completed", skipped=True)
            translation_path = _translation_path(job_dir, self.options.translate_to or "")
            if self._translation_requested() and translation_path.exists() and not translation_matches:
                raise ArtifactConflictError(
                    f"Translation output already exists with different settings: {translation_path}; "
                    "use --force"
                )
        if existing and not self.options.resume and not self.options.force:
            raise ArtifactConflictError(f"Job output already exists: {job_dir}; use --resume or --force")

        job_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        manifest = existing if existing and not self.options.force else _new_manifest(
            job_id=job_id,
            source=source_identity,
            model=model_identity,
            diar_model=diar_identity,
            translation_model=translation_identity,
            options={**semantic, "requested_formats": list(self.options.formats)},
            tools=tool_versions,
        )
        manifest.pop("error", None)
        manifest["state"] = "processing"
        manifest["updated_at"] = _now()
        atomic_write_json(manifest_path, manifest)

        prepared = PreparedJob(
            source=source,
            source_identity=source_identity,
            model_identity=model_identity,
            diar_identity=diar_identity,
            translation_identity=translation_identity,
            job_id=job_id,
            job_dir=job_dir,
            work_dir=work_dir,
            manifest_path=manifest_path,
            canonical_path=canonical_path,
            manifest=manifest,
            audio_path=source,
            duration=None,
            passthrough=True,
            started=started,
        )
        self._emit(
            "validating",
            "completed",
            "Input and model identities validated",
            prepared=prepared,
        )

        if not self.options.force and existing and canonical_path.is_file():
            prepared.transcript = Transcript.load(canonical_path)
            prepared.duration = prepared.transcript.duration
            self._emit(
                "transcribing",
                "completed",
                "Loaded committed canonical transcript",
                prepared=prepared,
            )
            translation_path = _translation_path(job_dir, self.options.translate_to or "")
            if self._translation_matches(existing, translation_identity) and translation_path.is_file():
                prepared.translation = Translation.load(translation_path)
                self._emit(
                    "translating",
                    "completed",
                    "Loaded committed translation",
                    prepared=prepared,
                )
            return prepared

        try:
            self._check_cancelled()
            self._emit("probing", "started", "Probing media streams", prepared=prepared)
            media = probe_media(source, self.options.ffprobe, runner=self.runner)
            stream = select_audio_stream(media, self.options.audio_stream)
            manifest["media"] = media.to_dict()
            manifest["selected_audio_stream"] = asdict(stream)
            atomic_write_json(manifest_path, manifest)
            self._emit("probing", "completed", "Audio stream selected", prepared=prepared)

            normalized_path = work_dir / "audio.wav"
            passthrough = can_passthrough_wav(source, stream)
            audio_path = source if passthrough else normalized_path
            can_resume_audio = (
                not self.options.force
                and manifest.get("state_detail") in {"normalized", "transcribed"}
                and normalized_path.is_file()
            )
            if not passthrough and not can_resume_audio:
                self._check_cancelled()
                self._emit(
                    "normalizing",
                    "started",
                    "Normalizing media audio",
                    prepared=prepared,
                )
                step_started = time.monotonic()
                normalize_audio(source, normalized_path, stream, self.options.ffmpeg, runner=self.runner)
                manifest.setdefault("timings", {})["normalization_seconds"] = (
                    time.monotonic() - step_started
                )
            manifest["state_detail"] = "normalized"
            manifest["normalized_audio"] = {"path": str(audio_path), "passthrough": passthrough}
            atomic_write_json(manifest_path, manifest)
            prepared.audio_path = audio_path
            prepared.duration = media.duration
            prepared.passthrough = passthrough
            normalization_message = (
                "Audio already matches the canonical format"
                if passthrough
                else "Normalized audio is ready"
            )
            self._emit(
                "normalizing",
                "completed",
                normalization_message,
                prepared=prepared,
            )
            return prepared
        except CancellationError as exc:
            return self._cancelled_result(source, exc, prepared)
        except KeyboardInterrupt:
            self._mark_interrupted(prepared)
            raise
        except Exception as exc:
            return self._failed_result(source, exc, prepared)

    def _complete(self, prepared: PreparedJob) -> JobResult:
        self._check_cancelled()
        if prepared.transcript is None:
            raise InferenceError("Cannot complete a job without a transcript")
        self._commit_transcript(prepared)
        if self._translation_requested() and prepared.translation is None:
            raise InferenceError("Cannot complete a translated job without translated segments")

        self._emit("rendering", "started", "Rendering requested artifacts", prepared=prepared)
        self._render(prepared.job_dir, prepared.transcript)
        self._check_cancelled()
        if prepared.translation is not None:
            self._render_translation(prepared.job_dir, prepared.translation)
            prepared.manifest["translation"] = {
                "model": prepared.translation_identity,
                "source_language": prepared.translation.source_language,
                "target_language": prepared.translation.target_language,
            }
        prepared.manifest.setdefault("options", {})["requested_formats"] = list(
            self.options.formats
        )
        prepared.manifest["artifacts"] = _artifact_manifest(prepared.job_dir)
        prepared.manifest["state"] = "completed"
        prepared.manifest["state_detail"] = "completed"
        prepared.manifest["updated_at"] = _now()
        prepared.manifest.setdefault("timings", {})["total_seconds"] = time.monotonic() - prepared.started
        atomic_write_json(prepared.manifest_path, prepared.manifest)
        self._cleanup_audio(prepared)
        self._emit("rendering", "completed", "Requested artifacts rendered", prepared=prepared)
        self._emit("completed", "completed", "Job completed", prepared=prepared)
        return JobResult(str(prepared.source), str(prepared.job_dir), "completed")

    def _commit_transcript(self, prepared: PreparedJob) -> None:
        if prepared.transcript is None:
            raise InferenceError("Cannot commit a job without a transcript")
        transcript = prepared.transcript
        transcript.segments = transcript.segments or build_segments(transcript)
        transcript.provenance.update(
            {
                "source_sha256": prepared.source_identity["sha256"],
                "model_sha256": prepared.model_identity["sha256"],
            }
        )
        if prepared.diar_identity:
            transcript.provenance["diar_model_sha256"] = prepared.diar_identity["sha256"]
        atomic_write_json(prepared.canonical_path, transcript.to_dict())
        prepared.manifest["state_detail"] = "transcribed"
        atomic_write_json(prepared.manifest_path, prepared.manifest)

    def _translate_jobs(self, prepared_jobs: list[PreparedJob]) -> None:
        if not self._translation_requested():
            return
        pending = [prepared for prepared in prepared_jobs if prepared.translation is None]
        if not pending:
            return
        self._check_cancelled()
        for prepared in pending:
            self._emit(
                "translating",
                "started",
                "Translation started",
                prepared=prepared,
            )
        source_texts: list[str] = []
        ranges: list[tuple[PreparedJob, int, int]] = []
        for prepared in pending:
            if prepared.transcript is None:
                raise InferenceError("Cannot translate a job without a transcript")
            prepared.transcript.segments = prepared.transcript.segments or build_segments(
                prepared.transcript
            )
            start = len(source_texts)
            source_texts.extend(segment.text for segment in prepared.transcript.segments)
            ranges.append((prepared, start, len(source_texts)))
        if not source_texts:
            raise InferenceError("Cannot translate an empty transcript")

        step_started = time.monotonic()
        with self._inference():
            translated_texts = translate_texts(
                source_texts,
                self._translation_options(),
                runner=self.runner,
            )
        elapsed = time.monotonic() - step_started
        for prepared, start, end in ranges:
            self._check_cancelled()
            assert prepared.transcript is not None
            source_segments = prepared.transcript.segments
            translated_segments = [
                TranslatedSegment(
                    source_text=source.text,
                    text=translated,
                    start=source.start,
                    end=source.end,
                    speaker=source.speaker,
                )
                for source, translated in zip(source_segments, translated_texts[start:end])
            ]
            prepared.translation = Translation(
                source_language=self.options.source_language or "",
                target_language=self.options.translate_to or "",
                segments=translated_segments,
                provenance={
                    "engine": "NeMo-Speech.cpp",
                    "model_path": str((self.options.translation_model or Path()).resolve()),
                    "model_sha256": (prepared.translation_identity or {}).get("sha256"),
                    "source_transcript_sha256": sha256_file(prepared.canonical_path),
                    "device": self.options.device,
                },
            )
            prepared.translation.validate()
            prepared.manifest.setdefault("timings", {})["translation_batch_seconds"] = elapsed
            prepared.manifest["state_detail"] = "translated"
            atomic_write_json(
                _translation_path(prepared.job_dir, prepared.translation.target_language),
                prepared.translation.to_dict(),
            )
            atomic_write_json(prepared.manifest_path, prepared.manifest)
            self._emit(
                "translating",
                "completed",
                "Translation committed",
                prepared=prepared,
            )

    def _render(self, job_dir: Path, transcript: Transcript) -> None:
        atomic_write_json(job_dir / "transcript.json", transcript.to_dict())
        formats = set(self.options.formats)
        if "txt" in formats:
            self._check_cancelled()
            atomic_write_text(job_dir / "transcript.txt", render_text(transcript))
        segments = transcript.segments or build_segments(transcript)
        if "srt" in formats:
            self._check_cancelled()
            atomic_write_text(job_dir / "subtitles.srt", render_srt(segments))
        if "vtt" in formats:
            self._check_cancelled()
            atomic_write_text(job_dir / "subtitles.vtt", render_vtt(segments))

    def _render_translation(self, job_dir: Path, translation: Translation) -> None:
        target = translation.target_language
        atomic_write_json(_translation_path(job_dir, target), translation.to_dict())
        formats = set(self.options.formats)
        if "txt" in formats:
            self._check_cancelled()
            atomic_write_text(job_dir / f"translation.{target}.txt", render_translation_text(translation))
        segments = [segment.as_subtitle_segment() for segment in translation.segments]
        if "srt" in formats:
            self._check_cancelled()
            atomic_write_text(job_dir / f"subtitles.{target}.srt", render_srt(segments))
        if "vtt" in formats:
            self._check_cancelled()
            atomic_write_text(job_dir / f"subtitles.{target}.vtt", render_vtt(segments))

    def _cleanup_audio(self, prepared: PreparedJob) -> None:
        if not prepared.passthrough and not self.options.keep_audio:
            prepared.audio_path.unlink(missing_ok=True)
            try:
                prepared.work_dir.rmdir()
            except OSError:
                pass

    def _mark_interrupted(self, prepared: PreparedJob) -> None:
        prepared.manifest["state"] = "interrupted"
        prepared.manifest["updated_at"] = _now()
        atomic_write_json(prepared.manifest_path, prepared.manifest)
        self._emit("interrupted", "interrupted", "Job interrupted", prepared=prepared)

    def _cancelled_result(
        self,
        source: Path,
        exc: CancellationError,
        prepared: PreparedJob | None,
    ) -> JobResult:
        if prepared is not None:
            prepared.manifest["state"] = "cancelled"
            prepared.manifest["updated_at"] = _now()
            prepared.manifest["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            prepared.manifest.setdefault("timings", {})["total_seconds"] = (
                time.monotonic() - prepared.started
            )
            atomic_write_json(prepared.manifest_path, prepared.manifest)
        self._emit(
            "cancelled",
            "cancelled",
            str(exc),
            prepared=prepared,
            source=source,
        )
        return JobResult(
            str(source),
            str(prepared.job_dir) if prepared else "",
            "cancelled",
            error=str(exc),
        )

    def _failed_result(
        self,
        source: Path,
        exc: Exception,
        prepared: PreparedJob | None,
    ) -> JobResult:
        if prepared is not None:
            prepared.manifest["state"] = "failed"
            prepared.manifest["updated_at"] = _now()
            prepared.manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
            prepared.manifest.setdefault("timings", {})["total_seconds"] = (
                time.monotonic() - prepared.started
            )
            atomic_write_json(prepared.manifest_path, prepared.manifest)
        self._emit(
            "failed",
            "failed",
            str(exc),
            prepared=prepared,
            source=source,
        )
        return JobResult(
            str(source),
            str(prepared.job_dir) if prepared else "",
            "failed",
            error=str(exc),
        )

    def _nemo_options(self) -> NemoOptions:
        return NemoOptions(
            executable=self.options.nemo_speech,
            model=self.options.model,
            device=self.options.device,
            diarize=self.options.diarize,
            diar_model=self.options.diar_model,
        )

    def _translation_options(self) -> TranslationOptions:
        if not self._translation_requested() or self.options.translation_model is None:
            raise ConfigurationError("Translation is not fully configured")
        return TranslationOptions(
            executable=self.options.nemo_speech,
            model=self.options.translation_model,
            source_language=self.options.source_language or "",
            target_language=self.options.translate_to or "",
            device=self.options.device,
        )

    def _translation_requested(self) -> bool:
        return self.options.translation_model is not None

    def _translation_matches(
        self,
        manifest: dict[str, Any],
        translation_identity: dict[str, Any] | None,
    ) -> bool:
        if not self._translation_requested() or translation_identity is None:
            return False
        existing = manifest.get("translation")
        return bool(
            isinstance(existing, dict)
            and isinstance(existing.get("model"), dict)
            and existing["model"].get("sha256") == translation_identity.get("sha256")
            and existing.get("source_language") == self.options.source_language
            and existing.get("target_language") == self.options.translate_to
        )

    def _semantic_options(
        self,
        model_identity: dict[str, Any],
        diar_identity: dict[str, Any] | None,
        tool_versions: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "pipeline_version": __version__,
            "transcript_schema": SCHEMA_VERSION,
            "model_sha256": model_identity["sha256"],
            "diar_model_sha256": diar_identity["sha256"] if diar_identity else None,
            "device": self.options.device,
            "audio_stream": self.options.audio_stream,
            "diarize": self.options.diarize,
            "ffmpeg_version": tool_versions["ffmpeg"],
            "nemo_speech_version": tool_versions["nemo_speech"],
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
    translation_model: dict[str, Any] | None,
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
        "translation_model": translation_model,
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


def _artifact_manifest(job_dir: Path) -> dict[str, Any]:
    names = {
        "json": "transcript.json",
        "txt": "transcript.txt",
        "srt": "subtitles.srt",
        "vtt": "subtitles.vtt",
    }
    output: dict[str, Any] = {}
    for format_name, filename in names.items():
        path = job_dir / filename
        if path.is_file():
            output[format_name] = {
                "path": path.name,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    translated_files = sorted(job_dir.glob("translation.*.json"))
    translated_files.extend(sorted(job_dir.glob("translation.*.txt")))
    translated_files.extend(sorted(job_dir.glob("subtitles.*.srt")))
    translated_files.extend(sorted(job_dir.glob("subtitles.*.vtt")))
    for path in translated_files:
        output[path.name] = {
            "path": path.name,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return output


def _requested_artifacts_exist(job_dir: Path, formats: tuple[str, ...]) -> bool:
    names = {
        "json": "transcript.json",
        "txt": "transcript.txt",
        "srt": "subtitles.srt",
        "vtt": "subtitles.vtt",
    }
    return (job_dir / "transcript.json").is_file() and all(
        (job_dir / names[format_name]).is_file() for format_name in formats
    )


def _translation_path(job_dir: Path, target_language: str) -> Path:
    return job_dir / f"translation.{target_language}.json"


def _requested_translation_artifacts_exist(
    job_dir: Path,
    formats: tuple[str, ...],
    target_language: str,
) -> bool:
    names = {
        "json": f"translation.{target_language}.json",
        "txt": f"translation.{target_language}.txt",
        "srt": f"subtitles.{target_language}.srt",
        "vtt": f"subtitles.{target_language}.vtt",
    }
    return _translation_path(job_dir, target_language).is_file() and all(
        (job_dir / names[format_name]).is_file() for format_name in formats
    )


def _ordered_results(
    sources: list[Path], results_by_source: dict[str, JobResult]
) -> list[JobResult]:
    return [
        results_by_source[str(source)]
        for source in sources
        if str(source) in results_by_source
    ]


def _job_id(source: dict[str, Any], options: dict[str, Any]) -> str:
    payload = json.dumps(
        {"source": source["sha256"], "options": options},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _job_dir_name(source: Path, job_id: str) -> str:
    safe_stem = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE).strip("-.") or "media"
    path_id = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{safe_stem}-{job_id[:10]}-{path_id}"


def _tool_version(argv: list[str], runner: Callable[..., CommandResult]) -> str:
    try:
        result = runner(argv)
    except Exception as exc:
        return f"unavailable: {exc}"
    line = (result.stdout.strip() or result.stderr.strip()).splitlines()
    return line[0] if line else "unknown"


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.symlink(source.resolve(), destination)
    except OSError:
        shutil.copyfile(source, destination)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
