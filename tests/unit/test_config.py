from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from speechloom.cli import build_parser
from speechloom.config import default_config_path, load_managed_settings, load_settings
from speechloom.errors import ConfigurationError
from speechloom.runtime import InstallState, InstalledArtifact, save_install_state


class SettingsTests(unittest.TestCase):
    def test_speechloom_public_names(self) -> None:
        self.assertEqual(build_parser().prog, "speechloom")
        self.assertEqual(
            default_config_path({"XDG_CONFIG_HOME": "/tmp/config"}),
            Path("/tmp/config/speechloom/config.ini"),
        )

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
                "[speechloom]\n"
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
                "[speechloom]\nmodel = from-file.gguf\nworkers = 2\ndevice = cpu\nshared_model = false\n",
                encoding="utf-8",
            )
            settings = load_settings(
                config_path=config,
                env={"SPEECHLOOM_MODEL": "from-env.gguf", "SPEECHLOOM_WORKERS": "3"},
                cli_values={"model": "from-cli.gguf", "formats": "json,txt"},
            )
        self.assertEqual(settings.model, "from-cli.gguf")
        self.assertEqual(settings.workers, 3)
        self.assertEqual(settings.device, "cpu")
        self.assertEqual(settings.formats, ("json", "txt"))
        self.assertFalse(settings.shared_model)

    def test_rejects_invalid_boolean(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings(env={"SPEECHLOOM_KEEP_AUDIO": "occasionally"})

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

    def test_managed_state_fills_only_unset_runtime_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_CACHE_HOME": str(root / "cache"),
            }
            state_path = root / "data/speechloom/install.json"
            save_install_state(
                state_path,
                InstallState(
                    backend="cuda",
                    features=("translation",),
                    config_path=str(root / "config/speechloom/config.ini"),
                    runtime=InstalledArtifact(
                        "runtime", "runtime", "/managed/nemo-speech"
                    ),
                    models=(
                        InstalledArtifact("asr", "asr", "/managed/asr.gguf"),
                        InstalledArtifact(
                            "translation", "translation", "/managed/translation.gguf"
                        ),
                    ),
                ),
            )

            managed = load_managed_settings(env=env)
            overridden = load_managed_settings(
                env=env,
                cli_values={
                    "nemo_speech": "/custom/nemo-speech",
                    "model": "/custom/asr.gguf",
                    "device": "cpu",
                },
            )

        self.assertEqual(managed.nemo_speech, "/managed/nemo-speech")
        self.assertEqual(managed.model, "/managed/asr.gguf")
        self.assertEqual(managed.translation_model, "/managed/translation.gguf")
        self.assertEqual(managed.device, "cuda")
        self.assertEqual(overridden.nemo_speech, "/custom/nemo-speech")
        self.assertEqual(overridden.model, "/custom/asr.gguf")
        self.assertEqual(overridden.device, "cpu")


if __name__ == "__main__":
    unittest.main()
