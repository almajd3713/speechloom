from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from speechloom.errors import ConfigurationError
from speechloom.registry import Registry
from speechloom.runtime import (
    AppPaths,
    InstallState,
    InstalledArtifact,
    load_install_state,
    save_install_state,
)


class AppPathsTests(unittest.TestCase):
    def test_respects_all_xdg_roots(self) -> None:
        paths = AppPaths.from_environment(
            {
                "XDG_CONFIG_HOME": "/xdg/config",
                "XDG_DATA_HOME": "/xdg/data",
                "XDG_CACHE_HOME": "/xdg/cache",
            },
            home=Path("/ignored"),
        )

        self.assertEqual(paths.config_file, Path("/xdg/config/speechloom/config.ini"))
        self.assertEqual(paths.runtime_dir, Path("/xdg/data/speechloom/runtime"))
        self.assertEqual(paths.models_dir, Path("/xdg/data/speechloom/models"))
        self.assertEqual(paths.state_file, Path("/xdg/data/speechloom/install.json"))
        self.assertEqual(paths.downloads_dir, Path("/xdg/cache/speechloom/downloads"))

    def test_uses_standard_linux_defaults(self) -> None:
        paths = AppPaths.from_environment({}, home=Path("/home/example"))

        self.assertEqual(paths.config_file, Path("/home/example/.config/speechloom/config.ini"))
        self.assertEqual(paths.data_dir, Path("/home/example/.local/share/speechloom"))
        self.assertEqual(paths.cache_dir, Path("/home/example/.cache/speechloom"))


class InstallStateTests(unittest.TestCase):
    def test_round_trips_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "install.json"
            state = InstallState(
                backend="cuda",
                features=("translation",),
                config_path="/config/config.ini",
                runtime=InstalledArtifact(
                    id="nemo-speech-cpp",
                    kind="runtime",
                    path="/runtime/bin/nemo-speech",
                    revision="a" * 40,
                    sha256="b" * 64,
                    size=123,
                ),
                models=(
                    InstalledArtifact(
                        id="parakeet",
                        kind="asr",
                        path="/models/parakeet.gguf",
                    ),
                ),
            )

            save_install_state(path, state)

            self.assertEqual(load_install_state(path), state)
            self.assertFalse(any(path.parent.glob(".install.json.tmp-*")))

    def test_rejects_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "install.json"
            path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationError, "schema"):
                load_install_state(path)


class RegistryTests(unittest.TestCase):
    def test_bundled_registry_is_pinned_and_queryable(self) -> None:
        registry = Registry.load()

        self.assertEqual(len(registry.runtime.revision), 40)
        self.assertIn("cuda", registry.runtime.backends)
        self.assertEqual(registry.model("asr").sha256, "e3880d0a" + "aaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e")
        self.assertEqual(registry.model("translation").kind, "translation")

    def test_registry_rejects_unpinned_revisions(self) -> None:
        registry = Registry.load()
        payload = {
            "schema_version": 1,
            "runtime": {
                **registry.runtime.__dict__,
                "revision": "main",
            },
            "models": [],
        }

        with self.assertRaisesRegex(ConfigurationError, "revision"):
            Registry.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
