from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from speechloom.cli import main
from speechloom.jobs import MANIFEST_SCHEMA_VERSION, inspect_job


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_REPEATED_ALIAS_METAVAR = re.compile(
    r"(?P<short>-[A-Za-z0-9]) (?P<metavar>[A-Z][A-Z0-9_]*), "
    r"(?P<long>--[A-Za-z0-9][A-Za-z0-9-]*) (?P=metavar)"
)


def _normalize_help(text: str) -> str:
    flattened = " ".join(text.split())
    return _REPEATED_ALIAS_METAVAR.sub(
        r"\g<short>, \g<long> \g<metavar>",
        flattened,
    )


class CliCompatibilityTests(unittest.TestCase):
    def test_help_output_matches_public_snapshots(self) -> None:
        snapshots = {
            (): "root.txt",
            ("doctor",): "doctor.txt",
            ("transcribe",): "transcribe.txt",
            ("inspect",): "inspect.txt",
            ("setup",): "setup.txt",
            ("serve",): "serve.txt",
        }

        for command, filename in snapshots.items():
            with self.subTest(command=command or ("root",)):
                output = io.StringIO()
                with patch.dict(os.environ, {"COLUMNS": "80"}):
                    with redirect_stdout(output):
                        with self.assertRaises(SystemExit) as raised:
                            main([*command, "--help"])
                self.assertEqual(raised.exception.code, 0)
                expected = (FIXTURES / "cli" / filename).read_text(encoding="utf-8")
                # Argparse wrapping and repeated alias metavars vary between Python
                # releases; option names, metavars, and help text remain the contract.
                self.assertEqual(
                    _normalize_help(output.getvalue()),
                    _normalize_help(expected),
                )

    def test_help_normalization_preserves_alias_semantics(self) -> None:
        legacy = "-o DIRECTORY, --output-dir DIRECTORY Write results"
        current = "-o, --output-dir DIRECTORY Write results"

        self.assertEqual(_normalize_help(legacy), _normalize_help(current))
        self.assertNotEqual(
            _normalize_help(legacy),
            _normalize_help("-o, --output DIRECTORY Write results"),
        )


class ManifestV1CompatibilityTests(unittest.TestCase):
    def test_v1_manifests_remain_inspectable(self) -> None:
        expected_states = {
            "v1-completed.json": ("completed", "completed"),
            "v1-interrupted.json": ("interrupted", "normalized"),
        }

        self.assertEqual(MANIFEST_SCHEMA_VERSION, 1)
        for filename, states in expected_states.items():
            with self.subTest(filename=filename):
                manifest = inspect_job(FIXTURES / "manifests" / filename)
                self.assertEqual(manifest["schema_version"], 1)
                self.assertEqual((manifest["state"], manifest["state_detail"]), states)
                self.assertEqual(manifest["options"]["transcript_schema"], 1)
                self.assertIn("sha256", manifest["source"])
                self.assertIn("sha256", manifest["model"])

    def test_completed_v1_artifact_names_are_stable(self) -> None:
        manifest = inspect_job(FIXTURES / "manifests" / "v1-completed.json")

        self.assertEqual(
            set(manifest["artifacts"]),
            {
                "json",
                "txt",
                "srt",
                "vtt",
                "translation.en.json",
                "translation.en.txt",
                "subtitles.en.srt",
                "subtitles.en.vtt",
            },
        )
        self.assertEqual(manifest["translation"]["source_language"], "ru")
        self.assertEqual(manifest["translation"]["target_language"], "en")


if __name__ == "__main__":
    unittest.main()
