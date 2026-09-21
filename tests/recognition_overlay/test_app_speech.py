from __future__ import annotations

import threading
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import RecognitionApp
from overlay_config import VoiceSettings


def make_app() -> RecognitionApp:
    app = RecognitionApp.__new__(RecognitionApp)
    app._lock = threading.RLock()
    app._stopped = False
    app._clock = Mock(return_value=5.0)
    app.model = Mock()
    app.model.snapshot.return_value = {"phase": "GameStart"}
    app.product = Mock()
    app.product.present.return_value = {"state": "waiting"}
    app.window = Mock()
    app.speech_policy = Mock()
    app.speech_policy.update.return_value = ("update-message",)
    app.speech_policy.tick.return_value = ("tick-message",)
    app.speech = Mock()
    app.reread = Mock()
    app.vision = Mock()
    app.poller = Mock()
    app.live_client = Mock()
    app.diagnostics = Mock()
    return app


class RecognitionAppSpeechTests(unittest.TestCase):
    def test_adapter_start_failure_does_not_break_app(self) -> None:
        adapter = Mock()
        adapter.start.return_value = False
        args = SimpleNamespace(
            knowledge=None, legacy_ui=False, width=420, height=700,
            recommendation_data=None, mode="KIWI", league_root=None,
            vision_exe=None, window_title="League", max_seconds=10.0,
        )
        dependencies = (
            "OcrDiagnostics", "HistoryStore", "RecognitionViewModel", "RecommendationEngine",
            "StrategyStore", "ProductController", "CatOverlayWindow", "LcuChampSelectPoller",
            "EnvironmentLcuConnectionProvider", "LiveClientPoller", "AutoRereadMonitor",
            "VisionSupervisor",
        )
        with ExitStack() as stack:
            stack.enter_context(patch("app.load_voice_settings", return_value=VoiceSettings()))
            stack.enter_context(patch("app.WindowsSpeechAdapter", return_value=adapter))
            stack.enter_context(patch("app.ChampionCatalog.load", return_value=Mock()))
            stack.enter_context(patch("app.AugmentCatalog.load", return_value=Mock()))
            mocks = {name: stack.enter_context(patch(f"app.{name}")) for name in dependencies}
            app = RecognitionApp(args)
            app.product.present.return_value = {
                "state": "champ_select", "bubble_visible": True,
            }
            app._publish()
            deadline = time.monotonic() + 1.0
            while adapter.start.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            app.stop()
        adapter.start.assert_called_once_with()
        mocks["CatOverlayWindow"].assert_called_once()

    def test_publish_updates_window_and_speech_policy(self) -> None:
        app = make_app()
        app._publish()
        app.window.set_view.assert_called_once_with({"state": "waiting"})
        app.speech_policy.update.assert_called_once_with({"state": "waiting"}, 5.0)
        app.speech.publish.assert_called_once_with("update-message")

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
        app.speech.close.side_effect = lambda: order.append("speech")
        app.reread.stop.side_effect = lambda: order.append("reread")
        app.stop()
        app.stop()
        self.assertEqual(["speech", "reread"], order[:2])
        app.speech.close.assert_called_once_with()
        app.vision.stop.assert_called_once_with()
        app.poller.stop.assert_called_once_with()
        app.live_client.stop.assert_called_once_with()
        app.diagnostics.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
