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
        self.assertIn("SHA256SUMS", workflow)

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
