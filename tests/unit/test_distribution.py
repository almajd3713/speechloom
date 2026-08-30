from __future__ import annotations

import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from scripts.validate_distribution import DistributionError, validate_distributions


class DistributionValidationTests(unittest.TestCase):
    def test_accepts_pure_typed_side_effect_free_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "speechloom-0.1.0-py3-none-any.whl"
            sdist = root / "speechloom-0.1.0.tar.gz"
            self._wheel(wheel)
            self._sdist(sdist)

            self.assertEqual(validate_distributions((wheel, sdist)), (wheel, sdist))

    def test_rejects_private_or_native_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "speechloom-0.1.0-py3-none-any.whl"
            sdist = root / "speechloom-0.1.0.tar.gz"
            self._wheel(wheel, extra={"speechloom/libnative.so": b"native"})
            self._sdist(sdist)

            with self.assertRaisesRegex(DistributionError, "native/model/cache"):
                validate_distributions((wheel, sdist))

    @staticmethod
    def _wheel(path: Path, *, extra: dict[str, bytes] | None = None) -> None:
        members = {
            "speechloom/__init__.py": b"",
            "speechloom/__main__.py": b"",
            "speechloom/cli.py": b"",
            "speechloom/api/server.py": b"",
            "speechloom/data/registry.json": b"{}",
            "speechloom/py.typed": b"",
            "speechloom-0.1.0.dist-info/licenses/LICENSE": b"MIT",
            "speechloom-0.1.0.dist-info/METADATA": (
                b"Name: speechloom\nVersion: 0.1.0\nRequires-Python: >=3.10\n"
                b"Requires-Dist: fastapi; extra == \"api\"\n"
            ),
            "speechloom-0.1.0.dist-info/WHEEL": (
                b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
            ),
            "speechloom-0.1.0.dist-info/entry_points.txt": (
                b"[console_scripts]\nspeechloom = speechloom.cli:main\n"
            ),
        }
        members.update(extra or {})
        with zipfile.ZipFile(path, mode="w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)

    @staticmethod
    def _sdist(path: Path) -> None:
        names = {
            "LICENSE": b"MIT",
            "README.md": b"Speechloom",
            "pyproject.toml": b"[project]",
            "scripts/bootstrap_runtime.sh": b"#!/bin/sh",
            "src/speechloom/api/server.py": b"",
            "src/speechloom/data/registry.json": b"{}",
            "src/speechloom/py.typed": b"",
        }
        with tarfile.open(path, mode="w:gz") as archive:
            for name, payload in names.items():
                member = tarfile.TarInfo(f"speechloom-0.1.0/{name}")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))


if __name__ == "__main__":
    unittest.main()
