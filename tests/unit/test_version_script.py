from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.version import VersionError, current_version, set_version


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class VersionScriptTests(unittest.TestCase):
    def test_makefile_exposes_version_commands(self) -> None:
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("version:", makefile)
        self.assertIn("check-version:", makefile)
        self.assertIn("set-version:", makefile)
        self.assertIn('$(VERSION_SCRIPT) set "$(VERSION)"', makefile)

    def test_reads_and_updates_both_version_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pyproject, package_init = _version_files(Path(directory))

            self.assertEqual(current_version(pyproject, package_init), "0.1.0")
            self.assertEqual(
                set_version("0.2.0rc1", pyproject, package_init),
                ("0.1.0", "0.1.0"),
            )
            self.assertEqual(current_version(pyproject, package_init), "0.2.0rc1")
            self.assertIn('version = "0.2.0rc1"', pyproject.read_text(encoding="utf-8"))
            self.assertIn(
                '__version__ = "0.2.0rc1"',
                package_init.read_text(encoding="utf-8"),
            )

    def test_rejects_invalid_versions_without_modifying_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pyproject, package_init = _version_files(Path(directory))
            before = (
                pyproject.read_text(encoding="utf-8"),
                package_init.read_text(encoding="utf-8"),
            )

            with self.assertRaisesRegex(VersionError, "Invalid package version"):
                set_version("v0.2", pyproject, package_init)

            self.assertEqual(pyproject.read_text(encoding="utf-8"), before[0])
            self.assertEqual(package_init.read_text(encoding="utf-8"), before[1])

    def test_reports_existing_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pyproject, package_init = _version_files(Path(directory))
            package_init.write_text('__version__ = "0.1.1"\n', encoding="utf-8")

            with self.assertRaisesRegex(VersionError, "not synchronized"):
                current_version(pyproject, package_init)


def _version_files(root: Path) -> tuple[Path, Path]:
    pyproject = root / "pyproject.toml"
    package_init = root / "__init__.py"
    pyproject.write_text(
        '[project]\nname = "speechloom"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    package_init.write_text('__version__ = "0.1.0"\n', encoding="utf-8")
    return pyproject, package_init


if __name__ == "__main__":
    unittest.main()
