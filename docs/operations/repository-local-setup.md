# Repository-local setup fallback

This is the fallback installation path for Speechloom while the managed `speechloom
setup` workflow is being introduced. It keeps the native runtime, models, and build
tools inside the checkout and does not modify XDG-managed application data.

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

## Verify and use

```bash
.venv/bin/speechloom --config config.ini doctor
.venv/bin/speechloom --config config.ini transcribe recording.mp4
```

Rerunning a bootstrap script is safe for its pinned target. Do not delete a working
`.runtime` directory during migration; the managed setup workflow must import or coexist
with it non-destructively. Existing transcript job directories remain independent of the
installation method.
