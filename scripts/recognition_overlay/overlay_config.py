"""Read and update shared overlay settings without discarding other fields."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Mapping, Sequence


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = True


def _read_object(path: Path) -> dict[str, object] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def read_config_value(key: str, paths: Sequence[Path]) -> object | None:
    for path in paths:
        config = _read_object(path)
        if config is not None and key in config:
            return config[key]
    return None


def load_voice_settings(primary: Path, legacy: Path) -> VoiceSettings:
    paths = (primary, legacy)
    enabled_value = read_config_value("voice_enabled", paths)
    enabled = enabled_value if isinstance(enabled_value, bool) else True
    return VoiceSettings(enabled=enabled)


def _remove_temporary(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.exception("overlay config temporary cleanup failed path=%s", path)


def update_overlay_config(path: Path, changes: Mapping[str, object]) -> bool:
    current = _read_object(path) or {}
    current.update(changes)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError:
        _remove_temporary(temporary)
        LOGGER.exception("overlay config update failed path=%s", path)
        return False
