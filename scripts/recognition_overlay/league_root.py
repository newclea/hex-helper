"""Locate the local League install without storing remoting tokens."""

from __future__ import annotations

import os
from pathlib import Path

from overlay_config import read_config_value, update_overlay_config
from paths import executable_dir, legacy_overlay_config_path, overlay_config_path


def is_league_root(path: Path) -> bool:
    try:
        return path.is_dir() and (path / "LeagueClient").is_dir()
    except OSError:
        return False


def root_from_client_exe(exe_path: Path) -> Path | None:
    try:
        resolved = exe_path.resolve()
    except OSError:
        return None
    if resolved.name.lower() not in {
        "leagueclientux.exe",
        "leagueclient.exe",
    }:
        return None
    client_dir = resolved.parent
    if client_dir.name.lower() != "leagueclient":
        return None
    root = client_dir.parent
    return root if is_league_root(root) else None


def _read_config_root() -> Path | None:
    for path in (overlay_config_path(), legacy_overlay_config_path()):
        value = read_config_value("league_root", (path,))
        if isinstance(value, str) and value.strip():
            return Path(value.strip())
    return None


def _read_text_root() -> Path | None:
    path = executable_dir() / "league_root.txt"
    if not path.is_file():
        return None
    try:
        line = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except OSError:
        return None
    return Path(line) if line else None


def discover_from_running_client() -> Path | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    th32cs_snapprocess = 0x00000002
    process_query_limited = 0x1000
    invalid = wintypes.HANDLE(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if snapshot == invalid:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            name = entry.szExeFile.lower()
            if name in {"leagueclientux.exe", "leagueclient.exe"}:
                handle = kernel32.OpenProcess(
                    process_query_limited, False, entry.th32ProcessID
                )
                if handle:
                    try:
                        length = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(length.value)
                        if kernel32.QueryFullProcessImageNameW(
                            handle, 0, buffer, ctypes.byref(length)
                        ):
                            found = root_from_client_exe(Path(buffer.value))
                            if found is not None:
                                return found
                    finally:
                        kernel32.CloseHandle(handle)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def persist_league_root(root: Path) -> None:
    update_overlay_config(overlay_config_path(), {"league_root": str(root)})


def resolve_league_root(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env = os.environ.get("LOL_ASSISTANT_LEAGUE_ROOT")
    if env:
        candidates.append(Path(env))
    config_root = _read_config_root()
    if config_root is not None:
        candidates.append(config_root)
    text_root = _read_text_root()
    if text_root is not None:
        candidates.append(text_root)
    running = discover_from_running_client()
    if running is not None:
        candidates.append(running)

    for candidate in candidates:
        try:
            resolved = candidate.expanduser()
            if not resolved.is_absolute():
                resolved = (executable_dir() / resolved).resolve()
            else:
                resolved = resolved.resolve()
        except OSError:
            continue
        if is_league_root(resolved):
            persist_league_root(resolved)
            os.environ["LOL_ASSISTANT_LEAGUE_ROOT"] = str(resolved)
            return resolved
    return None
