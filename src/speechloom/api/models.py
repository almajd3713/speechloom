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
    workers: int | None = Field(default=None, ge=1, le=8)
    fail_fast: bool = False


class SegmentEditModel(BaseModel):
    index: int = Field(ge=0)
    text: str | None = Field(min_length=1, max_length=1000)


class EditRequest(BaseModel):
    result: int = Field(default=1, ge=1)
    segments: list[SegmentEditModel] = Field(min_length=1, max_length=10000)


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
    has_upload: bool


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
    features: dict[str, bool]
    formats: list[str]
    asr_languages: list[str]
    translation_languages: list[str]
    max_upload_bytes: int


class SetupStartRequest(BaseModel):
    backend: str = "auto"
    features: list[str] = Field(default_factory=list)
    from_source: bool = False
    keep_cache: bool = False


class SetupRunRequestResponse(BaseModel):
    backend: str
    features: list[str]
    from_source: bool
    keep_cache: bool


class SetupRunResponse(BaseModel):
    id: str
    state: str
    request: SetupRunRequestResponse
    started_at: str
    finished_at: str | None
    current_stage: str
    message: str
    error: str | None
    ready: bool | None
    actions: list[str]


class SetupResponse(BaseModel):
    status: dict[str, Any] | None
    task: SetupRunResponse | None


class DoctorCheckResponse(BaseModel):
    name: str
    status: str
    message: str
    details: Any = None


class DoctorResponse(BaseModel):
    ready: bool
    checks: list[DoctorCheckResponse]


class StorageJobResponse(BaseModel):
    id: str
    state: str
    inputs: list[str]
    upload_bytes: int
    output_bytes: int
    total_bytes: int
    share: float


class StorageResponse(BaseModel):
    total_bytes: int
    upload_bytes: int
    output_bytes: int
    free_bytes: int
    jobs: list[StorageJobResponse]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
