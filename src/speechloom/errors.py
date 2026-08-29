"""Domain errors and stable CLI exit codes."""

from __future__ import annotations


class PipelineError(Exception):
    """Base error that can be presented directly to a CLI user."""

    exit_code = 1


class ConfigurationError(PipelineError):
    exit_code = 2


class SetupError(PipelineError):
    """A managed runtime or model could not be installed or verified."""


class MissingDependencyError(PipelineError):
    exit_code = 3


class MediaError(PipelineError):
    exit_code = 2


class ModelError(PipelineError):
    exit_code = 3


class UnsupportedFeatureError(PipelineError):
    exit_code = 4


class InferenceError(PipelineError):
    def __init__(self, message: str, *, native_exit_code: int | None = None) -> None:
        super().__init__(message)
        self.native_exit_code = native_exit_code


class ArtifactConflictError(PipelineError):
    exit_code = 2
