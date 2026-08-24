from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from parakeet_transcribe.nemo import (
    NemoOptions,
    TranslationOptions,
    adapt_payload,
    transcribe,
    translate_texts,
)
from parakeet_transcribe.process import CommandResult


class NemoAdapterTests(unittest.TestCase):
    def test_adapts_riva_words_info_shape(self) -> None:
        transcript = adapt_payload(
            {
                "transcript": "Привет, мир!",
                "words_info": {
                    "words": [
                        {"word": "Привет", "start_time": 0.1, "end_time": 0.5, "confidence": 0.9},
                        {"word": "мир!", "start_time": 0.6, "end_time": 1.0, "speaker_tag": 2},
                    ]
                },
            },
            duration=1.2,
        )
        self.assertEqual(transcript.text, "Привет, мир!")
        self.assertEqual(transcript.words[1].speaker, 2)
        self.assertIsNone(transcript.language)

    def test_transcribe_builds_one_integrated_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "аудио.wav"
            model = root / "model.gguf"
            diar = root / "sortformer.gguf"
            for path in (audio, model, diar):
                path.write_bytes(b"fixture")
            calls = []

            def runner(argv, **kwargs):
                calls.append(tuple(argv))
                return CommandResult(
                    tuple(argv), 0,
                    '{"text":"Hello.","words":[{"text":"Hello.","start":0.0,"end":0.5,"speaker":1}]}',
                    "",
                )

            transcript = transcribe(
                audio,
                NemoOptions("nemo-speech", model, "cuda:0", True, diar),
                duration=1.0,
                runner=runner,
            )
            self.assertEqual(len(calls), 1)
            self.assertIn("--word-times", calls[0])
            self.assertEqual(calls[0][calls[0].index("--format") + 1], "json")
            self.assertIn("--diar-model", calls[0])
            self.assertIn("cuda:0", calls[0])
            self.assertEqual(transcript.words[0].speaker, 1)

    def test_translate_texts_uses_one_structured_batch_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "translate.gguf"
            model.write_bytes(b"model")
            calls = []

            def runner(argv, **kwargs):
                calls.append(tuple(argv))
                input_path = Path(argv[argv.index("--input") + 1])
                inputs = input_path.read_text(encoding="utf-8").splitlines()
                payload = [
                    {
                        "input": text,
                        "text": f"English {index}",
                        "source_language": "ru",
                        "target_language": "en",
                    }
                    for index, text in enumerate(inputs)
                ]
                return CommandResult(tuple(argv), 0, __import__("json").dumps(payload), "")

            translated = translate_texts(
                ["Привет, мир!", "Как дела?"],
                TranslationOptions("nemo-speech", model, "ru", "en", "cuda:0"),
                runner=runner,
            )

            self.assertEqual(translated, ["English 0", "English 1"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][:2], ("nemo-speech", "translate"))
            self.assertIn("--input", calls[0])
            self.assertIn("cuda:0", calls[0])


if __name__ == "__main__":
    unittest.main()
