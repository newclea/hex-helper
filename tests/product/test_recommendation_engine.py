from __future__ import annotations

from pathlib import Path
import sys
import unittest


PRODUCT = Path(__file__).resolve().parents[2] / "scripts" / "product"
sys.path.insert(0, str(PRODUCT))

from recommendation_engine import RecommendationEngine


def engine() -> RecommendationEngine:
    return RecommendationEngine(
        augments=[
            {"name": "核心甲", "enabled": True},
            {"name": "核心乙", "enabled": True},
            {"name": "搭配丙", "enabled": True},
            {"name": "稳健丁", "enabled": True},
            {"name": "禁用戊", "enabled": False},
        ],
        win_rows=[
            {"英雄名": "测试英雄 小明", "英雄胜率": "55.00%", "海克斯名称": "核心甲", "海克斯胜率": "51.00%", "海克斯场次": 100, "海克斯胜率排名": 3},
            {"英雄名": "测试英雄 小明", "英雄胜率": "55.00%", "海克斯名称": "搭配丙", "海克斯胜率": "52.00%", "海克斯场次": 100, "海克斯胜率排名": 2},
            {"英雄名": "测试英雄 小明", "英雄胜率": "55.00%", "海克斯名称": "稳健丁", "海克斯胜率": "60.00%", "海克斯场次": 100, "海克斯胜率排名": 1},
        ],
        fun_builds=[
            {"英雄名": "测试英雄 小明", "英雄ID": "1", "趣味玩法名称": "测试流", "海克斯": "核心甲、核心乙、搭配丙、禁用戊", "装备": "", "评级": "S", "趣味玩法更新时间": "2026-09-01", "来源": "测试", "来源链接": "https://example.com"},
        ],
    )


class RecommendationEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = engine()
        self.choices = [
            {"slot": "LEFT", "name": "核心甲"},
            {"slot": "CENTER", "name": "搭配丙"},
            {"slot": "RIGHT", "name": "稳健丁"},
        ]

    def test_strategy_options_do_not_invent_second_fun_plan(self) -> None:
        options = self.engine.strategy_options("小明")
        self.assertEqual(3, len(options))
        self.assertTrue(options[1].available)
        self.assertFalse(options[2].available)

    def test_win_rate_mode_chooses_highest_rate(self) -> None:
        result = self.engine.recommend(
            hero="小明", strategy_id="win_rate", choices=self.choices
        )
        self.assertIsNotNone(result)
        self.assertEqual("稳健丁", result.augment)
        self.assertEqual("RIGHT", result.slot)

    def test_partial_ocr_chooses_best_recognized_card(self) -> None:
        result = self.engine.recommend(
            hero="小明",
            strategy_id="win_rate",
            choices=[{"slot": "CENTER", "name": "搭配丙"}],
        )
        self.assertIsNotNone(result)
        self.assertEqual("搭配丙", result.augment)

    def test_champion_select_includes_win_and_fun_lines(self) -> None:
        lines = self.engine.champion_select_recommendations(
            current_hero="小明", bench=[], seed="match-1"
        )
        self.assertIn("胜率推荐", lines[0])
        self.assertTrue(any("测试流" in line for line in lines))

    def test_fun_mode_prioritizes_core(self) -> None:
        strategy = self.engine.strategy_options("小明")[1].id
        result = self.engine.recommend(
            hero="小明", strategy_id=strategy, choices=self.choices
        )
        self.assertIsNotNone(result)
        self.assertEqual("核心甲", result.augment)
        self.assertEqual("core", result.matched_by)

    def test_fun_mode_uses_synergy_after_owned_core(self) -> None:
        strategy = self.engine.strategy_options("小明")[1].id
        result = self.engine.recommend(
            hero="小明",
            strategy_id=strategy,
            choices=self.choices,
            selected_augments=["核心甲"],
        )
        self.assertIsNotNone(result)
        self.assertEqual("搭配丙", result.augment)
        self.assertEqual("synergy", result.matched_by)

    def test_fun_mode_falls_back_to_win_rate_with_explanation(self) -> None:
        strategy = self.engine.strategy_options("小明")[1].id
        choices = [
            {"slot": "LEFT", "name": "未知一"},
            {"slot": "CENTER", "name": "稳健丁"},
            {"slot": "RIGHT", "name": "未知二"},
        ]
        result = self.engine.recommend(
            hero="小明", strategy_id=strategy, choices=choices
        )
        self.assertIsNotNone(result)
        self.assertEqual("稳健丁", result.augment)
        self.assertEqual("win_rate_fallback", result.matched_by)
        self.assertIn("没有抽到", result.reason)


if __name__ == "__main__":
    unittest.main()
