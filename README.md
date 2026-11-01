# Speechloom

[![PyPI](https://img.shields.io/pypi/v/speechloom)](https://pypi.org/project/speechloom/)
[![Python](https://img.shields.io/pypi/pyversions/speechloom)](https://pypi.org/project/speechloom/)
[![CI](https://github.com/almajd3713/speechloom/actions/workflows/python-package.yml/badge.svg)](https://github.com/almajd3713/speechloom/actions/workflows/python-package.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/almajd3713/speechloom/blob/main/LICENSE)

Offline speech-to-text for audio and video, using NVIDIA Parakeet via
[NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp). Outputs TXT, SRT, WebVTT
and JSON. Runs on an NVIDIA GPU or the CPU.

## Install

Requires Linux x86_64 (WSL2 works), Python 3.10+ and FFmpeg. For GPU use, an NVIDIA
driver with CUDA 12 support.

```bash
pipx install speechloom
speechloom setup    # downloads the engine and model (~1.5 GB), once
```

## Usage

```bash
speechloom transcribe interview.mp4
speechloom transcribe recordings/ --recursive --formats srt,txt
speechloom transcribe meeting.mp4 --diarize          # needs: setup --features diarization
speechloom transcribe talk.mp4 --source-language ru --translate-to en   # needs: setup --features translation
```

Output goes to `transcripts/<name>-<job id>/`: `transcript.txt`, `subtitles.srt`,
`subtitles.vtt`, `transcript.json` (word timestamps) and `manifest.json`. Finished jobs
are skipped and interrupted ones resume.

`speechloom doctor` checks your setup. `speechloom gui` starts a local web interface
(`pipx install "speechloom[api]"`).

## Docs

[Reference](docs/reference.md) ·
[Web interface](docs/web-ui.md) ·
[Python API](docs/python-api.md) ·
[HTTP API](docs/http-api.md) ·
[Troubleshooting](docs/operations/troubleshooting.md) ·
[Changelog](CHANGELOG.md)

## License

MIT. Models keep their own licenses: Parakeet and Sortformer are CC BY 4.0, Riva
Translate is under the NVIDIA Open Model License, and NeMo-Speech.cpp is Apache 2.0.
