"""Local champion-id catalog for LCU bench display.

Names come from the already-shipped zh-CN scrape catalog.  This module never
downloads Data Dragon and never logs credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHAMPION_CATALOG = (
    WORKSPACE_ROOT / "data" / "champions" / "champions_zh_CN.json"
)
MAXIMUM_CHAMPION_ID = 10_000
MAXIMUM_LABEL_CHARS = 32


@dataclass(frozen=True)
class ChampionInfo:
    champion_id: int
    name: str
    title: str
    riot_id: str = ""

    @property
    def label(self) -> str:
        if self.title:
            return self.title
        if self.name:
            return self.name
        return f"#{self.champion_id}"


def _safe_visible_text(value: Any, *, limit: int = MAXIMUM_LABEL_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.strip().split())
    return text[:limit]


def _bounded_champion_id(value: Any) -> int | None:
    if type(value) is not int or not 1 <= value <= MAXIMUM_CHAMPION_ID:
        return None
    return value


class ChampionCatalog:
    def __init__(self, records: Mapping[int, ChampionInfo]) -> None:
        self._records = dict(records)
        self._by_riot_id: dict[str, ChampionInfo] = {}
        for info in self._records.values():
            for alias in (info.riot_id, info.name, info.title, f"{info.name} {info.title}"):
                if alias.strip():
                    self._by_riot_id[alias.casefold()] = info

    @classmethod
    def load(cls, path: Path | None = None) -> "ChampionCatalog":
        catalog_path = path or DEFAULT_CHAMPION_CATALOG
        records: dict[int, ChampionInfo] = {}
        if not catalog_path.is_file():
            return cls(records)
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return cls(records)
        for key, item in raw.items():
            try:
                champion_id = int(key)
            except (TypeError, ValueError):
                continue
            if _bounded_champion_id(champion_id) is None or not isinstance(item, Mapping):
                continue
            records[champion_id] = ChampionInfo(
                champion_id=champion_id,
                name=_safe_visible_text(item.get("name")),
                title=_safe_visible_text(item.get("title")),
                riot_id=_safe_visible_text(item.get("riotId"), limit=64),
            )
        return cls(records)

    def resolve(self, champion_id: int) -> ChampionInfo:
        bounded = _bounded_champion_id(champion_id)
        if bounded is None:
            return ChampionInfo(0, "", "")
        found = self._records.get(bounded)
        if found is not None:
            return found
        return ChampionInfo(bounded, "", "")

    def label(self, champion_id: int) -> str:
        return self.resolve(champion_id).label

    def label_by_riot_id(self, riot_id: str) -> str | None:
        if not isinstance(riot_id, str):
            return None
        key = " ".join(riot_id.strip().split())
        if not key:
            return None
        found = self._by_riot_id.get(key.casefold())
        if found is None:
            return None
        return found.label[:MAXIMUM_LABEL_CHARS]
