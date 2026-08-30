"""Safe HTTP error mapping."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from speechloom.errors import (
    ConfigurationError,
    DuplicateJobError,
    JobNotFoundError,
    JobQueueFullError,
    MediaError,
)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"field": ".".join(str(item) for item in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return _response(request, 422, "validation_error", "Request validation failed", details)

    @app.exception_handler(JobNotFoundError)
    async def not_found(request: Request, exc: JobNotFoundError) -> JSONResponse:
        return _response(request, 404, "job_not_found", "Job not found")

    @app.exception_handler(DuplicateJobError)
    async def duplicate(request: Request, exc: DuplicateJobError) -> JSONResponse:
        return _response(request, 409, "job_conflict", "Job is already active")

    @app.exception_handler(JobQueueFullError)
    async def queue_full(request: Request, exc: JobQueueFullError) -> JSONResponse:
        return _response(request, 429, "queue_full", "The local job queue is full")

    @app.exception_handler(ConfigurationError)
    @app.exception_handler(MediaError)
    async def invalid_job(request: Request, exc: Exception) -> JSONResponse:
        return _response(request, 422, "invalid_job", str(exc))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return _response(request, 500, "internal_error", "An internal error occurred")


def _response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": getattr(request.state, "request_id", "unknown"),
            }
        },
    )
