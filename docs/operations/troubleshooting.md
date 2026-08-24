# Troubleshooting

## `doctor` reports that `nemo-speech` is missing

Install CMake 3.26+, Ninja, and a C++17 compiler, then run `scripts/bootstrap_runtime.sh`. Set `nemo_speech` to the resulting absolute executable path.

## CUDA is requested but GPU initialization fails

Run `nvidia-smi` inside WSL. If it fails, update or repair the Windows-side NVIDIA driver with WSL CUDA support, shut WSL down from Windows, restart it, and test again. Do not install a Linux display driver inside WSL.

Until both `nvidia-smi` and `nemo-speech doctor --json` report a usable CUDA device, configure `device = cpu` and build the CPU runtime.

## The runtime bootstrap rejects CMake

NeMo-Speech.cpp requires CMake 3.26 through 3.x. Ubuntu 22.04's default package can be older, while CMake 4.x rejects the policy floor in the pinned SentencePiece dependency. Run `scripts/bootstrap_build_tools.sh` to install the tested CMake and Ninja versions into `.runtime/tools`, then rerun the runtime bootstrap.

## A media file has no audio

Inspect streams with:

```bash
ffprobe -v error -show_streams input-file
```

The pipeline rejects video-only media before inference.

## The wrong language appears in the transcript

Parakeet v3 automatically transcribes its 25 supported European languages and does not accept a language prompt in this runtime. Confirm that the content is in the supported set and that the configured model is v3, not the English-focused v2 model.

## A batch item fails

Successful jobs remain complete. Inspect the failed job's `manifest.json` or run `parakeet-transcribe inspect <job-dir> --json`. Correct the underlying issue and rerun with resume enabled.

## NeMo native exit codes

The adapter preserves NeMo's stable distinction between runtime failures, invalid arguments, missing models, and unsupported features. The job manifest records the exception type and diagnostic message.
