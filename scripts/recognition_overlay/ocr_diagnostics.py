"""Bounded, per-frame OCR evidence with concise, rate-limited summaries."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Mapping
from choice_semantics import is_stat_shard_choice


SLOTS = ("LEFT", "CENTER", "RIGHT")


def _cards(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    debug = payload.get("recognition_debug")
    items = debug.get("cards") if isinstance(debug, Mapping) else None
    by_slot = {
        item.get("slot"): item
        for item in items or []
        if isinstance(item, Mapping) and item.get("slot") in SLOTS
    } if isinstance(items, list) else {}
    # A missing record is different from an OCR engine that read empty text.
    return [dict(by_slot[slot]) if slot in by_slot else {
        "slot": slot, "state": "MISSING", "raw_text": None,
        "augment_id": None, "reason": "no_card_diagnostic",
    } for slot in SLOTS]


def _recognized(card: Mapping[str, Any]) -> bool:
    return card.get("state") == "RECOGNIZED" and bool(card.get("augment_id"))


def _outcome(payload: Mapping[str, Any], recognized: int) -> str:
    reason = str(payload.get("reason") or "")
    if is_stat_shard_choice(payload):
        return "non_augment_choice"
    if payload.get("accepted") is True:
        return "accepted"
    if reason.startswith("session_"):
        return "session_rejected"
    if reason.startswith("awaiting_ocr_consensus"):
        return "awaiting_consensus"
    if payload.get("duplicate") is True or reason in {
        "duplicate_offer", "same_content_already_processed",
    }:
        return "unchanged_offer"
    if payload.get("ocr_executed") is not True:
        return "ocr_not_executed"
    return "partial_recognition" if recognized < 3 else "postprocess_pending"


def _quoted(value: Any) -> str:
    # Keep multiline OCR and diagnostic text on a single log line.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class OcrDiagnostics:
    """Store every OCR attempt; throttle only repeated summaries/no-OCR frames.

    Match confidence is the matcher's ranking score, not an OCR probability.
    All engine debug fields are kept verbatim in frame_result for investigation.
    """

    def __init__(
        self, path: Path, *, clock: Callable[[], float] = time.monotonic,
        repeat_seconds: float = 5.0, max_bytes: int = 8 * 1024 * 1024,
        backup_count: int = 3,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._clock = clock
        self._repeat_seconds = repeat_seconds
        self._last_signature: str | None = None
        self._last_summary_at = 0.0
        self._suppressed = 0
        logging.info("OCR diagnostics enabled path=%s", path)

    def record(self, payload: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        cards = _cards(payload)
        recognized = sum(_recognized(card) for card in cards)
        outcome = _outcome(payload, recognized)
        detector = payload.get("raw_detector")
        detector = detector if isinstance(detector, Mapping) else {}
        stable = payload.get("stable_detector")
        stable = stable if isinstance(stable, Mapping) else {}
        identity = {
            "match_id": context.get("match_id"),
            "expected_stage": context.get("expected_stage"),
            "engine_offer_round": payload.get("offer_round"),
            "scheduler_stage": context.get("mayhem_stage"),
        }
        # Exclude frame IDs, timestamps and minor score jitter so idle capture
        # cannot bury a useful failure. Every actual OCR frame is still saved.
        signature = _quoted({
            **identity, "outcome": outcome, "reason": payload.get("reason"),
            "detail": payload.get("reason_detail"),
            "ocr_executed": payload.get("ocr_executed"),
            "cause": payload.get("reread_cause"),
            "detector": [detector.get("visible"), detector.get("reason"),
                         stable.get("visible"), stable.get("reason")],
            "cards": [{key: card.get(key) for key in (
                "slot", "state", "raw_text", "augment_id", "display_name",
                "normalized_text", "match_kind", "reason",
            )} for card in cards],
        })
        now = self._clock()
        summarize = (
            signature != self._last_signature
            or now - self._last_summary_at >= self._repeat_seconds
        )
        if payload.get("ocr_executed") is True or summarize:
            record = {
                "schema_version": 1,
                "logged_at_utc": datetime.now(timezone.utc).isoformat(),
                **identity, "context": dict(context), "outcome": outcome,
                "recognized_count": recognized,
                "unrecognized_slots": [card["slot"] for card in cards if not _recognized(card)],
                "cards": cards, "frame_result": dict(payload),
            }
            self._handler.handle(logging.LogRecord(
                "ocr_diagnostics", logging.INFO, __file__, 0,
                _quoted(record), (), None,
            ))
        if not summarize:
            self._suppressed += 1
            return
        frames = payload.get("frames")
        first_frame = frames[0] if isinstance(frames, list) and frames else {}
        first_frame = first_frame if isinstance(first_frame, Mapping) else {}
        logging.info(
            "vision OCR match=%s stage=%s engine_round=%s frame=%s "
            "cause=%s executed=%s visible=%s recognized=%d/3 outcome=%s "
            "reason=%s detail=%s repeated=%d | %s",
            identity["match_id"], identity["expected_stage"],
            identity["engine_offer_round"],
            first_frame.get("frame_id", first_frame.get("id", detector.get("frame_id"))),
            payload.get("reread_cause"), payload.get("ocr_executed"),
            detector.get("visible"), recognized, outcome, payload.get("reason"),
            _quoted(payload.get("reason_detail")), self._suppressed,
            " | ".join(
                f'{card["slot"]} state={card.get("state")} '
                f'raw={_quoted(card.get("raw_text"))} '
                f'name={_quoted(card.get("display_name"))} id={card.get("augment_id")} '
                f'match={card.get("match_kind")} score={card.get("match_confidence")} '
                f'top2={card.get("match_top2_score")} margin={card.get("match_margin")} '
                f'reason={_quoted(card.get("reason"))}'
                for card in cards
            ),
        )
        self._last_signature = signature
        self._last_summary_at = now
        self._suppressed = 0

    def close(self) -> None:
        self._handler.close()
