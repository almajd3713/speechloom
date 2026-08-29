from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from speechloom.cli import main
from speechloom.config import Settings
from speechloom.contracts import JobDetails, TranscriptionRequest
from speechloom.doctor import Check, DoctorReport
from speechloom.jobs import JobResult


class CliServiceContractTests(unittest.TestCase):
    @patch("speechloom.cli.TranscriptionService")
    @patch("speechloom.cli.load_settings")
    def test_transcribe_delegates_to_service(
        self,
        load_settings: Mock,
        service_type: Mock,
    ) -> None:
        settings = Settings(model="model.gguf", output_dir="configured-output")
        load_settings.return_value = settings
        service = service_type.return_value
        service.transcribe.return_value = [
            JobResult("recording.mp4", "configured-output/job", "completed")
        ]
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "--config",
                    "config.ini",
                    "transcribe",
                    "recording.mp4",
                    "--output-dir",
                    "configured-output",
                    "--formats",
                    "json,txt",
                    "--workers",
                    "3",
                    "--recursive",
                    "--audio-stream",
                    "2",
                    "--force",
                    "--fail-fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        service_type.assert_called_once_with(settings)
        load_settings.assert_called_once()
        request = service.transcribe.call_args.args[0]
        self.assertIsInstance(request, TranscriptionRequest)
        self.assertEqual(request.inputs, (Path("recording.mp4"),))
        self.assertTrue(request.recursive)
        self.assertEqual(request.audio_stream, 2)
        self.assertTrue(request.force)
        self.assertTrue(request.fail_fast)
        self.assertEqual(output.getvalue(), "CREATED recording.mp4 -> configured-output/job\n")

    @patch("speechloom.cli.TranscriptionService")
    @patch("speechloom.cli.load_settings")
    def test_doctor_delegates_to_service(
        self,
        load_settings: Mock,
        service_type: Mock,
    ) -> None:
        settings = Settings()
        load_settings.return_value = settings
        service = service_type.return_value
        service.doctor.return_value = DoctorReport(
            True,
            (Check("runtime", "ok", "ready"),),
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["doctor", "--output-dir", "jobs"])

        self.assertEqual(exit_code, 0)
        service_type.assert_called_once_with(settings)
        service.doctor.assert_called_once_with(output_dir=Path("jobs"))
        self.assertEqual(output.getvalue(), "[OK] runtime: ready\nReady.\n")

    @patch("speechloom.cli.TranscriptionService")
    @patch("speechloom.cli.load_settings")
    def test_inspect_delegates_without_loading_runtime_config(
        self,
        load_settings: Mock,
        service_type: Mock,
    ) -> None:
        service = service_type.return_value
        service.inspect.return_value = JobDetails.from_manifest(
            {
                "schema_version": 1,
                "job_id": "job-1",
                "state": "completed",
                "state_detail": "completed",
                "source": {"path": "/media/example.mp4"},
                "artifacts": {
                    "txt": {
                        "path": "transcript.txt",
                        "size": 12,
                        "sha256": "abc",
                    }
                },
            }
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["inspect", "job-directory"])

        self.assertEqual(exit_code, 0)
        load_settings.assert_not_called()
        service_type.assert_called_once_with(Settings())
        service.inspect.assert_called_once_with(Path("job-directory"))
        self.assertEqual(
            output.getvalue(),
            "Job: job-1\n"
            "State: completed\n"
            "Source: /media/example.mp4\n"
            "TXT: transcript.txt (12 bytes)\n",
        )


if __name__ == "__main__":
    unittest.main()
