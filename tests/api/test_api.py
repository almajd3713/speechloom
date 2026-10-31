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
from speechloom.api.server import (
    ServerSettings,
    _format_sse_event,
    _sweep_staging,
    create_app,
    resolve_output_dir,
)
from speechloom.artifacts import sha256_file
from speechloom.contracts import JobDetails, SegmentEdit, ServiceCapabilities, StageEvent
from speechloom.errors import ArtifactConflictError, ConfigurationError
from speechloom.job_manager import JobManager
from speechloom.jobs import JobResult
from speechloom.languages import ASR_LANGUAGES, TRANSLATION_LANGUAGES


class FixtureService:
    def __init__(
        self, *, block: bool = False, conflict: bool = False, kept_audio: bool = False
    ) -> None:
        self.block = block
        self.conflict = conflict
        self.kept_audio = kept_audio
        self.saved_edits: list[tuple[Path, tuple[SegmentEdit, ...], bool]] = []
        self.cleared: list[Path] = []
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
        if self.kept_audio:
            audio = job_dir / ".work" / "audio.wav"
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"RIFFfixture")
            manifest["normalized_audio"] = {"path": str(audio), "passthrough": False}
        (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return [JobResult(str(source), str(job_dir), "completed")]

    def inspect(self, job) -> JobDetails:
        path = Path(job)
        manifest = path / "manifest.json" if path.is_dir() else path
        return JobDetails.from_manifest(json.loads(manifest.read_text(encoding="utf-8")))

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            diarization=False,
            translation=True,
            formats=("json", "txt", "srt", "vtt"),
            asr_languages=ASR_LANGUAGES,
            translation_languages=TRANSLATION_LANGUAGES,
        )

    def save_edits(self, job, edits, *, merge=False) -> JobDetails:
        if self.conflict:
            raise ArtifactConflictError("Only completed jobs can be edited: fixture")
        job_dir = Path(job)
        self.saved_edits.append((job_dir, tuple(edits), merge))
        edited = job_dir / "transcript.edited.txt"
        edited.write_text(f"{edits[0].text}\n", encoding="utf-8")
        manifest_path = job_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"]["edited.txt"] = {
            "path": edited.name,
            "size": edited.stat().st_size,
            "sha256": sha256_file(edited),
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return self.inspect(job_dir)

    def clear_edits(self, job) -> JobDetails:
        job_dir = Path(job)
        self.cleared.append(job_dir)
        (job_dir / "transcript.edited.txt").unlink(missing_ok=True)
        manifest_path = job_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"].pop("edited.txt", None)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return self.inspect(job_dir)


def _tree_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


async def _complete_json_job(client: httpx.AsyncClient, source: Path, **options) -> str:
    submitted = await client.post(
        "/v1/jobs", json={"inputs": [str(source)], "formats": ["txt"], **options}
    )
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["id"]
    job = await _wait_for_state(client, job_id, "completed")
    assert job["state"] == "completed", job
    return job_id


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

    async def test_worker_count_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    too_many = await client.post(
                        "/v1/jobs", json={"inputs": [str(source)], "workers": 9}
                    )
                    allowed = await client.post(
                        "/v1/jobs", json={"inputs": [str(source)], "workers": 8}
                    )

                    self.assertEqual(too_many.status_code, 422)
                    self.assertEqual(too_many.json()["error"]["code"], "validation_error")
                    self.assertEqual(allowed.status_code, 202)
            finally:
                manager.close(cancel=True)

    async def test_capabilities_report_features_and_languages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app, manager = self._app(Path(directory), FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    response = await client.get("/v1/capabilities")
                    body = response.json()

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(body["features"], {"diarization": False, "translation": True})
                    self.assertEqual(body["formats"], ["json", "txt", "srt", "vtt"])
                    self.assertEqual(len(body["asr_languages"]), 25)
                    self.assertIn("ar", body["translation_languages"])
                    self.assertEqual(body["api_version"], "1")
            finally:
                manager.close(cancel=True)

    async def test_edits_endpoint_saves_and_lists_edited_artifacts(self) -> None:
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
                    job_id = await _complete_json_job(client, source)

                    response = await client.put(
                        f"/v1/jobs/{job_id}/edits",
                        json={"segments": [{"index": 0, "text": "Hello"}]},
                    )

                    self.assertEqual(response.status_code, 200)
                    self.assertIn("edited.txt", [item["name"] for item in response.json()["items"]])
                    job_dir = root / "output" / "job-sample"
                    self.assertEqual(
                        service.saved_edits, [(job_dir, (SegmentEdit(0, "Hello"),), False)]
                    )
            finally:
                manager.close(cancel=True)

    async def test_edits_patch_merges_and_delete_clears(self) -> None:
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
                    job_id = await _complete_json_job(client, source)
                    job_dir = root / "output" / "job-sample"

                    patched = await client.patch(
                        f"/v1/jobs/{job_id}/edits",
                        json={"segments": [{"index": 0, "text": None}]},
                    )
                    self.assertEqual(patched.status_code, 200)
                    self.assertEqual(
                        service.saved_edits[-1], (job_dir, (SegmentEdit(0, None),), True)
                    )

                    cleared = await client.delete(f"/v1/jobs/{job_id}/edits")
                    self.assertEqual(cleared.status_code, 204)
                    self.assertEqual(cleared.content, b"")
                    self.assertEqual(service.cleared, [job_dir])
                    artifacts = (await client.get(f"/v1/jobs/{job_id}/artifacts")).json()["items"]
                    self.assertNotIn("edited.txt", [item["name"] for item in artifacts])
                    self.assertEqual(
                        (await client.delete(f"/v1/jobs/{job_id}/edits")).status_code, 204
                    )
            finally:
                manager.close(cancel=True)

    async def test_edits_endpoint_rejects_active_jobs_and_bad_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            service = FixtureService(block=True)
            app, manager = self._app(root, service)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    submitted = await client.post(
                        "/v1/jobs", json={"inputs": [str(source)], "formats": ["txt"]}
                    )
                    job_id = submitted.json()["id"]
                    await _wait_until_entered(service)
                    body = {"segments": [{"index": 0, "text": "Hi"}]}
                    for response in (
                        await client.put(f"/v1/jobs/{job_id}/edits", json=body),
                        await client.patch(f"/v1/jobs/{job_id}/edits", json=body),
                        await client.delete(f"/v1/jobs/{job_id}/edits"),
                    ):
                        self.assertEqual(response.status_code, 409)
                        self.assertEqual(response.json()["error"]["code"], "job_not_completed")

                    service.release.set()
                    await _wait_for_state(client, job_id, "completed")
                    missing = await client.put(
                        f"/v1/jobs/{job_id}/edits", json={"result": 2, **body}
                    )
                    self.assertEqual(missing.status_code, 404)
                    self.assertEqual(missing.json()["error"]["code"], "result_not_found")
                    empty = await client.put(f"/v1/jobs/{job_id}/edits", json={"segments": []})
                    self.assertEqual(empty.status_code, 422)
                    self.assertEqual(empty.json()["error"]["code"], "validation_error")
                    blank = await client.put(
                        f"/v1/jobs/{job_id}/edits",
                        json={"segments": [{"index": 0, "text": "   "}]},
                    )
                    self.assertEqual(blank.status_code, 422)
                    self.assertEqual(blank.json()["error"]["code"], "invalid_job")
            finally:
                service.release.set()
                manager.close(cancel=True)

    async def test_edits_endpoint_maps_artifact_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            app, manager = self._app(root, FixtureService(conflict=True))
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    job_id = await _complete_json_job(client, source)

                    response = await client.put(
                        f"/v1/jobs/{job_id}/edits",
                        json={"segments": [{"index": 0, "text": "Hi"}]},
                    )

                    self.assertEqual(response.status_code, 409)
                    error = response.json()["error"]
                    self.assertEqual(error["code"], "artifact_conflict")
                    self.assertEqual(
                        error["message"],
                        "The job's artifacts cannot be changed in their current state",
                    )
            finally:
                manager.close(cancel=True)

    async def test_cors_allows_put_for_configured_origins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FixtureService()
            manager = JobManager(service, queue_size=4, media_workers=1)
            app = create_app(
                service,
                manager,
                ServerSettings(
                    allowed_roots=(root,),
                    allowed_origins=("http://localhost:5173",),
                    staging_dir=root / "staging",
                ),
            )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    response = await client.options(
                        "/v1/jobs/x/edits",
                        headers={
                            "Origin": "http://localhost:5173",
                            "Access-Control-Request-Method": "PUT",
                        },
                    )

                    self.assertEqual(response.status_code, 200)
                    allowed = response.headers["access-control-allow-methods"]
                    self.assertIn("PUT", allowed)
                    self.assertIn("PATCH", allowed)
            finally:
                manager.close(cancel=True)

    async def test_media_serves_the_stored_upload_with_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    submitted = await client.post(
                        "/v1/jobs",
                        files=[("files", ("clip.wav", b"0123456789", "audio/wav"))],
                        data={"options": json.dumps({"formats": ["txt"]})},
                    )
                    job_id = submitted.json()["id"]
                    await _wait_for_state(client, job_id, "completed")

                    full = await client.get(f"/v1/jobs/{job_id}/media")
                    partial = await client.get(
                        f"/v1/jobs/{job_id}/media", headers={"Range": "bytes=0-3"}
                    )

                    self.assertEqual((full.status_code, full.content), (200, b"0123456789"))
                    self.assertEqual((partial.status_code, partial.content), (206, b"0123"))
            finally:
                manager.close(cancel=True)

    async def test_media_serves_local_sources_inside_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    job_id = await _complete_json_job(client, source)

                    response = await client.get(f"/v1/jobs/{job_id}/media")

                    self.assertEqual((response.status_code, response.content), (200, b"media"))
            finally:
                manager.close(cancel=True)

    async def test_media_is_unavailable_without_stored_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    job_id = await _complete_json_job(client, source)
                    source.unlink()

                    gone = await client.get(f"/v1/jobs/{job_id}/media")
                    other = await client.get(f"/v1/jobs/{job_id}/media?result=2")

                    self.assertEqual(gone.status_code, 404)
                    self.assertEqual(gone.json()["error"]["code"], "media_unavailable")
                    self.assertEqual(other.status_code, 404)
                    self.assertEqual(other.json()["error"]["code"], "result_not_found")
            finally:
                manager.close(cancel=True)

    async def test_media_falls_back_to_kept_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.write_bytes(b"media")
            app, manager = self._app(root, FixtureService(kept_audio=True))
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    job_id = await _complete_json_job(client, source)
                    source.unlink()

                    response = await client.get(f"/v1/jobs/{job_id}/media")

                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.headers["content-type"].startswith("audio/wav"))
                    self.assertEqual(response.content, b"RIFFfixture")
            finally:
                manager.close(cancel=True)

    async def test_storage_reports_totals_and_shares(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.wav").write_bytes(b"a")
            (root / "b.wav").write_bytes(b"bb")
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    await _complete_json_job(client, root / "a.wav")
                    await _complete_json_job(client, root / "b.wav")

                    response = await client.get("/v1/storage")
                    body = response.json()

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(body["jobs"]), 2)
                    for entry in body["jobs"]:
                        directory_name = f"job-{entry['inputs'][0].split('.')[0]}"
                        self.assertEqual(
                            entry["output_bytes"], _tree_bytes(root / "output" / directory_name)
                        )
                        self.assertEqual(entry["upload_bytes"], 0)
                    self.assertEqual(
                        body["total_bytes"], sum(entry["total_bytes"] for entry in body["jobs"])
                    )
                    self.assertAlmostEqual(sum(entry["share"] for entry in body["jobs"]), 1.0, delta=0.0002)
                    totals = [entry["total_bytes"] for entry in body["jobs"]]
                    self.assertEqual(totals, sorted(totals, reverse=True))
                    self.assertGreater(body["free_bytes"], 0)
            finally:
                manager.close(cancel=True)

    async def test_storage_counts_shared_directories_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.wav"
            source.write_bytes(b"a")
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    older = await _complete_json_job(client, source, formats=["txt"])
                    newer = await _complete_json_job(client, source, formats=["json"])
                    job_dir = root / "output" / "job-a"

                    body = (await client.get("/v1/storage")).json()
                    by_id = {entry["id"]: entry for entry in body["jobs"]}

                    self.assertEqual(by_id[newer]["output_bytes"], _tree_bytes(job_dir))
                    self.assertEqual(by_id[older]["output_bytes"], 0)
                    self.assertEqual(body["output_bytes"], _tree_bytes(job_dir))
            finally:
                manager.close(cancel=True)

    async def test_storage_report_is_empty_without_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app, manager = self._app(Path(directory), FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    body = (await client.get("/v1/storage")).json()

                    self.assertEqual(
                        (body["total_bytes"], body["upload_bytes"], body["output_bytes"]), (0, 0, 0)
                    )
                    self.assertEqual(body["jobs"], [])
            finally:
                manager.close(cancel=True)

    async def test_storage_counts_uploads_until_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, manager = self._app(root, FixtureService())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
                ) as client:
                    submitted = await client.post(
                        "/v1/jobs",
                        files=[("files", ("c.wav", b"x" * 1000, "audio/wav"))],
                        data={"options": json.dumps({"formats": ["txt"]})},
                    )
                    job_id = submitted.json()["id"]
                    await _wait_for_state(client, job_id, "completed")

                    entry = (await client.get("/v1/storage")).json()["jobs"][0]
                    self.assertGreaterEqual(entry["upload_bytes"], 1000)
                    self.assertEqual(entry["output_bytes"], 0)

                    self.assertEqual((await client.delete(f"/v1/jobs/{job_id}/upload")).status_code, 204)
                    entry = (await client.get("/v1/storage")).json()["jobs"][0]
                    self.assertEqual(entry["total_bytes"], 0)
            finally:
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

    def test_resolve_output_dir_uses_data_directory_for_relative_paths(self) -> None:
        data = Path("/data")

        self.assertEqual(resolve_output_dir("transcripts", data), Path("/data/transcripts"))
        self.assertEqual(resolve_output_dir("/srv/out", data), Path("/srv/out"))
        self.assertEqual(resolve_output_dir("~/out", data), Path.home() / "out")


if __name__ == "__main__":
    unittest.main()
