"""One background setup run at a time, with an event history and cancellation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable, Protocol, TypeVar
from uuid import uuid4

from speechloom.contracts import CancellationController, CancellationToken, StageEvent
from speechloom.errors import CancellationError
from speechloom.setup import SetupRequest, SetupResult, SetupStatus

from .errors import ApiError


T = TypeVar("T")
TERMINAL_STAGES = frozenset({"completed", "failed", "cancelled"})


class SetupBackend(Protocol):
    def setup(
        self,
        request: SetupRequest,
        *,
        on_event: Callable[[StageEvent], None] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> SetupResult: ...

    def status(self) -> SetupStatus: ...


@dataclass(frozen=True)
class SetupEvent:
    id: int
    payload: dict[str, Any]


@dataclass
class _Run:
    id: str
    request: dict[str, Any]
    started_at: str
    state: str = "running"
    finished_at: str | None = None
    current_stage: str = "queued"
    message: str = "Setup queued"
    error: str | None = None
    ready: bool | None = None
    actions: list[str] = field(default_factory=list)
    controller: CancellationController = field(default_factory=CancellationController)


class SetupRunner:
    """Runs ``SetupManager`` off the request path and keeps its progress for late readers.

    ``reload`` runs after every run and before the run is reported as ended; if it raises, the
    run's own result is still reported.
    """

    def __init__(
        self,
        backend: Callable[[], SetupBackend],
        reload: Callable[[], None],
        *,
        event_history: int = 1_000,
    ) -> None:
        self._backend_factory = backend
        self._backend: SetupBackend | None = None
        self._reload = reload
        self._event_history = event_history
        self._condition = threading.Condition(threading.RLock())
        self._run: _Run | None = None
        self._events: list[SetupEvent] = []
        self._next_event_id = 1

    @property
    def running(self) -> bool:
        with self._condition:
            return self._run is not None and self._run.state == "running"

    def start(self, request: SetupRequest, *, can_start: Callable[[], bool]) -> dict[str, Any]:
        with self._condition:
            if self.running:
                raise ApiError(409, "setup_running", "A setup run is already in progress")
            if not can_start():
                raise ApiError(
                    409, "jobs_active", "Setup cannot start while jobs are queued or running"
                )
            run = _Run(
                id=uuid4().hex,
                request={
                    "backend": request.backend,
                    "features": list(request.features),
                    "from_source": request.from_source,
                    "keep_cache": request.keep_cache,
                },
                started_at=_now(),
            )
            self._run = run
            self._events = []
            self._publish_locked(run, "queued", "queued", run.message)
            threading.Thread(
                target=self._work, args=(run, request), name="speechloom-setup", daemon=True
            ).start()
            return self._snapshot(run)

    def while_idle(self, action: Callable[[], T]) -> T:
        """Run ``action`` only while no setup run is active, and keep one from starting meanwhile."""
        with self._condition:
            if self.running:
                raise ApiError(409, "setup_running", "Setup is running; try again when it ends")
            return action()

    def current(self) -> dict[str, Any] | None:
        with self._condition:
            return self._snapshot(self._run) if self._run is not None else None

    def cancel(self) -> dict[str, Any]:
        with self._condition:
            if self._run is None:
                raise ApiError(404, "setup_not_started", "No setup run has started")
            if self._run.state == "running":
                self._run.controller.cancel()
            return self._snapshot(self._run)

    def status(self) -> dict[str, Any] | None:
        """Installed state, or None while a run is active (hashing models is slow)."""
        if self.running:
            return None
        if self._backend is None:
            self._backend = self._backend_factory()
        return self._backend.status().to_dict()

    def events(self, after: int = 0) -> tuple[SetupEvent, ...]:
        with self._condition:
            return tuple(event for event in self._events if event.id > after)

    def wait_for_events(self, *, after: int, timeout: float) -> tuple[SetupEvent, ...]:
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._run is None:
                raise ApiError(404, "setup_not_started", "No setup run has started")
            while True:
                events = self.events(after)
                if events or not self.running:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ()
                self._condition.wait(remaining)

    def _work(self, run: _Run, request: SetupRequest) -> None:
        state, error, result = "completed", None, None
        try:
            if self._backend is None:
                self._backend = self._backend_factory()
            result = self._backend.setup(
                request,
                on_event=lambda event: self._relay(run, event),
                cancellation=run.controller,
            )
        except CancellationError as exc:
            state, error = "cancelled", str(exc)
        except Exception as exc:
            state, error = "failed", str(exc)
        message = {
            "completed": "Setup completed",
            "failed": error or "Setup failed",
            "cancelled": error or "Setup cancelled",
        }[state]
        try:
            self._reload()
        except Exception as exc:
            message += f" (settings could not be reloaded: {exc})"
        with self._condition:
            run.state = state
            run.error = error
            run.finished_at = _now()
            run.message = message
            run.current_stage = state
            if result is not None:
                run.ready = result.ready
                run.actions = list(result.actions)
            self._publish_locked(run, state, state, message)

    def _relay(self, run: _Run, event: StageEvent) -> None:
        if event.stage in TERMINAL_STAGES:
            return  # the runner reports the end itself, once settings are reloaded
        with self._condition:
            run.current_stage = event.stage
            run.message = event.message
            self._publish_locked(run, event.stage, event.status, event.message, event.timestamp)

    def _publish_locked(
        self,
        run: _Run,
        stage: str,
        status: str,
        message: str,
        timestamp: str | None = None,
    ) -> None:
        self._events.append(
            SetupEvent(
                self._next_event_id,
                {
                    "task_id": run.id,
                    "stage": stage,
                    "status": status,
                    "message": message,
                    "timestamp": timestamp or _now(),
                },
            )
        )
        self._next_event_id += 1
        if len(self._events) > self._event_history:
            del self._events[: len(self._events) - self._event_history]
        self._condition.notify_all()

    @staticmethod
    def _snapshot(run: _Run) -> dict[str, Any]:
        return {
            "id": run.id,
            "state": run.state,
            "request": dict(run.request),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "current_stage": run.current_stage,
            "message": run.message,
            "error": run.error,
            "ready": run.ready,
            "actions": list(run.actions),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
