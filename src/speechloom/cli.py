"""Command-line interface for the local transcription pipeline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence

from . import __version__
from .config import Settings, load_managed_settings
from .contracts import TranscriptionRequest
from .errors import ConfigurationError, MissingDependencyError, PipelineError
from .service import TranscriptionService
from .setup import SetupManager, SetupRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speechloom",
        description="Transcribe local audio and video with NeMo-Speech.cpp.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="INI configuration file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Install and verify managed runtime assets")
    setup.add_argument("--backend", choices=("auto", "cpu", "cuda", "vulkan"), default="auto")
    setup.add_argument("--features", help="Comma-separated: translation,diarization")
    setup.add_argument("--from-source", action="store_true", help="Build the runtime from pinned source")
    setup.add_argument("--keep-cache", action="store_true", help="Keep converter and model caches")
    setup.add_argument("--json", action="store_true", help="Emit machine-readable setup results")
    setup_actions = setup.add_subparsers(dest="setup_action")
    setup_status = setup_actions.add_parser("status", help="Verify managed installation state")
    setup_status.add_argument("--json", action="store_true", help="Emit machine-readable status")
    setup_clean = setup_actions.add_parser("clean", help="Remove managed setup caches")
    setup_clean.add_argument("--downloads", action="store_true", help="Remove cached downloads")
    setup_clean.add_argument("--build-tools", action="store_true", help="Remove isolated build tools")
    setup_clean.add_argument("--all", action="store_true", dest="all_cache", help="Remove all setup caches")

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

    serve = subparsers.add_parser("serve", help="Run the optional local HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--allow-remote", action="store_true")
    serve.add_argument(
        "--allow-root",
        action="append",
        type=Path,
        default=[],
        help="Allow local-path jobs below this root (repeatable)",
    )
    serve.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="Allow this browser origin (repeatable)",
    )
    serve.add_argument("--max-upload-mb", type=int, default=2048)
    serve.add_argument("--queue-size", type=int, default=16)
    serve.add_argument("--media-workers", type=int, default=2)
    return parser


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--nemo-speech", dest="nemo_speech")
    parser.add_argument("--model")
    parser.add_argument("--diar-model", dest="diar_model")
    parser.add_argument("--translation-model", dest="translation_model")
    parser.add_argument("--source-language", dest="source_language", help="Spoken language, for example ru")
    parser.add_argument("--translate-to", dest="translate_to", help="Translate transcript outputs, for example en")
    parser.add_argument("--device", help="auto, cpu, cuda[:N], metal, vulkan[:N], or gpu[:N]")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            return _setup(args, SetupManager(config_path=args.config))
        if args.command == "inspect":
            return _inspect(args, TranscriptionService(Settings()))
        if args.command == "serve":
            return _serve(args)
        cli_values = _settings_cli_values(args)
        settings = load_managed_settings(config_path=args.config, cli_values=cli_values)
        service = TranscriptionService(settings)
        if args.command == "doctor":
            return _doctor(args, service)
        if args.command == "transcribe":
            return _transcribe(args, service)
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


def _setup(args: argparse.Namespace, manager: SetupManager) -> int:
    if args.setup_action == "status":
        status = manager.status()
        if args.json:
            print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
        else:
            print("Ready." if status.ready else "Not ready.")
            print(f"State: {status.state_file}")
            if status.backend:
                print(f"Backend: {status.backend}")
            for issue in status.issues:
                print(f"Issue: {issue}")
        return 0 if status.ready else 1
    if args.setup_action == "clean":
        if not (args.downloads or args.build_tools or args.all_cache):
            raise ConfigurationError("setup clean requires --downloads, --build-tools, or --all")
        removed = manager.clean(
            downloads=args.downloads,
            build_tools=args.build_tools,
            all_cache=args.all_cache,
        )
        for path in removed:
            print(f"Removed {path}")
        if not removed:
            print("Nothing to clean.")
        return 0

    features = tuple(
        part.strip().lower()
        for part in (args.features or "").split(",")
        if part.strip()
    )
    result = manager.setup(
        SetupRequest(
            backend=args.backend,
            features=features,
            from_source=args.from_source,
            keep_cache=args.keep_cache,
        ),
        on_stage=(lambda message: print(f"[setup] {message}", file=sys.stderr)),
    )
    if args.json:
        print(
            json.dumps(
                {
                    "ready": result.ready,
                    "actions": list(result.actions),
                    "state": result.state.to_dict(),
                    "doctor": result.doctor.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for action in result.actions:
            print(action.capitalize() + ".")
        if not result.actions:
            print("Managed assets are already current.")
        print(f"Configuration: {result.state.config_path}")
        print("Ready." if result.ready else "Setup completed, but diagnostics found errors.")
    return 0 if result.ready else 1


def _doctor(args: argparse.Namespace, service: TranscriptionService) -> int:
    report = service.doctor(
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


def _transcribe(args: argparse.Namespace, service: TranscriptionService) -> int:
    request = TranscriptionRequest(
        inputs=tuple(args.inputs),
        recursive=args.recursive,
        audio_stream=args.audio_stream,
        diarize=args.diarize,
        force=args.force,
        fail_fast=args.fail_fast,
    )
    results = service.transcribe(request)
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


def _inspect(args: argparse.Namespace, service: TranscriptionService) -> int:
    details = service.inspect(args.job)
    if args.json:
        print(json.dumps(details.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Job: {details.job_id or 'unknown'}")
        print(f"State: {details.state}")
        print(f"Source: {details.source or 'unknown'}")
        for artifact in details.artifacts:
            print(f"{artifact.name.upper()}: {artifact.path} ({artifact.size} bytes)")
        if details.error:
            print(f"Error: {details.error}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    try:
        from .api.server import ServerSettings, run_server
    except ImportError as exc:
        raise MissingDependencyError(
            'The HTTP API requires: python -m pip install "speechloom[api]"'
        ) from exc

    if not 1 <= args.port <= 65535:
        raise ConfigurationError("--port must be between 1 and 65535")
    if args.max_upload_mb < 1:
        raise ConfigurationError("--max-upload-mb must be at least 1")
    if args.queue_size < 1 or args.media_workers < 1:
        raise ConfigurationError("--queue-size and --media-workers must be at least 1")
    server_settings = ServerSettings(
        host=args.host,
        port=args.port,
        allow_remote=args.allow_remote,
        bearer_token=os.environ.get("SPEECHLOOM_API_TOKEN"),
        allowed_roots=tuple(args.allow_root),
        allowed_origins=tuple(args.allow_origin),
        max_upload_bytes=args.max_upload_mb * 1024 * 1024,
        queue_size=args.queue_size,
        media_workers=args.media_workers,
    )
    run_server(server_settings, config_path=args.config)
    return 0


def _settings_cli_values(args: argparse.Namespace) -> dict[str, Any]:
    names = (
        "ffmpeg", "ffprobe", "nemo_speech", "model", "diar_model", "translation_model",
        "source_language", "translate_to", "device",
        "output_dir", "workers", "keep_audio", "resume", "formats",
    )
    return {name: getattr(args, name, None) for name in names}
