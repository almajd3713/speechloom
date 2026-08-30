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
        self.assertIn("--skip-runtime-check", workflow)
        self.assertIn("scripts/package_runtime.py", workflow)
        self.assertIn("scripts/bundle_cuda_runtime.py", workflow)
        self.assertIn("/NGC-DL-CONTAINER-LICENSE", workflow)
        self.assertIn("/usr/share/doc/libnccl2/copyright", workflow)
        self.assertIn("scripts/update_runtime_registry.py", workflow)
        self.assertEqual(workflow.count("scripts/verify_runtime_archive.py"), 3)
        self.assertIn("needs: [cpu, cuda, verify]", workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertEqual(workflow.count("actions/cache/restore@v5"), 2)
        self.assertEqual(workflow.count("actions/cache/save@v5"), 2)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("actions/download-artifact@v8", workflow)
        self.assertIn("Restore compiled CUDA runtime", workflow)
        self.assertIn("Save compiled CUDA runtime", workflow)
        self.assertIn("Verify CPU runtime archive", workflow)
        self.assertIn("Verify CUDA runtime archive", workflow)
        self.assertLess(
            workflow.index("Package CUDA runtime"),
            workflow.index("Verify CUDA runtime archive"),
        )
        self.assertLess(
            workflow.index("Verify CUDA runtime archive"),
            workflow.index("name: runtime-cuda"),
        )
        self.assertEqual(workflow.count("python3-venv"), 2)
        self.assertIn("Push registry promotion branch", workflow)
        self.assertIn("Open registry promotion pull request", workflow)
        self.assertIn("git ls-remote --exit-code --heads", workflow)
        self.assertIn("cmp -s dist-runtime/registry.json", workflow)
        self.assertIn("gh pr list", workflow)
        self.assertIn("gh pr create", workflow)
        self.assertIn(
            "GitHub Actions is not permitted to create or approve pull requests",
            workflow,
        )
        self.assertIn("Manual registry promotion required", workflow)

    def test_cuda_hardware_checks_can_be_bypassed_explicitly_for_builds(self) -> None:
        result = run_command(
            ["bash", str(PROJECT_ROOT / "scripts/bootstrap_runtime.sh"), "--help"]
        )

        self.assertIn("--skip-gpu-check", result.stdout)
        self.assertIn("--skip-runtime-check", result.stdout)

    def test_managed_setup_smoke_uses_an_installed_wheel(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/setup-smoke.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("python -m build --wheel", workflow)
        self.assertEqual(workflow.count("speechloom-smoke-venv/bin/speechloom"), 8)
        self.assertIn("cd \"$RUNNER_TEMP\"", workflow)
        self.assertIn("setup status --json", workflow)
        self.assertIn("doctor --json", workflow)
        self.assertIn("runs-on: ubuntu-22.04", workflow)
        self.assertIn("runs-on: [self-hosted, Linux, X64, speechloom-cuda]", workflow)
        self.assertIn("nvidia-smi", workflow)


if __name__ == "__main__":
    unittest.main()
