"""Safe external-process execution."""

from __future__ import annotations

import codecs
from collections import deque
from dataclasses import dataclass
import os
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping, Sequence

from .contracts import CancellationToken
from .errors import CancellationError, MissingDependencyError, PipelineError


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandFailed(PipelineError):
    def __init__(self, result: CommandResult, message: str | None = None) -> None:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        super().__init__(message or f"Command failed ({result.returncode}): {detail}")
        self.result = result


class _BoundedText:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._chunks: deque[str] = deque()
        self._length = 0
        self._lock = threading.Lock()

    def append(self, value: str) -> None:
        if not value or self._limit == 0:
            return
        with self._lock:
            self._chunks.append(value)
            self._length += len(value)
            while self._length > self._limit and self._chunks:
                excess = self._length - self._limit
                first = self._chunks[0]
                if len(first) <= excess:
                    self._chunks.popleft()
                    self._length -= len(first)
                else:
                    self._chunks[0] = first[excess:]
                    self._length -= excess

    def getvalue(self) -> str:
        with self._lock:
            return "".join(self._chunks)


def _read_stream(
    stream,
    name: str,
    capture: _BoundedText,
    on_output: Callable[[str, str], None] | None,
    callback_lock: threading.Lock,
    errors: list[BaseException],
) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            text = decoder.decode(chunk)
            capture.append(text)
            if on_output is not None and text:
                with callback_lock:
                    on_output(name, text)
        tail = decoder.decode(b"", final=True)
        capture.append(tail)
        if on_output is not None and tail:
            with callback_lock:
                on_output(name, tail)
    except BaseException as exc:  # propagated by the process-owning thread
        errors.append(exc)
    finally:
        stream.close()


def _terminate_process_group(
    process: subprocess.Popen[bytes], grace: float, group_id: int | None
) -> None:
    if os.name == "posix":
        assert group_id is not None
        try:
            os.killpg(group_id, signal.SIGTERM)
        except ProcessLookupError:
            if process.poll() is None:
                process.wait()
            return

        deadline = time.monotonic() + grace
        if process.poll() is None:
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        while time.monotonic() < deadline:
            try:
                os.killpg(group_id, 0)
            except ProcessLookupError:
                break
            remaining = max(0.0, deadline - time.monotonic())
            time.sleep(min(0.02, remaining))
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:  # pragma: no cover - the supported runtime is Linux/WSL
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()

    if process.poll() is None:
        process.wait()


def run_command(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    cancellation: CancellationToken | None = None,
    on_output: Callable[[str, str], None] | None = None,
    max_output_chars: int = 65_536,
    termination_grace: float = 2.0,
) -> CommandResult:
    """Run argv safely while streaming and retaining bounded diagnostic output."""

    if not argv:
        raise ValueError("argv must not be empty")
    if max_output_chars < 0:
        raise ValueError("max_output_chars must not be negative")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive")
    if termination_grace < 0:
        raise ValueError("termination_grace must not be negative")
    normalized = tuple(str(part) for part in argv)
    popen_options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": env,
        "shell": False,
    }
    if os.name == "posix":
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(normalized, **popen_options)  # type: ignore[arg-type]
    except FileNotFoundError as exc:
        raise MissingDependencyError(f"Executable not found: {normalized[0]}") from exc
    group_id = process.pid if os.name == "posix" else None

    assert process.stdout is not None
    assert process.stderr is not None
    stdout = _BoundedText(max_output_chars)
    stderr = _BoundedText(max_output_chars)
    callback_lock = threading.Lock()
    reader_errors: list[BaseException] = []
    readers = (
        threading.Thread(
            target=_read_stream,
            args=(process.stdout, "stdout", stdout, on_output, callback_lock, reader_errors),
            daemon=True,
        ),
        threading.Thread(
            target=_read_stream,
            args=(process.stderr, "stderr", stderr, on_output, callback_lock, reader_errors),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()

    deadline = None if timeout is None else time.monotonic() + timeout
    interruption: PipelineError | None = None
    try:
        while process.poll() is None:
            if reader_errors:
                raise reader_errors[0]
            if cancellation is not None and cancellation.is_cancelled():
                interruption = CancellationError(f"Command cancelled: {normalized[0]}")
                break
            if deadline is not None and time.monotonic() >= deadline:
                interruption = PipelineError(
                    f"Command timed out after {timeout}s: {normalized[0]}"
                )
                break
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        _terminate_process_group(process, termination_grace, group_id)
        raise
    finally:
        if interruption is not None:
            _terminate_process_group(process, termination_grace, group_id)
        else:
            process.wait()
        for reader in readers:
            reader.join()

    if reader_errors:
        raise reader_errors[0]
    if interruption is not None:
        raise interruption

    result = CommandResult(normalized, process.returncode, stdout.getvalue(), stderr.getvalue())
    if check and result.returncode != 0:
        raise CommandFailed(result)
    return result
