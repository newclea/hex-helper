"""Always-on-top Win32 host with a closable caption bar."""

from __future__ import annotations

import ctypes
import os
import threading
from typing import Any, Callable

DEFAULT_TITLE = "LoL 识别（置顶）"
DEFAULT_WIDTH = 420
DEFAULT_HEIGHT = 700


class OverlayWindow:
    WS_OVERLAPPED = 0x00000000
    WS_CAPTION = 0x00C00000
    WS_SYSMENU = 0x00080000
    WS_THICKFRAME = 0x00040000
    WS_MINIMIZEBOX = 0x00020000
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_NOACTIVATE = 0x08000000
    SW_SHOW = 5
    SW_SHOWNOACTIVATE = 4
    WM_NCHITTEST = 0x0084
    HTCLIENT = 1
    HTTRANSPARENT = -1
    WM_DESTROY = 0x0002
    WM_PAINT = 0x000F
    WM_CLOSE = 0x0010
    WM_ERASEBKGND = 0x0014
    WM_TIMER = 0x0113
    WM_APP_MODEL = 0x8001
    HWND_TOPMOST = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_ASYNCWINDOWPOS = 0x4000
    SPI_GETWORKAREA = 0x0030
    DT_LEFT = 0x0000
    DT_TOP = 0x0000
    DT_WORDBREAK = 0x0010
    DT_NOPREFIX = 0x0800
    TRANSPARENT = 1
    DEFAULT_GUI_FONT = 17
    GB2312_CHARSET = 134
    CLEARTYPE_QUALITY = 5
    FW_NORMAL = 400
    TOPMOST_TIMER_ID = 1
    TOPMOST_INTERVAL_MS = 400

    def __init__(
        self,
        *,
        title: str = DEFAULT_TITLE,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        text: str = "",
        on_ready: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("overlay window requires Windows")
        from ctypes import wintypes

        self.wintypes = wintypes
        self.title = title
        self.width = width
        self.height = height
        self.on_ready = on_ready
        self.on_close = on_close
        self._text = text
        self._shown_text = text
        self._lock = threading.Lock()
        self.hwnd: Any = None
        self._font: Any = None
        self._configure_types()

    def _configure_types(self) -> None:
        wintypes = self.wintypes
        self.LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(
            self.LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", self.WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", wintypes.BYTE * 32),
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

        self.WNDCLASSW = WNDCLASSW
        self.PAINTSTRUCT = PAINTSTRUCT
        self.MSG = MSG
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [
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
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = self.LRESULT
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.UpdateWindow.argtypes = [wintypes.HWND]
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.InvalidateRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
            wintypes.BOOL,
        ]
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.BeginPaint.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(PAINTSTRUCT),
        ]
        self.user32.BeginPaint.restype = wintypes.HDC
        self.user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        self.user32.GetClientRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.FillRect.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.HBRUSH,
        ]
        self.user32.DrawTextW.argtypes = [
            wintypes.HDC,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.RECT),
            wintypes.UINT,
        ]
        self.user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            wintypes.LPVOID,
            wintypes.UINT,
        ]
        self.user32.SetTimer.argtypes = [
            wintypes.HWND,
            ctypes.c_size_t,
            wintypes.UINT,
            ctypes.c_void_p,
        ]
        self.user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        self.gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self.gdi32.GetStockObject.argtypes = [ctypes.c_int]
        self.gdi32.GetStockObject.restype = wintypes.HGDIOBJ
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.CreateFontW.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
        ]
        self.gdi32.CreateFontW.restype = wintypes.HFONT

    @staticmethod
    def _rgb(red: int, green: int, blue: int) -> int:
        return red | (green << 8) | (blue << 16)

    def _layout(self) -> tuple[int, int, int, int]:
        rect = self.wintypes.RECT()
        ok = self.user32.SystemParametersInfoW(
            self.SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        if not ok:
            return 40, 40, self.width, self.height
        width = min(self.width, max(280, int(rect.right - rect.left) - 24))
        height = min(self.height, max(280, int(rect.bottom - rect.top) - 24))
        x = max(int(rect.left), int(rect.right) - width - 16)
        y = int(rect.top) + 16
        return x, y, width, height

    def _assert_topmost(self) -> None:
        if not self.hwnd:
            return
        self.user32.SetWindowPos(
            self.hwnd,
            self.wintypes.HWND(self.HWND_TOPMOST),
            0,
            0,
            0,
            0,
            self.SWP_NOMOVE
            | self.SWP_NOSIZE
            | self.SWP_NOACTIVATE
            | self.SWP_ASYNCWINDOWPOS,
        )

    def set_text(self, text: str) -> None:
        with self._lock:
            self._text = text

    def set_title(self, title: str) -> None:
        text = " ".join(title.strip().split())[:80] or self.title
        self.title = text

    def _paint(self, hwnd: Any) -> None:
        paint = self.PAINTSTRUCT()
        hdc = self.user32.BeginPaint(hwnd, ctypes.byref(paint))
        if not hdc:
            return
        try:
            rect = self.wintypes.RECT()
            self.user32.GetClientRect(hwnd, ctypes.byref(rect))
            background = self.gdi32.CreateSolidBrush(self._rgb(18, 22, 30))
            if background:
                self.user32.FillRect(hdc, ctypes.byref(rect), background)
                self.gdi32.DeleteObject(background)
            self.gdi32.SetBkMode(hdc, self.TRANSPARENT)
            self.gdi32.SetTextColor(hdc, self._rgb(236, 240, 244))
            font = self._font or self.gdi32.GetStockObject(self.DEFAULT_GUI_FONT)
            if font:
                self.gdi32.SelectObject(hdc, font)
            with self._lock:
                text = self._text
            text_rect = self.wintypes.RECT(
                14, 12, max(14, rect.right - 14), max(12, rect.bottom - 12)
            )
            self.user32.DrawTextW(
                hdc,
                text,
                -1,
                ctypes.byref(text_rect),
                self.DT_LEFT | self.DT_TOP | self.DT_WORDBREAK | self.DT_NOPREFIX,
            )
        finally:
            self.user32.EndPaint(hwnd, ctypes.byref(paint))

    def _window_proc(self, hwnd: Any, message: int, wparam: int, lparam: int) -> int:
        if message == self.WM_NCHITTEST:
            hit = int(self.user32.DefWindowProcW(hwnd, message, wparam, lparam))
            if hit == self.HTCLIENT:
                return self.HTTRANSPARENT
            return hit
        if message == self.WM_TIMER and wparam == self.TOPMOST_TIMER_ID:
            with self._lock:
                text = self._text
            if text != self._shown_text:
                self._shown_text = text
                self.user32.InvalidateRect(hwnd, None, True)
            self._assert_topmost()
            return 0
        if message == self.WM_APP_MODEL:
            self.user32.InvalidateRect(hwnd, None, True)
            return 0
        if message == self.WM_ERASEBKGND:
            return 1
        if message == self.WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == self.WM_CLOSE:
            self.user32.DestroyWindow(hwnd)
            return 0
        if message == self.WM_DESTROY:
            if self.hwnd:
                self.user32.KillTimer(hwnd, self.TOPMOST_TIMER_ID)
            if self.on_close is not None:
                self.on_close()
            self.user32.PostQuitMessage(0)
            return 0
        return int(self.user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def run(self) -> int:
        hinstance = self.kernel32.GetModuleHandleW(None)
        class_name = f"LoLRecognitionOverlay_{os.getpid()}"
        self._wndproc_callback = self.WNDPROC(self._window_proc)
        window_class = self.WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc_callback
        window_class.hInstance = hinstance
        window_class.lpszClassName = class_name
        window_class.hbrBackground = 0
        if not self.user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._font = self.gdi32.CreateFontW(
            -16,
            0,
            0,
            0,
            self.FW_NORMAL,
            0,
            0,
            0,
            self.GB2312_CHARSET,
            0,
            0,
            self.CLEARTYPE_QUALITY,
            0,
            "Microsoft YaHei UI",
        )
        x, y, width, height = self._layout()
        style = (
            self.WS_OVERLAPPED
            | self.WS_CAPTION
            | self.WS_SYSMENU
            | self.WS_THICKFRAME
            | self.WS_MINIMIZEBOX
        )
        self.hwnd = self.user32.CreateWindowExW(
            self.WS_EX_APPWINDOW | self.WS_EX_TOPMOST | self.WS_EX_NOACTIVATE,
            class_name,
            self.title,
            style,
            x,
            y,
            width,
            height,
            None,
            None,
            hinstance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._assert_topmost()
        self.user32.SetTimer(
            self.hwnd, self.TOPMOST_TIMER_ID, self.TOPMOST_INTERVAL_MS, None
        )
        self.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
        self.user32.UpdateWindow(self.hwnd)
        if self.on_ready is not None:
            self.on_ready()
        message = self.MSG()
        while True:
            result = int(self.user32.GetMessageW(ctypes.byref(message), None, 0, 0))
            if result == 0:
                return 0
            if result < 0:
                return 2
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))

    def close(self) -> None:
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)
