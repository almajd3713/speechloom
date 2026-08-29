"""Safe external-process execution."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Mapping, Sequence

from .errors import MissingDependencyError, PipelineError


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandFailed(PipelineError):
    def __init__(self, result: CommandResult, message: str | None = None) -> None:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        super().__init__(message or f"Command failed ({result.returncode}): {detail}")
        self.result = result


def run_command(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> CommandResult:
    """Run an argv sequence without a shell and capture UTF-8 output."""

    if not argv:
        raise ValueError("argv must not be empty")
    normalized = tuple(str(part) for part in argv)
    try:
        completed = subprocess.run(
            normalized,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise MissingDependencyError(f"Executable not found: {normalized[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"Command timed out after {timeout}s: {normalized[0]}") from exc

    result = CommandResult(normalized, completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        raise CommandFailed(result)
    return result

