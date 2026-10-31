# Troubleshooting

Problems with `speechloom gui` are covered in the
[web interface guide](../web-ui.md#when-something-goes-wrong).

## `speechloom transcribe` says no ASR model is configured

Run `speechloom setup`, then `speechloom setup status`. Status must print `Ready.`; each
issue it lists names the missing or changed file.

## Setup installed the CPU engine on a machine with an NVIDIA GPU

Setup chooses the CUDA engine when `nvidia-smi` works in the same shell. Run
`nvidia-smi`; on WSL2 the driver is installed on Windows, not inside WSL. Once it works,
run `speechloom setup --backend cuda`.

## CUDA fails at run time, or `doctor` reports a CUDA error

The prebuilt CUDA engine bundles CUDA 12.6 libraries and needs a driver that supports
CUDA 12. Update the NVIDIA driver, or switch to the CPU engine with
`speechloom setup --backend cpu`.

## Setup reports "Existing artifact checksum does not match"

A file with the expected name but different contents is in the way, usually a damaged
download. Delete the file named in the message and run `speechloom setup` again.

## Translation setup fails or takes a long time

Translation setup downloads the original Riva Translate 4B checkpoint and converts it on
your machine. It needs `git`, Python's `venv` module (`sudo apt install python3-venv` on
Ubuntu) and 16 GB of free space. A rerun reuses the downloaded checkpoint.

## Translation asks for a source language

Parakeet does not report which language it heard, so `--translate-to` needs
`--source-language`. Use codes such as `en`, `ru`, `ar`, `zh-cn` or `pt-br`.

## The wrong language appears in the transcript

Parakeet v3 recognizes 25 European languages and does not accept a language hint. Check
that the recording is in one of them and that the configured model is v3.

## A media file has no audio

Inspect its streams with:

```bash
ffprobe -v error -show_streams input-file
```

Speechloom rejects video-only media before inference. Use `--audio-stream N` to pick a
different audio stream.

## A batch item fails

The other jobs in the batch still complete. Inspect the failed job's `manifest.json`, or
run `speechloom inspect <job-dir> --json`, fix the cause and rerun; finished jobs are
skipped.

## The local API answers 403

`origin_not_allowed` means a browser page from another origin made the request. Start
the server with `--allow-origin <origin>` for a front end you trust.
`host_not_allowed` means the request did not address `127.0.0.1`, `localhost` or
`[::1]` on the server's port.

## Port 8765 is already in use

Start the API on another port, such as `speechloom serve --port 8766`.

## Source builds

`speechloom setup --from-source` compiles NeMo-Speech.cpp. It needs Git, a C++17
compiler, Ninja and CMake 3.26 through 3.x; CMake 4.x rejects the policy floor of a
pinned dependency. When the system CMake does not fit, setup installs a compatible
CMake and Ninja into its cache. CUDA source builds also need `nvcc`.

## NeMo native exit codes

The adapter keeps NeMo's distinction between runtime failures, invalid arguments,
missing models and unsupported features. The job manifest records the exception type and
the diagnostic message.
