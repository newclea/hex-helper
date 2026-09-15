"""Turn OCR snapshots into product-level cat/bubble presentations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from recommendation_engine import RecommendationEngine
from strategy_store import StrategyStore


class ProductController:
    def __init__(
        self,
        *,
        engine: RecommendationEngine,
        store: StrategyStore,
        configured_mode: str = "KIWI",
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.on_change = on_change
        self.configured_mode = configured_mode
        self._last_snapshot: dict[str, Any] = {}
        self._strategy_id: str | None = None
        self._match_id: str | None = None
        self._hero: str | None = None
        self._champ_select_page = "advice"

    def select_strategy(self, strategy_id: str) -> None:
        hero = str(self._last_snapshot.get("champion") or "").strip()
        match_id = str(self._last_snapshot.get("match_id") or "").strip()
        if not hero or not match_id:
            return
        if (
            strategy_id == "__show_strategy__"
            and self._last_snapshot.get("phase") == "ChampSelect"
        ):
            self._champ_select_page = "strategy"
            if self.on_change is not None:
                self.on_change()
            return
        option = next(
            (
                item
                for item in self.engine.strategy_options(hero)
                if item.id == strategy_id and item.available
            ),
            None,
        )
        if option is None:
            return
        self._strategy_id = strategy_id
        self.store.save(match_id=match_id, hero=hero, strategy_id=strategy_id)
        if self.on_change is not None:
            self.on_change()

    def present(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        self._last_snapshot = dict(snapshot)
        hero = str(snapshot.get("champion") or "").strip()
        match_id = str(snapshot.get("match_id") or "").strip()
        phase = str(snapshot.get("phase") or "").strip()
        bench = [str(item) for item in snapshot.get("bench") or [] if str(item).strip()]
        if match_id and match_id != self._match_id:
            self._match_id = match_id
            self._strategy_id = None
            self._hero = None
            self._champ_select_page = "advice"
        if not hero and phase == "ChampSelect" and bench:
            selection_lines = self.engine.champion_select_recommendations(
                current_hero=None,
                bench=bench,
                seed=match_id,
            )
            return {
                "state": "champ_select",
                "message": "\n".join(selection_lines),
                "options": [],
            }
        if not hero:
            return {
                "state": "waiting",
                "message": "我会在选好英雄后，为你准备三种玩法。",
                "options": [],
            }
        if phase == "ChampSelect":
            selection_lines = self.engine.champion_select_recommendations(
                current_hero=hero,
                bench=bench,
                seed=match_id,
            )
        else:
            selection_lines = []
        if hero != self._hero:
            self._hero = hero
            self._strategy_id = None
            self._champ_select_page = "advice"
        persisted = self.store.load(match_id)
        if (
            self._strategy_id is None
            and persisted is not None
            and persisted.get("hero") == hero
        ):
            stored = persisted.get("strategy_id")
            available_ids = {
                option.id
                for option in self.engine.strategy_options(hero)
                if option.available
            }
            if isinstance(stored, str) and stored in available_ids:
                self._strategy_id = stored
        if phase != "ChampSelect":
            game_mode = str(snapshot.get("game_mode") or "").strip().upper()
            configured = self.configured_mode.upper()
            if configured not in {"KIWI", "KIWI_JADE"} or game_mode != "ARAM":
                detail = "正在确认对局模式" if not game_mode else f"当前模式为 {game_mode}"
                return {
                    "state": "unsupported_mode",
                    "message": f"{detail}，只有海克斯大乱斗才会给出推荐。",
                    "options": [],
                }

        if self._strategy_id is None:
            if phase == "ChampSelect" and self._champ_select_page == "advice":
                return {
                    "state": "champ_select_advice",
                    "message": "\n".join(selection_lines),
                    "options": [{
                        "id": "__show_strategy__",
                        "title": "选择本局推荐方式",
                        "subtitle": "查看胜率优先和两种趣味玩法",
                        "available": True,
                    }],
                }
            return {
                "state": "choose_strategy",
                "message": f"{hero} 已确认，选一种本局推荐方式：",
                "options": [item.as_dict() for item in self.engine.strategy_options(hero)],
            }

        choices = snapshot.get("offer")
        selected = snapshot.get("selected")
        if isinstance(choices, list) and 1 <= len(choices) <= 3:
            recommendation = self.engine.recommend(
                hero=hero,
                strategy_id=self._strategy_id,
                choices=[
                    {
                        "slot": str(item.get("slot") or ""),
                        "name": str(item.get("name") or ""),
                    }
                    for item in choices
                    if isinstance(item, Mapping)
                ],
                selected_augments=[
                    str(item.get("name") or "")
                    for item in selected or []
                    if isinstance(item, Mapping)
                ],
            )
            if recommendation is not None:
                incomplete = len(choices) < 3
                return {
                    "state": "recommendation",
                    "message": (
                        f"推荐选择「{recommendation.augment}」海克斯。"
                        f"\n{recommendation.reason}"
                        + (
                            "\n存在海克斯未准确识别，可点击猫咪刷新。"
                            if incomplete
                            else ""
                        )
                    ),
                    "recommendation": recommendation.as_dict(),
                    "refresh_available": incomplete,
                    "options": [],
                }
        count = len(selected) if isinstance(selected, list) else 0
        if phase == "ChampSelect":
            return {
                "state": "champ_select",
                "message": "\n".join(selection_lines),
                "options": [],
            }
        ocr_expected = snapshot.get("ocr_allowed") is True
        if phase != "ChampSelect" and not choices and ocr_expected:
            return {
                "state": "ocr_unavailable",
                "message": "哎呀，这轮还没看清海克斯呢，点击猫咪让我重新识别一次吧！",
                "refresh_available": True,
                "options": [],
            }
        return {
            "state": "in_game" if count else "strategy_ready",
            "message": (
                f"已记下 {count} 张海克斯，等待下一轮三选一。"
                if count
                else "方案已选好，刷出三张海克斯时我会告诉你推荐。"
            ),
            "options": [],
        }


def default_recommendation_root(repository_root: Path) -> Path:
    return repository_root / "data" / "recommendation"
