"""Persist match hero and the last viewed recommendation tab, not a locked plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StrategyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, match_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            value = json.loads(self.path.read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("match_id") != match_id:
            return None
        return value

    def save(self, *, match_id: str, hero: str, strategy_id: str) -> None:
        payload = {
            "schema_version": 1,
            "match_id": match_id,
            "hero": hero,
            "strategy_id": strategy_id,
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        temporary.replace(self.path)

    def clear(self, match_id: str) -> None:
        """Invalidate only this match's choice after a confirmed hero swap."""
        if self.load(match_id) is not None:
            self.path.unlink(missing_ok=True)
