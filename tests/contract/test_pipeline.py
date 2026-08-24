from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from parakeet_transcribe.jobs import Pipeline, PipelineOptions, inspect_job
from parakeet_transcribe.process import CommandResult


class FakeTools:
    def __init__(self, omit_batch_result: int | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.inference_calls = 0
        self.omit_batch_result = omit_batch_result

    @staticmethod
    def transcript_payload() -> dict:
        return {
            "text": "Привет, world!",
            "words": [
                {"word": "Привет,", "start_time": 0.0, "end_time": 0.5},
                {"word": "world!", "start_time": 0.6, "end_time": 1.1},
            ],
        }

    def __call__(self, argv, **kwargs):
        call = tuple(str(part) for part in argv)
        self.calls.append(call)
        if call[1:] in {("-version",), ("--version",)}:
            return CommandResult(call, 0, f"{call[0]} fixture-1.0\n", "")
        if call[0] == "ffprobe":
            payload = {
                "format": {"duration": "3.0"},
                "streams": [
                    {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2}
                ],
            }
            return CommandResult(call, 0, json.dumps(payload), "")
        if call[0] == "ffmpeg":
            Path(call[-1]).write_bytes(b"normalized-wave")
            return CommandResult(call, 0, "", "")
        if call[0] == "nemo-speech" and call[1] == "transcribe":
            self.inference_calls += 1
            input_path = Path(call[2])
            if input_path.is_dir():
                output_dir = Path(call[call.index("--output-dir") + 1])
                for index, wav in enumerate(sorted(input_path.glob("*.wav"))):
                    if index == self.omit_batch_result:
                        continue
                    destination = output_dir / wav.with_suffix(".json").name
                    destination.write_text(
                        json.dumps(self.transcript_payload(), ensure_ascii=False),
                        encoding="utf-8",
                    )
                return CommandResult(call, 0, "", "")
            payload = self.transcript_payload()
            return CommandResult(call, 0, json.dumps(payload, ensure_ascii=False), "")
        return CommandResult(call, 2, "", "unexpected fake command")


class PipelineContractTests(unittest.TestCase):
    def test_single_pass_artifacts_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Видео с пробелом.mp4"
            model = root / "parakeet.gguf"
            source.write_bytes(b"video-source")
            model.write_bytes(b"model")
            tools = FakeTools()
            initial_options = PipelineOptions(
                output_dir=root / "out", model=model, keep_audio=False, formats=("json", "txt")
            )
            pipeline = Pipeline(initial_options, runner=tools)

            first = pipeline.run([source])[0]
            expanded_options = PipelineOptions(output_dir=root / "out", model=model, keep_audio=False)
            expanded_pipeline = Pipeline(expanded_options, runner=tools)
            second = expanded_pipeline.run([source])[0]
            third = expanded_pipeline.run([source])[0]

            self.assertIsNone(first.error)
            self.assertFalse(second.skipped)
            self.assertTrue(third.skipped)
            self.assertEqual(tools.inference_calls, 1)
            job = Path(first.job_dir)
            self.assertEqual((job / "transcript.txt").read_text(encoding="utf-8"), "Привет, world!\n")
            self.assertTrue((job / "subtitles.srt").is_file())
            self.assertTrue((job / "subtitles.vtt").is_file())
            self.assertFalse((job / ".work" / "audio.wav").exists())
            manifest = inspect_job(job)
            self.assertEqual(manifest["state"], "completed")
            self.assertEqual(set(manifest["artifacts"]), {"json", "txt", "srt", "vtt"})
            nemo_calls = [call for call in tools.calls if call[:2] == ("nemo-speech", "transcribe")]
            self.assertEqual(len(nemo_calls), 1)
            self.assertIn("--format", nemo_calls[0])
            self.assertNotIn("shell=True", repr(nemo_calls[0]))

    def test_shared_model_batch_invokes_native_runtime_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "a" / "recording.mp4", root / "b" / "recording.mp4"]
            model = root / "parakeet.gguf"
            for source in sources:
                source.parent.mkdir()
                source.write_bytes(b"media")
            model.write_bytes(b"model")
            tools = FakeTools()
            pipeline = Pipeline(
                PipelineOptions(output_dir=root / "out", model=model, workers=2),
                runner=tools,
            )

            results = pipeline.run(sources)

            self.assertEqual(len(results), 2)
            self.assertTrue(all(result.error is None for result in results))
            self.assertEqual(tools.inference_calls, 1)
            batch_call = next(
                call for call in tools.calls if call[:2] == ("nemo-speech", "transcribe")
            )
            self.assertIn("--recursive", batch_call)
            self.assertEqual(batch_call[batch_call.index("--concurrency") + 1], "2")
            for result in results:
                manifest = inspect_job(Path(result.job_dir))
                self.assertEqual(manifest["timings"]["batch_size"], 2)

    def test_shared_model_batch_isolates_missing_native_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "one.mp4", root / "two.mp4"]
            model = root / "parakeet.gguf"
            for source in sources:
                source.write_bytes(b"media")
            model.write_bytes(b"model")
            tools = FakeTools(omit_batch_result=1)

            results = Pipeline(
                PipelineOptions(output_dir=root / "out", model=model), runner=tools
            ).run(sources)

            self.assertEqual(len(results), 2)
            self.assertEqual(sum(result.error is None for result in results), 1)
            failed = next(result for result in results if result.error)
            self.assertIn("produced no result", failed.error)
            self.assertEqual(inspect_job(Path(failed.job_dir))["state"], "failed")

    def test_media_preparation_failure_is_recorded_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "corrupt.mp4"
            model = root / "parakeet.gguf"
            source.write_bytes(b"not-media")
            model.write_bytes(b"model")
            tools = FakeTools()

            def failing_probe(argv, **kwargs):
                call = tuple(str(part) for part in argv)
                if call[0] == "ffprobe":
                    return CommandResult(call, 1, "", "invalid media")
                return tools(argv, **kwargs)

            result = Pipeline(
                PipelineOptions(output_dir=root / "out", model=model), runner=failing_probe
            ).run([source])[0]

            self.assertIsNotNone(result.error)
            self.assertTrue(result.job_dir)
            manifest = inspect_job(Path(result.job_dir))
            self.assertEqual(manifest["state"], "failed")
            self.assertIn("FFprobe", manifest["error"]["message"])



if __name__ == "__main__":
    unittest.main()
