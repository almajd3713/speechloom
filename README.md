# Speechloom

Offline transcription and subtitles for audio and video, on your own machine.

Speechloom turns recordings into timestamped transcripts and subtitles (TXT, SRT,
WebVTT and JSON) with NVIDIA's Parakeet TDT 0.6B v3 model, run locally through
[NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp). It uses an NVIDIA GPU
when one is available and the CPU otherwise. Your media never leaves your computer.

[![PyPI](https://img.shields.io/pypi/v/speechloom)](https://pypi.org/project/speechloom/)
[![Python](https://img.shields.io/pypi/pyversions/speechloom)](https://pypi.org/project/speechloom/)
[![CI](https://github.com/almajd3713/speechloom/actions/workflows/python-package.yml/badge.svg)](https://github.com/almajd3713/speechloom/actions/workflows/python-package.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/almajd3713/speechloom/blob/main/LICENSE)

<!-- demo: replace this comment with ![Speechloom transcribing a video](https://raw.githubusercontent.com/almajd3713/speechloom/main/docs/media/demo.gif) once docs/media/demo.gif exists -->

## Why Speechloom

- **Private.** Transcription runs entirely offline. Only `speechloom setup` downloads
  anything: the engine and the models.
- **Fast on a laptop GPU.** A 10-minute recording takes about 17 seconds on an RTX 4070
  Laptop GPU, 34 times faster than real time, using about 4 GB of GPU memory. See the
  [benchmarks](https://github.com/almajd3713/speechloom/blob/main/docs/benchmarks.md).
- **25 European languages**, detected automatically: Bulgarian, Croatian, Czech, Danish,
  Dutch, English, Estonian, Finnish, French, German, Greek, Hungarian, Italian, Latvian,
  Lithuanian, Maltese, Polish, Portuguese, Romanian, Russian, Slovak, Slovenian, Spanish,
  Swedish and Ukrainian.
- **Translation and speaker labels, when you want them.** Translate transcripts between
  English and 36 other languages, including Arabic, Chinese and Japanese, with Riva
  Translate 4B. Label up to four speakers with Sortformer.
- **Safe to rerun.** Every job is fingerprinted by its input and settings. Finished work
  is skipped, interrupted work resumes, and results are never overwritten without
  `--force`.
- **Usable from code.** A typed Python API and an optional local HTTP API share the
  engine with the command line.

## Quick start

```bash
pipx install speechloom
speechloom setup
speechloom transcribe interview.mp4
```

`speechloom setup` installs the CUDA engine when `nvidia-smi` works and the CPU engine
otherwise; the CUDA toolkit is not needed. Results land in a job directory under
`transcripts/` in the current directory:

```text
transcripts/interview-<job id>/
├── manifest.json       job state, settings, checksums and timings
├── transcript.json     canonical transcript with word timestamps
├── transcript.txt
├── subtitles.srt
└── subtitles.vtt
```

```text
1
00:00:00,240 --> 00:00:04,479
going along slushy country roads and
speaking to damp audiences in drafty

2
00:00:04,480 --> 00:00:07,119
schoolrooms day after day for a fortnight.
```

## Requirements

- Linux on x86_64 (WSL2 works)
- Python 3.10 or newer
- FFmpeg and FFprobe on `PATH` (`sudo apt install ffmpeg` on Ubuntu)
- For GPU speed: an NVIDIA GPU whose driver supports CUDA 12, so that `nvidia-smi`
  works. On WSL2, install the driver on Windows, not inside WSL.

| Component | Download | On disk | Installed |
|---|---|---|---|
| CUDA engine | 804 MB | 1.3 GB | when an NVIDIA GPU works |
| CPU engine | 2 MB | 6 MB | otherwise |
| Speech model (Parakeet TDT 0.6B v3) | 714 MB | 714 MB | always |
| Speaker model (Sortformer, 4 speakers) | 147 MB | 147 MB | `--features diarization` |
| Translation model (Riva Translate 4B) | converted locally | 4.4 GB | `--features translation` |

Translation setup downloads the original checkpoint and converts it on your machine,
which needs 16 GB of free space during the conversion. Translation runs on the CUDA
engine; on a CPU-only machine it needs a source build
(`speechloom setup --features translation --from-source`).

## Usage

Transcribe files or folders:

```bash
speechloom transcribe recording.mp4
speechloom transcribe recordings/ --recursive
speechloom transcribe recording.mp4 --output-dir ./output --formats srt,txt
```

Add speaker labels:

```bash
speechloom setup --features diarization
speechloom transcribe meeting.mp4 --diarize
```

Translate. Parakeet does not report the language it heard, so name it:

```bash
speechloom setup --features translation
speechloom transcribe russian.mp4 --source-language ru --translate-to en
speechloom transcribe lecture.mp4 --source-language en --translate-to ar
```

Translated files are written next to the originals as `translation.<lang>.*` and
`subtitles.<lang>.*`.

Check the installation, inspect a job, or get machine-readable output:

```bash
speechloom doctor
speechloom inspect transcripts/<job directory>
speechloom transcribe recording.mp4 --json
```

Features are cumulative: running `speechloom setup` again keeps the ones you installed.
`speechloom setup status` verifies the installation, and `speechloom setup clean --all`
removes download caches.

## Python API

```python
from pathlib import Path

from speechloom import TranscriptionRequest, TranscriptionService

service = TranscriptionService.from_default_config()
for result in service.transcribe(TranscriptionRequest(inputs=(Path("interview.mp4"),))):
    print(result.state, result.job_dir)
```

The [Python API guide](https://github.com/almajd3713/speechloom/blob/main/docs/python-api.md)
covers progress events, cancellation, job inspection and background jobs.

## Local HTTP API

```bash
pipx install "speechloom[api]"
speechloom serve --allow-root ~/Videos
```

The API listens on `http://127.0.0.1:8765`, with interactive documentation at `/docs`.
It accepts uploads, or local paths for files under an `--allow-root` directory, streams
progress as server-sent events and serves the finished files. It rejects requests from
other websites, and keeps uploaded files until a client deletes them. See the
[HTTP API guide](https://github.com/almajd3713/speechloom/blob/main/docs/http-api.md).

## How it works

```text
media file
  → FFprobe and FFmpeg: pick the audio stream, convert to 16 kHz mono WAV
  → NeMo-Speech.cpp with Parakeet TDT: words with timestamps
  → transcript.json, the canonical result, never overwritten without --force
  → TXT, SRT and VTT, plus an optional translation through Riva Translate 4B
```

Each job directory has a manifest with the input and model checksums, the settings, the
tool versions and the timings, so every result can be traced to how it was made.

## Configuration

Settings come from, in order of precedence: command-line options, `SPEECHLOOM_*`
environment variables, `~/.config/speechloom/config.ini`, and built-in defaults.
`speechloom setup` records the engine and model paths in its own state file, so the
configuration file only needs the settings you want to change. See
[config.example.ini](https://github.com/almajd3713/speechloom/blob/main/config.example.ini).

## Limitations

- Speech recognition covers the 25 languages above. Arabic speech is not supported;
  Arabic is available as a translation target.
- The engine reports neither the detected language nor meaningful per-word confidence:
  transcripts leave the language empty and give every word a confidence of 1.0.
- Prebuilt engines exist for Linux on x86_64 only. Other platforms and the Vulkan
  backend need `speechloom setup --from-source`, which requires CMake 3.26 through 3.x,
  Ninja and a C++17 compiler, plus `nvcc` for CUDA.
- Diarization labels at most four speakers.
- On the CPU engine, transcription runs slower than real time (about 0.6×).

## More

- [Troubleshooting](https://github.com/almajd3/speechloom/blob/main/docs/operations/troubleshooting.md)
- [Contributing](https://github.com/almajd3/speechloom/blob/main/CONTRIBUTING.md)
- [Changelog](https://github.com/almajd3/speechloom/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/almajd3/speechloom/blob/main/SECURITY.md)

## License

Speechloom is released under the MIT License. Models are downloaded from their
publishers under their own licenses: Parakeet TDT 0.6B v3 and Sortformer under CC BY
4.0, and Riva Translate 4B under the NVIDIA Open Model License. NeMo-Speech.cpp is
licensed under Apache 2.0.
