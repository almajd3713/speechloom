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



def _manifest(
    root: Path,
    name: str,
    *,
    media: object = 20.0,
    timings: dict[str, object] | None = None,
    state: str = "completed",
    updated_at: str = "2026-01-01T00:00:00+00:00",
) -> Path:
    path = root / name / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "job_id": name.ljust(64, "0"),
                "state": state,
                "created_at": updated_at,
                "updated_at": updated_at,
                "source": {"path": str(root / f"{name}.wav")},
                "media": {"duration": media},
                "timings": {"inference_seconds": 10.0} if timings is None else timings,
            }
        ),
        encoding="utf-8",
    )
    return path.parent


class EstimateService:
    """Finishes instantly, announcing each file's duration and writing its manifest."""

    def __init__(self, root: Path, outcomes: dict[str, dict[str, object]] | None = None) -> None:
        self._root = root
        self._outcomes = outcomes or {}

    def transcribe(self, request, *, on_event=None, cancellation=None, inference_gate=None):
        del cancellation, inference_gate
        results = []
        for source in request.inputs:
            outcome = self._outcomes.get(source.stem, {})
            job_dir = _manifest(
                self._root / "out",
                source.stem,
                media=outcome.get("media", 20.0),
                timings=outcome.get("timings"),
                updated_at=str(outcome.get("updated_at", "2026-02-01T00:00:00+00:00")),
            )
            if on_event is not None:
                on_event(
                    StageEvent(
                        None,
                        source,
                        "transcribing",
                        "started",
                        "Transcription started",
                        media_seconds=float(request_duration(source)),
                    )
                )
            results.append(
                JobResult(
                    str(source),
                    str(job_dir),
                    str(outcome.get("state", "completed")),
                    skipped=bool(outcome.get("skipped", False)),
                )
            )
        return results


def request_duration(source: Path) -> float:
    return float(source.stem.rsplit("-", 1)[-1]) if "-" in source.stem else 30.0


class EstimateTests(unittest.TestCase):
    def _run(self, manager: JobManager, path: Path):
        submitted = manager.submit(_request(path))
        manager.wait(submitted.id, timeout=2)
        return [
            item.event
            for item in manager.wait_for_events(submitted.id, after=0, timeout=0.1)
            if item.event.stage == "transcribing"
        ]

    def test_first_job_has_duration_but_no_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with JobManager(EstimateService(root)) as manager:
                (event,) = self._run(manager, root / "first-30.wav")

        self.assertEqual(event.media_seconds, 30.0)
        self.assertIsNone(event.estimated_seconds)

    def test_next_job_is_estimated_from_the_previous_speed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with JobManager(EstimateService(root)) as manager:
                self._run(manager, root / "first-30.wav")  # 20 s of media in 10 s: speed 2
                (event,) = self._run(manager, root / "second-30.wav")

        self.assertEqual(event.estimated_seconds, 15.0)

    def test_shared_batch_time_is_split_across_the_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = EstimateService(
                root,
                {"first-30": {"timings": {"batch_inference_seconds": 40.0, "batch_size": 4}}},
            )
            with JobManager(service) as manager:
                self._run(manager, root / "first-30.wav")
                (event,) = self._run(manager, root / "second-30.wav")

        self.assertEqual(event.estimated_seconds, 15.0)

    def test_only_the_newest_five_timed_manifests_count_and_recovery_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # The oldest manifest is an outlier (speed 100); the newest five have speeds 1, 2, 2, 4, 5.
            for index, (inference, day) in enumerate(
                [(0.2, 1), (20.0, 2), (10.0, 3), (10.0, 4), (5.0, 5), (4.0, 6)]
            ):
                _manifest(
                    root / "recovered",
                    f"m{index}",
                    timings={"inference_seconds": inference},
                    updated_at=f"2026-01-0{day}T00:00:00+00:00",
                )
            with JobManager(EstimateService(root), recovery_roots=(root / "recovered",)) as manager:
                (event,) = self._run(manager, root / "next-30.wav")

        self.assertEqual(event.estimated_seconds, 15.0)  # median speed 2

    def test_unusable_results_do_not_count(self) -> None:
        bad_timings: dict[str, dict[str, object]] = {
            "failed-30": {"state": "failed"},
            "cancelled-30": {"state": "cancelled"},
            "skipped-30": {"skipped": True},
            "nomedia-30": {"media": None},
            "zeromedia-30": {"media": 0},
            "zerotime-30": {"timings": {"inference_seconds": 0}},
            "negtime-30": {"timings": {"inference_seconds": -1.0}},
            "notime-30": {"timings": {}},
            "nobatch-30": {"timings": {"batch_inference_seconds": 5.0}},
            "emptybatch-30": {"timings": {"batch_inference_seconds": 5.0, "batch_size": 0}},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with JobManager(EstimateService(root, bad_timings), queue_size=16) as manager:
                for name in bad_timings:
                    self._run(manager, root / f"{name}.wav")
                (event,) = self._run(manager, root / "last-30.wav")

        self.assertIsNone(event.estimated_seconds)

    def test_estimate_is_rounded_and_never_below_a_tenth_of_a_second(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = EstimateService(root, {"first-30": {"media": 30.0, "timings": {"inference_seconds": 7.0}}})
            with JobManager(service) as manager:
                self._run(manager, root / "first-30.wav")  # speed 30/7
                (rounded,) = self._run(manager, root / "second-10.wav")
                (floored,) = self._run(manager, root / "third-0.001.wav")

        self.assertEqual(rounded.estimated_seconds, 2.3)  # 10 / (30/7) = 2.33
        self.assertEqual(floored.estimated_seconds, 0.1)


if __name__ == "__main__":
    unittest.main()
