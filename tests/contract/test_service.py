from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import speechloom
from speechloom import JobDetails, Settings, TranscriptionRequest, TranscriptionService
from speechloom.errors import ConfigurationError
from speechloom.process import CommandResult
from tests.contract.test_pipeline import FakeTools


class TranscriptionServiceTests(unittest.TestCase):
    def test_service_loads_an_explicit_config_without_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recording.mp4"
            model = root / "model.gguf"
            config = root / "config.ini"
            source.write_bytes(b"media")
            model.write_bytes(b"model")
            config.write_text(
                "[speechloom]\n"
                f"model = {model}\n"
                f"output_dir = {root / 'output'}\n",
                encoding="utf-8",
            )
            tools = FakeTools()

            service = TranscriptionService.from_default_config(
                config_path=config,
                env={},
                runner=tools,
            )
            result = service.transcribe(TranscriptionRequest(inputs=(source,)))[0]

            self.assertIsNone(result.error)
            self.assertEqual(tools.inference_calls, 1)

    def test_python_caller_can_transcribe_and_inspect_without_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Видео.mp4"
            model = root / "model.gguf"
            source.write_bytes(b"media")
            model.write_bytes(b"model")
            tools = FakeTools()
            service = TranscriptionService(
                Settings(model=str(model), output_dir=str(root / "default-out")),
                runner=tools,
            )

            results = service.transcribe(
                TranscriptionRequest(
                    inputs=(source,),
                    output_dir=root / "requested-out",
                    formats=("JSON", "txt"),
                )
            )

            self.assertEqual(len(results), 1)
            self.assertIsNone(results[0].error)
            self.assertEqual(tools.inference_calls, 1)
            details = service.inspect(results[0].job_dir)
            self.assertIsInstance(details, JobDetails)
            self.assertEqual(details.schema_version, 1)
            self.assertEqual(details.state, "completed")
            self.assertEqual({item.name for item in details.artifacts}, {"json", "txt"})
            manifest_copy = details.to_dict()
            manifest_copy["state"] = "mutated"
            self.assertEqual(details.state, "completed")
            self.assertEqual(details.to_dict()["state"], "completed")

    def test_python_caller_can_run_doctor_without_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.gguf"
            model.write_bytes(b"model")

            def runner(argv, **kwargs):
                call = tuple(str(part) for part in argv)
                if "doctor" in call:
                    return CommandResult(call, 0, json.dumps({"features": {}}), "")
                if "model" in call and "info" in call:
                    return CommandResult(call, 0, json.dumps({"runtime_compatible": True}), "")
                return CommandResult(call, 0, "fixture 1.0\n", "")

            executable = sys.executable
            service = TranscriptionService(
                Settings(
                    ffmpeg=executable,
                    ffprobe=executable,
                    nemo_speech=executable,
                    model=str(model),
                    output_dir=str(root),
                    device="cpu",
                ),
                runner=runner,
            )

            report = service.doctor()

            self.assertTrue(report.ready)
            self.assertIn("model-compatibility", {check.name for check in report.checks})

    def test_cancellation_is_checked_before_discovery_or_inference(self) -> None:
        class Cancelled:
            def is_cancelled(self) -> bool:
                return True

            def raise_if_cancelled(self) -> None:
                raise RuntimeError("cancelled fixture")

        service = TranscriptionService(Settings(model="missing.gguf"))

        with self.assertRaisesRegex(RuntimeError, "cancelled fixture"):
            service.transcribe(
                TranscriptionRequest(inputs=(Path("missing.mp4"),)),
                cancellation=Cancelled(),
            )

    def test_request_rejects_invalid_language_codes(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "codes such as"):
            TranscriptionRequest(
                inputs=(Path("recording.mp4"),),
                source_language="Russian",
            )

    def test_public_api_is_exported_from_package_root(self) -> None:
        expected = {
            "ArtifactDetails",
            "CancellationToken",
            "DoctorReport",
            "JobDetails",
            "JobResult",
            "Settings",
            "StageEvent",
            "TranscriptionRequest",
            "TranscriptionService",
        }

        self.assertTrue(expected.issubset(set(speechloom.__all__)))
        for name in expected:
            self.assertIsNotNone(getattr(speechloom, name))


if __name__ == "__main__":
    unittest.main()
