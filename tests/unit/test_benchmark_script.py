from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from scripts.benchmark import main, speed_label, summarize_runs


def _result(backend: str, **overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "backend": backend,
        "speechloom_version": "speechloom 0.1.2",
        "audio_seconds": 602.36,
        "runs": [
            {"total_seconds": 4.0, "inference_seconds": 2.0, "normalization_seconds": 0.1}
            for _ in range(3)
        ],
        "median_total_seconds": 4.0,
        "median_inference_seconds": 2.0,
        "speed": 150.59,
        "peak_gpu_memory_mib": 1200,
        "gpu": "NVIDIA GeForce RTX 4070 Laptop GPU, 617.14",
        "cpu": "Fixture CPU",
        "cpu_threads": 20,
        "platform": "Linux-fixture",
    }
    result.update(overrides)
    return result


class BenchmarkScriptTests(unittest.TestCase):
    def test_speed_label_rounds_by_magnitude(self) -> None:
        self.assertEqual(speed_label(157.43), "157")
        self.assertEqual(speed_label(10.0), "10")
        self.assertEqual(speed_label(3.24), "3.2")

    def test_summarize_runs_uses_medians(self) -> None:
        runs = [
            {"total_seconds": total, "inference_seconds": inference}
            for total, inference in ((5.0, 2.0), (4.0, 1.0), (6.0, 3.0))
        ]

        self.assertEqual(
            summarize_runs(runs, 600.0),
            {"median_total_seconds": 5.0, "median_inference_seconds": 2.0, "speed": 120.0},
        )

    def test_render_writes_markdown_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cuda = _result("cuda")
            cpu = _result(
                "cpu",
                audio_seconds=123.21,
                runs=cuda["runs"][:1],
                median_total_seconds=100.0,
                median_inference_seconds=90.0,
                speed=1.2321,
                peak_gpu_memory_mib=None,
                gpu=None,
            )
            (root / "cuda.json").write_text(json.dumps(cuda), encoding="utf-8")
            (root / "cpu.json").write_text(json.dumps(cpu), encoding="utf-8")

            exit_code = main(
                [
                    "render",
                    "--cuda", str(root / "cuda.json"),
                    "--cpu", str(root / "cpu.json"),
                    "--markdown", str(root / "docs/benchmarks.md"),
                    "--json", str(root / "docs/benchmarks.json"),
                ]
            )

            self.assertEqual(exit_code, 0)
            markdown = (root / "docs/benchmarks.md").read_text(encoding="utf-8")
            self.assertIn(
                "| CUDA | NVIDIA GeForce RTX 4070 Laptop GPU (driver 617.14) | 602.4 s | 3 "
                "| 4.0 s | 2.0 s | 151× real time |",
                markdown,
            )
            self.assertIn(
                "| CPU | Fixture CPU (20 threads) | 123.2 s | 1 | 100.0 s | 90.0 s "
                "| 1.2× real time |",
                markdown,
            )
            summary = json.loads((root / "docs/benchmarks.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["cuda"]["speed_label"], "151")
            self.assertEqual(summary["cuda"]["ten_minute_seconds_label"], "4")
            self.assertEqual(summary["cpu"]["ten_minute_seconds_label"], "487")

    def test_render_rejects_swapped_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cpu.json").write_text(json.dumps(_result("cpu")), encoding="utf-8")

            exit_code = main(
                [
                    "render",
                    "--cuda", str(root / "cpu.json"),
                    "--cpu", str(root / "cpu.json"),
                    "--markdown", str(root / "benchmarks.md"),
                    "--json", str(root / "benchmarks.json"),
                ]
            )

            self.assertEqual(exit_code, 2)
            self.assertFalse((root / "benchmarks.md").exists())
            self.assertFalse((root / "benchmarks.json").exists())


if __name__ == "__main__":
    unittest.main()
