# Repository-local setup

This development setup keeps the native runtime, models and build tools inside the
checkout (`.runtime/`) and does not touch the per-user Speechloom directories.

## Install

From the repository root, install the editable Python package and local build tools:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
./scripts/bootstrap_build_tools.sh
```

Build one runtime profile and download the pinned ASR model:

```bash
./scripts/bootstrap_runtime.sh --backend cpu
./scripts/download_model.sh
```

For CUDA with translation support:

```bash
./scripts/bootstrap_runtime.sh \
  --backend cuda \
  --prefix .runtime/nemo-speech-cuda \
  --with-nmt
./scripts/download_model.sh
./scripts/bootstrap_translation_model.sh
```

Copy `config.example.ini` to the ignored `config.ini`, then point `nemo_speech`,
`model`, `translation_model` when used, and `device` at the selected repository-local
runtime and models.
Use absolute paths, because relative paths in a configuration file are resolved from
the directory you run Speechloom in.

## Verify and use

```bash
.venv/bin/speechloom --config config.ini doctor
.venv/bin/speechloom --config config.ini transcribe recording.mp4
```

Rerunning a bootstrap script is safe for its pinned target. When `speechloom setup`
runs from an editable install of this checkout, it imports the `.runtime` engine and
models in place and never moves or deletes them. To use another checkout's scripts,
set `SPEECHLOOM_REPOSITORY_ROOT` to its path; setup never uses scripts from the
current working directory. Existing transcript job directories remain independent of
the installation method.
