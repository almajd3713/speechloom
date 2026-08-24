from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from parakeet_transcribe.jobs import Pipeline, PipelineOptions, inspect_job
from parakeet_transcribe.process import CommandResult


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.inference_calls = 0

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
            payload = {
                "text": "Привет, world!",
                "words": [
                    {"word": "Привет,", "start_time": 0.0, "end_time": 0.5},
                    {"word": "world!", "start_time": 0.6, "end_time": 1.1},
                ],
            }
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
            options = PipelineOptions(output_dir=root / "out", model=model, keep_audio=False)
            pipeline = Pipeline(options, runner=tools)

            first = pipeline.run([source])[0]
            second = pipeline.run([source])[0]

            self.assertIsNone(first.error)
            self.assertTrue(second.skipped)
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
            self.assertNotIn("shell=True", repr(nemo_calls[0]))


if __name__ == "__main__":
    unittest.main()
