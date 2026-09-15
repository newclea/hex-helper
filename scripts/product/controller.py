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
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.on_change = on_change
        self._last_snapshot: dict[str, Any] = {}
        self._strategy_id: str | None = None
        self._match_id: str | None = None
        self._hero: str | None = None

    def select_strategy(self, strategy_id: str) -> None:
        hero = str(self._last_snapshot.get("champion") or "").strip()
        match_id = str(self._last_snapshot.get("match_id") or "").strip()
        if not hero or not match_id:
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
        if match_id and match_id != self._match_id:
            self._match_id = match_id
            self._strategy_id = None
            self._hero = None
        if not hero:
            return {
                "state": "waiting",
                "message": "我会在选好英雄后，为你准备三种玩法。",
                "options": [],
            }
        if hero != self._hero:
            self._hero = hero
            self._strategy_id = None
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
        if self._strategy_id is None:
            return {
                "state": "choose_strategy",
                "message": f"{hero} 已确认，选一种本局推荐方式：",
                "options": [item.as_dict() for item in self.engine.strategy_options(hero)],
            }

        choices = snapshot.get("offer")
        selected = snapshot.get("selected")
        if isinstance(choices, list) and len(choices) == 3:
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
                return {
                    "state": "recommendation",
                    "message": (
                        f"推荐选「{recommendation.augment}」"
                        f"（{recommendation.position}）。\n{recommendation.reason}"
                    ),
                    "recommendation": recommendation.as_dict(),
                    "options": [],
                }
        count = len(selected) if isinstance(selected, list) else 0
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
