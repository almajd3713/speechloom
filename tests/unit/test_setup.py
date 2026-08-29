from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

from speechloom.doctor import DoctorReport
from speechloom.process import CommandResult
from speechloom.registry import Registry
from speechloom.runtime import AppPaths, load_install_state
from speechloom.setup import SetupManager, SetupRequest


class FakeInstaller:
    def __init__(self, asr_content: bytes) -> None:
        self.asr_content = asr_content
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **kwargs) -> CommandResult:
        del kwargs
        command = tuple(str(item) for item in argv)
        self.calls.append(command)
        if command[:2] == ("cmake", "--version"):
            return CommandResult(command, 0, "cmake version 3.31.10\n", "")
        if "bootstrap_runtime.sh" in command[1]:
            prefix = Path(command[command.index("--prefix") + 1])
            executable = prefix / "bin/nemo-speech"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"runtime")
            executable.chmod(0o755)
        elif "download_model.sh" in command[1]:
            destination = Path(command[command.index("--destination") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "parakeet.gguf").write_bytes(self.asr_content)
        return CommandResult(command, 0, "", "")


def _registry(asr_content: bytes) -> Registry:
    registry = Registry.load()
    asr = replace(
        registry.model("asr"),
        filename="parakeet.gguf",
        sha256=hashlib.sha256(asr_content).hexdigest(),
        minimum_free_bytes=0,
    )
    return replace(registry, models=(asr, registry.model("translation")))


def _paths(root: Path) -> AppPaths:
    return AppPaths.from_environment(
        {
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"),
        },
        home=root,
    )


class SetupManagerTests(unittest.TestCase):
    def test_setup_builds_once_and_rerun_only_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "repo/scripts"
            scripts.mkdir(parents=True)
            for name in ("bootstrap_runtime.sh", "download_model.sh"):
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


if __name__ == "__main__":
    unittest.main()
