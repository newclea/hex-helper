from __future__ import annotations

import unittest

from speech import SpeechPriority
from speech_policy import CompanionSpeechPolicy, STARTUP_GREETING


class CompanionSpeechPolicyTests(unittest.TestCase):
    def test_startup_greeting_uses_requested_text_once(self) -> None:
        policy = CompanionSpeechPolicy()

        first = policy.startup(1.0)

        self.assertEqual(STARTUP_GREETING, first[0].summary)
        self.assertEqual("startup", first[0].kind)
        self.assertEqual((), policy.startup(2.0))

    def test_champion_select_speaks_exact_three_bubble_recommendations(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {
            "state": "champ_select",
            "recommended_champions": ["妮蔻", "亚索", "盖伦"],
        }

        first = policy.update(view, 1.0)

        self.assertEqual(
            "根据当前英雄强度，推荐选择妮蔻、亚索、盖伦三个英雄哦。",
            first[0].summary,
        )
        self.assertEqual((), policy.update(view, 1.1))

    def test_champion_select_speaks_one_or_two_recommendations_once(self) -> None:
        for names, expected in (
            (["妮蔻"], "根据当前英雄强度，推荐选择妮蔻这个英雄哦。"),
            (["妮蔻", "亚索"], "根据当前英雄强度，推荐选择妮蔻、亚索两个英雄哦。"),
        ):
            with self.subTest(names=names):
                policy = CompanionSpeechPolicy()
                view = {"state": "champ_select", "recommended_champions": names}
                self.assertEqual(expected, policy.update(view, 1.0)[0].summary)
                self.assertEqual((), policy.update(view, 2.0))

    def test_empty_or_invalid_recommendations_stay_silent(self) -> None:
        for names in ([], [""], ["妮蔻", " "], ["妮蔻"] * 4):
            with self.subTest(names=names):
                policy = CompanionSpeechPolicy()
                self.assertEqual((), policy.update(
                    {"state": "champ_select", "recommended_champions": names}, 1.0
                ))

    def test_hex_recommendation_is_left_to_agent_source(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {
            "state": "recommendation",
            "message_blocks": [
                {"label": "当前推荐", "value": "巨人杀手（CENTER）"},
                {"label": "当前玩法", "value": "胜率优先"},
            ],
        }
        self.assertEqual((), policy.update(view, 1.0))

    def test_game_result_speaks_once_even_when_bubble_is_hidden(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {
            "state": "in_game",
            "bubble_visible": False,
            "match_id": "lcu:123",
            "game_result": "WIN",
        }

        first = policy.update(view, 1.0)

        self.assertEqual("耶，赢啦！", first[0].summary)
        self.assertEqual(SpeechPriority.HIGH, first[0].priority)
        self.assertEqual((), policy.update(view, 2.0))

    def test_loss_can_speak_for_a_new_match(self) -> None:
        policy = CompanionSpeechPolicy()
        policy.update({"match_id": "lcu:1", "game_result": "WIN"}, 1.0)

        result = policy.update({"match_id": "lcu:2", "game_result": "LOSS"}, 2.0)

        self.assertEqual("惜败惜败，再开一局吧。", result[0].summary)

    def test_result_replay_or_correction_does_not_repeat_a_match(self) -> None:
        policy = CompanionSpeechPolicy()
        policy.update({"match_id": "lcu:1", "game_result": "WIN"}, 1.0)
        policy.update({"match_id": "lcu:2", "game_result": "LOSS"}, 2.0)
        for result in ("WIN", "LOSS"):
            self.assertEqual((), policy.update(
                {"match_id": "lcu:1", "game_result": result}, 40.0
            ))

    def test_unknown_game_result_stays_silent(self) -> None:
        policy = CompanionSpeechPolicy()
        self.assertEqual(
            (),
            policy.update({"match_id": "lcu:1", "game_result": "UNKNOWN"}, 1.0),
        )

    def test_ocr_progress_waits_two_seconds_and_fires_once(self) -> None:
        policy = CompanionSpeechPolicy()
        policy.update({"state": "ocr_reading"}, 10.0)
        self.assertEqual((), policy.tick(11.99))
        self.assertEqual((), policy.tick(12.0))
        self.assertEqual("正在识别海克斯，请稍候。", policy.tick(12.001)[0].summary)
        self.assertEqual((), policy.tick(13.0))

    def test_leaving_ocr_cancels_delayed_progress(self) -> None:
        policy = CompanionSpeechPolicy()
        policy.update({"state": "ocr_reading"}, 10.0)
        policy.update({"state": "waiting"}, 11.0)
        self.assertEqual((), policy.tick(12.0))

    def test_error_and_unavailable_are_high_priority(self) -> None:
        policy = CompanionSpeechPolicy()
        error = policy.update({"state": "ocr_error"}, 1.0)[0]
        unavailable = policy.update({"state": "recommendation_unavailable"}, 2.0)[0]
        self.assertEqual(SpeechPriority.HIGH, error.priority)
        self.assertEqual(SpeechPriority.HIGH, unavailable.priority)

    def test_hidden_bubble_is_not_spoken(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {"state": "waiting", "bubble_visible": False, "message": "等待"}
        self.assertEqual((), policy.update(view, 1.0))


if __name__ == "__main__":
    unittest.main()
