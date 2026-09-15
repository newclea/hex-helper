"""Runtime smoke test for the independent sidecar's Win32 focus contract."""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
SIDECAR = WORKSPACE / "scripts" / "phase4" / "sidecar_window.py"
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000


def main() -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND

    before = int(user32.GetForegroundWindow() or 0)
    process = subprocess.Popen(
        [sys.executable, "-B", str(SIDECAR), "--title", "LoL Sidecar Focus Smoke"],
        cwd=WORKSPACE,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert process.stdin is not None
    process.stdin.write(
        json.dumps(
            {
                "type": "live_client_state",
                "schema_version": 1,
                "status": "READY",
                "reason": "runtime_focus_smoke",
                "player": {
                    "championName": "Ziggs",
                    "level": 7,
                    "isDead": False,
                    "respawnTimer": 0,
                },
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    process.stdin.flush()

    windows: list[int] = []
    enum_proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    @enum_proc_type
    def collect(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == process.pid:
            windows.append(int(hwnd))
        return True

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        windows.clear()
        user32.EnumWindows(collect, 0)
        if windows:
            break
        time.sleep(0.025)

    after = int(user32.GetForegroundWindow() or 0)
    hwnd = windows[0] if windows else 0
    ex_style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)) if hwnd else 0
    required_styles = (
        WS_EX_TOPMOST
        | WS_EX_TRANSPARENT
        | WS_EX_TOOLWINDOW
        | WS_EX_LAYERED
        | WS_EX_NOACTIVATE
    )
    rect = wintypes.RECT()
    hit_window = 0
    if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        point = wintypes.POINT(
            (int(rect.left) + int(rect.right)) // 2,
            (int(rect.top) + int(rect.bottom)) // 2,
        )
        hit_window = int(user32.WindowFromPoint(point) or 0)
    result = {
        "process_id": process.pid,
        "window_found": bool(hwnd),
        "foreground_before": before,
        "foreground_after": after,
        "foreground_unchanged": before == after,
        "extended_style_hex": f"0x{ex_style:08X}",
        "topmost": bool(ex_style & WS_EX_TOPMOST),
        "transparent": bool(ex_style & WS_EX_TRANSPARENT),
        "layered": bool(ex_style & WS_EX_LAYERED),
        "toolwindow": bool(ex_style & WS_EX_TOOLWINDOW),
        "noactivate": bool(ex_style & WS_EX_NOACTIVATE),
        "window_from_point": hit_window,
        "cross_process_hit_test_passthrough": bool(hit_window and hit_window != hwnd),
    }

    process.stdin.close()
    return_code = process.wait(timeout=3.0)
    stderr = process.stderr.read() if process.stderr is not None else ""
    result["exit_code"] = return_code
    result["stderr"] = stderr
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["window_found"] and result["foreground_unchanged"] and (
        ex_style & required_styles
    ) == required_styles and result["cross_process_hit_test_passthrough"] and (
        return_code == 0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
