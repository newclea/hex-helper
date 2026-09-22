from __future__ import annotations

import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from agent_config import AgentSettings
from app import RecognitionApp, _parser
from overlay_config import VoiceSettings
from speech_policy import CompanionSpeechPolicy


def make_app() -> RecognitionApp:
    app = RecognitionApp.__new__(RecognitionApp)
    app._lock = threading.RLock()
    app._stopped = False
    app._clock = Mock(return_value=5.0)
    app._startup_greeting_until = None
    app.model = Mock()
    app.model.snapshot.return_value = {"phase": "GameStart"}
    app.product = Mock()
    app.product.present.return_value = {"state": "waiting"}
    app.window = Mock()
    app.speech_policy = Mock()
    app.speech_policy.update.return_value = ("update-message",)
    app.speech_policy.tick.return_value = ("tick-message",)
    app.speech_policy.startup.return_value = ()
    app.speech = Mock()
    app.agent_speech = Mock()
    app.reread = Mock()
    app.vision = Mock()
    app.poller = Mock()
    app.live_client = Mock()
    app.diagnostics = Mock()
    return app


class RecognitionAppSpeechTests(unittest.TestCase):
    def test_parser_accepts_scoped_game_result_debug_mode(self) -> None:
        args = _parser().parse_args(["--debug-submode", "game-result"])
        self.assertEqual(["game-result"], args.debug_submode)

    def test_greeting_start_publishes_fixed_startup_voice(self) -> None:
        app = make_app()
        app.speech_policy = CompanionSpeechPolicy()

        app._on_greeting_start(10.0)
        app._on_greeting_start(11.0)

        app.speech.publish.assert_called_once()
        self.assertEqual("startup", app.speech.publish.call_args.args[0].kind)

    def test_adapter_start_failure_does_not_break_app(self) -> None:
        adapter = Mock()
        adapter.start.return_value = False
        args = SimpleNamespace(
            knowledge=None, legacy_ui=False, width=420, height=700,
            recommendation_data=None, mode="KIWI", league_root=None,
            vision_exe=None, window_title="League", max_seconds=10.0,
        )
        bundle_root = Path("C:/gamebuddy")
        agent_settings = AgentSettings()
        dependencies = (
            "OcrDiagnostics", "HistoryStore", "RecognitionViewModel", "RecommendationEngine",
            "StrategyStore", "ProductController", "CatOverlayWindow", "LcuChampSelectPoller",
            "EnvironmentLcuConnectionProvider", "LiveClientPoller", "AutoRereadMonitor",
            "VisionSupervisor", "build_agent_provider", "HexRecommendationAgentSpeech",
        )
        with ExitStack() as stack:
            stack.enter_context(patch("app.load_voice_settings", return_value=VoiceSettings()))
            stack.enter_context(patch("app.load_agent_settings", return_value=agent_settings))
            offline_adapter = stack.enter_context(
                patch("app.OfflineSpeechAdapter", return_value=adapter)
            )
            stack.enter_context(patch("app.bundle_dir", return_value=bundle_root))
            stack.enter_context(patch("app.ChampionCatalog.load", return_value=Mock()))
            stack.enter_context(patch("app.AugmentCatalog.load", return_value=Mock()))
            mocks = {name: stack.enter_context(patch(f"app.{name}")) for name in dependencies}
            app = RecognitionApp(args)
            app.product.present.return_value = {
                "state": "champ_select", "bubble_visible": True,
                "recommended_champions": ["妮蔻", "亚索", "盖伦"],
            }
            app._publish()
            deadline = time.monotonic() + 1.0
            while adapter.start.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            app.stop()
        adapter.start.assert_called_once_with()
        offline_adapter.assert_called_once_with(bundle_root=bundle_root)
        mocks["CatOverlayWindow"].assert_called_once()
        mocks["build_agent_provider"].assert_called_once_with(agent_settings)
        mocks["HexRecommendationAgentSpeech"].assert_called_once_with(
            mocks["build_agent_provider"].return_value,
            app.speech.publish,
            clock=app._clock,
        )

    def test_publish_updates_window_and_speech_policy(self) -> None:
        app = make_app()
        app._publish()
        app.window.set_view.assert_called_once_with({"state": "waiting"})
        app.speech_policy.update.assert_called_once_with({"state": "waiting"}, 5.0)
        app.speech.publish.assert_called_once_with("update-message")

    def test_startup_gate_uses_window_greeting_boundary_after_slow_construction(self) -> None:
        app = make_app()
        app._on_greeting_start(100.0)
        app._clock.side_effect = (102.99, 103.0)
        app.product.present.return_value = {
            "state": "ocr_error",
            "bubble_visible": True,
            "message": "识别尚未成功",
            "options": [],
        }
        app.speech_policy = Mock(wraps=CompanionSpeechPolicy())

        app._publish()

        hidden = {
            "state": "waiting",
            "bubble_visible": False,
            "message": "核宝来了。",
            "options": [],
        }
        app.window.set_view.assert_called_once_with(hidden)
        app.speech_policy.update.assert_called_once_with(hidden, 102.99)
        app.speech.publish.assert_not_called()

        app._publish()

        error = app.product.present.return_value
        self.assertEqual([call(hidden), call(error)], app.window.set_view.call_args_list)
        self.assertEqual(call(error, 103.0), app.speech_policy.update.call_args)
        self.assertEqual("ocr_error", app.speech.publish.call_args.args[0].kind)

    def test_ui_tick_publishes_delayed_policy_messages(self) -> None:
        app = make_app()
        app._on_ui_tick()
        app.speech_policy.tick.assert_called_once_with(5.0)
        self.assertEqual(
            [(("update-message",),), (("tick-message",),)],
            app.speech.publish.call_args_list,
        )

    def test_toggle_persists_only_enabled_and_updates_runtime(self) -> None:
        app = make_app()
        with patch("app.overlay_config_path", return_value="overlay.json"), patch(
            "app.update_overlay_config",
        ) as update:
            app._on_voice_toggle(False)
        update.assert_called_once_with("overlay.json", {"voice_enabled": False})
        app.speech.set_enabled.assert_called_once_with(False)
        app.window.set_voice_enabled.assert_called_once_with(False)

    def test_stop_closes_speech_first_and_is_idempotent(self) -> None:
        app = make_app()
        order: list[str] = []
        app.agent_speech.close.side_effect = lambda: order.append("agent")
        app.speech.close.side_effect = lambda: order.append("speech")
        app.reread.stop.side_effect = lambda: order.append("reread")
        app.stop()
        app.stop()
        self.assertEqual(["agent", "speech", "reread"], order[:3])
        app.agent_speech.close.assert_called_once_with()
        app.speech.close.assert_called_once_with()
        app.vision.stop.assert_called_once_with()
        app.poller.stop.assert_called_once_with()
        app.live_client.stop.assert_called_once_with()
        app.diagnostics.close.assert_called_once_with()

    def test_start_workers_preloads_speech_before_pollers(self) -> None:
        app = make_app()
        app.args = SimpleNamespace(league_root=None)
        order: list[str] = []
        app.speech.preload.side_effect = lambda: order.append("speech")
        app.poller.start.side_effect = lambda: order.append("poller")
        with patch("app.resolve_league_root", return_value=Path("C:/League")):
            app._start_workers()

        self.assertEqual(["speech", "poller"], order[:2])

    def test_publish_updates_agent_speech_with_recommendation_context(self) -> None:
        app = make_app()
        app.model.snapshot.return_value = {
            "phase": "InProgress",
            "match_id": "lcu:1",
            "champion": "阿狸",
            "offer_round": 2,
            "offer": [{"name": "巨人杀手"}, {"name": "珠光护手"}, {"name": "掷骰狂人"}],
            "selected": [{"name": "裁决使"}],
        }
        app.product.present.return_value = {
            "state": "recommendation",
            "bubble_visible": True,
            "recommendation": {"augment": "巨人杀手", "position": "LEFT"},
        }

        app._publish()

        app.agent_speech.update.assert_called_once_with(
            app.product.present.return_value,
            {
                "match_id": "lcu:1",
                "offer_round": 2,
                "champion": "阿狸",
                "choices": ["巨人杀手", "珠光护手", "掷骰狂人"],
                "selected_augments": ["裁决使"],
                "recommendation": {"augment": "巨人杀手", "position": "LEFT"},
            },
        )


if __name__ == "__main__":
    unittest.main()
