from __future__ import annotations

import unittest

from parakeet_transcribe.renderers import build_segments, render_srt, render_text, render_vtt
from parakeet_transcribe.schema import Transcript, Word


class RendererTests(unittest.TestCase):
    def test_segments_on_punctuation_gap_and_speaker(self) -> None:
        transcript = Transcript(
            text="Hello world. Привет мир!",
            duration=4.0,
            words=[
                Word("Hello", 0.0, 0.3, speaker=1),
                Word("world.", 0.4, 0.8, speaker=1),
                Word("Привет", 2.0, 2.4, speaker=2),
                Word("мир!", 2.5, 2.9, speaker=2),
            ],
        )
        segments = build_segments(transcript)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].speaker, 1)
        self.assertLessEqual(segments[0].end, segments[1].start)
        self.assertIn("[Speaker 1]", render_srt(segments))
        self.assertTrue(render_vtt(segments).startswith("WEBVTT\n"))
        self.assertIn("Speaker 2: Привет мир!", render_text(transcript))

    def test_long_subtitle_is_at_most_two_lines(self) -> None:
        words = [Word(f"word{index}", index * 0.2, index * 0.2 + 0.1) for index in range(15)]
        transcript = Transcript(text=" ".join(word.text for word in words), words=words, duration=4.0)
        for segment in build_segments(transcript):
            self.assertLessEqual(len(segment.text.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()

