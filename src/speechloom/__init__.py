"""Speechloom local transcription and translation pipeline."""

__version__ = "0.1.0"

from .config import Settings
from .contracts import (
    ArtifactDetails,
    CancellationToken,
    JobDetails,
    StageEvent,
    TranscriptionRequest,
)
from .doctor import DoctorReport
from .jobs import JobResult
from .service import TranscriptionService
from .setup import SetupManager, SetupRequest, SetupResult, SetupStatus

__all__ = [
    "ArtifactDetails",
    "CancellationToken",
    "DoctorReport",
    "JobDetails",
    "JobResult",
    "Settings",
    "SetupManager",
    "SetupRequest",
    "SetupResult",
    "SetupStatus",
    "StageEvent",
    "TranscriptionRequest",
    "TranscriptionService",
    "__version__",
]
