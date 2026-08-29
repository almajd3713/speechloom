from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from speechloom.media import (
    can_passthrough_wav,
    normalize_audio,
    probe_media,
    select_audio_stream,
)
from speechloom.process import CommandResult


class MediaTests(unittest.TestCase):
    def test_probe_and_select_stream(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append(tuple(argv))
            return CommandResult(
                tuple(argv),
                0,
                '{"format":{"duration":"12.5"},"streams":['
                '{"index":0,"codec_type":"video","codec_name":"h264"},'
                '{"index":2,"codec_type":"audio","codec_name":"aac","sample_rate":"48000","channels":2}]}' ,
                "",
            )

        info = probe_media(Path("lecture.mp4"), runner=runner)
        self.assertEqual(info.duration, 12.5)
        self.assertEqual(select_audio_stream(info, 2).codec, "aac")
        self.assertFalse(can_passthrough_wav(Path("lecture.mp4"), info.audio_streams[0]))
        self.assertEqual(
            calls,
            [
                (
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=index,codec_type,codec_name,sample_rate,channels",
                    "-of",
                    "json",
                    "lecture.mp4",
                )
            ],
        )

    def test_normalization_uses_selected_stream_and_atomic_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "audio.wav"
            calls = []

            def runner(argv, **kwargs):
                calls.append(tuple(argv))
                Path(argv[-1]).write_bytes(b"RIFF-fake")
                return CommandResult(tuple(argv), 0, "", "")

            info = probe_media(
                Path("input.mkv"),
                runner=lambda argv, **kwargs: CommandResult(
                    tuple(argv), 0,
                    '{"format":{"duration":"1"},"streams":['
                    '{"index":3,"codec_type":"audio","codec_name":"opus","sample_rate":"48000","channels":2}]}',
                    "",
                ),
            )
            normalize_audio(Path("input.mkv"), destination, info.audio_streams[0], runner=runner)
            self.assertTrue(destination.is_file())
            self.assertEqual(
                calls,
                [
                    (
                        "ffmpeg",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        "input.mkv",
                        "-map",
                        "0:3",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        str(destination.with_name("audio.part.wav")),
                    )
                ],
            )


if __name__ == "__main__":
    unittest.main()
