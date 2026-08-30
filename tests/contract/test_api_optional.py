from __future__ import annotations

import subprocess
import sys
import unittest


class OptionalApiDependencyTests(unittest.TestCase):
    def test_core_package_does_not_import_optional_api_dependencies(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, speechloom; "
                "assert not any(name == 'fastapi' or name.startswith('fastapi.') "
                "for name in sys.modules)",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
