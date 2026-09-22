"""Launch the existing C++ vision EXE after the in-game window appears."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from click_flag import write_ocr_hold

DEFAULT_WINDOW_TITLE = "League of Legends (TM) Client"
GAME_WINDOW_CLASS = "RiotWindowClass"
MAX_JSONL_BYTES = 65_536
STDOUT_POLL_SECONDS = 0.1
PROCESS_EXIT_SECONDS = 0.6
VISION_ERROR_TAIL_BYTES = 65_536
VISION_STDERR_MAX_BYTES = 2 * 1024 * 1024
VISION_MODES = frozenset({"KIWI", "KIWI_JADE"})
STABLE_PROCESS_SECONDS = 5.0
MAX_RESTART_BACKOFF_SECONDS = 5.0


class _WindowsKillJob:
    """Kill assigned vision children if the overlay exits unexpectedly."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self) -> None:
        self._handle = 0
        self._kernel32: Any = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class BASIC_LIMITS(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class EXTENDED_LIMITS(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC_LIMITS),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.argtypes = [
                ctypes.c_void_p,
                wintypes.LPCWSTR,
            ]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [
                wintypes.HANDLE,
                wintypes.HANDLE,
            ]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                logging.warning("vision job creation failed error=%s", ctypes.get_last_error())
                return
            limits = EXTENDED_LIMITS()
            limits.BasicLimitInformation.LimitFlags = (
                self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not kernel32.SetInformationJobObject(
                handle,
                self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                error = ctypes.get_last_error()
                kernel32.CloseHandle(handle)
                logging.warning("vision job configuration failed error=%s", error)
                return
            self._handle = int(handle)
            self._kernel32 = kernel32
        except Exception:
            logging.exception("vision kill-on-close job unavailable")

    def assign(self, process: subprocess.Popen[str]) -> bool:
        if not self._handle or self._kernel32 is None:
            return os.name != "nt"
        process_handle = int(getattr(process, "_handle", 0) or 0)
        if not process_handle:
            return False
        if self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            return True
        import ctypes

        logging.warning(
            "vision child could not join kill-on-close job pid=%s error=%s",
            process.pid,
            ctypes.get_last_error(),
        )
        return False

    def close(self) -> None:
        handle = self._handle
        self._handle = 0
        if handle and self._kernel32 is not None:
            self._kernel32.CloseHandle(handle)


def looks_like_game_window(
    title: str | None,
    class_name: str | None = None,
    expected_title: str | None = None,
) -> bool:
    text = (title or "").strip()
    klass = (class_name or "").strip()
    if klass == GAME_WINDOW_CLASS:
        return True
    expected = (expected_title or DEFAULT_WINDOW_TITLE).strip()
    return bool(text) and text in {expected, DEFAULT_WINDOW_TITLE, "League of Legends"}


def _visible_hwnd(user32: Any, hwnd: Any) -> int | None:
    if hwnd and user32.IsWindowVisible(hwnd):
        return int(hwnd)
    return None


def find_game_hwnd(title: str = DEFAULT_WINDOW_TITLE) -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_int
    found = _visible_hwnd(user32, user32.FindWindowW(None, title))
    if found:
        return found
    if title != DEFAULT_WINDOW_TITLE:
        found = _visible_hwnd(
            user32, user32.FindWindowW(None, DEFAULT_WINDOW_TITLE)
        )
        if found:
            return found
    return _visible_hwnd(user32, user32.FindWindowW(GAME_WINDOW_CLASS, None))


def game_window_visible(title: str = DEFAULT_WINDOW_TITLE) -> bool:
    return find_game_hwnd(title) is not None


def classify_vision_payload(payload: Mapping[str, Any]) -> str:
    payload_type = payload.get("type")
    if payload_type == "selection_observed":
        return "selection_observed"
    if payload_type == "selection_confirmation_ack":
        return "selection_confirmation_ack"
    if payload_type == "click_ack":
        return "click_ack"
    if payload_type == "frame_result":
        return "frame_result"
    if payload_type == "mayhem_selection_state":
        return "mayhem_selection_state"
    if payload_type == "live_client_state":
        return "live_client_state"
    if payload_type in {"capture_health", "lcu_context_state"}:
        return "status"
    if "current_offer" in payload and "offer_round" in payload:
        return "game_state"
    return "other"


def parse_vision_line(line: str) -> tuple[str, dict[str, Any]] | None:
    text = line.strip()
    if not text or len(text.encode("utf-8", errors="ignore")) > MAX_JSONL_BYTES:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return classify_vision_payload(payload), payload


def parse_vision_error_line(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:180]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()[:180]
    return text[:180]


def last_vision_error(path: Path, *, start_offset: int = 0) -> str | None:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            begin = max(0, int(start_offset), end - VISION_ERROR_TAIL_BYTES)
            stream.seek(begin)
            raw = stream.read(VISION_ERROR_TAIL_BYTES)
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    if begin > int(start_offset):
        _, separator, text = text.partition("\n")
        if not separator:
            return None
    for line in reversed(text.splitlines()):
        message = parse_vision_error_line(line)
        if message:
            return message
    return None


def rotate_vision_error_log(path: Path) -> None:
    try:
        if path.stat().st_size <= VISION_STDERR_MAX_BYTES:
            return
    except OSError:
        return
    backup = path.with_name(path.name + ".1")
    try:
        backup.unlink(missing_ok=True)
        path.replace(backup)
    except OSError:
        logging.warning("could not rotate vision stderr log %s", path)


def build_vision_command(
    *,
    exe: Path,
    knowledge: Path,
    workspace: Path,
    window_title: str = DEFAULT_WINDOW_TITLE,
    champion: str | None = None,
    hwnd: int | None = None,
    mode: str = "KIWI",
    max_seconds: float = 86400.0,
    completed_offers: int = 0,
    capture_backend: str = "wgc",
) -> list[str]:
    command = [
        str(exe),
        "--capture-backend",
        capture_backend,
        "--lcu-context",
        "off",
        "--live-client",
        "auto",
        "--completed-offers",
        str(max(0, min(4, completed_offers))),
        "--mode",
        mode,
        "--knowledge",
        str(knowledge),
        "--workspace",
        str(workspace),
        "--max-seconds",
        str(max_seconds),
        "--no-hotkey",
    ]
    if hwnd:
        command.extend(["--hwnd", hex(hwnd)])
    else:
        command.extend(["--window-title", window_title])
    if champion:
        command.extend(["--champion", champion])
    return command


class VisionSupervisor:
    def __init__(
        self,
        *,
        exe: Path | None,
        knowledge: Path,
        workspace: Path,
        home: Path | None = None,
        on_payload: Callable[[str, Mapping[str, Any]], None],
        on_status: Callable[[str, str | None], None],
        completed_offers: Callable[[], int],
        champion: Callable[[], str | None],
        match_id: Callable[[], str] | None = None,
        should_run: Callable[[], bool] | None = None,
        should_ocr: Callable[[], bool] | None = None,
        window_title: str = DEFAULT_WINDOW_TITLE,
        mode: str = "KIWI",
        max_seconds: float = 86400.0,
        capture_backend: str = "wgc",
    ) -> None:
        self._exe = exe
        self._knowledge = knowledge
        self._workspace = workspace
        self._home = home if home is not None else workspace
        self._on_payload = on_payload
        self._on_status = on_status
        self._completed_offers = completed_offers
        self._champion = champion
        self._match_id = match_id
        self._should_run = should_run
        self._should_ocr = should_ocr
        self._window_title = window_title
        self._mode = mode
        self._max_seconds = max_seconds
        self._capture_backend = capture_backend
        self._stop = threading.Event()
        self._offer_refresh_restart = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._process_match_id: str | None = None
        self._process_session_id: str | None = None
        self._pending_selection: tuple[dict[str, Any], float] | None = None
        self._process_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._last_status: tuple[str, str | None] | None = None
        self._kill_job: _WindowsKillJob | None = None
        self._stderr_path = self._workspace / "vision_stderr.log"

    @property
    def mode(self) -> str:
        with self._process_lock:
            return self._mode

    def set_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().upper()
        if normalized not in VISION_MODES:
            return
        with self._process_lock:
            if normalized == self._mode:
                return
            self._mode = normalized
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("vision supervisor already running")
        self._stop.clear()
        self._kill_job = _WindowsKillJob()
        with self._status_lock:
            self._last_status = None
        self._thread = threading.Thread(
            target=self._run_guarded, name="vision-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_process()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                logging.error("vision supervisor did not stop within timeout")
            else:
                self._thread = None
        try:
            write_ocr_hold(self._workspace, held=False)
        except OSError:
            pass
        job = self._kill_job
        self._kill_job = None
        if job is not None:
            job.close()

    def restart_for_offer_refresh(self) -> bool:
        if self._stop.is_set():
            return False
        self._offer_refresh_restart.set()
        return True

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            try:
                process.wait(timeout=0)
            except Exception:
                pass
            return
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=PROCESS_EXIT_SECONDS)
            return
        except Exception:
            pass
        try:
            process.kill()
        except OSError:
            return
        try:
            process.wait(timeout=PROCESS_EXIT_SECONDS)
        except Exception:
            logging.warning("vision child could not be reaped pid=%s", process.pid)

    def _stop_process(self) -> None:
        with self._process_lock:
            process = self._process
            self._process = None
            self._process_match_id = None
            self._process_session_id = None
            self._pending_selection = None
            self._clear_selection_signal()
        if process is None:
            return
        self._terminate_process(process)

    def _clear_selection_signal(self) -> None:
        try:
            (self._workspace / "selection-confirmed.json").unlink(missing_ok=True)
        except OSError:
            logging.exception("could not clear stale selection confirmation signal")

    def confirm_selection(self, command: Mapping[str, Any]) -> bool:
        """Publish a tiny session-bound signal; never call app callbacks here."""
        try:
            timestamp = datetime.fromisoformat(str(command.get("observed_at_utc", "")).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        except (TypeError, ValueError):
            return False
        ids = command.get("offer_augment_ids")
        if not (command.get("schema_version") == 1
                and command.get("evidence_source") == "python_fresh_ocr_title_transition"
                and type(command.get("offer_stage")) is int and 1 <= command["offer_stage"] <= 4
                and type(command.get("evidence_frames")) is int and command["evidence_frames"] >= 2
                and isinstance(ids, list) and len(ids) == 3
                and all(isinstance(value, str) and value and len(value) <= 160 for value in ids)
                and len(set(ids)) == 3
                and command.get("selected_augment_id") in ids and -0.25 <= age <= 5
                and str(command.get("observed_at_utc", "")).endswith("Z")):
            return False
        try:
            encoded = json.dumps(dict(command), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            return False
        if len(encoded) > 8192:
            return False
        for attempt in range(3):
            # Only supervisor-owned fields are read under this lock. Calling
            # _current_match_id here would invert the app-lock/process-lock order.
            with self._process_lock:
                process = self._process
                if (process is None or process.poll() is not None
                        or command.get("match_id") != self._process_match_id
                        or not self._process_session_id or command.get("session_id") != self._process_session_id):
                    logging.warning("selection confirmation rejected: stale worker binding session=%s stage=%s",
                                    command.get("session_id"), command.get("offer_stage"))
                    return False
                destination = self._workspace / "selection-confirmed.json"
                temporary = self._workspace / f"selection-confirmed-{uuid4().hex}.tmp"
                written = False
                try:
                    if destination.exists():
                        logging.warning("selection confirmation still pending session=%s", self._process_session_id)
                        return False
                    temporary.write_bytes(encoded)
                    os.replace(temporary, destination)
                    written = True
                except OSError:
                    logging.exception("selection confirmation write failed session=%s attempt=%s",
                                      self._process_session_id, attempt + 1)
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        logging.exception("could not remove selection confirmation temporary file")
                if written:
                    self._pending_selection = (dict(command), time.monotonic())
                    logging.info("selection confirmation sent pid=%s session=%s stage=%s ids=%s selected=%s",
                                 process.pid, self._process_session_id, command["offer_stage"], ids, command["selected_augment_id"])
                    return True
            if attempt < 2 and self._stop.wait(0.05):
                return False
        self._restart_selection_worker(command, "confirmation_write_failed")
        return False

    def _restart_selection_worker(self, command: Mapping[str, Any], reason: str) -> bool:
        """Recover a committed Python selection without touching a newer child."""
        with self._process_lock:
            process = self._process
            if (process is None or command.get("match_id") != self._process_match_id
                    or command.get("session_id") != self._process_session_id):
                return False
            self._process = None
            self._process_match_id = None
            self._process_session_id = None
            self._pending_selection = None
            self._clear_selection_signal()
        logging.error("selection confirmation unavailable; restarting matching worker pid=%s session=%s reason=%s",
                      process.pid, command.get("session_id"), reason)
        self._terminate_process(process)
        return True

    def _handle_selection_ack(self, payload: Mapping[str, Any]) -> bool:
        with self._process_lock:
            pending = self._pending_selection
            if pending is None or payload.get("session_id") != pending[0].get("session_id"):
                return False
            command = pending[0]
            if payload.get("reason") in {"confirmed", "confirmed_persistence_pending"}:
                self._pending_selection = None
                return False
        return self._restart_selection_worker(command, f"confirmation_ack_{payload.get('reason')}")

    def _check_selection_ack_timeout(self) -> bool:
        with self._process_lock:
            pending = self._pending_selection
            if pending is None or time.monotonic() - pending[1] < 7.0:
                return False
        return self._restart_selection_worker(pending[0], "confirmation_ack_timeout")

    def _emit_status(self, status: str, note: str | None) -> None:
        identity = (status, note)
        with self._status_lock:
            if identity == self._last_status:
                return
        try:
            self._on_status(status, note)
        except Exception:
            logging.exception("vision status callback failed status=%s", status)
        else:
            with self._status_lock:
                self._last_status = identity

    def _emit_payload(self, kind: str, payload: Mapping[str, Any]) -> None:
        try:
            self._on_payload(kind, payload)
        except Exception:
            logging.exception("vision payload callback failed kind=%s", kind)

    def _safe_completed_offers(self) -> int:
        try:
            return int(self._completed_offers())
        except Exception:
            logging.exception("completed-offers callback failed")
            return 0

    def _safe_champion(self) -> str | None:
        try:
            return self._champion()
        except Exception:
            logging.exception("champion callback failed")
            return None

    @staticmethod
    def _read_stdout(
        stream: Any,
        destination: "queue.Queue[str | None]",
    ) -> None:
        try:
            for line in iter(stream.readline, ""):
                destination.put(line)
        except Exception:
            logging.exception("vision stdout reader failed")
        finally:
            destination.put(None)

    def _run_guarded(self) -> None:
        try:
            self._run()
        except Exception:
            logging.exception("vision supervisor failed")
            self._emit_status(
                "视觉引擎监督异常",
                "识别监督线程发生异常；关闭并重新打开助手即可恢复。",
            )
        finally:
            self._stop_process()
            try:
                write_ocr_hold(self._workspace, held=False)
            except OSError:
                pass

    def _gate_open(self) -> bool:
        if self._should_run is None:
            return True
        try:
            return bool(self._should_run())
        except Exception:
            return False

    def _ocr_open(self) -> bool:
        if self._should_ocr is None:
            return True
        try:
            return bool(self._should_ocr())
        except Exception:
            return True

    def _current_match_id(self) -> str | None:
        return self._match_id() if self._match_id is not None else None

    def _sync_ocr_hold(self) -> None:
        try:
            write_ocr_hold(self._workspace, held=not self._ocr_open())
        except OSError:
            pass

    def _run(self) -> None:
        if self._exe is None or not self._exe.is_file():
            self._emit_status(
                "未找到视觉引擎",
                "内置海克斯识别引擎缺失，进局后无法识别三选一。待选席仍可用。",
            )
            return
        if not self._knowledge.is_file():
            self._emit_status("缺少海克斯词表", "找不到海克斯大乱斗知识库。")
            return
        consecutive_failures = 0
        while not self._stop.is_set():
            if not game_window_visible(self._window_title):
                consecutive_failures = 0
                self._stop_process()
                self._emit_status(
                    "等待游戏窗口",
                    "对局窗口一出现，识别引擎就会自动 OCR，不用点鼠标。",
                )
                self._stop.wait(1.0)
                continue
            if not self._gate_open():
                consecutive_failures = 0
                self._stop_process()
                self._emit_status(
                    "空闲，未识屏",
                    "本轮已记下或未到选牌窗口。升到下一档海克斯并阵亡后再自动识别。",
                )
                self._stop.wait(0.25)
                continue
            hwnd = find_game_hwnd(self._window_title)
            launch_match_id = self._current_match_id()
            self._sync_ocr_hold()
            with self._process_lock:
                launch_mode = self._mode
            command = build_vision_command(
                exe=self._exe,
                knowledge=self._knowledge,
                workspace=self._workspace,
                window_title=self._window_title,
                champion=self._safe_champion(),
                hwnd=hwnd,
                mode=launch_mode,
                max_seconds=self._max_seconds,
                completed_offers=self._safe_completed_offers(),
                capture_backend=self._capture_backend,
            )
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
                creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rotate_vision_error_log(self._stderr_path)
            try:
                stderr_offset = self._stderr_path.stat().st_size
            except OSError:
                stderr_offset = 0
            try:
                stderr_handle = self._stderr_path.open("ab")
            except OSError:
                stderr_handle = subprocess.DEVNULL
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(self._home),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=stderr_handle,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
                job = self._kill_job
                if job is not None:
                    job.assign(process)
            except OSError as error:
                if hasattr(stderr_handle, "close"):
                    stderr_handle.close()
                self._emit_status("视觉引擎启动失败", str(error)[:180])
                self._stop.wait(3.0)
                continue
            finally:
                if hasattr(stderr_handle, "close"):
                    stderr_handle.close()
            if self._stop.is_set():
                self._terminate_process(process)
                break
            with self._process_lock:
                if self._stop.is_set() or self._mode != launch_mode:
                    publish_process = False
                else:
                    self._process = process
                    self._process_match_id = launch_match_id
                    self._process_session_id = None
                    self._pending_selection = None
                    self._clear_selection_signal()
                    publish_process = True
            if not publish_process:
                self._terminate_process(process)
                if self._stop.is_set():
                    break
                continue
            logging.info("vision started pid=%s hwnd=%s", process.pid, hwnd)
            process_started_at = time.monotonic()
            self._emit_status(
                "识别中",
                "游戏窗口已出现，先认出三张海克斯，再在另外两张消失时记账。",
            )
            stream = process.stdout
            if stream is None:
                self._emit_status("视觉引擎无输出", None)
                self._stop_process()
                self._stop.wait(2.0)
                continue
            lines: queue.Queue[str | None] = queue.Queue()
            reader = threading.Thread(
                target=self._read_stdout,
                args=(stream, lines),
                name=f"vision-stdout-{process.pid}",
                daemon=True,
            )
            reader.start()
            closed_by_gate = False
            hwnd_misses = 0
            while not self._stop.is_set():
                if self._check_selection_ack_timeout():
                    closed_by_gate = True
                    break
                hwnd_now = find_game_hwnd(self._window_title)
                if (
                    not self._gate_open()
                    or self._current_match_id() != launch_match_id
                    or (hwnd_now is not None and hwnd_now != hwnd)
                ):
                    self._stop_process()
                    closed_by_gate = True
                    break
                self._sync_ocr_hold()
                if hwnd_now is None:
                    hwnd_misses += 1
                    if hwnd_misses >= 3:
                        self._stop_process()
                        closed_by_gate = True
                        break
                else:
                    hwnd_misses = 0
                try:
                    line = lines.get(timeout=STDOUT_POLL_SECONDS)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                parsed = parse_vision_line(line)
                if parsed is None:
                    continue
                kind, payload = parsed
                if kind == "frame_result":
                    session_id = payload.get("session_id")
                    if isinstance(session_id, str) and session_id:
                        with self._process_lock:
                            if self._process is process:
                                self._process_session_id = session_id
                if kind in {
                    "game_state",
                    "selection_observed",
                    "selection_confirmation_ack",
                    "click_ack",
                    "frame_result",
                    "mayhem_selection_state",
                    "live_client_state",
                }:
                    if launch_match_id is not None:
                        # The application validates this under its model lock,
                        # so an old child cannot update a newly restored match.
                        payload["_vision_match_id"] = launch_match_id
                    self._emit_payload(kind, payload)
                    if self._offer_refresh_restart.is_set():
                        self._offer_refresh_restart.clear()
                        self._stop_process()
                        closed_by_gate = True
                        break
                    if kind == "selection_confirmation_ack" and self._handle_selection_ack(payload):
                        closed_by_gate = True
                        break
                    self._sync_ocr_hold()
            if self._stop.is_set():
                break
            if closed_by_gate:
                consecutive_failures = 0
                continue
            code = None
            try:
                code = process.wait(timeout=1.0)
            except Exception:
                code = process.poll()
            with self._process_lock:
                if self._process is process and code is not None:
                    self._process = None
            logging.warning("vision process exited code=%s", code)
            if code is None:
                self._stop_process()
                self._stop.wait(2.0)
                continue
            detail = last_vision_error(
                self._stderr_path,
                start_offset=stderr_offset,
            )
            if code == 64:
                consecutive_failures += 1
                delay = min(
                    MAX_RESTART_BACKOFF_SECONDS,
                    0.5 * (2 ** min(consecutive_failures - 1, 4)),
                )
                self._emit_status(
                    "视觉引擎参数错误",
                    detail or "识别进程命令行被拒绝，稍后自动重试。",
                )
                self._stop.wait(delay)
                continue
            if self._gate_open() and game_window_visible(self._window_title):
                with self._process_lock:
                    mode_changed = self._mode != launch_mode
                if mode_changed:
                    consecutive_failures = 0
                    continue
                runtime = time.monotonic() - process_started_at
                if runtime >= STABLE_PROCESS_SECONDS:
                    consecutive_failures = 1
                else:
                    consecutive_failures += 1
                delay = min(
                    MAX_RESTART_BACKOFF_SECONDS,
                    0.2 * (2 ** min(consecutive_failures - 1, 5)),
                )
                self._emit_status(
                    "识别引擎重启中",
                    detail or f"识别进程意外退出，{delay:.1f} 秒后自动恢复。",
                )
                self._stop.wait(delay)
                continue
            self._emit_status(
                "空闲，未识屏",
                "识别进程已停。游戏窗口还在时会立刻再开，继续自动 OCR。",
            )
            self._stop.wait(0.5)
