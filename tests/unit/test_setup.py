from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from speechloom import CancellationController, StageEvent
from speechloom.doctor import DoctorReport
from speechloom.errors import CancellationError, SetupError
from speechloom.process import CommandResult
from speechloom.process import run_command
from speechloom.registry import Registry, RuntimeArchiveSpec
from speechloom.runtime import AppPaths, load_install_state
from speechloom.runtime_archive import RuntimeArchiveMetadata, build_runtime_archive
from speechloom.setup import SetupManager, SetupRequest


class FakeInstaller:
    def __init__(
        self,
        asr_content: bytes,
        diar_content: bytes = b"diar",
        artifacts: dict[str, bytes] | None = None,
    ) -> None:
        self.asr_content = asr_content
        self.diar_content = diar_content
        self.artifacts = artifacts or {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **kwargs) -> CommandResult:
        del kwargs
        command = tuple(str(item) for item in argv)
        self.calls.append(command)
        if command == ("nvidia-smi",):
            return CommandResult(command, 1, "", "unavailable")
        if command[:2] == ("cmake", "--version"):
            return CommandResult(command, 0, "cmake version 3.31.10\n", "")
        if "bootstrap_runtime.sh" in command[1]:
            prefix = Path(command[command.index("--prefix") + 1])
            executable = prefix / "bin/nemo-speech"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"runtime")
            executable.chmod(0o755)
        elif "download_artifact.sh" in command[1]:
            destination = Path(command[command.index("--destination") + 1])
            filename = command[command.index("--filename") + 1]
            destination.mkdir(parents=True, exist_ok=True)
            content = self.artifacts.get(filename)
            if content is None:
                content = self.asr_content if filename == "parakeet.gguf" else self.diar_content
            (destination / filename).write_bytes(content)
        return CommandResult(command, 0, "", "")


def _registry(asr_content: bytes, diar_content: bytes = b"diar") -> Registry:
    registry = Registry.load()
    asr = replace(
        registry.model("asr"),
        filename="parakeet.gguf",
        sha256=hashlib.sha256(asr_content).hexdigest(),
        minimum_free_bytes=0,
    )
    diar = replace(
        registry.model("diarization"),
        filename="sortformer.gguf",
        sha256=hashlib.sha256(diar_content).hexdigest(),
        minimum_free_bytes=0,
    )
    return replace(
        registry,
        runtime=replace(registry.runtime, archives=()),
        models=(asr, diar, registry.model("translation")),
    )


def _paths(root: Path) -> AppPaths:
    return AppPaths.from_environment(
        {
            "SPEECHLOOM_CONFIG_HOME": str(root / "config"),
            "SPEECHLOOM_DATA_HOME": str(root / "data"),
            "SPEECHLOOM_CACHE_HOME": str(root / "cache"),
        },
        home=root,
    )


class SetupManagerTests(unittest.TestCase):
    @patch("speechloom.setup._command_available", return_value=True)
    def test_auto_backend_requires_a_working_gpu_probe(self, available) -> None:
        del available

        def blocked_gpu(argv, **kwargs):
            del kwargs
            command = tuple(str(item) for item in argv)
            return CommandResult(command, 1, "", "GPU access blocked")

        manager = SetupManager(runner=blocked_gpu, repository_root=Path("/missing"))

        self.assertEqual(manager._resolve_backend("auto", None), "cpu")

    def test_setup_builds_once_and_rerun_only_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "repo/scripts"
            scripts.mkdir(parents=True)
            for name in ("bootstrap_runtime.sh", "download_artifact.sh"):
                (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")
            installer = FakeInstaller(b"asr")
            manager = SetupManager(
                paths=_paths(root),
                registry=_registry(b"asr"),
                runner=installer,
                doctor=lambda *args, **kwargs: DoctorReport(True, ()),
                repository_root=root / "repo",
            )

            first = manager.setup(SetupRequest(backend="cpu"))
            second = manager.setup(SetupRequest(backend="cpu"))

            scripts_run = [call for call in installer.calls if call[0] == "bash"]
            self.assertEqual(len(scripts_run), 2)
            self.assertEqual(
                first.actions,
                ("built cpu runtime", "installed ASR model", "created default configuration"),
            )
            self.assertEqual(second.actions, ())
            self.assertTrue(manager.status().ready)
            self.assertIsNotNone(load_install_state(manager.paths.state_file))

    def test_setup_prefers_a_verified_compatible_runtime_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "repo/scripts"
            scripts.mkdir(parents=True)
            for name in ("bootstrap_runtime.sh", "download_artifact.sh"):
                (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")
            registry = _registry(b"asr")
            prefix = root / "archive-prefix"
            executable = prefix / registry.runtime.executable
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"prebuilt-runtime")
            executable.chmod(0o755)
            license_file = prefix / "share/licenses/nemo-speech/LICENSE"
            license_file.parent.mkdir(parents=True)
            license_file.write_text("fixture license\n", encoding="utf-8")
            filename = "speechloom-runtime-fixture-cpu-linux-x86_64.tar.gz"
            archive_path = root / filename
            archive_sha = build_runtime_archive(
                prefix,
                archive_path,
                RuntimeArchiveMetadata(
                    backend="cpu",
                    system="linux",
                    architecture="x86_64",
                    revision=registry.runtime.revision,
                    features=("asr",),
                    executable=registry.runtime.executable,
                ),
            )
            archive = RuntimeArchiveSpec(
                backend="cpu",
                system="linux",
                architecture="x86_64",
                filename=filename,
                url=f"https://example.invalid/{filename}",
                sha256=archive_sha,
                features=("asr",),
                minimum_free_bytes=0,
            )
            registry = replace(
                registry,
                runtime=replace(registry.runtime, archives=(archive,)),
            )
            installer = FakeInstaller(
                b"asr",
                artifacts={filename: archive_path.read_bytes()},
            )
            manager = SetupManager(
                paths=_paths(root),
                registry=registry,
                runner=installer,
                doctor=lambda *args, **kwargs: DoctorReport(True, ()),
                repository_root=root / "repo",
            )

            first = manager.setup(SetupRequest(backend="cpu"))
            second = manager.setup(SetupRequest(backend="cpu"))

            self.assertIn("installed cpu prebuilt runtime", first.actions)
            self.assertEqual(second.actions, ())
            self.assertEqual(first.state.runtime.source, "prebuilt")
            self.assertEqual(first.state.runtime.download_url, archive.url)
            self.assertEqual(first.state.runtime.download_sha256, archive.sha256)
            self.assertTrue(Path(first.state.runtime.path).is_file())
            self.assertFalse(
                any("bootstrap_runtime.sh" in part for call in installer.calls for part in call)
            )
            self.assertFalse((manager.paths.downloads_dir / filename).exists())

    def test_low_disk_space_stops_before_runtime_or_model_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _paths(root)
            paths.create()
            installer = FakeInstaller(b"asr")
            registry = _registry(b"asr")
            manager = SetupManager(
                paths=paths,
                registry=registry,
                runner=installer,
                repository_root=root / "repo",
            )
            archive = RuntimeArchiveSpec(
                backend="cpu",
                system="linux",
                architecture="x86_64",
                filename="runtime.tar.gz",
                url="https://example.invalid/runtime.tar.gz",
                sha256="a" * 64,
                features=("asr",),
                minimum_free_bytes=11,
            )

            with patch(
                "speechloom.setup.shutil.disk_usage",
                return_value=SimpleNamespace(free=10),
            ):
                with self.assertRaisesRegex(SetupError, "Not enough free space"):
                    manager._install_runtime_archive(archive)
                with self.assertRaisesRegex(SetupError, "Not enough free space"):
                    manager._install_model(
                        replace(registry.model("asr"), minimum_free_bytes=11)
                    )

            self.assertFalse(any(call[0] == "bash" for call in installer.calls))

    def test_setup_emits_structured_stage_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "repo/scripts"
            scripts.mkdir(parents=True)
            for name in ("bootstrap_runtime.sh", "download_artifact.sh"):
                (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")
            events: list[StageEvent] = []
            manager = SetupManager(
                paths=_paths(root),
                registry=_registry(b"asr"),
                runner=FakeInstaller(b"asr"),
                doctor=lambda *args, **kwargs: DoctorReport(True, ()),
                repository_root=root / "repo",
            )

            manager.setup(SetupRequest(backend="cpu"), on_event=events.append)

            self.assertEqual(
                list(dict.fromkeys(event.stage for event in events)),
                [
                    "validating",
                    "installing_runtime",
                    "installing_models",
                    "configuring",
                    "completed",
                ],
            )
            self.assertTrue(all(event.timestamp for event in events))

    def test_setup_cancellation_stops_before_the_next_stage_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "repo/scripts"
            scripts.mkdir(parents=True)
            for name in ("bootstrap_runtime.sh", "download_artifact.sh"):
                (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")
            installer = FakeInstaller(b"asr")
            controller = CancellationController()
            events: list[StageEvent] = []
            manager = SetupManager(
                paths=_paths(root),
                registry=_registry(b"asr"),
                runner=installer,
                doctor=lambda *args, **kwargs: DoctorReport(True, ()),
                repository_root=root / "repo",
            )

            def cancel_runtime(event: StageEvent) -> None:
                events.append(event)
                if event.stage == "installing_runtime":
                    controller.cancel()

            with self.assertRaises(CancellationError):
                manager.setup(
                    SetupRequest(backend="cpu"),
                    on_event=cancel_runtime,
                    cancellation=controller,
                )

            self.assertEqual(events[-1].stage, "cancelled")
            self.assertFalse(any(call[0] == "bash" for call in installer.calls))

    def test_imports_repository_runtime_without_moving_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"
            executable = repository / ".runtime/nemo-speech/bin/nemo-speech"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"runtime")
            executable.chmod(0o755)
            model = repository / ".runtime/models/parakeet.gguf"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"legacy-asr")
            installer = FakeInstaller(b"legacy-asr")
            manager = SetupManager(
                paths=_paths(root),
                registry=_registry(b"legacy-asr"),
                runner=installer,
                doctor=lambda *args, **kwargs: DoctorReport(True, ()),
                repository_root=repository,
            )

            result = manager.setup(SetupRequest(backend="cpu"))

            self.assertEqual(result.state.runtime.path, str(executable.resolve()))
            self.assertEqual(result.state.model("asr").path, str(model.resolve()))
            self.assertTrue(executable.exists())
            self.assertTrue(model.exists())
            self.assertFalse(any(call[0] == "bash" for call in installer.calls))

    def test_diarization_feature_installs_pinned_optional_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "repo/scripts"
            scripts.mkdir(parents=True)
            for name in ("bootstrap_runtime.sh", "download_artifact.sh"):
                (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")
            installer = FakeInstaller(b"asr", b"diar")
            manager = SetupManager(
                paths=_paths(root),
                registry=_registry(b"asr", b"diar"),
                runner=installer,
                doctor=lambda *args, **kwargs: DoctorReport(True, ()),
                repository_root=root / "repo",
            )

            result = manager.setup(
                SetupRequest(backend="cpu", features=("diarization",))
            )

            diar = result.state.model("diarization")
            self.assertIsNotNone(diar)
            assert diar is not None
            self.assertEqual(diar.license, "CC-BY-4.0")
            self.assertEqual(Path(diar.path).read_bytes(), b"diar")
            self.assertIn("installed diarization model", result.actions)

    def test_clean_only_removes_requested_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _paths(root)
            paths.downloads_dir.mkdir(parents=True)
            paths.build_tools_dir.mkdir(parents=True)
            (paths.downloads_dir / "partial").write_bytes(b"partial")
            (paths.build_tools_dir / "cmake").write_bytes(b"tool")
            manager = SetupManager(paths=paths, repository_root=root)

            removed = manager.clean(downloads=True)

            self.assertEqual(removed, (paths.downloads_dir,))
            self.assertFalse(paths.downloads_dir.exists())
            self.assertTrue(paths.build_tools_dir.exists())


class DownloadArtifactScriptTests(unittest.TestCase):
    def test_verifies_before_publish_and_removes_invalid_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "#!/bin/sh\n"
                "while [ $# -gt 0 ]; do\n"
                "  if [ \"$1\" = \"--output\" ]; then output=$2; shift 2; else shift; fi\n"
                "done\n"
                "printf fixture-model > \"$output\"\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            script = Path(__file__).resolve().parents[2] / "scripts/download_artifact.sh"
            destination = root / "models"
            cache = root / "cache"
            digest = hashlib.sha256(b"fixture-model").hexdigest()
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }
            base_argv = [
                "bash",
                str(script),
                "--destination",
                str(destination),
                "--download-cache",
                str(cache),
                "--url",
                "https://models.example/fixture.gguf",
                "--filename",
                "fixture.gguf",
                "--sha256",
            ]

            installed = run_command([*base_argv, digest], env=environment)
            (destination / "fixture.gguf").unlink()
            rejected = run_command(
                [*base_argv, "0" * 64],
                env=environment,
                check=False,
            )

            self.assertEqual(installed.returncode, 0)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse((destination / "fixture.gguf").exists())
            self.assertFalse((cache / "fixture.gguf.part").exists())

    def test_preserves_partial_download_after_network_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "#!/bin/sh\n"
                "while [ $# -gt 0 ]; do\n"
                "  if [ \"$1\" = \"--output\" ]; then output=$2; shift 2; else shift; fi\n"
                "done\n"
                "printf partial-download > \"$output\"\n"
                "exit 18\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            script = Path(__file__).resolve().parents[2] / "scripts/download_artifact.sh"
            destination = root / "models"
            cache = root / "cache"
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            interrupted = run_command(
                [
                    "bash",
                    str(script),
                    "--destination",
                    str(destination),
                    "--download-cache",
                    str(cache),
                    "--url",
                    "https://models.example/fixture.gguf",
                    "--filename",
                    "fixture.gguf",
                    "--sha256",
                    "0" * 64,
                ],
                env=environment,
                check=False,
            )

            self.assertNotEqual(interrupted.returncode, 0)
            self.assertFalse((destination / "fixture.gguf").exists())
            self.assertEqual(
                (cache / "fixture.gguf.part").read_bytes(), b"partial-download"
            )


if __name__ == "__main__":
    unittest.main()
