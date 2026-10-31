from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import speechloom
from speechloom import (
    CancellationController,
    JobDetails,
    SegmentEdit,
    Settings,
    StageEvent,
    TranscriptionRequest,
    TranscriptionService,
)
from speechloom.artifacts import sha256_file
from speechloom.errors import ArtifactConflictError, ConfigurationError
from speechloom.process import CommandResult
from tests.contract.test_pipeline import FakeTools


EDITED_FILES = (
    "transcript.edited.json",
    "transcript.edited.txt",
    "subtitles.edited.srt",
    "subtitles.edited.vtt",
)


class TwoSegmentTools(FakeTools):
    @staticmethod
    def transcript_payload() -> dict:
        return {
            "text": "One. Two.",
            "words": [
                {"word": "One.", "start_time": 0.0, "end_time": 0.4},
                {"word": "Two.", "start_time": 2.0, "end_time": 2.4},
            ],
        }


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

    def test_python_caller_receives_stable_stage_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recording.mp4"
            model = root / "model.gguf"
            source.write_bytes(b"media")
            model.write_bytes(b"model")
            events: list[StageEvent] = []
            service = TranscriptionService(
                Settings(model=str(model), output_dir=str(root / "out")),
                runner=FakeTools(),
            )

            result = service.transcribe(
                TranscriptionRequest(inputs=(source,)),
                on_event=events.append,
            )[0]

            self.assertEqual(result.state, "completed")
            ordered_stages = list(dict.fromkeys(event.stage for event in events))
            self.assertEqual(
                ordered_stages,
                [
                    "queued",
                    "validating",
                    "probing",
                    "normalizing",
                    "transcribing",
                    "rendering",
                    "completed",
                ],
            )
            self.assertTrue(all(event.timestamp for event in events))
            self.assertIsNotNone(events[-1].job_id)

    def test_cancel_after_canonical_commit_resumes_without_repeating_asr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "russian.mp4"
            model = root / "model.gguf"
            translation_model = root / "translate.gguf"
            source.write_bytes(b"media")
            model.write_bytes(b"model")
            translation_model.write_bytes(b"translation")
            tools = FakeTools()
            service = TranscriptionService(
                Settings(
                    model=str(model),
                    translation_model=str(translation_model),
                    source_language="ru",
                    translate_to="en",
                    output_dir=str(root / "out"),
                ),
                runner=tools,
            )
            controller = CancellationController()

            def cancel_after_asr(event: StageEvent) -> None:
                if event.stage == "transcribing" and event.status == "completed":
                    controller.cancel()

            cancelled = service.transcribe(
                TranscriptionRequest(inputs=(source,)),
                on_event=cancel_after_asr,
                cancellation=controller,
            )[0]

            self.assertEqual(cancelled.state, "cancelled")
            manifest = json.loads(
                (Path(cancelled.job_dir) / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["state"], "cancelled")
            self.assertEqual(manifest["state_detail"], "transcribed")
            self.assertTrue((Path(cancelled.job_dir) / "transcript.json").is_file())
            self.assertFalse((Path(cancelled.job_dir) / "translation.en.json").exists())

            resumed = service.transcribe(TranscriptionRequest(inputs=(source,)))[0]

            self.assertEqual(resumed.state, "completed")
            self.assertEqual(tools.inference_calls, 1)
            self.assertEqual(tools.translation_calls, 1)

    def test_installed_translation_model_does_not_require_languages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recording.mp4"
            model = root / "model.gguf"
            translation_model = root / "translate.gguf"
            source.write_bytes(b"media")
            model.write_bytes(b"model")
            translation_model.write_bytes(b"translation")
            tools = FakeTools()
            service = TranscriptionService(
                Settings(
                    model=str(model),
                    translation_model=str(translation_model),
                    output_dir=str(root / "out"),
                ),
                runner=tools,
            )

            result = service.transcribe(TranscriptionRequest(inputs=(source,)))[0]

            self.assertEqual(result.state, "completed")
            self.assertIsNone(result.error)
            self.assertEqual(tools.inference_calls, 1)
            self.assertEqual(tools.translation_calls, 0)
            self.assertFalse((Path(result.job_dir) / "translation.en.json").exists())
            manifest = json.loads(
                (Path(result.job_dir) / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertIsNone(manifest["translation_model"])

    def test_request_languages_enable_installed_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "russian.mp4"
            model = root / "model.gguf"
            translation_model = root / "translate.gguf"
            source.write_bytes(b"media")
            model.write_bytes(b"model")
            translation_model.write_bytes(b"translation")
            tools = FakeTools()
            service = TranscriptionService(
                Settings(
                    model=str(model),
                    translation_model=str(translation_model),
                    output_dir=str(root / "out"),
                ),
                runner=tools,
            )

            result = service.transcribe(
                TranscriptionRequest(inputs=(source,), source_language="ru", translate_to="en")
            )[0]

            self.assertEqual(result.state, "completed")
            self.assertEqual(tools.translation_calls, 1)
            self.assertTrue((Path(result.job_dir) / "translation.en.json").is_file())

    def test_single_translation_language_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recording.mp4"
            model = root / "model.gguf"
            translation_model = root / "translate.gguf"
            source.write_bytes(b"media")
            model.write_bytes(b"model")
            translation_model.write_bytes(b"translation")
            tools = FakeTools()
            service = TranscriptionService(
                Settings(
                    model=str(model),
                    translation_model=str(translation_model),
                    output_dir=str(root / "out"),
                ),
                runner=tools,
            )

            with self.assertRaisesRegex(
                ConfigurationError, "requires both a source language and a target language"
            ):
                service.transcribe(TranscriptionRequest(inputs=(source,), translate_to="en"))

            self.assertEqual(tools.inference_calls, 0)

    def test_missing_model_points_to_setup(self) -> None:
        service = TranscriptionService(Settings())

        with self.assertRaises(ConfigurationError) as raised:
            service.transcribe(TranscriptionRequest(inputs=(Path("recording.mp4"),)))

        self.assertEqual(
            str(raised.exception),
            "No ASR model is configured; run 'speechloom setup' or pass --model",
        )

    def test_default_config_caches_fingerprints_in_cache_home(self) -> None:
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
            environment = {
                "SPEECHLOOM_CONFIG_HOME": str(root / "config"),
                "SPEECHLOOM_DATA_HOME": str(root / "data"),
                "SPEECHLOOM_CACHE_HOME": str(root / "cache"),
            }
            service = TranscriptionService.from_default_config(
                config_path=config,
                env=environment,
                runner=FakeTools(),
            )

            first = service.transcribe(TranscriptionRequest(inputs=(source,)))[0]

            self.assertEqual(first.state, "completed")
            cache = root / "cache" / "fingerprints.json"
            entries = json.loads(cache.read_text(encoding="utf-8"))["entries"]
            self.assertIn(str(model.resolve()), entries)
            self.assertIn(str(source.resolve()), entries)
            with patch("speechloom.fingerprints.sha256_file", wraps=sha256_file) as hashing:
                second = service.transcribe(TranscriptionRequest(inputs=(source,)))[0]
            self.assertTrue(second.skipped)
            self.assertEqual(hashing.call_count, 0)

    def test_transcription_needs_both_the_model_file_and_the_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "nemo-speech"
            engine.write_text("#!/bin/sh\n", encoding="utf-8")
            engine.chmod(0o755)
            model = root / "model.gguf"

            def ready(model_path: Path | None, engine_path: Path) -> bool:
                settings = Settings(
                    model=str(model_path) if model_path else None, nemo_speech=str(engine_path)
                )
                return TranscriptionService(settings).capabilities().transcription

            self.assertFalse(ready(model, engine))  # model file missing
            model.write_bytes(b"model")
            self.assertTrue(ready(model, engine))
            self.assertFalse(ready(model, root / "absent"))  # engine missing
            self.assertFalse(ready(None, engine))  # no model configured

    def test_service_reports_installed_features(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.gguf").write_bytes(b"model")
            (root / "diar.gguf").write_bytes(b"diar")
            settings = Settings(
                model=str(root / "model.gguf"),
                diar_model=str(root / "diar.gguf"),
                translation_model=str(root / "missing.gguf"),
            )

            capabilities = TranscriptionService(settings).capabilities()

            self.assertTrue(capabilities.diarization)
            self.assertFalse(capabilities.translation)
            self.assertEqual(capabilities.translation_languages, ())
            self.assertEqual(len(capabilities.asr_languages), 25)
            self.assertEqual(capabilities.formats, ("json", "txt", "srt", "vtt"))

            (root / "translate.gguf").write_bytes(b"translate")
            settings = Settings(
                model=str(root / "model.gguf"),
                diar_model=str(root / "diar.gguf"),
                translation_model=str(root / "translate.gguf"),
            )
            capabilities = TranscriptionService(settings).capabilities()

            self.assertTrue(capabilities.translation)
            self.assertEqual(len(capabilities.translation_languages), 37)
            self.assertIn("ar", capabilities.translation_languages)

    def _transcribed(self, root: Path, tools: FakeTools) -> tuple[TranscriptionService, Path]:
        source = root / "recording.mp4"
        model = root / "model.gguf"
        source.write_bytes(b"media")
        model.write_bytes(b"model")
        service = TranscriptionService(
            Settings(model=str(model), output_dir=str(root / "out")),
            runner=tools,
        )
        result = service.transcribe(TranscriptionRequest(inputs=(source,)))[0]
        self.assertIsNone(result.error)
        return service, Path(result.job_dir)

    def test_save_edits_writes_edited_artifacts_beside_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, job_dir = self._transcribed(Path(directory), FakeTools())
            canonical = (job_dir / "transcript.json").read_bytes()

            details = service.save_edits(job_dir, [SegmentEdit(0, "Hello,   world!")])

            names = {item.name for item in details.artifacts}
            self.assertTrue({"edited.json", "edited.txt", "edited.srt", "edited.vtt"} <= names)
            self.assertEqual((job_dir / "transcript.json").read_bytes(), canonical)
            self.assertEqual(
                (job_dir / "transcript.edited.txt").read_text(encoding="utf-8"), "Hello, world!\n"
            )
            self.assertIn(
                "Hello, world!", (job_dir / "subtitles.edited.srt").read_text(encoding="utf-8")
            )
            edited = json.loads((job_dir / "transcript.edited.json").read_text(encoding="utf-8"))
            self.assertEqual(edited["edited_indices"], [0])
            manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["edits"]["edited_segments"], 1)

    def test_save_edits_replaces_previous_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, job_dir = self._transcribed(Path(directory), TwoSegmentTools())

            service.save_edits(job_dir, [SegmentEdit(0, "First")])
            service.save_edits(job_dir, [SegmentEdit(1, "Second")])

            self.assertEqual(
                (job_dir / "transcript.edited.txt").read_text(encoding="utf-8"), "One. Second\n"
            )

    def test_save_edits_merges_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, job_dir = self._transcribed(Path(directory), TwoSegmentTools())
            text_path = job_dir / "transcript.edited.txt"

            service.save_edits(job_dir, [SegmentEdit(0, "First")])
            service.save_edits(job_dir, [SegmentEdit(1, "Second")], merge=True)
            self.assertEqual(text_path.read_text(encoding="utf-8"), "First Second\n")

            service.save_edits(job_dir, [SegmentEdit(0, None)], merge=True)
            self.assertEqual(text_path.read_text(encoding="utf-8"), "One. Second\n")
            edited = json.loads((job_dir / "transcript.edited.json").read_text(encoding="utf-8"))
            self.assertEqual(edited["edited_indices"], [1])

            details = service.save_edits(job_dir, [SegmentEdit(1, None)], merge=True)
            for filename in EDITED_FILES:
                self.assertFalse((job_dir / filename).exists())
            manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("edits", manifest)
            self.assertFalse([item.name for item in details.artifacts if item.name.startswith("edited.")])

    def test_clear_edits_removes_edited_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, job_dir = self._transcribed(Path(directory), FakeTools())
            service.save_edits(job_dir, [SegmentEdit(0, "Changed")])

            service.clear_edits(job_dir)

            for filename in EDITED_FILES:
                self.assertFalse((job_dir / filename).exists())
            manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("edits", manifest)
            service.clear_edits(job_dir)

    def test_save_edits_rejects_invalid_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, job_dir = self._transcribed(Path(directory), FakeTools())

            with self.assertRaisesRegex(ConfigurationError, "At least one segment edit is required"):
                service.save_edits(job_dir, [])
            with self.assertRaisesRegex(
                ConfigurationError, "Segment 5 does not exist; the transcript has 1 segments"
            ):
                service.save_edits(job_dir, [SegmentEdit(5, "x")])
            with self.assertRaisesRegex(ConfigurationError, "Segment 0 is edited more than once"):
                service.save_edits(job_dir, [SegmentEdit(0, "a"), SegmentEdit(0, "b")])
            with self.assertRaisesRegex(
                ConfigurationError, "Edit for segment 0 must contain 1 to 1000 characters"
            ):
                SegmentEdit(0, "   ")
            with self.assertRaisesRegex(ConfigurationError, "Segment index must not be negative"):
                SegmentEdit(-1, "x")
            self.assertIsNone(SegmentEdit(0, None).text)

    def test_save_edits_requires_a_completed_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, job_dir = self._transcribed(Path(directory), FakeTools())
            manifest_path = job_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["state"] = "failed"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                ArtifactConflictError, "^Only completed jobs can be edited"
            ):
                service.save_edits(job_dir, [SegmentEdit(0, "x")])
            with self.assertRaisesRegex(
                ArtifactConflictError, "^Only completed jobs can be edited"
            ):
                service.clear_edits(job_dir)

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
            "CancellationController",
            "CancellationError",
            "CancellationToken",
            "DoctorReport",
            "DuplicateJobError",
            "JobDetails",
            "JobEvent",
            "JobManager",
            "JobManagerError",
            "JobNotFoundError",
            "JobQueueFullError",
            "JobResult",
            "ManagedJob",
            "SegmentEdit",
            "ServiceCapabilities",
            "Settings",
            "SetupManager",
            "SetupRequest",
            "SetupResult",
            "SetupStatus",
            "StageEvent",
            "TranscriptionRequest",
            "TranscriptionService",
        }

        self.assertTrue(expected.issubset(set(speechloom.__all__)))
        for name in expected:
            self.assertIsNotNone(getattr(speechloom, name))


if __name__ == "__main__":
    unittest.main()
