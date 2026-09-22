from __future__ import annotations

import unittest

from agent_companion import HexRecommendationAgentSpeech
from agent_text import AgentResult
from speech import SpeechPriority


class FakeProvider:
    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def generate(self, prompt: str, context=None) -> AgentResult:
        self.calls.append((prompt, context))
        return self.result


def success(text: str = "这轮推荐巨人杀手，拿下它就开打吧！") -> AgentResult:
    return AgentResult(True, text, "query-1", None)


def recommendation_view(name: str = "巨人杀手") -> dict[str, object]:
    return {
        "state": "recommendation",
        "recommendation": {"augment": name, "position": "CENTER"},
        "message_blocks": [
            {"label": "当前推荐", "value": f"{name}（CENTER）"},
            {"label": "当前玩法", "value": "胜率优先"},
        ],
    }


class HexRecommendationAgentSpeechTests(unittest.TestCase):
    def test_entering_recommendation_calls_agent_and_publishes_once(self) -> None:
        provider = FakeProvider(success())
        published = []
        source = HexRecommendationAgentSpeech(
            provider,
            published.append,
            clock=lambda: 10.0,
            submit=lambda task: task(),
        )
        context = {"match_id": "lcu:1", "offer_round": 1, "choices": ["甲", "乙", "丙"]}

        source.update(recommendation_view(), context)
        source.update(recommendation_view(), context)

        self.assertEqual(1, len(provider.calls))
        self.assertIn("必须明确说出推荐的海克斯", provider.calls[0][0])
        self.assertEqual(context, provider.calls[0][1])
        self.assertEqual(1, len(published))
        self.assertEqual("agent", published[0].source)
        self.assertEqual(SpeechPriority.HIGH, published[0].priority)
        self.assertEqual(success().text, published[0].summary)

    def test_new_recommendation_round_starts_a_new_request(self) -> None:
        provider = FakeProvider(success())
        published = []
        source = HexRecommendationAgentSpeech(provider, published.append, submit=lambda task: task())

        source.update(recommendation_view(), {"match_id": "lcu:1", "offer_round": 1})
        source.update({"state": "ocr_reading"})
        source.update(recommendation_view(), {"match_id": "lcu:1", "offer_round": 2})

        self.assertEqual(2, len(provider.calls))
        self.assertNotEqual(published[0].dedupe_key, published[1].dedupe_key)

    def test_result_is_discarded_after_leaving_recommendation(self) -> None:
        provider = FakeProvider(success())
        published = []
        tasks = []
        source = HexRecommendationAgentSpeech(provider, published.append, submit=tasks.append)

        source.update(recommendation_view(), {"match_id": "lcu:1", "offer_round": 1})
        source.update({"state": "in_game"})
        tasks[0]()

        self.assertEqual([], published)

    def test_failure_logs_warning_and_publishes_local_fallback(self) -> None:
        provider = FakeProvider(AgentResult(False, "", "", "not_configured"))
        published = []
        source = HexRecommendationAgentSpeech(
            provider,
            published.append,
            submit=lambda task: task(),
        )

        with self.assertLogs("agent_companion", level="WARNING") as captured:
            source.update(recommendation_view())

        self.assertIn("WARNING:agent_companion", captured.output[0])
        self.assertIn("error=not_configured", captured.output[0])
        self.assertEqual("local", published[0].source)
        self.assertEqual("推荐选择巨人杀手，当前玩法胜率优先。", published[0].summary)

    def test_off_result_uses_local_fallback(self) -> None:
        provider = FakeProvider(success("OFF"))
        published = []
        source = HexRecommendationAgentSpeech(
            provider,
            published.append,
            submit=lambda task: task(),
        )

        source.update(recommendation_view())

        self.assertEqual("local", published[0].source)
        self.assertEqual("推荐选择巨人杀手，当前玩法胜率优先。", published[0].summary)

    def test_close_discards_an_inflight_result(self) -> None:
        provider = FakeProvider(success())
        published = []
        tasks = []
        source = HexRecommendationAgentSpeech(provider, published.append, submit=tasks.append)

        source.update(recommendation_view(), {"match_id": "lcu:1", "offer_round": 1})
        source.close()
        tasks[0]()

        self.assertEqual([], published)


if __name__ == "__main__":
    unittest.main()
