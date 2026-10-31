# Speechloom

Transcribe audio and video into subtitles on your own computer. Nothing is uploaded
anywhere.

Speechloom runs NVIDIA's Parakeet speech model through
[NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp) and writes TXT, SRT, WebVTT
and JSON files. It uses your NVIDIA GPU if you have one, and the CPU if you don't.

[![PyPI](https://img.shields.io/pypi/v/speechloom)](https://pypi.org/project/speechloom/)
[![Python](https://img.shields.io/pypi/pyversions/speechloom)](https://pypi.org/project/speechloom/)
[![CI](https://github.com/almajd3713/speechloom/actions/workflows/python-package.yml/badge.svg)](https://github.com/almajd3713/speechloom/actions/workflows/python-package.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/almajd3713/speechloom/blob/main/LICENSE)

<!-- demo: replace this comment with ![Speechloom transcribing a video](https://raw.githubusercontent.com/almajd3713/speechloom/main/docs/media/demo.gif) once docs/media/demo.gif exists -->

```bash
pipx install speechloom
speechloom setup
speechloom transcribe interview.mp4
```

The first `setup` downloads the engine and the speech model (about 1.5 GB on a GPU
machine). After that, everything works offline. You get a folder like this:

```text
transcripts/interview-<job id>/
├── transcript.txt
├── subtitles.srt
├── subtitles.vtt
├── transcript.json     word-level timestamps
└── manifest.json       settings, checksums and timings
```

```text
1
00:00:00,240 --> 00:00:04,479
going along slushy country roads and
speaking to damp audiences in drafty
```

## What you get

- **Speed.** A 10-minute recording takes about 17 seconds on an RTX 4070 laptop GPU
  (see the [benchmarks](https://github.com/almajd3713/speechloom/blob/main/docs/benchmarks.md)).
  On the CPU it runs slower than real time.
- **25 European languages**, detected automatically: Bulgarian, Croatian, Czech, Danish,
  Dutch, English, Estonian, Finnish, French, German, Greek, Hungarian, Italian, Latvian,
  Lithuanian, Maltese, Polish, Portuguese, Romanian, Russian, Slovak, Slovenian, Spanish,
  Swedish and Ukrainian.
- **Translation** between English and 36 other languages, including Arabic, Chinese and
  Japanese.
- **Speaker labels** for up to four speakers.
- **Safe reruns.** Finished jobs are skipped, interrupted ones resume, and nothing is
  overwritten unless you pass `--force`.

## Requirements

- Linux on x86_64 (WSL2 works)
- Python 3.10 or newer
- FFmpeg on your `PATH` (`sudo apt install ffmpeg` on Ubuntu)
- Optional, for GPU speed: an NVIDIA driver that supports CUDA 12, so that `nvidia-smi`
  works. On WSL2, install the driver on Windows, not inside WSL.

`speechloom setup` picks the CUDA engine if `nvidia-smi` works and the CPU engine
otherwise. You don't need the CUDA toolkit.

| Download | Size on disk | When |
|---|---|---|
| Speech model | 714 MB | always |
| CUDA engine | 1.3 GB | an NVIDIA GPU works |
| CPU engine | 6 MB | otherwise |
| Speaker model | 147 MB | `--features diarization` |
| Translation model | 4.4 GB | `--features translation` |

Translation is converted on your machine and needs 16 GB of free space while it does.
It runs on the CUDA engine; on a CPU-only machine you have to build from source with
`speechloom setup --features translation --from-source`.

## Using it

```bash
speechloom transcribe recording.mp4
speechloom transcribe recordings/ --recursive
speechloom transcribe recording.mp4 --output-dir ./output --formats srt,txt
```

For speaker labels:

```bash
speechloom setup --features diarization
speechloom transcribe meeting.mp4 --diarize
```

For translation, tell it what language is spoken, because the engine doesn't report it:

```bash
speechloom setup --features translation
speechloom transcribe russian.mp4 --source-language ru --translate-to en
```

Translations are written next to the originals as `translation.<lang>.*` and
`subtitles.<lang>.*`.

Setup features add up, so running `setup` again keeps what you installed. Other
commands you may want: `speechloom doctor` checks the installation, `speechloom inspect
<job folder>` shows a job, `--json` gives machine-readable output, and `speechloom setup
clean --all` removes download caches.

## Web interface

If you'd rather not use a terminal:

```bash
pipx install "speechloom[api]"
speechloom gui
```

This opens Speechloom in your browser. You can drop in files, watch progress, fix
mistakes in the transcript while the audio or video plays, download the results, see
what's using disk space, and install or repair the engine and models. It only listens on
your own computer. See the
[web interface guide](https://github.com/almajd3713/speechloom/blob/main/docs/web-ui.md).

## Python and HTTP APIs

```python
from pathlib import Path
from speechloom import TranscriptionRequest, TranscriptionService

service = TranscriptionService.from_default_config()
for result in service.transcribe(TranscriptionRequest(inputs=(Path("interview.mp4"),))):
    print(result.state, result.job_dir)
```

The [Python API guide](https://github.com/almajd3713/speechloom/blob/main/docs/python-api.md)
covers progress events, cancellation and background jobs.

`speechloom serve --allow-root ~/Videos` starts a local HTTP API on
`http://127.0.0.1:8765` with uploads, server-sent progress events and interactive docs
at `/docs`. See the
[HTTP API guide](https://github.com/almajd3713/speechloom/blob/main/docs/http-api.md).

## How it works

FFmpeg converts the audio to 16 kHz mono WAV, Parakeet turns it into words with
timestamps, and Speechloom saves that as `transcript.json` and renders the other
formats from it. Each job's manifest records the inputs, models, settings and tool
versions, so you can always tell how a result was made.

Settings come from command-line options, then `SPEECHLOOM_*` environment variables, then
`~/.config/speechloom/config.ini`. Setup remembers where it put the engine and models,
so the file only needs what you want to change. There's an
[example](https://github.com/almajd3713/speechloom/blob/main/config.example.ini).

## Limitations

- Arabic speech isn't recognized (Arabic works as a translation target).
- The engine doesn't report the detected language or per-word confidence, so those
  fields are left empty or set to 1.0.
- Prebuilt engines exist for Linux on x86_64 only. Anything else, including Vulkan,
  needs `speechloom setup --from-source`, which requires CMake, Ninja and a C++17
  compiler (plus `nvcc` for CUDA).

## More

[Troubleshooting](https://github.com/almajd3713/speechloom/blob/main/docs/operations/troubleshooting.md) ·
[Contributing](https://github.com/almajd3713/speechloom/blob/main/CONTRIBUTING.md) ·
[Changelog](https://github.com/almajd3713/speechloom/blob/main/CHANGELOG.md) ·
[Security](https://github.com/almajd3713/speechloom/blob/main/SECURITY.md)

## License

MIT. Models keep their publishers' licenses: Parakeet and Sortformer are CC BY 4.0,
Riva Translate is under the NVIDIA Open Model License, and NeMo-Speech.cpp is Apache 2.0.
