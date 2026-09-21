from __future__ import annotations

import unittest

from speech import SpeechPriority
from speech_policy import CompanionSpeechPolicy


class CompanionSpeechPolicyTests(unittest.TestCase):
    def test_recommendation_uses_short_semantic_summary_once(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {
            "state": "recommendation",
            "message_blocks": [
                {"label": "当前推荐", "value": "巨人杀手（CENTER）"},
                {"label": "当前玩法", "value": "胜率优先"},
            ],
        }
        first = policy.update(view, 1.0)
        self.assertEqual("推荐选择巨人杀手，当前玩法胜率优先。", first[0].summary)
        self.assertEqual(SpeechPriority.HIGH, first[0].priority)
        self.assertEqual((), policy.update(view, 1.1))

    def test_semantic_change_emits_new_recommendation(self) -> None:
        policy = CompanionSpeechPolicy()
        base = {"state": "recommendation", "message_blocks": [
            {"label": "当前推荐", "value": "巨人杀手（CENTER）"},
            {"label": "当前玩法", "value": "胜率优先"},
        ]}
        policy.update(base, 1.0)
        changed = {**base, "message_blocks": [
            {"label": "当前推荐", "value": "珠光护手（LEFT）"},
            {"label": "当前玩法", "value": "胜率优先"},
        ]}
        self.assertEqual("推荐选择珠光护手，当前玩法胜率优先。", policy.update(changed, 2.0)[0].summary)

    def test_same_recommendation_is_announced_again_in_a_new_round(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {
            "state": "recommendation",
            "message_blocks": [
                {"label": "当前推荐", "value": "巨人杀手（CENTER）"},
                {"label": "当前玩法", "value": "胜率优先"},
            ],
        }
        first = policy.update(view, 1.0)
        self.assertEqual((), policy.update(view, 1.1))
        policy.update({"state": "ocr_reading"}, 2.0)
        second = policy.update(view, 3.0)

        self.assertEqual("推荐选择巨人杀手，当前玩法胜率优先。", second[0].summary)
        self.assertNotEqual(first[0].dedupe_key, second[0].dedupe_key)

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

    def test_ordinary_bubble_uses_short_state_summary(self) -> None:
        policy = CompanionSpeechPolicy()
        result = policy.update({"state": "champ_select", "message": "很长" * 100}, 1.0)
        self.assertEqual("正在为你查看可选英雄。", result[0].summary)

    def test_hidden_bubble_is_not_spoken(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {"state": "waiting", "bubble_visible": False, "message": "等待"}
        self.assertEqual((), policy.update(view, 1.0))

    def test_hidden_ocr_error_is_not_spoken(self) -> None:
        policy = CompanionSpeechPolicy()
        messages = policy.update(
            {"state": "ocr_error", "bubble_visible": False},
            1.0,
        )
        self.assertEqual((), messages)
        self.assertEqual((), policy.tick(4.0))

    def test_recommendation_can_repeat_after_hidden_epoch(self) -> None:
        policy = CompanionSpeechPolicy()
        view = {
            "state": "recommendation",
            "bubble_visible": True,
            "message_blocks": [
                {"label": "当前推荐", "value": "巨人杀手（CENTER）"},
                {"label": "当前玩法", "value": "胜率优先"},
            ],
        }
        self.assertEqual(1, len(policy.update(view, 1.0)))
        self.assertEqual((), policy.update(view, 1.1))
        policy.update({"state": "waiting", "bubble_visible": False}, 2.0)
        self.assertEqual(1, len(policy.update(view, 32.0)))
        self.assertEqual((), policy.update(view, 32.1))


if __name__ == "__main__":
    unittest.main()
