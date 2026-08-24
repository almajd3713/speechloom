"""Runtime diagnostics and backend preflight."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable

from .config import Settings
from .process import CommandResult, run_command


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str
    details: Any = None


@dataclass(frozen=True)
class DoctorReport:
    ready: bool
    checks: tuple[Check, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, "checks": [asdict(check) for check in self.checks]}


def run_doctor(
    settings: Settings,
    *,
    output_dir: Path | None = None,
    runner: Callable[..., CommandResult] = run_command,
) -> DoctorReport:
    checks: list[Check] = []
    for name, executable in (
        ("ffmpeg", settings.ffmpeg),
        ("ffprobe", settings.ffprobe),
        ("nemo-speech", settings.nemo_speech),
    ):
        resolved = _resolve_executable(executable)
        if resolved is None:
            checks.append(Check(name, "error", f"Executable not found: {executable}"))
            continue
        result = runner([executable, "--version" if name == "nemo-speech" else "-version"], check=False)
        version = (result.stdout.strip() or result.stderr.strip()).splitlines()
        status = "ok" if result.returncode == 0 else "error"
        checks.append(Check(name, status, version[0] if version else str(resolved)))

    if settings.model:
        model = Path(settings.model).expanduser()
        checks.append(
            Check("asr-model", "ok", str(model.resolve()), {"size": model.stat().st_size})
            if model.is_file()
            else Check("asr-model", "error", f"Model not found: {model}")
        )
    else:
        checks.append(Check("asr-model", "error", "No ASR model is configured"))

    if settings.diar_model:
        diar_model = Path(settings.diar_model).expanduser()
        checks.append(
            Check("diar-model", "ok", str(diar_model.resolve()), {"speaker_limit": 4})
            if diar_model.is_file()
            else Check("diar-model", "error", f"Diarization model not found: {diar_model}")
        )

    if _resolve_executable(settings.nemo_speech):
        result = runner([settings.nemo_speech, "doctor", "--json"], check=False)
        details = _maybe_json(result.stdout)
        checks.append(
            Check(
                "nemo-doctor",
                "ok" if result.returncode == 0 else "error",
                "NeMo runtime diagnostics passed" if result.returncode == 0 else _diagnostic(result),
                details,
            )
        )

    if settings.device.startswith("cuda"):
        if _resolve_executable("nvidia-smi") is None:
            checks.append(Check("cuda", "error", "CUDA requested but nvidia-smi is unavailable"))
        else:
            result = runner(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                check=False,
            )
            checks.append(
                Check(
                    "cuda",
                    "ok" if result.returncode == 0 else "error",
                    result.stdout.strip() if result.returncode == 0 else _diagnostic(result),
                )
            )

    target = (output_dir or Path(settings.output_dir)).expanduser()
    existing = _nearest_existing_parent(target)
    if existing is None:
        checks.append(Check("output", "error", f"No existing parent for output directory: {target}"))
    else:
        usage = shutil.disk_usage(existing)
        writable = os.access(existing, os.W_OK)
        checks.append(
            Check(
                "output",
                "ok" if writable else "error",
                f"Output parent {'is' if writable else 'is not'} writable: {existing}",
                {"free_bytes": usage.free},
            )
        )

    ready = not any(check.status == "error" for check in checks)
    return DoctorReport(ready, tuple(checks))


def _resolve_executable(executable: str) -> Path | None:
    if os.sep in executable:
        path = Path(executable).expanduser()
        return path.resolve() if path.is_file() and os.access(path, os.X_OK) else None
    resolved = shutil.which(executable)
    return Path(resolved) if resolved else None


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path.resolve(strict=False)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def _diagnostic(result: CommandResult) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"


def _maybe_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None
