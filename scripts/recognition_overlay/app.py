#!/usr/bin/env python3
"""Standalone topmost overlay: champ-select bench + hexcore recognition."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
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
from lcu_champ_select import LcuChampSelectPoller
from league_root import resolve_league_root
from overlay_window import OverlayWindow
from paths import (
    augment_catalog_path,
    champion_catalog_path,
    find_vision_exe,
    history_path,
    log_path,
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
from cat_overlay import CatOverlayWindow
from controller import ProductController, default_recommendation_root
from recommendation_engine import RecommendationEngine
from strategy_store import StrategyStore


def _ocr_raw_summary(debug: Any) -> str:
    if not isinstance(debug, Mapping):
        return "-"
    cards = debug.get("cards")
    if not isinstance(cards, list) or not cards:
        return "-"
    parts: list[str] = []
    for item in cards:
        if not isinstance(item, Mapping):
            continue
        slot = str(item.get("slot") or "?")
        raw = item.get("raw_text")
        text = " ".join(str(raw).split()) if raw is not None else ""
        if len(text) > 40:
            text = text[:40] + "…"
        parts.append(f"{slot}:{text or '空'}")
    return " | ".join(parts) if parts else "-"


def _configure_logging() -> None:
    destination = log_path()
    handlers: list[logging.Handler] = [
        logging.FileHandler(destination, encoding="utf-8"),
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
    return parser


class RecognitionApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._lock = threading.Lock()
        champions = ChampionCatalog.load(champion_catalog_path())
        catalog = AugmentCatalog.load(args.knowledge or augment_catalog_path())
        self.model = RecognitionViewModel(
            catalog=catalog,
            store=HistoryStore(history_path()),
            champion_label=champions.label,
            champion_alias=champions.label_by_riot_id,
        )
        self.product: ProductController | None = None
        if args.legacy_ui:
            self.window = OverlayWindow(
                width=args.width,
                height=args.height,
                title="LoL 识别",
                text=self.model.render(),
                on_ready=self._start_workers,
                on_close=self.stop,
            )
        else:
            repository_root = Path(__file__).resolve().parents[2]
            engine = RecommendationEngine.load(
                args.recommendation_data
                or default_recommendation_root(repository_root)
            )
            self.product = ProductController(
                engine=engine,
                store=StrategyStore(strategy_path()),
                configured_mode=args.mode,
                on_change=self._on_product_change,
            )
            self.window = CatOverlayWindow(
                width=args.width,
                height=min(args.height, 340),
                cat_path=repository_root / "assets" / "gamebuddy-cat.png",
                on_strategy=self.product.select_strategy,
                on_refresh=self._on_manual_refresh,
                on_ready=self._start_workers,
                on_close=self.stop,
            )
        self.poller = LcuChampSelectPoller(
            self._on_lcu,
            catalog=champions,
            interval_seconds=1.0,
        )
        self.live_client = LiveClientPoller(
            self._on_live_client, interval_seconds=0.25
        )
        self.reread = AutoRereadMonitor(
            workspace=vision_workspace(),
            interval_seconds=AUTO_REREAD_INTERVAL_SECONDS,
            on_tick=self._on_reread_tick,
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
            should_run=self._should_run_vision,
            should_ocr=self._should_ocr,
            window_title=args.window_title,
            mode=args.mode,
            max_seconds=args.max_seconds,
            capture_backend="wgc",
        )

    def _publish(self) -> None:
        if self.product is None:
            self.window.set_text(self.model.render())
            return
        self.window.set_view(self.product.present(self.model.snapshot()))

    def _on_product_change(self) -> None:
        with self._lock:
            self._publish()

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
            self.model.apply_lcu(event)
            self._publish()

    def _on_vision(self, kind: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            if kind == "game_state":
                self.model.apply_game_state(payload)
            elif kind == "selection_observed":
                logging.info(
                    "vision selection_observed source=%s slot=%s id=%s",
                    payload.get("source"),
                    payload.get("selected_slot"),
                    payload.get("selected_augment_id"),
                )
                self.model.apply_selection_observed(payload)
            elif kind == "click_ack":
                logging.info(
                    "vision click_ack reason=%s", payload.get("reason")
                )
                self.model.apply_click_ack(payload)
            elif kind == "frame_result":
                reason = payload.get("reason")
                if reason not in {
                    "duplicate_offer",
                    "same_content_already_processed",
                    "awaiting_ocr_consensus:1/2",
                }:
                    logging.info(
                        "vision frame_result cause=%s reason=%s raw=%s",
                        payload.get("reread_cause"),
                        reason,
                        _ocr_raw_summary(payload.get("recognition_debug")),
                    )
                self.model.apply_frame_result(payload)
            elif kind == "mayhem_selection_state":
                self.model.apply_mayhem_selection(payload)
            elif kind == "live_client_state":
                self.model.apply_live_client(payload)
            self._publish()

    def _on_live_client(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self.model.apply_live_client(event)
            self._publish()

    def _on_vision_status(self, status: str, note: str | None) -> None:
        with self._lock:
            self.model.apply_vision_status(status, note)
            self._publish()

    def _completed_offers(self) -> int:
        with self._lock:
            return self.model.confirmed_count()

    def _should_run_vision(self) -> bool:
        with self._lock:
            return self.model.vision_process_wanted()

    def _should_ocr(self) -> bool:
        with self._lock:
            return self.model.vision_allowed()

    def _start_workers(self) -> None:
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
        self.reread.stop()
        self.poller.stop()
        self.live_client.stop()
        self.vision.stop()

    def run(self) -> int:
        try:
            return self.window.run()
        finally:
            self.stop()


def _single_instance() -> bool:
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
        return True
    if ctypes.get_last_error() == 183:
        return False
    return True


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
    _configure_logging()
    _keep_recognition_responsive()
    args = _parser().parse_args(argv)
    if not _single_instance():
        logging.warning("another overlay instance is already running")
        _message_box("已经有一个识别窗口在运行，请先关掉旧窗口再开这个。")
        return 2
    try:
        return RecognitionApp(args).run()
    except Exception:
        logging.exception("overlay failed")
        if os_name_nt():
            _message_box("LoL 识别启动失败，详见 overlay.log")
        return 1


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
