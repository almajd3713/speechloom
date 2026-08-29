"""Speechloom local transcription and translation pipeline."""

__version__ = "0.1.0"

from .config import Settings
from .contracts import (
    ArtifactDetails,
    CancellationController,
    CancellationToken,
    JobDetails,
    StageEvent,
    TranscriptionRequest,
)
from .doctor import DoctorReport
from .errors import (
    CancellationError,
    DuplicateJobError,
    JobManagerError,
    JobNotFoundError,
    JobQueueFullError,
)
from .jobs import JobResult
from .job_manager import JobEvent, JobManager, ManagedJob
from .service import TranscriptionService
from .setup import SetupManager, SetupRequest, SetupResult, SetupStatus

__all__ = [
    "ArtifactDetails",
    "CancellationController",
    "CancellationError",
    "CancellationToken",
    "DoctorReport",
    "DuplicateJobError",
    "JobDetails",
    "JobEvent",
    "JobManager",
    "JobManagerError",
    "JobNotFoundError",
    "JobQueueFullError",
    "ManagedJob",
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
