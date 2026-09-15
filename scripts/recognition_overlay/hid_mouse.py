"""Read left-button edges from the mouse HID device, not from the game window."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from collections.abc import Callable
from ctypes import wintypes

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
RIM_TYPEMOUSE = 0
RIDI_DEVICENAME = 0x20000007
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF

_SKIP_NAME_PARTS = ("RDP", "VIRTUAL", "ROOT\\RDP", "KBD")


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_ulonglong),
        ("InternalHigh", ctypes.c_ulonglong),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


def hid_left_button_down(report: bytes) -> bool:
    if not report:
        return False
    if len(report) >= 5 and 1 <= report[0] <= 16 and (report[0] & 0xE0) == 0:
        return (report[1] & 0x01) != 0
    return (report[0] & 0x01) != 0


def _looks_like_physical_mouse(path: str) -> bool:
    upper = path.upper()
    return not any(part in upper for part in _SKIP_NAME_PARTS)


def list_mouse_device_paths() -> list[str]:
    if os.name != "nt":
        return []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetRawInputDeviceList.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICELIST),
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputDeviceList.restype = wintypes.UINT
    user32.GetRawInputDeviceInfoW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
    ]
    user32.GetRawInputDeviceInfoW.restype = wintypes.UINT

    count = wintypes.UINT(0)
    listed = user32.GetRawInputDeviceList(
        None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST)
    )
    if listed == 0xFFFFFFFF or count.value == 0:
        return []
    devices = (RAWINPUTDEVICELIST * count.value)()
    listed = user32.GetRawInputDeviceList(
        devices, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST)
    )
    if listed == 0xFFFFFFFF:
        return []
    paths: list[str] = []
    for index in range(listed):
        item = devices[index]
        if item.dwType != RIM_TYPEMOUSE:
            continue
        size = wintypes.UINT(0)
        user32.GetRawInputDeviceInfoW(
            item.hDevice, RIDI_DEVICENAME, None, ctypes.byref(size)
        )
        if size.value == 0:
            continue
        name = ctypes.create_unicode_buffer(size.value)
        if (
            user32.GetRawInputDeviceInfoW(
                item.hDevice, RIDI_DEVICENAME, name, ctypes.byref(size)
            )
            == 0xFFFFFFFF
        ):
            continue
        path = name.value
        if path and _looks_like_physical_mouse(path):
            paths.append(path)
    return paths


class HidMouseLeftClickWatcher:
    def __init__(
        self,
        on_click: Callable[[], None],
        on_status: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self._on_click = on_click
        self._on_status = on_status
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self.opened = 0
        self.denied = 0
        self.last_error = ""

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("HID mouse watcher already running")
        if os.name != "nt":
            return
        self._stop.clear()
        paths = list_mouse_device_paths()
        logging.info("hid mouse devices %s", paths)
        for path in paths:
            thread = threading.Thread(
                target=self._read_device,
                args=(path,),
                name=f"hid-mouse-{len(self._threads)}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        if not paths:
            self.last_error = "未枚举到物理鼠标"
            logging.warning("hid mouse: %s", self.last_error)
            self._emit_status(0, 0, self.last_error)

    def _emit_status(self, opened: int, denied: int, error: str) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(opened, denied, error)
        except Exception:
            logging.exception("hid mouse status callback failed")

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=0.8)
        self._threads = []

    def _read_device(self, path: str) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(OVERLAPPED),
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
        kernel32.ResetEvent.restype = wintypes.BOOL
        kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(OVERLAPPED),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        kernel32.GetOverlappedResult.restype = wintypes.BOOL

        handle = kernel32.CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle == INVALID_HANDLE_VALUE or not handle:
            error = ctypes.get_last_error()
            with self._lock:
                self.denied += 1
                self.last_error = f"打开鼠标失败 {error}"
                denied = self.denied
                opened = self.opened
                message = self.last_error
            logging.warning("hid mouse open failed %s error=%s", path, error)
            self._emit_status(opened, denied, message)
            return
        with self._lock:
            self.opened += 1
            opened = self.opened
            denied = self.denied
        logging.info("hid mouse opened %s", path)
        self._emit_status(opened, denied, "")
        error_io_pending = 997
        event = kernel32.CreateEventW(None, True, False, None)
        if not event:
            kernel32.CloseHandle(handle)
            return
        down = False
        try:
            while not self._stop.is_set():
                kernel32.ResetEvent(event)
                overlapped = OVERLAPPED()
                overlapped.hEvent = event
                buffer = (ctypes.c_ubyte * 64)()
                read = wintypes.DWORD(0)
                if not kernel32.ReadFile(
                    handle,
                    buffer,
                    64,
                    ctypes.byref(read),
                    ctypes.byref(overlapped),
                ):
                    pending_error = ctypes.get_last_error()
                    if pending_error not in (0, error_io_pending):
                        continue
                wait = kernel32.WaitForSingleObject(event, 200)
                if wait == WAIT_TIMEOUT:
                    kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
                    continue
                if wait != WAIT_OBJECT_0:
                    break
                if not kernel32.GetOverlappedResult(
                    handle, ctypes.byref(overlapped), ctypes.byref(read), False
                ):
                    continue
                report = bytes(buffer[: max(1, int(read.value))])
                pressed = hid_left_button_down(report)
                if pressed and not down:
                    try:
                        self._on_click()
                    except Exception:
                        logging.exception("hid mouse click callback failed")
                down = pressed
        finally:
            kernel32.CancelIoEx(handle, None)
            kernel32.CloseHandle(event)
            kernel32.CloseHandle(handle)
