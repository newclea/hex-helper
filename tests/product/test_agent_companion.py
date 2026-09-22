from __future__ import annotations

import threading
import unittest

from agent_companion import ChampionSelectAgentSpeech
from agent_text import AgentResult
from speech import SpeechPriority, SpeechService


class FakeProvider:
    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def generate(self, prompt: str, context=None) -> AgentResult:
        self.calls.append((prompt, context))
        return self.result


class RecordingSpeechAdapter:
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.spoken_event = threading.Event()

    def start(self) -> bool:
        return True

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        self.spoken_event.set()
        return True

    def wait_started(self, timeout=None) -> bool:
        return True

    def wait_finished(self, timeout=None) -> bool:
        return True

    def cancel(self) -> None:
        return None

    def close(self) -> None:
        return None


def success(text: str = "这局好运相伴，选你喜欢的英雄吧！") -> AgentResult:
    return AgentResult(True, text, "query-1", None)


class ChampionSelectAgentSpeechTests(unittest.TestCase):
    def test_entering_champion_select_generates_and_publishes_once(self) -> None:
        provider = FakeProvider(success())
        published = []
        source = ChampionSelectAgentSpeech(
            provider,
            published.append,
            clock=lambda: 10.0,
            submit=lambda task: task(),
        )
        source.update("champ_select", {"champion": "阿狸"})
        source.update("champ_select", {"champion": "阿狸"})

        self.assertEqual(1, len(provider.calls))
        self.assertIn("随机生成一句", provider.calls[0][0])
        self.assertIn("不超过三十个汉字", provider.calls[0][0])
        self.assertEqual({"champion": "阿狸"}, provider.calls[0][1])
        self.assertEqual(1, len(published))
        self.assertEqual("agent", published[0].source)
        self.assertEqual("champ_select_agent", published[0].kind)
        self.assertEqual(SpeechPriority.LOW, published[0].priority)
        self.assertEqual(success().text, published[0].summary)

    def test_leaving_and_reentering_starts_a_new_request(self) -> None:
        provider = FakeProvider(success())
        published = []
        source = ChampionSelectAgentSpeech(
            provider,
            published.append,
            submit=lambda task: task(),
        )
        source.update("champ_select")
        source.update("waiting")
        source.update("champ_select")

        self.assertEqual(2, len(provider.calls))
        self.assertNotEqual(published[0].dedupe_key, published[1].dedupe_key)

    def test_result_is_discarded_after_leaving_champion_select(self) -> None:
        provider = FakeProvider(success())
        published = []
        tasks = []
        source = ChampionSelectAgentSpeech(provider, published.append, submit=tasks.append)

        source.update("champ_select")
        source.update("waiting")
        tasks[0]()

        self.assertEqual([], published)

    def test_failure_does_not_publish_speech(self) -> None:
        provider = FakeProvider(AgentResult(False, "", "query-2", "timeout"))
        source = ChampionSelectAgentSpeech(
            provider,
            self.fail,
            submit=lambda task: task(),
        )

        source.update("champ_select")

        self.assertEqual(1, len(provider.calls))

    def test_close_discards_an_inflight_result(self) -> None:
        provider = FakeProvider(success())
        published = []
        tasks = []
        source = ChampionSelectAgentSpeech(provider, published.append, submit=tasks.append)

        source.update("champ_select")
        source.close()
        tasks[0]()

        self.assertEqual([], published)

    def test_successful_agent_result_reaches_speech_adapter(self) -> None:
        text = "峡谷风起，今天也要秀出你的操作！"
        provider = FakeProvider(success(text))
        adapter = RecordingSpeechAdapter()
        speech = SpeechService(adapter, clock=lambda: 10.0)
        source = ChampionSelectAgentSpeech(
            provider,
            speech.publish,
            clock=lambda: 10.0,
            submit=lambda task: task(),
        )

        source.update("champ_select")

        self.assertTrue(adapter.spoken_event.wait(timeout=1.0))
        source.close()
        speech.close()
        self.assertEqual([text], adapter.spoken)


if __name__ == "__main__":
    unittest.main()
