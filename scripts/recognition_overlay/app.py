#!/usr/bin/env python3
"""Standalone topmost overlay: champ-select bench + hexcore recognition."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping


def _bootstrap_imports() -> None:
    here = Path(__file__).resolve().parent
    phase4 = here.parent / "phase4"
    product = here.parent / "product"
    for path in (here, phase4, product):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


_bootstrap_imports()

from augment_catalog import AugmentCatalog
from champion_catalog import ChampionCatalog
from history_store import HistoryStore
from lcu_champ_select import (
    EnvironmentLcuConnectionProvider,
    LcuChampSelectPoller,
)
from league_root import resolve_league_root
from overlay_window import OverlayWindow
from ocr_diagnostics import OcrDiagnostics
from paths import (
    augment_catalog_path,
    bundle_dir,
    champion_catalog_path,
    find_vision_exe,
    history_path,
    log_path,
    legacy_overlay_config_path,
    ocr_diagnostics_path,
    overlay_config_path,
    vision_home,
    vision_workspace,
    strategy_path,
)
from live_client import LiveClientPoller
from view_model import RecognitionViewModel
from click_flag import (
    AUTO_REREAD_INTERVAL_SECONDS,
    AutoRereadMonitor,
    write_left_click,
)
from vision_client import VisionSupervisor
from cat_animation import GREETING_SECONDS
from cat_overlay import CatOverlayWindow
from controller import ProductController, default_recommendation_root
from recommendation_engine import RecommendationEngine
from strategy_store import StrategyStore
from agent_companion import HexRecommendationAgentSpeech
from agent_text import build_agent_provider
from overlay_config import load_agent_settings, load_voice_settings, update_overlay_config
from offline_speech import OfflineSpeechAdapter
from speech import SpeechMessage, SpeechService
from speech_policy import CompanionSpeechPolicy
from scoped_debug import SUPPORTED_DEBUG_SUBMODES, configure_debug_submodes, scoped_debug


_single_instance_handle: int | None = None
STARTUP_OCR_STATES = frozenset({
    "ocr_reading",
    "ocr_confirming",
    "ocr_updating",
    "ocr_error",
})


def _agent_context(
    snapshot: Mapping[str, Any],
    view: Mapping[str, Any],
) -> dict[str, object]:
    context: dict[str, object] = {
        "match_id": str(snapshot.get("match_id") or "")[:64],
        "offer_round": snapshot.get("offer_round"),
    }
    champion = str(snapshot.get("champion") or "").strip()
    if champion:
        context["champion"] = champion[:32]
    offer = snapshot.get("offer")
    if isinstance(offer, list):
        choices = [
            str(item.get("name") or "").strip()[:64]
            for item in offer[:3]
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        ]
        if choices:
            context["choices"] = choices
    selected = snapshot.get("selected")
    if isinstance(selected, list):
        context["selected_augments"] = [
            str(item.get("name") or "").strip()[:64]
            for item in selected[:4]
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        ]
    recommendation = view.get("recommendation")
    if isinstance(recommendation, Mapping):
        context["recommendation"] = {
            "augment": str(recommendation.get("augment") or "")[:64],
            "position": str(recommendation.get("position") or "")[:16],
        }
    return context


def _configure_logging() -> None:
    destination = log_path()
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            destination,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ]
    if not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-root", type=Path)
    parser.add_argument("--vision-exe", type=Path)
    parser.add_argument("--knowledge", type=Path)
    parser.add_argument(
        "--mode",
        choices=("KIWI", "KIWI_JADE"),
        default="KIWI",
    )
    parser.add_argument("--max-seconds", type=float, default=86400.0)
    parser.add_argument(
        "--window-title",
        default="League of Legends (TM) Client",
    )
    parser.add_argument("--width", type=int, default=420)
    parser.add_argument("--height", type=int, default=700)
    parser.add_argument(
        "--legacy-ui",
        action="store_true",
        help="use the diagnostic text window instead of the GameBuddy cat UI",
    )
    parser.add_argument(
        "--recommendation-data",
        type=Path,
        help="directory containing fun_builds/win_rates/kiwi_augments JSON",
    )
    parser.add_argument(
        "--debug-submode",
        action="append",
        choices=tuple(sorted(SUPPORTED_DEBUG_SUBMODES)),
        default=[],
        help="enable diagnostics for one feature without changing behavior",
    )
    return parser


class RecognitionApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._lock = threading.RLock()
        self._stopped = False
        self._clock = time.monotonic
        self._startup_greeting_until: float | None = None
        voice = load_voice_settings(overlay_config_path(), legacy_overlay_config_path())
        self.speech_policy = CompanionSpeechPolicy()
        self.diagnostics = OcrDiagnostics(ocr_diagnostics_path())
        champions = ChampionCatalog.load(champion_catalog_path())
        catalog = AugmentCatalog.load(args.knowledge or augment_catalog_path())
        self.model = RecognitionViewModel(
            catalog=catalog,
            store=HistoryStore(history_path()),
            champion_label=champions.label,
            champion_alias=champions.label_by_riot_id,
        )
        self.product: ProductController | None = None
        self.window = self._create_window(args, voice)
        self.poller = LcuChampSelectPoller(
            self._on_lcu,
            catalog=champions,
            # Fast custom ARAM lobbies can expose the final champion for only
            # a brief transition before GameStart.  Four polls per second is
            # still negligible for a loopback GET and avoids missing it.
            interval_seconds=0.25,
            provider=EnvironmentLcuConnectionProvider(
                root_resolver=lambda: resolve_league_root(self.args.league_root)
            ),
        )
        self.live_client = LiveClientPoller(
            self._on_live_client, interval_seconds=0.25
        )
        self.reread = AutoRereadMonitor(
            workspace=vision_workspace(),
            interval_seconds=AUTO_REREAD_INTERVAL_SECONDS,
            on_tick=self._on_reread_tick,
            should_signal=self._should_ocr,
        )
        self.vision = VisionSupervisor(
            exe=find_vision_exe(args.vision_exe),
            knowledge=args.knowledge or augment_catalog_path(),
            workspace=vision_workspace(),
            home=vision_home(),
            on_payload=self._on_vision,
            on_status=self._on_vision_status,
            completed_offers=self._completed_offers,
            champion=lambda: None,
            match_id=self._current_match_id,
            should_run=self._should_run_vision,
            should_ocr=self._should_ocr,
            window_title=args.window_title,
            mode=args.mode,
            max_seconds=args.max_seconds,
            capture_backend="wgc",
        )
        self.speech = SpeechService(
            OfflineSpeechAdapter(bundle_root=bundle_dir()),
            enabled=voice.enabled,
            clock=self._clock,
        )
        agent_settings = load_agent_settings(
            overlay_config_path(), legacy_overlay_config_path()
        )
        self.agent_speech = HexRecommendationAgentSpeech(
            build_agent_provider(agent_settings),
            self.speech.publish,
            clock=self._clock,
        )

    def _create_window(self, args: argparse.Namespace, voice: Any) -> Any:
        if args.legacy_ui:
            self._startup_greeting_until = 0.0
            return OverlayWindow(
                width=args.width,
                height=args.height,
                title="LoL 识别",
                text=self.model.render(),
                on_ready=self._start_workers,
                on_close=self.stop,
            )
        repository_root = bundle_dir()
        engine = RecommendationEngine.load(
            args.recommendation_data or default_recommendation_root(repository_root)
        )
        self.product = ProductController(
            engine=engine,
            store=StrategyStore(strategy_path()),
            configured_mode=args.mode,
            on_change=self._on_product_change,
        )
        return CatOverlayWindow(
            width=args.width,
            height=min(args.height, 340),
            cat_path=repository_root / "assets" / "gamebuddy-cat.png",
            animation_root=repository_root / "assets" / "gamebuddy",
            on_strategy=self._on_strategy,
            on_refresh=self._on_manual_refresh,
            on_tick=self._on_ui_tick,
            on_ready=self._start_workers,
            on_close=self.stop,
            on_voice_toggle=self._on_voice_toggle,
            on_greeting_start=self._on_greeting_start,
            voice_enabled=voice.enabled,
        )

    def _publish(self) -> None:
        if self.product is None:
            self.window.set_text(self.model.render())
            return
        snapshot = self.model.snapshot()
        view = self.product.present(snapshot)
        now = self._clock()
        presented = self._startup_presentation(view, now)
        self.window.set_view(presented)
        speech_view = self._speech_view(snapshot, presented)
        self._publish_speech(self.speech_policy.update(speech_view, now))
        self.agent_speech.update(
            presented,
            _agent_context(snapshot, presented),
        )

    def _speech_view(
        self,
        snapshot: Mapping[str, Any],
        presented: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if presented.get("state") != "champ_select" or self.product is None:
            return presented
        names = self.product.champion_select_speech_names(snapshot)
        if len(names) != 3:
            return presented
        return {**presented, "recommended_champions": names}

    def _startup_presentation(self, view: Mapping[str, Any], now: float) -> Mapping[str, Any]:
        if view.get("state") not in STARTUP_OCR_STATES:
            return view
        if self._startup_greeting_until is not None and now >= self._startup_greeting_until:
            return view
        return {
            "state": "waiting",
            "bubble_visible": False,
            "message": "核宝来了。",
            "options": [],
        }

    def _on_greeting_start(self, now: float) -> None:
        self._startup_greeting_until = now + GREETING_SECONDS
        self._publish_speech(self.speech_policy.startup(now))

    def _publish_speech(self, messages: tuple[SpeechMessage, ...]) -> None:
        for message in messages:
            self.speech.publish(message)

    def _on_voice_toggle(self, enabled: bool) -> None:
        update_overlay_config(overlay_config_path(), {"voice_enabled": enabled})
        self.speech.set_enabled(enabled)
        setter = getattr(self.window, "set_voice_enabled", None)
        if setter is not None:
            setter(enabled)

    def _on_strategy(self, strategy_id: str) -> None:
        with self._lock:
            if self.product is not None:
                accepted = self.product.select_strategy(strategy_id)
                snapshot = self.model.snapshot()
                logging.info(
                    "strategy selection accepted=%s strategy_id=%s match_id=%s phase=%s champion=%s",
                    accepted,
                    strategy_id,
                    snapshot.get("match_id"),
                    snapshot.get("phase"),
                    snapshot.get("champion"),
                )

    def _on_product_change(self) -> None:
        with self._lock:
            self._publish()

    def _on_ui_tick(self) -> None:
        # snapshot() evaluates the short detector visibility grace against the
        # current monotonic clock.  A lightweight UI tick guarantees the speech
        # bubble disappears even if the detector process stops immediately
        # after its final "not visible" frame.
        with self._lock:
            self._publish()
            self._publish_speech(self.speech_policy.tick(self._clock()))

    def _on_reread_tick(self) -> None:
        with self._lock:
            if self.model.mark_recognize_waiting():
                self._publish()

    def _on_manual_refresh(self) -> None:
        with self._lock:
            self.model.mark_left_click()
            write_left_click(vision_workspace())
            self._publish()

    def _on_lcu(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            context = event.get("context")
            self.model.apply_lcu(event)
            snapshot = self.model.snapshot()
            logging.info(
                "lcu state status=%s reason=%s phase=%s champion_id=%s match_id=%s champion=%s",
                event.get("status"),
                event.get("reason"),
                context.get("gameflowPhase") if isinstance(context, Mapping) else None,
                context.get("championId") if isinstance(context, Mapping) else None,
                snapshot.get("match_id"),
                snapshot.get("champion"),
            )
            if isinstance(context, Mapping) and context.get("gameflowPhase") in {
                "PreEndOfGame", "WaitingForStats", "EndOfGame"
            }:
                scoped_debug(
                    "game-result",
                    "phase=%s game_id=%s raw_status=%s normalized=%s endpoint_status=%s error=%s",
                    context.get("gameflowPhase"),
                    context.get("gameId"),
                    context.get("gameResultRaw"),
                    context.get("gameResult"),
                    context.get("gameResultEndpointStatus"),
                    context.get("gameResultEndpointError") or "none",
                )
            self._publish()

    def _on_vision(self, kind: str, payload: Mapping[str, Any]) -> None:
        commands: list[dict[str, Any]] = []
        with self._lock:
            source_match = payload.get("_vision_match_id")
            if source_match is not None and source_match != self.model.match_id:
                return
            if kind == "game_state":
                self.model.apply_game_state(payload)
            elif kind == "selection_observed":
                logging.info(
                    "vision selection_observed payload=%s",
                    json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
                    .replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"),
                )
                self.model.apply_selection_observed(payload)
            elif kind == "selection_confirmation_ack":
                logging.info("vision selection_confirmation_ack payload=%s",
                             json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
            elif kind == "click_ack":
                logging.info(
                    "vision click_ack reason=%s", payload.get("reason")
                )
                self.model.apply_click_ack(payload)
            elif kind == "frame_result":
                completed = self.model.completed_stage()
                self.diagnostics.record(payload, {
                    "match_id": self.model.match_id,
                    "champion": self.model.champion,
                    "phase": self.model.phase,
                    "game_mode": self.model.game_mode,
                    "live_level": self.model.live_level,
                    "live_is_dead": self.model.live_is_dead,
                    "confirmed_count": self.model.confirmed_count(),
                    "completed_stage": completed,
                    "expected_stage": completed + 1 if completed < 4 else None,
                    "offer_round": self.model.offer_round,
                    "mayhem_stage": self.model.mayhem_stage,
                    "mayhem_phase": self.model.mayhem_phase,
                })
                self.model.apply_frame_result(payload)
                commands = self.model.take_selection_commands()
            elif kind == "mayhem_selection_state":
                self.model.apply_mayhem_selection(payload)
            elif kind == "live_client_state":
                self._sync_vision_mode(payload)
                self.model.apply_live_client(payload)
            self._publish()
        # File I/O stays outside the model/UI lock. The supervisor validates
        # that the same match and child session still own this command.
        for command in commands:
            sent = self.vision.confirm_selection(command)
            logging.info("python OCR selection confirmation submitted=%s session=%s stage=%s",
                         sent, command.get("session_id"), command.get("offer_stage"))

    def _on_live_client(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self._sync_vision_mode(event)
            self.model.apply_live_client(event)
            self._publish()

    def _sync_vision_mode(self, event: Mapping[str, Any]) -> None:
        mode = str(event.get("gameMode") or "").strip().upper()
        if mode in {"KIWI", "KIWI_JADE"}:
            self.vision.set_mode(mode)

    def _on_vision_status(self, status: str, note: str | None) -> None:
        with self._lock:
            self.model.apply_vision_status(status, note)
            self._publish()

    def _completed_offers(self) -> int:
        with self._lock:
            return self.model.completed_stage()

    def _current_match_id(self) -> str:
        with self._lock:
            return self.model.match_id

    def _should_run_vision(self) -> bool:
        with self._lock:
            return self.model.vision_process_wanted()

    def _should_ocr(self) -> bool:
        with self._lock:
            return self.model.vision_allowed()

    def _start_workers(self) -> None:
        self.speech.preload()
        league = resolve_league_root(self.args.league_root)
        if league is None:
            self.model.apply_lcu(
                {
                    "status": "UNAVAILABLE",
                    "reason": "未找到客户端目录",
                }
            )
            self.model.note = (
                "未找到英雄联盟安装目录。请先打开国服客户端，"
                "或把目录写进 overlay.json 的 league_root。"
            )
            self._publish()
        else:
            logging.info("league root %s", league)
        self.poller.start()
        self.live_client.start()
        self.reread.start()
        self.vision.start()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self.agent_speech.close()
        self.speech.close()
        self.reread.stop()
        self.vision.stop()
        self.poller.stop()
        self.live_client.stop()
        self.diagnostics.close()

    def run(self) -> int:
        try:
            return self.window.run()
        finally:
            self.stop()


def _single_instance() -> bool:
    global _single_instance_handle
    if not os_name_nt():
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, True, "Local\\LoLRecognitionOverlay.Single")
    if not handle:
        logging.error("single-instance mutex failed error=%s", ctypes.get_last_error())
        return False
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)
        return False
    _single_instance_handle = int(handle)
    return True


def _release_single_instance() -> None:
    global _single_instance_handle
    handle = _single_instance_handle
    _single_instance_handle = None
    if not handle or not os_name_nt():
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)
    except Exception:
        return


def _keep_recognition_responsive() -> None:
    if not sys.platform.startswith("win"):
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    handle = kernel32.GetCurrentProcess()
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.SetPriorityClass.restype = ctypes.c_int
    kernel32.SetPriorityClass(handle, 0x00000020)

    class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.DWORD),
            ("ControlMask", wintypes.DWORD),
            ("StateMask", wintypes.DWORD),
        ]

    state = PROCESS_POWER_THROTTLING_STATE(1, 0x1, 0)
    kernel32.SetProcessInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetProcessInformation.restype = wintypes.BOOL
    kernel32.SetProcessInformation(handle, 4, ctypes.byref(state), ctypes.sizeof(state))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_debug_submodes(args.debug_submode)
    _configure_logging()
    _keep_recognition_responsive()
    if not _single_instance():
        logging.warning("another overlay instance is already running")
        _message_box("无法启动第二个识别窗口；请先关掉已经运行的窗口再重试。")
        return 2
    try:
        return RecognitionApp(args).run()
    except Exception:
        logging.exception("overlay failed")
        if os_name_nt():
            _message_box("LoL 识别启动失败，详见 overlay.log")
        return 1
    finally:
        _release_single_instance()


def os_name_nt() -> bool:
    return sys.platform.startswith("win")


def _message_box(text: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "LoL 识别", 0x10)
    except Exception:
        return


if __name__ == "__main__":
    raise SystemExit(main())
