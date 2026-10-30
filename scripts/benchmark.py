#!/usr/bin/env python3
"""Measure Speechloom transcription speed and render docs/benchmarks.md."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import threading
from typing import Any, Sequence


RESULT_SCHEMA_VERSION = 1
GPU_SAMPLE_INTERVAL = 0.2

MARKDOWN_TEMPLATE = """\
# Benchmarks

Measured with `scripts/benchmark.py` using the prebuilt engines that `speechloom setup`
installs. The input is the 13.7-second `english.flac` fixture looped to the length shown.
Times are medians over the listed runs and cover the whole `speechloom transcribe`
command: model loading, audio normalization, inference and writing every output.

| Backend | Hardware | Audio | Runs | Total | Inference | Speed |
|---|---|---|---|---|---|---|
| CUDA | {gpu} | {cuda_audio} s | {cuda_runs} | {cuda_total} s | {cuda_inference} s | {cuda_speed}× real time |
| CPU | {cpu} ({cpu_threads} threads) | {cpu_audio} s | {cpu_runs} | {cpu_total} s | {cpu_inference} s | {cpu_speed}× real time |

Peak additional GPU memory during the CUDA runs: {peak} MiB.

- Speechloom: {version}
- Platform: {platform}

## Reproduce

```bash
ffmpeg -v error -stream_loop 43 -i english.flac -c:a flac bench-10min.flac
ffmpeg -v error -stream_loop 8 -i english.flac -c:a flac bench-2min.flac
python scripts/benchmark.py run --speechloom "$(command -v speechloom)" --backend cuda \\
  --input bench-10min.flac --work-dir /tmp/bench-cuda --output cuda.json
python scripts/benchmark.py run --speechloom "$(command -v speechloom)" --backend cpu \\
  --input bench-2min.flac --work-dir /tmp/bench-cpu --output cpu.json --runs 1
python scripts/benchmark.py render --cuda cuda.json --cpu cpu.json \\
  --markdown docs/benchmarks.md --json docs/benchmarks.json
```

The CPU run needs an installation made with `speechloom setup --backend cpu`, kept apart
from the CUDA one by its own `SPEECHLOOM_CONFIG_HOME`, `SPEECHLOOM_DATA_HOME` and
`SPEECHLOOM_CACHE_HOME`.
"""


class BenchmarkError(RuntimeError):
    """Raised when a benchmark run cannot produce a valid measurement."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def speed_label(speed: float) -> str:
    return f"{speed:.0f}" if speed >= 10 else f"{speed:.1f}"


def summarize_runs(runs: list[dict[str, float]], audio_seconds: float) -> dict[str, float]:
    if not runs:
        raise ValueError("no benchmark runs")
    median_total = statistics.median(run["total_seconds"] for run in runs)
    return {
        "median_total_seconds": median_total,
        "median_inference_seconds": statistics.median(run["inference_seconds"] for run in runs),
        "speed": audio_seconds / median_total,
    }


def render_summary(cuda: dict[str, Any], cpu: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "speechloom_version": cuda["speechloom_version"],
        "platform": cuda["platform"],
        "cuda": {
            **_summary_block(cuda),
            "gpu": cuda["gpu"],
            "peak_gpu_memory_mib": cuda["peak_gpu_memory_mib"],
        },
        "cpu": {
            **_summary_block(cpu),
            "cpu": cpu["cpu"],
            "cpu_threads": cpu["cpu_threads"],
        },
    }


def render_markdown(cuda: dict[str, Any], cpu: dict[str, Any]) -> str:
    gpu = str(cuda["gpu"])
    name, separator, driver = gpu.rpartition(", ")
    peak = cuda["peak_gpu_memory_mib"]
    return MARKDOWN_TEMPLATE.format(
        gpu=f"{name} (driver {driver})" if separator else gpu,
        cpu=cpu["cpu"],
        cpu_threads=cpu["cpu_threads"],
        cuda_audio=f"{cuda['audio_seconds']:.1f}",
        cuda_runs=len(cuda["runs"]),
        cuda_total=f"{cuda['median_total_seconds']:.1f}",
        cuda_inference=f"{cuda['median_inference_seconds']:.1f}",
        cuda_speed=speed_label(cuda["speed"]),
        cpu_audio=f"{cpu['audio_seconds']:.1f}",
        cpu_runs=len(cpu["runs"]),
        cpu_total=f"{cpu['median_total_seconds']:.1f}",
        cpu_inference=f"{cpu['median_inference_seconds']:.1f}",
        cpu_speed=speed_label(cpu["speed"]),
        peak="n/a" if peak is None else peak,
        version=cuda["speechloom_version"],
        platform=cuda["platform"],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        return _render(args)
    except BenchmarkError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="measure one backend")
    run.add_argument("--speechloom", type=Path, required=True)
    run.add_argument("--backend", choices=("cuda", "cpu"), required=True)
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--work-dir", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--runs", type=int, default=3)
    render = commands.add_parser("render", help="render the benchmark documents")
    render.add_argument("--cuda", type=Path, required=True)
    render.add_argument("--cpu", type=Path, required=True)
    render.add_argument("--markdown", type=Path, required=True)
    render.add_argument("--json", type=Path, required=True)
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.runs < 1:
        raise BenchmarkError("benchmark: --runs must be at least 1", 2)
    if args.work_dir.exists() and any(args.work_dir.iterdir()):
        raise BenchmarkError(f"benchmark: work directory must be empty: {args.work_dir}", 2)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    speechloom = str(args.speechloom)
    version = subprocess.run(
        [speechloom, "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    on_gpu = args.backend == "cuda"
    gpu = _first_line(_query_gpu("name,driver_version")) if on_gpu else None
    baseline = int(_first_line(_query_gpu("memory.used", units=False)) or 0) if on_gpu else 0
    sampler = _MemorySampler(baseline) if on_gpu else None
    if sampler is not None:
        sampler.start()
    runs: list[dict[str, float]] = []
    audio_seconds = 0.0
    try:
        for number in range(1, args.runs + 1):
            total, inference, normalization, duration = _run_once(
                speechloom, args.input, args.work_dir / f"run-{number}", number, args.backend
            )
            runs.append(
                {
                    "total_seconds": total,
                    "inference_seconds": inference,
                    "normalization_seconds": normalization,
                }
            )
            if number == 1:
                audio_seconds = duration
    finally:
        if sampler is not None:
            sampler.stop()
    summary = summarize_runs(runs, audio_seconds)
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "backend": args.backend,
        "speechloom_version": version,
        "audio_seconds": audio_seconds,
        "runs": runs,
        **summary,
        "peak_gpu_memory_mib": (
            max(sampler.peak, baseline) - baseline if sampler is not None else None
        ),
        "gpu": gpu,
        "cpu": _cpu_model(),
        "cpu_threads": os.cpu_count() or 0,
        "platform": platform.platform(),
    }
    _write_json(args.output, result)
    print(
        f"{args.backend}: median {summary['median_total_seconds']:.1f} s for "
        f"{audio_seconds:.1f} s of audio ({speed_label(summary['speed'])}x real time)"
    )
    return 0


def _run_once(
    speechloom: str,
    source: Path,
    output_dir: Path,
    number: int,
    backend: str,
) -> tuple[float, float, float, float]:
    completed = subprocess.run(
        [speechloom, "transcribe", str(source), "-o", str(output_dir), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    detail = completed.stderr.strip() or completed.stdout.strip()
    if completed.returncode != 0:
        raise BenchmarkError(f"benchmark: run {number} failed: {detail}")
    try:
        results = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"benchmark: run {number} failed: {detail}") from exc
    if (
        not isinstance(results, list)
        or len(results) != 1
        or results[0].get("state") != "completed"
        or results[0].get("skipped") is not False
        or results[0].get("error") is not None
    ):
        raise BenchmarkError(f"benchmark: run {number} failed: {detail}")
    manifest = json.loads(
        (Path(results[0]["job_dir"]) / "manifest.json").read_text(encoding="utf-8")
    )
    device = manifest["options"]["device"]
    if device != backend:
        raise BenchmarkError(f"benchmark: expected device {backend}, got {device}")
    timings = manifest["timings"]
    return (
        float(timings["total_seconds"]),
        float(timings["inference_seconds"]),
        float(timings.get("normalization_seconds", 0.0)),
        float(manifest["media"]["duration"]),
    )


def _render(args: argparse.Namespace) -> int:
    cuda = json.loads(args.cuda.read_text(encoding="utf-8"))
    cpu = json.loads(args.cpu.read_text(encoding="utf-8"))
    if cuda.get("backend") != "cuda" or cpu.get("backend") != "cpu":
        raise BenchmarkError("benchmark: expected a cuda result and a cpu result", 2)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render_markdown(cuda, cpu), encoding="utf-8")
    _write_json(args.json, render_summary(cuda, cpu))
    return 0


def _summary_block(result: dict[str, Any]) -> dict[str, Any]:
    median_total = result["median_total_seconds"]
    audio_seconds = result["audio_seconds"]
    return {
        "audio_seconds": round(audio_seconds, 2),
        "runs": len(result["runs"]),
        "median_total_seconds": round(median_total, 2),
        "median_inference_seconds": round(result["median_inference_seconds"], 2),
        "speed": round(result["speed"], 1),
        "speed_label": speed_label(result["speed"]),
        "ten_minute_seconds_label": f"{median_total * 600 / audio_seconds:.0f}",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def _query_gpu(fields: str, *, units: bool = True) -> str:
    fmt = "csv,noheader" if units else "csv,noheader,nounits"
    completed = subprocess.run(
        ["nvidia-smi", f"--query-gpu={fields}", f"--format={fmt}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


def _cpu_model() -> str:
    try:
        completed = subprocess.run(["lscpu"], capture_output=True, text=True, check=False)
    except OSError:
        return "unknown"
    for line in completed.stdout.splitlines():
        if line.startswith("Model name:"):
            return line.partition(":")[2].strip() or "unknown"
    return "unknown"


class _MemorySampler:
    def __init__(self, baseline: int) -> None:
        self.peak = baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _sample(self) -> None:
        while not self._stop.wait(GPU_SAMPLE_INTERVAL):
            line = _first_line(_query_gpu("memory.used", units=False))
            if line.isdigit():
                self.peak = max(self.peak, int(line))


if __name__ == "__main__":
    raise SystemExit(main())
