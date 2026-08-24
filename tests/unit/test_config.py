from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from parakeet_transcribe.config import load_settings
from parakeet_transcribe.errors import ConfigurationError


class SettingsTests(unittest.TestCase):
    def test_precedence_cli_then_environment_then_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.ini"
            config.write_text(
                "[parakeet-transcribe]\nmodel = from-file.gguf\nworkers = 2\ndevice = cpu\nshared_model = false\n",
                encoding="utf-8",
            )
            settings = load_settings(
                config_path=config,
                env={"PARAKEET_TRANSCRIBE_MODEL": "from-env.gguf", "PARAKEET_TRANSCRIBE_WORKERS": "3"},
                cli_values={"model": "from-cli.gguf", "formats": "json,txt"},
            )
        self.assertEqual(settings.model, "from-cli.gguf")
        self.assertEqual(settings.workers, 3)
        self.assertEqual(settings.device, "cpu")
        self.assertEqual(settings.formats, ("json", "txt"))
        self.assertFalse(settings.shared_model)

    def test_rejects_invalid_boolean(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings(env={"PARAKEET_TRANSCRIBE_KEEP_AUDIO": "occasionally"})

    def test_rejects_unknown_format(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings(cli_values={"formats": "json,pdf"}, env={})


if __name__ == "__main__":
    unittest.main()
