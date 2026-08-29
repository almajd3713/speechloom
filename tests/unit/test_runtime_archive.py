from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest

from speechloom.errors import SetupError
from speechloom.process import run_command
from speechloom.registry import Registry
from speechloom.runtime_archive import (
    RuntimeArchiveMetadata,
    build_runtime_archive,
    extract_runtime_archive,
    read_runtime_metadata,
)


class RuntimeArchiveTests(unittest.TestCase):
    def test_build_is_reproducible_and_extracts_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            executable = prefix / "bin/nemo-speech"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\necho fixture\n", encoding="utf-8")
            executable.chmod(0o755)
            license_file = prefix / "share/licenses/nemo-speech/LICENSE"
            license_file.parent.mkdir(parents=True)
            license_file.write_text("fixture license\n", encoding="utf-8")
            library = prefix / "lib/libfixture.so.1"
            library.parent.mkdir()
            library.write_bytes(b"library")
            (library.parent / "libfixture.so").symlink_to(library.name)
            metadata = RuntimeArchiveMetadata(
                backend="cpu",
                system="linux",
                architecture="x86_64",
                revision="a" * 40,
                features=("asr",),
            )
            first = root / "speechloom-runtime-fixture.tar.gz"
            second = root / "copy/speechloom-runtime-fixture.tar.gz"

            first_digest = build_runtime_archive(prefix, first, metadata)
            second_digest = build_runtime_archive(prefix, second, metadata)

            self.assertEqual(first_digest, second_digest)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(read_runtime_metadata(first), metadata)
            installed = extract_runtime_archive(first, root / "installed", metadata)
            self.assertTrue(installed.is_file())
            self.assertTrue(installed.stat().st_mode & 0o100)
            self.assertTrue((root / "installed/lib/libfixture.so").is_symlink())

    def test_rejects_archive_path_traversal_without_creating_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.tar.gz"
            with tarfile.open(archive_path, mode="w:gz") as archive:
                payload = b"escape"
                member = tarfile.TarInfo("runtime/../../escape")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            metadata = RuntimeArchiveMetadata(
                backend="cpu",
                system="linux",
                architecture="x86_64",
                revision="a" * 40,
                features=("asr",),
            )

            with self.assertRaises(SetupError):
                extract_runtime_archive(archive_path, root / "installed", metadata)

            self.assertFalse((root / "installed").exists())
            self.assertFalse((root / "escape").exists())

    def test_release_helper_generates_a_valid_pinned_registry_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            executable = prefix / "bin/nemo-speech"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            license_file = prefix / "share/licenses/nemo-speech/LICENSE"
            license_file.parent.mkdir(parents=True)
            license_file.write_text("fixture license\n", encoding="utf-8")
            registry = Registry.load()
            release_dir = root / "release"
            for backend, features in (
                ("cpu", ("asr",)),
                ("cuda", ("asr", "translation")),
            ):
                build_runtime_archive(
                    prefix,
                    release_dir / f"runtime-{backend}.tar.gz",
                    RuntimeArchiveMetadata(
                        backend=backend,
                        system="linux",
                        architecture="x86_64",
                        revision=registry.runtime.revision,
                        features=features,
                    ),
                )
            registry_path = root / "registry.json"
            source_registry = (
                Path(__file__).resolve().parents[2] / "src/speechloom/data/registry.json"
            )
            shutil.copyfile(source_registry, registry_path)

            run_command(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[2] / "scripts/update_runtime_registry.py"),
                    "--registry",
                    str(registry_path),
                    "--release-dir",
                    str(release_dir),
                    "--release-tag",
                    "runtime-v1",
                    "--repository",
                    "almajd3713/speechloom",
                ]
            )

            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            candidate = Registry.from_dict(payload)
            self.assertEqual(len(candidate.runtime.archives), 2)
            cuda = candidate.runtime_archive(
                "cuda", "linux", "x86_64", ("translation",)
            )
            self.assertIsNotNone(cuda)
            assert cuda is not None
            self.assertIn("/releases/download/runtime-v1/", cuda.url)
            self.assertEqual(len(cuda.sha256), 64)


if __name__ == "__main__":
    unittest.main()
