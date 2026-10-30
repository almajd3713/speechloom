from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import httpx2 as httpx

from speechloom.api.errors import ApiError
from speechloom.api.server import ServerSettings, _format_sse_event, _sweep_staging, create_app
from speechloom.artifacts import sha256_file
from speechloom.contracts import JobDetails, StageEvent
from speechloom.errors import ConfigurationError
from speechloom.job_manager import JobManager
from speechloom.jobs import JobResult


class FixtureService:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.entered = threading.Event()
        self.release = threading.Event()

    def transcribe(
        self,
        request,
        *,
        on_event=None,
        cancellation=None,
        inference_gate=None,
    ):
        source = request.inputs[0]
        if on_event is not None:
            on_event(StageEvent(None, source, "probing", "started", "Fixture probe"))
        with inference_gate or nullcontext():
            self.entered.set()
            while self.block and not self.release.wait(0.01):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
        job_dir = (request.output_dir or source.parent / "output") / f"job-{source.stem}"
        job_dir.mkdir(parents=True, exist_ok=True)
        artifact = job_dir / "transcript.txt"
        artifact.write_text("fixture transcript\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "job_id": "fixture",
            "state": "completed",
            "state_detail": "completed",
            "source": {"path": str(source)},
            "artifacts": {
                "txt": {
                    "path": artifact.name,
                    "size": artifact.stat().st_size,
                    "sha256": sha256_file(artifact),
                }
            },
        }
        (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return [JobResult(str(source), str(job_dir), "completed")]

    def inspect(self, job) -> JobDetails:
        path = Path(job)
        manifest = path / "manifest.json" if path.is_dir() else path
        return JobDetails.from_manifest(json.loads(manifest.read_text(encoding="utf-8")))


async def _wait_for_state(client: httpx.AsyncClient, job_id: str, state: str) -> dict:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = (await client.get(f"/v1/jobs/{job_id}")).json()
        if job["state"] == state:
            break
        await asyncio.sleep(0.01)
    return job


async def _wait_until_entered(service: FixtureService) -> None:
    deadline = time.monotonic() + 2
    while not service.entered.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


def _upload(name: str) -> dict:
    return {
        "files": [("files", (name, b"media", "audio/wav"))],
        "data": {"options": json.dumps({"formats": ["txt"]})},
    }


class ApiContractTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, root: Path, service: FixtureService):
        manager = JobManager(service, queue_size=4, media_workers=1)
        settings = ServerSettings(
            allowed_roots=(root,),
            staging_dir=root / "staging",
            max_upload_bytes=1024 * 1024,
        )
        return create_app(service, manager, settings), manager

    async def test_versioned_contract_submit_events_and_manifest_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            service = FixtureService()
            app, manager = self._app(root, service)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    self.assertEqual((await client.get("/v1/health")).json(), {"status": "ok"})
                    capabilities = (await client.get("/v1/capabilities")).json()
                    self.assertTrue(capabilities["local_path_input"])

                    submitted = await client.post(
                        "/v1/jobs",
                        json={"inputs": [str(source)], "formats": ["txt"]},
                    )
                    self.assertEqual(submitted.status_code, 202)
                    job_id = submitted.json()["id"]
                    self.assertEqual(submitted.headers["location"], f"/v1/jobs/{job_id}")

                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        job = (await client.get(f"/v1/jobs/{job_id}")).json()
                        if job["state"] == "completed":
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(job["state"], "completed")
                    listing = (await client.get("/v1/jobs?offset=0&limit=1")).json()
                    self.assertEqual((listing["total"], len(listing["items"])), (1, 1))

                    event_text = "".join(
                        _format_sse_event(item) for item in manager.events(job_id)
                    )
                    self.assertIn("id: 1", event_text)
                    self.assertIn("event: completed", event_text)

                    artifacts = (
                        await client.get(f"/v1/jobs/{job_id}/artifacts")
                    ).json()["items"]
                    self.assertEqual([item["name"] for item in artifacts], ["txt"])
                    artifact_route = next(
                        route
                        for route in app.routes
                        if getattr(route, "path", "")
                        == "/v1/jobs/{job_id}/artifacts/{artifact_name:path}"
                    )
                    downloaded = await artifact_route.endpoint(job_id, "txt")
                    self.assertEqual(
                        Path(downloaded.path).read_text(encoding="utf-8"),
                        "fixture transcript\n",
                    )
                    with self.assertRaises(ApiError):
                        await artifact_route.endpoint(job_id, "manifest.json")

                    document = (await client.get("/openapi.json")).json()
                    self.assertEqual(document["info"]["version"], "1")
                    self.assertTrue(
                        {
                            "/v1/health",
                            "/v1/capabilities",
                            "/v1/jobs",
                            "/v1/jobs/{job_id}",
                            "/v1/jobs/{job_id}/events",
                            "/v1/jobs/{job_id}/artifacts",
                            "/v1/jobs/{job_id}/artifacts/{artifact_name}",
                        }.issubset(document["paths"])
                    )
                    event_content = document["paths"][
                        "/v1/jobs/{job_id}/events"
                    ]["get"]["responses"]["200"]["content"]
                    self.assertIn("text/event-stream", event_content)
                    unknown = await client.get("/v1/jobs/not-a-job")
                    self.assertEqual(unknown.status_code, 404)
                    self.assertEqual(
                        unknown.json()["error"]["code"], "job_not_found"
                    )
            finally:
                manager.close(cancel=True)

    async def test_upload_path_policy_authentication_and_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.wav"
            outside.write_bytes(b"outside")
            service = FixtureService(block=True)
            app, manager = self._app(root, service)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    denied = await client.post("/v1/jobs", json={"inputs": [str(outside)]})
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(denied.json()["error"]["code"], "path_not_allowed")
                    self.assertIn("request_id", denied.json()["error"])

                    invalid_options = await client.post(
                        "/v1/jobs",
                        files=[("files", ("sample.wav", b"media", "audio/wav"))],
                        data={"options": json.dumps({"output_dir": str(root)})},
                    )
                    self.assertEqual(invalid_options.status_code, 422)
                    self.assertFalse(list((root / "staging").glob("*")))

                    oversized = await client.post(
                        "/v1/jobs",
                        files=[
                            (
                                "files",
                                ("large.wav", b"x" * (1024 * 1024 + 1), "audio/wav"),
                            )
                        ],
                    )
                    self.assertEqual(oversized.status_code, 413)

                    submitted = await client.post(
                        "/v1/jobs",
                        files=[("files", ("../unsafe name.wav", b"media", "audio/wav"))],
                        data={"options": json.dumps({"formats": ["txt"]})},
                    )
                    self.assertEqual(submitted.status_code, 202)
                    job_id = submitted.json()["id"]
                    deadline = time.monotonic() + 2
                    while not service.entered.is_set() and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
                    self.assertTrue(service.entered.is_set())
                    first = await client.delete(f"/v1/jobs/{job_id}")
                    second = await client.delete(f"/v1/jobs/{job_id}")
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(second.status_code, 200)
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        state = (await client.get(f"/v1/jobs/{job_id}")).json()["state"]
                        if state == "cancelled":
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(state, "cancelled")
                    staged = list((root / "staging").glob("*/*"))
                    self.assertEqual(len(staged), 1)
                    self.assertNotIn("..", staged[0].name)

                    removed = await client.delete(f"/v1/jobs/{job_id}/upload")
                    self.assertEqual(removed.status_code, 204)
                    self.assertEqual(removed.content, b"")
                    self.assertFalse(list((root / "staging").glob("*")))
                    after = (await client.get(f"/v1/jobs/{job_id}")).json()
                    self.assertFalse(after["has_upload"])
            finally:
                service.release.set()
                manager.close(cancel=True)
                outside.unlink(missing_ok=True)

            remote = ServerSettings(
                host="0.0.0.0",
                allow_remote=True,
                bearer_token="secret",
                staging_dir=root / "remote-staging",
            )
            remote_service = FixtureService()
            remote_manager = JobManager(remote_service)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=create_app(remote_service, remote_manager, remote)
                    ),
                    base_url="http://test",
                ) as client:
                    self.assertEqual((await client.get("/v1/health")).status_code, 401)
                    authorized = await client.get(
                        "/v1/health", headers={"Authorization": "Bearer secret"}
                    )
                    self.assertEqual(authorized.status_code, 200)
                    local_path = await client.post(
                        "/v1/jobs",
                        json={"inputs": [str(root / "anything.wav")]},
                        headers={"Authorization": "Bearer secret"},
                    )
                    self.assertEqual(local_path.status_code, 403)
            finally:
                remote_manager.close(cancel=True)

    async def test_rejects_foreign_hosts_and_origins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, manager = self._app(root, FixtureService())
            upload = {"files": [("files", ("clip.wav", b"media", "audio/wav"))]}
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://attacker.example:8765"
                ) as client:
                    health = await client.get("/v1/health")
                    posted = await client.post("/v1/jobs", **upload)
                    self.assertEqual(health.status_code, 403)
                    self.assertEqual(health.json()["error"]["code"], "host_not_allowed")
                    self.assertEqual(posted.status_code, 403)
                    self.assertEqual(posted.json()["error"]["code"], "host_not_allowed")
                    self.assertFalse((root / "staging").exists())

                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    foreign = {"Origin": "https://evil.example"}
                    listed = await client.get("/v1/jobs", headers=foreign)
                    posted = await client.post("/v1/jobs", headers=foreign, **upload)
                    null_origin = await client.get("/v1/jobs", headers={"Origin": "null"})
                    self.assertEqual(listed.status_code, 403)
                    self.assertEqual(listed.json()["error"]["code"], "origin_not_allowed")
                    self.assertEqual(posted.status_code, 403)
                    self.assertEqual(posted.json()["error"]["code"], "origin_not_allowed")
                    self.assertEqual(null_origin.status_code, 403)
                    own = await client.get(
                        "/v1/health", headers={"Origin": "http://127.0.0.1:8765"}
                    )
                    self.assertEqual(own.status_code, 200)

                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765"
                ) as client:
                    self.assertEqual((await client.get("/v1/health")).status_code, 200)

                allowed_service = FixtureService()
                allowed_manager = JobManager(allowed_service, queue_size=4, media_workers=1)
                allowed_app = create_app(
                    allowed_service,
                    allowed_manager,
                    ServerSettings(
                        allowed_roots=(root,),
                        allowed_origins=("http://localhost:5173",),
                        staging_dir=root / "staging-2",
                    ),
                )
                try:
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=allowed_app),
                        base_url="http://127.0.0.1:8765",
                    ) as client:
                        configured = await client.get(
                            "/v1/health", headers={"Origin": "http://localhost:5173"}
                        )
                        other = await client.get(
                            "/v1/health", headers={"Origin": "http://localhost:5174"}
                        )
                        self.assertEqual(configured.status_code, 200)
                        self.assertEqual(other.status_code, 403)
                finally:
                    allowed_manager.close(cancel=True)
            finally:
                manager.close(cancel=True)

    async def test_upload_is_kept_until_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    submitted = await client.post("/v1/jobs", **_upload("clip.wav"))
                    job_id = submitted.json()["id"]
                    job = await _wait_for_state(client, job_id, "completed")

                    self.assertEqual(job["state"], "completed")
                    self.assertTrue(job["has_upload"])
                    self.assertEqual(len(list((root / "staging").glob("*/001-clip.wav"))), 1)

                    removed = await client.delete(f"/v1/jobs/{job_id}/upload")

                    self.assertEqual(removed.status_code, 204)
                    self.assertFalse(list((root / "staging").glob("*")))
                    job = (await client.get(f"/v1/jobs/{job_id}")).json()
                    self.assertFalse(job["has_upload"])
                    again = await client.delete(f"/v1/jobs/{job_id}/upload")
                    self.assertEqual(again.status_code, 404)
                    self.assertEqual(again.json()["error"]["code"], "upload_not_found")
            finally:
                manager.close(cancel=True)

    async def test_upload_cannot_be_deleted_while_job_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FixtureService(block=True)
            app, manager = self._app(root, service)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    submitted = await client.post("/v1/jobs", **_upload("clip.wav"))
                    job_id = submitted.json()["id"]
                    await _wait_until_entered(service)

                    refused = await client.delete(f"/v1/jobs/{job_id}/upload")

                    self.assertEqual(refused.status_code, 409)
                    self.assertEqual(refused.json()["error"]["code"], "job_active")
                    self.assertEqual(len(list((root / "staging").glob("*/001-clip.wav"))), 1)
            finally:
                service.release.set()
                manager.close(cancel=True)

    async def test_local_path_job_has_no_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    submitted = await client.post("/v1/jobs", json={"inputs": [str(source)]})
                    job_id = submitted.json()["id"]
                    job = await _wait_for_state(client, job_id, "completed")

                    self.assertEqual(job["state"], "completed")
                    self.assertFalse(job["has_upload"])
                    removed = await client.delete(f"/v1/jobs/{job_id}/upload")
                    self.assertEqual(removed.status_code, 404)
                    self.assertEqual(removed.json()["error"]["code"], "upload_not_found")
            finally:
                manager.close(cancel=True)

    async def test_rejected_submission_removes_staged_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FixtureService(block=True)
            manager = JobManager(service, queue_size=1, media_workers=1)
            app = create_app(
                service,
                manager,
                ServerSettings(
                    allowed_roots=(root,),
                    staging_dir=root / "staging",
                    max_upload_bytes=1024 * 1024,
                ),
            )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    first = await client.post("/v1/jobs", **_upload("a.wav"))
                    await _wait_until_entered(service)
                    second = await client.post("/v1/jobs", **_upload("b.wav"))
                    third = await client.post("/v1/jobs", **_upload("c.wav"))

                    self.assertEqual(first.status_code, 202)
                    self.assertEqual(second.status_code, 202)
                    self.assertEqual(third.status_code, 429)
                    self.assertEqual(third.json()["error"]["code"], "queue_full")
                    self.assertEqual(len(list((root / "staging").glob("*"))), 2)
            finally:
                service.release.set()
                manager.close(cancel=True)

    async def test_remote_binding_requires_explicit_permission_and_token(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "--allow-remote"):
            ServerSettings(host="0.0.0.0")
        with self.assertRaisesRegex(ConfigurationError, "SPEECHLOOM_API_TOKEN"):
            ServerSettings(host="0.0.0.0", allow_remote=True)


class ServerHelperTests(unittest.TestCase):
    def test_startup_sweep_removes_only_orphaned_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            referenced = staging / ("a" * 32)
            orphaned = staging / ("b" * 32)
            other = staging / "keep-me"
            for path in (referenced, orphaned, other):
                path.mkdir()
            notes = staging / "notes.txt"
            notes.write_text("notes", encoding="utf-8")

            _sweep_staging(staging, frozenset({referenced.resolve()}))

            self.assertTrue(referenced.is_dir())
            self.assertTrue(other.is_dir())
            self.assertTrue(notes.is_file())
            self.assertFalse(orphaned.exists())


if __name__ == "__main__":
    unittest.main()
