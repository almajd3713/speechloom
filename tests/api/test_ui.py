from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx2 as httpx

from speechloom.api.server import ServerSettings, create_app
from speechloom.cli import _open_when_ready, main
from speechloom.job_manager import JobManager

from .test_api import FixtureService


def _client(root: Path, host: str = "127.0.0.1"):
    service = FixtureService()
    manager = JobManager(service, queue_size=2, media_workers=1)
    extra = {"allow_remote": True, "bearer_token": "secret"} if host != "127.0.0.1" else {}
    settings = ServerSettings(host=host, staging_dir=root / "staging", **extra)
    app = create_app(service, manager, settings)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{host}:8765"
    )
    return client, manager


class UiServingTests(unittest.IsolatedAsyncioTestCase):
    async def test_interface_is_served_at_ui_with_security_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager = _client(Path(directory))
            try:
                for path in ("/ui/", "/ui/index.html"):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 200, path)
                    self.assertIn("Speechloom", response.text)
                    self.assertTrue(response.headers["content-type"].startswith("text/html"))
                    csp = response.headers["content-security-policy"]
                    self.assertIn("default-src 'self'", csp)
                    self.assertIn("img-src 'self' data:", csp)
                    self.assertIn("media-src 'self' blob:", csp)
                    self.assertIn("frame-ancestors 'none'", csp)
                    self.assertEqual(response.headers["x-frame-options"], "DENY")
                    self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                    self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                    self.assertEqual(response.headers["cache-control"], "no-cache")
                for path in ("/", "/ui"):
                    redirect = await client.get(path)
                    self.assertEqual(redirect.status_code, 307, path)
                    self.assertEqual(redirect.headers["location"], "/ui/")
            finally:
                await client.aclose()
                manager.close(cancel=True)

    async def test_paths_outside_the_web_directory_are_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager = _client(Path(directory))
            try:
                for path in (
                    "/ui/..%2Fcli.py",
                    "/ui/%2e%2e/%2e%2e/pyproject.toml",
                    "/ui/..%2F..%2F..%2F..%2Fpyproject.toml",
                    "/ui/missing.js",
                    "/ui/%00",
                    "/ui//etc/passwd",
                ):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 404, path)
                    self.assertEqual(response.json()["error"]["code"], "not_found", path)
            finally:
                await client.aclose()
                manager.close(cancel=True)

    async def test_interface_does_not_exist_beyond_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, manager = _client(Path(directory), host="0.0.0.0")
            try:
                for path in ("/", "/ui", "/ui/", "/ui/index.html"):
                    self.assertEqual((await client.get(path)).status_code, 404, path)
            finally:
                await client.aclose()
                manager.close(cancel=True)


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args) -> None:
        pass


class GuiCommandTests(unittest.TestCase):
    def test_port_in_use_fails_before_the_server_starts(self) -> None:
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]
            stderr = io.StringIO()
            with patch("speechloom.api.server.run_server") as run:
                with redirect_stderr(stderr):
                    code = main(["gui", "--port", str(port), "--no-browser"])

        self.assertEqual(code, 2)
        self.assertIn("--port", stderr.getvalue())
        run.assert_not_called()

    def test_gui_serves_on_loopback_with_the_given_limits(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            with patch("speechloom.api.server.run_server") as run:
                code = main(
                    [
                        "gui",
                        "--port",
                        str(port),
                        "--allow-root",
                        directory,
                        "--max-upload-mb",
                        "5",
                        "--no-browser",
                    ]
                )

        settings = run.call_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(
            (settings.host, settings.port, settings.max_upload_bytes, settings.bearer_token),
            ("127.0.0.1", port, 5 * 1024 * 1024, None),
        )
        self.assertEqual(settings.allowed_roots, (Path(directory),))

    def test_missing_api_extra_gives_the_install_hint(self) -> None:
        stderr = io.StringIO()
        with patch.dict("sys.modules", {"speechloom.api": None}):
            with redirect_stderr(stderr):
                code = main(["gui", "--no-browser"])

        self.assertEqual(code, 3)
        self.assertIn("speechloom[api]", stderr.getvalue())

    def test_browser_opens_once_health_answers_and_falls_back_to_a_hint(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        health = f"http://127.0.0.1:{server.server_address[1]}/v1/health"
        opened: list[str] = []
        try:
            for opener, expect_hint in (
                (lambda url: opened.append(url) or True, False),
                (lambda url: False, True),
                (lambda url: (_ for _ in ()).throw(RuntimeError("no handler")), True),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    _open_when_ready("http://x/ui/", health, False, opener=opener)
                self.assertEqual("Open http://x/ui/ in your browser." in output.getvalue(), expect_hint)
            skipped = io.StringIO()
            with redirect_stdout(skipped):
                _open_when_ready(
                    "http://x/ui/", health, True, opener=lambda url: opened.append("no") or True
                )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(opened, ["http://x/ui/"])
        self.assertNotIn("Open http://x", skipped.getvalue())

    def test_waiting_gives_up_when_the_server_never_answers(self) -> None:
        opened: list[str] = []
        _open_when_ready(
            "http://x/ui/",
            "http://127.0.0.1:9/v1/health",
            False,
            opener=lambda url: opened.append(url) or True,
            timeout=0.3,
        )

        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()
