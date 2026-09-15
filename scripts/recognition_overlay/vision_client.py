"""Launch the existing C++ vision EXE after the in-game window appears."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from click_flag import write_ocr_hold

DEFAULT_WINDOW_TITLE = "League of Legends (TM) Client"
GAME_WINDOW_CLASS = "RiotWindowClass"
MAX_JSONL_BYTES = 65_536


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


def last_vision_error(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        message = parse_vision_error_line(line)
        if message:
            return message
    return None


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
        self._should_run = should_run
        self._should_ocr = should_ocr
        self._window_title = window_title
        self._mode = mode
        self._max_seconds = max_seconds
        self._capture_backend = capture_backend
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._stderr_path = self._workspace / "vision_stderr.log"

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if not mode or mode == self._mode:
            return
        self._mode = mode
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
        self._thread = threading.Thread(
            target=self._run, name="vision-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_process()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return

        def _reap() -> None:
            try:
                process.wait(timeout=0.4)
            except Exception:
                try:
                    process.kill()
                except OSError:
                    pass

        threading.Thread(target=_reap, name="vision-reap", daemon=True).start()

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

    def _sync_ocr_hold(self) -> None:
        try:
            write_ocr_hold(self._workspace, held=not self._ocr_open())
        except OSError:
            pass

    def _run(self) -> None:
        if self._exe is None or not self._exe.is_file():
            self._on_status(
                "未找到视觉引擎",
                "内置海克斯识别引擎缺失，进局后无法识别三选一。待选席仍可用。",
            )
            return
        if not self._knowledge.is_file():
            self._on_status("缺少海克斯词表", "找不到海克斯大乱斗知识库。")
            return
        while not self._stop.is_set():
            if not game_window_visible(self._window_title):
                self._stop_process()
                self._on_status(
                    "等待游戏窗口",
                    "对局窗口一出现，识别引擎就会自动 OCR，不用点鼠标。",
                )
                self._stop.wait(1.0)
                continue
            if not self._gate_open():
                self._stop_process()
                self._on_status(
                    "空闲，未识屏",
                    "本轮已记下或未到选牌窗口。升到下一档海克斯并阵亡后再自动识别。",
                )
                self._stop.wait(0.25)
                continue
            hwnd = find_game_hwnd(self._window_title)
            self._sync_ocr_hold()
            command = build_vision_command(
                exe=self._exe,
                knowledge=self._knowledge,
                workspace=self._workspace,
                window_title=self._window_title,
                champion=self._champion(),
                hwnd=hwnd,
                mode=self._mode,
                max_seconds=self._max_seconds,
                completed_offers=self._completed_offers(),
                capture_backend=self._capture_backend,
            )
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
                creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                stderr_handle = self._stderr_path.open("ab")
            except OSError:
                stderr_handle = subprocess.DEVNULL
            try:
                self._process = subprocess.Popen(
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
            except OSError as error:
                if hasattr(stderr_handle, "close"):
                    stderr_handle.close()
                self._on_status("视觉引擎启动失败", str(error)[:180])
                self._stop.wait(3.0)
                continue
            finally:
                if hasattr(stderr_handle, "close"):
                    stderr_handle.close()
            logging.info("vision started pid=%s hwnd=%s", self._process.pid, hwnd)
            self._on_status(
                "识别中",
                "游戏窗口已出现，先认出三张海克斯，再在另外两张消失时记账。",
            )
            stream = self._process.stdout
            if stream is None:
                self._on_status("视觉引擎无输出", None)
                self._stop_process()
                self._stop.wait(2.0)
                continue
            closed_by_gate = False
            hwnd_misses = 0
            while not self._stop.is_set():
                hwnd_now = find_game_hwnd(self._window_title)
                if not self._gate_open():
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
                line = stream.readline()
                if line == "":
                    break
                parsed = parse_vision_line(line)
                if parsed is None:
                    continue
                kind, payload = parsed
                if kind in {
                    "game_state",
                    "selection_observed",
                    "click_ack",
                    "frame_result",
                    "mayhem_selection_state",
                    "live_client_state",
                }:
                    self._on_payload(kind, payload)
                    self._sync_ocr_hold()
            if self._stop.is_set():
                break
            if closed_by_gate:
                continue
            code = None
            process = self._process
            if process is not None:
                try:
                    code = process.wait(timeout=1.0)
                except Exception:
                    code = process.poll()
            logging.warning("vision process exited code=%s", code)
            if code is None:
                self._stop_process()
                self._stop.wait(2.0)
                continue
            detail = last_vision_error(self._stderr_path)
            if code == 64:
                self._on_status(
                    "视觉引擎参数错误",
                    detail or "识别进程命令行被拒绝，已暂停重试。",
                )
                self._stop.wait(3.0)
                continue
            if self._gate_open() and game_window_visible(self._window_title):
                self._on_status(
                    "识别中",
                    detail or "选牌窗口还在，正在立即重试识别。",
                )
                self._stop.wait(0.2)
                continue
            self._on_status(
                "空闲，未识屏",
                "识别进程已停。游戏窗口还在时会立刻再开，继续自动 OCR。",
            )
            self._stop.wait(0.5)
