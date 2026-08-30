"""Idempotent installation workflow for managed Speechloom assets."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib.metadata import distribution
import json
import os
from pathlib import Path
import platform
import re
import shutil
from typing import Callable, Sequence

from . import __version__
from .artifacts import atomic_write_text, sha256_file
from .config import Settings
from .contracts import CancellationToken, StageEvent
from .doctor import DoctorReport, run_doctor
from .errors import CancellationError, ConfigurationError, PipelineError, SetupError
from .process import CommandResult, run_command
from .registry import ModelSpec, Registry, RuntimeArchiveSpec
from .runtime import (
    AppPaths,
    InstallState,
    InstalledArtifact,
    load_install_state,
    save_install_state,
)
from .runtime_archive import (
    RuntimeArchiveMetadata,
    extract_runtime_archive,
    read_runtime_metadata,
)


Runner = Callable[..., CommandResult]
StageSink = Callable[[str], None]
EventSink = Callable[[StageEvent], None]
Doctor = Callable[..., DoctorReport]


@dataclass(frozen=True)
class SetupRequest:
    backend: str = "auto"
    features: tuple[str, ...] = ()
    from_source: bool = False
    keep_cache: bool = False

    def __post_init__(self) -> None:
        allowed_backends = {"auto", "cpu", "cuda", "vulkan"}
        if self.backend not in allowed_backends:
            raise ConfigurationError(f"Unsupported setup backend: {self.backend}")
        normalized = tuple(sorted(set(self.features)))
        unsupported = set(normalized) - {"translation", "diarization"}
        if unsupported:
            raise ConfigurationError(
                f"Unsupported setup features: {', '.join(sorted(unsupported))}"
            )
        object.__setattr__(self, "features", normalized)


@dataclass(frozen=True)
class SetupResult:
    state: InstallState
    actions: tuple[str, ...]
    doctor: DoctorReport

    @property
    def ready(self) -> bool:
        return self.doctor.ready


@dataclass(frozen=True)
class SetupStatus:
    installed: bool
    ready: bool
    state_file: str
    config_file: str
    backend: str | None = None
    features: tuple[str, ...] = ()
    runtime: str | None = None
    models: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "ready": self.ready,
            "state_file": self.state_file,
            "config_file": self.config_file,
            "backend": self.backend,
            "features": list(self.features),
            "runtime": self.runtime,
            "models": list(self.models),
            "issues": list(self.issues),
        }


class SetupManager:
    """Owns setup state while delegating native source builds to pinned scripts."""

    def __init__(
        self,
        *,
        paths: AppPaths | None = None,
        registry: Registry | None = None,
        runner: Runner = run_command,
        doctor: Doctor = run_doctor,
        repository_root: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.paths = paths or AppPaths.from_environment()
        self.registry = registry or Registry.load()
        self.runner = runner
        self._doctor = doctor
        self.repository_root = repository_root or _find_repository_root()
        self.config_path = config_path or self.paths.config_file
        self._active_cancellation: CancellationToken | None = None

    def setup(
        self,
        request: SetupRequest,
        *,
        on_stage: StageSink | None = None,
        on_event: EventSink | None = None,
        cancellation: CancellationToken | None = None,
    ) -> SetupResult:
        self._active_cancellation = cancellation

        def relay(message: str) -> None:
            self._check_cancelled()
            _emit(on_stage, message)
            if on_event is not None:
                on_event(
                    StageEvent(
                        job_id=None,
                        source=None,
                        stage=_setup_event_stage(message),
                        status="started",
                        message=message,
                    )
                )
            self._check_cancelled()

        try:
            if on_event is not None:
                on_event(
                    StageEvent(
                        job_id=None,
                        source=None,
                        stage="validating",
                        status="started",
                        message="Validating setup request and installed assets",
                    )
                )
            self._check_cancelled()
            result = self._setup(request, on_stage=relay)
            self._check_cancelled()
            if on_event is not None:
                on_event(
                    StageEvent(
                        job_id=None,
                        source=None,
                        stage="completed",
                        status="completed",
                        message="Setup completed",
                    )
                )
            return result
        except CancellationError as exc:
            if on_event is not None:
                on_event(
                    StageEvent(
                        job_id=None,
                        source=None,
                        stage="cancelled",
                        status="cancelled",
                        message=str(exc),
                    )
                )
            raise
        except Exception as exc:
            if on_event is not None:
                on_event(
                    StageEvent(
                        job_id=None,
                        source=None,
                        stage="failed",
                        status="failed",
                        message=str(exc),
                    )
                )
            raise
        finally:
            self._active_cancellation = None

    def _setup(
        self,
        request: SetupRequest,
        *,
        on_stage: StageSink | None = None,
    ) -> SetupResult:
        self.paths.create()
        old_state = load_install_state(self.paths.state_file)
        backend = self._resolve_backend(request.backend, old_state)
        actions: list[str] = []

        runtime = self._reuse_runtime(
            old_state,
            backend,
            request.features,
            from_source=request.from_source,
        )
        if runtime is None and not request.from_source:
            runtime = self._legacy_runtime(backend, request.features)
            if runtime is not None:
                actions.append("imported legacy runtime")
        if runtime is None and not request.from_source:
            archive = self._runtime_archive(backend, request.features)
            if archive is not None:
                _emit(on_stage, f"installing {backend} runtime archive")
                runtime = self._install_runtime_archive(archive)
                actions.append(f"installed {backend} prebuilt runtime")
        if runtime is None:
            _emit(on_stage, f"building {backend} runtime")
            runtime = self._build_runtime(backend, request.features)
            actions.append(f"built {backend} runtime")

        installed_models: list[InstalledArtifact] = []
        asr_spec = self.registry.model("asr")
        asr = self._reuse_model(old_state, asr_spec) or self._legacy_model(asr_spec)
        if asr is None:
            _emit(on_stage, "installing ASR model")
            asr = self._install_model(asr_spec)
            actions.append("installed ASR model")
        elif asr.source == "legacy" and not self._state_has_path(old_state, asr.path):
            actions.append("imported legacy ASR model")
        installed_models.append(asr)

        if "diarization" in request.features:
            diar_spec = self.registry.model("diarization")
            diar = self._reuse_model(old_state, diar_spec) or self._legacy_model(diar_spec)
            if diar is None:
                _emit(on_stage, "installing diarization model")
                diar = self._install_model(diar_spec)
                actions.append("installed diarization model")
            elif diar.source == "legacy" and not self._state_has_path(old_state, diar.path):
                actions.append("imported legacy diarization model")
            installed_models.append(diar)

        if "translation" in request.features:
            translation_spec = self.registry.model("translation")
            translation = (
                self._reuse_model(old_state, translation_spec)
                or self._legacy_model(translation_spec)
            )
            if translation is None:
                _emit(on_stage, "installing translation model")
                translation = self._install_model(translation_spec)
                actions.append("installed translation model")
            elif translation.source == "legacy" and not self._state_has_path(old_state, translation.path):
                actions.append("imported legacy translation model")
            installed_models.append(translation)

        if not self.config_path.exists():
            _emit(on_stage, "writing default configuration")
            self._write_config(runtime, asr, backend)
            actions.append("created default configuration")

        state = InstallState(
            backend=backend,
            features=request.features,
            config_path=str(self.config_path),
            runtime=runtime,
            models=tuple(installed_models),
            platform=self._platform_details(),
            completed_stages=("runtime", "asr", "config") + request.features,
            retained_cache=self._retained_cache(),
            created_at=old_state.created_at if old_state else _timestamp(),
            updated_at=_timestamp(),
            installer_version=__version__,
        )
        self._check_cancelled()
        save_install_state(self.paths.state_file, state)

        _emit(on_stage, "running diagnostics")
        report = self._doctor(self._settings(state), runner=self._run_command)
        self._check_cancelled()
        if report.ready and not request.keep_cache:
            self._clean_generated_cache()
        state = replace(
            state,
            last_doctor=report.to_dict(),
            retained_cache=self._retained_cache(),
            updated_at=_timestamp(),
        )
        save_install_state(self.paths.state_file, state)
        return SetupResult(state, tuple(actions), report)

    def _run_command(self, argv, **kwargs) -> CommandResult:
        if self._active_cancellation is not None:
            kwargs.setdefault("cancellation", self._active_cancellation)
        return self.runner(argv, **kwargs)

    def _check_cancelled(self) -> None:
        if self._active_cancellation is not None:
            self._active_cancellation.raise_if_cancelled()

    def status(self) -> SetupStatus:
        state = load_install_state(self.paths.state_file)
        if state is None:
            return SetupStatus(
                False,
                False,
                str(self.paths.state_file),
                str(self.config_path),
                issues=("setup has not been completed",),
            )
        issues: list[str] = []
        if state.runtime is None or not self._valid_artifact(state.runtime):
            issues.append("runtime is missing or its checksum changed")
        for model in state.models:
            if not self._valid_artifact(model):
                issues.append(f"{model.kind} model is missing or its checksum changed")
        if state.model("asr") is None:
            issues.append("ASR model is not installed")
        if not Path(state.config_path).is_file():
            issues.append("configuration file is missing")
        if state.last_doctor is None:
            issues.append("diagnostics have not completed")
        elif not bool(state.last_doctor.get("ready")):
            issues.append("the last diagnostic run was not ready")
        return SetupStatus(
            True,
            not issues,
            str(self.paths.state_file),
            state.config_path,
            backend=state.backend,
            features=state.features,
            runtime=state.runtime.path if state.runtime else None,
            models=tuple(model.path for model in state.models),
            issues=tuple(issues),
        )

    def clean(
        self,
        *,
        downloads: bool = False,
        build_tools: bool = False,
        all_cache: bool = False,
    ) -> tuple[Path, ...]:
        targets: list[Path] = []
        if downloads or all_cache:
            targets.append(self.paths.downloads_dir)
        if build_tools or all_cache:
            targets.append(self.paths.build_tools_dir)
        if all_cache:
            targets.extend(
                [self.paths.cache_dir / "converter", self.paths.cache_dir / "huggingface"]
            )
        removed: list[Path] = []
        for target in dict.fromkeys(targets):
            if target.exists():
                shutil.rmtree(target)
                removed.append(target)
        return tuple(removed)

    def _resolve_backend(self, requested: str, state: InstallState | None) -> str:
        if requested in {"cpu", "vulkan"}:
            return requested
        gpu_usable = self._gpu_usable()
        if requested == "cuda":
            if gpu_usable:
                return requested
            raise SetupError(
                "CUDA requested but nvidia-smi could not communicate with the GPU"
            )
        if (
            state
            and state.backend in self.registry.runtime.backends
            and (state.backend != "cuda" or gpu_usable)
        ):
            return state.backend
        if self._legacy_executable("cuda") and gpu_usable:
            return "cuda"
        if gpu_usable and _command_available("nvcc"):
            return "cuda"
        return "cpu"

    def _gpu_usable(self) -> bool:
        if not _command_available("nvidia-smi"):
            return False
        try:
            return self._run_command(["nvidia-smi"], check=False).returncode == 0
        except CancellationError:
            raise
        except PipelineError:
            return False

    def _reuse_runtime(
        self,
        state: InstallState | None,
        backend: str,
        features: Sequence[str],
        *,
        from_source: bool,
    ) -> InstalledArtifact | None:
        if not state or state.backend != backend or state.runtime is None:
            return None
        if not set(features).issubset(state.features):
            return None
        if from_source and state.runtime.source != "source-build":
            return None
        return state.runtime if self._valid_artifact(state.runtime) else None

    def _legacy_runtime(
        self,
        backend: str,
        features: Sequence[str],
    ) -> InstalledArtifact | None:
        executable = self._legacy_executable(backend)
        if executable is None:
            return None
        if "translation" in features and not self._runtime_has_translation(executable):
            return None
        return self._record(
            self.registry.runtime.id,
            "runtime",
            executable,
            revision=self.registry.runtime.revision,
            source="legacy",
            license=self.registry.runtime.license,
        )

    def _legacy_executable(self, backend: str) -> Path | None:
        if self.repository_root is None:
            return None
        base = self.repository_root / ".runtime"
        names = [f"nemo-speech-{backend}"]
        if backend == "cpu":
            names.append("nemo-speech")
        for name in names:
            candidate = base / name / self.registry.runtime.executable
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
        return None

    def _build_runtime(self, backend: str, features: Sequence[str]) -> InstalledArtifact:
        self._ensure_build_tools()
        prefix = self.paths.runtime_dir / f"nemo-speech-{backend}"
        argv = [
            "bash",
            str(self._script_path(self.registry.runtime.build_script)),
            "--backend",
            backend,
            "--prefix",
            str(prefix),
            "--tools-prefix",
            str(self.paths.build_tools_dir),
        ]
        if "translation" in features:
            argv.append("--with-nmt")
        self._run_command(argv)
        executable = prefix / self.registry.runtime.executable
        if not executable.is_file():
            raise SetupError(f"Runtime build completed without creating {executable}")
        return self._record(
            self.registry.runtime.id,
            "runtime",
            executable,
            revision=self.registry.runtime.revision,
            source="source-build",
            license=self.registry.runtime.license,
        )

    def _runtime_archive(
        self, backend: str, features: Sequence[str]
    ) -> RuntimeArchiveSpec | None:
        machine = platform.machine().lower()
        if machine == "amd64":
            machine = "x86_64"
        return self.registry.runtime_archive(
            backend,
            platform.system().lower(),
            machine,
            tuple(features),
        )

    def _install_runtime_archive(self, spec: RuntimeArchiveSpec) -> InstalledArtifact:
        free = shutil.disk_usage(self.paths.runtime_dir).free
        if free < spec.minimum_free_bytes:
            raise SetupError(
                "Not enough free space for runtime installation: "
                f"need {spec.minimum_free_bytes} bytes, have {free}"
            )
        self._run_command(
            [
                "bash",
                str(self._script_path("download_artifact.sh")),
                "--destination",
                str(self.paths.downloads_dir),
                "--download-cache",
                str(self.paths.downloads_dir),
                "--url",
                spec.url,
                "--filename",
                spec.filename,
                "--sha256",
                spec.sha256,
            ]
        )
        archive_path = self.paths.downloads_dir / spec.filename
        if not archive_path.is_file() or sha256_file(archive_path) != spec.sha256:
            raise SetupError("Downloaded runtime archive checksum does not match the registry")
        expected = RuntimeArchiveMetadata(
            backend=spec.backend,
            system=spec.system,
            architecture=spec.architecture,
            revision=self.registry.runtime.revision,
            features=spec.features,
            executable=self.registry.runtime.executable,
        )
        destination = self.paths.runtime_dir / (
            f"nemo-speech-{spec.backend}-{spec.sha256[:12]}"
        )
        executable = destination / self.registry.runtime.executable
        if destination.exists():
            metadata_path = destination / "runtime.json"
            try:
                installed_metadata = RuntimeArchiveMetadata.from_dict(
                    json.loads(metadata_path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, SetupError) as exc:
                raise SetupError(
                    f"Existing managed runtime is invalid and was not overwritten: {destination}"
                ) from exc
            if installed_metadata != expected or not executable.is_file():
                raise SetupError(
                    f"Existing managed runtime does not match its profile: {destination}"
                )
        else:
            if read_runtime_metadata(archive_path) != expected:
                raise SetupError("Runtime archive metadata does not match the registry")
            executable = extract_runtime_archive(archive_path, destination, expected)
        return self._record(
            self.registry.runtime.id,
            "runtime",
            executable,
            revision=self.registry.runtime.revision,
            source="prebuilt",
            license=self.registry.runtime.license,
            download_url=spec.url,
            download_sha256=spec.sha256,
        )

    def _ensure_build_tools(self) -> None:
        try:
            result = self._run_command(["cmake", "--version"], check=False)
            match = re.search(r"cmake version (\d+)\.(\d+)", result.stdout)
            valid = bool(
                result.returncode == 0
                and match
                and int(match.group(1)) == 3
                and int(match.group(2)) >= 26
            )
        except CancellationError:
            raise
        except PipelineError:
            valid = False
        if valid:
            return
        self._run_command(
            [
                "bash",
                str(self._script_path("bootstrap_build_tools.sh")),
                "--prefix",
                str(self.paths.build_tools_dir),
            ]
        )

    def _reuse_model(
        self,
        state: InstallState | None,
        spec: ModelSpec,
    ) -> InstalledArtifact | None:
        if state is None:
            return None
        model = state.model(spec.kind)
        if model is None or model.id != spec.id:
            return None
        if spec.sha256 and model.sha256 != spec.sha256:
            return None
        return model if self._valid_artifact(model) else None

    def _legacy_model(self, spec: ModelSpec) -> InstalledArtifact | None:
        if self.repository_root is None:
            return None
        candidate = self.repository_root / ".runtime" / "models" / spec.filename
        if not candidate.is_file():
            return None
        if spec.sha256 and sha256_file(candidate) != spec.sha256:
            return None
        return self._record(
            spec.id,
            spec.kind,
            candidate.resolve(),
            revision=spec.revision,
            source="legacy",
            license=spec.license,
        )

    def _install_model(self, spec: ModelSpec) -> InstalledArtifact:
        self._require_disk_space(spec.minimum_free_bytes)
        argv = [
            "bash",
            str(self._script_path(spec.install_script)),
            "--destination",
            str(self.paths.models_dir),
        ]
        if spec.url is not None and spec.sha256 is not None:
            argv.extend(
                [
                    "--download-cache",
                    str(self.paths.downloads_dir),
                    "--url",
                    spec.url,
                    "--filename",
                    spec.filename,
                    "--sha256",
                    spec.sha256,
                ]
            )
        elif spec.kind == "translation":
            argv.extend(
                [
                    "--converter-env",
                    str(self.paths.cache_dir / "converter"),
                    "--cache-dir",
                    str(self.paths.cache_dir / "huggingface"),
                ]
            )
        self._run_command(argv)
        path = self.paths.models_dir / spec.filename
        if not path.is_file():
            raise SetupError(f"Model installation completed without creating {path}")
        artifact = self._record(
            spec.id,
            spec.kind,
            path,
            revision=spec.revision,
            license=spec.license,
        )
        if spec.sha256 and artifact.sha256 != spec.sha256:
            path.unlink(missing_ok=True)
            raise SetupError(f"Installed {spec.kind} model checksum does not match the registry")
        return artifact

    def _require_disk_space(self, minimum: int) -> None:
        free = shutil.disk_usage(self.paths.models_dir).free
        if free < minimum:
            raise SetupError(
                f"Not enough free space for installation: need {minimum} bytes, have {free}"
            )

    def _write_config(
        self,
        runtime: InstalledArtifact,
        asr: InstalledArtifact,
        backend: str,
    ) -> None:
        content = (
            "[speechloom]\n"
            f"nemo_speech = {runtime.path}\n"
            f"model = {asr.path}\n"
            f"device = {backend}\n"
            "output_dir = transcripts\n"
            "formats = json,txt,srt,vtt\n"
        )
        atomic_write_text(self.config_path, content)

    def _settings(self, state: InstallState) -> Settings:
        assert state.runtime is not None
        asr = state.model("asr")
        assert asr is not None
        translation = state.model("translation")
        diarization = state.model("diarization")
        return Settings(
            nemo_speech=state.runtime.path,
            model=asr.path,
            diar_model=diarization.path if diarization else None,
            translation_model=translation.path if translation else None,
            device=state.backend,
        )

    def _runtime_has_translation(self, executable: Path) -> bool:
        result = self._run_command([str(executable), "doctor", "--json"], check=False)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        return bool(
            result.returncode == 0
            and isinstance(payload.get("features"), dict)
            and payload["features"].get("translation") is True
        )

    def _record(
        self,
        artifact_id: str,
        kind: str,
        path: Path,
        *,
        revision: str | None = None,
        source: str = "managed",
        license: str | None = None,
        download_url: str | None = None,
        download_sha256: str | None = None,
    ) -> InstalledArtifact:
        return InstalledArtifact(
            id=artifact_id,
            kind=kind,
            path=str(path),
            revision=revision,
            sha256=sha256_file(path),
            size=path.stat().st_size,
            source=source,
            license=license,
            download_url=download_url,
            download_sha256=download_sha256,
        )

    def _valid_artifact(self, artifact: InstalledArtifact) -> bool:
        path = Path(artifact.path)
        return bool(
            path.is_file()
            and artifact.size == path.stat().st_size
            and artifact.sha256
            and artifact.sha256 == sha256_file(path)
        )

    def _state_has_path(self, state: InstallState | None, path: str) -> bool:
        if state is None:
            return False
        artifacts = ([state.runtime] if state.runtime else []) + list(state.models)
        return any(artifact.path == path for artifact in artifacts)

    def _script_path(self, name: str) -> Path:
        if self.repository_root is not None:
            candidate = self.repository_root / "scripts" / name
            if candidate.is_file():
                return candidate
        package = distribution("speechloom")
        suffix = f"share/speechloom/scripts/{name}"
        entry = next(
            (item for item in (package.files or ()) if str(item).endswith(suffix)),
            None,
        )
        if entry is not None:
            candidate = Path(package.locate_file(entry))
            if candidate.is_file():
                return candidate
        raise SetupError(f"Speechloom installation script is missing: {name}")

    def _clean_generated_cache(self) -> None:
        for path in (self.paths.cache_dir / "converter", self.paths.cache_dir / "huggingface"):
            if path.exists():
                shutil.rmtree(path)
        if self.paths.downloads_dir.exists():
            for path in self.paths.downloads_dir.iterdir():
                if path.is_file() and not path.name.endswith(".part"):
                    path.unlink()

    def _retained_cache(self) -> tuple[str, ...]:
        return tuple(
            str(path)
            for path in (
                self.paths.downloads_dir,
                self.paths.cache_dir / "converter",
                self.paths.cache_dir / "huggingface",
            )
            if path.exists() and any(path.iterdir())
        )

    def _platform_details(self) -> dict[str, object]:
        return {
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
            "gpu_visible": self._gpu_usable(),
        }


def _find_repository_root() -> Path | None:
    candidates = [Path.cwd(), Path(__file__).resolve().parents[2]]
    for start in candidates:
        for path in (start, *start.parents):
            if (path / "scripts/bootstrap_runtime.sh").is_file():
                return path
    return None


def _command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _emit(sink: StageSink | None, message: str) -> None:
    if sink is not None:
        sink(message)


def _setup_event_stage(message: str) -> str:
    if "runtime" in message:
        return "installing_runtime"
    if "model" in message:
        return "installing_models"
    if "configuration" in message:
        return "configuring"
    if "diagnostics" in message:
        return "validating"
    return "validating"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
