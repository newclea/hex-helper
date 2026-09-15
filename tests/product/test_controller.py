from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


PRODUCT = Path(__file__).resolve().parents[2] / "scripts" / "product"
sys.path.insert(0, str(PRODUCT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from controller import ProductController
from strategy_store import StrategyStore
from test_recommendation_engine import engine


class ProductControllerTest(unittest.TestCase):
    def test_champ_select_advice_is_paginated_before_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recommendation_engine = engine()
            recommendation_engine.champion_select_recommendations = (
                lambda **_kwargs: ["建议一", "建议二", "建议三", "建议四"]
            )
            controller = ProductController(
                engine=recommendation_engine,
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            snapshot = {
                "match_id": "match-pages",
                "champion": "小明",
                "phase": "ChampSelect",
                "bench": [],
            }
            first = controller.present(snapshot)
            self.assertEqual("建议一\n建议二", first["message"])
            self.assertEqual("__next_advice__", first["options"][0]["id"])
            controller.select_strategy("__next_advice__")
            second = controller.present(snapshot)
            self.assertEqual("建议三\n建议四", second["message"])
            self.assertEqual("__show_strategy__", second["options"][0]["id"])

    def test_snapshot_flow_uses_real_selected_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine(),
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            snapshot = {
                "match_id": "match-1",
                "champion": "小明",
                "phase": "ChampSelect",
                "bench": [],
                "game_mode": None,
                "offer": [],
                "selected": [],
            }
            choosing = controller.present(snapshot)
            self.assertEqual("champ_select_advice", choosing["state"])
            controller.select_strategy("__show_strategy__")
            choosing = controller.present(snapshot)
            self.assertEqual("choose_strategy", choosing["state"])
            strategy = choosing["options"][1]["id"]
            controller.select_strategy(strategy)
            snapshot["phase"] = "InProgress"
            snapshot["game_mode"] = "ARAM"
            snapshot["offer"] = [
                {"slot": "LEFT", "name": "核心甲"},
                {"slot": "CENTER", "name": "搭配丙"},
                {"slot": "RIGHT", "name": "稳健丁"},
            ]
            recommendation = controller.present(snapshot)
            self.assertEqual("recommendation", recommendation["state"])
            self.assertIn("选择「核心甲」海克斯", recommendation["message"])
            self.assertNotIn("左侧", recommendation["message"])
            snapshot["offer"] = []
            snapshot["selected"] = [{"name": "核心甲"}]
            snapshot["mayhem_status"] = "DEATH_TRIGGERED"
            snapshot["ocr_allowed"] = True
            waiting = controller.present(snapshot)
            self.assertEqual("ocr_unavailable", waiting["state"])
            self.assertIn("点击猫咪", waiting["message"])

            snapshot["champion"] = "另一个英雄"
            changed = controller.present(snapshot)
            self.assertEqual("choose_strategy", changed["state"])

    def test_running_vision_does_not_imply_ocr_is_expected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine(),
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            snapshot = {
                "match_id": "match-gate",
                "champion": "小明",
                "phase": "ChampSelect",
                "bench": [],
                "game_mode": None,
                "offer": [],
                "selected": [],
            }
            controller.present(snapshot)
            controller.select_strategy("__show_strategy__")
            choosing = controller.present(snapshot)
            controller.select_strategy(choosing["options"][0]["id"])
            snapshot.update({
                "phase": "InProgress",
                "game_mode": "ARAM",
                "vision_status": "识别中",
                "ocr_allowed": False,
                "selected": [{"name": "核心甲"}],
            })
            waiting = controller.present(snapshot)
            self.assertEqual("in_game", waiting["state"])
            snapshot["ocr_allowed"] = True
            unavailable = controller.present(snapshot)
            self.assertEqual("ocr_unavailable", unavailable["state"])

    def test_non_aram_mode_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine(),
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            view = controller.present(
                {
                    "match_id": "match-2",
                    "champion": "小明",
                    "phase": "InProgress",
                    "game_mode": "CLASSIC",
                    "offer": [],
                    "selected": [],
                }
            )
            self.assertEqual("unsupported_mode", view["state"])


if __name__ == "__main__":
    unittest.main()
