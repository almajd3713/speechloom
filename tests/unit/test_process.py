from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from speechloom import CancellationController
from speechloom.errors import CancellationError, PipelineError
from speechloom.process import run_command


def _process_is_running(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    fields = stat.split()
    return len(fields) > 2 and fields[2] != "Z"


class ManagedProcessTests(unittest.TestCase):
    def test_streams_both_outputs_and_bounds_retained_diagnostics(self) -> None:
        streamed: list[tuple[str, str]] = []

        result = run_command(
            [
                sys.executable,
                "-c",
                "import sys; print('x' * 40); print('err', file=sys.stderr)",
            ],
            on_output=lambda name, text: streamed.append((name, text)),
            max_output_chars=16,
        )

        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stdout), 16)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stderr, "err\n")
        self.assertEqual({name for name, _ in streamed}, {"stdout", "stderr"})

    def test_timeout_terminates_the_process(self) -> None:
        started = time.monotonic()

        with self.assertRaisesRegex(PipelineError, "timed out"):
            run_command(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                timeout=0.1,
                termination_grace=0.1,
            )

        self.assertLess(time.monotonic() - started, 2.0)

    @unittest.skipUnless(os.name == "posix" and Path("/proc").is_dir(), "requires Linux")
    def test_cancellation_terminates_the_real_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            child_pid_file = Path(directory) / "child.pid"
            controller = CancellationController()
            outcome: list[BaseException] = []
            script = (
                "import pathlib, subprocess, sys, time; "
                "child = subprocess.Popen([sys.executable, '-c', "
                "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
                "time.sleep(60)"
            )

            def invoke() -> None:
                try:
                    run_command(
                        [sys.executable, "-c", script, str(child_pid_file)],
                        cancellation=controller,
                        termination_grace=0.1,
                    )
                except BaseException as exc:
                    outcome.append(exc)

            worker = threading.Thread(target=invoke)
            worker.start()
            deadline = time.monotonic() + 5
            while not child_pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(child_pid_file.exists(), "fixture child did not start")
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))

            controller.cancel()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(outcome), 1)
            self.assertIsInstance(outcome[0], CancellationError)
            deadline = time.monotonic() + 2
            while _process_is_running(child_pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(_process_is_running(child_pid))


if __name__ == "__main__":
    unittest.main()
