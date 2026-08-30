"""Versioned HTTP request and response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JobCreateRequest(BaseModel):
    inputs: list[str] = Field(min_length=1)
    output_dir: str | None = None
    formats: list[str] | None = None
    recursive: bool = False
    audio_stream: int | None = None
    diarize: bool = False
    source_language: str | None = None
    translate_to: str | None = None
    resume: bool | None = None
    force: bool = False
    keep_audio: bool | None = None
    workers: int | None = Field(default=None, ge=1)
    fail_fast: bool = False


class ResultResponse(BaseModel):
    source: str
    state: str
    skipped: bool
    error: str | None = None


class JobResponse(BaseModel):
    id: str
    state: str
    inputs: list[str]
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    current_stage: str
    results: list[ResultResponse]
    error: str | None = None
    recovered: bool


class JobListResponse(BaseModel):
    items: list[JobResponse]
    offset: int
    limit: int
    total: int


class ArtifactResponse(BaseModel):
    name: str
    filename: str
    size: int | None = None
    sha256: str | None = None


class ArtifactListResponse(BaseModel):
    items: list[ArtifactResponse]


class HealthResponse(BaseModel):
    status: str


class CapabilitiesResponse(BaseModel):
    api_version: str
    local_path_input: bool
    upload_input: bool
    progress_events: str
    cancellation: bool
    artifact_downloads: bool


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
