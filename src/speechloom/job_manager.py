"""Bounded in-process job coordination for GUI and HTTP adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import queue
import statistics
import threading
import time
from typing import Any, Callable, Protocol

from .artifacts import atomic_write_json, read_json
from .contracts import CancellationController, CancellationToken, StageEvent, TranscriptionRequest
from .errors import (
    CancellationError,
    DuplicateJobError,
    JobActiveError,
    JobNotFoundError,
    JobQueueFullError,
)
from .jobs import JobResult
from .media import discover_inputs


TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
_SPEED_SAMPLES = 5


class _TranscriptionService(Protocol):
    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        on_event: Callable[[StageEvent], None] | None = None,
        cancellation: CancellationToken | None = None,
        inference_gate=None,
    ) -> list[JobResult]: ...


class _SemaphoreGate:
    def __init__(
        self, semaphore: threading.Semaphore, cancellation: CancellationToken
    ) -> None:
        self._semaphore = semaphore
        self._cancellation = cancellation
        self._acquired = False

    def __enter__(self) -> "_SemaphoreGate":
        while True:
            self._cancellation.raise_if_cancelled()
            if self._semaphore.acquire(timeout=0.1):
                break
        self._acquired = True
        try:
            self._cancellation.raise_if_cancelled()
        except BaseException:
            self._acquired = False
            self._semaphore.release()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._acquired:
            self._acquired = False
            self._semaphore.release()


@dataclass(frozen=True)
class JobEvent:
    id: int
    event: StageEvent


@dataclass(frozen=True)
class ManagedJob:
    id: str
    state: str
    inputs: tuple[Path, ...]
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    current_stage: str = "queued"
    results: tuple[JobResult, ...] = ()
    error: str | None = None
    recovered: bool = False


@dataclass
class _JobRecord:
    id: str
    request: TranscriptionRequest | None
    state: str
    inputs: tuple[Path, ...]
    submitted_at: str
    mutation_keys: tuple[str, ...] = ()
    controller: CancellationController = field(default_factory=CancellationController)
    started_at: str | None = None
    finished_at: str | None = None
    current_stage: str = "queued"
    results: tuple[JobResult, ...] = ()
    error: str | None = None
    recovered: bool = False
    next_event_id: int = 1
    events: list[JobEvent] = field(default_factory=list)


class JobManager:
    """Run bounded local jobs while admitting one inference workload at a time."""

    def __init__(
        self,
        service: _TranscriptionService,
        *,
        queue_size: int = 16,
        media_workers: int = 2,
        inference_slots: int = 1,
        event_history: int = 1_000,
        recovery_roots: tuple[Path, ...] = (),
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        if media_workers < 1:
            raise ValueError("media_workers must be at least 1")
        if inference_slots < 1:
            raise ValueError("inference_slots must be at least 1")
        if event_history < 1:
            raise ValueError("event_history must be at least 1")
        self._service = service
        self._event_history = event_history
        self._condition = threading.Condition(threading.RLock())
        self._records: dict[str, _JobRecord] = {}
        self._active_mutations: dict[str, str] = {}
        self._speeds: list[tuple[str, float]] = []
        self._queue: queue.Queue[_JobRecord | None] = queue.Queue(maxsize=queue_size)
        self._inference_slots = threading.Semaphore(inference_slots)
        self._closed = False
        self._recover(recovery_roots)
        self._workers = tuple(
            threading.Thread(
                target=self._worker,
                name=f"speechloom-job-{index + 1}",
                daemon=True,
            )
            for index in range(media_workers)
        )
        for worker in self._workers:
            worker.start()

    def submit(self, request: TranscriptionRequest) -> ManagedJob:
        sources = tuple(discover_inputs(list(request.inputs), recursive=request.recursive))
        job_id = _request_id(request, sources)
        with self._condition:
            if self._closed:
                raise JobQueueFullError("Job manager is closed")
            existing = self._records.get(job_id)
            if existing is not None and existing.state not in TERMINAL_STATES:
                raise DuplicateJobError(f"Job is already active: {job_id}")
            mutation_keys = _mutation_keys(request, sources)
            owner = next(
                (self._active_mutations[key] for key in mutation_keys if key in self._active_mutations),
                None,
            )
            if owner is not None:
                raise DuplicateJobError(f"Job output is already being mutated by: {owner}")
            record = _JobRecord(
                id=job_id,
                request=request,
                state="queued",
                inputs=sources,
                submitted_at=_now(),
                mutation_keys=mutation_keys,
            )
            try:
                self._queue.put_nowait(record)
            except queue.Full as exc:
                raise JobQueueFullError("Speechloom's local job queue is full") from exc
            self._records[job_id] = record
            for key in mutation_keys:
                self._active_mutations[key] = job_id
            self._publish_locked(
                record,
                StageEvent(job_id, None, "queued", "queued", "Job queued"),
            )
            return _snapshot(record)

    def get(self, job_id: str) -> ManagedJob:
        with self._condition:
            return _snapshot(self._record(job_id))

    def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        state: str | None = None,
    ) -> tuple[ManagedJob, ...]:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        with self._condition:
            records = sorted(
                self._records.values(),
                key=lambda record: (record.submitted_at, record.id),
                reverse=True,
            )
            if state is not None:
                records = [record for record in records if record.state == state]
            return tuple(_snapshot(record) for record in records[offset : offset + limit])

    def count(self, *, state: str | None = None) -> int:
        with self._condition:
            if state is None:
                return len(self._records)
            return sum(record.state == state for record in self._records.values())

    def events(self, job_id: str, *, after: int = 0) -> tuple[JobEvent, ...]:
        with self._condition:
            record = self._record(job_id)
            return tuple(event for event in record.events if event.id > after)

    def wait_for_events(
        self,
        job_id: str,
        *,
        after: int = 0,
        timeout: float | None = None,
    ) -> tuple[JobEvent, ...]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                record = self._record(job_id)
                events = tuple(event for event in record.events if event.id > after)
                if events or record.state in TERMINAL_STATES:
                    return events
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return ()
                self._condition.wait(remaining)

    def wait(self, job_id: str, *, timeout: float | None = None) -> ManagedJob:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                record = self._record(job_id)
                if record.state in TERMINAL_STATES:
                    return _snapshot(record)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return _snapshot(record)
                self._condition.wait(remaining)

    def cancel(self, job_id: str) -> ManagedJob:
        with self._condition:
            record = self._record(job_id)
            if record.state in TERMINAL_STATES:
                return _snapshot(record)
            record.controller.cancel()
            if record.state == "queued":
                record.state = "cancelled"
                record.current_stage = "cancelled"
                record.finished_at = _now()
                record.error = "Operation cancelled"
                self._release_mutations_locked(record)
                status = "cancelled"
                message = "Queued job cancelled"
            else:
                status = "requested"
                message = "Cancellation requested"
            self._publish_locked(
                record,
                StageEvent(job_id, None, "cancelled", status, message),
            )
            return _snapshot(record)

    def forget(self, job_id: str) -> ManagedJob:
        """Drop a finished job from the manager and return its last snapshot."""
        with self._condition:
            record = self._record(job_id)
            if record.state not in TERMINAL_STATES:
                raise JobActiveError(f"Job is still active: {job_id}")
            del self._records[job_id]
            return _snapshot(record)

    def close(self, *, wait: bool = True, cancel: bool = False) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            active_ids = [
                record.id
                for record in self._records.values()
                if record.state not in TERMINAL_STATES
            ]
        if cancel:
            for job_id in active_ids:
                self.cancel(job_id)
        for _ in self._workers:
            self._queue.put(None)
        if wait:
            for worker in self._workers:
                worker.join()

    def __enter__(self) -> "JobManager":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close(wait=True, cancel=exc is not None)

    def _worker(self) -> None:
        while True:
            record = self._queue.get()
            try:
                if record is None:
                    return
                with self._condition:
                    if record.state in TERMINAL_STATES:
                        continue
                self._execute(record)
            finally:
                self._queue.task_done()

    def _execute(self, record: _JobRecord) -> None:
        with self._condition:
            if record.state in TERMINAL_STATES:
                return
            record.state = "running"
            record.started_at = _now()
            self._condition.notify_all()
        assert record.request is not None
        try:
            results = self._service.transcribe(
                record.request,
                on_event=lambda event: self._record_event(record.id, event),
                cancellation=record.controller,
                inference_gate=_SemaphoreGate(
                    self._inference_slots,
                    record.controller,
                ),
            )
            with self._condition:
                record.results = tuple(results)
                for result in results:
                    if result.state == "completed" and not result.skipped:
                        self._learn_speed(Path(result.job_dir) / "manifest.json")
                if record.controller.is_cancelled() or any(
                    result.state == "cancelled" for result in results
                ):
                    state = "cancelled"
                elif any(result.error for result in results):
                    state = "failed"
                else:
                    state = "completed"
                error = next((result.error for result in results if result.error), None)
                self._finish_locked(record, state, error)
        except CancellationError as exc:
            with self._condition:
                self._finish_locked(record, "cancelled", str(exc))
        except KeyboardInterrupt:
            with self._condition:
                self._finish_locked(record, "interrupted", "Job interrupted")
        except Exception as exc:
            with self._condition:
                self._finish_locked(record, "failed", str(exc))

    def _finish_locked(
        self, record: _JobRecord, state: str, error: str | None
    ) -> None:
        record.state = state
        record.current_stage = state
        record.error = error
        record.finished_at = _now()
        self._release_mutations_locked(record)
        self._publish_locked(
            record,
            StageEvent(record.id, None, state, state, f"Managed job {state}"),
        )

    def _record_event(self, job_id: str, event: StageEvent) -> None:
        with self._condition:
            record = self._record(job_id)
            self._publish_locked(
                record, replace(event, job_id=job_id, estimated_seconds=self._estimate(event))
            )

    def _estimate(self, event: StageEvent) -> float | None:
        if (
            event.stage != "transcribing"
            or event.status != "started"
            or not event.media_seconds
            or event.media_seconds <= 0
            or not self._speeds
        ):
            return None
        newest = sorted(self._speeds)[-_SPEED_SAMPLES:]
        speed = statistics.median(value for _, value in newest)
        return max(0.1, round(event.media_seconds / speed, 1))

    def _learn_speed(self, manifest_path: Path, manifest: dict[str, Any] | None = None) -> None:
        if manifest is None:
            try:
                manifest = read_json(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError):
                return
        sample = _manifest_speed(manifest)
        if sample is not None:
            self._speeds.append((str(manifest.get("updated_at", "")), sample))

    def _publish_locked(self, record: _JobRecord, event: StageEvent) -> None:
        record.current_stage = event.stage
        record.events.append(JobEvent(record.next_event_id, event))
        record.next_event_id += 1
        if len(record.events) > self._event_history:
            del record.events[: len(record.events) - self._event_history]
        self._condition.notify_all()

    def _record(self, job_id: str) -> _JobRecord:
        try:
            return self._records[job_id]
        except KeyError as exc:
            raise JobNotFoundError(f"Unknown job: {job_id}") from exc

    def _release_mutations_locked(self, record: _JobRecord) -> None:
        for key in record.mutation_keys:
            if self._active_mutations.get(key) == record.id:
                del self._active_mutations[key]

    def _recover(self, roots: tuple[Path, ...]) -> None:
        for root in roots:
            if not root.exists():
                continue
            for manifest_path in root.rglob("manifest.json"):
                try:
                    manifest = read_json(manifest_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                state = str(manifest.get("state", "unknown"))
                if state == "processing":
                    state = "interrupted"
                    manifest["state"] = state
                    manifest["updated_at"] = _now()
                    atomic_write_json(manifest_path, manifest)
                if state not in TERMINAL_STATES:
                    continue
                source_data = manifest.get("source")
                source = (
                    Path(str(source_data["path"]))
                    if isinstance(source_data, dict) and source_data.get("path")
                    else None
                )
                job_id = str(manifest.get("job_id") or _path_id(manifest_path))
                error_data = manifest.get("error")
                error = (
                    str(error_data.get("message"))
                    if isinstance(error_data, dict) and error_data.get("message")
                    else None
                )
                result = JobResult(
                    str(source) if source is not None else "",
                    str(manifest_path.parent),
                    state,
                    error=error,
                )
                record = _JobRecord(
                    id=job_id,
                    request=None,
                    state=state,
                    inputs=(source,) if source is not None else (),
                    submitted_at=str(manifest.get("created_at", _now())),
                    started_at=str(manifest.get("created_at", "")) or None,
                    finished_at=str(manifest.get("updated_at", "")) or None,
                    current_stage=state,
                    results=(result,),
                    error=error,
                    recovered=True,
                )
                record.events.append(
                    JobEvent(
                        1,
                        StageEvent(job_id, source, state, state, "Recovered from manifest"),
                    )
                )
                record.next_event_id = 2
                if state == "completed":
                    self._learn_speed(manifest_path, manifest)
                self._records.setdefault(job_id, record)


def _manifest_speed(manifest: dict[str, Any]) -> float | None:
    """Media seconds per inference second, or None when the manifest cannot say."""
    try:
        media = manifest["media"]["duration"]
        timings = manifest["timings"]
        if "inference_seconds" in timings:
            inference = timings["inference_seconds"]
        else:
            inference = timings["batch_inference_seconds"] / timings["batch_size"]
        speed = media / inference
    except (KeyError, TypeError, ZeroDivisionError):
        return None
    if not math.isfinite(speed) or speed <= 0:
        return None
    return speed


def _snapshot(record: _JobRecord) -> ManagedJob:
    return ManagedJob(
        id=record.id,
        state=record.state,
        inputs=record.inputs,
        submitted_at=record.submitted_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        current_stage=record.current_stage,
        results=record.results,
        error=record.error,
        recovered=record.recovered,
    )


def _request_id(request: TranscriptionRequest, sources: tuple[Path, ...]) -> str:
    payload = asdict(request)
    payload["inputs"] = [_path_identity(path) for path in sources]
    if request.output_dir is not None:
        payload["output_dir"] = str(request.output_dir.expanduser().resolve())
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _mutation_keys(
    request: TranscriptionRequest, sources: tuple[Path, ...]
) -> tuple[str, ...]:
    output = (
        str(request.output_dir.expanduser().resolve())
        if request.output_dir is not None
        else "<default-output>"
    )
    return tuple(
        hashlib.sha256(
            f"{path.expanduser().resolve()}\0{output}".encode("utf-8")
        ).hexdigest()
        for path in sources
    )


def _path_identity(path: Path) -> dict[str, object]:
    resolved = path.expanduser().resolve()
    try:
        stat = resolved.stat()
    except OSError:
        return {"path": str(resolved), "missing": True}
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }


def _path_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
