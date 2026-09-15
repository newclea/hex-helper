"""Resolve data, vision, and user-state locations for the overlay EXE."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "LoLRecognitionOverlay"


def frozen() -> bool:
    return getattr(sys, "frozen", False) is True


def executable_dir() -> Path:
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundle_dir() -> Path:
    if frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def workspace_root() -> Path:
    return executable_dir() if frozen() else Path(__file__).resolve().parents[2]


def data_file(*parts: str) -> Path:
    candidates = [
        bundle_dir().joinpath(*parts),
        executable_dir().joinpath(*parts),
        workspace_root().joinpath(*parts),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def champion_catalog_path() -> Path:
    packaged = data_file("data", "champions", "champions_zh_CN.json")
    if packaged.is_file():
        return packaged
    return data_file("Scrape", "raw", "shared", "champions_zh_CN.json")


def augment_catalog_path() -> Path:
    kiwi = data_file("data", "knowledge", "kiwi_augments.zh-CN.json")
    if kiwi.is_file():
        return kiwi
    return data_file("data", "knowledge", "augments.zh-CN.json")


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def history_path() -> Path:
    return user_data_dir() / "selected_hexcores.jsonl"


def strategy_path() -> Path:
    return user_data_dir() / "strategy.json"


def log_path() -> Path:
    return user_data_dir() / "overlay.log"


def overlay_config_path() -> Path:
    return executable_dir() / "overlay.json"


def vision_workspace() -> Path:
    path = user_data_dir() / "vision_runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def vision_home() -> Path:
    marker = ("data", "knowledge", "augment_icons", "manifest.json")
    for root in (bundle_dir(), executable_dir(), workspace_root()):
        if root.joinpath(*marker).is_file():
            return root
    return workspace_root()


def find_vision_exe(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    # Frozen onefile extracts the embedded vision engine into _MEIPASS.
    if frozen():
        candidates.append(bundle_dir() / "lol_augment_assistant.exe")
    candidates.extend(
        [
            executable_dir() / "lol_augment_assistant.exe",
            workspace_root() / "outputs" / "tmp" / "build" / "bin" / "lol_augment_assistant.exe",
            workspace_root() / "result" / "bin" / "lol_augment_assistant.exe",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None
