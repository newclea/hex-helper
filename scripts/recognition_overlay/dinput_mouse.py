"""Read the left mouse button via DirectInput, not via a window."""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from typing import Any

DIRECTINPUT_VERSION = 0x0800
DISCL_NONEXCLUSIVE = 0x00000002
DISCL_BACKGROUND = 0x00000008
DIDF_RELAXIS = 0x00000001
DIDFT_AXIS = 0x00000003
DIDFT_BUTTON = 0x0000000C
DIDFT_ANYINSTANCE = 0x00FFFF00
DIDFT_OPTIONAL = 0x80000000
HWND_MESSAGE = -3


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class DIMOUSESTATE(ctypes.Structure):
    _fields_ = [
        ("lX", ctypes.c_long),
        ("lY", ctypes.c_long),
        ("lZ", ctypes.c_long),
        ("rgbButtons", ctypes.c_ubyte * 4),
    ]


class DIOBJECTDATAFORMAT(ctypes.Structure):
    _fields_ = [
        ("pguid", ctypes.c_void_p),
        ("dwOfs", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
    ]


class DIDATAFORMAT(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("dwObjSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("dwDataSize", wintypes.DWORD),
        ("dwNumObjs", wintypes.DWORD),
        ("rgodf", ctypes.POINTER(DIOBJECTDATAFORMAT)),
    ]


def _guid(data1: int, data2: int, data3: int, data4: tuple[int, ...]) -> GUID:
    value = GUID()
    value.Data1 = data1
    value.Data2 = data2
    value.Data3 = data3
    value.Data4 = (ctypes.c_ubyte * 8)(*data4)
    return value


GUID_SYS_MOUSE = _guid(
    0x6F1D2B60, 0xD5A0, 0x11CF, (0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00)
)
IID_IDIRECTINPUT8W = _guid(
    0xBF798031, 0x483A, 0x4DA2, (0xAA, 0x99, 0x5D, 0x64, 0xED, 0x36, 0x97, 0x00)
)
GUID_X_AXIS = _guid(
    0xA36D02E0, 0xC9F3, 0x11CF, (0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00)
)
GUID_Y_AXIS = _guid(
    0xA36D02E1, 0xC9F3, 0x11CF, (0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00)
)
GUID_Z_AXIS = _guid(
    0xA36D02E2, 0xC9F3, 0x11CF, (0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00)
)


def _mouse_data_format() -> tuple[DIDATAFORMAT, Any]:
    objects = (DIOBJECTDATAFORMAT * 7)(
        DIOBJECTDATAFORMAT(ctypes.addressof(GUID_X_AXIS), 0, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0),
        DIOBJECTDATAFORMAT(ctypes.addressof(GUID_Y_AXIS), 4, DIDFT_AXIS | DIDFT_ANYINSTANCE, 0),
        DIOBJECTDATAFORMAT(
            ctypes.addressof(GUID_Z_AXIS),
            8,
            DIDFT_AXIS | DIDFT_ANYINSTANCE | DIDFT_OPTIONAL,
            0,
        ),
        DIOBJECTDATAFORMAT(None, 12, DIDFT_BUTTON | DIDFT_ANYINSTANCE, 0),
        DIOBJECTDATAFORMAT(None, 13, DIDFT_BUTTON | DIDFT_ANYINSTANCE, 0),
        DIOBJECTDATAFORMAT(None, 14, DIDFT_BUTTON | DIDFT_ANYINSTANCE | DIDFT_OPTIONAL, 0),
        DIOBJECTDATAFORMAT(None, 15, DIDFT_BUTTON | DIDFT_ANYINSTANCE | DIDFT_OPTIONAL, 0),
    )
    fmt = DIDATAFORMAT(
        ctypes.sizeof(DIDATAFORMAT),
        ctypes.sizeof(DIOBJECTDATAFORMAT),
        DIDF_RELAXIS,
        ctypes.sizeof(DIMOUSESTATE),
        7,
        objects,
    )
    return fmt, objects


def _vtable(obj: int, index: int, restype: Any, *argtypes: Any):
    pointer = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))
    table = ctypes.cast(pointer[0], ctypes.POINTER(ctypes.c_void_p))
    function = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])
    return lambda *args: function(obj, *args)


class DirectInputLeftButton:
    def __init__(self) -> None:
        self.ready = False
        self.last_error = ""
        self._device = 0
        self._input = 0
        self._hwnd = 0
        self._format_hold: Any = None

    def start(self) -> bool:
        if os.name != "nt":
            return False
        try:
            return self._open()
        except Exception as exc:
            self.last_error = str(exc)
            logging.exception("directinput mouse open failed")
            self.stop()
            return False

    def left_down(self) -> bool:
        if not self.ready or not self._device:
            return False
        state = DIMOUSESTATE()
        get_state = _vtable(
            self._device, 9, ctypes.HRESULT, wintypes.DWORD, ctypes.c_void_p
        )
        status = int(get_state(ctypes.sizeof(state), ctypes.byref(state)))
        if status != 0:
            acquire = _vtable(self._device, 7, ctypes.HRESULT)
            if int(acquire()) != 0:
                return False
            status = int(get_state(ctypes.sizeof(state), ctypes.byref(state)))
            if status != 0:
                return False
        return (state.rgbButtons[0] & 0x80) != 0

    def stop(self) -> None:
        self.ready = False
        if self._device:
            try:
                unacquire = _vtable(self._device, 8, ctypes.HRESULT)
                unacquire()
                release = _vtable(self._device, 2, ctypes.c_ulong)
                release()
            except Exception:
                pass
            self._device = 0
        if self._input:
            try:
                release = _vtable(self._input, 2, ctypes.c_ulong)
                release()
            except Exception:
                pass
            self._input = 0
        if self._hwnd:
            try:
                ctypes.WinDLL("user32", use_last_error=True).DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = 0

    def _open(self) -> bool:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        ole32.CoInitializeEx.restype = ctypes.HRESULT
        ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED

        dinput8 = ctypes.WinDLL("dinput8", use_last_error=True)
        create = dinput8.DirectInput8Create
        create.argtypes = [
            wintypes.HINSTANCE,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        create.restype = ctypes.HRESULT
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        raw = ctypes.c_void_p()
        status = int(
            create(
                kernel32.GetModuleHandleW(None),
                DIRECTINPUT_VERSION,
                ctypes.byref(IID_IDIRECTINPUT8W),
                ctypes.byref(raw),
                None,
            )
        )
        if status != 0 or not raw.value:
            self.last_error = f"DirectInput8Create {status}"
            return False
        self._input = int(raw.value)
        device = ctypes.c_void_p()
        create_device = _vtable(
            self._input,
            3,
            ctypes.HRESULT,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        )
        status = int(
            create_device(ctypes.byref(GUID_SYS_MOUSE), ctypes.byref(device), None)
        )
        if status != 0 or not device.value:
            self.last_error = f"CreateDevice {status}"
            return False
        self._device = int(device.value)

        user32 = ctypes.WinDLL("user32", use_last_error=True)
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
        self._hwnd = int(
            user32.CreateWindowExW(
                0,
                "STATIC",
                "",
                0,
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                None,
                kernel32.GetModuleHandleW(None),
                None,
            )
            or 0
        )
        if not self._hwnd:
            self.last_error = "DirectInput hwnd failed"
            return False

        fmt, objects = _mouse_data_format()
        self._format_hold = (fmt, objects)
        set_format = _vtable(self._device, 11, ctypes.HRESULT, ctypes.c_void_p)
        status = int(set_format(ctypes.byref(fmt)))
        if status != 0:
            self.last_error = f"SetDataFormat {status}"
            return False
        set_coop = _vtable(
            self._device, 13, ctypes.HRESULT, wintypes.HWND, wintypes.DWORD
        )
        status = int(
            set_coop(self._hwnd, DISCL_BACKGROUND | DISCL_NONEXCLUSIVE)
        )
        if status != 0:
            self.last_error = f"SetCooperativeLevel {status}"
            return False
        acquire = _vtable(self._device, 7, ctypes.HRESULT)
        status = int(acquire())
        if status != 0:
            self.last_error = f"Acquire {status}"
            return False
        self.ready = True
        logging.info("directinput mouse ready")
        return True
