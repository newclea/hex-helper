"""Pure view-model for the recognition overlay."""

from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from collections.abc import Callable

from augment_catalog import AugmentCatalog
from hexcore_gate import (
    PICK_PROBE_SECONDS,
    eligible_offer_count,
    hexcore_ocr_open,
    next_hexcore_level,
)

OUT_OF_GAME_PHASES = frozenset(
    {
        "EndOfGame",
        "PreEndOfGame",
        "WaitingForStats",
        "Lobby",
        "Matchmaking",
        "ReadyCheck",
    }
)
IN_GAME_PHASES = frozenset({"InProgress", "GameStart", "Reconnect"})
from history_store import HistoryStore


SLOTS = ("LEFT", "CENTER", "RIGHT")
SLOT_LABELS = {"LEFT": "左", "CENTER": "中", "RIGHT": "右"}
PICK_SOURCES = frozenset({"sole_remaining", "flash_luma", "click_luma_flash"})


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
        self.match_id = match_id or _utc_now()
        self.lcu_status = "UNKNOWN"
        self.lcu_reason = "等待客户端"
        self.phase: str | None = None
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
        self._respawned_at: float | None = None
        self._probe_until: float | None = None
        self._probe_key: tuple[int, int] | None = None
        self._max_eligible: int = 0
        self.vision_status = "未启动"
        self.vision_detail: str | None = None
        self.ocr_preview: list[dict[str, str]] = []
        self._ocr_pick_slot: str | None = None
        self._ocr_pick_hits: int = 0
        self.hover_slot: str | None = None
        self.click_seq = 0
        self.click_pending = False
        self.recognize_seq = 0
        self.last_recognize_at: str | None = None
        self.last_reason: str | None = None
        self.detector_reason: str | None = None
        self.last_frame_mono: float | None = None
        self.hexcore_change = "海克斯未变化：尚未 OCR"
        self._round_ocr_closed = False
        self.click_banner = "每帧重读三张并判断是否已选"
        self.note = (
            "选人待选席走 LCU。每次 OCR 都重新读左中右文字，"
            "并判断另外两张是否消失、是否已经选中。"
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "lcu_status": self.lcu_status,
            "lcu_reason": self.lcu_reason,
            "phase": self.phase,
            "champion": self.champion,
            "game_mode": self.game_mode,
            "bench": list(self.bench),
            "offer": deepcopy(self.offer),
            "offer_round": self.offer_round,
            "selected": deepcopy(self.selected),
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
        if (
            previous in IN_GAME_PHASES
            and phase is not None
            and phase not in IN_GAME_PHASES
        ):
            self._start_new_match()
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
        champion_id = _int(context.get("championId"), minimum=1, maximum=10_000)
        named = _text(context.get("championName"), limit=32)
        if named:
            self.champion = named
        elif champion_id is not None:
            if self._champion_label is not None:
                self.champion = self._champion_label(champion_id)[:32]
            else:
                self.champion = f"#{champion_id}"

    def apply_vision_status(self, status: str, note: str | None = None) -> None:
        self.vision_status = status[:80]
        if note and not self.ocr_preview and not self.click_pending:
            self.note = note[:200]

    def apply_game_state(self, payload: Mapping[str, Any]) -> None:
        offer = payload.get("current_offer")
        if not isinstance(offer, Mapping):
            return
        cards = self._catalog.complete_offer(offer.get("recognitions"))
        if cards:
            self._reopen_hexcore_round()
            self._remember_hexcore_ocr(cards)
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

    def apply_selection_observed(self, payload: Mapping[str, Any]) -> None:
        if payload.get("recognition_status") == "UNKNOWN":
            reason = _text(payload.get("reason"), limit=80)
            if reason:
                self.vision_detail = f"已选 {reason}"
            return
        slot = _text(payload.get("selected_slot"), limit=16)
        source = _text(payload.get("source"), limit=32) or "selection_observed"
        flash = source in PICK_SOURCES
        stage = self._next_pick_stage()
        augment_id = _text(payload.get("selected_augment_id"), limit=64)
        lookup_id = (
            augment_id
            if augment_id is not None and augment_id.upper() != "UNKNOWN"
            else None
        )
        name = None
        resolved_id = None
        known_cards = _slot_cards(self.last_offer, self.offer)
        if slot in SLOTS:
            card = next((item for item in known_cards if item.get("slot") == slot), None)
            if card is not None:
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
        if threshold is not None:
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
                self._extend_pick_probe(PICK_PROBE_SECONDS)
            self.live_is_dead = is_dead
            if is_dead:
                self._respawned_at = None
                self._extend_pick_probe(PICK_PROBE_SECONDS)
        raw_name = _text(player.get("championName"), limit=64)
        if raw_name:
            self._set_live_champion(raw_name)
        self._arm_pick_probe()
        self._maybe_reopen_hexcore_round()
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

    def _next_pick_stage(self) -> int:
        used = {
            int(item.get("stage") or 0)
            for item in self.selected
            if int(item.get("stage") or 0) >= 1
        }
        for stage in (1, 2, 3, 4):
            if stage not in used:
                return stage
        return 4

    def owed_unconfirmed(self) -> int:
        if self.live_level is not None:
            self._max_eligible = max(
                self._max_eligible, eligible_offer_count(self.live_level)
            )
        remaining = self._max_eligible - self.confirmed_count()
        return remaining if remaining > 0 else 0

    def seconds_since_respawn(self) -> float | None:
        if self._respawned_at is None:
            return None
        return time.monotonic() - self._respawned_at

    def _probe_active(self) -> bool:
        return self._probe_until is not None and time.monotonic() < self._probe_until

    def _extend_pick_probe(self, seconds: float) -> None:
        until = time.monotonic() + seconds
        if self._probe_until is None or until > self._probe_until:
            self._probe_until = until

    def _arm_pick_probe(self) -> None:
        completed = self.confirmed_count()
        threshold = next_hexcore_level(completed)
        if threshold is None or self.live_level is None or self.live_level < threshold:
            return
        key = (completed, threshold)
        if self._probe_key != key:
            self._probe_key = key
            self._extend_pick_probe(PICK_PROBE_SECONDS)
        elif self.live_is_dead is True:
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
        self._round_ocr_closed = True
        self.offer = []
        self.last_offer = []
        self.ocr_preview = []
        self.offer_round = None
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0
        self.note = (
            f"{self.note} 本轮识别结束，升到下一档并阵亡后再识。"
        )[:200]

    def _reopen_hexcore_round(self) -> None:
        if not self._round_ocr_closed:
            return
        self._round_ocr_closed = False
        self.offer = []
        self.last_offer = []
        self.ocr_preview = []
        self.offer_round = None
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0

    def _maybe_reopen_hexcore_round(self) -> None:
        if not self._round_ocr_closed:
            return
        if hexcore_ocr_open(
            completed=self.confirmed_count(),
            level=self.live_level,
            is_dead=self.live_is_dead,
            round_closed=True,
            seconds_since_respawn=self.seconds_since_respawn(),
        ):
            self._reopen_hexcore_round()

    def vision_process_wanted(self) -> bool:
        if self.phase in OUT_OF_GAME_PHASES:
            return False
        if self.phase == "ChampSelect" and not self._in_live_match():
            return False
        return self.confirmed_count() < 4

    def vision_allowed(self) -> bool:
        if not self.vision_process_wanted():
            return False
        return hexcore_ocr_open(
            completed=self.confirmed_count(),
            level=self.live_level,
            is_dead=self.live_is_dead,
            round_closed=self._round_ocr_closed,
            offer_visible=bool(self.offer) and not self._round_ocr_closed,
            seconds_since_respawn=self.seconds_since_respawn(),
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

    def apply_frame_result(self, payload: Mapping[str, Any]) -> None:
        debug = payload.get("recognition_debug")
        has_debug_cards = isinstance(debug, Mapping) and isinstance(
            debug.get("cards"), list
        ) and bool(debug.get("cards"))
        if self._round_ocr_closed and not has_debug_cards:
            self.click_pending = False
            return
        if self._round_ocr_closed:
            self._reopen_hexcore_round()
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
        raw_detector = payload.get("raw_detector")
        if isinstance(raw_detector, Mapping):
            self.detector_reason = _text(raw_detector.get("reason"), limit=48)
        else:
            self.detector_reason = None
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

        def finish() -> None:
            self._consider_ocr_pick()
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

        debug = payload.get("recognition_debug")
        has_debug_cards = isinstance(debug, Mapping) and isinstance(
            debug.get("cards"), list
        ) and bool(debug.get("cards"))
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
                    "session_invalid_offer": "本帧读到了牌，本轮已记下。",
                }.get(reason or "", f"本帧已识别：{reason or '无文字结果'}。")
            if reason in {
                "duplicate_offer",
                "same_content_already_processed",
                "accepted_offer",
            }:
                self._remember_hexcore_ocr(self.offer or self.last_offer or None)
            else:
                self._remember_hexcore_ocr(None)
            finish()
            return
        cards = self._catalog.complete_offer(debug.get("cards"))
        aligned = self._catalog.align_offer(debug.get("cards"))
        self.ocr_preview = _ocr_preview(debug.get("cards"), self._catalog)
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
            changed = self._merge_hexcore_ocr(aligned)
            names = "、".join(card.get("name") or "?" for card in aligned)
            if not changed:
                self.note = f"本帧 OCR 海克斯未变：{names}"
            else:
                self.note = f"本帧 OCR 已更新海克斯：{names}"
            if auto_reread and changed:
                self.note = f"本帧已根据 OCR 更新：{names}"
            elif cause == "offer_band":
                self.note = (
                    f"三选一文字区变化，已更新：{names}"
                    if changed
                    else f"三选一文字区变化，名字未变：{names}"
                )
            elif reread and not auto_reread:
                self.note = (
                    f"第 {self.click_seq} 次左键重识，已更新：{names}"
                    if changed
                    else f"第 {self.click_seq} 次左键重识，海克斯未变：{names}"
                )
            finish()
            return
        changed = self._remember_hexcore_ocr(cards)
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
    ) -> bool:
        previous = list(self.offer)
        if cards:
            changed = _offer_key(previous) != _offer_key(cards)
            self.hexcore_change = _hexcore_change_text(previous, cards)
            self.offer = cards
            self.last_offer = _slot_cards(self.last_offer, cards)
            self.offer_round = self._next_pick_stage()
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

    def _emit_ocr_pick(self, slot: str, known_by_slot: dict[str, dict[str, str]]) -> bool:
        card = known_by_slot.get(slot)
        if card is None:
            return False
        self.apply_selection_observed(
            {
                "selected_slot": slot,
                "selected_augment_id": card.get("augment_id"),
                "display_name": card.get("name"),
                "source": "sole_remaining",
            }
        )
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0
        return True

    def _consider_ocr_pick(self) -> bool:
        if self._round_ocr_closed:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        known = _slot_cards(self.last_offer, self.offer)
        known_by_slot = {str(card.get("slot")): card for card in known}
        preview_empty = bool(self.ocr_preview) and all(
            (item.get("raw") in {None, "空"} or item.get("aligned") == "未对齐")
            for item in self.ocr_preview
        )
        if preview_empty and self._ocr_pick_slot in SLOTS and self._ocr_pick_hits >= 1:
            if len(known) >= 3:
                return self._emit_ocr_pick(self._ocr_pick_slot, known_by_slot)
        if len(known) < 3 or not self.ocr_preview:
            if not preview_empty:
                self._ocr_pick_slot = None
                self._ocr_pick_hits = 0
            return False
        remaining: list[dict[str, str]] = []
        vanished = 0
        for item in self.ocr_preview:
            slot = str(item.get("slot") or "")
            aligned = item.get("aligned")
            known_card = known_by_slot.get(slot)
            still = (
                known_card is not None
                and aligned not in {None, "未对齐"}
                and aligned == known_card.get("name")
            )
            if still:
                remaining.append(item)
            else:
                vanished += 1
        if len(remaining) >= 2:
            self._ocr_pick_slot = None
            self._ocr_pick_hits = 0
            return False
        if len(remaining) != 1 or vanished != 2:
            return False
        slot = str(remaining[0].get("slot") or "")
        if slot not in SLOTS:
            return False
        if self._ocr_pick_slot == slot:
            self._ocr_pick_hits += 1
        else:
            self._ocr_pick_slot = slot
            self._ocr_pick_hits = 1
        if self._ocr_pick_hits < 2:
            return False
        return self._emit_ocr_pick(slot, known_by_slot)

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
        self.offer = []
        self.ocr_preview = []
        self._probe_key = None
        self._probe_until = None

    def _start_new_match(self) -> None:
        self.match_id = _utc_now()
        self.offer = []
        self.last_offer = []
        self.ocr_preview = []
        self._ocr_pick_slot = None
        self._ocr_pick_hits = 0
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
        self._respawned_at = None
        self._probe_until = None
        self._probe_key = None
        self._max_eligible = 0
        self.live_champion = None
        self.game_mode = None
        self.hexcore_change = "海克斯未变化：新对局尚未 OCR"
        self._round_ocr_closed = False
        self.note = "新的选人阶段，已开始新的海克斯记录。"

    def _mayhem_text(self) -> str:
        status = self.mayhem_status
        if self.confirmed_count() >= 4:
            prefix = "第 4 轮 · "
        elif self._round_ocr_closed:
            prefix = f"第 {self.confirmed_count()} 轮 · "
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
            nxt = next_hexcore_level(self.confirmed_count())
            if nxt is None:
                return f"{prefix}四轮已记完"
            return f"{prefix}本轮已记下，等待 Lv.{nxt} 并阵亡"
        if self.mayhem_phase == "WAITING_INITIAL_OFFER":
            return f"{prefix}等待开局第一次海克斯"
        if self.mayhem_phase == "WAITING_LEVEL":
            return f"{prefix}等待 7/11/15 级"
        if self.live_level is not None:
            if self.vision_allowed():
                pending = (
                    f"，待选 {self.mayhem_pending}"
                    if self.mayhem_pending
                    else ""
                )
                return f"{prefix}Lv.{self.live_level}{pending}，自动识别中"
            dead = " · 阵亡" if self.live_is_dead else ""
            return f"{prefix}Lv.{self.live_level}{dead}，等待下一档 7/11/15 级"
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
