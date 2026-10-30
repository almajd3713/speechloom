# Benchmarks

Measured with `scripts/benchmark.py` using the prebuilt engines that `speechloom setup`
installs. The input is the 13.7-second `english.flac` fixture looped to the length shown.
Times are medians over the listed runs and cover the whole `speechloom transcribe`
command: model loading, audio normalization, inference and writing every output.

| Backend | Hardware | Audio | Runs | Total | Inference | Speed |
|---|---|---|---|---|---|---|
| CUDA | NVIDIA GeForce RTX 4070 Laptop GPU (driver 617.14) | 602.4 s | 3 | 17.5 s | 15.2 s | 34× real time |
| CPU | AMD Ryzen 9 7940HS w/ Radeon 780M Graphics (16 threads) | 123.2 s | 1 | 198.0 s | 196.9 s | 0.6× real time |

Peak additional GPU memory during the CUDA runs: 4063 MiB.

- Speechloom: speechloom 0.1.1
- Platform: Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.35

## Reproduce

```bash
ffmpeg -v error -stream_loop 43 -i english.flac -c:a flac bench-10min.flac
ffmpeg -v error -stream_loop 8 -i english.flac -c:a flac bench-2min.flac
python scripts/benchmark.py run --speechloom "$(command -v speechloom)" --backend cuda \
  --input bench-10min.flac --work-dir /tmp/bench-cuda --output cuda.json
python scripts/benchmark.py run --speechloom "$(command -v speechloom)" --backend cpu \
  --input bench-2min.flac --work-dir /tmp/bench-cpu --output cpu.json --runs 1
python scripts/benchmark.py render --cuda cuda.json --cpu cpu.json \
  --markdown docs/benchmarks.md --json docs/benchmarks.json
```

The CPU run needs an installation made with `speechloom setup --backend cpu`, kept apart
from the CUDA one by its own `SPEECHLOOM_CONFIG_HOME`, `SPEECHLOOM_DATA_HOME` and
`SPEECHLOOM_CACHE_HOME`.
