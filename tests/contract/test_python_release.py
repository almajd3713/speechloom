from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PythonReleaseContractTests(unittest.TestCase):
    def test_package_ci_covers_supported_versions_and_isolated_installs(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/python-package.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('python-version: ["3.10", "3.14"]', workflow)
        self.assertIn("python scripts/validate_distribution.py", workflow)
        self.assertIn("python -m twine check", workflow)
        self.assertIn("-py3-none-any", (
            PROJECT_ROOT / "scripts/validate_distribution.py"
        ).read_text(encoding="utf-8"))
        self.assertIn("--no-index --no-deps", workflow)
        self.assertIn("dist/*.tar.gz", workflow)
        self.assertIn(".[api,test]", workflow)
        self.assertIn("speechloom serve --help", workflow)
        self.assertIn("python -m speechloom --help", workflow)
        self.assertIn("scripts/verify_offline_import.py", workflow)
        self.assertEqual(workflow.count("test ! -e /tmp/speechloom-"), 3)
        self.assertEqual(workflow.count("export SPEECHLOOM_CONFIG_HOME="), 3)

    def test_release_uses_separate_trusted_publishing_jobs(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("environment:\n      name: testpypi", workflow)
        self.assertIn("environment:\n      name: pypi", workflow)
        self.assertEqual(workflow.count("id-token: write"), 2)
        self.assertIn("pypa/gh-action-pypi-publish@release/v1", workflow)
        self.assertIn("repository-url: https://test.pypi.org/legacy/", workflow)
        self.assertIn("Verify tag matches the package version", workflow)
        self.assertIn('python-version: ["3.10", "3.14"]', workflow)
        self.assertIn("needs: test", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("GH_REPO: ${{ github.repository }}", workflow)
        self.assertIn("SHA256SUMS", workflow)

    def test_release_preparation_updates_versions_and_selects_a_runtime(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/prepare-release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("package_version:", workflow)
        self.assertIn("runtime_version:", workflow)
        self.assertIn("default: latest", workflow)
        self.assertIn("auto_merge:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn('startswith("runtime-v")', workflow)
        self.assertIn("gh release download", workflow)
        self.assertIn("sha256sum --check --strict", workflow)
        self.assertIn("scripts/verify_runtime_archive.py", workflow)
        self.assertIn("scripts/update_runtime_registry.py", workflow)
        self.assertEqual(workflow.count('scripts/version.py set "$PACKAGE_VERSION"'), 2)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("git ls-remote --exit-code --tags", workflow)
        self.assertIn("git ls-remote --exit-code --heads", workflow)
        self.assertIn("gh pr list", workflow)
        self.assertIn("gh pr create", workflow)

        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("prepare-release:", makefile)
        self.assertIn('package_version="$(VERSION)"', makefile)
        self.assertIn('runtime_version="$(RUNTIME_VERSION)"', makefile)
        self.assertIn('auto_merge="$(AUTO_MERGE)"', makefile)

    def test_release_preparation_can_request_auto_merge_without_hiding_failure(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/prepare-release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("if: inputs.auto_merge", workflow)
        self.assertIn('gh pr merge "$RELEASE_PR_URL" --auto "$merge_flag"', workflow)
        self.assertIn("allow_merge_commit", workflow)
        self.assertIn("allow_squash_merge", workflow)
        self.assertIn("allow_rebase_merge", workflow)
        self.assertIn("The pull request remains open for manual review and merge", workflow)

    def test_typed_marker_and_registry_are_declared_as_package_data(self) -> None:
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('speechloom = ["data/*.json", "py.typed"]', pyproject)
        self.assertTrue((PROJECT_ROOT / "src/speechloom/py.typed").is_file())

    def test_package_version_is_semantic_and_synchronized(self) -> None:
        import re

        from speechloom import __version__

        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        match = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
        self.assertIsNotNone(match)
        assert match is not None
        version = match.group(1)
        self.assertRegex(version, re.compile(r"\d+\.\d+\.\d+(?:[a-z]+\d+)?"))
        self.assertEqual(__version__, version)


if __name__ == "__main__":
    unittest.main()
