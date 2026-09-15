"""Run the real vision pipeline and a long-lived recommendation worker.

This module is an orchestration layer only.  It does not capture pixels, run
OCR, modify the recommendation algorithm, or send input to League of Legends.
The existing C++ program emits accepted ``GameState`` JSON; this bridge parses
that stream, asks one resident Python worker for a ranking, and maintains the
four-round session state.

After selecting a card in the game, an exact high-confidence screen observation
can confirm the chosen slot.  Ctrl+Alt+1/2/3 (left/center/right) remains the
manual fallback.  The hotkeys are polled without registration or consumption,
so neither child process needs foreground focus.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, TextIO


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VISION_EXE = (
    WORKSPACE_ROOT / "outputs" / "tmp" / "build" / "bin" /
    "lol_augment_assistant.exe"
)
DEFAULT_SCRAPE_ROOT = WORKSPACE_ROOT / "Scrape"
DEFAULT_VISUAL_CATALOG = (
    WORKSPACE_ROOT / "data" / "knowledge" / "augments.zh-CN.json"
)
DEFAULT_RUNTIME_ROOT = WORKSPACE_ROOT / "outputs" / "runtime"
DEFAULT_KNOWLEDGE = WORKSPACE_ROOT / "data" / "knowledge" / "augments.zh-CN.json"
DEFAULT_SIDECAR_SCRIPT = WORKSPACE_ROOT / "scripts" / "phase4" / "sidecar_window.py"
DEFAULT_WINDOW_TITLE = "League of Legends (TM) Client"
SLOTS = ("LEFT", "CENTER", "RIGHT")
PROTOCOL_VERSION = 1
SIDECAR_QUEUE_CAPACITY = 32
SIDECAR_CLOSE_TIMEOUT_SECONDS = 0.25
VISION_RESTART_WINDOW_SECONDS = 60.0
VISION_RESTART_BACKOFF_SECONDS = (0.0, 0.250, 1.000)
VISION_TERMINATE_GRACE_SECONDS = 0.250
# Desktop Duplication can legitimately spend about one second waiting for the
# next frame (for example while the game is loading or presenting a mostly
# static screen).  Keep the watchdog comfortably above that capture cadence so
# a healthy source is not restarted on the timeout boundary.
VISION_HEARTBEAT_SUSPECT_SECONDS = 3.000
VISION_HEARTBEAT_RESTART_SECONDS = 8.000
CAPTURE_HEALTH_INVALIDATING_STATES = {
    "RESTARTING",
    "SUSPECT",
    "STOPPED",
}
SELECTION_OBSERVATION_MIN_CONFIDENCE = 0.95
HUD_SELECTION_MIN_TOP1_SCORE = 0.84
HUD_SELECTION_MIN_TOP1_MARGIN = 0.08
HUD_SELECTION_MIN_STABLE_FRAMES = 2


class BridgeError(ValueError):
    """An input or state transition that must fail closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OfferCard:
    slot: str
    augment_id: str
    display_name: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "augment_id": self.augment_id,
            "display_name": self.display_name,
        }


@dataclass
class PendingOffer:
    stage: int
    cards: tuple[OfferCard, OfferCard, OfferCard]
    fingerprint: str
    resolved_names: dict[str, str] = field(default_factory=dict)
    decision_committed: bool = False
    recommendation_event: dict[str, Any] | None = None
    freshness: str = "current"
    stale_reason: str | None = None
    duplicate_observation_reported: bool = False
    manual_confirmation_allowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "fingerprint": self.fingerprint,
            "cards": [card.as_dict() for card in self.cards],
        }


@dataclass(frozen=True)
class ConfirmedSelection:
    stage: int
    offer_id: str
    selected_slot: str
    selected_augment_id: str
    selected: str
    confirmation_basis: str
    confidence: float | None = None
    observation_source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "offer_id": self.offer_id,
            "offer_fingerprint": self.offer_id,
            "selected_slot": self.selected_slot,
            "selected_augment_id": self.selected_augment_id,
            "selected": self.selected,
            "confirmation_basis": self.confirmation_basis,
            "confidence": self.confidence,
            "observation_source": self.observation_source,
        }


@dataclass
class SessionState:
    hero: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    owned_augments: list[str] = field(default_factory=list)
    owned_names: list[str] = field(default_factory=list)
    current_offer: PendingOffer | None = None
    seen_offers: set[str] = field(default_factory=set)
    quarantined_offer: PendingOffer | None = None
    reconcile_required: bool = False
    vision_suspended: bool = False
    vision_suspend_reason: str | None = None
    state_revision: int = 0
    confirmed_selections: dict[str, ConfirmedSelection] = field(default_factory=dict)

    @property
    def next_stage(self) -> int:
        return len(self.owned_augments) + 1


class Worker(Protocol):
    @property
    def ready(self) -> Mapping[str, Any]: ...

    def recommend(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _fingerprint(stage: int, augment_ids: Sequence[str]) -> str:
    # Slot order is part of the identity.  A stale LEFT/CENTER/RIGHT binding
    # must never survive a re-ordered observation of the same three augments.
    canonical = f"{stage}\n" + "\n".join(augment_ids)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bind_selection_observation_offer_identity(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the bridge offer identity from C++'s ordered visual evidence.

    C++ intentionally does not duplicate Python's SHA-256 implementation.  It
    emits the recognized stage plus LEFT/CENTER/RIGHT IDs; this boundary binds
    those fields to the exact fingerprint already used by PendingOffer.  A
    malformed pair or a supplied identity that disagrees with the derived one
    is kept fail-closed for ``handle_selection_observed``.
    """

    observation = dict(payload)
    has_stage = "offer_stage" in observation
    has_ids = "offer_augment_ids" in observation
    if not has_stage and not has_ids:
        return observation

    stage = observation.get("offer_stage")
    augment_ids = observation.get("offer_augment_ids")
    valid_stage = (
        not isinstance(stage, bool)
        and isinstance(stage, int)
        and stage in {1, 2, 3, 4}
    )
    valid_ids = (
        isinstance(augment_ids, list)
        and len(augment_ids) == len(SLOTS)
        and all(
            isinstance(augment_id, str)
            and bool(augment_id.strip())
            and augment_id.strip().casefold() != "unknown"
            for augment_id in augment_ids
        )
    )
    if not valid_stage or not valid_ids:
        observation["fingerprint"] = "UNKNOWN"
        return observation

    derived = _fingerprint(stage, augment_ids)
    supplied = [
        observation[key]
        for key in ("offer_id", "fingerprint", "offer_fingerprint")
        if isinstance(observation.get(key), str)
        and bool(observation[key].strip())
    ]
    if supplied and any(identity != derived for identity in supplied):
        # Preserve both values so the coordinator's existing identity conflict
        # gate rejects the observation and retains the evidence.
        observation["offer_id"] = supplied[0]
        observation["fingerprint"] = derived
    else:
        observation.setdefault("offer_id", derived)
    return observation


def _parse_offer(payload: Mapping[str, Any], expected_hero: str) -> PendingOffer:
    champion = payload.get("champion")
    if champion != expected_hero:
        raise BridgeError(
            "hero_mismatch",
            f"vision champion {champion!r} does not match {expected_hero!r}",
        )
    stage = payload.get("offer_round")
    if isinstance(stage, bool) or not isinstance(stage, int) or stage not in {
        1,
        2,
        3,
        4,
    }:
        raise BridgeError("invalid_stage", "offer_round must be 1, 2, 3, or 4")

    offer = payload.get("current_offer")
    if not isinstance(offer, dict):
        raise BridgeError("invalid_offer", "current_offer must be an object")
    recognitions = offer.get("recognitions")
    if not isinstance(recognitions, list) or len(recognitions) != 3:
        raise BridgeError(
            "unknown_ocr", "current_offer must contain exactly three recognitions"
        )

    by_slot: dict[str, OfferCard] = {}
    for recognition in recognitions:
        if not isinstance(recognition, dict):
            raise BridgeError("unknown_ocr", "recognition must be an object")
        if recognition.get("state") != "RECOGNIZED":
            raise BridgeError("unknown_ocr", "all three cards must be RECOGNIZED")
        slot = recognition.get("slot")
        augment_id = recognition.get("augment_id")
        display_name = recognition.get("display_name")
        if slot not in SLOTS or slot in by_slot:
            raise BridgeError("unknown_ocr", "card slots must be unique LEFT/CENTER/RIGHT")
        if (
            not isinstance(augment_id, str)
            or not augment_id.strip()
            or augment_id.strip().casefold() == "unknown"
        ):
            raise BridgeError("unknown_ocr", f"{slot} has no recognized augment id")
        if display_name is not None and (
            not isinstance(display_name, str) or not display_name.strip()
        ):
            raise BridgeError("unknown_ocr", f"{slot} has an invalid display name")
        by_slot[slot] = OfferCard(
            slot=slot,
            augment_id=augment_id.strip(),
            display_name=display_name.strip() if isinstance(display_name, str) else None,
        )

    cards = tuple(by_slot[slot] for slot in SLOTS)
    augment_ids = [card.augment_id for card in cards]
    if len(set(augment_ids)) != 3:
        raise BridgeError("unknown_ocr", "recognized augment ids must be distinct")
    return PendingOffer(
        stage=stage,
        cards=cards,  # type: ignore[arg-type]
        fingerprint=_fingerprint(stage, augment_ids),
    )


def _capture_health_invalidation(
    payload: Mapping[str, Any],
) -> tuple[bool, str]:
    """Return the coordinator invalidation decision for one C++ health event."""

    raw_state = payload.get("state", payload.get("status"))
    state = raw_state.strip().upper() if isinstance(raw_state, str) else "UNKNOWN"
    raw_freshness = payload.get("recommendation_freshness")
    freshness = (
        raw_freshness.strip().lower()
        if isinstance(raw_freshness, str)
        else None
    )
    raw_explicit = payload.get("recommendation_invalidated")
    explicit = raw_explicit if isinstance(raw_explicit, bool) else None

    # The C++ producer owns the source-switch/freshness decision.  A selected
    # WGC source can legitimately be labelled DEGRADED while still carrying a
    # fresh, processable frame (desktop_unavailable_wgc_fresh).  Do not
    # override that explicit current/false contract merely because the broad
    # health label is not HEALTHY.  Missing or contradictory contracts remain
    # fail-closed, as do terminal/suspect states.
    if freshness == "stale" or explicit is True:
        invalidating = True
    elif (
        freshness == "current"
        and explicit is False
        and state not in CAPTURE_HEALTH_INVALIDATING_STATES
        and state != "UNKNOWN"
    ):
        invalidating = False
    else:
        invalidating = state != "HEALTHY"
    raw_reason = payload.get("reason", payload.get("reason_code"))
    reason = raw_reason.strip() if isinstance(raw_reason, str) and raw_reason.strip() else state.lower()
    return invalidating, f"capture_health:{state.lower()}:{reason}"


def _validated_success_result(
    response: Mapping[str, Any],
    result: Mapping[str, Any],
    pending: PendingOffer,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Validate normal and additive knowledge-conflict success contracts."""

    ranking = result.get("ranking")
    recommended = result.get("recommended")
    if (
        not isinstance(ranking, list)
        or len(ranking) != 3
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("augment"), str)
            or not item["augment"]
            or not isinstance(item.get("stage_invalid"), bool)
            for item in ranking
        )
        or not isinstance(recommended, str)
        or not recommended
    ):
        raise BridgeError(
            "unsafe_engine_result",
            "engine result is missing, incomplete, or has invalid candidates",
        )

    if "knowledge_conflicts" not in response:
        if any(item["stage_invalid"] for item in ranking):
            raise BridgeError(
                "unsafe_engine_result",
                "stage-invalid success requires knowledge_conflicts evidence",
            )
        if recommended != ranking[0]["augment"]:
            raise BridgeError(
                "unsafe_engine_result",
                "normal recommendation does not match the first engine candidate",
            )
        return ranking, recommended, []

    conflicts = response.get("knowledge_conflicts")
    if not isinstance(conflicts, list) or not conflicts:
        raise BridgeError(
            "unsafe_engine_result",
            "knowledge_conflicts must be a non-empty array on conflict success",
        )

    invalid_indexes: list[int] = []
    valid_candidates: list[dict[str, Any]] = []
    for ranking_index, candidate in enumerate(ranking):
        eligible = candidate.get("recommendation_eligible")
        reason = candidate.get("eligibility_reason")
        conflict_index = candidate.get("knowledge_conflict_index")
        if not isinstance(eligible, bool) or eligible == candidate["stage_invalid"]:
            raise BridgeError(
                "unsafe_engine_result",
                "recommendation eligibility must be the inverse of stage_invalid",
            )
        if candidate["stage_invalid"]:
            if reason != "knowledge_conflict" or (
                isinstance(conflict_index, bool)
                or not isinstance(conflict_index, int)
            ):
                raise BridgeError(
                    "unsafe_engine_result",
                    "stage conflict candidate has invalid eligibility metadata",
                )
            invalid_indexes.append(ranking_index)
        else:
            if reason != "eligible" or conflict_index is not None:
                raise BridgeError(
                    "unsafe_engine_result",
                    "stage-valid candidate has invalid eligibility metadata",
                )
            valid_candidates.append(candidate)

    if not invalid_indexes or not valid_candidates:
        raise BridgeError(
            "unsafe_engine_result",
            "a successful conflict response needs both valid and invalid candidates",
        )
    if len(conflicts) != len(invalid_indexes):
        raise BridgeError(
            "unsafe_engine_result",
            "knowledge conflict count does not match stage-invalid candidates",
        )
    if recommended != valid_candidates[0]["augment"]:
        raise BridgeError(
            "unsafe_engine_result",
            "recommendation is not the first stage-valid engine candidate",
        )

    choice_mapping = response.get("choice_mapping")
    if not isinstance(choice_mapping, list) or len(choice_mapping) != 3:
        raise BridgeError(
            "unsafe_engine_result", "conflict response has no complete choice mapping"
        )
    mapping_by_name: dict[str, str] = {}
    for mapping in choice_mapping:
        if not isinstance(mapping, dict):
            raise BridgeError(
                "unsafe_engine_result", "conflict choice mapping is malformed"
            )
        augment_id = mapping.get("augment_id")
        display_name = mapping.get("display_name")
        if (
            not isinstance(augment_id, str)
            or not augment_id
            or not isinstance(display_name, str)
            or not display_name
            or display_name in mapping_by_name
        ):
            raise BridgeError(
                "unsafe_engine_result", "conflict choice mapping is malformed"
            )
        mapping_by_name[display_name] = augment_id
    offer_ids = {card.augment_id for card in pending.cards}
    if set(mapping_by_name.values()) != offer_ids:
        raise BridgeError(
            "unsafe_engine_result", "choice mapping does not match the vision offer"
        )

    used_conflict_indexes: set[int] = set()
    for ranking_index in invalid_indexes:
        candidate = ranking[ranking_index]
        conflict_index = candidate["knowledge_conflict_index"]
        if (
            conflict_index in used_conflict_indexes
            or not 0 <= conflict_index < len(conflicts)
        ):
            raise BridgeError(
                "unsafe_engine_result", "knowledge conflict index is invalid"
            )
        used_conflict_indexes.add(conflict_index)
        conflict = conflicts[conflict_index]
        debug = candidate.get("debug")
        score_adjustment = (
            conflict.get("score_adjustment_pp")
            if isinstance(conflict, dict)
            else None
        )
        if (
            not isinstance(conflict, dict)
            or not isinstance(debug, dict)
            or conflict.get("code") != "snapshot_available_stages_conflict"
            or conflict.get("augment") != candidate["augment"]
            or conflict.get("augment_id")
            != mapping_by_name.get(candidate["augment"])
            or conflict.get("observed_stage") != pending.stage
            or conflict.get("offer_evidence") != "vision_accepted"
            or conflict.get("snapshot_stage_source") != debug.get("stage_source")
            or conflict.get("snapshot_available_stages")
            != debug.get("available_stages")
            or conflict.get("snapshot_stage_agnostic")
            != debug.get("stage_agnostic")
            or conflict.get("original_stage_invalid") is not True
            or conflict.get("recommendation_eligible") is not False
            or isinstance(score_adjustment, bool)
            or not isinstance(score_adjustment, (int, float))
            or float(score_adjustment) != 0.0
            or conflict.get("debug_path")
            != f"/result/ranking/{ranking_index}/debug"
        ):
            raise BridgeError(
                "unsafe_engine_result", "knowledge conflict evidence is inconsistent"
            )

    if used_conflict_indexes != set(range(len(conflicts))):
        raise BridgeError(
            "unsafe_engine_result", "knowledge conflicts are not one-to-one"
        )
    return ranking, recommended, conflicts


class RecommendationCoordinator:
    """Pure four-round state machine around a worker-compatible object."""

    def __init__(
        self,
        hero: str,
        worker: Worker,
        initial_owned: Sequence[tuple[str, str]] = (),
    ) -> None:
        if not hero.strip():
            raise ValueError("hero must not be blank")
        owned_ids = [augment_id for augment_id, _ in initial_owned]
        owned_names = [name for _, name in initial_owned]
        if len(owned_ids) > 3 or len(set(owned_ids)) != len(owned_ids):
            raise ValueError("initial owned augments must contain 0..3 unique ids")
        if any(not value.strip() for value in [*owned_ids, *owned_names]):
            raise ValueError("initial owned augment ids and names must not be blank")
        self.state = SessionState(
            hero=hero.strip(),
            owned_augments=owned_ids,
            owned_names=owned_names,
        )
        self.worker = worker

    @property
    def snapshot_id(self) -> str | None:
        value = self.worker.ready.get("snapshot_id")
        return value if isinstance(value, str) else None

    @property
    def active_offer_identity(self) -> str | None:
        pending = self.state.current_offer
        if (
            pending is None
            or self.state.reconcile_required
            or self.state.vision_suspended
        ):
            return None
        return pending.fingerprint

    @property
    def manual_offer_identity(self) -> str | None:
        pending = self.state.current_offer
        if (
            pending is None
            or not pending.manual_confirmation_allowed
            or self.state.reconcile_required
            or self.state.vision_suspended
        ):
            return None
        return pending.fingerprint

    @property
    def hotkeys_enabled(self) -> bool:
        return self.manual_offer_identity is not None

    def invalidate_vision(self, reason: str) -> dict[str, Any] | None:
        """Suspend slot bindings while preserving the reducer/session state."""

        pending = self.state.current_offer
        already_stale = self.state.vision_suspended and (
            pending is None or pending.freshness == "stale"
        )
        self.state.vision_suspended = True
        self.state.vision_suspend_reason = reason
        if pending is not None:
            pending.freshness = "stale"
            pending.stale_reason = reason
        if already_stale:
            return None
        self.state.state_revision += 1
        if pending is None:
            return None
        cached = pending.recommendation_event or {}
        cached_ranking = cached.get("ranking")
        cached_choices = cached.get("choices")
        preserved = (
            pending.decision_committed
            and isinstance(cached.get("recommended"), str)
            and bool(cached["recommended"].strip())
            and isinstance(cached_ranking, list)
        )
        return {
            "type": "recommendation_invalidated",
            "status": "stale",
            "hero": self.state.hero,
            "stage": pending.stage,
            "offer_id": pending.fingerprint,
            "offer_fingerprint": pending.fingerprint,
            "reason": reason,
            "recommended": cached.get("recommended") if preserved else None,
            "ranking": list(cached_ranking) if preserved else [],
            "choices": (
                list(cached_choices)
                if preserved and isinstance(cached_choices, list)
                else [card.display_name or card.augment_id for card in pending.cards]
            ),
            "snapshot_id": cached.get("snapshot_id") if preserved else self.snapshot_id,
            "recommendation_freshness": "stale",
            "preserved_recommendation": preserved,
            "awaiting_offer_revalidation": preserved,
            "hotkeys_enabled": False,
            "state_revision": self.state.state_revision,
        }

    def apply_capture_health(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Transactionally synchronize C++ freshness into slot bindings."""

        invalidating, reason = _capture_health_invalidation(payload)
        if not invalidating:
            return None
        return self.invalidate_vision(reason)

    @staticmethod
    def _fail_closed(
        *,
        hero: str,
        stage: int | None,
        owned: Sequence[str],
        choices: Sequence[str],
        snapshot_id: str | None,
        code: str,
        message: str,
        bridge_latency_ms: float,
        worker_response: Mapping[str, Any] | None = None,
        pending: PendingOffer | None = None,
        manual_confirmation_available: bool = False,
        reconcile_required: bool = False,
        quarantined_offer_id: str | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "recommendation",
            "status": "fail_closed",
            "hero": hero,
            "stage": stage,
            "owned": list(owned),
            "choices": list(choices),
            "recommended": None,
            "ranking": [],
            "snapshot_id": snapshot_id,
            "engine_latency_ms": None,
            "bridge_latency_ms": round(bridge_latency_ms, 3),
            "error": {"code": code, "message": message},
            "hotkeys_enabled": manual_confirmation_available,
            "reconcile_required": reconcile_required,
        }
        if pending is not None:
            event.update(
                {
                    "offer_id": pending.fingerprint,
                    "offer_fingerprint": pending.fingerprint,
                    "vision_offer": pending.as_dict(),
                    "manual_confirmation_available": manual_confirmation_available,
                }
            )
        if quarantined_offer_id is not None:
            event["quarantined_offer_id"] = quarantined_offer_id
        if worker_response is not None:
            event["worker_response"] = dict(worker_response)
            result = worker_response.get("result")
            if isinstance(result, dict) and isinstance(result.get("ranking"), list):
                event["ranking"] = result["ranking"]
            latency = worker_response.get("engine_latency_ms")
            if isinstance(latency, (int, float)) and not isinstance(latency, bool):
                event["engine_latency_ms"] = latency
        return event

    def handle_game_state(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Process one accepted GameState; current duplicates are bounded diagnostics."""

        started = time.perf_counter_ns()
        try:
            pending = _parse_offer(payload, self.state.hero)
        except BridgeError as error:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
            stage = payload.get("offer_round")
            return self._fail_closed(
                hero=self.state.hero,
                stage=stage if isinstance(stage, int) and not isinstance(stage, bool) else None,
                owned=self.state.owned_names,
                choices=[],
                snapshot_id=self.snapshot_id,
                code=error.code,
                message=str(error),
                bridge_latency_ms=elapsed,
            )

        visual_choices = [
            card.display_name or card.augment_id for card in pending.cards
        ]
        active = self.state.current_offer
        if pending.fingerprint in self.state.seen_offers:
            if active is None or active.fingerprint != pending.fingerprint:
                return None
            if active.decision_committed and active.freshness == "current":
                if not active.duplicate_observation_reported:
                    active.duplicate_observation_reported = True
                    return {
                        "type": "bridge_diagnostic",
                        "status": "ignored",
                        "offer_id": active.fingerprint,
                        "hotkeys_enabled": self.hotkeys_enabled,
                        "error": {
                            "code": "current_offer_duplicate",
                            "message": "current validated offer was already recommended",
                        },
                    }
                return None

        if active is not None and active.fingerprint != pending.fingerprint:
            self.state.reconcile_required = True
            self.state.quarantined_offer = pending
            self.state.vision_suspended = False
            self.state.vision_suspend_reason = None
            self.state.state_revision += 1
            elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
            return self._fail_closed(
                hero=self.state.hero,
                stage=pending.stage,
                owned=self.state.owned_names,
                choices=visual_choices,
                snapshot_id=self.snapshot_id,
                code="selection_unconfirmed",
                message="the previous offer has no confirmed player selection",
                bridge_latency_ms=elapsed,
                pending=active,
                manual_confirmation_available=False,
                reconcile_required=True,
                quarantined_offer_id=pending.fingerprint,
            )
        if active is None and pending.stage != self.state.next_stage:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
            return self._fail_closed(
                hero=self.state.hero,
                stage=pending.stage,
                owned=self.state.owned_names,
                choices=visual_choices,
                snapshot_id=self.snapshot_id,
                code="stage_sequence_invalid",
                message=(
                    f"expected stage {self.state.next_stage} after "
                    f"{len(self.state.owned_augments)} confirmed choices"
                ),
                bridge_latency_ms=elapsed,
            )

        if active is not None:
            pending = active
            visual_choices = [
                card.display_name or card.augment_id for card in pending.cards
            ]
            if pending.decision_committed:
                if pending.freshness != "current" or self.state.vision_suspended:
                    stale_reason = (
                        pending.stale_reason
                        or self.state.vision_suspend_reason
                        or "vision_stale"
                    )
                    pending.freshness = "current"
                    pending.stale_reason = None
                    self.state.vision_suspended = False
                    self.state.vision_suspend_reason = None
                    self.state.state_revision += 1
                    restored = dict(pending.recommendation_event or {})
                    restored.update(
                        {
                            "type": "recommendation",
                            "status": restored.get("status", "recommended"),
                            "offer_id": pending.fingerprint,
                            "offer_fingerprint": pending.fingerprint,
                            "recommendation_freshness": "current",
                            "hotkeys_enabled": True,
                            "restored": True,
                            "restored_after_vision_restart": not stale_reason.startswith(
                                "capture_health:"
                            ),
                            "restored_after_capture_fallback": stale_reason.startswith(
                                "capture_health:"
                            ),
                            "restoration_reason": stale_reason,
                            "state_revision": self.state.state_revision,
                        }
                    )
                    return restored
                return None
        else:
            self.state.current_offer = pending
            self.state.state_revision += 1
        self.state.vision_suspended = False
        self.state.vision_suspend_reason = None
        request_id = f"offer-{pending.stage}-{pending.fingerprint[:16]}"
        request = {
            "type": "recommend",
            "request_id": request_id,
            "hero": self.state.hero,
            "owned_augments": list(self.state.owned_augments),
            "choices": [card.augment_id for card in pending.cards],
            "stage": pending.stage,
            "debug": True,
            "offer_evidence": "vision_accepted",
        }
        try:
            response = self.worker.recommend(request)
        except Exception as error:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
            return self._fail_closed(
                hero=self.state.hero,
                stage=pending.stage,
                owned=self.state.owned_names,
                choices=visual_choices,
                snapshot_id=self.snapshot_id,
                code="worker_ipc_error",
                message=str(error),
                bridge_latency_ms=elapsed,
                pending=pending,
                manual_confirmation_available=False,
            )

        elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
        if (
            response.get("type") != "recommendation_response"
            or response.get("request_id") != request_id
            or not isinstance(response.get("ok"), bool)
        ):
            return self._fail_closed(
                hero=self.state.hero,
                stage=pending.stage,
                owned=self.state.owned_names,
                choices=visual_choices,
                snapshot_id=self.snapshot_id,
                code="worker_protocol_error",
                message="worker response does not match the request",
                bridge_latency_ms=elapsed,
                worker_response=response,
                pending=pending,
                manual_confirmation_available=False,
            )

        mapping = response.get("choice_mapping")
        if isinstance(mapping, list):
            for item in mapping:
                if not isinstance(item, dict):
                    continue
                augment_id = item.get("augment_id")
                name = item.get("display_name")
                if isinstance(augment_id, str) and isinstance(name, str):
                    pending.resolved_names[augment_id] = name

        result = response.get("result")
        if response.get("ok") is not True:
            error = response.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            return self._fail_closed(
                hero=self.state.hero,
                stage=pending.stage,
                owned=self.state.owned_names,
                choices=visual_choices,
                snapshot_id=(
                    response.get("snapshot_id")
                    if isinstance(response.get("snapshot_id"), str)
                    else self.snapshot_id
                ),
                code=code if isinstance(code, str) else "worker_rejected",
                message=message if isinstance(message, str) else "worker rejected offer",
                bridge_latency_ms=elapsed,
                worker_response=response,
                pending=pending,
                manual_confirmation_available=False,
            )
        if not isinstance(result, dict):
            return self._fail_closed(
                hero=self.state.hero,
                stage=pending.stage,
                owned=self.state.owned_names,
                choices=visual_choices,
                snapshot_id=self.snapshot_id,
                code="worker_protocol_error",
                message="successful worker response has no result",
                bridge_latency_ms=elapsed,
                worker_response=response,
                pending=pending,
                manual_confirmation_available=False,
            )

        try:
            ranking, recommended, knowledge_conflicts = _validated_success_result(
                response, result, pending
            )
        except BridgeError as error:
            return self._fail_closed(
                hero=self.state.hero,
                stage=pending.stage,
                owned=self.state.owned_names,
                choices=visual_choices,
                snapshot_id=self.snapshot_id,
                code=error.code,
                message=str(error),
                bridge_latency_ms=elapsed,
                worker_response=response,
                pending=pending,
                manual_confirmation_available=False,
            )

        event = {
            "type": "recommendation",
            "status": (
                "recommended_with_knowledge_conflict"
                if knowledge_conflicts
                else "recommended"
            ),
            "hero": result.get("hero", self.state.hero),
            "stage": pending.stage,
            "owned": result.get("owned_augments", list(self.state.owned_names)),
            "choices": result.get("choices", visual_choices),
            "recommended": recommended,
            "ranking": ranking,
            "snapshot_id": response.get("snapshot_id"),
            "engine_latency_ms": response.get("engine_latency_ms"),
            "bridge_latency_ms": round(elapsed, 3),
            "offer_fingerprint": pending.fingerprint,
            "offer_id": pending.fingerprint,
            "vision_offer": pending.as_dict(),
            "engine_result": result,
            "recommendation_freshness": "current",
            "hotkeys_enabled": True,
            "state_revision": self.state.state_revision,
        }
        if knowledge_conflicts:
            event["knowledge_conflicts"] = knowledge_conflicts
        pending.decision_committed = True
        pending.manual_confirmation_allowed = True
        pending.freshness = "current"
        pending.recommendation_event = dict(event)
        self.state.seen_offers.add(pending.fingerprint)
        self.state.state_revision += 1
        event["state_revision"] = self.state.state_revision
        return event

    def confirm_selection(
        self,
        slot_number: int,
        *,
        offer_identity: str | None = None,
        basis: str = "manual_hotkey",
    ) -> list[dict[str, Any]]:
        """Confirm the player's manual slot choice and advance owned state."""

        pending = self.state.current_offer
        if pending is None:
            return [
                {
                    "type": "choice_confirmation",
                    "status": "ignored",
                    "hero": self.state.hero,
                    "stage": None,
                    "selected": None,
                    "reason": "no_pending_offer",
                }
            ]
        if isinstance(slot_number, bool) or slot_number not in {1, 2, 3}:
            raise ValueError("slot_number must be 1, 2, or 3")
        if basis not in {"manual_hotkey", "manual_reconcile"}:
            raise ValueError("basis must be manual_hotkey or manual_reconcile")
        if basis == "manual_hotkey" and not pending.manual_confirmation_allowed:
            return [
                {
                    "type": "choice_confirmation",
                    "status": "ignored",
                    "hero": self.state.hero,
                    "stage": pending.stage,
                    "selected": None,
                    "offer_id": pending.fingerprint,
                    "reason": "manual_confirmation_unavailable",
                }
            ]
        if self.state.vision_suspended and basis != "manual_reconcile":
            return [
                {
                    "type": "choice_confirmation",
                    "status": "ignored",
                    "hero": self.state.hero,
                    "stage": pending.stage,
                    "selected": None,
                    "offer_id": pending.fingerprint,
                    "reason": "vision_not_current",
                }
            ]
        if self.state.reconcile_required and basis != "manual_reconcile":
            return [
                {
                    "type": "choice_confirmation",
                    "status": "ignored",
                    "hero": self.state.hero,
                    "stage": pending.stage,
                    "selected": None,
                    "offer_id": pending.fingerprint,
                    "reason": "reconcile_required",
                }
            ]
        if offer_identity is not None and offer_identity != pending.fingerprint:
            return [
                {
                    "type": "choice_confirmation",
                    "status": "ignored",
                    "hero": self.state.hero,
                    "stage": pending.stage,
                    "selected": None,
                    "offer_id": pending.fingerprint,
                    "reason": "stale_offer_binding",
                }
            ]
        card = pending.cards[slot_number - 1]
        if card.augment_id in self.state.owned_augments:
            return [
                {
                    "type": "choice_confirmation",
                    "status": "fail_closed",
                    "hero": self.state.hero,
                    "stage": pending.stage,
                    "selected": None,
                    "reason": "duplicate_owned_augment",
                }
            ]

        return self._commit_selection(pending, card, basis=basis)

    @staticmethod
    def _selection_observation_evidence(
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Keep the decision inputs JSON-safe for durable fail-closed logging."""

        evidence: dict[str, Any] = {}
        for key in (
            "type",
            "schema_version",
            "offer_id",
            "fingerprint",
            "offer_fingerprint",
            "offer_stage",
            "offer_augment_ids",
            "selected_slot",
            "selected_augment_id",
            "confidence",
            "top1_score",
            "top1_margin",
            "stable_frames",
            "candidate_scope",
            "hud_slot_index",
            "frame_id",
            "source",
            "recognition_status",
            "reason",
            "vision_generation",
        ):
            if key not in payload:
                continue
            value = payload[key]
            if isinstance(value, float) and not math.isfinite(value):
                evidence[key] = repr(value)
            elif key == "offer_augment_ids" and isinstance(value, list):
                evidence[key] = list(value)
            elif value is None or isinstance(value, (str, int, float, bool)):
                evidence[key] = value
            else:
                evidence[key] = repr(value)
        return evidence

    def _selection_observation_result(
        self,
        *,
        status: str,
        evidence: Mapping[str, Any],
        code: str | None = None,
        message: str | None = None,
        reason: str | None = None,
        pending: PendingOffer | None = None,
        confirmed: ConfirmedSelection | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "selection_observed",
            "status": status,
            "hero": self.state.hero,
            "stage": pending.stage if pending is not None else None,
            "owned_augments": list(self.state.owned_augments),
            "owned": list(self.state.owned_names),
            "owned_updated": False,
            "selection_observation": dict(evidence),
            "evidence_retained": True,
            "hotkeys_enabled": self.hotkeys_enabled,
            "state_revision": self.state.state_revision,
        }
        if pending is not None:
            event.update(
                {
                    "stage": pending.stage,
                    "offer_id": pending.fingerprint,
                    "offer_fingerprint": pending.fingerprint,
                    "pending_offer": pending.as_dict(),
                }
            )
        if confirmed is not None:
            event["confirmed_selection"] = confirmed.as_dict()
            event["stage"] = confirmed.stage
            event.setdefault("offer_id", confirmed.offer_id)
            event.setdefault("offer_fingerprint", confirmed.offer_id)
        if code is not None:
            event["error"] = {"code": code, "message": message or code}
        if reason is not None:
            event["reason"] = reason
        if idempotent:
            event["idempotent"] = True
        return event

    def handle_selection_observed(
        self, payload: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Reconcile one screen-derived selection without trusting a ranking."""

        evidence = self._selection_observation_evidence(payload)

        source_value = payload.get("source")
        if source_value == "hud_icon_template":
            schema_version = payload.get("schema_version")
            if type(schema_version) is not int or schema_version != 1:
                return [
                    self._selection_observation_result(
                        status="fail_closed",
                        evidence=evidence,
                        code="unsupported_selection_schema",
                        message="HUD selection observation requires schema_version 1",
                        pending=self.state.current_offer,
                    )
                ]

        identities: list[str] = []
        for key in ("offer_id", "fingerprint", "offer_fingerprint"):
            if key not in payload:
                continue
            value = payload.get(key)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value.strip().casefold() == "unknown"
            ):
                return [
                    self._selection_observation_result(
                        status="fail_closed",
                        evidence=evidence,
                        code="unknown_selection_observation",
                        message=f"{key} must be an exact non-UNKNOWN offer identity",
                        pending=self.state.current_offer,
                    )
                ]
            identities.append(value)
        if not identities:
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="invalid_selection_observation",
                    message="selection observation requires offer_id or fingerprint",
                    pending=self.state.current_offer,
                )
            ]
        if len(set(identities)) != 1:
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="conflicting_selection_observation",
                    message="selection observation offer identities conflict",
                    pending=self.state.current_offer,
                )
            ]
        offer_identity = identities[0]

        selected_slot = payload.get("selected_slot")
        selected_augment_id = payload.get("selected_augment_id")
        source = payload.get("source")
        if (
            not isinstance(selected_slot, str)
            or selected_slot not in SLOTS
            or not isinstance(selected_augment_id, str)
            or not selected_augment_id.strip()
            or selected_augment_id.strip().casefold() == "unknown"
            or not isinstance(source, str)
            or not source.strip()
            or source.strip().casefold() == "unknown"
        ):
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="unknown_selection_observation",
                    message=(
                        "selected_slot, selected_augment_id, and source must be "
                        "exact non-UNKNOWN values"
                    ),
                    pending=self.state.current_offer,
                )
            ]
        if source.strip().casefold() in {
            "engine",
            "ranker",
            "ranking",
            "recommendation",
            "recommendation_engine",
        }:
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="invalid_selection_source",
                    message="recommendation output is not player-selection evidence",
                    pending=self.state.current_offer,
                )
            ]

        confidence = payload.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="invalid_selection_confidence",
                    message="confidence must be a finite number in [0, 1]",
                    pending=self.state.current_offer,
                )
            ]
        confidence_value = float(confidence)
        minimum_confidence = SELECTION_OBSERVATION_MIN_CONFIDENCE
        if source.strip().casefold() == "hud_icon_template":
            top1_score = payload.get("top1_score")
            top1_margin = payload.get("top1_margin")
            stable_frames = payload.get("stable_frames")
            candidate_scope = payload.get("candidate_scope")
            hud_slot_index = payload.get("hud_slot_index")
            structured_evidence_valid = (
                not isinstance(top1_score, bool)
                and isinstance(top1_score, (int, float))
                and math.isfinite(float(top1_score))
                and HUD_SELECTION_MIN_TOP1_SCORE <= float(top1_score) <= 1.0
                and not isinstance(top1_margin, bool)
                and isinstance(top1_margin, (int, float))
                and math.isfinite(float(top1_margin))
                and HUD_SELECTION_MIN_TOP1_MARGIN <= float(top1_margin) <= 1.0
                and not isinstance(stable_frames, bool)
                and isinstance(stable_frames, int)
                and stable_frames >= HUD_SELECTION_MIN_STABLE_FRAMES
                and candidate_scope == "pending_offer"
                and not isinstance(hud_slot_index, bool)
                and isinstance(hud_slot_index, int)
                and hud_slot_index in {0, 1, 2, 3}
                and math.isclose(
                    confidence_value,
                    float(top1_score),
                    rel_tol=0.0,
                    abs_tol=1e-6,
                )
            )
            if not structured_evidence_valid:
                return [
                    self._selection_observation_result(
                        status="fail_closed",
                        evidence=evidence,
                        code="invalid_hud_selection_evidence",
                        message=(
                            "HUD selection requires offer-scoped, two-frame, "
                            "score-and-margin-bound icon evidence"
                        ),
                        pending=self.state.current_offer,
                    )
                ]
            minimum_confidence = HUD_SELECTION_MIN_TOP1_SCORE
        if confidence_value < minimum_confidence:
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="low_selection_confidence",
                    message=(
                        "confidence is below the high-confidence threshold "
                        f"{minimum_confidence:.2f}"
                    ),
                    pending=self.state.current_offer,
                )
            ]

        pending = self.state.current_offer
        if pending is None:
            confirmed = self.state.confirmed_selections.get(offer_identity)
            if confirmed is None:
                return [
                    self._selection_observation_result(
                        status="fail_closed",
                        evidence=evidence,
                        code="no_pending_selection_offer",
                        message="selection observation has no matching pending offer",
                    )
                ]
            if (
                confirmed.selected_slot == selected_slot
                and confirmed.selected_augment_id == selected_augment_id
            ):
                return [
                    self._selection_observation_result(
                        status="ignored",
                        evidence=evidence,
                        reason="duplicate_selection_observation",
                        confirmed=confirmed,
                        idempotent=True,
                    )
                ]
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="conflicting_selection_observation",
                    message="selection conflicts with the confirmed result for this offer",
                    confirmed=confirmed,
                )
            ]

        if offer_identity != pending.fingerprint:
            confirmed = self.state.confirmed_selections.get(offer_identity)
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="stale_selection_observation",
                    message="selection observation does not match the pending offer",
                    pending=pending,
                    confirmed=confirmed,
                )
            ]
        if self.state.vision_suspended or pending.freshness != "current":
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="vision_not_current",
                    message="selection observation cannot bind to stale vision",
                    pending=pending,
                )
            ]
        if self.state.reconcile_required:
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="selection_reconcile_required",
                    message="conflicting offer evidence requires manual reconciliation",
                    pending=pending,
                )
            ]

        if (
            source.strip().casefold() == "hud_icon_template"
            and payload.get("hud_slot_index") != pending.stage - 1
        ):
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="hud_slot_stage_mismatch",
                    message="HUD owned slot does not match the pending offer stage",
                    pending=pending,
                )
            ]

        matches = [
            card
            for card in pending.cards
            if card.slot == selected_slot
            and card.augment_id == selected_augment_id
        ]
        if len(matches) != 1:
            return [
                self._selection_observation_result(
                    status="fail_closed",
                    evidence=evidence,
                    code="conflicting_selection_observation",
                    message=(
                        "selected slot and augment id do not form one exact pending-offer card"
                    ),
                    pending=pending,
                )
            ]
        return self._commit_selection(
            pending,
            matches[0],
            basis="selection_observed",
            observation=evidence,
            confidence=confidence_value,
            observation_source=source,
        )

    def _commit_selection(
        self,
        pending: PendingOffer,
        card: OfferCard,
        *,
        basis: str,
        observation: Mapping[str, Any] | None = None,
        confidence: float | None = None,
        observation_source: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_name = pending.resolved_names.get(
            card.augment_id, card.display_name or card.augment_id
        )
        self.state.owned_augments.append(card.augment_id)
        self.state.owned_names.append(selected_name)
        self.state.seen_offers.add(pending.fingerprint)
        confirmed = ConfirmedSelection(
            stage=pending.stage,
            offer_id=pending.fingerprint,
            selected_slot=card.slot,
            selected_augment_id=card.augment_id,
            selected=selected_name,
            confirmation_basis=basis,
            confidence=confidence,
            observation_source=observation_source,
        )
        self.state.confirmed_selections[pending.fingerprint] = confirmed
        self.state.current_offer = None
        self.state.quarantined_offer = None
        self.state.reconcile_required = False
        self.state.vision_suspended = False
        self.state.vision_suspend_reason = None
        self.state.state_revision += 1
        event = {
            "type": "choice_confirmation",
            "status": "choice_confirmed",
            "hero": self.state.hero,
            "stage": pending.stage,
            "selected_slot": card.slot,
            "selected_augment_id": card.augment_id,
            "selected": selected_name,
            "owned_augments": list(self.state.owned_augments),
            "owned": list(self.state.owned_names),
            "snapshot_id": self.snapshot_id,
            "offer_fingerprint": pending.fingerprint,
            "offer_id": pending.fingerprint,
            "confirmation_basis": basis,
            "owned_updated": True,
            "hotkeys_enabled": False,
            "state_revision": self.state.state_revision,
        }
        if observation is not None:
            event.update(
                {
                    "selection_observation": dict(observation),
                    "evidence_retained": True,
                    "confidence": confidence,
                    "observation_source": observation_source,
                }
            )
        events = [event]
        if len(self.state.owned_augments) == 4:
            events.append(
                {
                    "type": "recommendation_session_complete",
                    "status": "completed",
                    "hero": self.state.hero,
                    "stages_completed": 4,
                    "owned_augments": list(self.state.owned_augments),
                    "owned": list(self.state.owned_names),
                    "snapshot_id": self.snapshot_id,
                }
            )
        return events


class EventSink:
    """Write every bridge event to console and durable JSONL."""

    _SOURCE_KINDS = {"screen_capture", "replay", "synthetic"}

    def __init__(
        self,
        path: Path,
        stdout: TextIO = sys.stdout,
        *,
        source_kind: str = "synthetic",
        source_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if source_kind not in self._SOURCE_KINDS:
            raise ValueError("source_kind must be screen_capture, replay, or synthetic")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._stdout = stdout
        self._file = path.open("a", encoding="utf-8", newline="\n")
        self._source_kind = source_kind
        self._source_metadata = dict(source_metadata or {})
        self._source_metadata["kind"] = source_kind
        self._sidecar: SidecarClient | None = None
        self._write_lock = threading.RLock()
        self._sidecar_diagnostic_emitted = False
        self._closed = False

    def attach_sidecar(self, sidecar: "SidecarClient") -> None:
        with self._write_lock:
            if self._closed:
                raise RuntimeError("event sink is closed")
            self._sidecar = sidecar

    def emit(self, payload: Mapping[str, Any]) -> None:
        event = dict(payload)
        event.setdefault(
            "bridge_emitted_at_utc",
            datetime.now(timezone.utc).isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        )
        event["source"] = dict(self._source_metadata)
        line = json.dumps(
            event, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        with self._write_lock:
            if self._closed:
                return
            self._stdout.write(line + "\n")
            self._stdout.flush()
            self._file.write(line + "\n")
            self._file.flush()
            sidecar = self._sidecar
        if sidecar is not None:
            sidecar.offer(line)

    def sidecar_unavailable(self, reason: str) -> None:
        """Persist one UI-only degradation without forwarding it recursively."""

        with self._write_lock:
            if self._closed or self._sidecar_diagnostic_emitted:
                return
            self._sidecar_diagnostic_emitted = True
            event = {
                "type": "bridge_diagnostic",
                "status": "degraded",
                "recommended": None,
                "error": {
                    "code": "sidecar_unavailable",
                    "message": f"sidecar disabled: {reason}",
                },
                "bridge_emitted_at_utc": datetime.now(timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                "source": dict(self._source_metadata),
            }
            line = json.dumps(
                event, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
            self._stdout.write(line + "\n")
            self._stdout.flush()
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._write_lock:
            if self._closed:
                return
            self._closed = True
            self._sidecar = None
            self._file.close()

    def __enter__(self) -> "EventSink":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _stderr_reader(stream: TextIO, prefix: str) -> None:
    try:
        for line in stream:
            sys.stderr.write(f"[{prefix}] {line}")
            sys.stderr.flush()
    except (OSError, UnicodeError):
        return


_SIDECAR_STOP = object()


class SidecarClient:
    """Best-effort, non-blocking JSONL writer for the independent result UI."""

    def __init__(
        self,
        *,
        python: Path,
        script: Path,
        on_unavailable: Callable[[str], None] | None = None,
        queue_capacity: int = SIDECAR_QUEUE_CAPACITY,
        extra_args: Sequence[str] | None = None,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("sidecar queue capacity must be positive")
        self._on_unavailable = on_unavailable
        self._events: queue.Queue[str | object] = queue.Queue(
            maxsize=queue_capacity
        )
        self._state_lock = threading.Lock()
        self._enabled = True
        self._closing = False
        self._diagnostic_reported = False
        self._process = subprocess.Popen(
            [str(python), "-B", str(script), *(extra_args or [])],
            cwd=WORKSPACE_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            creationflags=_creation_flags(),
        )
        if self._process.stdin is None:
            self._process.terminate()
            raise RuntimeError("sidecar stdin pipe was not created")
        self._writer_thread = threading.Thread(
            target=self._write_events,
            name="sidecar-jsonl-writer",
            daemon=True,
        )
        self._writer_thread.start()
        if self._process.stderr is not None:
            threading.Thread(
                target=_stderr_reader,
                args=(self._process.stderr, "sidecar"),
                name="sidecar-stderr",
                daemon=True,
            ).start()

    @property
    def enabled(self) -> bool:
        with self._state_lock:
            return self._enabled

    def _disable(self, reason: str) -> None:
        callback: Callable[[str], None] | None = None
        with self._state_lock:
            if not self._enabled:
                return
            self._enabled = False
            if not self._closing and not self._diagnostic_reported:
                self._diagnostic_reported = True
                callback = self._on_unavailable
        try:
            if self._process.poll() is None:
                self._process.terminate()
        except (OSError, ProcessLookupError):
            pass
        if callback is not None:
            try:
                callback(reason)
            except Exception:
                pass

    def offer(self, line: str) -> None:
        """Queue one EventSink-produced JSON line without waiting or raising."""

        try:
            if self._process.poll() is not None:
                self._disable(
                    f"process exited with code {self._process.returncode}"
                )
                return
            with self._state_lock:
                if not self._enabled or self._closing:
                    return
            self._events.put_nowait(line)
        except queue.Full:
            self._disable("event queue is full")
        except Exception as error:
            self._disable(f"event queue failed: {error}")

    def _write_events(self) -> None:
        assert self._process.stdin is not None
        try:
            while True:
                item = self._events.get()
                if item is _SIDECAR_STOP:
                    return
                with self._state_lock:
                    if not self._enabled and not self._closing:
                        return
                if self._process.poll() is not None:
                    self._disable(
                        f"process exited with code {self._process.returncode}"
                    )
                    return
                assert isinstance(item, str)
                self._process.stdin.write(item + "\n")
                self._process.stdin.flush()
        except (BrokenPipeError, OSError, UnicodeError, ValueError) as error:
            self._disable(f"stdin unavailable: {error}")
        finally:
            try:
                if not self._process.stdin.closed:
                    self._process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def close(self, timeout_seconds: float = SIDECAR_CLOSE_TIMEOUT_SECONDS) -> None:
        """Best-effort bounded shutdown; never wait indefinitely for the UI."""

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
            self._enabled = False
        try:
            self._events.put_nowait(_SIDECAR_STOP)
        except queue.Full:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
            except (OSError, ProcessLookupError):
                pass

        self._writer_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        try:
            if self._process.poll() is None:
                self._process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                self._process.terminate()
            except (OSError, ProcessLookupError):
                pass
            try:
                self._process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except (OSError, ProcessLookupError):
                    pass
        finally:
            for stream in (self._process.stdin, self._process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except (BrokenPipeError, OSError, ValueError):
                        pass


class RecommendationWorkerClient:
    """Synchronous API over a resident, generation-isolated JSONL worker."""

    def __init__(
        self,
        *,
        python: Path,
        scrape_root: Path,
        visual_catalog: Path,
        timeout_seconds: float,
    ) -> None:
        self._command = [
            str(python),
            "-B",
            "-m",
            "src.recommendation_worker",
            "--data-dir",
            str(scrape_root / "data"),
            "--visual-catalog",
            str(visual_catalog),
        ]
        self._cwd = scrape_root
        self._timeout = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | BaseException | None] = queue.Queue()
        self._ready: dict[str, Any] = {}
        self._generation = 0
        self._faulted = False
        self._fault_reason: str | None = None
        self._closed = False
        self._start_generation()

    def _spawn_process(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            self._command,
            cwd=self._cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            creationflags=_creation_flags(),
        )

    @staticmethod
    def _validate_ready(ready: Mapping[str, Any]) -> None:
        if ready.get("type") != "worker_ready" or ready.get("ok") is not True:
            raise RuntimeError(f"recommendation worker initialization failed: {ready}")
        if (
            ready.get("single_prior_games") != 5000
            or ready.get("combo_prior_games") != 2000
        ):
            raise RuntimeError("recommendation worker prior gate mismatch")
        if ready.get("engine_load_count") != 1:
            raise RuntimeError("recommendation engine was not loaded exactly once")

    def _start_generation(self) -> None:
        process = self._spawn_process()
        if process.stdin is None or process.stdout is None:
            self._terminate_process(process)
            self._close_process_pipes(process)
            raise RuntimeError("worker pipes were not created")
        responses: queue.Queue[str | BaseException | None] = queue.Queue()
        self._process = process
        self._responses = responses
        self._generation = getattr(self, "_generation", 0) + 1
        self._faulted = False
        self._fault_reason = None
        generation = self._generation
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process, responses),
            name=f"recommendation-worker-stdout-g{generation}",
            daemon=True,
        )
        self._stdout_thread.start()
        if process.stderr is not None:
            threading.Thread(
                target=_stderr_reader,
                args=(process.stderr, f"recommendation-worker-g{generation}"),
                name=f"recommendation-worker-stderr-g{generation}",
                daemon=True,
            ).start()
        try:
            ready = self._read_message(responses=responses)
            self._validate_ready(ready)
        except Exception:
            self._terminate_process(process)
            self._close_process_pipes(process)
            if self._process is process:
                self._process = None
            raise
        self._ready = ready
        self._faulted = False
        self._fault_reason = None

    @property
    def ready(self) -> Mapping[str, Any]:
        return self._ready

    @property
    def generation(self) -> int:
        return self._generation

    @staticmethod
    def _read_stdout(
        process: subprocess.Popen[str],
        responses: queue.Queue[str | BaseException | None],
    ) -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                responses.put(line)
            responses.put(None)
        except BaseException as error:
            responses.put(error)

    def _read_message(
        self,
        *,
        responses: queue.Queue[str | BaseException | None] | None = None,
    ) -> dict[str, Any]:
        response_queue = responses if responses is not None else self._responses
        try:
            item = response_queue.get(timeout=self._timeout)
        except queue.Empty as error:
            self._faulted = True
            self._fault_reason = "response_timeout"
            raise TimeoutError("recommendation worker response timeout") from error
        if item is None:
            self._faulted = True
            self._fault_reason = "stdout_closed"
            raise RuntimeError("recommendation worker closed stdout")
        if isinstance(item, BaseException):
            self._faulted = True
            self._fault_reason = "stdout_read_error"
            raise RuntimeError(f"recommendation worker read failed: {item}") from item
        try:
            payload = json.loads(item)
        except json.JSONDecodeError as error:
            self._faulted = True
            self._fault_reason = "invalid_json"
            raise RuntimeError("recommendation worker emitted invalid JSON") from error
        if not isinstance(payload, dict):
            self._faulted = True
            self._fault_reason = "non_object"
            raise RuntimeError("recommendation worker emitted a non-object")
        return payload

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            return

    @staticmethod
    def _close_process_pipes(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        for stream in (
            getattr(process, "stdin", None),
            getattr(process, "stdout", None),
            getattr(process, "stderr", None),
        ):
            if stream is not None and not getattr(stream, "closed", False):
                try:
                    stream.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass

    def _replace_generation(self, reason: str) -> bool:
        """Quarantine the old FIFO and complete a fresh ready handshake."""

        old_process = getattr(self, "_process", None)
        command = getattr(self, "_command", None)
        if command is None:
            # A fail-closed compatibility path for partially constructed test
            # clients: detach the contaminated FIFO even when no spawn recipe
            # exists.  Production clients always take the branch below.
            self._responses = queue.Queue()
            self._generation = getattr(self, "_generation", 0) + 1
            self._faulted = False
            self._fault_reason = reason
            return False
        self._terminate_process(old_process)
        self._close_process_pipes(old_process)
        self._process = None
        self._responses = queue.Queue()
        try:
            self._start_generation()
        except Exception as error:
            self._faulted = True
            self._fault_reason = f"{reason}: {error}"
            return False
        return True

    def _failure_response(
        self, request_id: str, code: str, message: str
    ) -> dict[str, Any]:
        ready = getattr(self, "_ready", {})
        return {
            "type": "recommendation_response",
            "request_id": request_id,
            "ok": False,
            "snapshot_id": ready.get("snapshot_id") if isinstance(ready, Mapping) else None,
            "worker_generation": getattr(self, "_generation", 0),
            "error": {"code": code, "message": message},
        }

    def recommend(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("recommendation request_id must be a non-empty string")
        if getattr(self, "_closed", False):
            return self._failure_response(
                request_id, "worker_closed", "recommendation worker is closed"
            )
        if getattr(self, "_faulted", False):
            self._replace_generation(getattr(self, "_fault_reason", None) or "prior_fault")

        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            if not self._replace_generation("process_not_running"):
                process = getattr(self, "_process", None)
                if process is None:
                    return self._failure_response(
                        request_id,
                        "worker_restart_failed",
                        "recommendation worker replacement failed",
                    )
            process = self._process

        try:
            assert process is not None and process.stdin is not None
            process.stdin.write(
                json.dumps(
                    request,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            process.stdin.flush()
        except Exception as error:
            self._faulted = True
            self._fault_reason = "request_write_error"
            self._replace_generation("request_write_error")
            return self._failure_response(
                request_id, "worker_write_error", str(error)
            )

        try:
            response = self._read_message()
        except Exception as error:
            reason = getattr(self, "_fault_reason", None) or "response_read_error"
            self._replace_generation(reason)
            code = "worker_timeout" if isinstance(error, TimeoutError) else "worker_transport_error"
            return self._failure_response(request_id, code, str(error))

        if (
            response.get("type") != "recommendation_response"
            or response.get("request_id") != request_id
            or not isinstance(response.get("ok"), bool)
        ):
            self._faulted = True
            self._fault_reason = "protocol_desync"
            self._replace_generation("protocol_desync")
            return self._failure_response(
                request_id,
                "worker_protocol_desync",
                "worker response did not match the active request generation",
            )
        self._faulted = False
        self._fault_reason = None
        return response

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        process = getattr(self, "_process", None)
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
            request_id = f"shutdown-{uuid.uuid4().hex}"
            assert process.stdin is not None
            process.stdin.write(
                json.dumps(
                    {"type": "shutdown", "request_id": request_id},
                    separators=(",", ":"),
                )
                + "\n"
            )
            process.stdin.flush()
            response = self._read_message()
            if (
                response.get("type") != "worker_stopped"
                or response.get("request_id") != request_id
            ):
                raise RuntimeError("worker did not acknowledge shutdown")
            process.wait(timeout=2.0)
        except Exception:
            self._terminate_process(process)
        finally:
            self._close_process_pipes(process)
            self._process = None

    def _close_pipes(self) -> None:
        self._close_process_pipes(getattr(self, "_process", None))

    def __enter__(self) -> "RecommendationWorkerClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class HotkeyPoller:
    """Read Ctrl+Alt+1/2/3 state without registering or consuming keys."""

    _VK_CONTROL = 0x11
    _VK_MENU = 0x12
    _SLOT_KEYS = (0x31, 0x32, 0x33)

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("selection hotkeys require Windows")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._get_key_state = self._user32.GetAsyncKeyState
        self._get_key_state.argtypes = [ctypes.c_int]
        self._get_key_state.restype = ctypes.c_short
        self._previous = [False, False, False]

    def _down(self, virtual_key: int) -> bool:
        return bool(self._get_key_state(virtual_key) & 0x8000)

    def poll(self) -> int | None:
        modifier_down = self._down(self._VK_CONTROL) and self._down(self._VK_MENU)
        result: int | None = None
        for index, virtual_key in enumerate(self._SLOT_KEYS):
            active = modifier_down and self._down(virtual_key)
            if active and not self._previous[index] and result is None:
                result = index + 1
            self._previous[index] = active
        return result


def _vision_command(args: argparse.Namespace) -> list[str]:
    command = [str(args.vision_exe)]
    live_source = args.replay is None
    if args.replay is not None:
        command.extend(["--replay", str(args.replay)])
    elif args.hwnd is not None:
        command.extend(["--hwnd", args.hwnd])
    else:
        command.extend(["--window-title", args.window_title or DEFAULT_WINDOW_TITLE])
    if live_source:
        capture_backend = getattr(args, "capture_backend", None)
        if capture_backend is None:
            legacy_backend = os.environ.get("LOL_ASSISTANT_CAPTURE_BACKEND", "auto")
            capture_backend = (
                legacy_backend if legacy_backend in {"auto", "wgc", "desktop"} else "auto"
            )
        command.extend(["--capture-backend", capture_backend])
        lcu_context = getattr(args, "lcu_context", None) or "auto"
        command.extend(["--lcu-context", lcu_context])
        completed_offers = getattr(
            args,
            "vision_completed_offers",
            len(getattr(args, "resume_owned", [])),
        )
        command.extend(["--completed-offers", str(completed_offers)])
    command.extend(
        [
            "--champion",
            args.hero,
            "--mode",
            args.mode,
            "--knowledge",
            str(args.knowledge),
            "--workspace",
            str(args.runtime_root),
            "--max-seconds",
            str(args.max_seconds),
        ]
    )
    if live_source and getattr(args, "collect_samples", False):
        command.append("--collect-samples")
    if live_source and getattr(args, "no_force_recognition_hotkey", False):
        command.append("--no-force-recognition-hotkey")
    return command


def _start_vision(args: argparse.Namespace) -> subprocess.Popen[str]:
    return subprocess.Popen(
        _vision_command(args),
        cwd=WORKSPACE_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        bufsize=1,
        creationflags=_creation_flags(),
    )


@dataclass(frozen=True)
class VisionStreamEvent:
    generation: int
    kind: str
    value: str | BaseException | None = None


def _vision_stdout_reader(
    stream: TextIO,
    events: queue.Queue[VisionStreamEvent],
    generation: int = 0,
) -> None:
    try:
        for line in stream:
            events.put(VisionStreamEvent(generation, "line", line))
        events.put(VisionStreamEvent(generation, "eof"))
    except BaseException as error:
        events.put(VisionStreamEvent(generation, "error", error))


class VisionGenerationSupervisor:
    """Bounded live-child replacement with heartbeat and generation gates."""

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        emit: Callable[[Mapping[str, Any]], None],
        on_restart: Callable[[str], None],
        stage_offset: Callable[[], int],
        process_factory: Callable[[argparse.Namespace], subprocess.Popen[str]] = _start_vision,
        clock: Callable[[], float] = time.monotonic,
        reader_starter: Callable[[subprocess.Popen[str], queue.Queue[VisionStreamEvent], int], None] | None = None,
    ) -> None:
        self.args = args
        self._emit = emit
        self._on_restart = on_restart
        self._stage_offset = stage_offset
        self._process_factory = process_factory
        self._clock = clock
        self._reader_starter = reader_starter or self._start_readers
        self.events: queue.Queue[VisionStreamEvent] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.generation = 0
        self.current_stage_offset = 0
        self.state = "STOPPED"
        self.finished = False
        self.degraded = False
        self.last_exit_code: int | None = None
        self._restart_due: float | None = None
        self._restart_attempts: deque[float] = deque()
        self._restart_reason: str | None = None
        self._stopping = False
        self.heartbeat_capable = False
        self.last_heartbeat: float | None = None
        self.last_heartbeat_event_seq: int | None = None
        self._vision_invalidated_generation: int | None = None

    @property
    def live(self) -> bool:
        return getattr(self.args, "replay", None) is None

    @staticmethod
    def _start_readers(
        process: subprocess.Popen[str],
        events: queue.Queue[VisionStreamEvent],
        generation: int,
    ) -> None:
        if process.stdout is None:
            raise RuntimeError("vision stdout pipe was not created")
        threading.Thread(
            target=_vision_stdout_reader,
            args=(process.stdout, events, generation),
            name=f"vision-stdout-g{generation}",
            daemon=True,
        ).start()
        if process.stderr is not None:
            threading.Thread(
                target=_stderr_reader,
                args=(process.stderr, f"vision-g{generation}"),
                name=f"vision-stderr-g{generation}",
                daemon=True,
            ).start()

    def start(self) -> None:
        self._spawn()

    def _spawn(self) -> None:
        self.current_stage_offset = self._stage_offset()
        # The C++ timing scheduler needs the same confirmed-round seed as the
        # Python reducer, including after a supervised child replacement.
        self.args.vision_completed_offers = self.current_stage_offset
        process = self._process_factory(self.args)
        self.generation += 1
        self.process = process
        self.state = "HEALTHY"
        self._restart_due = None
        self._restart_reason = None
        self.heartbeat_capable = False
        self.last_heartbeat = None
        self.last_heartbeat_event_seq = None
        self._vision_invalidated_generation = None
        self._reader_starter(process, self.events, self.generation)
        self._emit(
            {
                "type": "capture_health",
                "status": "HEALTHY",
                "state": "HEALTHY",
                "reason": "process_spawned",
                "health_basis": "process_and_pipe",
                "vision_generation": self.generation,
                "heartbeat_capable": False,
                "stage_offset": self.current_stage_offset,
            }
        )

    def _invalidate_once(self, reason: str) -> None:
        if self._vision_invalidated_generation == self.generation:
            return
        self._vision_invalidated_generation = self.generation
        self._on_restart(reason)

    def _observe_heartbeat(self, line: str) -> bool:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        payload_type = payload.get("type", payload.get("event"))
        declared = payload.get("heartbeat_capable") is True
        if payload_type != "capture_health" and not declared:
            return False
        self.heartbeat_capable = True
        self.last_heartbeat = self._clock()
        event_seq = payload.get("event_seq")
        if isinstance(event_seq, int) and not isinstance(event_seq, bool):
            self.last_heartbeat_event_seq = event_seq
        if self.state == "SUSPECT":
            self.state = "HEALTHY"
            self._vision_invalidated_generation = None
        return True

    def _enter_heartbeat_suspect(self, age_seconds: float) -> None:
        if self.state != "SUSPECT":
            self.state = "SUSPECT"
            self._invalidate_once("heartbeat_suspect")
            self._emit(
                {
                    "type": "capture_health",
                    "status": "SUSPECT",
                    "state": "SUSPECT",
                    "reason": "heartbeat_silence",
                    "health_basis": "capture_health_heartbeat",
                    "vision_generation": self.generation,
                    "heartbeat_capable": True,
                    "event_seq": self.last_heartbeat_event_seq,
                    "last_heartbeat_age_ms": int(age_seconds * 1000),
                    "recommendation_freshness": "stale",
                    "recommendation_invalidated": True,
                    "recommended": None,
                    "hotkeys_enabled": False,
                }
            )

    @staticmethod
    def _terminate(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=VISION_TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=VISION_TERMINATE_GRACE_SECONDS)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            return

    def _schedule_restart(self, reason: str) -> None:
        if self._stopping or self.finished or self._restart_due is not None:
            return
        process = self.process
        if not self.live:
            return_code = process.poll() if process is not None else 0
            if return_code is None:
                self.state = "STOPPING"
                return
            self.last_exit_code = return_code
            self.finished = True
            self.state = "STOPPED"
            return

        now = self._clock()
        while (
            self._restart_attempts
            and now - self._restart_attempts[0] >= VISION_RESTART_WINDOW_SECONDS
        ):
            self._restart_attempts.popleft()
        self._invalidate_once(reason)
        if len(self._restart_attempts) >= len(VISION_RESTART_BACKOFF_SECONDS):
            self._terminate(process)
            self.process = None
            self.degraded = True
            self.finished = True
            self.state = "DEGRADED"
            self._emit(
                {
                    "type": "fallback_changed",
                    "status": "DEGRADED",
                    "state": "DEGRADED",
                    "mode": "NO_SAFE_RECOMMENDATION",
                    "reason": "vision_restart_budget_exhausted",
                    "vision_generation": self.generation,
                    "recommended": None,
                    "hotkeys_enabled": False,
                }
            )
            return

        attempt = len(self._restart_attempts) + 1
        backoff = VISION_RESTART_BACKOFF_SECONDS[attempt - 1]
        self._restart_attempts.append(now)
        self.state = "RESTARTING"
        self._restart_reason = reason
        self._restart_due = now + backoff
        from_generation = self.generation
        self._emit(
            {
                "type": "component_restart",
                "status": "RESTARTING",
                "component": "vision",
                "phase": "scheduled",
                "reason": reason,
                "from_generation": from_generation,
                "to_generation": from_generation + 1,
                "attempt": attempt,
                "backoff_ms": int(backoff * 1000),
                "recommended": None,
                "hotkeys_enabled": False,
            }
        )
        self._terminate(process)
        self.process = None

    def accept(self, event: VisionStreamEvent) -> str | None:
        if (
            event.generation != self.generation
            or self.state in {"RESTARTING", "STOPPING"}
            or self.finished
        ):
            self._emit(
                {
                    "type": "bridge_diagnostic",
                    "status": "ignored",
                    "error": {
                        "code": "old_vision_generation",
                        "message": (
                            f"ignored vision generation {event.generation}; "
                            f"current is {self.generation}"
                        ),
                    },
                }
            )
            return None
        if event.kind == "line":
            if isinstance(event.value, str):
                heartbeat_observed = self._observe_heartbeat(event.value)
                if self.state == "SUSPECT" and not heartbeat_observed:
                    self._emit(
                        {
                            "type": "bridge_diagnostic",
                            "status": "ignored",
                            "error": {
                                "code": "vision_heartbeat_suspect",
                                "message": "ignored non-heartbeat vision data while heartbeat is suspect",
                            },
                        }
                    )
                    return None
                return event.value
            return None
        if event.kind == "eof":
            self._schedule_restart("stdout_eof")
            return None
        if event.kind == "error":
            self._schedule_restart("stdout_reader_error")
            return None
        return None

    def tick(self) -> None:
        if self.finished or self._stopping:
            return
        process = self.process
        if process is not None:
            return_code = process.poll()
            if return_code is not None:
                self.last_exit_code = return_code
                self._schedule_restart("process_exit")
            elif self.live and self.heartbeat_capable and self.last_heartbeat is not None:
                age_seconds = self._clock() - self.last_heartbeat
                if age_seconds >= VISION_HEARTBEAT_RESTART_SECONDS:
                    self._enter_heartbeat_suspect(age_seconds)
                    self._schedule_restart("heartbeat_timeout")
                    # Keep the observable state RESTARTING for this tick.  A
                    # zero-backoff replacement is spawned by the next tick.
                    return
                if age_seconds >= VISION_HEARTBEAT_SUSPECT_SECONDS:
                    self._enter_heartbeat_suspect(age_seconds)
        if self._restart_due is not None and self._clock() >= self._restart_due:
            try:
                self._spawn()
            except Exception as error:
                self._restart_due = None
                self._emit(
                    {
                        "type": "component_restart",
                        "status": "failed",
                        "component": "vision",
                        "phase": "failed",
                        "reason": "spawn_failed",
                        "message": str(error),
                    }
                )
                self._schedule_restart("spawn_failed")

    def close(self) -> None:
        self._stopping = True
        self._terminate(self.process)
        self.process = None
        self.state = "STOPPED"


def _is_game_state(payload: Mapping[str, Any]) -> bool:
    return all(key in payload for key in ("champion", "offer_round", "current_offer"))


def _with_stage_offset(payload: Mapping[str, Any], offset: int) -> Mapping[str, Any]:
    """Translate a restarted vision reducer's round into the resumed game round."""

    if offset == 0:
        return payload
    adjusted = dict(payload)
    stage = payload.get("offer_round")
    if isinstance(stage, int) and not isinstance(stage, bool):
        adjusted["offer_round"] = stage + offset
    return adjusted


def _default_log_path(runtime_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return (
        runtime_root
        / "recommendation_sessions"
        / f"recommendation-{timestamp}-{os.getpid()}.jsonl"
    )


def _event_source_kind(args: argparse.Namespace) -> str:
    return "replay" if args.replay is not None else "screen_capture"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _event_source_metadata(args: argparse.Namespace) -> dict[str, Any]:
    kind = _event_source_kind(args)
    metadata: dict[str, Any] = {"kind": kind}
    if args.replay is not None:
        replay_path = Path(args.replay).resolve(strict=True)
        metadata.update(
            {
                "input_path": str(replay_path),
                "input_sha256": _sha256_file(replay_path),
                "input_size_bytes": replay_path.stat().st_size,
            }
        )
    return metadata


def _load_lcu_champ_select():
    path = WORKSPACE_ROOT / "scripts" / "phase4" / "lcu_champ_select.py"
    spec = importlib.util.spec_from_file_location("phase4_lcu_champ_select", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load LCU champ-select module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start_lcu_poller(args: argparse.Namespace, sink: EventSink) -> Any | None:
    if getattr(args, "lcu_context", "off") != "auto":
        return None
    try:
        module = _load_lcu_champ_select()
        poller = module.LcuChampSelectPoller(sink.emit)
        poller.start()
        return poller
    except Exception as error:
        sink.emit(
            {
                "type": "lcu_context_state",
                "schema_version": 1,
                "status": "UNAVAILABLE",
                "reason": "poller_start_failed",
                "context": None,
                "message": str(error)[:128],
            }
        )
        return None


def _start_sidecar(
    args: argparse.Namespace, sink: EventSink
) -> SidecarClient | None:
    if args.no_sidecar:
        return None
    try:
        sidecar = SidecarClient(
            python=args.python,
            script=args.sidecar_script,
            on_unavailable=sink.sidecar_unavailable,
            extra_args=[
                "--title",
                "LoL 识别（置顶只读）",
                "--width",
                "460",
                "--height",
                "480",
            ],
        )
    except Exception as error:
        sink.sidecar_unavailable(f"startup failed: {error}")
        return None
    sink.attach_sidecar(sidecar)
    return sidecar


class VisionEvidenceBuffer:
    """Bind one accepted frame diagnostic to the immediately following offer.

    C++ emits ``frame_result`` before the GameState generated from that same
    processor result.  Slot-ordered Augment IDs are checked at this boundary;
    a mismatch drops the diagnostic instead of attaching stale OCR evidence to
    a recommendation.
    """

    def __init__(self) -> None:
        self._pending: dict[str, Any] | None = None

    @staticmethod
    def _recognition_ids(payload: Mapping[str, Any]) -> tuple[str, str, str] | None:
        recognition = payload.get("recognition_debug")
        cards = recognition.get("cards") if isinstance(recognition, Mapping) else None
        if not isinstance(cards, list) or len(cards) != len(SLOTS):
            return None
        by_slot: dict[str, str] = {}
        for card in cards:
            if not isinstance(card, Mapping):
                return None
            slot = card.get("slot")
            augment_id = card.get("augment_id")
            if (
                slot not in SLOTS
                or slot in by_slot
                or card.get("state") != "RECOGNIZED"
                or not isinstance(augment_id, str)
                or not augment_id.strip()
            ):
                return None
            by_slot[str(slot)] = augment_id.strip()
        return tuple(by_slot[slot] for slot in SLOTS)  # type: ignore[return-value]

    @staticmethod
    def _offer_ids(payload: Mapping[str, Any]) -> tuple[str, str, str] | None:
        offer = payload.get("current_offer")
        recognitions = offer.get("recognitions") if isinstance(offer, Mapping) else None
        if not isinstance(recognitions, list) or len(recognitions) != len(SLOTS):
            return None
        by_slot: dict[str, str] = {}
        for card in recognitions:
            if not isinstance(card, Mapping):
                return None
            slot = card.get("slot")
            augment_id = card.get("augment_id")
            if (
                slot not in SLOTS
                or slot in by_slot
                or not isinstance(augment_id, str)
                or not augment_id.strip()
            ):
                return None
            by_slot[str(slot)] = augment_id.strip()
        return tuple(by_slot[slot] for slot in SLOTS)  # type: ignore[return-value]

    def observe_frame_result(
        self, payload: Mapping[str, Any], generation: int
    ) -> dict[str, Any] | None:
        if payload.get("accepted") is not True:
            return None
        augment_ids = self._recognition_ids(payload)
        if augment_ids is None:
            self._pending = None
            return None
        evidence = {
            key: payload.get(key)
            for key in (
                "captured_at_utc",
                "emitted_at_utc",
                "frames",
                "static_replay",
                "static_source_frame_id",
                "static_pass_index",
                "static_pass_count",
                "raw_detector",
                "stable_detector",
                "ocr_executed",
                "accepted",
                "force_recognition",
                "detector_stability_bypassed",
                "vision_processing_latency_ms",
                "reason",
                "rois",
                "recognition_debug",
            )
            if key in payload
        }
        evidence["vision_generation"] = generation
        evidence["augment_ids"] = list(augment_ids)
        canonical = json.dumps(
            evidence,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        evidence["evidence_id"] = hashlib.sha256(canonical).hexdigest()
        # Clone through JSON so later producer dictionaries cannot mutate the
        # durable evidence attached to an offer.
        self._pending = json.loads(json.dumps(evidence, ensure_ascii=False))
        return dict(self._pending)

    def bind_game_state(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        evidence = self._pending
        self._pending = None
        if evidence is None:
            return None
        offer_ids = self._offer_ids(payload)
        evidence_ids = evidence.get("augment_ids")
        if offer_ids is None or evidence_ids != list(offer_ids):
            return None
        evidence["binding"] = "exact_slot_ordered_augment_ids"
        return evidence


def _capture_to_recommend_latency_ms(
    evidence: Mapping[str, Any], now: datetime
) -> float | None:
    raw = evidence.get("captured_at_utc")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        captured = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if captured.tzinfo is None:
        return None
    elapsed = (now - captured.astimezone(timezone.utc)).total_seconds() * 1000.0
    if not math.isfinite(elapsed) or elapsed < 0.0:
        return None
    return round(elapsed, 3)


def _process_vision_stream_event(
    stream_event: VisionStreamEvent,
    *,
    supervisor: VisionGenerationSupervisor,
    coordinator: RecommendationCoordinator,
    sink: EventSink,
    vision_debug: bool,
    evidence_buffer: VisionEvidenceBuffer | None = None,
) -> None:
    """Apply one generation-accepted line, synchronizing health first."""

    item = supervisor.accept(stream_event)
    if not item:
        return
    if vision_debug:
        sys.stderr.write(f"[vision-json] {item}")
        sys.stderr.flush()
    try:
        payload = json.loads(item)
    except json.JSONDecodeError:
        sink.emit(
            {
                "type": "bridge_diagnostic",
                "status": "fail_closed",
                "recommended": None,
                "error": {
                    "code": "vision_invalid_json",
                    "message": "vision emitted a non-JSON line",
                },
            }
        )
        return
    if not isinstance(payload, dict):
        return
    payload_type = payload.get("type", payload.get("event"))
    if payload_type == "frame_result":
        diagnostic = dict(payload)
        diagnostic.setdefault("vision_generation", supervisor.generation)
        if evidence_buffer is not None:
            evidence_buffer.observe_frame_result(
                diagnostic, supervisor.generation
            )
        # Persist the full detector/ROI/OCR diagnostic.  The following
        # GameState is independently bound by ordered IDs before recommendation.
        sink.emit(diagnostic)
        return
    if payload_type in {"capture_health", "component_health"}:
        health_event = dict(payload)
        health_event.setdefault("vision_generation", supervisor.generation)
        invalidation = None
        component = health_event.get("component")
        if payload_type == "capture_health" or component in {"capture", "vision"}:
            # This mutation precedes every sink/UI write and every possible
            # hotkey confirmation in the tick.
            invalidation = coordinator.apply_capture_health(health_event)
        sink.emit(health_event)
        if invalidation is not None:
            invalidation["invalidation_source"] = "cpp_capture_health"
            sink.emit(invalidation)
        return
    if payload_type in {
        "live_client_state",
        "lcu_context_state",
        "mayhem_selection_state",
    }:
        context_event = dict(payload)
        context_event.setdefault("vision_generation", supervisor.generation)
        # This phase exposes the verified local API state without changing the
        # session's manually bound hero. Hero fusion is a separate fail-closed
        # state transition and must not silently overwrite --hero.
        sink.emit(context_event)
        return
    if payload_type == "selection_observed":
        observation = _bind_selection_observation_offer_identity(payload)
        observation.setdefault("vision_generation", supervisor.generation)
        for event in coordinator.handle_selection_observed(observation):
            sink.emit(event)
        return
    if _is_game_state(payload):
        evidence = (
            evidence_buffer.bind_game_state(payload)
            if evidence_buffer is not None
            else None
        )
        event = coordinator.handle_game_state(
            _with_stage_offset(payload, supervisor.current_stage_offset)
        )
        if event is not None:
            if evidence is not None:
                event["vision_evidence_id"] = evidence["evidence_id"]
                event["vision_evidence"] = evidence
                event["vision_processing_latency_ms"] = evidence.get(
                    "vision_processing_latency_ms"
                )
                event["capture_to_recommend_ms"] = (
                    _capture_to_recommend_latency_ms(
                        evidence, datetime.now(timezone.utc)
                    )
                )
            sink.emit(event)


def _drain_vision_events(
    *,
    supervisor: VisionGenerationSupervisor,
    coordinator: RecommendationCoordinator,
    sink: EventSink,
    vision_debug: bool,
    first_event: VisionStreamEvent | None = None,
    evidence_buffer: VisionEvidenceBuffer | None = None,
) -> int:
    """Drain the current FIFO in order before a hotkey can be committed."""

    drained = 0
    if first_event is not None:
        _process_vision_stream_event(
            first_event,
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=vision_debug,
            evidence_buffer=evidence_buffer,
        )
        drained += 1
    while True:
        try:
            stream_event = supervisor.events.get_nowait()
        except queue.Empty:
            return drained
        _process_vision_stream_event(
            stream_event,
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=vision_debug,
            evidence_buffer=evidence_buffer,
        )
        drained += 1


def run(args: argparse.Namespace) -> int:
    log_path = args.log_path or _default_log_path(args.runtime_root)
    worker: RecommendationWorkerClient | None = None
    supervisor: VisionGenerationSupervisor | None = None
    sidecar: SidecarClient | None = None
    lcu_poller: Any | None = None
    source_metadata = _event_source_metadata(args)
    with EventSink(
        log_path,
        source_kind=str(source_metadata["kind"]),
        source_metadata=source_metadata,
    ) as sink:
        try:
            worker = RecommendationWorkerClient(
                python=args.python,
                scrape_root=args.scrape_root,
                visual_catalog=args.visual_catalog,
                timeout_seconds=args.worker_timeout,
            )
            coordinator = RecommendationCoordinator(
                args.hero, worker, initial_owned=args.resume_owned
            )
            evidence_buffer = VisionEvidenceBuffer()
            stage_offset = len(args.resume_owned)
            hotkeys = None if args.no_hotkeys else HotkeyPoller()
            sidecar = _start_sidecar(args, sink)
            sink.emit(
                {
                    "type": "recommendation_session_start",
                    "status": "ready",
                    "hero": args.hero,
                    "snapshot_id": worker.ready.get("snapshot_id"),
                    "patch": worker.ready.get("patch"),
                    "data_date": worker.ready.get("data_date"),
                    "single_prior_games": worker.ready.get("single_prior_games"),
                    "combo_prior_games": worker.ready.get("combo_prior_games"),
                    "engine_load_count": worker.ready.get("engine_load_count"),
                    "resumed_owned_augments": list(coordinator.state.owned_augments),
                    "resumed_owned": list(coordinator.state.owned_names),
                    "vision_stage_offset": stage_offset,
                    "selection_hotkeys": (
                        None
                        if hotkeys is None
                        else {"LEFT": "Ctrl+Alt+1", "CENTER": "Ctrl+Alt+2", "RIGHT": "Ctrl+Alt+3"}
                    ),
                    "log_path": str(log_path),
                }
            )
            lcu_poller = _start_lcu_poller(args, sink)

            def preserve_and_invalidate(reason: str) -> None:
                event = coordinator.invalidate_vision(reason)
                if event is not None:
                    sink.emit(event)

            supervisor = VisionGenerationSupervisor(
                args,
                emit=sink.emit,
                on_restart=preserve_and_invalidate,
                stage_offset=lambda: len(coordinator.state.owned_augments),
            )
            supervisor.start()
            bridge_deadline = time.monotonic() + args.max_seconds
            while not supervisor.finished and time.monotonic() < bridge_deadline:
                supervisor.tick()
                if supervisor.finished:
                    break
                try:
                    stream_event = supervisor.events.get(timeout=0.03)
                except queue.Empty:
                    stream_event = None
                _drain_vision_events(
                    supervisor=supervisor,
                    coordinator=coordinator,
                    sink=sink,
                    vision_debug=args.vision_debug,
                    first_event=stream_event,
                    evidence_buffer=evidence_buffer,
                )

                # A synchronous worker can block while vision control lines
                # accumulate.  Re-check timers and drain once more around the
                # key edge so queued stale health wins the transaction.
                supervisor.tick()
                if hotkeys is not None and not supervisor.finished:
                    selected_slot = hotkeys.poll()
                    _drain_vision_events(
                        supervisor=supervisor,
                        coordinator=coordinator,
                        sink=sink,
                        vision_debug=args.vision_debug,
                        evidence_buffer=evidence_buffer,
                    )
                    supervisor.tick()
                    if selected_slot is not None:
                        offer_identity = coordinator.manual_offer_identity
                        for event in coordinator.confirm_selection(
                            selected_slot, offer_identity=offer_identity
                        ):
                            sink.emit(event)

            return_code = (
                2
                if supervisor.degraded
                else (supervisor.last_exit_code or 0)
            )
            sink.emit(
                {
                    "type": "recommendation_session_end",
                    "status": (
                        "completed"
                        if len(coordinator.state.owned_augments) == 4
                        else "incomplete"
                    ),
                    "hero": args.hero,
                    "confirmed_rounds": len(coordinator.state.owned_augments),
                    "owned_augments": list(coordinator.state.owned_augments),
                    "owned": list(coordinator.state.owned_names),
                    "snapshot_id": coordinator.snapshot_id,
                    "vision_exit_code": return_code,
                    "vision_generation": supervisor.generation,
                    "vision_health": supervisor.state,
                }
            )
            return return_code
        except KeyboardInterrupt:
            sink.emit(
                {
                    "type": "recommendation_session_end",
                    "status": "interrupted",
                    "hero": args.hero,
                }
            )
            return 130
        except Exception as error:
            sink.emit(
                {
                    "type": "recommendation_session_end",
                    "status": "fail_closed",
                    "hero": args.hero,
                    "recommended": None,
                    "error": {"code": "bridge_error", "message": str(error)},
                }
            )
            return 2
        finally:
            if lcu_poller is not None:
                lcu_poller.stop()
            if supervisor is not None:
                supervisor.close()
            if sidecar is not None:
                sidecar.close()
            if worker is not None:
                worker.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Real LoL augment recognition to recommendation bridge"
    )
    parser.add_argument("--hero", required=True, help="manual hero name, slug, or id")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--window-title")
    source.add_argument("--hwnd")
    source.add_argument("--replay", type=Path)
    parser.add_argument(
        "--capture-backend",
        choices=("auto", "wgc", "desktop"),
        default=None,
        help="live capture backend (default: auto); invalid with --replay",
    )
    parser.add_argument(
        "--lcu-context",
        choices=("auto", "off"),
        default=None,
        help="live LCU gameflow context (default: auto); invalid with --replay",
    )
    parser.add_argument("--mode", choices=("KIWI", "KIWI_JADE"), default="KIWI")
    parser.add_argument("--max-seconds", type=float, default=7200.0)
    parser.add_argument("--vision-exe", type=Path, default=DEFAULT_VISION_EXE)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--scrape-root", type=Path, default=DEFAULT_SCRAPE_ROOT)
    parser.add_argument(
        "--visual-catalog", type=Path, default=DEFAULT_VISUAL_CATALOG
    )
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--log-path", type=Path)
    parser.add_argument(
        "--sidecar-script", type=Path, default=DEFAULT_SIDECAR_SCRIPT
    )
    parser.add_argument("--worker-timeout", type=float, default=5.0)
    parser.add_argument(
        "--resume-owned",
        action="append",
        default=[],
        metavar="AUGMENT_ID=NAME",
        help="resume after a confirmed earlier choice; repeat in selection order",
    )
    parser.add_argument("--no-hotkeys", action="store_true")
    parser.add_argument("--no-sidecar", action="store_true")
    parser.add_argument("--collect-samples", action="store_true")
    parser.add_argument(
        "--no-force-recognition-hotkey", action="store_true"
    )
    parser.add_argument("--vision-debug", action="store_true")
    return parser


def _validated_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.replay is not None and args.capture_backend is not None:
        parser.error("capture-backend is live-only and cannot be used with replay")
    if args.replay is not None and args.lcu_context is not None:
        parser.error("lcu-context is live-only and cannot be used with replay")
    if args.replay is None:
        if args.capture_backend is None:
            legacy_backend = os.environ.get("LOL_ASSISTANT_CAPTURE_BACKEND")
            if legacy_backend is not None and legacy_backend not in {
                "auto",
                "wgc",
                "desktop",
            }:
                parser.error(
                    "LOL_ASSISTANT_CAPTURE_BACKEND must be auto, wgc, or desktop"
                )
            args.capture_backend = legacy_backend or "auto"
        if args.lcu_context is None:
            args.lcu_context = "auto"
    else:
        args.capture_backend = None
        args.lcu_context = "off"
    args.vision_exe = args.vision_exe.resolve()
    args.python = args.python.resolve()
    args.scrape_root = args.scrape_root.resolve()
    args.visual_catalog = args.visual_catalog.resolve()
    args.knowledge = args.knowledge.resolve()
    args.runtime_root = args.runtime_root.resolve()
    args.sidecar_script = args.sidecar_script.resolve()
    if args.replay is not None:
        args.replay = args.replay.resolve()
    if args.log_path is not None:
        args.log_path = args.log_path.resolve()
    required_files = {
        "vision executable": args.vision_exe,
        "python executable": args.python,
        "visual catalog": args.visual_catalog,
        "vision knowledge catalog": args.knowledge,
        "recommendation metadata": args.scrape_root / "data" / "metadata.json",
        "recommendation worker": args.scrape_root / "src" / "recommendation_worker.py",
    }
    if not args.no_sidecar:
        required_files["sidecar script"] = args.sidecar_script
    for label, path in required_files.items():
        if not path.is_file():
            parser.error(f"{label} not found: {path}")
    if args.replay is not None and not args.replay.exists():
        parser.error(f"replay source not found: {args.replay}")
    if not (0.0 < args.max_seconds <= 86400.0):
        parser.error("max-seconds must be in (0, 86400]")
    if not (0.1 <= args.worker_timeout <= 60.0):
        parser.error("worker-timeout must be in [0.1, 60]")
    parsed_owned: list[tuple[str, str]] = []
    for item in args.resume_owned:
        augment_id, separator, name = item.partition("=")
        augment_id = augment_id.strip()
        name = name.strip() if separator else augment_id
        if (
            not augment_id
            or augment_id.casefold() == "unknown"
            or not name
        ):
            parser.error("resume-owned must be AUGMENT_ID or AUGMENT_ID=NAME")
        parsed_owned.append((augment_id, name))
    if len(parsed_owned) > 3 or len({item[0] for item in parsed_owned}) != len(parsed_owned):
        parser.error("resume-owned must contain 0..3 unique augment ids")
    args.resume_owned = parsed_owned
    return args


def main(argv: Sequence[str] | None = None) -> int:
    return run(_validated_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
