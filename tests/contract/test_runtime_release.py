from __future__ import annotations

from pathlib import Path
import unittest

from speechloom.process import run_command
from speechloom.registry import Registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RuntimeReleaseContractTests(unittest.TestCase):
    def test_workflow_builds_cpu_and_cuda_from_the_pinned_revision(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/runtime-release.yml").read_text(
            encoding="utf-8"
        )
        revision = Registry.load().runtime.revision

        self.assertIn(f"RUNTIME_REVISION: {revision}", workflow)
        self.assertIn("--backend cpu", workflow)
        self.assertIn("--backend cuda", workflow)
        self.assertIn("--with-nmt", workflow)
        self.assertIn("--skip-gpu-check", workflow)
        self.assertIn("scripts/package_runtime.py", workflow)
        self.assertIn("scripts/update_runtime_registry.py", workflow)
        self.assertIn("scripts/verify_runtime_archive.py", workflow)
        self.assertIn("needs: [cpu, cuda, verify]", workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("actions/download-artifact@v8", workflow)
        self.assertEqual(workflow.count("python3-venv"), 2)
        self.assertIn("Open registry promotion pull request", workflow)
        self.assertIn("gh pr create", workflow)

    def test_cuda_gpu_probe_bypass_is_an_explicit_build_option(self) -> None:
        result = run_command(
            ["bash", str(PROJECT_ROOT / "scripts/bootstrap_runtime.sh"), "--help"]
        )

        self.assertIn("--skip-gpu-check", result.stdout)


if __name__ == "__main__":
    unittest.main()
