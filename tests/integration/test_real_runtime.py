from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest

from parakeet_transcribe.jobs import Pipeline, PipelineOptions
from parakeet_transcribe.schema import Transcript


class RealRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("PARAKEET_TEST_NEMO")
        and os.environ.get("PARAKEET_TEST_MODEL")
        and os.environ.get("PARAKEET_TEST_MEDIA"),
        "set PARAKEET_TEST_NEMO, PARAKEET_TEST_MODEL, and PARAKEET_TEST_MEDIA",
    )
    def test_real_cpu_transcription_contract(self) -> None:
        executable = Path(os.environ["PARAKEET_TEST_NEMO"])
        model = Path(os.environ["PARAKEET_TEST_MODEL"])
        media = Path(os.environ["PARAKEET_TEST_MEDIA"])
        with tempfile.TemporaryDirectory() as directory:
            result = Pipeline(
                PipelineOptions(
                    output_dir=Path(directory),
                    model=model,
                    nemo_speech=str(executable),
                    device="cpu",
                )
            ).run([media])[0]
            self.assertIsNone(result.error)
            transcript = Transcript.load(Path(result.job_dir) / "transcript.json")
            self.assertTrue(transcript.text)
            self.assertGreater(len(transcript.words), 1)
            self.assertTrue((Path(result.job_dir) / "subtitles.srt").is_file())

    @unittest.skipUnless(
        os.environ.get("PARAKEET_TEST_NEMO")
        and os.environ.get("PARAKEET_TEST_MODEL")
        and os.environ.get("PARAKEET_TEST_MEDIA"),
        "set PARAKEET_TEST_NEMO, PARAKEET_TEST_MODEL, and PARAKEET_TEST_MEDIA",
    )
    def test_real_shared_model_batch_contract(self) -> None:
        executable = Path(os.environ["PARAKEET_TEST_NEMO"])
        model = Path(os.environ["PARAKEET_TEST_MODEL"])
        media = Path(os.environ["PARAKEET_TEST_MEDIA"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "a" / "recording.wav", root / "b" / "recording.wav"]
            for source in sources:
                source.parent.mkdir()
                shutil.copy2(media, source)
            results = Pipeline(
                PipelineOptions(
                    output_dir=root / "out",
                    model=model,
                    nemo_speech=str(executable),
                    device="cpu",
                    workers=2,
                )
            ).run(sources)

            self.assertEqual(len(results), 2)
            self.assertTrue(all(result.error is None for result in results))
            for result in results:
                transcript = Transcript.load(Path(result.job_dir) / "transcript.json")
                self.assertTrue(transcript.text)


if __name__ == "__main__":
    unittest.main()
