"""Pure view-model for the recognition overlay."""

from __future__ import annotations

import math
import logging
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from collections.abc import Callable

from augment_catalog import AugmentCatalog
from choice_semantics import is_stat_shard_choice
from hexcore_gate import (
    PICK_PROBE_SECONDS,
    death_ocr_allowed,
    eligible_offer_count,
    hexcore_ocr_open,
)

OUT_OF_GAME_PHASES = frozenset(
    {
        "EndOfGame",
        "PreEndOfGame",
        "WaitingForStats",
        "Lobby",
        "Matchmaking",
        "ReadyCheck",
        "None",
        "Idle",
        "TerminatedInError",
    }
)
IN_GAME_PHASES = frozenset({"InProgress", "GameStart", "Reconnect"})
from history_store import HistoryStore


SLOTS = ("LEFT", "CENTER", "RIGHT")
SLOT_LABELS = {"LEFT": "左", "CENTER": "中", "RIGHT": "右"}
HUD_PICK_SOURCE = "hud_icon_template"
PICK_SOURCES = frozenset(
    {"sole_remaining", "flash_luma", "click_luma_flash", HUD_PICK_SOURCE}
)
HUD_SELECTION_MIN_STABLE_FRAMES = 2
HUD_SELECTION_MIN_TOP1_SCORE = 0.84
HUD_SELECTION_MIN_TOP1_MARGIN = 0.08
OFFER_VISIBILITY_GRACE_SECONDS = 1.5
OCR_PICK_EVIDENCE_SECONDS = 0.75
POST_PICK_SCAN_SECONDS = 4.0
BORDER_PICK_SOURCE = "accepted_card_border_transition"


def _ocr_processing_error(reason: str | None) -> str | None:
    """Explain post-recognition failures without presenting them as bad OCR."""
    if not reason or not reason.startswith(("session_", "reducer_")):
        return None
    if reason in {"session_invalid_path", "session_storage_error", "session_artifact_error"}:
        failure = "结果保存失败"
    elif reason == "session_closed":
        failure = "本轮识别已中断"
    elif reason == "session_serialization_error":
        failure = "结果处理失败"
    else:
        failure = "本轮结果校验未通过"
    return f"三张海克斯已读到，但{failure}，正在重试。可点击猫咪重识，详情已记入日志。"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _text(value: Any, *, limit: int = 64) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    return text[:limit] if text else None


def _int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if type(value) is not int or not minimum <= value <= maximum:
        return None
    return value


def _ocr_preview(cards: Any, catalog: AugmentCatalog) -> list[dict[str, str]]:
    preview: list[dict[str, str]] = []
    if not isinstance(cards, list):
        return preview
    by_slot: dict[str, Mapping[str, Any]] = {}
    for item in cards:
        if isinstance(item, Mapping) and item.get("slot") in SLOTS:
            by_slot[str(item.get("slot"))] = item
    for slot in SLOTS:
        item = by_slot.get(slot, {})
        raw = _text(item.get("raw_text"), limit=80) or _text(
            item.get("display_name"), limit=80
        )
        record = catalog.resolve(
            item.get("augment_id"),
            item.get("display_name"),
            item.get("raw_text"),
        )
        preview.append(
            {
                "slot": slot,
                "raw": raw or "空",
                "aligned": record.name if record is not None else "未对齐",
            }
        )
    return preview


def _empty_ocr_preview() -> list[dict[str, str]]:
    return [
        {"slot": slot, "raw": "空", "aligned": "未对齐"} for slot in SLOTS
    ]


def _offer_key(cards: list[dict[str, str]] | None) -> tuple[tuple[str, str], ...]:
    if not cards:
        return ()
    keys: list[tuple[str, str]] = []
    for card in cards:
        slot = str(card.get("slot") or "")
        identity = str(card.get("augment_id") or card.get("name") or "")
        keys.append((slot, identity))
    return tuple(keys)


def _offer_stage(payload: Mapping[str, Any]) -> tuple[bool, int | None]:
    """Return whether a logical-round marker was supplied and its valid value."""

    for key in ("offer_stage", "offer_round"):
        if key in payload:
            return True, _int(payload.get(key), minimum=1, maximum=4)
    current = payload.get("current_offer")
    if isinstance(current, Mapping):
        for key in ("offer_stage", "offer_round"):
            if key in current:
                return True, _int(current.get(key), minimum=1, maximum=4)
    return False, None


def _bounded_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return None
    return score


def _slot_cards(*groups: list[dict[str, str]] | None) -> list[dict[str, str]]:
    by_slot: dict[str, dict[str, str]] = {}
    for cards in groups:
        if not cards:
            continue
        for card in cards:
            slot = card.get("slot")
            if slot not in SLOTS or not card.get("name"):
                continue
            by_slot[str(slot)] = card
    return [by_slot[slot] for slot in SLOTS if slot in by_slot]


def _hexcore_change_text(
    previous: list[dict[str, str]] | None,
    current: list[dict[str, str]] | None,
) -> str:
    before = _offer_key(previous)
    after = _offer_key(current)
    if before == after:
        if not after:
            return "海克斯未变化：本帧仍没有三选一"
        names = "、".join(card.get("name") or "?" for card in (current or []))
        return f"海克斯未变化：{names}"
    if not before and after:
        names = "、".join(card.get("name") or "?" for card in current or [])
        return f"海克斯已变化：出现 {names}"
    if before and not after:
        names = "、".join(card.get("name") or "?" for card in previous or [])
        return f"海克斯已变化：三选一从画面消失（上次 {names}）"
    old_names = "、".join(card.get("name") or "?" for card in previous or [])
    new_names = "、".join(card.get("name") or "?" for card in current or [])
    return f"海克斯已变化：{old_names} → {new_names}"


def _mismatch_note(cards: Any) -> str:
    labels = {"LEFT": "左", "CENTER": "中", "RIGHT": "右"}
    by_slot: dict[str, str] = {}
    if isinstance(cards, list):
        for item in cards:
            if not isinstance(item, Mapping):
                continue
            slot = item.get("slot")
            if slot not in labels:
                continue
            raw = _text(item.get("display_name"), limit=24) or _text(
                item.get("raw_text"), limit=24
            )
            by_slot[str(slot)] = raw or "空"
    parts = [f"{labels[slot]}[{by_slot.get(slot, '空')}]" for slot in SLOTS]
    return "OCR " + " ".join(parts)


class RecognitionViewModel:
    def __init__(
        self,
        *,
        catalog: AugmentCatalog,
        store: HistoryStore,
        match_id: str | None = None,
        champion_label: Callable[[int], str] | None = None,
        champion_alias: Callable[[str], str | None] | None = None,
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._champion_label = champion_label
        self._champion_alias = champion_alias
        self.match_id = match_id or f"local:{uuid4().hex}"
        self._game_id: int | None = None
        self._previous_match_id: str | None = None
        self._retired_game_id: int | None = None
        self.lcu_status = "UNKNOWN"
        self.lcu_reason = "等待客户端"
        self.phase: str | None = None
        self._entered_live_match = False
        self._awaiting_new_champ_select = False
        self.game_result: str | None = None
        self.champion: str | None = None
        self.live_champion: str | None = None
        self.game_mode: str | None = None
        self.bench: list[str] = []
        self.offer: list[dict[str, str]] = []
        self.last_offer: list[dict[str, str]] = []
        self.offer_round: int | None = None
        self.selected: list[dict[str, Any]] = []
        self.mayhem_status: str | None = None
        self.mayhem_phase: str | None = None
        self.mayhem_stage: int | None = None
        self.mayhem_threshold: int | None = None
        self.mayhem_pending: int | None = None
        self.live_level: int | None = None
        self.live_is_dead: bool | None = None
        self.live_game_time: float | None = None
        self._reset_death_ocr_state()
        self._post_pick_scan_until: float | None = None
        self._max_eligible: int = 0
        self.vision_status = "未启动"
        self.vision_detail: str | None = None
        self.ocr_preview: list[dict[str, str]] = []
        self._ocr_pick_slot: str | None = None
        self._ocr_pick_hits: int = 0
        self._ocr_pick_frame_ids: dict[str, int] = {}
        self._ocr_pick_at: float | None = None
        self._ocr_pick_first_frame_id: int | None = None
        self._selection_commands: list[dict[str, Any]] = []
        self._ocr_pick_source: str | None = None
        self.hover_slot: str | None = None
        self.click_seq = 0
        self.click_pending = False
        self.recognize_seq = 0
        self.last_recognize_at: str | None = None
        self.last_reason: str | None = None
        self.ocr_feedback: dict[str, str] | None = None
        self.offer_visible = False
        self.offer_refreshing = False
        self._offer_display_only = False
        self._offer_seen_at: float | None = None
        # `offer` is scoped to one raw-detector visible cycle. `last_offer`
        # may outlive that cycle, but is only evidence for resolving a pick.
        self._visible_cycle_accepted_key: tuple[tuple[str, str], ...] = ()
        # Kept separately from the display cache: a noisy partial OCR may
        # hide recommendations while a short, bound selection animation ends.
        self._accepted_pick_context: dict[str, Any] | None = None
        self.detector_reason: str | None = None
        self.last_frame_mono: float | None = None
        self.hexcore_change = "海克斯未变化：尚未 OCR"
        self._round_ocr_closed = False
        self._closed_offer_key: tuple[tuple[str, str], ...] = ()
        self._closed_offer_stage: int | None = None
        self.click_banner = "每帧重读三张并判断是否已选"
        self.note = (
            "选人待选席走 LCU。每次 OCR 都重新读左中右文字，"
            "并判断另外两张是否消失、是否已经选中。"
        )

    def _reset_death_ocr_state(self) -> None:
        self._respawned_at: float | None = None
        self._death_sequence = 0
        self._closed_death_sequence = 0
        self._latest_death_level: int | None = None
        self._death_ocr_allowed = False
        self._probe_until: float | None = None
        self._probe_key: tuple[int, int] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "previous_match_id": self._previous_match_id,
            "lcu_status": self.lcu_status,
            "lcu_reason": self.lcu_reason,
            "phase": self.phase,
            "game_result": self.game_result,
            "champion": self.champion,
            "game_mode": self.game_mode,
            "bench": list(self.bench),
            "offer": deepcopy(self.offer),
            "offer_visible": (
                not self._round_ocr_closed
                and (
                    self.offer_visible
                    or (
                        len(self.offer) == 3 and not self.offer_refreshing
                        and self._offer_seen_at is not None
                        and time.monotonic() - self._offer_seen_at
                        < OFFER_VISIBILITY_GRACE_SECONDS
                    )
                )
            ),
            "offer_detected": self.offer_visible and not self._round_ocr_closed,
            "offer_round": self.offer_round,
            "offer_refreshing": self.offer_refreshing,
            "offer_display_only": self._offer_display_only,
            "selected": deepcopy(self.selected),
            "confirmed_count": self.confirmed_count(),
            "completed_stage": self.completed_stage(),
            "mayhem_status": self.mayhem_status,
            "mayhem_phase": self.mayhem_phase,
            "mayhem_stage": self.mayhem_stage,
            "mayhem_threshold": self.mayhem_threshold,
            "mayhem_pending": self.mayhem_pending,
            "live_level": self.live_level,
            "vision_status": self.vision_status,
            "vision_detail": self.vision_detail,
            "recognize_seq": self.recognize_seq,
            "last_reason": self.last_reason,
            "ocr_feedback": deepcopy(self.ocr_feedback),
            "hexcore_change": self.hexcore_change,
            "note": self.note,
            "render_text": self.render(),
        }

    def apply_lcu(self, event: Mapping[str, Any]) -> None:
        status = _text(event.get("status"), limit=32) or "UNKNOWN"
        self.lcu_status = status.upper()
        self.lcu_reason = _text(event.get("reason"), limit=128) or self.lcu_status
        context = event.get("context")
        if self.lcu_status in {"UNAVAILABLE", "INVALID_RESPONSE"} or not isinstance(
            context, Mapping
        ):
            if self.lcu_status != "READY":
                self.phase = None
                self.bench = []
                if self.live_champion is None:
                    self.champion = None
            return
        phase = _text(context.get("gameflowPhase"), limit=64)
        previous = self.phase
        self.phase = phase
        starts_new_match = (
            phase == "ChampSelect"
            and previous != "ChampSelect"
            and (self._entered_live_match or self._awaiting_new_champ_select)
        )
        if starts_new_match:
            self._start_new_match()
        game_id = _int(
            context.get("gameId"), minimum=1, maximum=18_446_744_073_709_551_615
        )
        if phase in IN_GAME_PHASES or phase == "ChampSelect":
            if game_id is not None and game_id != self._retired_game_id:
                self._adopt_game_id(game_id)
        if phase in IN_GAME_PHASES:
            self._entered_live_match = True
            self.game_result = None
        elif not starts_new_match and (
            previous in IN_GAME_PHASES
            or (previous == "ChampSelect" and phase in OUT_OF_GAME_PHASES)
        ):
            # Keep the current match identity through post-game pages and
            # short LCU phase glitches.  A fresh identity is created only when
            # the next champion-select session actually arrives.
            self._awaiting_new_champ_select = True
        if phase in OUT_OF_GAME_PHASES:
            self._dismiss_visual_offer()
            self._apply_game_result(context)
        if phase != "ChampSelect":
            self.bench = []
        else:
            bench: list[str] = []
            raw = context.get("benchChampions")
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, Mapping):
                        name = _text(item.get("name"), limit=32)
                        if name:
                            bench.append(name)
                    elif isinstance(item, str):
                        name = _text(item, limit=32)
                        if name:
                            bench.append(name)
            self.bench = bench[:16]
        if phase != "ChampSelect":
            if phase in IN_GAME_PHASES:
                if self.live_champion is None:
                    self.champion = None
                self._arm_pick_probe()
            elif self.live_champion is None:
                self.champion = None
            return
        self._apply_champion_context(context)

    def _apply_champion_context(self, context: Mapping[str, Any]) -> None:
        champion_id = _int(context.get("championId"), minimum=1, maximum=10_000)
        named = _text(context.get("championName"), limit=32)
        if named:
            self.champion = (self._champion_alias(named) if self._champion_alias else None) or named
        elif champion_id is not None:
            if self._champion_label is not None:
                self.champion = self._champion_label(champion_id)[:32]
            else:
                self.champion = f"#{champion_id}"
        else:
            # Never show or bind the previous match's hero while a new ARAM
            # assignment is still loading.
            self.champion = None

    def _apply_game_result(self, context: Mapping[str, Any]) -> None:
        result = _text(context.get("gameResult"), limit=8)
        result_game_id = _int(
            context.get("gameId"), minimum=1, maximum=18_446_744_073_709_551_615
        )
        same_game = self._game_id is not None and result_game_id == self._game_id
        if self._entered_live_match and same_game and result in {"WIN", "LOSS"}:
            if self.game_result != result:
                logging.info("game result accepted game_id=%s result=%s source=%s",
                             result_game_id, result, context.get("gameResultRaw"))
            self.game_result = result

    def apply_vision_status(self, status: str, note: str | None = None) -> None:
        if status == "识别中":
            # The supervisor emits this when a new worker starts. Its capture
            # sequence may restart at zero, so evidence cannot span workers.
            self._ocr_pick_frame_ids.clear()
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            self._ocr_pick_at = None
            self._ocr_pick_source = None
            self._accepted_pick_context = None
            self._selection_commands.clear()
        self.vision_status = status[:80]
        if note and not self.ocr_preview and not self.click_pending:
            self.note = note[:200]

    def apply_game_state(self, payload: Mapping[str, Any]) -> None:
        # Only current frame confirmation may finish an observed offer change.
        # A queued game_state can still describe the preceding three cards.
        if self.offer_refreshing:
            return
        offer = payload.get("current_offer")
        if not isinstance(offer, Mapping):
            return
        cards = self._catalog.complete_offer(offer.get("recognitions"))
        if cards:
            stage_present, offer_stage = _offer_stage(payload)
            if stage_present and offer_stage is not None and offer_stage < self._next_pick_stage():
                return
            card_key = _offer_key(cards)
            # game_state follows an accepted frame_result.  A closed round must
            # only be reopened by current-round timing or by that accepted
            # frame, otherwise a delayed game_state can resurrect the prior
            # round immediately after the player picked a card.
            if self._round_ocr_closed:
                stage_is_next = (
                    stage_present
                    and offer_stage == self._next_pick_stage()
                    and (
                        self._closed_offer_stage is None
                        or offer_stage > self._closed_offer_stage
                    )
                )
                if not (
                    stage_is_next
                    and self.offer_visible
                    and card_key == self._visible_cycle_accepted_key
                ):
                    return
                visible_now = self.offer_visible
                seen_at = self._offer_seen_at
                accepted_key = self._visible_cycle_accepted_key
                self._reopen_hexcore_round()
                self.offer_visible = visible_now
                self._offer_seen_at = seen_at
                self._visible_cycle_accepted_key = accepted_key
            elif (
                self.offer_visible
                and card_key != self._visible_cycle_accepted_key
            ):
                # A delayed state from the preceding detector cycle must not
                # repopulate the current recommendation before a fresh
                # accepted frame arrives.
                return
            self._remember_hexcore_ocr(cards, offer_stage=offer_stage)
            self.ocr_feedback = None
            self.ocr_preview = _ocr_preview(
                offer.get("recognitions"), self._catalog
            )
            self.note = "已用知识库名字拼出当前三选一。"
            return
        if self._round_ocr_closed:
            return
        if offer.get("recognitions"):
            self.ocr_preview = _ocr_preview(
                offer.get("recognitions"), self._catalog
            )
            self.note = _mismatch_note(offer.get("recognitions"))

    def _bound_border_selection(self, payload: Mapping[str, Any]) -> bool:
        if self._offer_display_only:
            return False
        context = self._accepted_pick_context
        border = payload.get("border_debug")
        if not isinstance(context, Mapping) or not isinstance(border, Mapping):
            return False
        stage_present, stage = _offer_stage(payload)
        slot = payload.get("selected_slot")
        ids = payload.get("offer_augment_ids")
        if not (
            self.phase in IN_GAME_PHASES and not self._round_ocr_closed
            and stage_present and stage == context["stage"] and stage >= self._next_pick_stage()
            and isinstance(ids, list) and tuple(ids) == context["ids"]
            and slot in SLOTS and payload.get("selected_augment_id") == ids[SLOTS.index(slot)]
            and payload.get("candidate_scope") == "pending_offer"
            and payload.get("capture_source") == context.get("source") == border.get("source")
            and bool(context.get("source"))
            and border.get("emitted") is True
            and border.get("candidate_slot") == slot
        ):
            return False
        maximum_id = 18_446_744_073_709_551_615
        frame_id = _int(payload.get("frame_id"), minimum=0, maximum=maximum_id)
        baseline_id = _int(border.get("baseline_frame_id"), minimum=0, maximum=maximum_id)
        first_id = _int(border.get("candidate_first_frame_id"), minimum=0, maximum=maximum_id)
        last_id = _int(border.get("last_frame_id"), minimum=0, maximum=maximum_id)
        evidence_frames = _int(payload.get("evidence_frames"), minimum=2, maximum=1_000_000)
        if not (
            baseline_id is not None and baseline_id == context.get("baseline_frame_id")
            and first_id is not None and frame_id is not None and last_id == frame_id
            and baseline_id < first_id < frame_id and evidence_frames is not None
            and border.get("evidence_frames") == evidence_frames
        ):
            return False
        for field, maximum in (("baseline_age_ms", 1500.0), ("candidate_age_ms", 150.0)):
            age = border.get(field)
            if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= maximum:
                return False
        # C++ permits at most 1.5 seconds from its complete-card baseline;
        # reserve one additional second for bounded pending-event dispatch.
        seen_at = context.get("seen_at")
        if not isinstance(seen_at, (int, float)) or not 0 <= time.monotonic() - seen_at <= 2.5:
            return False
        observed = payload.get("observed_at_utc")
        try:
            stamp = datetime.fromisoformat(observed.replace("Z", "+00:00")) if isinstance(observed, str) else None
            if stamp is None or stamp.tzinfo is None or not -1 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 5:
                return False
        except (ValueError, OverflowError):
            return False
        return True

    def _track_pick_offer(self, payload: Mapping[str, Any], cards: list[dict[str, str]] | None, *, accepted: bool) -> None:
        if not cards:
            return
        ids = tuple(card.get("augment_id") for card in cards)
        stage_present, observed_stage = _offer_stage(payload)
        context = self._accepted_pick_context
        stage = observed_stage if stage_present else (context["stage"] if context and context["ids"] == ids else self._next_pick_stage())
        if stage is None or stage < self._next_pick_stage() or len(ids) != 3:
            return
        if accepted and (context is None or context["ids"] != ids or context["stage"] != stage):
            context = {"ids": ids, "stage": stage, "cards": deepcopy(cards)}
            self._accepted_pick_context = context
        if context is None or context["ids"] != ids or context["stage"] != stage:
            return
        session_id = _text(payload.get("session_id"), limit=160)
        if accepted and session_id:
            context["session_id"] = session_id
        debug = payload.get("recognition_debug")
        raw_cards = debug.get("cards") if isinstance(debug, Mapping) else None
        selection_debug = payload.get("selection_debug")
        border = selection_debug.get("border") if isinstance(selection_debug, Mapping) else None
        frames = payload.get("frames")
        frame = frames[0] if isinstance(frames, list) and len(frames) == 1 else None
        if not (
            (payload.get("ocr_executed") is True or payload.get("accepted") is True)
            and isinstance(raw_cards, list) and len(raw_cards) == 3
            and all(isinstance(card, Mapping) and card.get("state") == "RECOGNIZED" for card in raw_cards)
            and isinstance(border, Mapping) and border.get("armed") is True
            and isinstance(frame, Mapping) and frame.get("valid") is True
        ):
            return
        frame_id = _int(frame.get("id"), minimum=0, maximum=18_446_744_073_709_551_615)
        source = border.get("source")
        if frame_id is None or not isinstance(source, str) or not source.strip() or len(source) > 512 or border.get("baseline_frame_id") != frame_id:
            return
        if context.get("source") == source and frame_id <= context.get("baseline_frame_id", -1):
            return
        context.update(source=source, baseline_frame_id=frame_id, seen_at=time.monotonic())

    def apply_selection_observed(self, payload: Mapping[str, Any]) -> None:
        if self._offer_display_only:
            return
        if payload.get("recognition_status") == "UNKNOWN":
            reason = _text(payload.get("reason"), limit=80)
            if reason:
                self.vision_detail = f"已选 {reason}"
            return
        slot = _text(payload.get("selected_slot"), limit=16)
        source = _text(payload.get("source"), limit=32) or "selection_observed"
        context = self._accepted_pick_context
        stage = context["stage"] if context is not None and not self._round_ocr_closed else self._next_pick_stage()
        stage_present, observed_stage = _offer_stage(payload)
        if stage_present and (observed_stage != stage or any(
            key in payload and payload.get(key) != observed_stage for key in ("offer_stage", "offer_round")
        )):
            logging.info("selection rejected reason=stage_mismatch observed=%s expected=%s", observed_stage, stage)
            return
        bound_border = payload.get("evidence_source") == BORDER_PICK_SOURCE
        if bound_border and (source != "sole_remaining" or not self._bound_border_selection(payload)):
            logging.info("selection rejected reason=unbound_border_transition stage=%s frame=%s accepted_context=%s",
                         observed_stage, payload.get("frame_id"), self._accepted_pick_context)
            return
        if self._round_ocr_closed and source in PICK_SOURCES:
            return
        if self.offer_refreshing and source == "sole_remaining" and not bound_border:
            # Slot disappearance cannot resolve a pick against superseded
            # cards while the replacement offer is still being confirmed.
            return
        if source.casefold() == HUD_PICK_SOURCE:
            expected_stage = stage
            stage_present, observed_stage = _offer_stage(payload)
            stable_frames = _int(
                payload.get("stable_frames"), minimum=2, maximum=1_000_000
            )
            top1_score = _bounded_score(payload.get("top1_score"))
            top1_margin = _bounded_score(payload.get("top1_margin"))
            if not (
                stage_present
                and observed_stage == expected_stage
                and stable_frames is not None
                and stable_frames >= HUD_SELECTION_MIN_STABLE_FRAMES
                and top1_score is not None
                and top1_score >= HUD_SELECTION_MIN_TOP1_SCORE
                and top1_margin is not None
                and top1_margin >= HUD_SELECTION_MIN_TOP1_MARGIN
            ):
                return
            source = HUD_PICK_SOURCE
        flash = source in PICK_SOURCES
        if flash and stage == 4 and self.completed_stage() >= 4:
            # Continued detection after the fourth record is for display and
            # diagnosis; it must not overwrite a previously confirmed card.
            logging.info("selection rejected reason=history_complete stage=%s", stage)
            return
        augment_id = _text(payload.get("selected_augment_id"), limit=64)
        lookup_id = (
            augment_id
            if augment_id is not None and augment_id.upper() != "UNKNOWN"
            else None
        )
        name = None
        resolved_id = None
        known_cards = (deepcopy(self._accepted_pick_context["cards"]) if bound_border
                       else [] if self.offer_refreshing else _slot_cards(self.last_offer, self.offer))
        payload_ids = payload.get("offer_augment_ids")
        if "offer_augment_ids" in payload:
            if not (isinstance(payload_ids, list) and len(payload_ids) == 3
                    and all(isinstance(item, str) and item and item.upper() != "UNKNOWN" for item in payload_ids)
                    and len(set(payload_ids)) == 3 and slot in SLOTS):
                return
            if lookup_id is not None and lookup_id != payload_ids[SLOTS.index(slot)]:
                logging.info("selection rejected reason=payload_slot_id_mismatch slot=%s id=%s offer_ids=%s", slot, lookup_id, payload_ids)
                return
            if any(card.get("augment_id") != payload_ids[SLOTS.index(card["slot"])] for card in known_cards):
                logging.info("selection rejected reason=accepted_offer_identity_mismatch offered=%s accepted=%s", payload_ids, _offer_key(known_cards))
                return
        if slot in SLOTS:
            card = next((item for item in known_cards if item.get("slot") == slot), None)
            if card is not None:
                if lookup_id is not None and lookup_id != card.get("augment_id"):
                    logging.info("selection rejected reason=known_slot_id_mismatch slot=%s id=%s accepted=%s", slot, lookup_id, card.get("augment_id"))
                    return
                resolved_id = card.get("augment_id")
                name = card.get("name")
        if name is None:
            payload_ids = payload.get("offer_augment_ids")
            if isinstance(payload_ids, list) and slot in SLOTS:
                index = SLOTS.index(slot)
                if 0 <= index < len(payload_ids):
                    record = self._catalog.resolve(
                        payload_ids[index],
                        payload.get("display_name"),
                    )
                    if record is not None:
                        resolved_id = record.augment_id
                        name = record.name
        if name is None:
            record = self._catalog.resolve(
                augment_id=lookup_id,
                display_name=payload.get("display_name"),
                raw_text=payload.get("raw_text"),
            )
            if record is not None:
                resolved_id = record.augment_id
                name = record.name
        if name is None and slot in SLOTS:
            preview = next(
                (item for item in self.ocr_preview if item.get("slot") == slot),
                None,
            )
            aligned = preview.get("aligned") if preview else None
            if aligned and aligned != "未对齐":
                record = self._catalog.resolve(display_name=aligned)
                if record is not None:
                    resolved_id = record.augment_id
                    name = record.name
        if name is None:
            raw_name = _text(payload.get("display_name"), limit=32)
            if raw_name:
                name = raw_name
                resolved_id = lookup_id or raw_name
        if name is None or resolved_id is None:
            return
        self._record(
            stage=stage,
            augment_id=str(resolved_id),
            name=str(name),
            slot=slot,
            source=source,
        )
        recorded = next(
            (
                item
                for item in self.selected
                if item.get("augment_id") == str(resolved_id)
            ),
            None,
        )
        shown_stage = int((recorded or {}).get("stage") or stage)
        if flash:
            label = SLOT_LABELS.get(slot or "", slot or "?")
            self.note = (
                f"已自动记下第 {shown_stage} 轮：{name}（{label}）"
            )
            self.click_banner = f"▶ 选中 {label} · {name}"
            self.hexcore_change = (
                f"海克斯已变化：已选 {name}（{label}），本轮三选一结束"
            )
            self._close_hexcore_round()
            # Let C++ observe the fade/empty frames and close its persisted
            # round. This keeps capture running without reopening the bubble.
            self._post_pick_scan_until = time.monotonic() + POST_PICK_SCAN_SECONDS
        else:
            self.note = f"已记录第 {shown_stage} 轮选择：{name}"

    def apply_mayhem_selection(self, payload: Mapping[str, Any]) -> None:
        status = _text(payload.get("status"), limit=32)
        phase = _text(payload.get("phase"), limit=32)
        stage = _int(payload.get("stage"), minimum=1, maximum=8)
        threshold = _int(payload.get("thresholdLevel"), minimum=1, maximum=255)
        pending = _int(payload.get("pendingCount"), minimum=0, maximum=4)
        if status:
            self.mayhem_status = status
        if phase:
            self.mayhem_phase = phase
        if stage is not None:
            self.mayhem_stage = stage
        if "thresholdLevel" in payload:
            self.mayhem_threshold = threshold
        if pending is not None:
            self.mayhem_pending = pending
        player = payload.get("player")
        if isinstance(player, Mapping):
            self._apply_live_player(player)

    def apply_live_client(self, event: Mapping[str, Any]) -> None:
        status = _text(event.get("status"), limit=32)
        if status != "READY":
            return
        mode = _text(event.get("gameMode"), limit=32)
        if mode:
            self.game_mode = mode
        game_time = event.get("gameTime")
        if isinstance(game_time, (int, float)) and game_time >= 0:
            self.live_game_time = float(game_time)
        player = event.get("player")
        if isinstance(player, Mapping):
            self._apply_live_player(player)
            return
        raw_name = _text(event.get("championName"), limit=64)
        if raw_name is None:
            self._arm_pick_probe()
            return
        self._set_live_champion(raw_name)
        self._arm_pick_probe()

    def _apply_live_player(self, player: Mapping[str, Any]) -> None:
        level = _int(player.get("level"), minimum=1, maximum=255)
        if level is not None:
            self.live_level = level
        if type(player.get("isDead")) is bool:
            is_dead = bool(player.get("isDead"))
            if self.live_is_dead is True and is_dead is False:
                self._respawned_at = time.monotonic()
                if self._death_ocr_allowed:
                    self._extend_pick_probe(PICK_PROBE_SECONDS)
            elif self.live_is_dead is not True and is_dead is True:
                self._death_sequence += 1
                self._latest_death_level = level
                confirmed = self.confirmed_count()
                self._death_ocr_allowed = death_ocr_allowed(level, confirmed)
                if level is None:
                    decision_reason = "missing_level"
                elif level < 7:
                    decision_reason = "level_below_7"
                elif self._death_ocr_allowed:
                    decision_reason = "confirmed_pick_pending"
                else:
                    decision_reason = "confirmed_limit_reached"
                logging.info(
                    "death OCR decision match_id=%s sequence=%s level=%s "
                    "confirmed=%s allowed=%s reason=%s",
                    self.match_id,
                    self._death_sequence,
                    level,
                    confirmed,
                    self._death_ocr_allowed,
                    decision_reason,
                )
            self.live_is_dead = is_dead
            if is_dead:
                self._respawned_at = None
                if self._death_ocr_allowed:
                    self._extend_pick_probe(PICK_PROBE_SECONDS)
        raw_name = _text(player.get("championName"), limit=64)
        if raw_name:
            self._set_live_champion(raw_name)
        self._arm_pick_probe()
        self._maybe_reopen_hexcore_round()

    def _set_live_champion(self, raw_name: str) -> None:
        labeled = None
        if self._champion_alias is not None:
            labeled = self._champion_alias(raw_name)
        self.live_champion = (labeled or raw_name)[:32]
        self.champion = self.live_champion

    def confirmed_count(self) -> int:
        return min(
            4,
            sum(
                1
                for item in self.selected
                if item.get("source") in PICK_SOURCES
            ),
        )

    def completed_stage(self) -> int:
        """Logical progress can have gaps without inventing selected cards."""
        return max((
            _int(item.get("stage"), minimum=1, maximum=4) or 0
            for item in self.selected if item.get("source") in PICK_SOURCES
        ), default=0)

    def _next_pick_stage(self) -> int:
        used = {
            int(item.get("stage") or 0)
            for item in self.selected
            if int(item.get("stage") or 0) >= 1
        }
        # A later accepted offer can prove that an earlier selection was
        # missed. Never assign a current selection to that historical gap.
        return min(4, max(used, default=0) + 1)

    def owed_unconfirmed(self) -> int:
        if self.live_level is not None:
            self._max_eligible = max(
                self._max_eligible, eligible_offer_count(self.live_level)
            )
        remaining = self._max_eligible - self.completed_stage()
        return remaining if remaining > 0 else 0

    def seconds_since_respawn(self) -> float | None:
        if self._respawned_at is None:
            return None
        if self._round_ocr_closed and self._death_sequence <= self._closed_death_sequence:
            return None
        return time.monotonic() - self._respawned_at

    def _probe_active(self) -> bool:
        return self._probe_until is not None and time.monotonic() < self._probe_until

    def _extend_pick_probe(self, seconds: float) -> None:
        until = time.monotonic() + seconds
        if self._probe_until is None or until > self._probe_until:
            self._probe_until = until

    def _arm_pick_probe(self) -> None:
        initial = (
            self.completed_stage() == 0
            and self.live_level is not None
            and 3 <= self.live_level < 7
            and self.live_is_dead is not True
        )
        death_probe = self.live_is_dead is True and self._death_ocr_allowed
        if not initial and not death_probe:
            return
        key = (self._death_sequence, self.completed_stage())
        if self._probe_key != key:
            self._probe_key = key
            self._extend_pick_probe(PICK_PROBE_SECONDS)
        elif death_probe:
            self._extend_pick_probe(15.0)

    def _in_live_match(self) -> bool:
        return (
            self.live_game_time is not None
            or self.live_level is not None
            or bool(self.live_champion)
        )

    def _close_hexcore_round(self) -> None:
        if self._round_ocr_closed:
            return
        self._closed_offer_key = _offer_key(self.offer or self.last_offer)
        completed = self.completed_stage()
        self._closed_offer_stage = (
            self.offer_round
            if self.offer_round is not None and self.offer_round >= completed
            else (completed or None)
        )
        self._round_ocr_closed = True
        self._closed_death_sequence = self._death_sequence
        self._accepted_pick_context = None
        self.ocr_feedback = None
        self.offer = []
        self.offer_visible = False
        self.offer_refreshing = False
        self._offer_display_only = False
        self._offer_seen_at = None
        self._visible_cycle_accepted_key = ()
        self.last_offer = []
        self.ocr_preview = []
        self.offer_round = None
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0
        self.note = (
            f"{self.note} 本轮已结束，下次阵亡时自动检查海克斯。"
        )[:200]

    def _dismiss_visual_offer(self) -> None:
        """Drop screen-scoped card state when the game is no longer visible."""
        self._post_pick_scan_until = None
        self.offer_visible = False
        self._accepted_pick_context = None
        self.offer_refreshing = False
        self._offer_display_only = False
        self._offer_seen_at = None
        self._visible_cycle_accepted_key = ()
        self.offer = []
        self.offer_round = None
        self.ocr_preview = []
        self.detector_reason = None
        self.ocr_feedback = None
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0

    def _reopen_hexcore_round(self) -> None:
        if not self._round_ocr_closed:
            return
        self._round_ocr_closed = False
        self._accepted_pick_context = None
        self.offer = []
        self.offer_visible = False
        self.offer_refreshing = False
        self._offer_display_only = False
        self._offer_seen_at = None
        self._visible_cycle_accepted_key = ()
        self.last_offer = []
        self.ocr_preview = []
        self.offer_round = None
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0

    def _maybe_reopen_hexcore_round(self) -> None:
        if not self._round_ocr_closed:
            return
        # Repeated snapshots of one death may keep capture/OCR running, but
        # must not resurrect the card animation that was just selected.
        if self._death_sequence > self._closed_death_sequence:
            self._reopen_hexcore_round()

    def vision_process_wanted(self) -> bool:
        if self.phase in OUT_OF_GAME_PHASES:
            return False
        if self.phase == "ChampSelect" and not self._in_live_match():
            return False
        return True

    def vision_allowed(self) -> bool:
        if not self.vision_process_wanted():
            return False
        if self._post_pick_scan_until is not None and time.monotonic() < self._post_pick_scan_until:
            return True
        return hexcore_ocr_open(
            completed=self.completed_stage(),
            level=self.live_level,
            is_dead=self.live_is_dead,
            round_closed=self._round_ocr_closed,
            offer_visible=self.offer_visible and len(self.offer) == 3 and not self._round_ocr_closed,
            seconds_since_respawn=self.seconds_since_respawn(),
            death_scan_allowed=self._death_ocr_allowed,
            initial_probe_active=self._probe_active(),
        )

    def mark_left_click(self) -> None:
        self.click_seq += 1
        self.click_pending = True
        stamp = time.strftime("%H:%M:%S")
        self.vision_detail = f"left_click #{self.click_seq}"
        self.click_banner = f"▶ 左键 #{self.click_seq} · {stamp} · 正在识当前帧"
        self._extend_pick_probe(PICK_PROBE_SECONDS)
        idle = self.vision_status.startswith(("空闲", "等待", "未启动", "未找到"))
        if idle:
            self.note = (
                f"左键已记下第 {self.click_seq} 次，但识别进程未开。"
            )
            self.click_banner = (
                f"▶ 左键 #{self.click_seq} · {stamp} · 识别进程未开"
            )
            return
        self.note = f"左键已触发第 {self.click_seq} 次重识，正在整屏识字。"

    def apply_click_ack(self, payload: Mapping[str, Any]) -> None:
        reason = _text(payload.get("reason"), limit=40) or ""
        stamp = time.strftime("%H:%M:%S")
        self.click_pending = True
        if self.click_seq <= 0:
            self.click_seq = 1
        if reason == "no_current_frame":
            self.click_banner = f"▶ 自动识别 · {stamp} · 还没抓到当前帧，继续试"
            self.note = "识别进程已启动，但还没有当前画面，正在重试截屏。"
            return
        self.click_banner = f"▶ 自动识别 · {stamp} · 识别进程已接到，抓当前帧"
        self.note = "识别进程已接到定时识屏，正在整屏识字。"

    def _ocr_changes_offer(self, cards: Any) -> bool:
        """Recognized identities can invalidate cached cards; OCR noise cannot."""
        if not isinstance(cards, list):
            return False
        known = {
            card["slot"]: card for card in _slot_cards(self.last_offer, self.offer)
        }
        for card in cards:
            if not isinstance(card, Mapping):
                continue
            previous = known.get(card.get("slot"))
            if previous is None:
                continue
            state = str(card.get("state") or "").upper()
            if state and state != "RECOGNIZED":
                continue
            augment_id = _text(card.get("augment_id"))
            display_name = _text(card.get("display_name"))
            raw_text = _text(card.get("raw_text"), limit=200)
            record = None
            if self._catalog.database.contains_id(augment_id):
                record = self._catalog.resolve(augment_id=augment_id)
            elif self._catalog.database.contains_name(display_name):
                record = self._catalog.resolve(display_name=display_name)
            elif state == "RECOGNIZED" and self._catalog.database.contains_name(raw_text):
                record = self._catalog.resolve(display_name=raw_text)
            if record is None:
                continue
            if previous.get("augment_id"):
                changed = record.augment_id != previous["augment_id"]
            else:
                changed = record.name != previous.get("name")
            if changed:
                return True
        return False

    def apply_frame_result(self, payload: Mapping[str, Any]) -> None:
        stage_present, frame_stage = _offer_stage(payload)
        if stage_present and frame_stage is not None and frame_stage < self._next_pick_stage():
            # Death opens a new observation cycle, not permission to replay a
            # queued frame from an already recorded earlier round.
            return
        reason = _text(payload.get("reason"), limit=80)
        frames = payload.get("frames")
        size = None
        if isinstance(frames, list) and frames and isinstance(frames[0], Mapping):
            width = _int(frames[0].get("width"), minimum=1, maximum=16_384)
            height = _int(frames[0].get("height"), minimum=1, maximum=16_384)
            if width is not None and height is not None:
                size = f"{width}x{height}"
        self.hover_slot = None
        reread = payload.get("reread_offer") is True
        cause = _text(payload.get("reread_cause"), limit=24)
        auto_reread = cause in {"interval", "left_click"}
        self.recognize_seq += 1
        self.last_recognize_at = time.strftime("%H:%M:%S")
        self.last_reason = reason or "—"
        self.ocr_feedback = None
        raw_detector = payload.get("raw_detector")
        was_offer_visible = self.offer_visible
        selection_debug = payload.get("selection_debug")
        border = selection_debug.get("border") if isinstance(selection_debug, Mapping) else None
        context = self._accepted_pick_context
        stage_present, observed_stage = _offer_stage(payload)
        preserve_obscured = bool(
            reason == "offer_obscured_existing_cards_present"
            and self.phase in IN_GAME_PHASES and not self._round_ocr_closed
            and isinstance(raw_detector, Mapping) and raw_detector.get("visible") is False
            and isinstance(border, Mapping) and border.get("three_card_tops_visible") is True
            and context is not None and len(self.offer) == 3
            and _offer_key(self.offer) == _offer_key(context["cards"])
            and self.offer_round == context["stage"]
            and (not stage_present or observed_stage == context["stage"])
            and (not context.get("source") or border.get("source") == context["source"])
        )
        scan_allowed = self.vision_allowed()
        debug_cards = payload.get("recognition_debug")
        complete_cards = (
            self._catalog.complete_offer(debug_cards.get("cards"))
            if isinstance(debug_cards, Mapping) else None
        )
        proven_offer = payload.get("accepted") is True and complete_cards is not None
        self.offer_visible = False
        if isinstance(raw_detector, Mapping):
            self.offer_visible = (
                raw_detector.get("visible") is True and (scan_allowed or proven_offer)
            ) or preserve_obscured
            if self.offer_visible:
                self._offer_seen_at = time.monotonic()
            self.detector_reason = _text(raw_detector.get("reason"), limit=48)
        else:
            self.detector_reason = None
        if self.offer_visible and not was_offer_visible and not preserve_obscured:
            # A detector rising edge starts a new visual offer cycle. Cached
            # cards stay in `last_offer` for pick resolution, but cannot be
            # presented again until this cycle accepts a complete OCR result.
            self.offer = []
            self.offer_round = None
            self.ocr_preview = []
            self._visible_cycle_accepted_key = ()
        self.last_frame_mono = time.monotonic()
        if auto_reread:
            prefix = "0.05s"
        elif cause == "offer_band":
            prefix = "画面变化"
        elif cause == "enter":
            prefix = "进局识屏"
        elif reread or self.click_seq:
            prefix = f"left_click #{self.click_seq}"
        else:
            prefix = ""
        if reason or size or prefix:
            parts = [part for part in (prefix, reason, size) if part]
            self.vision_detail = " ".join(parts)

        def finish(*, consider_pick: bool = True) -> None:
            if self.offer_visible and self.offer_refreshing and (
                not self.ocr_feedback or self.ocr_feedback.get("state") != "ocr_error"
            ):
                detail = (self.ocr_feedback or {}).get("message")
                self.note = (
                    f"候选已更新。{detail}" if detail
                    else "海克斯候选已变化，正在重新确认三张名字。"
                )
                self.ocr_feedback = {"state": "ocr_updating", "message": self.note}
            if consider_pick:
                self._consider_ocr_pick(payload)
            self.click_pending = False
            stamp = self.last_recognize_at or time.strftime("%H:%M:%S")
            line = f"▶ 自动识别 · {stamp} · {self.note}"
            if auto_reread:
                self.click_banner = line
            elif cause == "enter":
                self.click_banner = f"▶ 进局识屏 · {stamp} · {self.note}"
            elif reread or self.click_seq:
                self.click_banner = (
                    f"▶ 左键 #{self.click_seq} · {stamp} · {self.note}"
                )
            else:
                self.click_banner = line

        if is_stat_shard_choice(payload):
            # The same geometry is used by stat forges. Clear only display and
            # transient pick evidence; neither hiding nor a forge closes a round.
            self.offer = []
            self.offer_round = None
            self.offer_visible = False
            self._offer_seen_at = None
            self._visible_cycle_accepted_key = ()
            self.offer_refreshing = False
            self._offer_display_only = False
            self.ocr_preview = []
            self.ocr_feedback = None
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            self._ocr_pick_at = None
            self._ocr_pick_first_frame_id = None
            self.note = "当前是属性锻造器碎片选择，等待下一轮海克斯。"
            finish(consider_pick=False)
            return

        if preserve_obscured:
            # The title detector is still negative. Current visible top
            # borders can retain this already accepted offer, but cannot
            # initialize names or count as evidence of a selected card.
            self.note = "卡片文字暂被遮挡，保留本轮已确认的三张海克斯。"
            finish(consider_pick=False)
            return

        if reason == "offer_temporarily_hidden":
            # Hiding the panel does not select a card. Keep accepted identity
            # for same-round revalidation, but hide the bubble immediately.
            self.offer = []
            self.offer_visible = False
            self._offer_seen_at = None
            self._visible_cycle_accepted_key = ()
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            self._ocr_pick_at = None
            self._ocr_pick_first_frame_id = None
            self.note = "海克斯面板已收起，重新展开后继续本轮推荐。"
            finish(consider_pick=False)
            return

        debug = payload.get("recognition_debug")
        has_debug_cards = isinstance(debug, Mapping) and isinstance(
            debug.get("cards"), list
        ) and bool(debug.get("cards"))
        cards = (
            self._catalog.complete_offer(debug.get("cards"))
            if isinstance(debug, Mapping)
            else None
        )
        accepted_value = payload.get("accepted")
        display_only = bool(
            reason == "offer_recognized_history_complete"
            and cards is not None and self.offer_visible
        )
        if isinstance(debug, Mapping):
            self.ocr_preview = _ocr_preview(debug.get("cards"), self._catalog)
        candidate_changed = (
            isinstance(debug, Mapping)
            and self._ocr_changes_offer(debug.get("cards"))
        )
        refresh_conflict = reason == "session_invalid_offer" and candidate_changed
        processing_error = None if refresh_conflict else _ocr_processing_error(reason)
        legacy_rejected = processing_error is not None or reason in {
            "recognition_unknown",
            "low_confidence",
        } or bool(reason and reason.startswith("awaiting_ocr_consensus:"))
        accepted = display_only or accepted_value is True or (
            accepted_value is None
            and cards is not None
            and not legacy_rejected
        )
        stage_present, payload_stage = _offer_stage(payload)
        accepted_visible_key = (
            _offer_key(cards)
            if accepted and cards is not None and self.offer_visible
            else ()
        )
        if accepted_visible_key:
            self._visible_cycle_accepted_key = accepted_visible_key

        if self._round_ocr_closed:
            # Live Client normally reopens display state on the next death.
            # If telemetry is unavailable, a newly accepted, visibly detected
            # three-card offer is sufficient proof.  Repeated evidence for the
            # just-closed offer is ignored so the bubble stays gone after pick.
            stage_is_next = (
                stage_present
                and payload_stage == self._next_pick_stage()
                and (
                    self._closed_offer_stage is None
                    or payload_stage > self._closed_offer_stage
                )
            )
            legacy_new_content = (
                not stage_present
                and bool(accepted_visible_key)
                and accepted_visible_key != self._closed_offer_key
            )
            can_reopen_from_frame = bool(
                accepted_visible_key and (
                    stage_is_next or legacy_new_content
                    or (display_only and accepted_visible_key != self._closed_offer_key)
                )
            )
            if can_reopen_from_frame:
                visible_now = self.offer_visible
                seen_at = self._offer_seen_at
                accepted_key = self._visible_cycle_accepted_key
                self._reopen_hexcore_round()
                self.offer_visible = visible_now
                self._offer_seen_at = seen_at
                self._visible_cycle_accepted_key = accepted_key
            else:
                self.click_pending = False
                return

        if not accepted and candidate_changed:
            self.offer_refreshing = True
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            # Prevent delayed game_state events for the old offer from being
            # accepted after the changed card was already observed.
            self._visible_cycle_accepted_key = ()

        if processing_error is not None and not accepted:
            # The names reached a later processing stage, but no valid offer
            # was committed. Never recommend cached cards or infer a pick
            # from this failed update.
            self.offer = []
            self._accepted_pick_context = None
            self.offer_round = None
            self._visible_cycle_accepted_key = ()
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            self.note = processing_error
            if self.offer_visible:
                self.ocr_feedback = {"state": "ocr_error", "message": self.note}
            finish(consider_pick=False)
            return

        raw_cards = debug.get("cards") if isinstance(debug, Mapping) else None
        fully_recognized = (
            isinstance(raw_cards, list)
            and len(raw_cards) == 3
            and all(
                isinstance(card, Mapping)
                and card.get("state") == "RECOGNIZED"
                and self._catalog.database.contains_id(_text(card.get("augment_id")))
                for card in raw_cards
            )
        )
        duplicate_reconfirmed = bool(
            self.offer_refreshing
            and not accepted
            and reason == "duplicate_offer"
            and self.offer_visible
            and fully_recognized
            and cards is not None
            and _offer_key(cards) == _offer_key(self.offer)
            and (not stage_present or payload_stage == self.offer_round)
        )
        duplicate_restored = bool(
            not accepted and reason == "duplicate_offer" and self.offer_visible
            and fully_recognized and cards is not None and not self.offer
            and context is not None and stage_present and payload_stage == context["stage"]
            and tuple(card.get("augment_id") for card in cards) == context["ids"]
            and (not context.get("session_id") or payload.get("session_id") == context["session_id"])
        )
        if duplicate_restored:
            self.offer_refreshing = False
            self._visible_cycle_accepted_key = _offer_key(cards)
            self._remember_hexcore_ocr(cards, offer_stage=payload_stage)
            duplicate_reconfirmed = True
        if duplicate_reconfirmed:
            # Unlike same_content_already_processed, duplicate_offer is only
            # emitted by C++ PersistConsensus after current-frame confirmation.
            # Its strong-evidence fast path may reuse the prior frame's OCR
            # (ocr_executed=false), so that flag cannot gate this recovery.
            self.offer_refreshing = False
            self._visible_cycle_accepted_key = _offer_key(cards)

        if not display_only:
            self._track_pick_offer(payload, cards, accepted=accepted)

        if not accepted and self.offer_visible and cards is not None and not duplicate_reconfirmed:
            self.ocr_feedback = {
                "state": "ocr_confirming",
                "message": (
                    "三张名字已读到，正在连续确认。若迟迟没有结果，可点击猫咪重试。"
                    if reason and reason.startswith("awaiting_ocr_consensus:")
                    else "三张名字已读到，结果尚未确认。若迟迟没有结果，可点击猫咪重试。"
                ),
            }
        elif not accepted and self.offer_visible and not duplicate_reconfirmed:
            recognized = {}
            for card in raw_cards if isinstance(raw_cards, list) else []:
                if not isinstance(card, Mapping) or card.get("slot") not in SLOTS:
                    continue
                if card.get("state") not in (None, "RECOGNIZED"):
                    continue
                record = self._catalog.resolve(
                    card.get("augment_id"), card.get("display_name"), None,
                )
                if record is not None:
                    recognized[card["slot"]] = record.name
            positions = {"LEFT": "左", "CENTER": "中", "RIGHT": "右"}
            names = "、".join(
                f"{positions[slot]}「{recognized[slot]}」"
                for slot in SLOTS if slot in recognized
            )
            locations = {"LEFT": "左侧", "CENTER": "中间", "RIGHT": "右侧"}
            missing = "、".join(locations[slot] for slot in SLOTS if slot not in recognized)
            progress = f"已识别 {len(recognized)}/3"
            if names:
                progress += f"：{names}"
            self.ocr_feedback = {
                "state": "ocr_reading",
                "message": f"{progress}。\n{missing}尚未读齐，正在自动重试。可点击猫咪重识。",
            }

        empty_screen = reason in {
            "no_offer_on_screen",
            "no_current_frame",
        } or (reason == "recognition_unknown" and not has_debug_cards)
        if empty_screen and (reread or cause in {"left_click", "enter", "interval"}):
            if reason == "no_current_frame":
                self.note = "本帧还没抓到游戏画面，继续自动识别。"
                self._remember_hexcore_ocr(None)
            elif reason == "recognition_unknown":
                self.note = "本帧 OCR 未读全三张，沿用上次三选一。"
                self._remember_hexcore_ocr(None)
            elif cause == "enter":
                self.note = (
                    "进局已抓到当前帧，画面上没有海克斯三选一（训练营会这样）。"
                    if not self.offer
                    else "进局画面上暂时没有三选一，仍显示上次读到的三张。"
                )
                self._remember_hexcore_ocr(None)
            elif auto_reread:
                self.note = (
                    "本帧没有海克斯三选一。"
                    if not self.offer
                    else "三选一界面暂时离开画面，仍显示上次读到的三张。"
                )
                if self.detector_reason:
                    self.note = self.note[:-1] + f"（{self.detector_reason}）。"
                self._remember_hexcore_ocr(None)
            else:
                self.note = (
                    f"第 {self.click_seq} 次左键已抓到当前帧，"
                    "画面上没有海克斯三选一（训练营会这样）。"
                    if not self.offer
                    else (
                        f"第 {self.click_seq} 次左键已抓到当前帧，"
                        "三选一暂时不在画面上，仍显示上次读到的三张。"
                    )
                )
                self._remember_hexcore_ocr(None)
            finish()
            return

        if not isinstance(debug, Mapping):
            if reason == "recognition_unknown":
                self.note = "本帧 OCR 未读全三张，沿用上次三选一。"
                self._remember_hexcore_ocr(None)
                finish()
                return
            if cause == "offer_band":
                self.note = f"三选一文字区变化，已重识。{reason or ''}".strip()
            elif reread and not auto_reread:
                self.note = (
                    f"第 {self.click_seq} 次左键已重识，"
                    f"{reason or '无文字结果'}。"
                )
            else:
                self.note = {
                    "accepted_offer": "本帧已读到三选一。",
                    "duplicate_offer": "本帧仍是同一组三选一。",
                    "same_content_already_processed": "画面未变，沿用上次结果。",
                }.get(reason or "", f"本帧已识别：{reason or '无文字结果'}。")
            if reason in {
                "duplicate_offer",
                "same_content_already_processed",
                "accepted_offer",
            }:
                self._remember_hexcore_ocr(self.offer or None)
            else:
                self._remember_hexcore_ocr(None)
            finish()
            return
        aligned = self._catalog.align_offer(debug.get("cards"))
        if cards is None and not aligned:
            self.note = _mismatch_note(debug.get("cards"))
            if reason == "recognition_unknown":
                self.note = "本帧 OCR 未读全三张，沿用上次三选一。"
            elif auto_reread:
                self.note = f"本帧 OCR 未读全：{self.note}"
            elif cause == "offer_band":
                self.note = f"三选一文字区变化：{self.note}"
            elif reread:
                self.note = f"第 {self.click_seq} 次左键重识：{self.note}"
            self._remember_hexcore_ocr(None)
            finish()
            return
        if cards is None:
            names = "、".join(card.get("name") or "?" for card in aligned)
            self.note = f"正在确认三张海克斯，暂时读到：{names}"
            finish()
            return
        if not accepted:
            self.note = (
                "已重新确认当前三张海克斯。"
                if duplicate_reconfirmed
                else self.ocr_feedback["message"] if self.ocr_feedback
                else "三张名字已读到，结果尚未确认。"
            )
            finish()
            return
        self.offer_refreshing = False
        self._offer_display_only = display_only
        changed = self._remember_hexcore_ocr(cards, offer_stage=payload_stage)
        if display_only:
            self._accepted_pick_context = None
        if not changed:
            self.note = "本帧仍是同一组三选一。"
            if cause == "offer_band":
                self.note = "三选一文字区有变化，名字未变。"
            elif reread and not auto_reread:
                self.note = f"第 {self.click_seq} 次左键重识，三选一未变。"
            finish()
            return
        self.note = "本帧已更新三选一。"
        if auto_reread:
            self.note = "本帧已读到三选一。"
        elif cause == "offer_band":
            self.note = "三选一文字区变化，已更新三选一。"
        elif reread:
            self.note = f"第 {self.click_seq} 次左键重识，已更新三选一。"
        finish()

    def _remember_hexcore_ocr(
        self,
        cards: list[dict[str, str]] | None,
        *,
        clear_if_empty: bool = False,
        offer_stage: int | None = None,
    ) -> bool:
        previous = list(self.offer)
        if cards:
            changed = _offer_key(previous) != _offer_key(cards)
            self.hexcore_change = _hexcore_change_text(previous, cards)
            self.offer = cards
            self.last_offer = _slot_cards(self.last_offer, cards)
            ids = tuple(card.get("augment_id") for card in cards)
            context = self._accepted_pick_context
            self.offer_round = offer_stage or (
                context["stage"] if context and context["ids"] == ids else self._next_pick_stage()
            )
            if context is None or context["ids"] != ids or context["stage"] != self.offer_round:
                self._accepted_pick_context = {"ids": ids, "stage": self.offer_round, "cards": deepcopy(cards)}
            return changed
        if clear_if_empty:
            changed = bool(previous)
            self.hexcore_change = _hexcore_change_text(previous, None)
            self.offer = []
            return changed
        if previous:
            self.hexcore_change = _hexcore_change_text(previous, previous)
            return False
        self.hexcore_change = "海克斯未变化：本帧 OCR 未读全三张"
        return False

    def take_selection_commands(self) -> list[dict[str, Any]]:
        commands = self._selection_commands
        self._selection_commands = []
        return commands

    def _emit_ocr_pick(self, slot: str, known_by_slot: dict[str, dict[str, str]], payload: Mapping[str, Any]) -> bool:
        card = known_by_slot.get(slot)
        if card is None:
            return False
        context = deepcopy(self._accepted_pick_context)
        stage = context["stage"] if context else self._next_pick_stage()
        previous = {(item.get("stage"), item.get("augment_id")) for item in self.selected}
        first_frame = self._ocr_pick_first_frame_id
        last_frame = self._ocr_pick_frame_ids.get("live")
        capture_kind = self._ocr_pick_source
        self.apply_selection_observed(
            {
                "selected_slot": slot,
                "selected_augment_id": card.get("augment_id"),
                "display_name": card.get("name"),
                "source": "sole_remaining",
                "offer_stage": stage,
            }
        )
        confirmed = any(item.get("stage") == stage and item.get("augment_id") == card.get("augment_id")
                        and item.get("source") in PICK_SOURCES for item in self.selected)
        session_id = _text(payload.get("session_id"), limit=160)
        if (confirmed and (stage, card.get("augment_id")) not in previous and context
                and session_id and session_id == context.get("session_id") and capture_kind == "live"
                and first_frame is not None and last_frame is not None and first_frame < last_frame
                and tuple(known_by_slot.get(key, {}).get("augment_id") for key in SLOTS) == context["ids"]):
            self._selection_commands.append({
                "schema_version": 1, "session_id": session_id, "match_id": self.match_id,
                "offer_stage": stage, "offer_augment_ids": list(context["ids"]),
                "selected_augment_id": card["augment_id"], "observed_at_utc": _utc_now(),
                "evidence_source": "python_fresh_ocr_title_transition", "evidence_frames": 2,
                "first_frame_id": first_frame, "last_frame_id": last_frame,
                "capture_source": context.get("source"),
            })
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0
        return confirmed

    def _consider_ocr_pick(self, payload: Mapping[str, Any]) -> bool:
        # Display previews may be cached through many non-OCR updates. Only
        # this packet's actual OCR of a new physical capture can confirm a
        # disappearance; repeating static passes is still one capture.
        if payload.get("ocr_executed") is not True:
            return False
        frames = payload.get("frames")
        frame = frames[0] if isinstance(frames, list) and len(frames) == 1 else None
        if not isinstance(frame, Mapping) or frame.get("valid") is not True:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        replay = payload.get("static_replay") is True
        frame_id = _int(
            payload.get("static_source_frame_id") if replay else frame.get("id"),
            minimum=0, maximum=18_446_744_073_709_551_615,
        )
        if frame_id is None:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        capture_kind = "static" if replay else "live"
        now = time.monotonic()
        if (
            self._ocr_pick_source != capture_kind
            or (self._ocr_pick_at is not None and (
                now < self._ocr_pick_at or now - self._ocr_pick_at > OCR_PICK_EVIDENCE_SECONDS
            ))
        ):
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            self._ocr_pick_at = None
        self._ocr_pick_source = capture_kind
        previous_id = self._ocr_pick_frame_ids.get(capture_kind)
        if previous_id is not None and frame_id <= previous_id:
            return False
        self._ocr_pick_frame_ids[capture_kind] = frame_id
        debug = payload.get("recognition_debug")
        cards = debug.get("cards") if isinstance(debug, Mapping) else None
        if not (
            isinstance(cards, list) and len(cards) == 3
            and all(isinstance(card, Mapping) and card.get("slot") in SLOTS for card in cards)
            and {card["slot"] for card in cards} == set(SLOTS)
            and all(card.get("state") in ("RECOGNIZED", "UNKNOWN") for card in cards)
        ):
            # OCR_FAILED / BACKEND_UNAVAILABLE / omitted crops mean we do not
            # know whether a card exists. They must never masquerade as blank.
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        if self._round_ocr_closed or self._offer_display_only or self.offer_refreshing or self.phase not in IN_GAME_PHASES:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        context = self._accepted_pick_context
        if (context and context.get("session_id") and payload.get("session_id")
                and context["session_id"] != payload.get("session_id")):
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        known = _slot_cards(self.last_offer, self.offer)
        known_by_slot = {str(card.get("slot")): card for card in known}
        # Unknown text is not proof that a card vanished.  System dialogs and
        # tooltips can cover a card title with unrelated copy while the card is
        # still on screen.  Only genuinely empty title crops may participate in
        # the sole-remaining pick fallback.
        if len(known) < 3:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        remaining: list[Mapping[str, Any]] = []
        vanished = 0
        obstructed = 0
        for item in cards:
            slot = str(item.get("slot") or "")
            known_card = known_by_slot.get(slot)
            raw = item.get("raw_text")
            still = (
                known_card is not None
                and item.get("state") == "RECOGNIZED"
                and isinstance(raw, str) and bool(raw.strip())
                and item.get("augment_id") == known_card.get("augment_id")
            )
            if still:
                remaining.append(item)
            elif (
                item.get("state") == "UNKNOWN"
                and isinstance(raw, str) and not raw.strip()
                and not item.get("augment_id") and not item.get("display_name")
            ):
                vanished += 1
            else:
                obstructed += 1
        if obstructed:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        if len(remaining) >= 2:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        if vanished == 3 and self._ocr_pick_slot in SLOTS and self._ocr_pick_hits >= 1:
            # Match the engine's short animation window: one real sole-card
            # OCR followed by a different, successfully empty OCR is enough.
            # A detector-only disappearance never reaches this branch.
            return self._emit_ocr_pick(self._ocr_pick_slot, known_by_slot, payload)
        if len(remaining) != 1 or vanished != 2:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        slot = str(remaining[0].get("slot") or "")
        if slot not in SLOTS:
            return False
        if self._ocr_pick_slot == slot:
            self._ocr_pick_hits += 1
        else:
            self._ocr_pick_slot = slot
            self._ocr_pick_hits = 1
            self._ocr_pick_at = now
            self._ocr_pick_first_frame_id = frame_id
        if self._ocr_pick_hits < 2:
            return False
        return self._emit_ocr_pick(slot, known_by_slot, payload)

    def _merge_hexcore_ocr(self, cards: list[dict[str, str]]) -> bool:
        previous = list(self.offer)
        by_slot: dict[str, dict[str, str]] = {
            str(card.get("slot")): dict(card)
            for card in previous
            if card.get("slot") in SLOTS
        }
        for card in cards:
            slot = card.get("slot")
            name = card.get("name")
            if slot not in SLOTS or not name:
                continue
            conflict = next(
                (
                    existing
                    for existing_slot, existing in by_slot.items()
                    if existing_slot != slot and existing.get("name") == name
                ),
                None,
            )
            if conflict is not None:
                continue
            by_slot[str(slot)] = card
        merged = [by_slot[slot] for slot in SLOTS if slot in by_slot]
        changed = _offer_key(previous) != _offer_key(merged)
        self.hexcore_change = _hexcore_change_text(previous, merged)
        self.offer = merged
        self.last_offer = _slot_cards(self.last_offer, merged)
        if merged:
            self.offer_round = self._next_pick_stage()
        return changed

    def mark_recognize_waiting(self) -> bool:
        if self.vision_status != "识别中":
            return False
        if self.last_frame_mono is None:
            return False
        if time.monotonic() - self.last_frame_mono < 0.6:
            return False
        stamp = time.strftime("%H:%M:%S")
        self.note = "等待识别引擎下一帧。"
        self.click_banner = f"▶ 自动识别 · {stamp} · {self.note}"
        return True

    def _record(
        self,
        *,
        stage: int,
        augment_id: str,
        name: str,
        slot: str | None,
        source: str,
    ) -> None:
        for item in self.selected:
            if item.get("augment_id") == augment_id:
                if source in PICK_SOURCES:
                    item["source"] = source
                if slot and not item.get("slot"):
                    item["slot"] = slot
                return
        replaced = [item for item in self.selected if item.get("stage") != stage]
        record = {
            "match_id": self.match_id,
            "stage": stage,
            "augment_id": augment_id,
            "name": name,
            "slot": slot,
            "source": source,
        }
        persisted = self._store.append(record)
        replaced.append(persisted)
        replaced.sort(key=lambda item: int(item.get("stage") or 0))
        self.selected = replaced[:4]
        self._accepted_pick_context = None
        self.offer = []
        self.offer_visible = False
        self.offer_refreshing = False
        self._offer_display_only = False
        self._offer_seen_at = None
        self._visible_cycle_accepted_key = ()
        self.ocr_preview = []
        self._probe_key = None
        self._probe_until = None
        # A pick consumes this observation window, but a later death always
        # starts another window independently of recorded stages or levels.
        self._respawned_at = None

    def _adopt_game_id(self, game_id: int) -> None:
        if game_id == self._game_id:
            return
        stable_id = f"lcu:{game_id}"
        # A new server identity proves that a match changed even if polling
        # missed its champion-select phase.  A first identity can promote the
        # current local session, but never one separated by an end/lobby page.
        if self._game_id is not None or self._awaiting_new_champ_select:
            self._start_new_match(match_id=stable_id)
        else:
            previous_id = self.match_id
            self.match_id = stable_id
            self._previous_match_id = previous_id
            # Selections made before LCU assigned its ID must follow the same
            # promotion as the strategy.  Keep append-only history intact.
            for item in self.selected:
                item["match_id"] = stable_id
                try:
                    self._store.append(dict(item))
                except OSError:
                    logging.exception("could not promote confirmed match history")
        self._game_id = game_id
        self._restore_confirmed_history()

    def _restore_confirmed_history(self) -> None:
        recent = getattr(self._store, "recent", None)
        if recent is None:
            return
        try:
            records = recent(64)
        except (OSError, UnicodeError):
            logging.exception("could not restore confirmed match history")
            return
        by_stage: dict[int, dict[str, Any]] = {}
        for item in records:
            if not isinstance(item, Mapping) or item.get("match_id") != self.match_id:
                continue
            stage = _int(item.get("stage"), minimum=1, maximum=4)
            if stage is None or item.get("source") not in PICK_SOURCES:
                continue
            known = self._catalog.resolve(
                augment_id=item.get("augment_id"), display_name=item.get("name")
            )
            if known is None:
                continue
            by_stage[stage] = {
                **dict(item), "augment_id": known.augment_id, "name": known.name
            }
        # A trustworthy later-stage selection is still real when an earlier
        # card was missed. Restore that record at its own stage; progress is
        # derived separately and no placeholder cards are manufactured.
        restored = []
        identities: set[str] = set()
        for stage in (1, 2, 3, 4):
            item = by_stage.get(stage)
            if item is None or item["augment_id"] in identities:
                continue
            identities.add(item["augment_id"])
            restored.append(item)
        if len(restored) > self.confirmed_count():
            self.selected = restored
            self._close_hexcore_round()

    def _start_new_match(self, *, match_id: str | None = None) -> None:
        self._selection_commands.clear()
        self._accepted_pick_context = None
        if self._game_id is not None:
            self._retired_game_id = self._game_id
        self._game_id = None
        self._previous_match_id = None
        self.match_id = match_id or f"local:{uuid4().hex}"
        self._entered_live_match = False
        self._awaiting_new_champ_select = False
        self.game_result = None
        self.offer_visible = False
        self.offer_refreshing = False
        self._offer_display_only = False
        self._offer_seen_at = None
        self._visible_cycle_accepted_key = ()
        self.offer = []
        self.last_offer = []
        self.ocr_preview = []
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0
        self._ocr_pick_frame_ids.clear()
        self._ocr_pick_at = None
        self._ocr_pick_source = None
        self.hover_slot = None
        self.offer_round = None
        self.selected = []
        self.mayhem_status = None
        self.mayhem_phase = None
        self.mayhem_stage = None
        self.mayhem_threshold = None
        self.mayhem_pending = None
        self.live_level = None
        self.live_is_dead = None
        self.live_game_time = None
        self._reset_death_ocr_state()
        self._post_pick_scan_until = None
        self._max_eligible = 0
        self.live_champion = None
        self.champion = None
        self.game_mode = None
        self.ocr_feedback = None
        self.hexcore_change = "海克斯未变化：新对局尚未 OCR"
        self._round_ocr_closed = False
        self._closed_offer_key = ()
        self._closed_offer_stage = None
        self.note = "新的选人阶段，已开始新的海克斯记录。"

    def _mayhem_text(self) -> str:
        status = self.mayhem_status
        if self.completed_stage() >= 4:
            prefix = "第 4 轮 · "
        elif self._round_ocr_closed:
            prefix = f"第 {self.completed_stage()} 轮 · "
        else:
            prefix = f"第 {self._next_pick_stage()} 轮 · "
        if status == "ARMED":
            threshold = (
                f"（Lv.{self.mayhem_threshold}）"
                if self.mayhem_threshold is not None
                else ""
            )
            return f"{prefix}待选中，自动识别中{threshold}"
        if status == "DEATH_TRIGGERED":
            return f"{prefix}阵亡选牌，识别中"
        if status == "FOUNTAIN_TRIGGERED":
            return f"{prefix}泉水选牌，识别中"
        if status == "QUEUED_OFFER_TRIGGERED":
            return f"{prefix}还有待选，继续自动识别"
        if status == "OFFER_DETECTED":
            return f"{prefix}已读到三选一，盯另外两张是否消失"
        if status == "SELECTION_CONFIRMED":
            return f"{prefix}本轮已记下，待选 -1"
        if status == "WINDOW_EXPIRED":
            return f"{prefix}仍待确认，继续自动识别"
        if self._round_ocr_closed:
            return f"{prefix}本轮已记下，下次阵亡时检查海克斯"
        if self.mayhem_phase == "WAITING_INITIAL_OFFER":
            return f"{prefix}等待开局第一次海克斯"
        if self.mayhem_phase == "WAITING_LEVEL":
            return f"{prefix}等待阵亡时检查海克斯"
        if self.live_level is not None:
            if self.vision_allowed():
                pending = (
                    f"，待选 {self.mayhem_pending}"
                    if self.mayhem_pending
                    else ""
                )
                return f"{prefix}Lv.{self.live_level}{pending}，自动识别中"
            dead = " · 阵亡" if self.live_is_dead else ""
            return f"{prefix}Lv.{self.live_level}{dead}，下次阵亡时检查海克斯"
        return f"{prefix}游戏窗口出现后自动 OCR"

    def render(self) -> str:
        phase = self.phase or "未知"
        champion = self.champion or "—"
        heading = "LoL 识别"
        lines = [
            heading,
            self.click_banner,
            f"阶段: {phase}",
            f"英雄: {champion}",
            f"LCU: {self.lcu_status} ({self.lcu_reason})",
            f"海克斯识别: {self.vision_status}"
            + (f" ({self.vision_detail})" if self.vision_detail else ""),
            f"海克斯时机: {self._mayhem_text()}",
            "",
            "待选席:",
        ]
        if self.bench:
            lines.append("  " + "、".join(self.bench))
        elif self.phase == "ChampSelect":
            lines.append("  选人中，待选席为空或尚未读到")
        elif self.lcu_status in {"UNAVAILABLE", "INVALID_RESPONSE", "UNKNOWN"}:
            lines.append("  等待 LCU（请先打开国服客户端）")
        else:
            lines.append("  非选人阶段")
        if self._round_ocr_closed:
            lines.extend(["", "当前海克斯:", "  —"])
        else:
            if self.offer:
                lines.extend(["", f"当前海克斯: 第 {self._next_pick_stage()} 轮"])
                by_slot = {card.get("slot"): card for card in self.offer}
                for slot in SLOTS:
                    label = SLOT_LABELS[slot]
                    card = by_slot.get(slot)
                    name = card.get("name", "—") if card else "—"
                    lines.append(f"  {label}. {name}")
                lines.append(f"海克斯变化: {self.hexcore_change}")
            else:
                lines.extend(["", "当前海克斯:", "  —"])
            lines.extend(["", "读取原文:"])
            preview = self.ocr_preview or _empty_ocr_preview()
            for item in preview:
                label = SLOT_LABELS.get(str(item.get("slot") or ""), "?")
                raw = item.get("raw") or "空"
                aligned = item.get("aligned") or "未对齐"
                lines.append(f"  {label}. 「{raw}」→ {aligned}")
            if self.detector_reason and all(
                item.get("raw") in {None, "", "空"} for item in preview
            ):
                lines.append(f"  检出: {self.detector_reason}")
        lines.extend(["", "已选海克斯:"])
        if self.selected:
            for item in self.selected:
                slot = SLOT_LABELS.get(str(item.get("slot") or ""), "")
                slot_text = f" ({slot})" if slot else ""
                lines.append(
                    f"  第 {item.get('stage', '?')} 轮 {item.get('name', '—')}{slot_text}"
                )
        else:
            lines.append("  —")
        lines.extend(["", self.note])
        return "\n".join(lines)
