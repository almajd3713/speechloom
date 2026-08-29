from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from speechloom import JobManager, StageEvent, TranscriptionRequest
from speechloom.errors import DuplicateJobError, JobQueueFullError
from speechloom.jobs import JobResult


class BlockingService:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def transcribe(
        self,
        request,
        *,
        on_event=None,
        cancellation=None,
        inference_gate=None,
    ):
        with inference_gate or nullcontext():
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.calls += 1
                self.entered.set()
            try:
                if on_event is not None:
                    on_event(
                        StageEvent(
                            None,
                            request.inputs[0],
                            "transcribing",
                            "started",
                            "Fixture inference started",
                        )
                    )
                while not self.release.wait(0.01):
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                return [
                    JobResult(str(source), f"/jobs/{source.stem}", "completed")
                    for source in request.inputs
                ]
            finally:
                with self._lock:
                    self.active -= 1


def _request(path: Path, **kwargs) -> TranscriptionRequest:
    path.write_bytes(b"media")
    return TranscriptionRequest(inputs=(path,), **kwargs)


class JobManagerTests(unittest.TestCase):
    def test_media_preparation_is_bounded_but_can_run_in_parallel(self) -> None:
        class PreparationService:
            def __init__(self) -> None:
                self.release = threading.Event()
                self.lock = threading.Lock()
                self.active = 0
                self.max_active = 0

            def transcribe(
                self,
                request,
                *,
                on_event=None,
                cancellation=None,
                inference_gate=None,
            ):
                del on_event
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    while not self.release.wait(0.01):
                        if cancellation is not None:
                            cancellation.raise_if_cancelled()
                finally:
                    with self.lock:
                        self.active -= 1
                with inference_gate or nullcontext():
                    return [JobResult(str(request.inputs[0]), "/jobs/prepared", "completed")]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = PreparationService()
            manager = JobManager(service, queue_size=3, media_workers=2)
            try:
                jobs = [
                    manager.submit(_request(root / f"prep-{index}.wav"))
                    for index in range(3)
                ]
                deadline = time.monotonic() + 2
                while service.max_active < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(service.max_active, 2)
                service.release.set()
                self.assertTrue(
                    all(manager.wait(job.id, timeout=2).state == "completed" for job in jobs)
                )
            finally:
                manager.close(cancel=True)

    def test_one_inference_slot_serializes_multiple_clients(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = BlockingService()
            manager = JobManager(
                service,
                queue_size=4,
                media_workers=3,
                inference_slots=1,
            )
            try:
                jobs = [
                    manager.submit(_request(root / f"{index}.wav"))
                    for index in range(3)
                ]
                self.assertTrue(service.entered.wait(2))
                service.release.set()
                completed = [manager.wait(job.id, timeout=3) for job in jobs]

                self.assertTrue(all(job.state == "completed" for job in completed))
                self.assertEqual(service.max_active, 1)
                self.assertEqual(service.calls, 3)
            finally:
                manager.close(cancel=True)

    def test_queue_bound_and_duplicate_mutation_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = BlockingService()
            manager = JobManager(service, queue_size=1, media_workers=1)
            try:
                first_request = _request(root / "first.wav", formats=("json",))
                first = manager.submit(first_request)
                self.assertTrue(service.entered.wait(2))
                manager.submit(_request(root / "second.wav"))

                with self.assertRaises(JobQueueFullError):
                    manager.submit(_request(root / "third.wav"))
                with self.assertRaises(DuplicateJobError):
                    manager.submit(
                        TranscriptionRequest(
                            inputs=first_request.inputs,
                            formats=("json", "txt"),
                        )
                    )

                self.assertEqual(manager.get(first.id).state, "running")
            finally:
                manager.close(cancel=True)

    def test_active_cancellation_and_event_replay_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = BlockingService()
            manager = JobManager(service, queue_size=2, media_workers=1)
            try:
                submitted = manager.submit(_request(root / "cancel.wav"))
                self.assertTrue(service.entered.wait(2))

                requested = manager.cancel(submitted.id)
                cancelled = manager.wait(submitted.id, timeout=3)
                repeated = manager.cancel(submitted.id)

                self.assertEqual(requested.state, "running")
                self.assertEqual(cancelled.state, "cancelled")
                self.assertEqual(repeated, cancelled)
                first_subscriber = manager.events(submitted.id)
                second_subscriber = manager.events(submitted.id)
                self.assertEqual(first_subscriber, second_subscriber)
                self.assertEqual(
                    [event.id for event in first_subscriber],
                    list(range(1, len(first_subscriber) + 1)),
                )
                self.assertEqual(first_subscriber[-1].event.stage, "cancelled")
            finally:
                manager.close(cancel=True)

    def test_reconciles_processing_manifests_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "job/manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "job_id": "a" * 64,
                        "state": "processing",
                        "state_detail": "transcribed",
                        "source": {"path": str(root / "source.wav")},
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:01:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            manager = JobManager(BlockingService(), recovery_roots=(root,))
            try:
                recovered = manager.get("a" * 64)
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))

                self.assertTrue(recovered.recovered)
                self.assertEqual(recovered.state, "interrupted")
                self.assertEqual(recovered.current_stage, "interrupted")
                self.assertEqual(payload["state"], "interrupted")
                self.assertEqual(payload["state_detail"], "transcribed")
            finally:
                manager.close()

    def test_preserves_successful_batch_results_when_one_item_fails(self) -> None:
        class PartialFailureService:
            def transcribe(
                self,
                request,
                *,
                on_event=None,
                cancellation=None,
                inference_gate=None,
            ):
                del on_event, cancellation, inference_gate
                return [
                    JobResult(str(request.inputs[0]), "/jobs/good", "completed"),
                    JobResult(
                        str(request.inputs[1]),
                        "/jobs/bad",
                        "failed",
                        error="fixture failure",
                    ),
                ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = (root / "good.wav", root / "bad.wav")
            for source in sources:
                source.write_bytes(b"media")
            manager = JobManager(PartialFailureService(), media_workers=1)
            try:
                submitted = manager.submit(TranscriptionRequest(inputs=sources))
                finished = manager.wait(submitted.id, timeout=2)

                self.assertEqual(finished.state, "failed")
                self.assertEqual(len(finished.results), 2)
                self.assertEqual(finished.results[0].state, "completed")
                self.assertEqual(finished.results[1].state, "failed")
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
