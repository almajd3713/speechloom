# Speechloom

Local audio and video transcription using
[NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp) and Parakeet TDT
0.6B v3. Outputs JSON, plain text, SRT, and WebVTT.

## Requirements

- Python 3.10+
- FFmpeg and FFprobe
- Git, a C++17 compiler, Ninja, and CMake 3.26–3.x
- CUDA and `nvcc` for GPU builds

## Setup

Install the Python package:

```bash
python3 -m pip install --user -e .
```

Install the repository-local build tools, build NeMo-Speech.cpp, and download
the model:

```bash
./scripts/bootstrap_build_tools.sh
./scripts/bootstrap_runtime.sh --backend cpu
./scripts/download_model.sh
```

For CUDA:

```bash
./scripts/bootstrap_runtime.sh \
  --backend cuda \
  --prefix .runtime/nemo-speech-cuda
```

To enable local translation, build the runtime with NMT and convert the pinned
Riva Translate model (the conversion needs about 16 GiB free):

```bash
./scripts/bootstrap_runtime.sh \
  --backend cuda \
  --prefix .runtime/nemo-speech-cuda \
  --with-nmt
./scripts/bootstrap_translation_model.sh
```

Copy `config.example.ini` and set `nemo_speech`, `model`, and `device` for the
runtime you built.

The complete repository-local procedure is retained as the
[setup fallback](docs/operations/repository-local-setup.md) during the managed-setup
migration.

## Usage

Check the installation:

```bash
speechloom --config config.ini doctor
```

Transcribe one or more files:

```bash
speechloom --config config.ini transcribe recording.mp4
speechloom --config config.ini transcribe recordings/ --recursive --workers 2
speechloom --config config.ini transcribe recording.mp4 --output-dir ./output
```

For transcribing between different languages, set the translation model and source/target languages in `config.ini`:

```ini
translation_model = .runtime/models/riva-translate-4b-instruct-v2.q8_0.gguf
source_language = ru
translate_to = en
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

## Configuration

Settings are read in this order:

1. command-line options
2. `SPEECHLOOM_*` environment variables
3. the selected INI file
4. defaults

See `config.example.ini` for available settings.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Real-runtime tests are enabled by setting `SPEECHLOOM_TEST_NEMO`,
`SPEECHLOOM_TEST_MODEL`, and `SPEECHLOOM_TEST_MEDIA`.

## License

The project is licensed under Apache-2.0. Models are distributed separately
under their respective licenses.
