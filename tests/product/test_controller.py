from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from controller import ProductController
from recommendation_engine import RecommendationEngine
from strategy_store import StrategyStore


class ProductControllerTextTests(unittest.TestCase):
    def test_recommendation_keeps_message_and_adds_blocks(self) -> None:
        engine = RecommendationEngine.load(Path("data/recommendation"))
        with TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine,
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            view = controller.present({
                "phase": "InProgress",
                "match_id": "test-match",
                "champion": "万花通灵 妮蔻",
                "game_mode": "KIWI",
                "offer_visible": True,
                "offer": [
                    {"slot": "LEFT", "name": "亮出你的剑"},
                    {"slot": "CENTER", "name": "巨人杀手"},
                    {"slot": "RIGHT", "name": "掷骰狂人"},
                ],
                "selected": [],
            })
        self.assertIn("当前推荐", view["message"])
        self.assertEqual("当前推荐", view["message_blocks"][0]["label"])
        self.assertTrue(view["message_blocks"][0]["value"])
        self.assertEqual("当前玩法", view["message_blocks"][1]["label"])
        self.assertTrue(any("胜率" in block.get("text", "") for block in view["message_blocks"]))

    def test_waiting_view_does_not_add_structured_blocks(self) -> None:
        engine = RecommendationEngine.load(Path("data/recommendation"))
        with TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine,
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            view = controller.present({"phase": "InProgress", "match_id": "waiting"})
        self.assertNotIn("message_blocks", view)


if __name__ == "__main__":
    unittest.main()
