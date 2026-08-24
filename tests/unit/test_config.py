from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from parakeet_transcribe.cli import build_parser
from parakeet_transcribe.config import load_settings
from parakeet_transcribe.errors import ConfigurationError


class SettingsTests(unittest.TestCase):
    def test_transcribe_accepts_output_directory(self) -> None:
        args = build_parser().parse_args(
            ["transcribe", "recording.wav", "--output-dir", "custom-output"]
        )

        self.assertEqual(args.output_dir, "custom-output")

    def test_translation_options_are_available_in_cli_and_config(self) -> None:
        args = build_parser().parse_args(
            [
                "transcribe",
                "recording.wav",
                "--translation-model",
                "translate.gguf",
                "--source-language",
                "RU",
                "--translate-to",
                "EN",
            ]
        )
        settings = load_settings(
            cli_values={
                "translation_model": args.translation_model,
                "source_language": args.source_language,
                "translate_to": args.translate_to,
            },
            env={},
        )

        self.assertEqual(settings.translation_model, "translate.gguf")
        self.assertEqual(settings.source_language, "ru")
        self.assertEqual(settings.translate_to, "en")

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.ini"
            config.write_text(
                "[parakeet-transcribe]\n"
                "translation_model = from-config.gguf\n"
                "source_language = ru\n"
                "translate_to = en\n",
                encoding="utf-8",
            )
            configured = load_settings(config_path=config, env={})
        self.assertEqual(configured.translation_model, "from-config.gguf")
        self.assertEqual(configured.source_language, "ru")
        self.assertEqual(configured.translate_to, "en")

    def test_translation_options_must_be_complete(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "configured together"):
            load_settings(cli_values={"translate_to": "en"}, env={})

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

    def test_validates_complete_device_syntax(self) -> None:
        self.assertEqual(
            load_settings(cli_values={"device": "cuda:0"}, env={}).device,
            "cuda:0",
        )
        with self.assertRaisesRegex(ConfigurationError, "expected auto, cpu"):
            load_settings(cli_values={"device": "cuda[:0] # cpu, cuda"}, env={})


if __name__ == "__main__":
    unittest.main()
