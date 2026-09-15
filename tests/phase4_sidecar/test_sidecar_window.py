from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_PATH = WORKSPACE_ROOT / "scripts" / "phase4" / "sidecar_window.py"
SPEC = importlib.util.spec_from_file_location("phase4_sidecar_window", SIDECAR_PATH)
assert SPEC is not None and SPEC.loader is not None
sidecar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sidecar)


def start_event(**extra: object) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "recommendation_session_start",
        "status": "ready",
        "hero": "阿狸",
        "snapshot_id": "16.16-test",
        "source": {"kind": "screen_capture"},
    }
    event.update(extra)
    return event


def recommendation_event(
    *,
    status: str = "recommended",
    source_kind: str = "screen_capture",
) -> dict[str, object]:
    return {
        "type": "recommendation",
        "status": status,
        "hero": "阿狸",
        "stage": 2,
        "choices": ["全心为你", "物理转魔法", "顶级发明家"],
        "recommended": "物理转魔法",
        "ranking": [
            {"augment": "物理转魔法"},
            {"augment": "全心为你"},
            {"augment": "顶级发明家"},
        ],
        "snapshot_id": "16.16-test",
        "offer_id": "offer-stage2-abc",
        "source": {"kind": source_kind},
    }


def live_client_event(
    *, level: int = 11, is_dead: bool = True, respawn_timer: float = 8.5
) -> dict[str, object]:
    return {
        "type": "live_client_state",
        "schema_version": 1,
        "status": "READY",
        "reason": "ok",
        "player": {
            "championName": "Ahri",
            "level": level,
            "isDead": is_dead,
            "respawnTimer": respawn_timer,
        },
    }


def mayhem_event(status: str, **extra: object) -> dict[str, object]:
    phases = {
        "ARMED": "ARMED",
        "DEATH_TRIGGERED": "TRIGGERED",
        "QUEUED_OFFER_TRIGGERED": "TRIGGERED",
        "OFFER_DETECTED": "WAITING_LEVEL",
        "WINDOW_EXPIRED": "ARMED",
    }
    event: dict[str, object] = {
        "type": "mayhem_selection_state",
        "schema_version": 1,
        "status": status,
        "phase": phases[status],
        "stage": 2,
        "thresholdLevel": 7,
        "player": {
            "championName": "Ahri",
            "level": 11,
            "isDead": status == "DEATH_TRIGGERED",
            "respawnTimer": 8.5 if status == "DEATH_TRIGGERED" else 0.0,
        },
    }
    event.update(extra)
    return event


class EventReducerTests(unittest.TestCase):
    def test_session_start_maps_hero_source_and_waiting_stage(self) -> None:
        model = sidecar.reduce_event(None, start_event())
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model["event"], "recommendation_session_start")
        self.assertEqual(model["hero"], "阿狸")
        self.assertEqual(model["source_kind"], "live_screen")
        self.assertIn("SCREEN CAPTURE", model["render_text"])
        self.assertIn("阶段: 等待识别", model["render_text"])

    def test_recommended_maps_three_slots_recommendation_and_hotkeys(self) -> None:
        model = sidecar.reduce_event(None, recommendation_event())
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model["status"], "recommended")
        self.assertEqual([item["slot"] for item in model["choices"]], [1, 2, 3])
        self.assertEqual(len([item for item in model["choices"] if item["recommended"]]), 1)
        self.assertEqual(model["recommended"], "物理转魔法")
        self.assertIn("Ctrl+Alt+1 / 2 / 3", model["render_text"])

    def test_live_client_state_displays_level_and_life_without_touching_advice(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        model = sidecar.reduce_event(previous, live_client_event())
        assert model is not None
        self.assertEqual(model["status"], "recommended")
        self.assertEqual(model["recommended"], "物理转魔法")
        self.assertEqual(len(model["choices"]), 3)
        self.assertTrue(model["hotkeys_enabled"])
        self.assertEqual(model["live_level"], 11)
        self.assertIs(model["live_is_dead"], True)
        self.assertIn("Lv.11 · 阵亡 · 8.5 秒复活", model["render_text"])

    def test_live_context_unavailable_clears_only_context_fields(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        ready = sidecar.reduce_event(previous, live_client_event())
        assert ready is not None
        unavailable = sidecar.reduce_event(
            ready,
            {
                "type": "live_client_state",
                "schema_version": 1,
                "status": "UNAVAILABLE",
                "reason": "game_not_running",
                "player": None,
            },
        )
        assert unavailable is not None
        self.assertEqual(unavailable["live_client_status"], "UNAVAILABLE")
        self.assertIsNone(unavailable["live_level"])
        self.assertEqual(unavailable["recommended"], "物理转魔法")
        self.assertEqual(len(unavailable["choices"]), 3)

    def test_mayhem_trigger_states_are_explicit_and_preserve_safe_advice(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        expected_text = {
            "ARMED": "已到阈值，等待首次死亡",
            "DEATH_TRIGGERED": "选牌触发，识别中",
            "QUEUED_OFFER_TRIGGERED": "连续选牌已排队，保持识别中",
            "OFFER_DETECTED": "推荐状态：已就绪（物理转魔法）",
        }
        for status, text in expected_text.items():
            with self.subTest(status=status):
                model = sidecar.reduce_event(previous, mayhem_event(status))
                assert model is not None
                self.assertEqual(model["mayhem_status"], status)
                self.assertEqual(model["status"], "recommended")
                self.assertEqual(model["recommended"], "物理转魔法")
                self.assertEqual(len(model["choices"]), 3)
                self.assertIn(text, model["render_text"])

    def test_offer_detected_transitions_from_waiting_to_recommendation_status(self) -> None:
        detected = sidecar.reduce_event(None, mayhem_event("OFFER_DETECTED"))
        assert detected is not None
        self.assertEqual(detected["status"], "waiting")
        self.assertIsNone(detected["recommended"])
        self.assertIn("推荐状态：等待安全推荐", detected["render_text"])

        recommended = sidecar.reduce_event(detected, recommendation_event())
        assert recommended is not None
        self.assertEqual(recommended["mayhem_status"], "OFFER_DETECTED")
        self.assertIn("推荐状态：已就绪（物理转魔法）", recommended["render_text"])

    def test_malformed_context_fails_closed_only_in_context_lane(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        malformed_events = (
            {**live_client_event(), "schema_version": 9},
            {**mayhem_event("ARMED"), "stage": 9},
        )
        for event in malformed_events:
            with self.subTest(event=event["type"]):
                model = sidecar.reduce_event(previous, event)
                assert model is not None
                self.assertEqual(model["status"], "recommended")
                self.assertEqual(model["recommended"], "物理转魔法")
                self.assertEqual(len(model["choices"]), 3)
                self.assertIsNotNone(model["context_warning"])
                self.assertIn("上下文警示", model["render_text"])

    def test_short_recommended_type_and_enveloped_payload_are_supported(self) -> None:
        raw = recommendation_event()
        raw.pop("type")
        raw.pop("status")
        model = sidecar.reduce_event(
            None,
            {
                "type": "recommended",
                "source": {"kind": "screen_capture"},
                "payload": raw,
            },
        )
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model["status"], "recommended")
        self.assertEqual(model["recommended"], "物理转魔法")

    def test_knowledge_conflict_is_never_hidden(self) -> None:
        event = recommendation_event(status="recommended_with_knowledge_conflict")
        event["knowledge_conflicts"] = [
            {
                "code": "snapshot_available_stages_conflict",
                "augment": "全心为你",
                "observed_stage": 2,
                "snapshot_available_stages": [3, 4],
            }
        ]
        model = sidecar.reduce_event(None, event)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model["status"], "recommended_with_knowledge_conflict")
        self.assertTrue(model["choices"][0]["knowledge_conflict"])
        self.assertFalse(model["choices"][1]["knowledge_conflict"])
        self.assertEqual(model["recommended"], "物理转魔法")
        self.assertIn("知识冲突", model["render_text"])
        self.assertIn("snapshot 轮次 3,4", model["render_text"])

    def test_recommended_choice_in_conflicts_fails_closed(self) -> None:
        event = recommendation_event(status="recommended_with_knowledge_conflict")
        event["knowledge_conflicts"] = [{"augment": "物理转魔法"}]
        model = sidecar.reduce_event(None, event)
        assert model is not None
        self.assertEqual(model["status"], "fail_closed")
        self.assertEqual(model["error"]["code"], "recommended_knowledge_conflict")
        self.assertIsNone(model["recommended"])
        self.assertEqual(model["choices"], [])

    def test_all_choices_in_conflicts_fail_closed(self) -> None:
        event = recommendation_event(status="recommended_with_knowledge_conflict")
        event["knowledge_conflicts"] = [
            {"augment": augment}
            for augment in ("全心为你", "物理转魔法", "顶级发明家")
        ]
        model = sidecar.reduce_event(None, event)
        assert model is not None
        self.assertEqual(model["status"], "fail_closed")
        self.assertEqual(model["error"]["code"], "all_choices_knowledge_conflict")
        self.assertIsNone(model["recommended"])

    def test_conflict_outside_choices_fails_closed(self) -> None:
        event = recommendation_event(status="recommended_with_knowledge_conflict")
        event["knowledge_conflicts"] = [{"augment": "不在当前三选一"}]
        model = sidecar.reduce_event(None, event)
        assert model is not None
        self.assertEqual(model["status"], "fail_closed")
        self.assertEqual(model["error"]["code"], "invalid_knowledge_conflicts")
        self.assertEqual(model["knowledge_conflicts"], [])

    def test_malformed_conflict_fields_fail_closed(self) -> None:
        malformed_values: tuple[object, ...] = (
            {"augment": "全心为你"},
            ["全心为你"],
            [{}],
            [{"augment": "全心为你", "observed_stage": 9}],
            [{"augment": "全心为你", "snapshot_available_stages": "3,4"}],
        )
        for malformed in malformed_values:
            with self.subTest(knowledge_conflicts=malformed):
                event = recommendation_event()
                event["knowledge_conflicts"] = malformed
                model = sidecar.reduce_event(None, event)
                assert model is not None
                self.assertEqual(model["status"], "fail_closed")
                self.assertEqual(model["error"]["code"], "invalid_knowledge_conflicts")

    def test_conflict_payload_upgrades_plain_status_to_warning(self) -> None:
        event = recommendation_event()
        event["knowledge_conflicts"] = [{"augment": "全心为你"}]
        model = sidecar.reduce_event(None, event)
        assert model is not None
        self.assertEqual(model["status"], "recommended_with_knowledge_conflict")
        self.assertIsNotNone(model["warning"])

    def test_fail_closed_clears_an_existing_recommendation(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        model = sidecar.reduce_event(
            previous,
            {
                "type": "recommendation",
                "status": "fail_closed",
                "hero": "阿狸",
                "stage": 2,
                "recommended": "不得保留",
                "choices": ["A", "B", "C"],
                "ranking": [{"augment": "不得保留"}],
                "error": {"code": "unknown_ocr", "message": "unsafe offer"},
            },
        )
        assert model is not None
        self.assertEqual(model["status"], "fail_closed")
        self.assertIsNone(model["recommended"])
        self.assertEqual(model["choices"], [])
        self.assertEqual(model["error"]["code"], "unknown_ocr")
        self.assertIn("无安全建议", model["render_text"])

    def test_malformed_success_event_fails_closed(self) -> None:
        event = recommendation_event()
        event["choices"] = ["A", "A", "C"]
        event["recommended"] = "A"
        model = sidecar.reduce_event(None, event)
        assert model is not None
        self.assertEqual(model["status"], "fail_closed")
        self.assertEqual(model["error"]["code"], "invalid_recommendation_choices")

    def test_choice_confirmation_updates_display_but_sends_nothing(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        model = sidecar.reduce_event(
            previous,
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "hero": "阿狸",
                "stage": 2,
                "offer_id": "offer-stage2-abc",
                "selected_slot": "CENTER",
                "selected": "物理转魔法",
            },
        )
        assert model is not None
        self.assertEqual(model["selected_slot"], 2)
        self.assertEqual(model["selected"], "物理转魔法")
        self.assertIsNone(model["recommended"])
        self.assertEqual(model["choices"], [])
        self.assertEqual(model["knowledge_conflicts"], [])
        self.assertIn("玩家已确认", model["render_text"])

    def test_choice_confirmation_requires_exact_status_and_active_offer(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        invalid_events = (
            {
                "type": "choice_confirmation",
                "status": "completed",
                "stage": 2,
                "selected_slot": 2,
                "selected": "物理转魔法",
            },
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "stage": 3,
                "selected_slot": 2,
                "selected": "物理转魔法",
            },
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "stage": 2,
                "selected_slot": 1,
                "selected": "物理转魔法",
            },
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "stage": 2,
                "selected_slot": 2,
                "selected": "顶级发明家",
            },
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "stage": 2,
                "offer_id": "stale-offer-same-stage",
                "selected_slot": 2,
                "selected": "物理转魔法",
            },
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "stage": 2,
                "selected_slot": True,
                "selected": "全心为你",
            },
        )
        for event in invalid_events:
            with self.subTest(event=event):
                model = sidecar.reduce_event(previous, event)
                assert model is not None
                self.assertEqual(model["status"], "fail_closed")
                self.assertIsNone(model["recommended"])
                self.assertEqual(model["choices"], [])

    def test_choice_confirmation_without_active_offer_fails_closed(self) -> None:
        model = sidecar.reduce_event(
            sidecar.reduce_event(None, start_event()),
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "stage": 2,
                "selected_slot": 2,
                "selected": "物理转魔法",
            },
        )
        assert model is not None
        self.assertEqual(model["status"], "fail_closed")
        self.assertEqual(model["error"]["code"], "invalid_choice_confirmation")

    def test_session_end_is_terminal_and_clears_old_result(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        model = sidecar.reduce_event(
            previous,
            {"type": "session_end", "status": "completed", "hero": "阿狸"},
        )
        assert model is not None
        self.assertTrue(model["terminal"])
        self.assertIsNone(model["recommended"])
        self.assertEqual(model["choices"], [])
        self.assertIn("会话已安全结束", model["render_text"])

    def test_terminal_rejects_delayed_known_events_and_only_start_reopens(self) -> None:
        active = sidecar.reduce_event(None, recommendation_event())
        assert active is not None
        terminal = sidecar.reduce_event(
            active,
            {"type": "session_end", "status": "completed", "hero": "阿狸"},
        )
        assert terminal is not None
        delayed_events = (
            recommendation_event(),
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "stage": 2,
                "selected_slot": 2,
                "selected": "物理转魔法",
            },
            {"type": "session_end", "status": "completed", "hero": "阿狸"},
        )
        for delayed in delayed_events:
            with self.subTest(event=delayed["type"]):
                model = sidecar.reduce_event(terminal, delayed)
                assert model is not None
                self.assertEqual(model["status"], "fail_closed")
                self.assertEqual(model["error"]["code"], "event_after_terminal")
                self.assertTrue(model["terminal"])
                self.assertIsNone(model["recommended"])
                self.assertEqual(model["choices"], [])

        restarted = sidecar.reduce_event(terminal, start_event(hero="阿狸"))
        assert restarted is not None
        self.assertFalse(restarted["terminal"])
        self.assertEqual(restarted["status"], "ready")

    def test_schema_version_if_present_must_be_integer_one(self) -> None:
        for invalid in (999, "1", True, None):
            with self.subTest(schema_version=invalid):
                event = recommendation_event()
                event["schema_version"] = invalid
                model = sidecar.reduce_event(None, event)
                assert model is not None
                self.assertEqual(model["status"], "fail_closed")
                self.assertEqual(model["error"]["code"], "unsupported_schema_version")

        valid = recommendation_event()
        valid["schema_version"] = 1
        model = sidecar.reduce_event(None, valid)
        assert model is not None
        self.assertEqual(model["status"], "recommended")

    def test_enveloped_payload_schema_version_is_validated(self) -> None:
        event = recommendation_event()
        event.pop("type")
        event.pop("status")
        event["schema_version"] = 999
        model = sidecar.reduce_event(None, {"type": "recommended", "payload": event})
        assert model is not None
        self.assertEqual(model["status"], "fail_closed")
        self.assertEqual(model["error"]["code"], "unsupported_schema_version")

    def test_upstream_recommendation_session_end_alias_is_supported(self) -> None:
        model = sidecar.reduce_event(
            None,
            {
                "type": "recommendation_session_end",
                "status": "incomplete",
                "hero": "阿狸",
            },
        )
        assert model is not None
        self.assertEqual(model["event"], "session_end")


class DryRunJsonlTests(unittest.TestCase):
    def _run(self, lines: list[str]) -> list[dict[str, object]]:
        output = io.StringIO()
        sidecar.process_jsonl(io.StringIO("".join(lines)), output)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_unknown_event_is_ignored(self) -> None:
        results = self._run(
            [
                json.dumps(start_event(), ensure_ascii=False) + "\n",
                json.dumps({"type": "heartbeat"}) + "\n",
                json.dumps(recommendation_event(), ensure_ascii=False) + "\n",
            ]
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[-1]["status"], "recommended")

    def test_illegal_json_fails_closed_and_processing_continues(self) -> None:
        results = self._run(
            [
                "{not-json}\n",
                json.dumps(recommendation_event(), ensure_ascii=False) + "\n",
            ]
        )
        self.assertEqual(results[0]["status"], "fail_closed")
        self.assertEqual(results[0]["error"]["code"], "invalid_json")
        self.assertEqual(results[0]["choices"], [])
        self.assertEqual(results[1]["status"], "recommended")

    def test_blank_duplicate_key_and_non_finite_json_fail_closed(self) -> None:
        results = self._run(
            [
                "\n",
                '{"type":"recommended","type":"fail_closed"}\n',
                '{"type":"recommended","stage":NaN}\n',
            ]
        )
        self.assertEqual(len(results), 3)
        self.assertTrue(all(item["status"] == "fail_closed" for item in results))
        self.assertTrue(all(item["error"]["code"] == "invalid_json" for item in results))

    def test_lcu_bench_and_selected_history_are_displayed(self) -> None:
        started = sidecar.reduce_event(
            None, start_event(resumed_owned=["全心为你"])
        )
        assert started is not None
        self.assertEqual(started["selected_history"][0]["label"], "全心为你")
        self.assertIn("已选海克斯", started["render_text"])

        recommended = sidecar.reduce_event(started, recommendation_event())
        assert recommended is not None
        lcu_event = {
            "type": "lcu_context_state",
            "schema_version": 1,
            "status": "READY",
            "reason": "ok",
            "context": {
                "gameflowPhase": "ChampSelect",
                "championId": 51,
                "benchEnabled": True,
                "benchChampions": [
                    {"championId": 78, "name": "波比"},
                    {"championId": 51, "name": "凯特琳"},
                ],
            },
        }
        model = sidecar.reduce_event(recommended, lcu_event)
        assert model is not None
        self.assertEqual(model["status"], "recommended")
        self.assertEqual(model["recommended"], "物理转魔法")
        self.assertEqual(len(model["choices"]), 3)
        self.assertEqual(
            [item["name"] for item in model["bench_champions"]],
            ["波比", "凯特琳"],
        )
        self.assertIn("待选席: 波比、凯特琳", model["render_text"])
        self.assertIn("LCU: READY · ChampSelect", model["render_text"])

        confirmed = sidecar.reduce_event(
            model,
            {
                "type": "choice_confirmation",
                "status": "choice_confirmed",
                "hero": "阿狸",
                "stage": 2,
                "offer_id": "offer-stage2-abc",
                "selected_slot": "CENTER",
                "selected": "物理转魔法",
            },
        )
        assert confirmed is not None
        self.assertEqual(
            [item["label"] for item in confirmed["selected_history"]],
            ["全心为你", "物理转魔法"],
        )
        self.assertIn("第 2 轮 物理转魔法", confirmed["render_text"])
        self.assertEqual(
            [item["name"] for item in confirmed["bench_champions"]],
            ["波比", "凯特琳"],
        )

        in_progress = sidecar.reduce_event(
            confirmed,
            {
                "type": "lcu_context_state",
                "schema_version": 1,
                "status": "READY",
                "reason": "ok",
                "context": {"gameflowPhase": "InProgress", "championId": None},
            },
        )
        assert in_progress is not None
        self.assertEqual(in_progress["bench_champions"], [])
        self.assertIn("非选人阶段", in_progress["render_text"])
        self.assertEqual(
            [item["label"] for item in in_progress["selected_history"]],
            ["全心为你", "物理转魔法"],
        )

    def test_malformed_lcu_context_fails_closed_only_in_context_lane(self) -> None:
        previous = sidecar.reduce_event(None, recommendation_event())
        assert previous is not None
        model = sidecar.reduce_event(
            previous,
            {
                "type": "lcu_context_state",
                "schema_version": 1,
                "status": "READY",
                "context": {"gameflowPhase": "ChampSelect", "benchChampions": "bad"},
            },
        )
        assert model is not None
        self.assertEqual(model["status"], "recommended")
        self.assertEqual(model["recommended"], "物理转魔法")
        self.assertEqual(len(model["choices"]), 3)
        self.assertEqual(model["lcu_status"], "INVALID_RESPONSE")
        self.assertIn("上下文警示", model["render_text"])

    def test_cli_dry_run_creates_no_window_and_outputs_normalized_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", str(SIDECAR_PATH), "--dry-run-jsonl"],
            input=json.dumps(recommendation_event(), ensure_ascii=False) + "\n",
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=WORKSPACE_ROOT,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "recommended")
        self.assertEqual(payload["recommended"], "物理转魔法")
        self.assertEqual(completed.stderr, "")

    def test_position_and_size_arguments(self) -> None:
        args = sidecar._parser().parse_args(
            ["--dry-run-jsonl", "--x", "30", "--y", "40", "--width", "500", "--height", "360"]
        )
        self.assertEqual((args.x, args.y, args.width, args.height), (30, 40, 500, 360))


class LayoutTests(unittest.TestCase):
    def test_default_layout_stays_left_of_cards_and_above_bottom_hud(self) -> None:
        work_area = (0, 0, 1920, 1040)
        x, y, width, height = sidecar.compute_sidecar_layout(
            work_area,
            requested_x=None,
            requested_y=None,
            width=460,
            height=900,
        )
        self.assertGreaterEqual(x, work_area[0])
        self.assertGreaterEqual(y, work_area[1])
        self.assertLessEqual(width, int(1920 * 0.18))
        self.assertLessEqual((x + width) / 1920, 0.197)
        self.assertLessEqual((y + height) / 1040, 0.68)

    def test_default_layout_uses_offset_primary_work_area(self) -> None:
        work_area = (1920, 40, 3840, 1080)
        x, y, width, height = sidecar.compute_sidecar_layout(
            work_area,
            requested_x=None,
            requested_y=None,
            width=460,
            height=340,
        )
        self.assertGreater(x, work_area[0])
        self.assertGreater(y, work_area[1])
        self.assertLessEqual((x + width - work_area[0]) / 1920, 0.197)
        self.assertLess(y + height, work_area[3])

    def test_explicit_cli_position_is_preserved_while_width_remains_bounded(self) -> None:
        layout = sidecar.compute_sidecar_layout(
            (0, 0, 1600, 900),
            requested_x=40,
            requested_y=50,
            width=500,
            height=360,
        )
        self.assertEqual(layout, (40, 50, 288, 360))

    def test_invalid_work_area_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sidecar.compute_sidecar_layout(
                (0, 0, 0, 900),
                requested_x=None,
                requested_y=None,
                width=460,
                height=340,
            )


class Win32ContractTests(unittest.TestCase):
    def test_hit_test_is_transparent_before_default_window_processing(self) -> None:
        host = object.__new__(sidecar.Win32SidecarHost)
        result = host._window_proc(
            123,
            sidecar.Win32SidecarHost.WM_NCHITTEST,
            0,
            0,
        )
        self.assertEqual(result, sidecar.Win32SidecarHost.HTTRANSPARENT)

    def test_topmost_promotion_uses_only_noactivate_flag(self) -> None:
        host = object.__new__(sidecar.Win32SidecarHost)
        host.hwnd = 123
        host.user32 = mock.Mock()
        host.user32.SetWindowPos.return_value = True
        host.wintypes = mock.Mock()
        host.wintypes.HWND.side_effect = lambda value: value

        host._set_topmost_without_activation(8, 9, 300, 340)

        host.user32.SetWindowPos.assert_called_once_with(
            123,
            sidecar.Win32SidecarHost.HWND_TOPMOST,
            8,
            9,
            300,
            340,
            sidecar.Win32SidecarHost.SWP_NOACTIVATE,
        )


class SourceSafetyTests(unittest.TestCase):
    def test_nonactivating_styles_and_show_mode_are_present(self) -> None:
        source = SIDECAR_PATH.read_text(encoding="utf-8")
        self.assertIn("WS_EX_NOACTIVATE = 0x08000000", source)
        self.assertIn("WS_EX_TOOLWINDOW = 0x00000080", source)
        self.assertIn("WS_EX_TRANSPARENT = 0x00000020", source)
        self.assertIn("WS_EX_LAYERED = 0x00080000", source)
        self.assertIn("SetLayeredWindowAttributes", source)
        self.assertIn("LWA_ALPHA = 0x00000002", source)
        self.assertIn("SW_SHOWNOACTIVATE = 4", source)
        self.assertIn("HWND_TOPMOST = -1", source)
        self.assertIn("SWP_NOACTIVATE = 0x0010", source)
        self.assertIn("WM_MOUSEACTIVATE", source)
        self.assertIn("WM_NCHITTEST = 0x0084", source)
        self.assertIn("HTTRANSPARENT = -1", source)
        self.assertIn("MA_NOACTIVATE", source)
        self.assertIn("self._set_topmost_without_activation(x, y, width, height)", source)

    def test_forbidden_focus_and_input_apis_are_absent(self) -> None:
        source = SIDECAR_PATH.read_text(encoding="utf-8")
        forbidden = (
            "SetForegroundWindow",
            "SetFocus",
            "SetActiveWindow",
            "AttachThreadInput",
            "BringWindowToTop",
            "SwitchToThisWindow",
            "AllowSetForegroundWindow",
            "LockSetForegroundWindow",
            "WS_EX_TOPMOST",
            "SendInput",
            "keybd_event",
            "mouse_event",
            "SetCursorPos",
            "ClipCursor",
            "RegisterHotKey",
            "UnregisterHotKey",
            "GetAsyncKeyState",
            "GetKeyState",
            "SetWindowsHookEx",
            "UnhookWindowsHookEx",
            "RegisterRawInputDevices",
            "GetRawInputData",
            "SetCapture",
            "ReleaseCapture",
            "UpdateLayeredWindow",
            "DwmExtendFrameIntoClientArea",
            "Direct3D",
            "DirectDraw",
            "OpenGL",
            "Vulkan",
            "OpenProcess",
            "WriteProcessMemory",
            "CreateRemoteThread",
            "VirtualAllocEx",
        )
        for name in forbidden:
            with self.subTest(name=name):
                self.assertNotIn(name, source)

        self.assertEqual(
            source.count("self.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)"),
            1,
        )
        self.assertEqual(source.count("self.user32.SetWindowPos("), 1)

    def test_no_tkinter_or_third_party_imports(self) -> None:
        source = SIDECAR_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("import requests", source)
        self.assertNotIn("import pywin32", source)


if __name__ == "__main__":
    unittest.main()
