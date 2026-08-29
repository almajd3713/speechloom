"""Managed application paths and durable installation state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .artifacts import atomic_write_json
from .errors import ConfigurationError


INSTALL_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    config_file: Path
    data_dir: Path
    runtime_dir: Path
    models_dir: Path
    state_file: Path
    cache_dir: Path
    downloads_dir: Path
    build_tools_dir: Path

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        home: Path | None = None,
    ) -> "AppPaths":
        environ = env if env is not None else os.environ
        home_dir = home if home is not None else Path.home()
        config_root = Path(environ.get("XDG_CONFIG_HOME", home_dir / ".config"))
        data_root = Path(environ.get("XDG_DATA_HOME", home_dir / ".local/share"))
        cache_root = Path(environ.get("XDG_CACHE_HOME", home_dir / ".cache"))
        config_dir = config_root / "speechloom"
        data_dir = data_root / "speechloom"
        cache_dir = cache_root / "speechloom"
        return cls(
            config_dir=config_dir,
            config_file=config_dir / "config.ini",
            data_dir=data_dir,
            runtime_dir=data_dir / "runtime",
            models_dir=data_dir / "models",
            state_file=data_dir / "install.json",
            cache_dir=cache_dir,
            downloads_dir=cache_dir / "downloads",
            build_tools_dir=cache_dir / "build-tools",
        )

    def create(self) -> None:
        for path in (
            self.config_dir,
            self.data_dir,
            self.runtime_dir,
            self.models_dir,
            self.cache_dir,
            self.downloads_dir,
            self.build_tools_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class InstalledArtifact:
    id: str
    kind: str
    path: str
    revision: str | None = None
    sha256: str | None = None
    size: int | None = None
    source: str = "managed"


@dataclass(frozen=True)
class InstallState:
    backend: str
    features: tuple[str, ...]
    config_path: str
    runtime: InstalledArtifact | None = None
    models: tuple[InstalledArtifact, ...] = ()
    platform: dict[str, Any] = field(default_factory=dict)
    completed_stages: tuple[str, ...] = ()
    retained_cache: tuple[str, ...] = ()
    last_doctor: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    schema_version: int = INSTALL_STATE_SCHEMA_VERSION
    installer_version: str = __version__

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InstallState":
        if payload.get("schema_version") != INSTALL_STATE_SCHEMA_VERSION:
            raise ConfigurationError("Unsupported Speechloom installation state schema")
        try:
            runtime_payload = payload.get("runtime")
            runtime = InstalledArtifact(**runtime_payload) if runtime_payload else None
            models = tuple(InstalledArtifact(**item) for item in payload.get("models", []))
            return cls(
                backend=str(payload["backend"]),
                features=tuple(str(item) for item in payload.get("features", [])),
                config_path=str(payload["config_path"]),
                runtime=runtime,
                models=models,
                platform=dict(payload.get("platform", {})),
                completed_stages=tuple(str(item) for item in payload.get("completed_stages", [])),
                retained_cache=tuple(str(item) for item in payload.get("retained_cache", [])),
                last_doctor=(dict(payload["last_doctor"]) if payload.get("last_doctor") else None),
                created_at=str(payload["created_at"]),
                updated_at=str(payload["updated_at"]),
                schema_version=int(payload["schema_version"]),
                installer_version=str(payload["installer_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError("Speechloom installation state is incomplete") from exc

    def model(self, kind: str) -> InstalledArtifact | None:
        return next((model for model in self.models if model.kind == kind), None)


def load_install_state(path: Path) -> InstallState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Could not read Speechloom installation state: {path}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"Speechloom installation state is not an object: {path}")
    return InstallState.from_dict(payload)


def save_install_state(path: Path, state: InstallState) -> None:
    atomic_write_json(path, state.to_dict())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
