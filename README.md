# Speechloom

Local audio and video transcription using
[NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp) and Parakeet TDT
0.6B v3. Outputs JSON, plain text, SRT, and WebVTT.

## Requirements

- Python 3.10+
- FFmpeg and FFprobe
- A supported native runtime profile for managed setup
- Git, a C++17 compiler, Ninja, and CMake 3.26–3.x only for source builds
- CUDA and `nvcc` only for CUDA source builds

## Setup

Install the Python package from PyPI:

```bash
pipx install speechloom
# or: python3 -m pip install speechloom
```

Install the runtime and ASR model:

```bash
speechloom setup
```

Speechloom selects a usable backend from the capabilities available on the host.
You can also select one explicitly:

```bash
speechloom setup --backend cuda
```

To install translation support (about 16 GiB of free space is needed during
conversion):

```bash
speechloom setup --backend cuda --features translation
```

Speaker diarization uses the pinned four-speaker Sortformer model:

```bash
speechloom setup --features diarization
speechloom transcribe meeting.mp4 --diarize
```

Multiple optional features can be selected with
`--features translation,diarization`.

Setup uses the platform's standard per-user configuration, data, and cache
directories. Existing repository-local `.runtime` assets are imported in
place; they are not moved or deleted.

Portable installations can set `SPEECHLOOM_CONFIG_HOME`,
`SPEECHLOOM_DATA_HOME`, and `SPEECHLOOM_CACHE_HOME` explicitly.

Check setup state or remove setup caches with:

```bash
speechloom setup status
speechloom setup clean --all
```

The [repository-local setup](docs/operations/repository-local-setup.md) remains
available for development and troubleshooting.

## Usage

Check the installation:

```bash
speechloom doctor
```

Transcribe one or more files:

```bash
speechloom transcribe recording.mp4
speechloom transcribe recordings/ --recursive --workers 2
speechloom transcribe recording.mp4 --output-dir ./output
```

To translate a transcript, install the translation feature and provide the
source and target languages:

```bash
speechloom transcribe russian.mp4 --source-language ru --translate-to en
```

The original files remain `transcript.*` and `subtitles.*`. Translated files are
named `translation.en.*` and `subtitles.en.*`.

Inspect a completed job:

```bash
speechloom inspect transcripts/<job-directory>
```

Each job contains a manifest, canonical `transcript.json`, and the requested
text and subtitle formats. Completed jobs are reused unless `--force` is set.

Run `speechloom transcribe --help` for all options.

## Local API

Install the optional server dependencies and allow the directories a desktop
client may submit:

```bash
python3 -m pip install -e ".[api]"
speechloom serve --allow-root /path/to/media
```

The API listens on `127.0.0.1:8765`; OpenAPI documentation is available at
`/docs`. Remote binding requires `--allow-remote` and a
`SPEECHLOOM_API_TOKEN` bearer token.

## Configuration

Settings are read in this order:

1. command-line options
2. `SPEECHLOOM_*` environment variables
3. the selected INI file
4. defaults

The default file is in the platform's standard user configuration directory.
`--config` still selects an explicit file. See `config.example.ini` for
available settings.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Real-runtime tests are enabled by setting `SPEECHLOOM_TEST_NEMO`,
`SPEECHLOOM_TEST_MODEL`, and `SPEECHLOOM_TEST_MEDIA`.

## License

The project is licensed under MIT. Models are distributed separately
under their respective licenses.
