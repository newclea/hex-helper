#!/usr/bin/env python3
"""Read-only JSONL result sidecar with a non-activating Win32 host.

The protocol reducer and text renderer are platform independent.  The Win32
host is loaded only when the normal window mode is selected, so
``--dry-run-jsonl`` works in headless and non-Windows test environments.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import json
import math
import os
import sys
import threading
from collections.abc import Mapping
from typing import Any, Sequence, TextIO


SCHEMA_VERSION = 1
MAX_JSONL_BYTES = 65_536
DEFAULT_TITLE = "LoL 识别（置顶只读）"
HOTKEYS = ("Ctrl+Alt+1", "Ctrl+Alt+2", "Ctrl+Alt+3")
MAXIMUM_BENCH_CHAMPIONS = 16
MAXIMUM_SELECTED_HISTORY = 4
DEFAULT_WORK_AREA = (0, 0, 1280, 720)
SAFE_RAIL_WIDTH_RATIO = 0.18
CARD_AREA_LEFT_RATIO = 0.197
BOTTOM_HUD_TOP_RATIO = 0.68
SAFE_INSET_RATIO = 0.008
MIN_SAFE_INSET_PX = 8
MAX_SAFE_INSET_PX = 20

_SOURCE_LABELS = {
    "live_screen": "SCREEN CAPTURE / 屏幕可见信息",
    "replay": "REPLAY / 非真实动态验收",
    "synthetic": "SYNTHETIC / 仅协议测试",
    "unknown": "SOURCE UNSPECIFIED / 来源未声明",
}
_SOURCE_ALIASES = {
    "live": "live_screen",
    "live_screen": "live_screen",
    "screen_capture": "live_screen",
    "replay": "replay",
    "synthetic": "synthetic",
    "unknown": "unknown",
}
_START_TYPES = {"recommendation_session_start", "session_start"}
_RECOMMENDATION_TYPES = {
    "recommendation",
    "recommended",
    "recommended_with_knowledge_conflict",
    "fail_closed",
}
_END_TYPES = {
    "recommendation_session_complete",
    "recommendation_session_end",
    "session_complete",
    "session_end",
}
_SUCCESS_STATUSES = {"recommended", "recommended_with_knowledge_conflict"}
_HEALTH_TYPES = {"capture_health", "component_health"}
_RESTART_TYPES = {"component_restart"}
_INVALIDATION_TYPES = {"recommendation_invalidated"}
_FALLBACK_TYPES = {"fallback_changed"}
_CONTEXT_TYPES = {"live_client_state", "mayhem_selection_state", "lcu_context_state"}
_LCU_STATES = {"READY", "PARTIAL", "UNAVAILABLE", "INVALID_RESPONSE"}
_HEALTH_STATES = {
    "HEALTHY",
    "SUSPECT",
    "RESTARTING",
    "DESKTOP_FALLBACK",
    "DEGRADED",
    "STOPPED",
}
_LIVE_CLIENT_STATES = {"READY", "UNAVAILABLE", "INVALID_RESPONSE"}
_MAYHEM_STATUSES = {
    "ARMED",
    "DEATH_TRIGGERED",
    "QUEUED_OFFER_TRIGGERED",
    "OFFER_DETECTED",
    "WINDOW_EXPIRED",
}
_MAYHEM_PHASES = {
    "WAITING_INITIAL_OFFER",
    "WAITING_LEVEL",
    "ARMED",
    "TRIGGERED",
    "COMPLETE",
}


class JsonLineError(ValueError):
    """Raised when one stdin line is not safe JSONL input."""


def _safe_text(value: Any, *, limit: int = 128) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text:
        return None
    return text[:limit]


def _safe_stage(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4:
        return value
    return None


def _safe_bounded_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if type(value) is int and minimum <= value <= maximum:
        return value
    return None


def _safe_nonnegative_number(value: Any, *, maximum: float) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number) and 0.0 <= number <= maximum:
            return number
    return None


def compute_sidecar_layout(
    work_area: tuple[int, int, int, int],
    *,
    requested_x: int | None,
    requested_y: int | None,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Return a bounded sidecar rectangle for the primary work area.

    The default placement stays wholly inside the narrow rail to the left of
    the normalized card area (x >= 0.197) and above the reserved bottom HUD.
    Explicit coordinates remain supported, while width is always capped to
    the work area's 18% visibility budget.
    """

    if (
        len(work_area) != 4
        or any(type(value) is not int for value in work_area)
        or type(width) is not int
        or type(height) is not int
        or width <= 0
        or height <= 0
    ):
        raise ValueError("work area and dimensions must be positive integers")
    left, top, right, bottom = work_area
    if right <= left or bottom <= top:
        raise ValueError("work area must have positive width and height")

    work_width = right - left
    work_height = bottom - top
    inset = max(
        MIN_SAFE_INSET_PX,
        min(MAX_SAFE_INSET_PX, int(work_width * SAFE_INSET_RATIO)),
    )
    default_x = left + inset
    default_y = top + inset
    rail_width = max(1, int(work_width * SAFE_RAIL_WIDTH_RATIO))
    card_boundary = left + int(work_width * CARD_AREA_LEFT_RATIO)
    default_width_budget = max(1, card_boundary - default_x)
    bounded_width = min(width, rail_width)
    if requested_x is None:
        bounded_width = min(bounded_width, default_width_budget)

    bounded_height = height
    if requested_y is None:
        hud_boundary = top + int(work_height * BOTTOM_HUD_TOP_RATIO)
        bounded_height = min(height, max(1, hud_boundary - default_y))

    return (
        requested_x if requested_x is not None else default_x,
        requested_y if requested_y is not None else default_y,
        bounded_width,
        bounded_height,
    )


def _base_view_model() -> dict[str, Any]:
    model: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event": "waiting",
        "status": "waiting",
        "hero": None,
        "stage": None,
        "choices": [],
        "recommended": None,
        "knowledge_conflicts": [],
        "warning": None,
        "error": None,
        "selected": None,
        "selected_slot": None,
        "selected_history": [],
        "lcu_status": "UNKNOWN",
        "lcu_reason": None,
        "lcu_phase": None,
        "lcu_champion": None,
        "bench_enabled": None,
        "bench_champions": [],
        "source_kind": "unknown",
        "source_label": _SOURCE_LABELS["unknown"],
        "snapshot_id": None,
        "hotkeys": list(HOTKEYS),
        "hotkeys_enabled": False,
        "active_offer_id": None,
        "recommendation_freshness": "none",
        "resume_hotkeys_enabled": False,
        "stale_reason": None,
        "health_state": "UNKNOWN",
        "health_reason": None,
        "active_capture_state": "UNKNOWN",
        "live_client_status": "UNKNOWN",
        "live_level": None,
        "live_is_dead": None,
        "live_respawn_timer": None,
        "live_reason": None,
        "mayhem_status": None,
        "mayhem_phase": None,
        "mayhem_stage": None,
        "mayhem_threshold_level": None,
        "last_context_event": None,
        "context_warning": None,
        "terminal": False,
    }
    model["render_text"] = render_text(model)
    return model


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else event


def _event_type(event: Mapping[str, Any]) -> str | None:
    value = event.get("type", event.get("event"))
    return _safe_text(value, limit=64)


def _source_kind(
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
    fallback: str,
) -> str:
    candidates: list[Any] = []
    source = event.get("source")
    if isinstance(source, Mapping):
        candidates.append(source.get("kind"))
    elif source is not None:
        candidates.append(source)
    payload_source = payload.get("source")
    if isinstance(payload_source, Mapping):
        candidates.append(payload_source.get("kind"))
    elif payload_source is not None:
        candidates.append(payload_source)
    candidates.extend(
        [event.get("observation_source"), payload.get("observation_source")]
    )
    for candidate in candidates:
        normalized = _safe_text(candidate, limit=32)
        if normalized is not None:
            return _SOURCE_ALIASES.get(normalized.lower(), "unknown")
    return fallback if fallback in _SOURCE_LABELS else "unknown"


def _choice_label(item: Any) -> str | None:
    if isinstance(item, str):
        return _safe_text(item)
    if not isinstance(item, Mapping):
        return None
    for key in (
        "display_name",
        "augment",
        "name",
        "choice",
        "augment_name",
        "augment_id",
    ):
        label = _safe_text(item.get(key))
        if label is not None:
            return label
    return None


def _choice_slot(item: Any, default: int) -> int:
    if not isinstance(item, Mapping):
        return default
    value = item.get("slot")
    if isinstance(value, int) and not isinstance(value, bool) and value in {1, 2, 3}:
        return value
    if isinstance(value, str):
        slots = {"LEFT": 1, "CENTER": 2, "RIGHT": 3}
        return slots.get(value.strip().upper(), default)
    return default


def _choice_records(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    records: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        label = _choice_label(item)
        if label is None:
            return []
        records.append(
            {
                "slot": _choice_slot(item, index),
                "label": label,
                "recommended": False,
                "knowledge_conflict": False,
            }
        )
    return records


def _extract_choices(
    payload: Mapping[str, Any], recommended: str
) -> list[dict[str, Any]]:
    choices = _choice_records(payload.get("choices"))
    ranking = _choice_records(payload.get("ranking"))
    choice_labels = {item["label"] for item in choices}
    ranking_labels = {item["label"] for item in ranking}
    if recommended not in choice_labels and recommended in ranking_labels:
        choices = ranking
    return choices


def _extract_conflicts(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    if "knowledge_conflicts" not in payload:
        return []
    raw_conflicts = payload.get("knowledge_conflicts")
    if not isinstance(raw_conflicts, list):
        return None
    conflicts: list[dict[str, Any]] = []
    for item in raw_conflicts:
        if not isinstance(item, Mapping):
            return None
        augment = _safe_text(item.get("augment", item.get("augment_id")))
        if augment is None:
            return None
        observed_stage = None
        if "observed_stage" in item:
            observed_stage = _safe_stage(item.get("observed_stage"))
            if observed_stage is None:
                return None
        stages: list[int] = []
        if "snapshot_available_stages" in item:
            available = item.get("snapshot_available_stages")
            if not isinstance(available, list):
                return None
            for value in available:
                stage = _safe_stage(value)
                if stage is None:
                    return None
                stages.append(stage)
        conflicts.append(
            {
                "code": _safe_text(item.get("code"), limit=64)
                or "snapshot_available_stages_conflict",
                "augment": augment,
                "observed_stage": observed_stage,
                "snapshot_available_stages": stages,
                "detail": _safe_text(item.get("message"), limit=256),
            }
        )
    return conflicts


def _schema_version_is_valid(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> bool:
    for container in (event, payload):
        if "schema_version" in container:
            value = container.get("schema_version")
            if type(value) is not int or value != SCHEMA_VERSION:
                return False
    return True


def _player_context(player: Any) -> tuple[int, bool, float] | None:
    if not isinstance(player, Mapping):
        return None
    level = _safe_bounded_int(player.get("level"), minimum=1, maximum=255)
    is_dead = player.get("isDead")
    respawn_timer = _safe_nonnegative_number(
        player.get("respawnTimer"), maximum=3_600.0
    )
    if level is None or type(is_dead) is not bool or respawn_timer is None:
        return None
    return level, is_dead, respawn_timer


def _history_label(item: Any) -> str | None:
    if isinstance(item, str):
        return _safe_text(item)
    return _choice_label(item)


def _selected_history_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("selected_history", "resumed_owned", "owned"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        history: list[dict[str, Any]] = []
        for index, item in enumerate(raw, 1):
            label = _history_label(item)
            if label is None:
                return []
            stage = index
            if isinstance(item, Mapping):
                parsed_stage = _safe_stage(item.get("stage"))
                if parsed_stage is not None:
                    stage = parsed_stage
            history.append({"stage": stage, "label": label})
            if len(history) >= MAXIMUM_SELECTED_HISTORY:
                break
        if history:
            return history
    return []


def _bench_champion_record(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        label = _safe_text(item, limit=32)
        if label is None:
            return None
        return {"champion_id": None, "name": label}
    if not isinstance(item, Mapping):
        return None
    champion_id = item.get("championId", item.get("champion_id"))
    if champion_id is not None:
        champion_id = _safe_bounded_int(champion_id, minimum=1, maximum=10_000)
        if champion_id is None:
            return None
    name = _safe_text(
        item.get("name", item.get("title", item.get("label"))), limit=32
    )
    if name is None and champion_id is not None:
        name = f"#{champion_id}"
    if name is None:
        return None
    return {"champion_id": champion_id, "name": name}


def _extract_bench_champions(context: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    if "benchChampions" not in context and "benchChampionIds" not in context:
        return None
    records: list[dict[str, Any]] = []
    raw_bench = context.get("benchChampions")
    if raw_bench is None:
        raw_bench = []
    if "benchChampions" in context:
        if not isinstance(raw_bench, list) or len(raw_bench) > MAXIMUM_BENCH_CHAMPIONS:
            return None
        for item in raw_bench:
            record = _bench_champion_record(item)
            if record is None:
                return None
            records.append(record)
        return records
    raw_ids = context.get("benchChampionIds")
    if not isinstance(raw_ids, list) or len(raw_ids) > MAXIMUM_BENCH_CHAMPIONS:
        return None
    for item in raw_ids:
        champion_id = _safe_bounded_int(item, minimum=1, maximum=10_000)
        if champion_id is None:
            return None
        records.append({"champion_id": champion_id, "name": f"#{champion_id}"})
    return records


def _append_selected_history(
    previous: Mapping[str, Any],
    *,
    stage: int | None,
    label: str,
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    raw = previous.get("selected_history")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            item_label = _safe_text(item.get("label"))
            item_stage = _safe_stage(item.get("stage"))
            if item_label is None:
                continue
            if stage is not None and item_stage == stage:
                continue
            history.append({"stage": item_stage or len(history) + 1, "label": item_label})
    next_stage = stage if stage is not None else len(history) + 1
    history.append({"stage": next_stage, "label": label})
    history.sort(key=lambda item: item["stage"])
    return history[:MAXIMUM_SELECTED_HISTORY]


def _context_fail_closed(
    previous: Mapping[str, Any],
    *,
    event_type: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    """Fail closed only the auxiliary context lane, preserving safe advice."""

    model = copy.deepcopy(dict(previous))
    model.update(
        {
            "last_context_event": event_type,
            "context_warning": f"{code[:64]} — {message[:256]}",
        }
    )
    if event_type == "live_client_state":
        model.update(
            {
                "live_client_status": "INVALID_RESPONSE",
                "live_level": None,
                "live_is_dead": None,
                "live_respawn_timer": None,
                "live_reason": code[:64],
            }
        )
    elif event_type == "lcu_context_state":
        model.update(
            {
                "lcu_status": "INVALID_RESPONSE",
                "lcu_reason": code[:64],
                "lcu_phase": None,
                "lcu_champion": None,
                "bench_enabled": None,
                "bench_champions": [],
            }
        )
    else:
        model.update(
            {
                "mayhem_status": None,
                "mayhem_phase": None,
                "mayhem_stage": None,
                "mayhem_threshold_level": None,
            }
        )
    return _finalize(model)


def _reduce_live_client_state(
    previous: Mapping[str, Any],
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not _schema_version_is_valid(event, payload):
        return _context_fail_closed(
            previous,
            event_type="live_client_state",
            code="unsupported_schema_version",
            message="live context schema_version must be integer 1",
        )
    raw_status = _safe_text(payload.get("status"), limit=32)
    status = raw_status.upper() if raw_status is not None else None
    if status not in _LIVE_CLIENT_STATES:
        return _context_fail_closed(
            previous,
            event_type="live_client_state",
            code="invalid_live_client_status",
            message="live client status is unsupported",
        )

    model = copy.deepcopy(dict(previous))
    reason = _safe_text(payload.get("reason"), limit=128)
    model.update(
        {
            "last_context_event": "live_client_state",
            "live_client_status": status,
            "live_reason": reason,
            "context_warning": None,
        }
    )
    if status != "READY":
        model.update(
            {
                "live_level": None,
                "live_is_dead": None,
                "live_respawn_timer": None,
            }
        )
        return _finalize(model)

    player = _player_context(payload.get("player"))
    if player is None:
        return _context_fail_closed(
            previous,
            event_type="live_client_state",
            code="invalid_live_client_player",
            message="READY live context requires bounded level/death/respawn fields",
        )
    level, is_dead, respawn_timer = player
    model.update(
        {
            "live_level": level,
            "live_is_dead": is_dead,
            "live_respawn_timer": respawn_timer,
        }
    )
    return _finalize(model)


def _reduce_mayhem_selection_state(
    previous: Mapping[str, Any],
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not _schema_version_is_valid(event, payload):
        return _context_fail_closed(
            previous,
            event_type="mayhem_selection_state",
            code="unsupported_schema_version",
            message="selection context schema_version must be integer 1",
        )
    raw_status = _safe_text(payload.get("status"), limit=32)
    status = raw_status.upper() if raw_status is not None else None
    raw_phase = _safe_text(payload.get("phase"), limit=32)
    phase = raw_phase.upper() if raw_phase is not None else None
    stage = _safe_stage(payload.get("stage"))
    raw_threshold = payload.get("thresholdLevel", payload.get("threshold_level"))
    threshold = (
        None
        if raw_threshold is None
        else _safe_bounded_int(raw_threshold, minimum=1, maximum=255)
    )
    if (
        status not in _MAYHEM_STATUSES
        or phase not in _MAYHEM_PHASES
        or stage is None
        or (raw_threshold is not None and threshold is None)
    ):
        return _context_fail_closed(
            previous,
            event_type="mayhem_selection_state",
            code="invalid_mayhem_selection_state",
            message="selection context status, phase, stage, or threshold is invalid",
        )

    raw_player = payload.get("player")
    player = None if raw_player is None else _player_context(raw_player)
    if raw_player is not None and player is None:
        return _context_fail_closed(
            previous,
            event_type="mayhem_selection_state",
            code="invalid_mayhem_player",
            message="selection player context has invalid level/death/respawn fields",
        )

    model = copy.deepcopy(dict(previous))
    model.update(
        {
            "last_context_event": "mayhem_selection_state",
            "mayhem_status": status,
            "mayhem_phase": phase,
            "mayhem_stage": stage,
            "mayhem_threshold_level": threshold,
            "context_warning": None,
        }
    )
    if player is not None:
        level, is_dead, respawn_timer = player
        model.update(
            {
                "live_client_status": "READY",
                "live_level": level,
                "live_is_dead": is_dead,
                "live_respawn_timer": respawn_timer,
                "live_reason": None,
            }
        )
    return _finalize(model)


def _reduce_lcu_context_state(
    previous: Mapping[str, Any],
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not _schema_version_is_valid(event, payload):
        return _context_fail_closed(
            previous,
            event_type="lcu_context_state",
            code="unsupported_schema_version",
            message="LCU context schema_version must be integer 1",
        )
    raw_status = _safe_text(payload.get("status"), limit=32)
    status = raw_status.upper() if raw_status is not None else None
    if status not in _LCU_STATES:
        return _context_fail_closed(
            previous,
            event_type="lcu_context_state",
            code="invalid_lcu_status",
            message="LCU status is unsupported",
        )

    model = copy.deepcopy(dict(previous))
    reason = _safe_text(payload.get("reason"), limit=128)
    model.update(
        {
            "last_context_event": "lcu_context_state",
            "lcu_status": status,
            "lcu_reason": reason,
            "context_warning": None,
        }
    )
    if status in {"UNAVAILABLE", "INVALID_RESPONSE"}:
        model.update(
            {
                "lcu_phase": None,
                "lcu_champion": None,
                "bench_enabled": None,
                "bench_champions": [],
            }
        )
        return _finalize(model)

    raw_context = payload.get("context")
    if not isinstance(raw_context, Mapping):
        return _context_fail_closed(
            previous,
            event_type="lcu_context_state",
            code="invalid_lcu_context",
            message="READY/PARTIAL LCU context requires a context object",
        )
    phase = _safe_text(raw_context.get("gameflowPhase"), limit=64)
    if phase is None:
        return _context_fail_closed(
            previous,
            event_type="lcu_context_state",
            code="invalid_lcu_phase",
            message="LCU context requires gameflowPhase",
        )
    raw_champion = raw_context.get("championId")
    champion = None
    if raw_champion is not None:
        champion = _safe_bounded_int(raw_champion, minimum=1, maximum=10_000)
        if champion is None:
            return _context_fail_closed(
                previous,
                event_type="lcu_context_state",
                code="invalid_lcu_champion",
                message="LCU championId is out of range",
            )
    raw_enabled = raw_context.get("benchEnabled")
    bench_enabled = raw_enabled if type(raw_enabled) is bool else None
    bench = _extract_bench_champions(raw_context)
    if (
        "benchChampions" in raw_context or "benchChampionIds" in raw_context
    ) and bench is None:
        return _context_fail_closed(
            previous,
            event_type="lcu_context_state",
            code="invalid_lcu_bench",
            message="LCU benchChampions is malformed",
        )

    model["lcu_phase"] = phase
    model["lcu_champion"] = champion
    if phase != "ChampSelect":
        model["bench_enabled"] = False
        model["bench_champions"] = []
        return _finalize(model)

    if bench is not None:
        model["bench_enabled"] = True if bench_enabled is None else bench_enabled
        model["bench_champions"] = bench
    elif bench_enabled is not None:
        model["bench_enabled"] = bench_enabled
    return _finalize(model)


def _reduce_context_event(
    previous: Mapping[str, Any],
    event_type: str,
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if event_type == "live_client_state":
        return _reduce_live_client_state(previous, event, payload)
    if event_type == "lcu_context_state":
        return _reduce_lcu_context_state(previous, event, payload)
    return _reduce_mayhem_selection_state(previous, event, payload)


def _error(payload: Mapping[str, Any], fallback_code: str) -> dict[str, str]:
    raw_error = payload.get("error")
    if isinstance(raw_error, Mapping):
        code = _safe_text(raw_error.get("code"), limit=64) or fallback_code
        message = _safe_text(raw_error.get("message"), limit=256) or code
    else:
        code = _safe_text(payload.get("reason"), limit=64) or fallback_code
        message = _safe_text(payload.get("message"), limit=256) or code
    return {"code": code, "message": message}


def _active_capture_state(
    payload: Mapping[str, Any], fallback: str
) -> str:
    raw_state = payload.get("active_capture_state")
    if raw_state is None:
        capture = payload.get("capture")
        if isinstance(capture, Mapping):
            raw_state = capture.get("state")
    text = _safe_text(raw_state, limit=64)
    if text is None:
        return fallback
    return text.replace("-", "_").replace(" ", "_").upper()


def _finalize(model: dict[str, Any]) -> dict[str, Any]:
    model["render_text"] = render_text(model)
    return model


def _has_cached_recommendation(model: Mapping[str, Any]) -> bool:
    recommended = _safe_text(model.get("recommended"))
    choices = model.get("choices")
    return (
        model.get("status") in _SUCCESS_STATUSES
        and recommended is not None
        and isinstance(choices, list)
        and len(choices) == 3
    )


def _retain_stale_recommendation(
    model: dict[str, Any], reason: str | None
) -> bool:
    if not _has_cached_recommendation(model):
        return False
    model.update(
        {
            "hotkeys_enabled": False,
            "recommendation_freshness": "stale",
            "stale_reason": reason or "capture_freshness_unavailable",
        }
    )
    return True


def _fail_closed_model(
    previous: Mapping[str, Any] | None,
    *,
    code: str,
    message: str,
    hero: str | None = None,
    stage: int | None = None,
    source_kind: str | None = None,
) -> dict[str, Any]:
    model = copy.deepcopy(dict(previous)) if previous is not None else _base_view_model()
    model.update(
        {
            "schema_version": SCHEMA_VERSION,
            "event": "fail_closed",
            "status": "fail_closed",
            "hero": hero if hero is not None else model.get("hero"),
            "stage": stage,
            "choices": [],
            "recommended": None,
            "knowledge_conflicts": [],
            "warning": "无安全建议；已关闭旧推荐显示。",
            "error": {"code": code[:64], "message": message[:256]},
            "selected": None,
            "selected_slot": None,
            "hotkeys_enabled": False,
            "active_offer_id": None,
            "recommendation_freshness": "stale",
            "resume_hotkeys_enabled": False,
            "stale_reason": code,
            "terminal": model.get("terminal") is True,
        }
    )
    if source_kind is not None:
        model["source_kind"] = source_kind
        model["source_label"] = _SOURCE_LABELS[source_kind]
    return _finalize(model)


def reduce_event(
    previous: Mapping[str, Any] | None,
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Reduce one supported event to a normalized, renderable view model.

    Unknown events return ``None`` and leave the caller's state untouched.
    Malformed supported events fail closed and clear any old recommendation.
    """

    event_type = _event_type(event)
    if event_type is None:
        return None
    payload = _payload(event)
    known = (
        event_type in _START_TYPES
        or event_type in _RECOMMENDATION_TYPES
        or event_type == "choice_confirmation"
        or event_type in _END_TYPES
        or event_type in _HEALTH_TYPES
        or event_type in _RESTART_TYPES
        or event_type in _INVALIDATION_TYPES
        or event_type in _FALLBACK_TYPES
        or event_type in _CONTEXT_TYPES
    )
    if not known:
        return None

    prior = copy.deepcopy(dict(previous)) if previous is not None else _base_view_model()
    source_kind = _source_kind(event, payload, str(prior.get("source_kind", "unknown")))
    hero = _safe_text(payload.get("hero", event.get("hero")))
    snapshot_id = _safe_text(
        payload.get("snapshot_id", event.get("snapshot_id")), limit=128
    )
    event_stage = _safe_stage(payload.get("stage", event.get("stage")))

    if event_type in _CONTEXT_TYPES:
        if prior.get("terminal") is True:
            return _fail_closed_model(
                prior,
                code="event_after_terminal",
                message="only a new session start is accepted after session end",
                hero=hero,
                stage=event_stage,
                source_kind=source_kind,
            )
        return _reduce_context_event(prior, event_type, event, payload)

    if not _schema_version_is_valid(event, payload):
        return _fail_closed_model(
            prior,
            code="unsupported_schema_version",
            message="schema_version, when present, must be integer 1",
            hero=hero,
            stage=event_stage,
            source_kind=source_kind,
        )

    if event_type in _START_TYPES:
        model = _base_view_model()
        model.update(
            {
                "event": "recommendation_session_start",
                "status": _safe_text(payload.get("status"), limit=64) or "ready",
                "hero": hero,
                "selected_history": _selected_history_from_payload(payload),
                "source_kind": source_kind,
                "source_label": _SOURCE_LABELS[source_kind],
                "snapshot_id": snapshot_id,
                "terminal": False,
            }
        )
        return _finalize(model)

    if prior.get("terminal") is True:
        return _fail_closed_model(
            prior,
            code="event_after_terminal",
            message="only a new session start is accepted after session end",
            hero=hero,
            stage=event_stage,
            source_kind=source_kind,
        )

    if event_type in _HEALTH_TYPES:
        raw_state = _safe_text(
            payload.get("state", payload.get("status")), limit=32
        )
        state = raw_state.upper() if raw_state is not None else None
        if state not in _HEALTH_STATES:
            return _fail_closed_model(
                prior,
                code="invalid_capture_health",
                message="capture health state is unsupported",
                hero=hero,
                stage=event_stage,
                source_kind=source_kind,
            )
        active_capture_state = _active_capture_state(payload, state)
        raw_freshness = _safe_text(
            payload.get("recommendation_freshness"), limit=32
        )
        freshness = raw_freshness.lower() if raw_freshness is not None else None
        invalidated = payload.get("recommendation_invalidated")
        health_reason = _safe_text(
            payload.get("reason", payload.get("reason_code")), limit=128
        )
        fallback_transition = state == "DESKTOP_FALLBACK" and (
            prior.get("active_capture_state") != "DESKTOP_FALLBACK"
        )
        prior.update(
            {
                "event": "capture_health",
                "health_state": state,
                "active_capture_state": active_capture_state,
                "health_reason": health_reason,
                "source_kind": source_kind,
                "source_label": _SOURCE_LABELS[source_kind],
            }
        )

        # Explicit freshness fields are the authoritative recommendation
        # continuity handshake.  A DEGRADED capture can still carry a fresh,
        # processable WGC frame, so broad health labels must not override an
        # explicit current/false pair.
        if freshness == "stale" or invalidated is True:
            if not _retain_stale_recommendation(prior, health_reason):
                prior.update(
                    {
                        "choices": [],
                        "recommended": None,
                        "knowledge_conflicts": [],
                        "hotkeys_enabled": False,
                        "resume_hotkeys_enabled": False,
                        "active_offer_id": None,
                        "recommendation_freshness": "stale",
                        "stale_reason": health_reason,
                        "status": state.lower(),
                        "warning": "识别状态不可用；没有可保留的安全推荐。",
                    }
                )
            return _finalize(prior)

        if freshness == "current" and invalidated is False:
            # Capture recovery proves only that pixels are flowing again.  It
            # does not prove that the cached LEFT/CENTER/RIGHT offer is still
            # on screen.  Keep a previously invalidated recommendation stale
            # (and its hotkeys disabled) until the coordinator emits an
            # offer-scoped restored recommendation after re-observing the same
            # three cards.  A recommendation that never became stale remains
            # current across additive health heartbeats.
            if prior.get("recommendation_freshness") == "stale":
                _retain_stale_recommendation(
                    prior,
                    _safe_text(prior.get("stale_reason"), limit=128)
                    or health_reason,
                )
            return _finalize(prior)

        clear_visible = state in {
            "RESTARTING",
            "DEGRADED",
            "STOPPED",
        } or fallback_transition
        if clear_visible:
            prior.update(
                {
                    "choices": [],
                    "recommended": None,
                    "knowledge_conflicts": [],
                    "hotkeys_enabled": False,
                    "resume_hotkeys_enabled": False,
                    "active_offer_id": None,
                    "recommendation_freshness": "stale",
                    "stale_reason": health_reason,
                    "status": state.lower(),
                    "warning": "识别状态不可用；旧推荐已清除。",
                }
            )
        return _finalize(prior)

    if event_type in _RESTART_TYPES:
        prior.update(
            {
                "event": "component_restart",
                "status": "restarting",
                "health_state": "RESTARTING",
                "active_capture_state": "RESTARTING",
                "health_reason": _safe_text(
                    payload.get("reason", payload.get("reason_code")), limit=128
                ),
                "choices": [],
                "recommended": None,
                "knowledge_conflicts": [],
                "hotkeys_enabled": False,
                "resume_hotkeys_enabled": False,
                "active_offer_id": None,
                "recommendation_freshness": "stale",
                "stale_reason": _safe_text(
                    payload.get("reason", payload.get("reason_code")), limit=128
                ),
                "warning": "屏幕识别正在重启；旧推荐已清除。",
                "source_kind": source_kind,
                "source_label": _SOURCE_LABELS[source_kind],
            }
        )
        return _finalize(prior)

    if event_type in _INVALIDATION_TYPES:
        event_offer_id = _safe_text(
            payload.get("offer_id", payload.get("offer_fingerprint")),
            limit=128,
        )
        active_offer_id = _safe_text(prior.get("active_offer_id"), limit=128)
        if (
            event_offer_id is not None
            and active_offer_id is not None
            and event_offer_id != active_offer_id
        ):
            return _finalize(prior)
        stale_reason = _safe_text(
            payload.get("reason", payload.get("reason_code")), limit=128
        )
        if _retain_stale_recommendation(prior, stale_reason):
            prior.update(
                {
                    "event": "recommendation_invalidated",
                    "source_kind": source_kind,
                    "source_label": _SOURCE_LABELS[source_kind],
                }
            )
            return _finalize(prior)
        prior.update(
            {
                "event": "recommendation_invalidated",
                "status": "stale",
                "choices": [],
                "recommended": None,
                "knowledge_conflicts": [],
                "hotkeys_enabled": False,
                "resume_hotkeys_enabled": False,
                "active_offer_id": None,
                "recommendation_freshness": "stale",
                "stale_reason": stale_reason,
                "warning": "识别结果已过期；旧推荐已清除。",
                "source_kind": source_kind,
                "source_label": _SOURCE_LABELS[source_kind],
            }
        )
        return _finalize(prior)

    if event_type in _FALLBACK_TYPES:
        raw_state = _safe_text(
            payload.get("state", payload.get("status")), limit=32
        )
        mode = _safe_text(payload.get("mode"), limit=64)
        state = (
            "DEGRADED"
            if raw_state is not None and raw_state.upper() == "DEGRADED"
            else "DESKTOP_FALLBACK"
        )
        prior.update(
            {
                "event": "fallback_changed",
                "status": state.lower(),
                "health_state": state,
                "active_capture_state": state,
                "health_reason": _safe_text(
                    payload.get("reason", payload.get("reason_code")), limit=128
                ),
                "choices": [],
                "recommended": None,
                "knowledge_conflicts": [],
                "hotkeys_enabled": False,
                "resume_hotkeys_enabled": False,
                "active_offer_id": None,
                "recommendation_freshness": "stale",
                "stale_reason": _safe_text(
                    payload.get("reason", payload.get("reason_code")), limit=128
                ),
                "warning": (
                    "屏幕识别已降级；旧推荐已清除。"
                    if state == "DEGRADED"
                    else f"已切换桌面备份源{f' ({mode})' if mode else ''}；等待新结果。"
                ),
                "source_kind": source_kind,
                "source_label": _SOURCE_LABELS[source_kind],
            }
        )
        return _finalize(prior)

    if event_type in _RECOMMENDATION_TYPES:
        if event_type == "recommendation":
            status = _safe_text(payload.get("status"), limit=64)
        else:
            status = event_type
        stage = event_stage
        if status == "fail_closed":
            error = _error(payload, "upstream_fail_closed")
            return _fail_closed_model(
                prior,
                code=error["code"],
                message=error["message"],
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        if status not in _SUCCESS_STATUSES:
            return _fail_closed_model(
                prior,
                code="invalid_recommendation_status",
                message="recommendation event has an unsupported status",
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        recommended = _safe_text(payload.get("recommended"))
        if recommended is None or stage is None:
            return _fail_closed_model(
                prior,
                code="invalid_recommendation_event",
                message="safe recommendation requires a stage and recommended augment",
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        choices = _extract_choices(payload, recommended)
        labels = [item["label"] for item in choices]
        slots = [item["slot"] for item in choices]
        if (
            len(choices) != 3
            or len(set(labels)) != 3
            or set(slots) != {1, 2, 3}
            or recommended not in labels
        ):
            return _fail_closed_model(
                prior,
                code="invalid_recommendation_choices",
                message="safe recommendation requires three unique choices and slots",
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        conflicts = _extract_conflicts(payload)
        if conflicts is None or (
            status == "recommended_with_knowledge_conflict" and not conflicts
        ):
            return _fail_closed_model(
                prior,
                code="invalid_knowledge_conflicts",
                message="knowledge_conflicts must be a valid mapped conflict array",
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        if conflicts and status == "recommended":
            status = "recommended_with_knowledge_conflict"
        conflict_names = {item["augment"] for item in conflicts}
        if not conflict_names.issubset(set(labels)):
            return _fail_closed_model(
                prior,
                code="invalid_knowledge_conflicts",
                message="every knowledge conflict must map to a current choice",
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        if len(conflict_names) == len(labels):
            return _fail_closed_model(
                prior,
                code="all_choices_knowledge_conflict",
                message="no stage-valid recommendation remains",
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        if recommended in conflict_names:
            return _fail_closed_model(
                prior,
                code="recommended_knowledge_conflict",
                message="recommended augment must not be knowledge-conflicted",
                hero=hero,
                stage=stage,
                source_kind=source_kind,
            )
        for choice in choices:
            choice["recommended"] = choice["label"] == recommended
            choice["knowledge_conflict"] = choice["label"] in conflict_names
        warning = None
        if status == "recommended_with_knowledge_conflict":
            warning = (
                "知识冲突：屏幕已观察到候选，但与 snapshot available_stages "
                "冲突；请人工复核。"
            )
        recommendation_hotkeys_enabled = payload.get("hotkeys_enabled") is not False
        prior.update(
            {
                "event": status,
                "status": status,
                "hero": hero if hero is not None else prior.get("hero"),
                "stage": stage,
                "choices": sorted(choices, key=lambda item: item["slot"]),
                "recommended": recommended,
                "knowledge_conflicts": conflicts,
                "warning": warning,
                "error": None,
                "selected": None,
                "selected_slot": None,
                "source_kind": source_kind,
                "source_label": _SOURCE_LABELS[source_kind],
                "snapshot_id": snapshot_id or prior.get("snapshot_id"),
                "hotkeys_enabled": recommendation_hotkeys_enabled,
                "resume_hotkeys_enabled": recommendation_hotkeys_enabled,
                "active_offer_id": _safe_text(
                    payload.get("offer_id", payload.get("offer_fingerprint")),
                    limit=128,
                ),
                "recommendation_freshness": "current",
                "stale_reason": None,
                "terminal": False,
            }
        )
        return _finalize(prior)

    if event_type == "choice_confirmation":
        status = _safe_text(payload.get("status"), limit=64)
        if status != "choice_confirmed":
            return _fail_closed_model(
                prior,
                code="invalid_choice_confirmation_status",
                message="choice confirmation status must be choice_confirmed",
                hero=hero,
                stage=event_stage,
                source_kind=source_kind,
            )
        selected = _safe_text(payload.get("selected"))
        selected_slot = payload.get("selected_slot")
        if isinstance(selected_slot, str):
            selected_slot = {"LEFT": 1, "CENTER": 2, "RIGHT": 3}.get(
                selected_slot.strip().upper()
            )
        if type(selected_slot) is not int or selected_slot not in {1, 2, 3}:
            selected_slot = None
        prior_stage = _safe_stage(prior.get("stage"))
        event_offer_id = _safe_text(
            payload.get("offer_id", payload.get("offer_fingerprint")),
            limit=128,
        )
        active_offer_id = _safe_text(prior.get("active_offer_id"), limit=128)
        active_choices = prior.get("choices")
        selected_choice = None
        if isinstance(active_choices, list):
            for choice in active_choices:
                if isinstance(choice, Mapping) and choice.get("slot") == selected_slot:
                    selected_choice = choice
                    break
        active_offer_valid = (
            prior.get("status") in _SUCCESS_STATUSES
            and prior_stage is not None
            and event_stage == prior_stage
            and active_offer_id is not None
            and event_offer_id == active_offer_id
            and isinstance(active_choices, list)
            and len(active_choices) == 3
        )
        if not active_offer_valid:
            return _fail_closed_model(
                prior,
                code="invalid_choice_confirmation",
                message="choice confirmation does not match an active offer",
                hero=hero,
                stage=event_stage,
                source_kind=source_kind,
            )
        if (
            selected is None
            or selected_slot is None
            or not isinstance(selected_choice, Mapping)
            or _safe_text(selected_choice.get("label")) != selected
        ):
            return _fail_closed_model(
                prior,
                code="invalid_choice_confirmation",
                message="confirmed stage, slot, and selected augment must match",
                hero=hero,
                stage=event_stage,
                source_kind=source_kind,
            )
        prior.update(
            {
                "event": "choice_confirmation",
                "status": "choice_confirmed",
                "hero": hero if hero is not None else prior.get("hero"),
                "stage": event_stage,
                "choices": [],
                "recommended": None,
                "knowledge_conflicts": [],
                "warning": "玩家已确认；当前推荐已清除。",
                "error": None,
                "selected": selected,
                "selected_slot": selected_slot,
                "selected_history": _append_selected_history(
                    prior, stage=event_stage, label=selected
                ),
                "source_kind": source_kind,
                "source_label": _SOURCE_LABELS[source_kind],
                "snapshot_id": snapshot_id or prior.get("snapshot_id"),
                "hotkeys_enabled": False,
                "resume_hotkeys_enabled": False,
                "active_offer_id": None,
                "recommendation_freshness": "none",
                "stale_reason": None,
                "terminal": False,
            }
        )
        return _finalize(prior)

    status = _safe_text(payload.get("status"), limit=64) or "ended"
    error = _error(payload, "session_ended_fail_closed") if status == "fail_closed" else None
    prior.update(
        {
            "event": "session_end",
            "status": status,
            "hero": hero if hero is not None else prior.get("hero"),
            "choices": [],
            "recommended": None,
            "knowledge_conflicts": [],
            "warning": (
                "会话已安全结束；没有保留旧推荐。"
                if status != "fail_closed"
                else "会话异常结束；没有安全建议。"
            ),
            "error": error,
            "source_kind": source_kind,
            "source_label": _SOURCE_LABELS[source_kind],
            "snapshot_id": snapshot_id or prior.get("snapshot_id"),
            "hotkeys_enabled": False,
            "resume_hotkeys_enabled": False,
            "active_offer_id": None,
            "recommendation_freshness": "none",
            "stale_reason": None,
            "terminal": True,
        }
    )
    return _finalize(prior)


def _lcu_context_text(model: Mapping[str, Any]) -> str:
    status = _safe_text(model.get("lcu_status"), limit=32) or "UNKNOWN"
    phase = _safe_text(model.get("lcu_phase"), limit=64)
    if status == "UNKNOWN":
        return "等待客户端"
    if phase is not None:
        return f"{status} · {phase}"
    reason = _safe_text(model.get("lcu_reason"), limit=128)
    return f"{status}{f' ({reason})' if reason else ''}"


def _bench_text(model: Mapping[str, Any]) -> str:
    status = _safe_text(model.get("lcu_status"), limit=32) or "UNKNOWN"
    phase = _safe_text(model.get("lcu_phase"), limit=64)
    bench = model.get("bench_champions")
    if isinstance(bench, list) and bench:
        names = [
            _safe_text(item.get("name"), limit=32) or "—"
            for item in bench
            if isinstance(item, Mapping)
        ]
        return "、".join(name for name in names if name)
    if status in {"UNAVAILABLE", "INVALID_RESPONSE"}:
        return "LCU 不可用（小头像无文字，OCR 不可靠）"
    if phase == "ChampSelect":
        if model.get("bench_enabled") is False:
            return "当前模式无共享待选席"
        return "选人中，待选席为空"
    if status == "UNKNOWN":
        return "等待 LCU"
    return "非选人阶段"


def _selected_history_text(model: Mapping[str, Any]) -> list[str]:
    history = model.get("selected_history")
    if not isinstance(history, list) or not history:
        return ["已选海克斯: —"]
    lines = ["已选海克斯:"]
    for item in history:
        if not isinstance(item, Mapping):
            continue
        label = _safe_text(item.get("label")) or "—"
        stage = _safe_stage(item.get("stage"))
        prefix = f"第 {stage} 轮 " if stage is not None else ""
        lines.append(f"  - {prefix}{label}")
    return lines


def _live_context_text(model: Mapping[str, Any]) -> str:
    status = _safe_text(model.get("live_client_status"), limit=32) or "UNKNOWN"
    level = _safe_bounded_int(model.get("live_level"), minimum=1, maximum=255)
    is_dead = model.get("live_is_dead")
    respawn = _safe_nonnegative_number(
        model.get("live_respawn_timer"), maximum=3_600.0
    )
    if level is not None and type(is_dead) is bool and respawn is not None:
        if is_dead:
            seconds = str(int(respawn)) if respawn.is_integer() else f"{respawn:.1f}"
            return f"Lv.{level} · 阵亡 · {seconds} 秒复活"
        return f"Lv.{level} · 存活"
    if status == "UNKNOWN":
        return "等待对局状态"
    reason = _safe_text(model.get("live_reason"), limit=128)
    return f"{status}{f' ({reason})' if reason else ''}"


def _selection_context_text(model: Mapping[str, Any]) -> str:
    status = _safe_text(model.get("mayhem_status"), limit=32)
    stage = _safe_stage(model.get("mayhem_stage"))
    threshold = _safe_bounded_int(
        model.get("mayhem_threshold_level"), minimum=1, maximum=255
    )
    prefix = f"第 {stage} 轮 · " if stage is not None else ""
    if status == "ARMED":
        threshold_text = f"（Lv.{threshold}）" if threshold is not None else ""
        return f"{prefix}已到阈值，等待首次死亡{threshold_text}"
    if status == "DEATH_TRIGGERED":
        return f"{prefix}选牌触发，识别中"
    if status == "QUEUED_OFFER_TRIGGERED":
        return f"{prefix}连续选牌已排队，保持识别中"
    if status == "OFFER_DETECTED":
        recommended = _safe_text(model.get("recommended"))
        recommendation_status = _safe_text(model.get("status"), limit=64)
        if recommended is not None and recommendation_status in _SUCCESS_STATUSES:
            state = f"已就绪（{recommended}）"
        elif recommendation_status == "fail_closed":
            state = "无安全建议"
        else:
            state = "等待安全推荐"
        return f"{prefix}选牌已识别，推荐状态：{state}"
    if status == "WINDOW_EXPIRED":
        return f"{prefix}识别窗口已结束，继续等待"
    return "等待触发状态"


def render_text(model: Mapping[str, Any]) -> str:
    """Render a normalized view model as simple, readable Unicode text."""

    source_label = _safe_text(model.get("source_label"), limit=128) or _SOURCE_LABELS[
        "unknown"
    ]
    hero = _safe_text(model.get("hero")) or "—"
    stage = _safe_stage(model.get("stage"))
    stage_text = f"{stage}/4" if stage is not None else "等待识别"
    health_state = _safe_text(model.get("health_state"), limit=32) or "UNKNOWN"
    health_reason = _safe_text(model.get("health_reason"), limit=128)
    health_text = f"识别健康: {health_state}"
    if health_reason is not None:
        health_text += f" ({health_reason})"
    active_capture_state = (
        _safe_text(model.get("active_capture_state"), limit=64) or "UNKNOWN"
    )
    raw_freshness = (
        _safe_text(model.get("recommendation_freshness"), limit=32) or "none"
    ).lower()
    freshness = raw_freshness if raw_freshness in {"current", "stale", "none"} else "none"
    if freshness == "current":
        freshness_text = "推荐时效: CURRENT"
    elif freshness == "stale":
        freshness_text = "推荐时效: STALE（仅供参考，快捷键已禁用）"
        stale_reason = _safe_text(model.get("stale_reason"), limit=128)
        if stale_reason is not None:
            freshness_text += f" ({stale_reason})"
    else:
        freshness_text = "推荐时效: NONE"
    lines = [
        source_label,
        health_text,
        f"当前捕获状态: {active_capture_state}",
        freshness_text,
        f"英雄: {hero}    阶段: {stage_text}",
        f"LCU: {_lcu_context_text(model)}",
        f"待选席: {_bench_text(model)}",
        f"实时: {_live_context_text(model)}",
        f"选牌: {_selection_context_text(model)}",
        "",
        *_selected_history_text(model),
        "",
    ]
    context_warning = _safe_text(model.get("context_warning"), limit=320)
    if context_warning is not None:
        lines.extend([f"上下文警示: {context_warning}", ""])
    choices = model.get("choices")
    if isinstance(choices, list) and choices:
        lines.append("当前海克斯:")
        for item in choices:
            if not isinstance(item, Mapping):
                continue
            slot = item.get("slot")
            label = _safe_text(item.get("label")) or "—"
            markers = []
            if item.get("recommended") is True:
                markers.append("推荐")
            if item.get("knowledge_conflict") is True:
                markers.append("知识冲突")
            suffix = f"  [{' / '.join(markers)}]" if markers else ""
            lines.append(f"  {slot}. {label}{suffix}")
    else:
        lines.append("当前海克斯: —")
    recommended = _safe_text(model.get("recommended"))
    lines.extend(["", f"推荐: {recommended or '无安全建议'}"])
    warning = _safe_text(model.get("warning"), limit=256)
    if warning is not None:
        lines.append(f"警示: {warning}")
    conflicts = model.get("knowledge_conflicts")
    if isinstance(conflicts, list):
        for conflict in conflicts:
            if not isinstance(conflict, Mapping):
                continue
            augment = _safe_text(conflict.get("augment")) or "未知候选"
            observed = _safe_stage(conflict.get("observed_stage"))
            available = conflict.get("snapshot_available_stages")
            observed_text = str(observed) if observed is not None else "?"
            available_text = (
                ",".join(str(item) for item in available)
                if isinstance(available, list) and available
                else "?"
            )
            lines.append(
                f"  - {augment}: 屏幕轮次 {observed_text}; snapshot 轮次 {available_text}"
            )
    error = model.get("error")
    if isinstance(error, Mapping):
        code = _safe_text(error.get("code"), limit=64) or "unknown_error"
        message = _safe_text(error.get("message"), limit=256) or code
        lines.append(f"错误: {code} — {message}")
    selected = _safe_text(model.get("selected"))
    if selected is not None:
        lines.append(f"玩家已确认: {selected}")
    hotkeys_text = (
        "Ctrl+Alt+1 / 2 / 3"
        if model.get("hotkeys_enabled") is True
        else "已禁用"
    )
    lines.extend(["", f"人工确认快捷键: {hotkeys_text}"])
    snapshot = _safe_text(model.get("snapshot_id"), limit=128)
    status = _safe_text(model.get("status"), limit=64) or "waiting"
    footer = f"状态: {status}"
    if snapshot is not None:
        footer += f"    snapshot: {snapshot}"
    lines.append(footer)
    return "\n".join(lines)


def _reject_constant(value: str) -> None:
    raise JsonLineError(f"non-finite JSON number: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JsonLineError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_jsonl_line(line: str) -> dict[str, Any]:
    """Decode one strict JSON object line or raise ``JsonLineError``."""

    if line.startswith("\ufeff"):
        raise JsonLineError("UTF-8 BOM is not allowed")
    if not line.strip():
        raise JsonLineError("blank JSONL line is not allowed")
    if len(line.encode("utf-8", errors="strict")) > MAX_JSONL_BYTES:
        raise JsonLineError("JSONL line exceeds 65536 bytes")
    try:
        decoded = json.loads(
            line,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, JsonLineError, UnicodeError, ValueError) as error:
        raise JsonLineError(str(error)) from error
    if not isinstance(decoded, dict):
        raise JsonLineError("JSONL value must be an object")
    return decoded


def reduce_jsonl_line(
    previous: Mapping[str, Any] | None,
    line: str,
) -> dict[str, Any] | None:
    try:
        event = decode_jsonl_line(line)
    except JsonLineError as error:
        return _fail_closed_model(
            previous,
            code="invalid_json",
            message=str(error),
        )
    return reduce_event(previous, event)


def process_jsonl(input_stream: TextIO, output_stream: TextIO) -> int:
    """Normalize stdin JSONL to stdout JSONL without creating a window."""

    state: dict[str, Any] | None = None
    emitted = 0
    while True:
        try:
            line = input_stream.readline()
        except UnicodeError as error:
            state = _fail_closed_model(
                state,
                code="invalid_utf8",
                message=str(error),
            )
            output_stream.write(json.dumps(state, ensure_ascii=False) + "\n")
            emitted += 1
            break
        if line == "":
            break
        next_state = reduce_jsonl_line(state, line)
        if next_state is None:
            continue
        state = next_state
        output_stream.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        output_stream.flush()
        emitted += 1
    return emitted


class Win32SidecarHost:
    """Minimal opaque Win32 host; protocol work remains in pure functions."""

    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    WS_OVERLAPPED = 0x00000000
    WS_CAPTION = 0x00C00000
    WS_SYSMENU = 0x00080000
    WS_THICKFRAME = 0x00040000
    WS_MINIMIZEBOX = 0x00020000
    SW_SHOWNOACTIVATE = 4
    WM_DESTROY = 0x0002
    WM_PAINT = 0x000F
    WM_CLOSE = 0x0010
    WM_ERASEBKGND = 0x0014
    WM_MOUSEACTIVATE = 0x0021
    WM_NCHITTEST = 0x0084
    WM_APP_MODEL = 0x8001
    MA_NOACTIVATE = 3
    HTTRANSPARENT = -1
    HWND_TOPMOST = -1
    SWP_NOACTIVATE = 0x0010
    LWA_ALPHA = 0x00000002
    SPI_GETWORKAREA = 0x0030
    DT_LEFT = 0x0000
    DT_TOP = 0x0000
    DT_WORDBREAK = 0x0010
    DT_NOPREFIX = 0x0800
    TRANSPARENT = 1
    DEFAULT_GUI_FONT = 17

    def __init__(
        self,
        *,
        title: str,
        x: int | None,
        y: int | None,
        width: int,
        height: int,
        input_stream: TextIO,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("window mode requires Windows; use --dry-run-jsonl")
        from ctypes import wintypes

        self.wintypes = wintypes
        self.title = title
        self.requested_x = x
        self.requested_y = y
        self.width = width
        self.height = height
        self.input_stream = input_stream
        self.model = _base_view_model()
        self.model_lock = threading.Lock()
        self.hwnd: Any = None
        self._reader: threading.Thread | None = None
        self._configure_types()

    def _configure_types(self) -> None:
        wintypes = self.wintypes
        self.LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(
            self.LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", self.WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", wintypes.BYTE * 32),
            ]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("lPrivate", wintypes.DWORD),
            ]

        self.WNDCLASSW = WNDCLASSW
        self.PAINTSTRUCT = PAINTSTRUCT
        self.MSG = MSG
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = self.LRESULT
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND,
            wintypes.COLORREF,
            wintypes.BYTE,
            wintypes.DWORD,
        ]
        self.user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        self.user32.UpdateWindow.argtypes = [wintypes.HWND]
        self.user32.UpdateWindow.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.InvalidateRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
            wintypes.BOOL,
        ]
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        self.user32.BeginPaint.restype = wintypes.HDC
        self.user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
        self.user32.DrawTextW.argtypes = [
            wintypes.HDC,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.RECT),
            wintypes.UINT,
        ]
        self.user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            wintypes.LPVOID,
            wintypes.UINT,
        ]
        self.gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self.gdi32.GetStockObject.argtypes = [ctypes.c_int]
        self.gdi32.GetStockObject.restype = wintypes.HGDIOBJ
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]

    @staticmethod
    def _rgb(red: int, green: int, blue: int) -> int:
        return red | (green << 8) | (blue << 16)

    def _window_layout(self) -> tuple[int, int, int, int]:
        rect = self.wintypes.RECT()
        ok = self.user32.SystemParametersInfoW(
            self.SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        work_area = (
            (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
            if ok
            else DEFAULT_WORK_AREA
        )
        return compute_sidecar_layout(
            work_area,
            requested_x=self.requested_x,
            requested_y=self.requested_y,
            width=self.width,
            height=self.height,
        )

    def _set_topmost_without_activation(
        self, x: int, y: int, width: int, height: int
    ) -> None:
        topmost = self.wintypes.HWND(self.HWND_TOPMOST)
        if not self.user32.SetWindowPos(
            self.hwnd,
            topmost,
            x,
            y,
            width,
            height,
            self.SWP_NOACTIVATE,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def _paint(self, hwnd: Any) -> None:
        paint = self.PAINTSTRUCT()
        hdc = self.user32.BeginPaint(hwnd, ctypes.byref(paint))
        if not hdc:
            return
        try:
            rect = self.wintypes.RECT()
            self.user32.GetClientRect(hwnd, ctypes.byref(rect))
            background = self.gdi32.CreateSolidBrush(self._rgb(20, 24, 32))
            if background:
                self.user32.FillRect(hdc, ctypes.byref(rect), background)
                self.gdi32.DeleteObject(background)
            self.gdi32.SetBkMode(hdc, self.TRANSPARENT)
            self.gdi32.SetTextColor(hdc, self._rgb(236, 240, 244))
            font = self.gdi32.GetStockObject(self.DEFAULT_GUI_FONT)
            if font:
                self.gdi32.SelectObject(hdc, font)
            with self.model_lock:
                text = str(self.model.get("render_text", ""))
            text_rect = self.wintypes.RECT(16, 14, max(16, rect.right - 16), max(14, rect.bottom - 14))
            self.user32.DrawTextW(
                hdc,
                text,
                -1,
                ctypes.byref(text_rect),
                self.DT_LEFT | self.DT_TOP | self.DT_WORDBREAK | self.DT_NOPREFIX,
            )
        finally:
            self.user32.EndPaint(hwnd, ctypes.byref(paint))

    def _window_proc(self, hwnd: Any, message: int, wparam: int, lparam: int) -> int:
        if message == self.WM_NCHITTEST:
            return self.HTTRANSPARENT
        if message == self.WM_MOUSEACTIVATE:
            return self.MA_NOACTIVATE
        if message == self.WM_APP_MODEL:
            self.user32.InvalidateRect(hwnd, None, True)
            return 0
        if message == self.WM_ERASEBKGND:
            return 1
        if message == self.WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == self.WM_CLOSE:
            self.user32.DestroyWindow(hwnd)
            return 0
        if message == self.WM_DESTROY:
            self.user32.PostQuitMessage(0)
            return 0
        return int(self.user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _read_stdin(self) -> None:
        while True:
            try:
                line = self.input_stream.readline()
            except UnicodeError as error:
                with self.model_lock:
                    self.model = _fail_closed_model(
                        self.model,
                        code="invalid_utf8",
                        message=str(error),
                    )
                self.user32.PostMessageW(self.hwnd, self.WM_APP_MODEL, 0, 0)
                break
            if line == "":
                break
            with self.model_lock:
                next_model = reduce_jsonl_line(self.model, line)
                if next_model is None:
                    continue
                self.model = next_model
            self.user32.PostMessageW(self.hwnd, self.WM_APP_MODEL, 0, 0)
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)

    def run(self) -> int:
        hinstance = self.kernel32.GetModuleHandleW(None)
        class_name = f"LoLAugmentSidecar_{os.getpid()}"
        self._wndproc_callback = self.WNDPROC(self._window_proc)
        window_class = self.WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc_callback
        window_class.hInstance = hinstance
        window_class.lpszClassName = class_name
        window_class.hbrBackground = 0
        if not self.user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        x, y, width, height = self._window_layout()
        style = (
            self.WS_OVERLAPPED
            | self.WS_CAPTION
            | self.WS_SYSMENU
            | self.WS_THICKFRAME
            | self.WS_MINIMIZEBOX
        )
        ex_style = (
            self.WS_EX_NOACTIVATE
            | self.WS_EX_TOOLWINDOW
            | self.WS_EX_TRANSPARENT
            | self.WS_EX_LAYERED
        )
        self.hwnd = self.user32.CreateWindowExW(
            ex_style,
            class_name,
            self.title,
            style,
            x,
            y,
            width,
            height,
            None,
            None,
            hinstance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        # Microsoft documents WS_EX_LAYERED + WS_EX_TRANSPARENT as the
        # top-level, cross-process hit-testing contract. Alpha 255 keeps the
        # independent result window visually opaque while pointer input passes
        # to the game underneath.
        if not self.user32.SetLayeredWindowAttributes(
            self.hwnd, 0, 255, self.LWA_ALPHA
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        self._set_topmost_without_activation(x, y, width, height)
        self.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
        self.user32.UpdateWindow(self.hwnd)
        self._reader = threading.Thread(
            target=self._read_stdin,
            name="sidecar-jsonl-reader",
            daemon=True,
        )
        self._reader.start()
        message = self.MSG()
        while True:
            result = int(self.user32.GetMessageW(ctypes.byref(message), None, 0, 0))
            if result == 0:
                return 0
            if result < 0:
                return 2
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))


def _dimension(value: str) -> int:
    parsed = int(value)
    if not 100 <= parsed <= 2_000:
        raise argparse.ArgumentTypeError("dimension must be between 100 and 2000")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run-jsonl",
        action="store_true",
        help="normalize stdin JSONL to stdout without creating a window",
    )
    parser.add_argument("--x", type=int, help="window left coordinate")
    parser.add_argument("--y", type=int, help="window top coordinate")
    parser.add_argument("--width", type=_dimension, default=460)
    parser.add_argument("--height", type=_dimension, default=480)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    return parser


def _strict_utf8_standard_streams() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _strict_utf8_standard_streams()
    if args.dry_run_jsonl:
        process_jsonl(sys.stdin, sys.stdout)
        return 0
    try:
        host = Win32SidecarHost(
            title=args.title,
            x=args.x,
            y=args.y,
            width=args.width,
            height=args.height,
            input_stream=sys.stdin,
        )
        return host.run()
    except Exception as error:
        sys.stderr.write(f"sidecar unavailable: {error}\n")
        sys.stderr.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
