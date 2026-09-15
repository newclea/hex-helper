from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.phase3 import realtime_recommendation as bridge


SNAPSHOT_ID = "16.16-20260823-255fffa635da"
OFFER_IDS = [
    ["ARAM_ADAPt", "ARAM_AllForYou", "ARAM_ApexInventor"],
    ["ARAM_Archmage", "ARAM_BacktoBasics", "ARAM_BangBang"],
    ["ARAM_BigBrain", "ARAM_BladeWaltz", "ARAM_BluntForce"],
    ["ARAM_BladeWaltz", "ARAM_BreadAndButter", "ARAM_BreadAndCheese"],
]


def game_state(
    stage: int,
    augment_ids: list[str],
    *,
    hero: str = "阿狸",
    states: list[str] | None = None,
) -> dict[str, Any]:
    states = states or ["RECOGNIZED", "RECOGNIZED", "RECOGNIZED"]
    return {
        "champion": hero,
        "offer_round": stage,
        "selected_augments": [],
        "current_offer": {
            "recognitions": [
                {
                    "state": states[index],
                    "slot": bridge.SLOTS[index],
                    "augment_id": augment_id,
                    "display_name": f"name:{augment_id}",
                }
                for index, augment_id in enumerate(augment_ids)
            ]
        },
    }


def selection_observed(
    offer_id: str,
    selected_slot: str,
    selected_augment_id: str,
    *,
    confidence: float = 0.99,
    source: str = "synthetic_unit_screen_observer",
) -> dict[str, Any]:
    # Synthetic contract fixture: unit tests only, never presented as live evidence.
    return {
        "type": "selection_observed",
        "offer_id": offer_id,
        "selected_slot": selected_slot,
        "selected_augment_id": selected_augment_id,
        "confidence": confidence,
        "source": source,
    }


def hud_selection_observed(
    stage: int,
    augment_ids: list[str],
    selected_slot: str,
    *,
    score: float = 0.859375,
    margin: float = 0.09375,
    stable_frames: int = 2,
) -> dict[str, Any]:
    slot_index = bridge.SLOTS.index(selected_slot)
    return {
        "type": "selection_observed",
        "schema_version": 1,
        "offer_stage": stage,
        "offer_augment_ids": list(augment_ids),
        "selected_slot": selected_slot,
        "selected_augment_id": augment_ids[slot_index],
        "confidence": score,
        "top1_score": score,
        "top1_margin": margin,
        "stable_frames": stable_frames,
        "candidate_scope": "pending_offer",
        "hud_slot_index": stage - 1,
        "frame_id": 42,
        "source": "hud_icon_template",
    }


class FakeWorker:
    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior
        self.calls: list[dict[str, Any]] = []
        self._ready = {
            "type": "worker_ready",
            "ok": True,
            "snapshot_id": SNAPSHOT_ID,
            "patch": "16.16",
            "data_date": "2026-08-23",
            "single_prior_games": 5000,
            "combo_prior_games": 2000,
            "engine_load_count": 1,
        }

    @property
    def ready(self) -> Mapping[str, Any]:
        return self._ready

    def recommend(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(dict(request))
        if self.behavior == "exception":
            raise RuntimeError("injected worker failure")
        choices = list(request["choices"])
        names = [f"engine:{value}" for value in choices]
        soft_conflict_indexes = {
            "knowledge_conflict": {2},
            "two_knowledge_conflicts": {1, 2},
            "knowledge_conflict_missing_entry": {2},
            "knowledge_conflict_bad_recommendation": {2},
            "knowledge_conflict_wrong_id": {2},
            "all_conflicts_ok": {0, 1, 2},
        }.get(self.behavior, set())
        ranking = [
            {
                "augment": name,
                "rank": index + 1,
                "score": 60.0 - index,
                "confidence": 0.9 - index * 0.1,
                "stage_invalid": (
                    (self.behavior == "stage_invalid" and index == 0)
                    or index in soft_conflict_indexes
                ),
                "debug": {
                    "hero_data": {"sample_size": 10000},
                    "combo_data": [],
                    "inferred_data": [],
                    "stage_invalid": (
                        (self.behavior == "stage_invalid" and index == 0)
                        or index in soft_conflict_indexes
                    ),
                    "stage_source": "hero",
                    "available_stages": [2, 3, 4],
                    "stage_agnostic": False,
                },
            }
            for index, name in enumerate(names)
        ]
        if soft_conflict_indexes:
            conflict_indexes = {
                ranking_index: conflict_index
                for conflict_index, ranking_index in enumerate(
                    sorted(soft_conflict_indexes)
                )
            }
            for ranking_index, item in enumerate(ranking):
                item["recommendation_eligible"] = ranking_index not in soft_conflict_indexes
                item["eligibility_reason"] = (
                    "knowledge_conflict"
                    if ranking_index in soft_conflict_indexes
                    else "eligible"
                )
                item["knowledge_conflict_index"] = conflict_indexes.get(ranking_index)

        valid_names = [
            item["augment"] for item in ranking if not item["stage_invalid"]
        ]
        recommended = valid_names[0] if valid_names else None
        if self.behavior == "stage_invalid":
            recommended = None
        elif self.behavior == "knowledge_conflict_bad_recommendation":
            recommended = ranking[2]["augment"]
        result = {
            "hero": "阿狸",
            "owned_augments": [f"engine:{value}" for value in request["owned_augments"]],
            "choices": names,
            "stage": request["stage"],
            "recommended": recommended,
            "ranking": ranking,
        }
        response: dict[str, Any] = {
            "type": "recommendation_response",
            "request_id": request["request_id"],
            "ok": self.behavior != "stage_invalid",
            "snapshot_id": SNAPSHOT_ID,
            "engine_latency_ms": 0.05,
            "choice_mapping": [
                {
                    "augment_id": value,
                    "numeric_id": 1000 + index,
                    "display_name": names[index],
                }
                for index, value in enumerate(choices)
            ],
            "result": result,
        }
        if soft_conflict_indexes and self.behavior != "knowledge_conflict_missing_entry":
            conflicts = []
            for ranking_index in sorted(soft_conflict_indexes):
                item = ranking[ranking_index]
                conflicts.append(
                    {
                        "code": "snapshot_available_stages_conflict",
                        "augment_id": choices[ranking_index],
                        "augment": item["augment"],
                        "observed_stage": request["stage"],
                        "offer_evidence": "vision_accepted",
                        "snapshot_stage_source": "hero",
                        "snapshot_available_stages": [2, 3, 4],
                        "snapshot_stage_agnostic": False,
                        "original_stage_invalid": True,
                        "recommendation_eligible": False,
                        "score_adjustment_pp": 0.0,
                        "debug_path": f"/result/ranking/{ranking_index}/debug",
                    }
                )
            if self.behavior == "knowledge_conflict_wrong_id":
                conflicts[0]["augment_id"] = "not-in-the-offer"
            response["knowledge_conflicts"] = conflicts
        if self.behavior == "stage_invalid":
            response.update(
                {
                    "recommended": None,
                    "error": {"code": "stage_invalid", "message": "bad stage"},
                }
            )
        return response


class RecordingSidecar:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def offer(self, line: str) -> None:
        self.lines.append(line)


class RecommendationCoordinatorTests(unittest.TestCase):
    def test_four_round_contract_state_and_owned_combo_context(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        selected_ids: list[str] = []
        for stage, ids in enumerate(OFFER_IDS, 1):
            event = coordinator.handle_game_state(game_state(stage, ids))
            self.assertIsNotNone(event)
            assert event is not None
            self.assertEqual(event["status"], "recommended")
            self.assertEqual(event["stage"], stage)
            self.assertEqual(event["snapshot_id"], SNAPSHOT_ID)
            self.assertEqual(len(event["ranking"]), 3)
            self.assertNotIn("knowledge_conflicts", event)
            self.assertLess(event["bridge_latency_ms"], 1000.0)
            self.assertEqual(
                worker.calls[-1]["owned_augments"],
                selected_ids,
            )
            self.assertTrue(worker.calls[-1]["debug"])
            self.assertEqual(
                worker.calls[-1]["offer_evidence"], "vision_accepted"
            )
            selected_slot = (stage - 1) % 3 + 1
            selected_ids.append(ids[selected_slot - 1])
            confirmations = coordinator.confirm_selection(selected_slot)
            self.assertEqual(confirmations[0]["status"], "choice_confirmed")
            self.assertEqual(confirmations[0]["owned_augments"], selected_ids)
        self.assertEqual(len(worker.calls), 4)
        self.assertEqual(confirmations[-1]["type"], "recommendation_session_complete")
        self.assertEqual(confirmations[-1]["stages_completed"], 4)

    def test_same_offer_is_recommended_only_once(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        payload = game_state(1, OFFER_IDS[0])
        self.assertIsNotNone(coordinator.handle_game_state(payload))
        duplicate = coordinator.handle_game_state(payload)
        self.assertEqual(duplicate["type"], "bridge_diagnostic")
        self.assertEqual(duplicate["error"]["code"], "current_offer_duplicate")
        self.assertIsNone(coordinator.handle_game_state(payload))
        self.assertEqual(len(worker.calls), 1)

    def test_unknown_ocr_fails_closed_without_worker_call(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        payload = game_state(
            1,
            ["UNKNOWN", OFFER_IDS[0][1], OFFER_IDS[0][2]],
            states=["UNKNOWN", "RECOGNIZED", "RECOGNIZED"],
        )
        event = coordinator.handle_game_state(payload)
        self.assertEqual(event["status"], "fail_closed")
        self.assertIsNone(event["recommended"])
        self.assertEqual(event["error"]["code"], "unknown_ocr")
        self.assertEqual(worker.calls, [])

    def test_unconfirmed_previous_offer_and_bad_stage_fail_closed(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        self.assertEqual(
            coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))["status"],
            "recommended",
        )
        event = coordinator.handle_game_state(game_state(2, OFFER_IDS[1]))
        self.assertEqual(event["status"], "fail_closed")
        self.assertEqual(event["error"]["code"], "selection_unconfirmed")
        self.assertEqual(len(worker.calls), 1)

        second = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        event = second.handle_game_state(game_state(2, OFFER_IDS[1]))
        self.assertEqual(event["error"]["code"], "stage_sequence_invalid")

    def test_stage_invalid_and_worker_exception_fail_closed(self) -> None:
        invalid = bridge.RecommendationCoordinator("阿狸", FakeWorker("stage_invalid"))
        event = invalid.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "fail_closed")
        self.assertEqual(event["error"]["code"], "stage_invalid")
        self.assertIsNone(event["recommended"])
        self.assertEqual(len(event["ranking"]), 3)
        self.assertFalse(event["hotkeys_enabled"])
        self.assertFalse(event["manual_confirmation_available"])
        self.assertIsNone(invalid.manual_offer_identity)
        manual = invalid.confirm_selection(
            1, offer_identity=invalid.active_offer_identity
        )[0]
        self.assertEqual(manual["status"], "ignored")
        self.assertEqual(manual["reason"], "manual_confirmation_unavailable")
        # Engine failure must not discard exact independent screen evidence.
        automatic = invalid.handle_selection_observed(
            selection_observed(
                invalid.active_offer_identity,
                "LEFT",
                OFFER_IDS[0][0],
            )
        )[0]
        self.assertEqual(automatic["status"], "choice_confirmed")

        broken = bridge.RecommendationCoordinator("阿狸", FakeWorker("exception"))
        event = broken.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "fail_closed")
        self.assertEqual(event["error"]["code"], "worker_ipc_error")
        self.assertIsNone(event["recommended"])

    def test_soft_knowledge_conflict_is_validated_and_propagated(self) -> None:
        worker = FakeWorker("knowledge_conflict")
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        event = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "recommended_with_knowledge_conflict")
        self.assertEqual(worker.calls[0]["offer_evidence"], "vision_accepted")
        self.assertEqual(len(event["knowledge_conflicts"]), 1)
        conflicts = [item for item in event["ranking"] if item["stage_invalid"]]
        self.assertEqual(len(conflicts), 1)
        self.assertFalse(conflicts[0]["recommendation_eligible"])
        self.assertNotEqual(event["recommended"], conflicts[0]["augment"])

    def test_two_knowledge_conflicts_recommend_only_the_valid_candidate(self) -> None:
        coordinator = bridge.RecommendationCoordinator(
            "阿狸", FakeWorker("two_knowledge_conflicts")
        )
        event = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "recommended_with_knowledge_conflict")
        eligible = [
            item for item in event["ranking"] if item["recommendation_eligible"]
        ]
        self.assertEqual(len(eligible), 1)
        self.assertEqual(event["recommended"], eligible[0]["augment"])
        self.assertEqual(len(event["knowledge_conflicts"]), 2)

    def test_soft_conflict_missing_evidence_entry_fails_closed(self) -> None:
        coordinator = bridge.RecommendationCoordinator(
            "阿狸", FakeWorker("knowledge_conflict_missing_entry")
        )
        event = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "fail_closed")
        self.assertEqual(event["error"]["code"], "unsafe_engine_result")
        self.assertIsNone(event["recommended"])

    def test_soft_conflict_cannot_recommend_ineligible_candidate(self) -> None:
        coordinator = bridge.RecommendationCoordinator(
            "阿狸", FakeWorker("knowledge_conflict_bad_recommendation")
        )
        event = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "fail_closed")
        self.assertEqual(event["error"]["code"], "unsafe_engine_result")
        self.assertIsNone(event["recommended"])

    def test_soft_conflict_must_bind_to_an_offer_id(self) -> None:
        coordinator = bridge.RecommendationCoordinator(
            "阿狸", FakeWorker("knowledge_conflict_wrong_id")
        )
        event = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "fail_closed")
        self.assertEqual(event["error"]["code"], "unsafe_engine_result")
        self.assertIsNone(event["recommended"])

    def test_success_response_with_all_candidates_ineligible_fails_closed(self) -> None:
        coordinator = bridge.RecommendationCoordinator(
            "阿狸", FakeWorker("all_conflicts_ok")
        )
        event = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        self.assertEqual(event["status"], "fail_closed")
        self.assertEqual(event["error"]["code"], "unsafe_engine_result")
        self.assertIsNone(event["recommended"])

    def test_selection_without_offer_is_ignored(self) -> None:
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        event = coordinator.confirm_selection(1)[0]
        self.assertEqual(event["status"], "ignored")
        self.assertEqual(event["reason"], "no_pending_offer")

    def test_selection_observed_stream_confirms_exact_visual_card_not_recommendation(
        self,
    ) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator("阿狸", worker)
        recommendation = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        payload = selection_observed(
            recommendation["offer_id"], "CENTER", OFFER_IDS[0][1]
        )
        payload["fingerprint"] = payload.pop("offer_id")
        supervisor = mock.Mock()
        supervisor.generation = 4
        supervisor.accept.return_value = json.dumps(payload) + "\n"
        sink = mock.Mock()

        bridge._process_vision_stream_event(
            bridge.VisionStreamEvent(4, "line", json.dumps(payload) + "\n"),
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=False,
        )

        confirmation = sink.emit.call_args.args[0]
        self.assertEqual(confirmation["type"], "choice_confirmation")
        self.assertEqual(confirmation["status"], "choice_confirmed")
        self.assertEqual(confirmation["confirmation_basis"], "selection_observed")
        self.assertEqual(confirmation["selected_slot"], "CENTER")
        self.assertEqual(confirmation["selected_augment_id"], OFFER_IDS[0][1])
        self.assertNotEqual(confirmation["selected"], recommendation["recommended"])
        self.assertEqual(coordinator.state.owned_augments, [OFFER_IDS[0][1]])
        self.assertEqual(confirmation["selection_observation"]["vision_generation"], 4)
        self.assertTrue(confirmation["evidence_retained"])

    def test_cpp_hud_selection_derives_exact_offer_identity_and_updates_owned(
        self,
    ) -> None:
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        recommendation = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        payload = hud_selection_observed(1, OFFER_IDS[0], "RIGHT")
        supervisor = mock.Mock()
        supervisor.generation = 7
        supervisor.accept.return_value = json.dumps(payload) + "\n"
        sink = mock.Mock()

        bridge._process_vision_stream_event(
            bridge.VisionStreamEvent(7, "line", json.dumps(payload) + "\n"),
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=False,
        )

        confirmation = sink.emit.call_args.args[0]
        self.assertEqual(confirmation["status"], "choice_confirmed")
        self.assertEqual(confirmation["offer_id"], recommendation["offer_id"])
        self.assertEqual(confirmation["selected_slot"], "RIGHT")
        self.assertEqual(
            confirmation["selected_augment_id"], OFFER_IDS[0][2]
        )
        self.assertEqual(coordinator.state.owned_augments, [OFFER_IDS[0][2]])
        evidence = confirmation["selection_observation"]
        self.assertEqual(evidence["offer_stage"], 1)
        self.assertEqual(evidence["offer_augment_ids"], OFFER_IDS[0])
        self.assertEqual(evidence["stable_frames"], 2)

    def test_cpp_hud_selection_identity_and_structured_evidence_fail_closed(
        self,
    ) -> None:
        cases: list[tuple[str, dict[str, Any], str]] = []

        malformed_ids = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        malformed_ids["offer_augment_ids"] = OFFER_IDS[0][:2]
        cases.append(
            ("malformed_ids", malformed_ids, "unknown_selection_observation")
        )

        conflicting_identity = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        conflicting_identity["offer_id"] = "stale-offer"
        cases.append(
            (
                "conflicting_identity",
                conflicting_identity,
                "conflicting_selection_observation",
            )
        )

        low_margin = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        low_margin["top1_margin"] = bridge.HUD_SELECTION_MIN_TOP1_MARGIN - 0.01
        cases.append(
            ("low_margin", low_margin, "invalid_hud_selection_evidence")
        )

        one_frame = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        one_frame["stable_frames"] = 1
        cases.append(
            ("one_frame", one_frame, "invalid_hud_selection_evidence")
        )

        wrong_owned_slot = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        wrong_owned_slot["hud_slot_index"] = 1
        cases.append(
            ("wrong_owned_slot", wrong_owned_slot, "hud_slot_stage_mismatch")
        )

        missing_schema = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        missing_schema.pop("schema_version")
        cases.append(
            ("missing_schema", missing_schema, "unsupported_selection_schema")
        )
        future_schema = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        future_schema["schema_version"] = 2
        cases.append(
            ("future_schema", future_schema, "unsupported_selection_schema")
        )
        boolean_schema = hud_selection_observed(1, OFFER_IDS[0], "LEFT")
        boolean_schema["schema_version"] = True
        cases.append(
            ("boolean_schema", boolean_schema, "unsupported_selection_schema")
        )

        for label, payload, expected_code in cases:
            with self.subTest(label=label):
                coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
                coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
                normalized = bridge._bind_selection_observation_offer_identity(
                    payload
                )
                rejected = coordinator.handle_selection_observed(normalized)[0]
                self.assertEqual(rejected["status"], "fail_closed")
                self.assertEqual(rejected["error"]["code"], expected_code)
                self.assertEqual(coordinator.state.owned_augments, [])

    def test_selection_observed_duplicate_is_idempotent_and_conflict_fails_closed(
        self,
    ) -> None:
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        recommendation = coordinator.handle_game_state(game_state(1, OFFER_IDS[0]))
        observation = selection_observed(
            recommendation["offer_id"], "LEFT", OFFER_IDS[0][0]
        )
        confirmed = coordinator.handle_selection_observed(observation)[0]
        revision = coordinator.state.state_revision

        duplicate = coordinator.handle_selection_observed(observation)[0]
        self.assertEqual(confirmed["status"], "choice_confirmed")
        self.assertEqual(duplicate["status"], "ignored")
        self.assertEqual(duplicate["reason"], "duplicate_selection_observation")
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(coordinator.state.owned_augments, [OFFER_IDS[0][0]])
        self.assertEqual(coordinator.state.state_revision, revision)

        conflict = coordinator.handle_selection_observed(
            selection_observed(
                recommendation["offer_id"], "RIGHT", OFFER_IDS[0][2]
            )
        )[0]
        self.assertEqual(conflict["status"], "fail_closed")
        self.assertEqual(
            conflict["error"]["code"], "conflicting_selection_observation"
        )
        self.assertEqual(conflict["confirmed_selection"]["selected_slot"], "LEFT")
        self.assertEqual(coordinator.state.owned_augments, [OFFER_IDS[0][0]])

    def test_selection_observed_wrong_unknown_pair_conflict_and_low_confidence_fail_closed(
        self,
    ) -> None:
        cases = [
            (
                "wrong_offer",
                selection_observed("stale-offer", "LEFT", OFFER_IDS[0][0]),
                "stale_selection_observation",
            ),
            (
                "unknown",
                selection_observed("pending", "LEFT", "UNKNOWN"),
                "unknown_selection_observation",
            ),
            (
                "slot_augment_conflict",
                selection_observed("pending", "LEFT", OFFER_IDS[0][1]),
                "conflicting_selection_observation",
            ),
            (
                "low_confidence",
                selection_observed(
                    "pending",
                    "LEFT",
                    OFFER_IDS[0][0],
                    confidence=bridge.SELECTION_OBSERVATION_MIN_CONFIDENCE - 0.01,
                ),
                "low_selection_confidence",
            ),
            (
                "recommendation_is_not_evidence",
                selection_observed(
                    "pending",
                    "LEFT",
                    OFFER_IDS[0][0],
                    source="recommendation",
                ),
                "invalid_selection_source",
            ),
        ]
        for label, observation, expected_code in cases:
            with self.subTest(label=label):
                coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
                recommendation = coordinator.handle_game_state(
                    game_state(1, OFFER_IDS[0])
                )
                if observation["offer_id"] == "pending":
                    observation["offer_id"] = recommendation["offer_id"]

                rejected = coordinator.handle_selection_observed(observation)[0]

                self.assertEqual(rejected["status"], "fail_closed")
                self.assertEqual(rejected["error"]["code"], expected_code)
                self.assertTrue(rejected["evidence_retained"])
                self.assertEqual(
                    rejected["selection_observation"]["source"],
                    observation["source"],
                )
                self.assertEqual(coordinator.state.owned_augments, [])
                self.assertEqual(
                    coordinator.state.current_offer.fingerprint,
                    recommendation["offer_id"],
                )

        temp_root = bridge.WORKSPACE_ROOT / "outputs" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="selection-evidence-", dir=temp_root
        ) as directory:
            path = Path(directory) / "selection-events.jsonl"
            with bridge.EventSink(path, stdout=io.StringIO()) as sink:
                sink.emit(rejected)
            persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(persisted["evidence_retained"])
        self.assertEqual(
            persisted["selection_observation"]["source"], "recommendation"
        )
        self.assertEqual(persisted["source"], {"kind": "synthetic"})

    def test_resume_owned_offsets_vision_stage_and_preserves_combo_context(self) -> None:
        worker = FakeWorker()
        coordinator = bridge.RecommendationCoordinator(
            "阿狸", worker, initial_owned=[("ARAM_ADAPt", "物理转魔法")]
        )
        restarted_vision = game_state(1, OFFER_IDS[1])
        adjusted = bridge._with_stage_offset(restarted_vision, 1)
        event = coordinator.handle_game_state(adjusted)
        self.assertEqual(event["status"], "recommended")
        self.assertEqual(event["stage"], 2)
        self.assertEqual(worker.calls[-1]["owned_augments"], ["ARAM_ADAPt"])
        self.assertEqual(event["owned"], ["engine:ARAM_ADAPt"])

    def test_event_sink_is_utf8_jsonl(self) -> None:
        temp_root = bridge.WORKSPACE_ROOT / "outputs" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            path = Path(directory) / "events.jsonl"
            with bridge.EventSink(path, stdout) as sink:
                sink.emit({"type": "test", "hero": "阿狸"})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["hero"], "阿狸")
            self.assertEqual(payload["source"], {"kind": "synthetic"})
            self.assertEqual(json.loads(stdout.getvalue())["type"], "test")

    def test_event_sink_forwards_exact_persisted_line_with_live_or_replay_source(
        self,
    ) -> None:
        temp_root = bridge.WORKSPACE_ROOT / "outputs" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            for source_kind in ("screen_capture", "replay"):
                with self.subTest(source_kind=source_kind):
                    stdout = io.StringIO()
                    sidecar = RecordingSidecar()
                    path = Path(directory) / f"{source_kind}.jsonl"
                    with bridge.EventSink(
                        path, stdout, source_kind=source_kind
                    ) as sink:
                        sink.attach_sidecar(sidecar)  # type: ignore[arg-type]
                        sink.emit({"type": "test", "hero": "阿狸"})
                    durable_line = path.read_text(encoding="utf-8").rstrip("\n")
                    self.assertEqual(sidecar.lines, [durable_line])
                    self.assertEqual(stdout.getvalue().rstrip("\n"), durable_line)
                    self.assertEqual(
                        json.loads(durable_line)["source"], {"kind": source_kind}
                    )

    def test_replay_source_metadata_binds_path_hash_and_size(self) -> None:
        temp_root = bridge.WORKSPACE_ROOT / "outputs" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            replay = Path(directory) / "offer.png"
            replay.write_bytes(b"real-replay-bytes")
            metadata = bridge._event_source_metadata(
                argparse.Namespace(replay=replay)
            )
            self.assertEqual(metadata["kind"], "replay")
            self.assertEqual(metadata["input_path"], str(replay.resolve()))
            self.assertEqual(metadata["input_size_bytes"], replay.stat().st_size)
            self.assertEqual(
                metadata["input_sha256"],
                hashlib.sha256(replay.read_bytes()).hexdigest(),
            )

            path = Path(directory) / "events.jsonl"
            with bridge.EventSink(
                path,
                stdout=io.StringIO(),
                source_kind="replay",
                source_metadata=metadata,
            ) as sink:
                sink.emit({"type": "test"})
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["source"], metadata)
            self.assertTrue(persisted["bridge_emitted_at_utc"].endswith("Z"))

    def test_accepted_frame_evidence_binds_exact_offer_and_is_persisted(self) -> None:
        ids = OFFER_IDS[0]
        frame_result = {
            "type": "frame_result",
            "captured_at_utc": "2026-08-30T09:16:52.000000Z",
            "emitted_at_utc": "2026-08-30T09:16:52.100000Z",
            "frames": [{"frame_id": 77, "width": 2560, "height": 1440}],
            "static_replay": True,
            "raw_detector": {"visible": True, "confidence": 0.99},
            "stable_detector": {"visible": True, "confidence": 0.99},
            "ocr_executed": True,
            "accepted": True,
            "vision_processing_latency_ms": 81.25,
            "reason": "accepted",
            "rois": {"offer_region": {"x": 256, "y": 144}},
            "recognition_debug": {
                "cards": [
                    {
                        "slot": slot,
                        "state": "RECOGNIZED",
                        "raw_text": f"raw-{slot}",
                        "normalized_text": f"norm-{slot}",
                        "match_kind": "NORMALIZED",
                        "match_confidence": 1.0,
                        "match_top2_score": 0.0,
                        "match_margin": 1.0,
                        "augment_id": augment_id,
                    }
                    for slot, augment_id in zip(bridge.SLOTS, ids)
                ]
            },
        }
        evidence = bridge.VisionEvidenceBuffer()
        coordinator = bridge.RecommendationCoordinator("阿狸", FakeWorker())
        supervisor = mock.Mock()
        supervisor.generation = 3
        supervisor.current_stage_offset = 0
        sink = mock.Mock()

        frame_line = json.dumps(frame_result, ensure_ascii=False) + "\n"
        supervisor.accept.return_value = frame_line
        bridge._process_vision_stream_event(
            bridge.VisionStreamEvent(3, "line", frame_line),
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=False,
            evidence_buffer=evidence,
        )
        self.assertEqual(sink.emit.call_args.args[0]["type"], "frame_result")

        offer = game_state(1, ids)
        offer_line = json.dumps(offer, ensure_ascii=False) + "\n"
        supervisor.accept.return_value = offer_line
        bridge._process_vision_stream_event(
            bridge.VisionStreamEvent(3, "line", offer_line),
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=False,
            evidence_buffer=evidence,
        )
        recommendation = sink.emit.call_args.args[0]
        self.assertEqual(recommendation["type"], "recommendation")
        self.assertEqual(
            recommendation["vision_evidence"]["binding"],
            "exact_slot_ordered_augment_ids",
        )
        self.assertEqual(
            recommendation["vision_evidence"]["augment_ids"], ids
        )
        self.assertEqual(
            recommendation["vision_evidence_id"],
            recommendation["vision_evidence"]["evidence_id"],
        )
        self.assertEqual(recommendation["vision_processing_latency_ms"], 81.25)

    def test_frame_evidence_mismatch_is_dropped(self) -> None:
        buffer = bridge.VisionEvidenceBuffer()
        accepted = {
            "type": "frame_result",
            "accepted": True,
            "recognition_debug": {
                "cards": [
                    {
                        "slot": slot,
                        "state": "RECOGNIZED",
                        "augment_id": augment_id,
                    }
                    for slot, augment_id in zip(bridge.SLOTS, OFFER_IDS[0])
                ]
            },
        }
        self.assertIsNotNone(buffer.observe_frame_result(accepted, 1))
        self.assertIsNone(buffer.bind_game_state(game_state(1, OFFER_IDS[1])))

    def test_vision_command_never_preselects_a_card(self) -> None:
        args = argparse.Namespace(
            vision_exe=Path("vision.exe"),
            replay=None,
            hwnd=None,
            window_title=bridge.DEFAULT_WINDOW_TITLE,
            hero="阿狸",
            mode="KIWI",
            knowledge=Path("catalog.json"),
            runtime_root=Path("runtime"),
            max_seconds=60.0,
            collect_samples=False,
            no_force_recognition_hotkey=False,
            capture_backend="auto",
        )
        command = bridge._vision_command(args)
        self.assertNotIn("--selected", command)
        self.assertIn("--window-title", command)
        self.assertEqual(command[command.index("--capture-backend") + 1], "auto")
        self.assertEqual(command[command.index("--lcu-context") + 1], "auto")
        self.assertEqual(command[command.index("--completed-offers") + 1], "0")
        self.assertEqual(command[command.index("--champion") + 1], "阿狸")

    def test_vision_command_forwards_collection_and_force_switch_only_live(
        self,
    ) -> None:
        base = {
            "vision_exe": Path("vision.exe"),
            "hwnd": None,
            "window_title": bridge.DEFAULT_WINDOW_TITLE,
            "hero": "阿狸",
            "mode": "KIWI",
            "knowledge": Path("catalog.json"),
            "runtime_root": Path("runtime"),
            "max_seconds": 60.0,
            "collect_samples": True,
            "no_force_recognition_hotkey": True,
            "capture_backend": "desktop",
            "lcu_context": "off",
        }
        live = bridge._vision_command(argparse.Namespace(replay=None, **base))
        self.assertIn("--collect-samples", live)
        self.assertIn("--no-force-recognition-hotkey", live)
        self.assertEqual(live[live.index("--capture-backend") + 1], "desktop")
        self.assertEqual(live[live.index("--lcu-context") + 1], "off")
        self.assertEqual(live[live.index("--completed-offers") + 1], "0")

        replay = bridge._vision_command(
            argparse.Namespace(replay=Path("offer.png"), **base)
        )
        self.assertNotIn("--collect-samples", replay)
        self.assertNotIn("--force-recognition-hotkey", replay)
        self.assertNotIn("--no-force-recognition-hotkey", replay)
        self.assertNotIn("--capture-backend", replay)
        self.assertNotIn("--lcu-context", replay)
        self.assertNotIn("--completed-offers", replay)

    def test_bridge_rejects_explicit_live_backend_for_replay(self) -> None:
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            bridge._validated_args(
                [
                    "--hero",
                    "阿狸",
                    "--replay",
                    str(Path(__file__).resolve()),
                    "--capture-backend",
                    "desktop",
                ]
            )

        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            bridge._validated_args(
                [
                    "--hero",
                    "阿狸",
                    "--replay",
                    str(Path(__file__).resolve()),
                    "--lcu-context",
                    "off",
                ]
            )

    def test_no_sidecar_skips_start_and_startup_failure_is_diagnostic_once(
        self,
    ) -> None:
        temp_root = bridge.WORKSPACE_ROOT / "outputs" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            no_sidecar_args = argparse.Namespace(
                no_sidecar=True,
                python=Path(sys.executable),
                sidecar_script=bridge.DEFAULT_SIDECAR_SCRIPT,
            )
            no_sidecar_path = Path(directory) / "disabled.jsonl"
            with bridge.EventSink(
                no_sidecar_path, io.StringIO(), source_kind="screen_capture"
            ) as sink, mock.patch.object(bridge, "SidecarClient") as client:
                self.assertIsNone(bridge._start_sidecar(no_sidecar_args, sink))
                client.assert_not_called()
            self.assertEqual(no_sidecar_path.read_text(encoding="utf-8"), "")

            enabled_args = argparse.Namespace(
                no_sidecar=False,
                python=Path(sys.executable),
                sidecar_script=bridge.DEFAULT_SIDECAR_SCRIPT,
            )
            failed_path = Path(directory) / "failed.jsonl"
            with bridge.EventSink(
                failed_path, io.StringIO(), source_kind="screen_capture"
            ) as sink, mock.patch.object(
                bridge, "SidecarClient", side_effect=OSError("injected startup")
            ):
                self.assertIsNone(bridge._start_sidecar(enabled_args, sink))
                sink.sidecar_unavailable("second report must be suppressed")
            events = [
                json.loads(line)
                for line in failed_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["error"]["code"], "sidecar_unavailable")
            self.assertEqual(events[0]["source"], {"kind": "screen_capture"})

    def test_live_client_state_is_forwarded_without_rebinding_manual_hero(
        self,
    ) -> None:
        payload = {
            "type": "live_client_state",
            "schema_version": 1,
            "status": "READY",
            "player": {
                "championName": "AurelionSol",
                "level": 11,
                "isDead": True,
                "respawnTimer": 8.5,
                "currentHealth": 0.0,
                "maxHealth": 2038.0,
                "healthPercent": 0.0,
            },
        }
        supervisor = mock.Mock()
        supervisor.generation = 7
        supervisor.accept.return_value = json.dumps(payload) + "\n"
        coordinator = mock.Mock()
        sink = mock.Mock()

        bridge._process_vision_stream_event(
            bridge.VisionStreamEvent(7, "line", json.dumps(payload) + "\n"),
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=False,
        )

        emitted = sink.emit.call_args.args[0]
        self.assertEqual(emitted["player"], payload["player"])
        self.assertEqual(emitted["vision_generation"], 7)
        coordinator.apply_capture_health.assert_not_called()
        coordinator.handle_game_state.assert_not_called()

    def test_mayhem_selection_state_is_forwarded_to_runtime_output(self) -> None:
        payload = {
            "type": "mayhem_selection_state",
            "schema_version": 1,
            "status": "DEATH_TRIGGERED",
            "phase": "TRIGGERED",
            "stage": 2,
            "thresholdLevel": 7,
            "highFrequencyActive": True,
        }
        supervisor = mock.Mock()
        supervisor.generation = 9
        supervisor.accept.return_value = json.dumps(payload) + "\n"
        coordinator = mock.Mock()
        sink = mock.Mock()

        bridge._process_vision_stream_event(
            bridge.VisionStreamEvent(9, "line", json.dumps(payload) + "\n"),
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=False,
        )

        emitted = sink.emit.call_args.args[0]
        self.assertEqual(emitted["status"], "DEATH_TRIGGERED")
        self.assertTrue(emitted["highFrequencyActive"])
        self.assertEqual(emitted["vision_generation"], 9)
        coordinator.apply_capture_health.assert_not_called()
        coordinator.handle_game_state.assert_not_called()

    def test_lcu_bench_poller_starts_only_when_context_is_auto(self) -> None:
        sink = mock.Mock()
        self.assertIsNone(
            bridge._start_lcu_poller(argparse.Namespace(lcu_context="off"), sink)
        )
        self.assertIsNone(bridge._start_lcu_poller(argparse.Namespace(), sink))
        sink.emit.assert_not_called()

        class FakePoller:
            def __init__(self, emit: object) -> None:
                self.started = False
                self.emit = emit

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                return None

        fake_module = mock.Mock()
        fake_module.LcuChampSelectPoller = FakePoller
        with mock.patch.object(bridge, "_load_lcu_champ_select", return_value=fake_module):
            poller = bridge._start_lcu_poller(
                argparse.Namespace(lcu_context="auto"), sink
            )
        self.assertIsNotNone(poller)
        assert poller is not None
        self.assertTrue(poller.started)

    def test_lcu_context_state_is_forwarded_without_touching_recommendation(
        self,
    ) -> None:
        payload = {
            "type": "lcu_context_state",
            "schema_version": 1,
            "status": "READY",
            "reason": "ok",
            "context": {"gameflowPhase": "ChampSelect", "championId": 136},
        }
        supervisor = mock.Mock()
        supervisor.generation = 8
        supervisor.accept.return_value = json.dumps(payload) + "\n"
        coordinator = mock.Mock()
        sink = mock.Mock()

        bridge._process_vision_stream_event(
            bridge.VisionStreamEvent(8, "line", json.dumps(payload) + "\n"),
            supervisor=supervisor,
            coordinator=coordinator,
            sink=sink,
            vision_debug=False,
        )

        emitted = sink.emit.call_args.args[0]
        self.assertEqual(emitted["context"], payload["context"])
        self.assertEqual(emitted["vision_generation"], 8)
        coordinator.apply_capture_health.assert_not_called()
        coordinator.handle_game_state.assert_not_called()

    def test_real_worker_ipc_with_four_round_state_contract(self) -> None:
        # This validates the production IPC against the real locked snapshot.
        # These GameState objects are contracts, not a claimed LoL live test.
        with bridge.RecommendationWorkerClient(
            python=Path(sys.executable),
            scrape_root=bridge.DEFAULT_SCRAPE_ROOT,
            visual_catalog=bridge.DEFAULT_VISUAL_CATALOG,
            timeout_seconds=5.0,
        ) as worker:
            coordinator = bridge.RecommendationCoordinator("阿狸", worker)
            for stage, offer in enumerate(OFFER_IDS, 1):
                event = coordinator.handle_game_state(game_state(stage, offer))
                self.assertEqual(event["status"], "recommended")
                self.assertEqual(event["snapshot_id"], SNAPSHOT_ID)
                self.assertLess(event["bridge_latency_ms"], 1000.0)
                self.assertEqual(len(event["owned"]), stage - 1)
                for item in event["ranking"]:
                    self.assertIn("hero_data", item["debug"])
                    self.assertIn("combo_data", item["debug"])
                    self.assertIn("inferred_data", item["debug"])
                confirmations = coordinator.confirm_selection(1)
                self.assertEqual(confirmations[0]["status"], "choice_confirmed")
            self.assertEqual(confirmations[-1]["type"], "recommendation_session_complete")
            self.assertEqual(coordinator.state.owned_augments, [row[0] for row in OFFER_IDS])
            self.assertEqual(worker.generation, 1)

    def test_real_worker_mixed_conflict_softens_but_all_conflicts_fail_closed(self) -> None:
        with bridge.RecommendationWorkerClient(
            python=Path(sys.executable),
            scrape_root=bridge.DEFAULT_SCRAPE_ROOT,
            visual_catalog=bridge.DEFAULT_VISUAL_CATALOG,
            timeout_seconds=5.0,
        ) as worker:
            mixed = bridge.RecommendationCoordinator("阿狸", worker)
            mixed_event = mixed.handle_game_state(
                game_state(
                    1,
                    ["ARAM_BreadAndButter", "ARAM_ADAPt", "ARAM_ApexInventor"],
                )
            )
            self.assertEqual(
                mixed_event["status"], "recommended_with_knowledge_conflict"
            )
            self.assertEqual(len(mixed_event["knowledge_conflicts"]), 1)
            self.assertTrue(
                all(
                    not item["stage_invalid"]
                    for item in mixed_event["ranking"]
                    if item["recommendation_eligible"]
                )
            )

            all_conflicts = bridge.RecommendationCoordinator("阿狸", worker)
            rejected = all_conflicts.handle_game_state(
                game_state(
                    1,
                    [
                        "ARAM_BreadAndButter",
                        "ARAM_BreadAndCheese",
                        "ARAM_BreadAndJam",
                    ],
                )
            )
            self.assertEqual(rejected["status"], "fail_closed")
            self.assertEqual(rejected["error"]["code"], "stage_invalid")
            self.assertIsNone(rejected["recommended"])


if __name__ == "__main__":
    unittest.main()
