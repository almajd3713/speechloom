"""Command-line interface for the local transcription pipeline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from . import __version__
from .config import Settings, load_settings
from .doctor import run_doctor
from .errors import ConfigurationError, PipelineError
from .jobs import Pipeline, PipelineOptions, inspect_job
from .media import discover_inputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parakeet-transcribe",
        description="Transcribe local audio and video with NeMo-Speech.cpp.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="INI configuration file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate runtime, models, backend, and output")
    _add_runtime_options(doctor)
    doctor.add_argument("--output-dir")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable diagnostics")

    transcribe = subparsers.add_parser("transcribe", help="Transcribe one or more media inputs")
    transcribe.add_argument("inputs", nargs="+", type=Path)
    _add_runtime_options(transcribe)
    transcribe.add_argument(
        "-o",
        "--output-dir",
        metavar="DIRECTORY",
        help="Write transcription job directories here (default: transcripts)",
    )
    transcribe.add_argument("--formats", help="Comma-separated: json,txt,srt,vtt")
    transcribe.add_argument("--audio-stream", type=int, help="FFmpeg audio stream index")
    transcribe.add_argument("--recursive", action="store_true", help="Recurse into input directories")
    transcribe.add_argument("--diarize", action="store_true", help="Enable integrated Sortformer diarization")
    transcribe.add_argument("--workers", type=int)
    shared_model_group = transcribe.add_mutually_exclusive_group()
    shared_model_group.add_argument(
        "--shared-model",
        dest="shared_model",
        action="store_true",
        help="Use one native directory invocation for multi-file jobs (default)",
    )
    shared_model_group.add_argument(
        "--no-shared-model",
        dest="shared_model",
        action="store_false",
        help="Invoke the native runtime separately for each input",
    )
    transcribe.add_argument("--keep-audio", action="store_true", default=None)
    transcribe.add_argument("--force", action="store_true", help="Recompute and atomically replace job artifacts")
    transcribe.add_argument("--fail-fast", action="store_true", help="Stop scheduling after the first failure")
    resume_group = transcribe.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", dest="resume", action="store_true")
    resume_group.add_argument("--no-resume", dest="resume", action="store_false")
    transcribe.set_defaults(resume=None, shared_model=None)
    transcribe.add_argument("--json", action="store_true", help="Emit machine-readable job results")

    inspect = subparsers.add_parser("inspect", help="Inspect a job directory or manifest")
    inspect.add_argument("job", type=Path)
    inspect.add_argument("--json", action="store_true", help="Emit the complete manifest")
    return parser


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--nemo-speech", dest="nemo_speech")
    parser.add_argument("--model")
    parser.add_argument("--diar-model", dest="diar_model")
    parser.add_argument("--device", help="auto, cpu, cuda[:N], metal, vulkan[:N], or gpu[:N]")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            return _inspect(args)
        cli_values = _settings_cli_values(args)
        settings = load_settings(config_path=args.config, cli_values=cli_values)
        if args.command == "doctor":
            return _doctor(args, settings)
        if args.command == "transcribe":
            return _transcribe(args, settings)
        parser.error(f"Unknown command: {args.command}")
    except KeyboardInterrupt:
        print("Interrupted; partial job state was preserved for resume.", file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _doctor(args: argparse.Namespace, settings: Settings) -> int:
    report = run_doctor(
        settings,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for check in report.checks:
            marker = {"ok": "OK", "warn": "WARN", "error": "ERROR"}.get(check.status, check.status.upper())
            print(f"[{marker}] {check.name}: {check.message}")
        print("Ready." if report.ready else "Not ready; resolve the errors above.")
    return 0 if report.ready else 1


def _transcribe(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.model:
        raise ConfigurationError(
            "No ASR model configured; pass --model or set PARAKEET_TRANSCRIBE_MODEL"
        )
    if args.diarize and not settings.diar_model:
        raise ConfigurationError("--diarize requires --diar-model or PARAKEET_TRANSCRIBE_DIAR_MODEL")
    sources = discover_inputs(args.inputs, recursive=args.recursive)
    options = PipelineOptions(
        output_dir=Path(settings.output_dir),
        model=Path(settings.model).expanduser(),
        ffmpeg=settings.ffmpeg,
        ffprobe=settings.ffprobe,
        nemo_speech=settings.nemo_speech,
        device=settings.device,
        formats=settings.formats,
        audio_stream=args.audio_stream,
        diarize=args.diarize,
        diar_model=Path(settings.diar_model).expanduser() if settings.diar_model else None,
        keep_audio=settings.keep_audio,
        resume=settings.resume,
        force=args.force,
        workers=settings.workers,
        fail_fast=args.fail_fast,
        shared_model=settings.shared_model,
    )
    results = Pipeline(options).run(sources)
    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            if result.error:
                print(f"FAILED {result.source}: {result.error}", file=sys.stderr)
            elif result.skipped:
                print(f"SKIPPED {result.source} -> {result.job_dir}")
            else:
                print(f"CREATED {result.source} -> {result.job_dir}")
    return 1 if any(result.error for result in results) else 0


def _inspect(args: argparse.Namespace) -> int:
    manifest = inspect_job(args.job)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        source = manifest.get("source", {}).get("path", "unknown")
        print(f"Job: {manifest.get('job_id', 'unknown')}")
        print(f"State: {manifest.get('state', 'unknown')}")
        print(f"Source: {source}")
        for format_name, artifact in manifest.get("artifacts", {}).items():
            print(f"{format_name.upper()}: {artifact.get('path')} ({artifact.get('size')} bytes)")
        error = manifest.get("error")
        if error:
            print(f"Error: {error.get('message', error)}")
    return 0


def _settings_cli_values(args: argparse.Namespace) -> dict[str, Any]:
    names = (
        "ffmpeg", "ffprobe", "nemo_speech", "model", "diar_model", "device",
        "output_dir", "workers", "keep_audio", "resume", "formats",
    )
    return {name: getattr(args, name, None) for name in names}
