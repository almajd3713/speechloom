"""Application service shared by the CLI, Python callers, and future adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from .config import Settings, load_managed_settings
from .contracts import CancellationToken, JobDetails, StageEvent, TranscriptionRequest
from .doctor import DoctorReport, run_doctor
from .errors import ConfigurationError
from .jobs import JobResult, Pipeline, PipelineOptions, inspect_job
from .media import discover_inputs
from .process import CommandResult, run_command


Runner = Callable[..., CommandResult]
EventSink = Callable[[StageEvent], None]


class TranscriptionService:
    """Stable application boundary over configuration, diagnostics, and jobs."""

    def __init__(self, settings: Settings, *, runner: Runner = run_command) -> None:
        self._settings = settings
        self._runner = runner

    @classmethod
    def from_default_config(
        cls,
        *,
        config_path: Path | None = None,
        env: Mapping[str, str] | None = None,
        runner: Runner = run_command,
    ) -> "TranscriptionService":
        return cls(load_managed_settings(config_path=config_path, env=env), runner=runner)

    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        on_event: EventSink | None = None,
        cancellation: CancellationToken | None = None,
    ) -> list[JobResult]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if not self._settings.model:
            raise ConfigurationError(
                "No ASR model configured; pass --model or set SPEECHLOOM_MODEL"
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

        sources = discover_inputs(list(request.inputs), recursive=request.recursive)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        options = self._pipeline_options(request)
        return Pipeline(
            options,
            runner=self._runner,
            on_event=on_event,
            cancellation=cancellation,
        ).run(sources)

    def inspect(self, job: Path | str) -> JobDetails:
        return JobDetails.from_manifest(inspect_job(Path(job)))

    def doctor(self, *, output_dir: Path | None = None) -> DoctorReport:
        return run_doctor(
            self._settings,
            output_dir=output_dir,
            runner=self._runner,
        )

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
                if settings.translation_model
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
        )
