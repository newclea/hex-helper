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
    def test_snapshot_flow_uses_real_selected_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine(),
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            snapshot = {
                "match_id": "match-1",
                "champion": "小明",
                "offer": [],
                "selected": [],
            }
            choosing = controller.present(snapshot)
            self.assertEqual("choose_strategy", choosing["state"])
            strategy = choosing["options"][1]["id"]
            controller.select_strategy(strategy)
            snapshot["offer"] = [
                {"slot": "LEFT", "name": "核心甲"},
                {"slot": "CENTER", "name": "搭配丙"},
                {"slot": "RIGHT", "name": "稳健丁"},
            ]
            recommendation = controller.present(snapshot)
            self.assertEqual("recommendation", recommendation["state"])
            self.assertIn("左侧", recommendation["message"])
            snapshot["offer"] = []
            snapshot["selected"] = [{"name": "核心甲"}]
            waiting = controller.present(snapshot)
            self.assertEqual("in_game", waiting["state"])
            self.assertIn("1 张", waiting["message"])

            snapshot["champion"] = "另一个英雄"
            changed = controller.present(snapshot)
            self.assertEqual("choose_strategy", changed["state"])


if __name__ == "__main__":
    unittest.main()
