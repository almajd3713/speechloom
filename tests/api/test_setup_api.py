from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import httpx2 as httpx

from speechloom.api.server import ServerSettings, create_app
from speechloom.api.setup_runner import SetupRunner
from speechloom.contracts import StageEvent
from speechloom.doctor import Check, DoctorReport
from speechloom.errors import CancellationError, SetupError
from speechloom.job_manager import JobManager
from speechloom.setup import SetupStatus

from .test_api import FixtureService, _upload, _wait_until_entered


class FakeSetup:
    """Stands in for ``SetupManager``; the test decides when a run may finish."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.fail = fail
        self.requests: list = []
        self.status_calls = 0

    def setup(self, request, *, on_event=None, cancellation=None):
        self.requests.append(request)
        self.entered.set()
        on_event(StageEvent(None, None, "validating", "started", "Validating"))
        on_event(StageEvent(None, None, "installing", "started", "installing ASR model"))
        while not self.release.wait(0.01):
            if cancellation.is_cancelled():
                on_event(StageEvent(None, None, "cancelled", "cancelled", "Operation cancelled"))
                raise CancellationError("Operation cancelled")
        if self.fail:
            on_event(StageEvent(None, None, "failed", "failed", self.fail))
            raise SetupError(self.fail)
        on_event(StageEvent(None, None, "completed", "completed", "Setup completed"))
        return SimpleNamespace(ready=True, actions=("installed ASR model",))

    def status(self) -> SetupStatus:
        self.status_calls += 1
        return SetupStatus(True, True, "/state.json", "/config.ini", backend="cpu")


class DoctorService(FixtureService):
    def doctor(self) -> DoctorReport:
        return DoctorReport(True, (Check("ffmpeg", "ok", "ffmpeg 7"),))


def _sse(text: str) -> list[dict]:
    return [
        {"id": int(block.splitlines()[0][4:]), **json.loads(block.split("data: ", 1)[1])}
        for block in text.strip().split("\n\n")
        if block.startswith("id: ")
    ]


class SetupApiTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, root: Path, *, service=None, backend=None, reload=None, host="127.0.0.1"):
        service = service or DoctorService()
        backend = backend or FakeSetup()
        self.reloads: list[str] = []
        self.state_at_reload: list[str] = []

        def do_reload() -> None:
            self.reloads.append("reload")
            self.state_at_reload.append(self.runner.current()["state"])
            if reload is not None:
                reload()

        self.runner = SetupRunner(lambda: backend, do_reload)
        manager = JobManager(service, queue_size=4, media_workers=1)
        extra = {"allow_remote": True, "bearer_token": "secret"} if host != "127.0.0.1" else {}
        settings = ServerSettings(
            host=host,
            allowed_roots=(root,),
            staging_dir=root / "staging",
            max_upload_bytes=1024 * 1024,
            **extra,
        )
        app = create_app(service, manager, settings, setup_runner=self.runner)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"http://{host}:8765"
        )
        return client, manager, backend

    async def _finished(self, client: httpx.AsyncClient) -> dict:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task = (await client.get("/v1/setup")).json()["task"]
            if task["state"] != "running":
                return task
            await asyncio.sleep(0.01)
        self.fail("setup did not finish")

    async def test_run_lifecycle_reloads_settings_before_reporting_the_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager, backend = self._app(Path(directory))
            try:
                before = (await client.get("/v1/setup")).json()
                self.assertIsNone(before["task"])
                self.assertEqual(before["status"]["backend"], "cpu")

                started = await client.post(
                    "/v1/setup", json={"backend": "cpu", "features": ["diarization"]}
                )
                self.assertEqual(started.status_code, 202)
                run = started.json()
                self.assertEqual(run["state"], "running")
                self.assertEqual(
                    run["request"],
                    {
                        "backend": "cpu",
                        "features": ["diarization"],
                        "from_source": False,
                        "keep_cache": False,
                    },
                )
                backend.entered.wait(2)
                calls = backend.status_calls

                during = (await client.get("/v1/setup")).json()
                self.assertIsNone(during["status"])
                self.assertEqual(during["task"]["id"], run["id"])
                self.assertEqual(backend.status_calls, calls)
                again = await client.post("/v1/setup")
                self.assertEqual(
                    (again.status_code, again.json()["error"]["code"]), (409, "setup_running")
                )
                doctor = await client.get("/v1/doctor")
                self.assertEqual(
                    (doctor.status_code, doctor.json()["error"]["code"]), (409, "setup_running")
                )

                backend.release.set()
                done = await self._finished(client)

                self.assertEqual(done["state"], "completed")
                self.assertTrue(done["ready"])
                self.assertEqual(done["actions"], ["installed ASR model"])
                self.assertEqual(self.reloads, ["reload"])
                self.assertEqual(self.state_at_reload, ["running"])
                events = _sse((await client.get("/v1/setup/events")).text)
                self.assertEqual(
                    [e["stage"] for e in events],
                    ["queued", "validating", "installing", "completed"],
                )
                self.assertEqual({e["task_id"] for e in events}, {run["id"]})
                self.assertEqual(
                    set(events[0]), {"id", "task_id", "stage", "status", "message", "timestamp"}
                )
                self.assertEqual(events[-1]["message"], "Setup completed")
                after_run = (await client.get("/v1/setup")).json()
                self.assertEqual(after_run["status"]["installed"], True)
                report = (await client.get("/v1/doctor")).json()
                self.assertEqual(report["checks"][0]["name"], "ffmpeg")
                self.assertTrue(report["ready"])
            finally:
                await client.aclose()
                manager.close(cancel=True)

    async def test_event_ids_keep_increasing_and_resume_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager, backend = self._app(Path(directory))
            try:
                backend.release.set()
                await client.post("/v1/setup")
                await self._finished(client)
                first = _sse((await client.get("/v1/setup/events")).text)
                await client.post("/v1/setup")
                await self._finished(client)

                second = _sse((await client.get("/v1/setup/events")).text)
                resumed = _sse(
                    (
                        await client.get(
                            "/v1/setup/events", headers={"Last-Event-ID": str(second[1]["id"])}
                        )
                    ).text
                )

                self.assertGreater(second[0]["id"], first[-1]["id"])
                self.assertEqual([e["id"] for e in resumed], [e["id"] for e in second[2:]])
                self.assertEqual(len({e["task_id"] for e in second}), 1)
            finally:
                await client.aclose()
                manager.close(cancel=True)

    async def test_failed_and_cancelled_runs_report_their_end_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager, backend = self._app(Path(directory), backend=FakeSetup(fail="no disk"))
            try:
                backend.release.set()
                await client.post("/v1/setup")
                failed = await self._finished(client)
                self.assertEqual((failed["state"], failed["error"]), ("failed", "no disk"))
                self.assertIsNone(failed["ready"])

                backend.release.clear()
                backend.fail = None
                await client.post("/v1/setup")
                backend.entered.wait(2)
                cancelled = await client.delete("/v1/setup")
                self.assertEqual(cancelled.status_code, 200)
                done = await self._finished(client)
                self.assertEqual(done["state"], "cancelled")
                self.assertEqual(self.reloads, ["reload", "reload"])
                last = _sse((await client.get("/v1/setup/events")).text)[-1]
                self.assertEqual((last["stage"], last["status"]), ("cancelled", "cancelled"))
            finally:
                await client.aclose()
                manager.close(cancel=True)

    async def test_failed_reload_keeps_the_run_result(self) -> None:
        def broken() -> None:
            raise RuntimeError("bad config")

        with tempfile.TemporaryDirectory() as directory:
            client, manager, backend = self._app(Path(directory), reload=broken)
            try:
                backend.release.set()
                await client.post("/v1/setup")
                done = await self._finished(client)

                self.assertEqual(done["state"], "completed")
                self.assertTrue(done["ready"])
                self.assertIn("bad config", done["message"])
            finally:
                await client.aclose()
                manager.close(cancel=True)

    async def test_request_and_event_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager, backend = self._app(Path(directory))
            try:
                for path in ("/v1/setup/events",):
                    missing = await client.get(path)
                    self.assertEqual(
                        (missing.status_code, missing.json()["error"]["code"]),
                        (404, "setup_not_started"),
                    )
                missing = await client.delete("/v1/setup")
                self.assertEqual(
                    (missing.status_code, missing.json()["error"]["code"]),
                    (404, "setup_not_started"),
                )
                for body in ({"backend": "gpu"}, {"features": ["telepathy"]}):
                    invalid = await client.post("/v1/setup", json=body)
                    self.assertEqual(
                        (invalid.status_code, invalid.json()["error"]["code"]),
                        (422, "invalid_setup"),
                    )
                self.assertEqual(backend.requests, [])
                backend.release.set()
                await client.post("/v1/setup")
                await self._finished(client)
                bad = await client.get("/v1/setup/events", headers={"Last-Event-ID": "x"})
                self.assertEqual(
                    (bad.status_code, bad.json()["error"]["code"]), (422, "invalid_event_id")
                )
            finally:
                await client.aclose()
                manager.close(cancel=True)

    async def test_setup_and_jobs_exclude_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DoctorService(block=True)
            client, manager, backend = self._app(root, service=service)
            try:
                submitted = await client.post("/v1/jobs", **_upload("clip.wav"))
                self.assertEqual(submitted.status_code, 202)
                await _wait_until_entered(service)

                refused = await client.post("/v1/setup")
                self.assertEqual(
                    (refused.status_code, refused.json()["error"]["code"]), (409, "jobs_active")
                )
                service.release.set()
                job_id = submitted.json()["id"]
                deadline = time.monotonic() + 2
                while (await client.get(f"/v1/jobs/{job_id}")).json()["state"] != "completed":
                    self.assertLess(time.monotonic(), deadline)
                    await asyncio.sleep(0.01)
                staged = len(list((root / "staging").glob("*")))

                await client.post("/v1/setup")
                backend.entered.wait(2)
                blocked = await client.post("/v1/jobs", **_upload("other.wav"))

                self.assertEqual(
                    (blocked.status_code, blocked.json()["error"]["code"]),
                    (409, "setup_running"),
                )
                self.assertEqual(len(list((root / "staging").glob("*"))), staged)
                backend.release.set()
                await self._finished(client)
            finally:
                service.release.set()
                backend.release.set()
                await client.aclose()
                manager.close(cancel=True)

    async def test_setup_and_doctor_are_loopback_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager, _ = self._app(Path(directory), host="0.0.0.0")
            headers = {"Authorization": "Bearer secret"}
            try:
                for method, path in (
                    ("GET", "/v1/doctor"),
                    ("GET", "/v1/setup"),
                    ("POST", "/v1/setup"),
                    ("DELETE", "/v1/setup"),
                    ("GET", "/v1/setup/events"),
                ):
                    response = await client.request(method, path, headers=headers)
                    self.assertEqual(
                        (response.status_code, response.json()["error"]["code"]),
                        (403, "local_only"),
                        path,
                    )
            finally:
                await client.aclose()
                manager.close(cancel=True)


if __name__ == "__main__":
    unittest.main()
