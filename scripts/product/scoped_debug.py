"""Emit opt-in diagnostics for one narrowly scoped feature."""

from __future__ import annotations

import logging
from typing import Iterable


SUPPORTED_DEBUG_SUBMODES = frozenset({"game-result", "hex-refresh", "speech"})
_enabled_submodes: frozenset[str] = frozenset()


def configure_debug_submodes(submodes: Iterable[str]) -> None:
    global _enabled_submodes
    requested = frozenset(submodes)
    unknown = requested - SUPPORTED_DEBUG_SUBMODES
    if unknown:
        raise ValueError(f"unsupported debug submode: {sorted(unknown)[0]}")
    _enabled_submodes = requested


def scoped_debug(submode: str, message: str, *args: object) -> None:
    if submode not in _enabled_submodes:
        return
    logging.info("DEBUG[%s] " + message, submode, *args)
