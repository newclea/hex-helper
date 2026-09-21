"""Conservative exclusions for clearly labelled non-augment choices."""

from typing import Any, Mapping


def is_stat_shard_choice(payload: Mapping[str, Any]) -> bool:
    """Require all three explicit forge labels; unknown OCR alone is not proof."""
    if payload.get("accepted") is True:
        return False
    debug = payload.get("recognition_debug")
    cards = debug.get("cards") if isinstance(debug, Mapping) else None
    if not isinstance(cards, list) or len(cards) != 3:
        return False
    slots = set()
    for card in cards:
        if not isinstance(card, Mapping) or card.get("slot") not in {"LEFT", "CENTER", "RIGHT"}:
            return False
        if card["slot"] in slots or card.get("state") != "UNKNOWN" or card.get("augment_id") or card.get("display_name"):
            return False
        slots.add(card["slot"])
        raw = card.get("raw_text")
        if not isinstance(raw, str):
            return False
        compact = "".join(raw.split())
        if "碎片" not in compact or "属性锻造器" not in compact:
            return False
    return len(slots) == 3
