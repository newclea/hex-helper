"""Official hexcore name database used to accept or reject OCR."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from pathlib import Path


SLOTS = ("LEFT", "CENTER", "RIGHT")
_HUD_TAGS = frozenset(
    {
        "伤害",
        "复原力",
        "复苏力",
        "坦度",
        "爆发力",
        "机动",
        "全能",
        "成长",
        "经济",
        "暴击几率",
        "星界力",
        "圣毅力",
        "任务",
        "功能",
    }
)
_EXTRA_DISPLAY_NAMES: dict[str, tuple[str, ...]] = {}


def _clean(value: Any, *, limit: int = 64) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    return text[:limit] if text else None


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _normalize_key(value: str) -> str:
    return "".join(character for character in _fold(value) if character.isalnum())


def _compact(value: str) -> str:
    return _normalize_key(value)


def _chinese_only(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value)
        if "\u4e00" <= character <= "\u9fff"
    )


_HUD_TAG_KEYS = frozenset(
    key for key in (_normalize_key(tag) for tag in _HUD_TAGS) if key
)


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _strip_hud_affixes(key: str) -> str:
    remaining = key
    changed = True
    tags = sorted(_HUD_TAG_KEYS, key=len, reverse=True)
    while remaining and changed:
        changed = False
        for tag in tags:
            if len(remaining) <= len(tag):
                continue
            if remaining.startswith(tag):
                remaining = remaining[len(tag) :]
                changed = True
                break
            if remaining.endswith(tag):
                remaining = remaining[: -len(tag)]
                changed = True
                break
    return remaining


def _usable_needle(key: str) -> bool:
    if not key or key in _HUD_TAG_KEYS:
        return False
    if _has_cjk(key):
        return len(key) >= 3
    return len(key) >= 4


def _is_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    index = 0
    for character in haystack:
        if character == needle[index]:
            index += 1
            if index == len(needle):
                return True
    return False


def _edit_distance(left: str, right: str, bound: int) -> int:
    if abs(len(left) - len(right)) > bound:
        return bound + 1
    previous = list(range(len(right) + 1))
    current = [0] * (len(right) + 1)
    for i, left_ch in enumerate(left, 1):
        current[0] = i
        row_min = i
        for j, right_ch in enumerate(right, 1):
            substitution = previous[j - 1] + (left_ch != right_ch)
            current[j] = min(previous[j] + 1, current[j - 1] + 1, substitution)
            row_min = min(row_min, current[j])
        if row_min > bound:
            return bound + 1
        previous, current = current, previous
    return previous[len(right)]


def _prefer_record(current: "HexcoreRecord | None", candidate: "HexcoreRecord") -> HexcoreRecord:
    if current is None:
        return candidate
    current_score = (
        int("KIWI" in current.modes or "KIWI_JADE" in current.modes),
        int(current.augment_id.startswith("ARAM_")),
    )
    candidate_score = (
        int("KIWI" in candidate.modes or "KIWI_JADE" in candidate.modes),
        int(candidate.augment_id.startswith("ARAM_")),
    )
    if candidate_score > current_score:
        return candidate
    if candidate_score == current_score and candidate.augment_id < current.augment_id:
        return candidate
    return current


@dataclass(frozen=True)
class HexcoreRecord:
    augment_id: str
    name: str
    default_name: str
    modes: tuple[str, ...]


class HexcoreDatabase:
    """Every official hexcore name. OCR is accepted only against this set."""

    def __init__(self, records: Iterable[HexcoreRecord]) -> None:
        self._by_id: dict[str, HexcoreRecord] = {}
        self._by_id_folded: dict[str, HexcoreRecord] = {}
        self._by_name: dict[str, HexcoreRecord] = {}
        needles: dict[str, HexcoreRecord] = {}

        def index_label(label: str, record: HexcoreRecord) -> None:
            if not label:
                return
            self._by_name[label] = _prefer_record(self._by_name.get(label), record)
            key = _normalize_key(label)
            if key:
                self._by_name[key] = _prefer_record(self._by_name.get(key), record)
            chinese = _chinese_only(label)
            if chinese and chinese not in _HUD_TAGS and _normalize_key(chinese) == key:
                self._by_name[chinese] = _prefer_record(
                    self._by_name.get(chinese), record
                )
            if _usable_needle(key):
                needles[key] = _prefer_record(needles.get(key), record)

        for record in records:
            self._by_id[record.augment_id] = record
            folded_id = record.augment_id.casefold()
            self._by_id_folded[folded_id] = _prefer_record(
                self._by_id_folded.get(folded_id), record
            )
            extra_labels = _EXTRA_DISPLAY_NAMES.get(record.augment_id, ())
            for label in (record.name, record.default_name, *extra_labels):
                index_label(label, record)
        self._name_needles = sorted(
            needles.items(), key=lambda item: len(item[0]), reverse=True
        )

    @classmethod
    def from_names(cls, names: Mapping[str, str]) -> "HexcoreDatabase":
        records: list[HexcoreRecord] = []
        for augment_id, name in names.items():
            ident = _clean(augment_id)
            label = _clean(name)
            if ident is None or label is None:
                continue
            records.append(
                HexcoreRecord(
                    augment_id=ident,
                    name=label,
                    default_name=label,
                    modes=("KIWI",),
                )
            )
        return cls(records)

    @classmethod
    def load(cls, path: Path) -> "HexcoreDatabase":
        if not path.is_file():
            return cls([])
        raw = path.read_text(encoding="utf-8")
        import json

        payload = json.loads(raw)
        rows = payload.get("augments") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            return cls([])
        records: list[HexcoreRecord] = []
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            augment_id = _clean(item.get("id") or item.get("internal_id"))
            name = _clean(item.get("display_name") or item.get("name"))
            default_name = (
                _clean(item.get("default_name") or item.get("name_en")) or name
            )
            if augment_id is None or name is None:
                continue
            # Swarm/Strawberry stat chips reuse HUD words like 伤害. Matching
            # those as hexcore names makes two Damage cards look identical.
            if augment_id.startswith("Stat_"):
                continue
            modes_raw = item.get("modes")
            modes = (
                tuple(
                    mode
                    for mode in modes_raw
                    if isinstance(mode, str) and mode.strip()
                )
                if isinstance(modes_raw, list)
                else ("KIWI",)
            )
            records.append(
                HexcoreRecord(
                    augment_id=augment_id,
                    name=name,
                    default_name=default_name or name,
                    modes=modes,
                )
            )
        return cls(records)

    def __len__(self) -> int:
        return len(self._by_id)

    def records(self) -> list[HexcoreRecord]:
        return list(self._by_id.values())

    def names(self) -> list[str]:
        return sorted({record.name for record in self._by_id.values()})

    def contains_id(self, augment_id: str | None) -> bool:
        if not augment_id:
            return False
        return augment_id in self._by_id or augment_id.casefold() in self._by_id_folded

    def contains_name(self, name: str | None) -> bool:
        label = _clean(name)
        if not label:
            return False
        return label in self._by_name or _normalize_key(label) in self._by_name

    def resolve(
        self,
        augment_id: Any = None,
        display_name: Any = None,
        raw_text: Any = None,
    ) -> HexcoreRecord | None:
        ident = _clean(augment_id)
        if ident and ident in self._by_id:
            return self._by_id[ident]
        if ident:
            folded = ident.casefold()
            if folded in self._by_id_folded:
                return self._by_id_folded[folded]
        for value in (display_name, raw_text):
            record = self._align_text(value)
            if record is not None:
                return record
        return None

    def _align_text(self, value: Any) -> HexcoreRecord | None:
        label = _clean(value)
        if not label:
            return None
        chinese = _chinese_only(label)
        key = _normalize_key(label)
        if chinese in _HUD_TAGS or key in _HUD_TAG_KEYS:
            return None
        if key and key in self._by_name:
            return self._by_name[key]
        stripped = _strip_hud_affixes(key)
        if (
            stripped
            and stripped != key
            and stripped not in _HUD_TAG_KEYS
            and stripped in self._by_name
        ):
            return self._by_name[stripped]
        if chinese and chinese in self._by_name:
            return self._by_name[chinese]

        haystack = stripped or key
        if not haystack or haystack in _HUD_TAG_KEYS:
            return None

        # Official name sits inside noisy OCR ("复原力会心治疗").
        contained: HexcoreRecord | None = None
        contained_len = 0
        for needle, record in self._name_needles:
            if needle in haystack or _is_subsequence(needle, haystack):
                if len(needle) > contained_len:
                    contained = record
                    contained_len = len(needle)
                elif len(needle) == contained_len and contained is not None:
                    contained = _prefer_record(contained, record)
        if contained is not None:
            return contained

        # Keep only a unique official name that contains this fragment
        # ("会心治" -> 会心治疗).
        covers: list[tuple[int, HexcoreRecord]] = []
        for needle, record in self._name_needles:
            if haystack not in needle and not _is_subsequence(haystack, needle):
                continue
            if len(haystack) < 2 or len(haystack) * 4 < len(needle) * 3:
                continue
            covers.append((len(needle), record))
        if covers:
            covers.sort(key=lambda item: item[0], reverse=True)
            best_len = covers[0][0]
            best = covers[0][1]
            tied = [
                record
                for length, record in covers
                if length == best_len
                and _normalize_key(record.name) != _normalize_key(best.name)
            ]
            if not tied:
                return best

        bound = 1 if len(haystack) >= 4 else 0
        if len(haystack) >= 6:
            bound = 2
        if bound == 0:
            return None
        fuzzy: HexcoreRecord | None = None
        fuzzy_dist = bound + 1
        for needle, record in self._name_needles:
            if abs(len(needle) - len(haystack)) > bound:
                continue
            distance = _edit_distance(haystack, needle, bound)
            if distance > bound:
                continue
            if fuzzy is None or distance < fuzzy_dist:
                fuzzy = record
                fuzzy_dist = distance
            elif distance == fuzzy_dist and fuzzy is not None:
                if record.augment_id != fuzzy.augment_id:
                    return None
        return fuzzy

    def align_offer(self, cards: Any) -> list[dict[str, str]]:
        if not isinstance(cards, list):
            return []
        by_slot: dict[str, dict[str, str]] = {}
        for item in cards:
            if not isinstance(item, Mapping):
                continue
            slot = item.get("slot")
            if slot not in SLOTS:
                continue
            record = self.resolve(
                item.get("augment_id"),
                item.get("display_name"),
                item.get("raw_text"),
            )
            if record is None:
                continue
            if any(
                existing["name"] == record.name and existing["slot"] != slot
                for existing in by_slot.values()
            ):
                continue
            by_slot[str(slot)] = {
                "slot": str(slot),
                "augment_id": record.augment_id,
                "name": record.name,
            }
        return [by_slot[slot] for slot in SLOTS if slot in by_slot]

    def complete_offer(self, cards: Any) -> list[dict[str, str]] | None:
        offer = self.align_offer(cards)
        if len(offer) != 3:
            return None
        names = [card["name"] for card in offer]
        if len(set(names)) != 3:
            return None
        return offer
