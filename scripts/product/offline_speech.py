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
        self._failure_logged = False
        self._generation = 0
        self._request_sequence = 0
        self._current_request_id: int | None = None

    def start(self) -> bool:
        if os.environ.get("GAMEBUDDY_OFFLINE_SPEECH_DISABLED") == "1":
            return False
        with self._state_lock:
            if self._closed:
                return False
            if not self._is_running() and not self._launch_process():
                return False
            process = self._process
        if not self._ready.wait(READY_TIMEOUT_SECONDS) or not self._ready_ok:
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
            self._closed = True
            process = self._process
        if process is None:
            return
        if self._process_is_running(process):
            self._write({"command": "close"})
        self._close_input(process)
        try:
            process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._terminate_process(process)
        self._close_output(process)

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
                self._handle_event(process, generation, line)
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as error:
            self._protocol_failure(process, generation, "Invalid offline speech event", error)

    def _handle_event(self, process: subprocess.Popen, generation: int, line: str) -> None:
        message = json.loads(line)
        event = message.get("event") if isinstance(message, dict) else None
        if event not in VALID_EVENTS:
            raise ValueError("unknown speech event")
        with self._state_lock:
            if self._closed or not self._is_current_generation(process, generation):
                return
            if event == "ready":
                self._ready_ok = True
                self._ready.set()
                return
            request_id = message.get("request_id")
            if event == "error" and request_id is None and not self._ready_ok:
                self._ready.set()
                return
            if type(request_id) is not int:
                raise ValueError("request-scoped event is missing request_id")
            if request_id != self._current_request_id or event == "started":
                return
            self._completion_ok = event == "finished"
            self._completion.set()

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
            self._completion_ok = False
            self._completion.set()
            self._fail("Unable to write offline speech command", error)
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
            self._ready_ok = False
            self._completion_ok = False
            self._ready.set()
            self._completion.set()
        self._fail(message, error)
        self._terminate_process(process)

    def _terminate_process(self, process: subprocess.Popen | None) -> None:
        if process is None:
            return
        try:
            if self._process_is_running(process):
                process.terminate()
                process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._fail("Unable to terminate offline speech worker")
        self._close_input(process)
        self._close_output(process)

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
