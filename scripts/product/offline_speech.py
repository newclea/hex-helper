"""Persistent adapter for the bundled offline speech worker."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Sequence


LOGGER = logging.getLogger(__name__)
READY_TIMEOUT_SECONDS = 30.0
CLOSE_TIMEOUT_SECONDS = 2.0
VALID_EVENTS = frozenset({"ready", "started", "finished", "cancelled", "error"})


class OfflineSpeechAdapter:
    def __init__(
        self,
        command: str | Sequence[str] | None = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        bundle_root: Path | None = None,
    ) -> None:
        self._command = command
        self._process_factory = process_factory
        self._bundle_root = bundle_root or Path(__file__).resolve().parents[2]
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._completion = threading.Event()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ready_ok = False
        self._completion_ok = False
        self._closed = False
        self._unavailable = False
        self._failure_logged = False
        self._generation = 0
        self._request_sequence = 0
        self._current_request_id: int | None = None

    def start(self) -> bool:
        if os.environ.get("GAMEBUDDY_OFFLINE_SPEECH_DISABLED") == "1":
            return False
        with self._state_lock:
            if self._closed or self._unavailable:
                return False
            if not self._is_running() and not self._launch_process():
                return False
            process = self._process
        ready_signalled = self._ready.wait(READY_TIMEOUT_SECONDS)
        with self._state_lock:
            if self._closed:
                return False
            ready_ok = ready_signalled and self._ready_ok
            if not ready_ok:
                self._disable_locked()
        if not ready_ok:
            self._fail("Offline speech worker did not become ready")
            self._terminate_process(process)
            return False
        return True

    def speak(self, text: str) -> bool:
        if not isinstance(text, str) or not text.strip() or not self.start():
            return False
        with self._state_lock:
            self._request_sequence += 1
            request_id = self._request_sequence
            self._current_request_id = request_id
            self._completion_ok = False
            self._completion.clear()
        return self._write({"command": "speak", "request_id": request_id, "text": text})

    def wait_finished(self, timeout: float | None = None) -> bool | None:
        if not self._completion.wait(timeout):
            return None
        return self._completion_ok

    def cancel(self) -> None:
        with self._state_lock:
            request_id = self._current_request_id
        if request_id is not None and self._is_running() and self._ready_ok:
            self._write({"command": "cancel", "request_id": request_id})

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            was_ready = self._ready_ok
            self._closed = True
            self._ready_ok = False
            self._completion_ok = False
            self._ready.set()
            self._completion.set()
            process = self._process
        if process is None:
            return
        if not was_ready:
            self._terminate_process(process)
            return
        if self._process_is_running(process):
            self._write({"command": "close"})
        self._close_input(process)
        try:
            process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._terminate_process(process)
        self._close_output(process)
        self._join_reader()

    def _launch_process(self) -> bool:
        self._ready.clear()
        self._ready_ok = False
        self._completion.clear()
        self._completion_ok = False
        try:
            process = self._process_factory(
                self._worker_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, ValueError) as error:
            self._disable_locked()
            self._fail("Unable to start offline speech worker", error)
            return False
        self._generation += 1
        generation = self._generation
        self._process = process
        self._reader = threading.Thread(
            target=self._read_events,
            args=(process, generation),
            daemon=True,
        )
        self._reader.start()
        return True

    def _worker_command(self) -> list[str]:
        if self._command is None:
            prefix = [sys.executable, str(Path(__file__).with_name("offline_speech_worker.py"))]
        elif isinstance(self._command, str):
            prefix = [self._command]
        else:
            prefix = list(self._command)
        return prefix + ["--bundle-root", str(self._bundle_root)]

    def _read_events(self, process: subprocess.Popen, generation: int) -> None:
        output = process.stdout
        if output is None:
            self._protocol_failure(process, generation, "Offline speech worker stdout is unavailable")
            return
        try:
            while True:
                line = output.readline()
                if not line:
                    self._protocol_failure(process, generation, "Offline speech worker stdout closed")
                    return
                if not self._handle_event(process, generation, line):
                    return
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as error:
            self._protocol_failure(process, generation, "Invalid offline speech event", error)

    def _handle_event(self, process: subprocess.Popen, generation: int, line: str) -> bool:
        message = json.loads(line)
        event = message.get("event") if isinstance(message, dict) else None
        if event not in VALID_EVENTS:
            raise ValueError("unknown speech event")
        failure: str | None = None
        with self._state_lock:
            if self._closed or not self._is_current_generation(process, generation):
                return True
            if event == "ready":
                self._ready_ok = True
                self._ready.set()
                return True
            request_id = message.get("request_id")
            if event == "error" and request_id is None:
                failure = str(message.get("message") or "unknown worker error")
                self._fail(f"Offline speech worker error: {failure}")
                self._disable_locked()
                self._ready.set()
            elif type(request_id) is not int:
                raise ValueError("request-scoped event is missing request_id")
            elif request_id == self._current_request_id and event == "error":
                failure = str(message.get("message") or "unknown worker error")
                self._fail(f"Offline speech worker error: {failure}")
                self._disable_locked()
            elif request_id == self._current_request_id and event != "started":
                self._completion_ok = event == "finished"
                self._completion.set()
        if failure is None:
            return True
        self._terminate_process(process)
        return False

    def _write(self, message: dict[str, object]) -> bool:
        process = self._process
        input_stream = process.stdin if process is not None else None
        if process is None or input_stream is None or not self._process_is_running(process):
            return False
        try:
            line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._write_lock:
                input_stream.write(line)
                input_stream.flush()
            return True
        except (OSError, BrokenPipeError, ValueError) as error:
            with self._state_lock:
                self._disable_locked()
            self._fail("Unable to write offline speech command", error)
            self._terminate_process(process)
            return False

    def _protocol_failure(
        self,
        process: subprocess.Popen,
        generation: int,
        message: str,
        error: Exception | None = None,
    ) -> None:
        with self._state_lock:
            if self._closed or not self._is_current_generation(process, generation):
                return
            self._disable_locked()
        self._fail(message, error)
        self._terminate_process(process)

    def _terminate_process(self, process: subprocess.Popen | None) -> None:
        if process is None:
            return
        try:
            if self._process_is_running(process):
                process.terminate()
            process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._kill_process(process)
        except (OSError, ValueError) as error:
            self._fail("Unable to terminate offline speech worker", error)
        finally:
            self._close_input(process)
            self._close_output(process)
            self._join_reader()

    def _kill_process(self, process: subprocess.Popen) -> None:
        try:
            process.kill()
            process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            self._fail("Unable to kill offline speech worker", error)

    def _join_reader(self) -> None:
        reader = self._reader
        if reader is None or reader is threading.current_thread():
            return
        reader.join(timeout=CLOSE_TIMEOUT_SECONDS)

    def _disable_locked(self) -> None:
        self._unavailable = True
        self._ready_ok = False
        self._completion_ok = False
        self._ready.set()
        self._completion.set()

    @staticmethod
    def _close_input(process: subprocess.Popen) -> None:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _close_output(process: subprocess.Popen) -> None:
        if process.stdout is not None:
            try:
                process.stdout.close()
            except (OSError, ValueError):
                pass

    def _is_running(self) -> bool:
        return self._process is not None and self._process_is_running(self._process)

    def _is_current_generation(self, process: subprocess.Popen, generation: int) -> bool:
        return self._process is process and self._generation == generation

    def _process_is_running(self, process: subprocess.Popen) -> bool:
        try:
            return process.poll() is None
        except (OSError, ValueError) as error:
            self._fail("Unable to inspect offline speech worker", error)
            return False

    def _fail(self, message: str, error: Exception | None = None) -> None:
        if self._failure_logged:
            return
        self._failure_logged = True
        LOGGER.warning("%s: %s", message, error) if error else LOGGER.warning("%s", message)
