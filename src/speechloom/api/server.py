"""FastAPI adapter over Speechloom's application service and job manager."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import hmac
import ipaddress
import json
from pathlib import Path
import re
import shutil
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from speechloom.config import load_managed_settings
from speechloom.contracts import ArtifactDetails, JobDetails, TranscriptionRequest
from speechloom.errors import ConfigurationError
from speechloom.job_manager import JobEvent, JobManager, ManagedJob, TERMINAL_STATES
from speechloom.runtime import AppPaths
from speechloom.service import TranscriptionService

from .errors import ApiError, install_error_handlers
from .models import (
    ArtifactListResponse,
    CapabilitiesResponse,
    ErrorResponse,
    HealthResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
)


API_VERSION = "1"
ERROR_RESPONSES = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}


@dataclass(frozen=True)
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    allow_remote: bool = False
    bearer_token: str | None = None
    allowed_roots: tuple[Path, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    staging_dir: Path | None = None
    recovery_roots: tuple[Path, ...] = ()
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    queue_size: int = 16
    media_workers: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("API port must be between 1 and 65535")
        if self.max_upload_bytes < 1:
            raise ConfigurationError("API upload limit must be positive")
        if self.queue_size < 1 or self.media_workers < 1:
            raise ConfigurationError("API queue and worker limits must be positive")
        if not _is_loopback(self.host):
            if not self.allow_remote:
                raise ConfigurationError(
                    "Binding beyond loopback requires --allow-remote"
                )
            if not self.bearer_token:
                raise ConfigurationError(
                    "Binding beyond loopback requires SPEECHLOOM_API_TOKEN"
                )


def create_app(
    service: TranscriptionService,
    manager: JobManager,
    settings: ServerSettings,
) -> FastAPI:
    staging_dir = settings.staging_dir or AppPaths.from_environment().data_dir / "staging"
    policy = _PathPolicy(settings.allowed_roots)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        staging_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(
        title="Speechloom local API",
        version=API_VERSION,
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.job_manager = manager
    app.state.server_settings = settings
    install_error_handlers(app)

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        )
    app.add_middleware(_BoundaryMiddleware, settings=settings)

    @app.get(
        "/v1/health",
        response_model=HealthResponse,
        responses=ERROR_RESPONSES,
        tags=["system"],
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/v1/capabilities",
        response_model=CapabilitiesResponse,
        responses=ERROR_RESPONSES,
        tags=["system"],
    )
    async def capabilities() -> dict[str, Any]:
        return {
            "api_version": API_VERSION,
            "local_path_input": _is_loopback(settings.host) and bool(policy.roots),
            "upload_input": True,
            "progress_events": "sse",
            "cancellation": True,
            "artifact_downloads": True,
        }

    @app.post(
        "/v1/jobs",
        response_model=JobResponse,
        status_code=202,
        responses=ERROR_RESPONSES,
        tags=["jobs"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": JobCreateRequest.model_json_schema()},
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "files": {
                                    "type": "array",
                                    "items": {"type": "string", "format": "binary"},
                                },
                                "options": {
                                    "type": "string",
                                    "description": "JSON transcription options",
                                },
                            },
                            "required": ["files"],
                        }
                    },
                },
            }
        },
    )
    async def create_job(request: Request) -> JSONResponse:
        content_type = request.headers.get("content-type", "").lower()
        if content_type.startswith("application/json"):
            if not _is_loopback(settings.host):
                raise ApiError(403, "local_paths_disabled", "Local path input is loopback-only")
            try:
                payload = JobCreateRequest.model_validate(await request.json())
            except json.JSONDecodeError as exc:
                raise ApiError(422, "validation_error", "Request body must be valid JSON") from exc
            except ValidationError as exc:
                raise ApiError(
                    422,
                    "validation_error",
                    "Request validation failed",
                    details=_validation_details(exc),
                ) from exc
            transcription = _request_from_model(payload, policy)
        elif content_type.startswith("multipart/form-data"):
            transcription = await _request_from_upload(
                request, staging_dir, settings.max_upload_bytes
            )
        else:
            raise ApiError(
                415,
                "unsupported_media_type",
                "Use application/json or multipart/form-data",
            )
        submitted = manager.submit(transcription)
        response = JSONResponse(status_code=202, content=_job_payload(submitted))
        response.headers["Location"] = f"/v1/jobs/{submitted.id}"
        return response

    @app.get(
        "/v1/jobs",
        response_model=JobListResponse,
        responses=ERROR_RESPONSES,
        tags=["jobs"],
    )
    async def list_jobs(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
        state: str | None = None,
    ) -> dict[str, Any]:
        jobs = manager.list(offset=offset, limit=limit, state=state)
        return {
            "items": [_job_payload(job) for job in jobs],
            "offset": offset,
            "limit": limit,
            "total": manager.count(state=state),
        }

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobResponse,
        responses=ERROR_RESPONSES,
        tags=["jobs"],
    )
    async def get_job(job_id: str) -> dict[str, Any]:
        return _job_payload(manager.get(job_id))

    @app.delete(
        "/v1/jobs/{job_id}",
        response_model=JobResponse,
        responses=ERROR_RESPONSES,
        tags=["jobs"],
    )
    async def cancel_job(job_id: str) -> dict[str, Any]:
        return _job_payload(manager.cancel(job_id))

    @app.get(
        "/v1/jobs/{job_id}/events",
        responses={**ERROR_RESPONSES, 200: {"content": {"text/event-stream": {}}}},
        tags=["jobs"],
    )
    async def events(
        request: Request,
        job_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            after = max(0, int(last_event_id or "0"))
        except ValueError as exc:
            raise ApiError(422, "invalid_event_id", "Last-Event-ID must be an integer") from exc
        manager.get(job_id)
        return StreamingResponse(
            _event_stream(request, manager, job_id, after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(
        "/v1/jobs/{job_id}/artifacts",
        response_model=ArtifactListResponse,
        responses=ERROR_RESPONSES,
        tags=["artifacts"],
    )
    async def list_artifacts(job_id: str) -> dict[str, Any]:
        catalog = _artifact_catalog(service, manager.get(job_id))
        return {"items": [item.public for item in catalog.values()]}

    @app.get(
        "/v1/jobs/{job_id}/artifacts/{artifact_name:path}",
        responses=ERROR_RESPONSES,
        tags=["artifacts"],
    )
    async def download_artifact(job_id: str, artifact_name: str) -> FileResponse:
        catalog = _artifact_catalog(service, manager.get(job_id))
        item = catalog.get(artifact_name)
        if item is None:
            raise ApiError(404, "artifact_not_found", "Artifact not found")
        return FileResponse(item.path, filename=item.path.name)

    return app


def run_server(settings: ServerSettings, *, config_path: Path | None = None) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - guarded by the CLI
        raise ConfigurationError('Install the HTTP API with "speechloom[api]"') from exc

    app_paths = AppPaths.from_environment()
    staging_dir = settings.staging_dir or app_paths.data_dir / "staging"
    loaded = load_managed_settings(config_path=config_path)
    service = TranscriptionService(loaded)
    recovery_roots = settings.recovery_roots or (Path(loaded.output_dir),)
    configured = ServerSettings(
        **{
            **asdict(settings),
            "staging_dir": staging_dir,
            "recovery_roots": recovery_roots,
        }
    )
    manager = JobManager(
        service,
        queue_size=configured.queue_size,
        media_workers=configured.media_workers,
        inference_slots=1,
        recovery_roots=configured.recovery_roots,
    )
    app = create_app(service, manager, configured)
    try:
        uvicorn.run(app, host=configured.host, port=configured.port, workers=1)
    finally:
        manager.close(wait=True, cancel=True)


class _PathPolicy:
    def __init__(self, roots: tuple[Path, ...]) -> None:
        self.roots = tuple(root.expanduser().resolve() for root in roots)

    def input(self, raw: str) -> Path:
        if not self.roots:
            raise ApiError(403, "local_paths_disabled", "No local path roots are configured")
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ApiError(422, "invalid_input", "Input path does not exist") from exc
        self._require_allowed(path)
        return path

    def output(self, raw: str) -> Path:
        if not self.roots:
            raise ApiError(403, "local_paths_disabled", "No local path roots are configured")
        path = Path(raw).expanduser().resolve(strict=False)
        self._require_allowed(path)
        return path

    def _require_allowed(self, path: Path) -> None:
        if not any(path == root or root in path.parents for root in self.roots):
            raise ApiError(403, "path_not_allowed", "Path is outside the configured roots")


@dataclass(frozen=True)
class _Artifact:
    public: dict[str, Any]
    path: Path


def _request_from_model(model: JobCreateRequest, policy: _PathPolicy) -> TranscriptionRequest:
    return TranscriptionRequest(
        inputs=tuple(policy.input(value) for value in model.inputs),
        output_dir=policy.output(model.output_dir) if model.output_dir else None,
        formats=tuple(model.formats) if model.formats is not None else None,
        recursive=model.recursive,
        audio_stream=model.audio_stream,
        diarize=model.diarize,
        source_language=model.source_language,
        translate_to=model.translate_to,
        resume=model.resume,
        force=model.force,
        keep_audio=model.keep_audio,
        workers=model.workers,
        fail_fast=model.fail_fast,
    )


async def _request_from_upload(
    request: Request,
    staging_root: Path,
    max_upload_bytes: int,
) -> TranscriptionRequest:
    form = await request.form()
    files = [
        value
        for key, value in form.multi_items()
        if key == "files" and isinstance(value, UploadFile)
    ]
    if not files:
        raise ApiError(422, "missing_upload", "At least one files upload is required")
    raw_options = form.get("options", "{}")
    try:
        options = json.loads(str(raw_options))
    except json.JSONDecodeError as exc:
        raise ApiError(422, "invalid_options", "Upload options must be valid JSON") from exc
    disallowed = {"inputs", "output_dir", "recursive"}
    if not isinstance(options, dict) or disallowed.intersection(options):
        raise ApiError(
            422,
            "invalid_options",
            "Upload options cannot set inputs, output_dir, or recursive",
        )

    request_dir = staging_root / uuid4().hex
    request_dir.mkdir(parents=True, exist_ok=False)
    stored: list[Path] = []
    total = 0
    try:
        for index, upload in enumerate(files):
            filename = _safe_filename(upload.filename, index)
            target = request_dir / filename
            with target.open("xb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_upload_bytes:
                        raise ApiError(
                            413,
                            "upload_too_large",
                            "Uploaded files exceed the configured limit",
                        )
                    handle.write(chunk)
            await upload.close()
            stored.append(target)
        try:
            model = JobCreateRequest.model_validate(
                {"inputs": [str(path) for path in stored], **options}
            )
        except ValidationError as exc:
            raise ApiError(
                422,
                "validation_error",
                "Upload options failed validation",
                details=_validation_details(exc),
            ) from exc
        return TranscriptionRequest(
            inputs=tuple(stored),
            output_dir=None,
            formats=tuple(model.formats) if model.formats is not None else None,
            recursive=False,
            audio_stream=model.audio_stream,
            diarize=model.diarize,
            source_language=model.source_language,
            translate_to=model.translate_to,
            resume=model.resume,
            force=model.force,
            keep_audio=model.keep_audio,
            workers=model.workers,
            fail_fast=model.fail_fast,
        )
    except Exception:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise


def _safe_filename(value: str | None, index: int) -> str:
    basename = Path(value or f"upload-{index + 1}").name
    normalized = re.sub(r"[^\w.-]+", "-", basename, flags=re.UNICODE).strip(".-")
    return f"{index + 1:03d}-{normalized or 'upload'}"


def _validation_details(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(item) for item in error["loc"]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]


def _job_payload(job: ManagedJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "state": job.state,
        "inputs": [path.name for path in job.inputs],
        "submitted_at": job.submitted_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "current_stage": job.current_stage,
        "results": [
            {
                "source": Path(result.source).name,
                "state": result.state,
                "skipped": result.skipped,
                "error": result.error,
            }
            for result in job.results
        ],
        "error": job.error,
        "recovered": job.recovered,
    }


async def _event_stream(
    request: Request,
    manager: JobManager,
    job_id: str,
    after: int,
) -> AsyncIterator[str]:
    cursor = after
    while True:
        events = await asyncio.to_thread(
            manager.wait_for_events,
            job_id,
            after=cursor,
            timeout=15.0,
        )
        for item in events:
            cursor = item.id
            yield _format_sse_event(item)
        if (await asyncio.to_thread(manager.get, job_id)).state in TERMINAL_STATES:
            return
        if await request.is_disconnected():
            return
        if not events:
            yield ": keep-alive\n\n"


def _event_payload(item: JobEvent) -> dict[str, Any]:
    event = item.event
    return {
        "job_id": event.job_id,
        "source": event.source.name if event.source is not None else None,
        "stage": event.stage,
        "status": event.status,
        "message": event.message,
        "completed": event.completed,
        "total": event.total,
        "timestamp": event.timestamp,
    }


def _format_sse_event(item: JobEvent) -> str:
    payload = _event_payload(item)
    return (
        f"id: {item.id}\n"
        f"event: {item.event.stage}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )


def _artifact_catalog(
    service: TranscriptionService,
    job: ManagedJob,
) -> dict[str, _Artifact]:
    manifests: list[tuple[Path, JobDetails]] = []
    for result in job.results:
        job_dir = Path(result.job_dir).expanduser().resolve()
        try:
            details = service.inspect(job_dir)
        except Exception:
            continue
        manifests.append((job_dir, details))
    catalog: dict[str, _Artifact] = {}
    multiple = len(manifests) > 1
    for index, (job_dir, details) in enumerate(manifests, start=1):
        for artifact in details.artifacts:
            key = f"{index}.{artifact.name}" if multiple else artifact.name
            path = _manifest_artifact_path(job_dir, artifact)
            if path is None:
                continue
            catalog[key] = _Artifact(
                public={
                    "name": key,
                    "filename": path.name,
                    "size": artifact.size,
                    "sha256": artifact.sha256,
                },
                path=path,
            )
    return catalog


def _manifest_artifact_path(job_dir: Path, artifact: ArtifactDetails) -> Path | None:
    path = (job_dir / artifact.path).resolve()
    if job_dir != path and job_dir not in path.parents:
        return None
    return path if path.is_file() else None


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": None,
                "request_id": request.state.request_id,
            }
        },
        headers={"X-Request-ID": request.state.request_id},
    )


class _BoundaryMiddleware:
    def __init__(self, app, *, settings: ServerSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        request = Request(scope, receive=receive)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                response = _error_response(
                    request, 400, "invalid_content_length", "Invalid Content-Length"
                )
                await response(scope, receive, send)
                return
            if length > self.settings.max_upload_bytes:
                response = _error_response(
                    request, 413, "request_too_large", "Request body is too large"
                )
                await response(scope, receive, send)
                return
        if self.settings.bearer_token and scope.get("path", "").startswith("/v1/"):
            authorization = request.headers.get("authorization", "")
            expected = f"Bearer {self.settings.bearer_token}"
            if not hmac.compare_digest(authorization, expected):
                response = _error_response(
                    request, 401, "unauthorized", "Bearer token required"
                )
                await response(scope, receive, send)
                return

        async def send_with_request_id(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", ()))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_request_id)


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
