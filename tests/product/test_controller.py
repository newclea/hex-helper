from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from controller import ProductController
from recommendation_engine import RecommendationEngine
from strategy_store import StrategyStore


class ProductControllerTextTests(unittest.TestCase):
    def test_champion_select_exposes_same_structured_names_as_bubble_source(self) -> None:
        engine = Mock()
        engine.champion_select_recommendations.return_value = ["妮蔻推荐", "亚索推荐", "盖伦推荐"]
        engine.champion_select_recommendation_names.return_value = ["妮蔻", "亚索", "盖伦"]
        with TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine,
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            view = controller.present({
                "phase": "ChampSelect",
                "match_id": "lcu:1",
                "champion": "万花通灵 妮蔻",
                "bench": ["封魔剑魂 永恩", "疾风剑豪 亚索", "德玛西亚之力 盖伦"],
            })

        self.assertEqual(["妮蔻", "亚索", "盖伦"], view["recommended_champions"])
        self.assertEqual("妮蔻推荐\n亚索推荐\n盖伦推荐", view["message"])

    def test_real_champion_recommendations_are_limited_to_three_visible_names(self) -> None:
        engine = RecommendationEngine.load(Path("data/recommendation"))
        with TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine,
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            view = controller.present({
                "phase": "ChampSelect",
                "match_id": "lcu:1",
                "champion": "万花通灵 妮蔻",
                "bench": ["疾风剑豪 亚索", "德玛西亚之力 盖伦", "封魔剑魂 永恩"],
            })

        names = view["recommended_champions"]
        self.assertEqual(3, len(names))
        self.assertTrue(all(name in view["message"] for name in names))

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

    def test_hidden_offer_ignores_stale_ocr_error(self) -> None:
        engine = RecommendationEngine.load(Path("data/recommendation"))
        with TemporaryDirectory() as directory:
            controller = ProductController(
                engine=engine,
                store=StrategyStore(Path(directory) / "strategy.json"),
            )
            view = controller.present({
                "phase": "InProgress",
                "match_id": "hidden-offer",
                "champion": "万花通灵 妮蔻",
                "game_mode": "KIWI",
                "offer_visible": False,
                "ocr_feedback": {"state": "ocr_error"},
            })
        self.assertFalse(view["bubble_visible"])
        self.assertEqual([], view["options"])
        self.assertNotEqual("ocr_error", view["state"])


if __name__ == "__main__":
    unittest.main()
