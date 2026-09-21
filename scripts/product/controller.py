"""Turn OCR snapshots into switchable, per-offer cat recommendations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from recommendation_engine import MODE_WIN_RATE, SLOTS, RecommendationEngine
from strategy_store import StrategyStore


LOGGER = logging.getLogger(__name__)
IN_GAME_PHASES = frozenset({"GameStart", "InProgress", "Reconnect"})
SUPPORTED_GAME_MODES = frozenset({"ARAM", "KIWI", "KIWI_JADE"})


class ProductController:
    def __init__(
        self, *, engine: RecommendationEngine, store: StrategyStore,
        configured_mode: str = "KIWI", on_change: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.on_change = on_change
        self.configured_mode = configured_mode
        self._last_snapshot: dict[str, Any] = {}
        self._strategy_id: str | None = None
        self._match_id: str | None = None
        self._hero: str | None = None
        self._last_recommendation_key: tuple[Any, ...] | None = None
        self.last_selection_result: dict[str, Any] | None = None

    def _notify_change(self) -> None:
        if self.on_change is not None:
            self.on_change()

    @staticmethod
    def _choices(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
        items = snapshot.get("offer")
        if not isinstance(items, list) or len(items) != 3:
            return []
        cards = [{"slot": str(item.get("slot") or ""),
                  "name": str(item.get("name") or "").strip()}
                 for item in items if isinstance(item, Mapping)]
        if len(cards) != 3 or {card["slot"] for card in cards} != set(SLOTS):
            return []
        return cards if all(card["name"] for card in cards) else []

    @staticmethod
    def _selected(snapshot: Mapping[str, Any]) -> list[str]:
        return [str(item.get("name") or "") for item in snapshot.get("selected") or []
                if isinstance(item, Mapping) and item.get("name")]

    def _options(self, snapshot: Mapping[str, Any]):
        return self.engine.strategy_options(
            self._hero or "", choices=self._choices(snapshot),
            selected_augments=self._selected(snapshot),
        )

    def _save_preference(self) -> bool:
        try:
            self.store.save(match_id=self._match_id or "", hero=self._hero or "",
                            strategy_id=self._strategy_id or MODE_WIN_RATE)
            return True
        except (OSError, ValueError):
            LOGGER.exception("recommendation preference save failed match_id=%s hero=%s",
                             self._match_id, self._hero)
            return False

    def _sync_context(self, snapshot: Mapping[str, Any]) -> None:
        match_id = str(snapshot.get("match_id") or "").strip()
        observed = str(snapshot.get("champion") or "").strip()
        phase = str(snapshot.get("phase") or "").strip()
        changed = False
        if match_id != self._match_id:
            previous_id = str(snapshot.get("previous_match_id") or "").strip()
            migrating = bool(previous_id and previous_id == self._match_id)
            saved = self.store.load(match_id) if match_id else None
            if saved is None and migrating:
                saved = self.store.load(previous_id)
            self._match_id = match_id
            if not migrating:
                self._hero = None
                self._strategy_id = None
                self.last_selection_result = None
            if isinstance(saved, Mapping):
                self._hero = str(saved.get("hero") or "").strip() or self._hero
                self._strategy_id = str(saved.get("strategy_id") or "").strip() or None
            changed = migrating
        # Only a real champion-select swap changes an established hero;
        # differing Live Client aliases cannot replace it during the match.
        if observed and (self._hero is None or (phase == "ChampSelect" and observed != self._hero)):
            self._hero = observed
            self._strategy_id = MODE_WIN_RATE
            self.last_selection_result = None
            changed = True
        # Remember the hero without requiring a champion-select plan choice.
        if changed and self._match_id and self._hero:
            self._save_preference()

    def select_strategy(self, strategy_id: str) -> bool:
        snapshot = self._last_snapshot
        phase = str(snapshot.get("phase") or "")
        feedback = snapshot.get("ocr_feedback")
        error = isinstance(feedback, Mapping) and feedback.get("state") == "ocr_error"
        valid_context = (
            phase in IN_GAME_PHASES and snapshot.get("offer_visible") is True
            and self._match_id and self._hero and self._choices(snapshot) and not error
            and snapshot.get("offer_refreshing") is not True
            and self.configured_mode.upper() in {"KIWI", "KIWI_JADE"}
            and str(snapshot.get("game_mode") or "").upper() in SUPPORTED_GAME_MODES
        )
        option = next((item for item in self._options(snapshot)
                       if item.id == strategy_id and item.available), None) if valid_context else None
        if option is None:
            self.last_selection_result = {
                "ok": False, "reason": "offer_changed" if not valid_context else "strategy_unavailable",
                "strategy_id": strategy_id, "match_id": self._match_id, "hero": self._hero,
            }
            self._notify_change()
            return False
        self._strategy_id = strategy_id
        # A tab switch is usable even if saving the browsing preference fails.
        # It never writes a selected augment into OCR history.
        persisted = self._save_preference()
        self.last_selection_result = {
            "ok": True, "reason": "selected" if persisted else "selected_not_saved",
            "strategy_id": strategy_id, "match_id": self._match_id, "hero": self._hero,
        }
        self._notify_change()
        return True

    def present(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        self._last_snapshot = dict(snapshot)
        self._sync_context(snapshot)
        view = self._present(snapshot)
        phase = str(snapshot.get("phase") or "").strip()
        visible = phase == "ChampSelect" or (
            phase in IN_GAME_PHASES and snapshot.get("offer_visible") is True
        )
        view["bubble_visible"] = visible
        if not visible:
            self._last_recommendation_key = None
            view["options"] = []
            view["refresh_available"] = False
        return view

    @staticmethod
    def _message_blocks(recommendation: Any, plan: Any, rows: list[str]) -> list[dict[str, str]]:
        blocks = [
            {
                "label": "当前推荐",
                "value": f"{recommendation.augment}（{recommendation.position}）",
            },
            {"label": "当前玩法", "value": plan.name if plan is not None else "胜率优先"},
            {
                "label": "所需海克斯",
                "value": "、".join(plan.augments) if plan is not None else "无固定组合",
            },
            {
                "label": "所需装备",
                "value": (
                    "、".join(plan.equipment) or "无固定出装要求"
                    if plan is not None else "无固定出装要求"
                ),
            },
        ]
        prefixes = ("当前推荐 ", "当前玩法 ", "所需海克斯 ", "所需装备 ")
        blocks.extend({"text": row} for row in rows if not row.startswith(prefixes))
        return blocks

    def _present(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        phase = str(snapshot.get("phase") or "").strip()
        if phase == "ChampSelect":
            lines = self.engine.champion_select_recommendations(
                current_hero=str(snapshot.get("champion") or "").strip() or None,
                bench=[str(item) for item in snapshot.get("bench") or [] if str(item).strip()],
                seed=self._match_id or "",
            )
            return {
                "state": "champ_select",
                "message": "\n".join(lines) if lines else "正在读取可选英雄，稍后为你推荐~",
                "options": [],
            }
        if not self._hero:
            return {"state": "waiting", "message": "正在读取本局英雄。海克斯出现后可切换查看推荐。", "options": []}
        mode = str(snapshot.get("game_mode") or "").strip().upper()
        if self.configured_mode.upper() not in {"KIWI", "KIWI_JADE"} or mode not in SUPPORTED_GAME_MODES:
            detail = "正在确认对局模式" if not mode else f"当前模式为 {mode}"
            return {"state": "unsupported_mode", "message": f"{detail}，只有海克斯大乱斗才会给出推荐。", "options": []}
        visible = phase in IN_GAME_PHASES and snapshot.get("offer_visible") is True
        if not visible:
            count = len(self._selected(snapshot))
            return {"state": "in_game", "message": f"已记下 {count} 张海克斯，等待下一轮三选一。", "options": []}
        feedback = snapshot.get("ocr_feedback")
        if isinstance(feedback, Mapping) and feedback.get("state") == "ocr_error":
            return {"state": "ocr_error", "message": str(feedback.get("message") or "本轮结果处理失败，可点击猫咪重试，详情已记入日志。"),
                    "refresh_available": True, "options": []}
        if snapshot.get("offer_refreshing") is True:
            detail = str(feedback.get("message") or "") if isinstance(feedback, Mapping) else ""
            return {"state": "ocr_updating", "message": detail or "海克斯候选已变化，正在重新确认三张名字。",
                    "refresh_available": True, "options": []}
        choices = self._choices(snapshot)
        if not choices:
            state = str(feedback.get("state")) if isinstance(feedback, Mapping) else "ocr_reading"
            message = str(feedback.get("message") or "") if isinstance(feedback, Mapping) else ""
            return {"state": state if state in {"ocr_reading", "ocr_confirming"} else "ocr_reading",
                    "message": message or "看到海克斯卡片了，正在确认三张名字。可点击猫咪重试。",
                    "refresh_available": True, "options": []}
        options = self._options(snapshot)
        available = {option.id for option in options if option.available}
        if self._strategy_id not in available:
            self._strategy_id = MODE_WIN_RATE if MODE_WIN_RATE in available else next(
                (option.id for option in options if option.available), None)
            self.last_selection_result = None
        tabs = [{**option.as_dict(), "selected": option.id == self._strategy_id} for option in options]
        recommendation = self.engine.recommend(
            hero=self._hero, strategy_id=self._strategy_id or "", choices=choices,
            selected_augments=self._selected(snapshot),
        )
        if recommendation is None:
            return {"state": "recommendation_unavailable", "message": "已读到三张海克斯，当前英雄的推荐数据不足。",
                    "options": tabs, "active_strategy_id": self._strategy_id, "refresh_available": False}
        plan = self.engine.strategy_plan(self._hero, self._strategy_id or "")
        rows = [f"当前推荐 {recommendation.augment}（{recommendation.position}）"]
        if plan is not None:
            rows += [f"当前玩法 {plan.name}",
                     f"所需海克斯 {'、'.join(plan.augments)}",
                     f"所需装备 {'、'.join(plan.equipment) or '暂无装备资料'}"]
            if recommendation.matched_by == "win_rate_fallback":
                rows.append("本轮未抽到搭配，先按胜率推荐。" if recommendation.win_rate is not None
                            else "本轮未抽到搭配，胜率资料也不足；当前仅为备选。")
        else:
            rows += ["所需海克斯 无固定组合", "所需装备 无固定出装要求"]
            rows.append(f"该海克斯胜率 {recommendation.win_rate:g}%" if recommendation.win_rate is not None
                        else "本轮三张暂无胜率资料；当前仅为备选。")
        message_blocks = self._message_blocks(recommendation, plan, rows)
        message = "\n".join(rows)
        if self.last_selection_result and self.last_selection_result.get("reason") == "selected_not_saved":
            warning = "已切换推荐，但未能保存浏览偏好。"
            message += "\n" + warning
            message_blocks.append({"text": warning})
        log_key = (
            self._match_id, snapshot.get("offer_round"), self._hero,
            tuple((card["slot"], card["name"]) for card in choices),
            tuple(self._selected(snapshot)), tuple(option.id for option in options),
            self._strategy_id, recommendation.augment,
        )
        if log_key != self._last_recommendation_key:
            LOGGER.info(
                "recommendation view match=%s round=%s hero=%s selected_augments=%s "
                "tabs=%s active=%s recommended=%s matched_by=%s",
                self._match_id, snapshot.get("offer_round"), self._hero,
                self._selected(snapshot), [option.title for option in options],
                self._strategy_id, recommendation.augment, recommendation.matched_by,
            )
            self._last_recommendation_key = log_key
        return {
            "state": "recommendation", "message": message,
            "message_blocks": message_blocks,
            "introduction": f"针对{self.engine.hero_display_name(self._hero)}，有下面几套玩法可供选择哟~",
            "recommendation": recommendation.as_dict(), "refresh_available": False,
            "options": tabs, "active_strategy_id": self._strategy_id,
        }


def default_recommendation_root(repository_root: Path) -> Path:
    return repository_root / "data" / "recommendation"
