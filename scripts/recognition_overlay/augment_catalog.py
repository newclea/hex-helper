"""Map technical augment IDs to Chinese display names."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from hexcore_database import HexcoreDatabase, HexcoreRecord


class AugmentCatalog:
    def __init__(
        self,
        names: Mapping[str, str],
        database: HexcoreDatabase | None = None,
    ) -> None:
        self._names = dict(names)
        self._database = database or HexcoreDatabase.from_names(names)

    @classmethod
    def load(cls, path: Path) -> "AugmentCatalog":
        database = HexcoreDatabase.load(path)
        names = {record.augment_id: record.name for record in database.records()}
        return cls(names, database)

    @property
    def database(self) -> HexcoreDatabase:
        return self._database

    def label(self, augment_id: Any, fallback: str | None = None) -> str:
        record = self._database.resolve(augment_id=augment_id)
        if record is not None:
            return record.name
        if not isinstance(augment_id, str) or not augment_id.strip():
            return fallback or "未知海克斯"
        if fallback:
            return fallback
        return augment_id.strip()

    def resolve(
        self,
        augment_id: Any = None,
        display_name: Any = None,
        raw_text: Any = None,
    ) -> HexcoreRecord | None:
        return self._database.resolve(augment_id, display_name, raw_text)

    def align_offer(self, cards: Any) -> list[dict[str, str]]:
        return self._database.align_offer(cards)

    def complete_offer(self, cards: Any) -> list[dict[str, str]] | None:
        return self._database.complete_offer(cards)
