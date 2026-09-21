"""Persistent Windows System.Speech adapter using a JSON Lines worker."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, Sequence


LOGGER = logging.getLogger(__name__)
READY_TIMEOUT_SECONDS = 3.0
CLOSE_TIMEOUT_SECONDS = 2.0
VALID_EVENTS = frozenset({"ready", "started", "finished", "error"})


class WindowsSpeechAdapter:
    def __init__(
        self,
        command: str | Sequence[str] | None = None,
        voice_name: str | None = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self._command = command
        self._voice_name = voice_name
        self._process_factory = process_factory
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ready_ok = False
        self._finished_ok = False
        self._closed = False
        self._failure_logged = False

    def start(self) -> bool:
        with self._state_lock:
            if self._closed:
                return False
            if not self._is_running() and not self._launch_process():
                return False
        if not self._ready.wait(READY_TIMEOUT_SECONDS) or not self._ready_ok:
            self._fail("Windows speech worker did not become ready")
            self._terminate_process()
            return False
        return True

    def speak(self, text: str) -> bool:
        if not isinstance(text, str) or not text.strip() or not self.start():
            return False
        self._finished_ok = False
        self._finished.clear()
        return self._write({"command": "speak", "text": text})

    def wait_finished(self, timeout: float | None = None) -> bool:
        if not self._finished.wait(timeout):
            return False
        return self._finished_ok

    def cancel(self) -> None:
        if self._is_running() and self._ready_ok:
            self._write({"command": "cancel"})

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
            self._terminate_process()
        self._close_output(process)

    def _launch_process(self) -> bool:
        self._ready.clear()
        self._ready_ok = False
        try:
            self._process = self._process_factory(
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
            self._fail("Unable to start Windows speech worker", error)
            return False
        self._reader = threading.Thread(target=self._read_events, daemon=True)
        self._reader.start()
        return True

    def _worker_command(self) -> list[str]:
        if self._command is None:
            prefix = ["powershell.exe"]
        elif isinstance(self._command, str):
            prefix = [self._command]
        else:
            prefix = list(self._command)
        worker = Path(__file__).with_name("windows_speech_worker.ps1")
        result = prefix + ["-NoProfile", "-NonInteractive", "-File", str(worker)]
        if self._voice_name:
            result += ["-VoiceName", self._voice_name]
        return result

    def _read_events(self) -> None:
        process = self._process
        output = process.stdout if process is not None else None
        if output is None:
            self._protocol_failure("Windows speech worker stdout is unavailable")
            return
        try:
            while True:
                line = output.readline()
                if not line:
                    self._protocol_failure("Windows speech worker stdout closed")
                    return
                self._handle_event(line)
        except (OSError, ValueError, TypeError) as error:
            self._protocol_failure("Unable to read Windows speech event", error)

    def _handle_event(self, line: str) -> None:
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError("invalid JSON event") from error
        event = message.get("event") if isinstance(message, dict) else None
        if event not in VALID_EVENTS:
            raise ValueError("invalid speech event")
        if event == "ready":
            self._ready_ok = True
            self._ready.set()
        elif event == "started":
            return
        else:
            self._finished_ok = event == "finished"
            self._finished.set()

    def _write(self, message: dict[str, str]) -> bool:
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
            self._finished_ok = False
            self._finished.set()
            self._fail("Unable to write Windows speech command", error)
            return False

    def _protocol_failure(self, message: str, error: Exception | None = None) -> None:
        if self._closed:
            return
        self._ready_ok = False
        self._finished_ok = False
        self._ready.set()
        self._finished.set()
        self._fail(message, error)
        self._terminate_process()

    def _terminate_process(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if self._process_is_running(process):
                process.terminate()
                process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._fail("Unable to terminate Windows speech worker")
        self._close_input(process)
        self._close_output(process)

    def _close_input(self, process: subprocess.Popen) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.close()
        except (OSError, ValueError):
            return

    def _close_output(self, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        try:
            process.stdout.close()
        except (OSError, ValueError):
            return

    def _is_running(self) -> bool:
        return self._process is not None and self._process_is_running(self._process)

    def _process_is_running(self, process: subprocess.Popen) -> bool:
        try:
            return process.poll() is None
        except (OSError, ValueError) as error:
            self._fail("Unable to inspect Windows speech worker", error)
            return False

    def _fail(self, message: str, error: Exception | None = None) -> None:
        if self._failure_logged:
            return
        self._failure_logged = True
        if error:
            LOGGER.warning("%s: %s", message, error)
        else:
            LOGGER.warning("%s", message)
