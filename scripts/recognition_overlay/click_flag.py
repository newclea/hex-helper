"""Signal a left-click reread to the C++ vision process."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

FLAG_NAME = "left_click.flag"
OCR_HOLD_NAME = "ocr_hold.flag"
CLICK_EVENT_NAME = "Local\\LoLAssistantLeftClick"
AUTO_REREAD_INTERVAL_SECONDS = 0.05
AUTO_REREAD_STATUS_INTERVAL_SECONDS = 0.5
DEFAULT_GAME_TITLE = "League of Legends (TM) Client"
LEAGUE_WINDOW_CLASSES = ("RiotWindowClass", "RCLIENT")
LEAGUE_WINDOW_TITLES = (
    DEFAULT_GAME_TITLE,
    "League of Legends",
    "英雄联盟",
)
_event_handle = 0
_last_token = 0
_token_lock = threading.Lock()
_windows_guard = threading.Lock()
_windows_cached: list[tuple[str, str, tuple[int, int, int, int]]] = []
_windows_cached_at = 0.0
LEAGUE_WINDOWS_CACHE_SECONDS = 0.5
RIM_TYPEMOUSE = 0
RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIDEV_EXINPUTSINK = 0x00001000
RIDEV_REMOVE = 0x00000001
# League already consumes raw mouse in the foreground. EXINPUTSINK would
# suppress our WM_INPUT until the overlay itself is focused.
RAW_MOUSE_SINK_FLAGS = RIDEV_INPUTSINK
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02
HWND_MESSAGE = -3
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_QUIT = 0x0012
HEADER_SIZE_64 = 24
OVERLAY_TITLE_PREFIXES = ("LoL 识别", "▶ 左键")
OVERLAY_CLASS_PREFIX = "LoLRecognitionOverlay"


def write_ocr_hold(workspace: Path, held: bool) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / OCR_HOLD_NAME
    if held:
        path.write_text("1", encoding="ascii")
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def write_left_click(workspace: Path) -> int:
    global _last_token
    with _token_lock:
        token = time.monotonic_ns()
        if token <= _last_token:
            token = _last_token + 1
        _last_token = token
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / FLAG_NAME
    tmp = path.with_name(FLAG_NAME + ".tmp")
    tmp.write_text(str(token), encoding="ascii")
    tmp.replace(path)
    signal_left_click_event()
    return token


def signal_left_click_event() -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    global _event_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not _event_handle:
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        _event_handle = kernel32.CreateEventW(
            None, False, False, CLICK_EVENT_NAME
        )
    if _event_handle:
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.SetEvent(_event_handle)


def looks_like_league_window(
    title: str | None,
    class_name: str | None = None,
    expected_title: str | None = None,
) -> bool:
    text = (title or "").strip()
    klass = (class_name or "").strip()
    expected = (expected_title or DEFAULT_GAME_TITLE).strip()
    if text and text == expected:
        return True
    if text in LEAGUE_WINDOW_TITLES:
        return True
    return klass in LEAGUE_WINDOW_CLASSES


def point_in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def league_contains_point(
    x: int,
    y: int,
    windows: list[tuple[str, str, tuple[int, int, int, int]]],
) -> tuple[str, str] | None:
    for title, klass, rect in windows:
        if looks_like_league_window(title, klass) and point_in_rect(x, y, rect):
            return title, klass
    return None


def foreground_window_info() -> tuple[int, str, str]:
    if os.name != "nt":
        return 0, "", ""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    hwnd = int(user32.GetForegroundWindow() or 0)
    if not hwnd:
        return 0, "", ""
    title = ctypes.create_unicode_buffer(512)
    klass = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 512)
    user32.GetClassNameW(hwnd, klass, 256)
    return hwnd, title.value, klass.value


def game_window_hwnd(title: str = DEFAULT_GAME_TITLE) -> int:
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    return int(user32.FindWindowW(None, title) or 0)


def _same_pid(left: int, right: int) -> bool:
    if not left or not right:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    left_pid = wintypes.DWORD(0)
    right_pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(left, ctypes.byref(left_pid))
    user32.GetWindowThreadProcessId(right, ctypes.byref(right_pid))
    return bool(left_pid.value and left_pid.value == right_pid.value)


def cursor_root_hwnd() -> int:
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return 0
    hwnd = int(user32.WindowFromPoint(point) or 0)
    if not hwnd:
        return 0
    root = int(user32.GetAncestor(hwnd, 2) or 0)  # GA_ROOT
    return root or hwnd


def window_title_class(hwnd: int) -> tuple[str, str]:
    if os.name != "nt" or not hwnd:
        return "", ""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    title = ctypes.create_unicode_buffer(512)
    klass = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 512)
    user32.GetClassNameW(hwnd, klass, 256)
    return title.value, klass.value


def looks_like_overlay_window(title: str | None, class_name: str | None) -> bool:
    text = (title or "").strip()
    klass = (class_name or "").strip()
    if klass.startswith(OVERLAY_CLASS_PREFIX):
        return True
    return any(text.startswith(prefix) for prefix in OVERLAY_TITLE_PREFIXES)


def describe_click_hit(
    overlay_hwnd: int = 0,
    point: tuple[int, int] | None = None,
    cursor_hwnd: int = 0,
    title: str | None = None,
    class_name: str | None = None,
) -> str:
    if looks_like_overlay_window(title, class_name):
        return "overlay"
    if overlay_hwnd and cursor_hwnd and int(overlay_hwnd) == int(cursor_hwnd):
        return "overlay"
    if looks_like_league_window(title, class_name):
        return "game"
    label = (title or class_name or "").strip()
    return f"other:{label or '?'}"


def cursor_point() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return int(point.x), int(point.y)


def league_windows() -> list[tuple[str, str, tuple[int, int, int, int]]]:
    global _windows_cached, _windows_cached_at
    if os.name != "nt":
        return []
    now = time.monotonic()
    with _windows_guard:
        if (
            _windows_cached
            and now - _windows_cached_at < LEAGUE_WINDOWS_CACHE_SECONDS
        ):
            return list(_windows_cached)
    found = _enum_league_windows()
    with _windows_guard:
        _windows_cached = found
        _windows_cached_at = time.monotonic()
    return list(found)


def _enum_league_windows() -> list[tuple[str, str, tuple[int, int, int, int]]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    found: list[tuple[str, str, tuple[int, int, int, int]]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(512)
        klass = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 512)
        user32.GetClassNameW(hwnd, klass, 256)
        if not looks_like_league_window(title.value, klass.value):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        found.append(
            (
                title.value,
                klass.value,
                (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
            )
        )
        return True

    user32.EnumWindows.argtypes = [type(callback), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    return found


def league_window_under_cursor() -> tuple[str, str] | None:
    point = cursor_point()
    if point is None:
        return None
    return league_contains_point(point[0], point[1], league_windows())


def is_league_foreground(title: str = DEFAULT_GAME_TITLE) -> bool:
    return is_in_game_click(title)


def is_in_game_click(title: str = DEFAULT_GAME_TITLE) -> bool:
    if league_window_under_cursor() is not None:
        return True
    hwnd, fg_title, fg_class = foreground_window_info()
    if looks_like_league_window(fg_title, fg_class, title):
        return True
    game = game_window_hwnd(title)
    if game and _same_pid(hwnd, game):
        return True
    return False


def raw_mouse_left_down(payload: bytes) -> bool:
    if len(payload) < 22:
        return False
    if int.from_bytes(payload[0:4], "little") != RIM_TYPEMOUSE:
        return False
    header = HEADER_SIZE_64 if len(payload) >= 32 else 16
    offset = header + 4
    if len(payload) < offset + 2:
        return False
    flags = int.from_bytes(payload[offset : offset + 2], "little")
    return (flags & RI_MOUSE_LEFT_BUTTON_DOWN) != 0


class ClickCoalesce:
    def __init__(self, cooldown_seconds: float = 0.08) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._last = 0.0

    def accept(self, now: float | None = None) -> bool:
        stamp = time.monotonic() if now is None else now
        if stamp - self._last < self.cooldown_seconds:
            return False
        self._last = stamp
        return True


def register_mouse_input_sink(user32: object, hwnd: int) -> bool:
    import ctypes
    from ctypes import wintypes

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE),
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    device = RAWINPUTDEVICE(
        HID_USAGE_PAGE_GENERIC,
        HID_USAGE_GENERIC_MOUSE,
        RAW_MOUSE_SINK_FLAGS,
        hwnd,
    )
    return bool(
        user32.RegisterRawInputDevices(
            ctypes.byref(device), 1, ctypes.sizeof(device)
        )
    )


def remove_mouse_input_sink(user32: object) -> None:
    import ctypes
    from ctypes import wintypes

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    device = RAWINPUTDEVICE(
        HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, RIDEV_REMOVE, None
    )
    try:
        user32.RegisterRawInputDevices(
            ctypes.byref(device), 1, ctypes.sizeof(device)
        )
    except Exception:
        return


def read_wm_input(user32: object, lparam: int) -> bytes | None:
    import ctypes
    from ctypes import wintypes

    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    header = ctypes.sizeof(wintypes.DWORD) * 2 + ctypes.sizeof(wintypes.HANDLE) + ctypes.sizeof(
        wintypes.WPARAM
    )
    size = wintypes.UINT(0)
    queried = user32.GetRawInputData(
        lparam, RID_INPUT, None, ctypes.byref(size), header
    )
    if queried == 0xFFFFFFFF or size.value == 0 or size.value > 4096:
        return None
    buffer = (ctypes.c_ubyte * size.value)()
    copied = user32.GetRawInputData(
        lparam, RID_INPUT, buffer, ctypes.byref(size), header
    )
    if copied == 0xFFFFFFFF or copied == 0:
        return None
    return bytes(buffer)


class AutoRereadMonitor:
    """Signal rereads while OCR is eligible, with a throttled UI heartbeat."""

    def __init__(
        self,
        workspace: Path,
        interval_seconds: float = AUTO_REREAD_INTERVAL_SECONDS,
        on_tick: Callable[[], None] | None = None,
        should_signal: Callable[[], bool] | None = None,
        status_interval_seconds: float = AUTO_REREAD_STATUS_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds <= 0 or status_interval_seconds <= 0:
            raise ValueError("auto-reread intervals must be positive")
        self._workspace = workspace
        self._interval = interval_seconds
        self._on_tick = on_tick
        self._should_signal = should_signal
        self._status_interval = status_interval_seconds
        self._clock = clock
        self._last_status_at: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("auto-reread monitor already running")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="auto-reread-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        self._emit()
        while not self._stop.wait(self._interval):
            self._emit()

    def _emit(self) -> None:
        if self._should_signal is not None:
            try:
                if not self._should_signal():
                    return
            except Exception:
                logging.exception("auto-reread gate callback failed")
                return
        try:
            write_left_click(self._workspace)
        except OSError:
            pass
        if self._on_tick is None:
            return
        now = self._clock()
        if (
            self._last_status_at is not None
            and now - self._last_status_at < self._status_interval
        ):
            return
        self._last_status_at = now
        try:
            self._on_tick()
        except Exception:
            logging.exception("auto-reread tick callback failed")


class LeftClickMonitor:
    def __init__(
        self,
        workspace: Path,
        on_click: Callable[[], None],
        interval_seconds: float = 0.001,
        game_title: str = DEFAULT_GAME_TITLE,
    ) -> None:
        self._workspace = workspace
        self._on_click = on_click
        self._interval = interval_seconds
        self._game_title = game_title
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gate = ClickCoalesce()
        self._hid: Any = None
        self._raw: Any = None
        self._hook: Any = None
        self._overlay_hwnd = 0
        self.on_hid_status: Callable[[int, int, str], None] | None = None

    def set_overlay_hwnd(self, hwnd: int) -> None:
        self._overlay_hwnd = int(hwnd or 0)

    @property
    def mouse_ready(self) -> bool:
        hook = self._hook
        raw = self._raw
        return bool(
            (hook is not None and getattr(hook, "ready", False))
            or (raw is not None and getattr(raw, "ready", False))
        )

    def emit(self, source: str = "async_key") -> bool:
        if not self._gate.accept():
            return False
        token = 0
        try:
            token = write_left_click(self._workspace)
        except OSError:
            pass
        _, fg_title, fg_class = foreground_window_info()
        point = cursor_point()
        cursor_hwnd = cursor_root_hwnd()
        hit_title, hit_class = window_title_class(cursor_hwnd)
        hit = describe_click_hit(
            overlay_hwnd=self._overlay_hwnd,
            point=point,
            cursor_hwnd=cursor_hwnd,
            title=hit_title,
            class_name=hit_class,
        )
        logging.info(
            "left click token=%s source=%s hit=%s fg=%s class=%s under=%s cursor=%s",
            token,
            source,
            hit,
            fg_title or "?",
            fg_class or "?",
            hit_title or hit_class or "?",
            f"{point[0]},{point[1]}" if point else "?",
        )
        self._on_click()
        return True

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("left-click monitor already running")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="left-click-monitor", daemon=True
        )
        self._thread.start()
        self._hook = BackgroundLowLevelMouseHook(lambda: self.emit("ll_hook"))
        try:
            self._hook.start()
        except Exception:
            logging.exception("low-level mouse hook failed")
            self._hook = None
        self._raw = BackgroundRawMouseSink(lambda: self.emit("raw_input"))
        try:
            self._raw.start()
        except Exception:
            logging.exception("background raw mouse sink failed")
            self._raw = None
        # HID CreateFile is ACCESS_DENIED on this machine; skip the scan.

    def stop(self) -> None:
        self._stop.set()
        hid = self._hid
        self._hid = None
        if hid is not None:
            hid.stop()
        hook = self._hook
        self._hook = None
        if hook is not None:
            hook.stop()
        raw = getattr(self, "_raw", None)
        self._raw = None
        if raw is not None:
            raw.stop()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        if os.name != "nt":
            return
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        winmm = ctypes.WinDLL("winmm", use_last_error=True)
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_int16
        winmm.timeBeginPeriod.argtypes = [ctypes.c_uint]
        winmm.timeEndPeriod.argtypes = [ctypes.c_uint]
        winmm.timeBeginPeriod(1)
        dinput = None
        try:
            from dinput_mouse import DirectInputLeftButton

            dinput = DirectInputLeftButton()
            if not dinput.start():
                logging.warning("directinput mouse unavailable: %s", dinput.last_error)
                dinput = None
        except Exception:
            logging.exception("directinput mouse failed")
            dinput = None
        vk_lbutton = 0x01
        vk_f9 = 0x78
        down = (user32.GetAsyncKeyState(vk_lbutton) & 0x8000) != 0
        f9_down = (user32.GetAsyncKeyState(vk_f9) & 0x8000) != 0
        if dinput is not None:
            down = down or dinput.left_down()
        try:
            while not self._stop.wait(self._interval):
                pressed = (user32.GetAsyncKeyState(vk_lbutton) & 0x8000) != 0
                source = "mouse_button"
                if dinput is not None and dinput.left_down():
                    pressed = True
                    source = "dinput"
                if pressed and not down:
                    self.emit(source)
                down = pressed
                f9_pressed = (user32.GetAsyncKeyState(vk_f9) & 0x8000) != 0
                if f9_pressed and not f9_down:
                    self.emit("hotkey_f9")
                f9_down = f9_pressed
        finally:
            if dinput is not None:
                dinput.stop()
            winmm.timeEndPeriod(1)


class BackgroundRawMouseSink:
    """Own a hidden window + message loop so WM_INPUT is not tied to the overlay UI thread."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        self._on_click = on_click
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ready = False

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("raw mouse sink already running")
        if os.name != "nt":
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="raw-mouse-sink", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        wndproc_type = ctypes.WINFUNCTYPE(
            lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("lPrivate", wintypes.DWORD),
            ]

        def window_proc(hwnd, message, wparam, lparam):
            if message == WM_INPUT:
                payload = read_wm_input(user32, lparam)
                if payload is not None and raw_mouse_left_down(payload):
                    logging.debug("raw input left down bytes=%s", len(payload))
                    try:
                        self._on_click()
                    except Exception:
                        logging.exception("raw mouse click callback failed")
                return 0
            return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = lresult
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        callback = wndproc_type(window_proc)
        class_name = f"LoLRawMouseSink_{os.getpid()}"
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = callback
        window_class.hInstance = kernel32.GetModuleHandleW(None)
        window_class.lpszClassName = class_name
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            logging.warning("raw mouse sink class failed %s", ctypes.get_last_error())
            return
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        hwnd = user32.CreateWindowExW(
            0,
            class_name,
            "",
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            None,
            window_class.hInstance,
            None,
        )
        if not hwnd:
            logging.warning("raw mouse sink hwnd failed %s", ctypes.get_last_error())
            return
        self.ready = register_mouse_input_sink(user32, int(hwnd))
        logging.info(
            "background raw mouse sink ready=%s hwnd=%s message_only=1",
            self.ready,
            int(hwnd),
        )
        message = MSG()
        while not self._stop.is_set():
            if user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            else:
                self._stop.wait(0.05)
        remove_mouse_input_sink(user32)


class BackgroundLowLevelMouseHook:
    """Global WH_MOUSE_LL on its own thread. Clicks are taken from the mouse, not a window."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        self._on_click = on_click
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._thread_id = 0
        self._hook = 0
        self._proc: Any = None
        self._clicked = threading.Event()
        self.ready = False

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("low-level mouse hook already running")
        if os.name != "nt":
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._consume, name="ll-mouse-click", daemon=True
        )
        self._worker.start()
        self._thread = threading.Thread(
            target=self._run, name="ll-mouse-hook", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._clicked.set()
        thread_id = self._thread_id
        if thread_id:
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostThreadMessageW.argtypes = [
                    wintypes.DWORD,
                    wintypes.UINT,
                    wintypes.WPARAM,
                    wintypes.LPARAM,
                ]
                user32.PostThreadMessageW.restype = wintypes.BOOL
                user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                logging.exception("low-level mouse hook quit failed")
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        worker = self._worker
        if worker is not None:
            worker.join(timeout=1.0)
        self._thread = None
        self._worker = None

    def _consume(self) -> None:
        while not self._stop.is_set():
            if not self._clicked.wait(0.1):
                continue
            self._clicked.clear()
            if self._stop.is_set():
                return
            try:
                self._on_click()
            except Exception:
                logging.exception("low-level mouse click callback failed")

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        hook_type = ctypes.WINFUNCTYPE(
            lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("lPrivate", wintypes.DWORD),
            ]

        def hook_proc(code: int, wparam: int, lparam: int) -> int:
            if code >= 0 and wparam == WM_LBUTTONDOWN:
                self._clicked.set()
            return int(user32.CallNextHookEx(self._hook, code, wparam, lparam))

        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._thread_id = int(kernel32.GetCurrentThreadId())
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            hook_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = lresult
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        self._proc = hook_type(hook_proc)
        self._hook = int(
            user32.SetWindowsHookExW(
                WH_MOUSE_LL,
                self._proc,
                kernel32.GetModuleHandleW(None),
                0,
            )
            or 0
        )
        self.ready = bool(self._hook)
        logging.info("low-level mouse hook ready=%s tid=%s", self.ready, self._thread_id)
        if not self._hook:
            return
        message = MSG()
        while not self._stop.is_set():
            result = int(user32.GetMessageW(ctypes.byref(message), None, 0, 0))
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = 0
            self.ready = False
