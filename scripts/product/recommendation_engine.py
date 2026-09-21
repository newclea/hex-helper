"""Deterministic recommendation logic backed by the shipped Chinese pools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SLOTS = ("LEFT", "CENTER", "RIGHT")
SLOT_LABELS = {"LEFT": "左侧", "CENTER": "中间", "RIGHT": "右侧"}
MODE_WIN_RATE = "win_rate"
MODE_FUN_PREFIX = "fun:"


def normalize_name(value: object) -> str:
    """Normalize OCR/data-source punctuation without guessing translations."""

    text = str(value or "").strip().casefold()
    return re.sub(r"[\s·・•.。'’‘\-—_]+", "", text)


def split_augments(value: object) -> tuple[str, ...]:
    parts = re.split(r"[、,，+]", str(value or ""))
    return tuple(part.strip() for part in parts if part.strip())


def _percentage(value: object) -> float | None:
    text = str(value or "").strip()
    if not text.endswith("%"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None


@dataclass(frozen=True)
class StrategyOption:
    id: str
    title: str
    subtitle: str
    available: bool
    plan_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "available": self.available,
            "plan_id": self.plan_id,
        }


@dataclass(frozen=True)
class FunPlan:
    id: str
    hero: str
    hero_id: str
    name: str
    augments: tuple[str, ...]
    equipment: tuple[str, ...]
    rating: str
    updated_on: str
    source: str
    source_url: str

    @property
    def core_augments(self) -> tuple[str, ...]:
        # The source pool is ordered but has no explicit core flag. For the MVP,
        # the first two entries are the required/core path and the rest are
        # compatible extensions. Keeping this in one property makes a future
        # explicit `核心海克斯` field a local migration.
        return self.augments[:2]

    @property
    def synergy_augments(self) -> tuple[str, ...]:
        return self.augments[2:]


@dataclass(frozen=True)
class Recommendation:
    augment: str
    slot: str
    position: str
    reason: str
    matched_by: str
    win_rate: float | None
    strategy_id: str
    plan_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "augment": self.augment,
            "slot": self.slot,
            "position": self.position,
            "reason": self.reason,
            "matched_by": self.matched_by,
            "win_rate": self.win_rate,
            "strategy_id": self.strategy_id,
            "plan_id": self.plan_id,
        }


class RecommendationDataError(ValueError):
    pass


class RecommendationEngine:
    def __init__(
        self,
        *,
        fun_builds: Sequence[Mapping[str, Any]],
        win_rows: Sequence[Mapping[str, Any]],
        augments: Sequence[Mapping[str, Any]],
    ) -> None:
        enabled = {
            normalize_name(item.get("name")): str(item.get("name"))
            for item in augments
            if item.get("enabled") is True and normalize_name(item.get("name"))
        }
        if not enabled:
            raise RecommendationDataError("knowledge pool has no enabled augments")
        self._enabled_augments = enabled
        self._win_by_hero: dict[str, dict[str, Mapping[str, Any]]] = {}
        self._hero_win_rates: dict[str, float] = {}
        self._hero_labels: dict[str, str] = {}
        self._hero_aliases: dict[str, str] = {}
        for row in win_rows:
            hero = str(row.get("英雄名") or "").strip()
            if not hero:
                continue
            hero_key = normalize_name(hero)
            self._register_hero_aliases(hero, hero_key)
            self._hero_labels.setdefault(hero_key, hero)
            hero_rate = _percentage(row.get("英雄胜率"))
            if hero_rate is not None:
                self._hero_win_rates[hero_key] = hero_rate
            augment = str(row.get("海克斯名称") or "").strip()
            rate = _percentage(row.get("海克斯胜率"))
            if not augment or rate is None:
                continue
            self._win_by_hero.setdefault(hero_key, {})[normalize_name(augment)] = row

        raw_plans: list[FunPlan] = []
        for index, row in enumerate(fun_builds):
            hero = str(row.get("英雄名") or "").strip()
            name = str(row.get("趣味玩法名称") or "").strip()
            hero_id = str(row.get("英雄ID") or "").strip()
            augments_in_plan = tuple(
                augment
                for augment in split_augments(row.get("海克斯"))
                if normalize_name(augment) in enabled
            )
            if not hero or not name or not augments_in_plan:
                continue
            plan = FunPlan(
                id=f"fun-{hero_id or normalize_name(hero)}-{index + 1}",
                hero=hero,
                hero_id=hero_id,
                name=name,
                augments=augments_in_plan,
                equipment=split_augments(row.get("装备")),
                rating=str(row.get("评级") or "A").strip().upper(),
                updated_on=str(row.get("趣味玩法更新时间") or "").strip(),
                source=str(row.get("来源") or "").strip(),
                source_url=str(row.get("来源链接") or "").strip(),
            )
            raw_plans.append(plan)
            self._register_hero_aliases(hero, normalize_name(hero))
            self._hero_labels.setdefault(self._resolve_hero_key(hero), hero)

        self._plans_by_hero: dict[str, list[FunPlan]] = {}
        seen: set[tuple[str, str]] = set()
        for plan in sorted(raw_plans, key=self._plan_sort_key):
            hero_key = self._resolve_hero_key(plan.hero)
            dedupe_key = (hero_key, normalize_name(plan.name))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            self._plans_by_hero.setdefault(hero_key, []).append(plan)

    @classmethod
    def load(cls, root: Path) -> "RecommendationEngine":
        try:
            fun = json.loads((root / "fun_builds.zh-CN.json").read_text("utf-8"))
            win = json.loads((root / "win_rates.zh-CN.json").read_text("utf-8"))
            knowledge = json.loads(
                (root / "kiwi_augments.zh-CN.json").read_text("utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RecommendationDataError(f"cannot load recommendation data: {error}") from error
        if not isinstance(fun, list):
            raise RecommendationDataError("fun pool must be an array")
        if not isinstance(win, Mapping) or not isinstance(win.get("rows"), list):
            raise RecommendationDataError("win-rate pool must contain rows")
        if not isinstance(knowledge, Mapping) or not isinstance(
            knowledge.get("augments"), list
        ):
            raise RecommendationDataError("knowledge pool must contain augments")
        return cls(
            fun_builds=fun,
            win_rows=win["rows"],
            augments=knowledge["augments"],
        )

    @staticmethod
    def _plan_sort_key(plan: FunPlan) -> tuple[int, int, str, str]:
        try:
            ordinal = -date.fromisoformat(plan.updated_on).toordinal()
        except ValueError:
            ordinal = 0
        return (0 if plan.rating == "S" else 1, ordinal, plan.name, plan.id)

    def _register_hero_aliases(self, hero: str, canonical: str) -> None:
        compact = normalize_name(hero)
        if not compact:
            return
        self._hero_aliases.setdefault(compact, canonical)
        parts = hero.split()
        if len(parts) > 1:
            # Recommendation rows prefix the champion title to the display
            # name used by the client catalog.  Most display names are a
            # single token, but names such as "烈娜塔 · 戈拉斯克" are not.
            # Register the whole suffix as well as the final token so both
            # forms resolve to the same recommendation pool.
            self._hero_aliases.setdefault(
                normalize_name(" ".join(parts[1:])), canonical
            )
            self._hero_aliases.setdefault(normalize_name(parts[-1]), canonical)

    def _resolve_hero_key(self, hero: str) -> str:
        key = normalize_name(hero)
        return self._hero_aliases.get(key, key)

    @staticmethod
    def _normalize_choices(
        choices: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, str]] | None:
        if not 1 <= len(choices) <= 3:
            return None
        cards: list[dict[str, str]] = []
        seen_slots: set[str] = set()
        for item in choices:
            slot = str(item.get("slot") or "")
            name = str(item.get("name") or "").strip()
            if slot not in SLOTS or slot in seen_slots or not name:
                return None
            seen_slots.add(slot)
            cards.append({"slot": slot, "name": name})
        return cards

    def strategy_options(
        self,
        hero: str,
        *,
        choices: Sequence[Mapping[str, Any]] = (),
        selected_augments: Sequence[str] = (),
    ) -> list[StrategyOption]:
        """Choose two plans from the whole pool for this offer and history.

        Any plan that can recommend an unowned card in the current offer comes
        before plans that would fall back to win rate. History overlap then
        favors a path the player has already started; names break ties without
        depending on source rating, update date, or input order.
        """

        hero_key = self._resolve_hero_key(hero)
        selected = {normalize_name(item) for item in selected_augments}
        offered = {
            normalize_name(card["name"])
            for card in self._normalize_choices(choices) or []
        } - selected

        def offer_plan_key(plan: FunPlan) -> tuple[bool, int, str, str]:
            augments = {normalize_name(name) for name in plan.augments}
            return (
                not bool(augments & offered),
                -len(augments & selected),
                plan.name,
                plan.id,
            )

        options = [
            StrategyOption(
                id=MODE_WIN_RATE,
                title="胜率流",
                subtitle=(
                    "每轮推荐三张中该英雄胜率最高的一张"
                    if hero_key in self._win_by_hero
                    else "当前英雄暂无可靠胜率数据"
                ),
                available=hero_key in self._win_by_hero,
            )
        ]
        plans = sorted(
            self._plans_by_hero.get(hero_key, []), key=offer_plan_key
        )[:2]
        for plan in plans:
            options.append(StrategyOption(
                id=f"{MODE_FUN_PREFIX}{plan.id}", title=plan.name,
                subtitle=f"核心：{' + '.join(plan.core_augments)}",
                available=True, plan_id=plan.id,
            ))
        return options

    def champion_select_recommendations(
        self,
        *,
        current_hero: str | None,
        bench: Sequence[str],
        seed: str,
    ) -> list[str]:
        """Show the known win-rate leader, then up to three distinct fun heroes.

        ``seed`` remains accepted for callers using the previous interface;
        recommendation order is deterministic and independent of it.
        """

        candidates: list[str] = []
        candidate_labels: dict[str, str] = {}
        seen: set[str] = set()
        for hero in ([current_hero] if current_hero else []) + list(bench):
            key = self._resolve_hero_key(str(hero or ""))
            if key and key not in seen:
                seen.add(key)
                candidates.append(key)
                candidate_labels[key] = str(hero).strip()
        if not candidates:
            return []

        def label(key: str) -> str:
            return self.hero_display_name(candidate_labels[key])

        ranked = sorted(
            candidates,
            key=lambda key: (
                -len(self._plans_by_hero.get(key, [])),
                key not in self._hero_win_rates,
                -self._hero_win_rates.get(key, 0.0),
                label(key),
            ),
        )
        lines: list[str] = []
        known = [key for key in candidates if key in self._hero_win_rates]
        winner = min(known, key=lambda key: (-self._hero_win_rates[key], label(key))) if known else None
        if winner is not None:
            rate = f"{self._hero_win_rates[winner]:.2f}".rstrip("0").rstrip(".")
            scope = "胜率最高" if len(known) == len(candidates) else "已知胜率最高"
            lines.append(f"{label(winner)}胜率有{rate}%，可选英雄里{scope}，追求取胜优选！")
        fun_candidates = [key for key in ranked if key != winner and self._plans_by_hero.get(key)][:3]
        for index, key in enumerate(fun_candidates):
            plans = sorted(
                self._plans_by_hero.get(key, []),
                key=lambda plan: (plan.name, plan.id),
            )
            examples = "和".join(f"[{plan.name}]" for plan in plans[:2])
            ending = ("新玩法，可以试试~", "创意，值得一试~", "玩法，欢乐对局快开始咯！")[index]
            lines.append(f"{label(key)}有{examples}{ending}")
        # With a single useful candidate, retain its fun paths without a
        # duplicate hero line or the previous lengthy core explanations.
        if winner is not None and not fun_candidates:
            plans = sorted(self._plans_by_hero.get(winner, []), key=lambda plan: (plan.name, plan.id))
            if plans:
                examples = "和".join(f"[{plan.name}]" for plan in plans[:2])
                lines[0] += f"也有{examples}玩法~"
        if not lines:
            lines.append("可选英雄暂无可靠的胜率或趣味玩法资料，选喜欢的英雄吧~")
        return lines

    def hero_display_name(self, hero: str) -> str:
        label = self._hero_labels.get(self._resolve_hero_key(hero), hero)
        parts = label.split(maxsplit=1)
        return parts[-1] if parts else hero

    def strategy_plan(self, hero: str, strategy_id: str) -> FunPlan | None:
        """Return the complete documented requirements, not only the core pair."""
        return self._plan(hero, strategy_id)

    def _plan(self, hero: str, strategy_id: str) -> FunPlan | None:
        if not strategy_id.startswith(MODE_FUN_PREFIX):
            return None
        plan_id = strategy_id[len(MODE_FUN_PREFIX) :]
        return next(
            (
                plan
                for plan in self._plans_by_hero.get(self._resolve_hero_key(hero), [])
                if plan.id == plan_id
            ),
            None,
        )

    def _win_rate(self, hero: str, augment: str) -> tuple[float | None, int, int]:
        row = self._win_by_hero.get(self._resolve_hero_key(hero), {}).get(
            normalize_name(augment)
        )
        if row is None:
            return None, 0, 1_000_000
        return (
            _percentage(row.get("海克斯胜率")),
            int(row.get("海克斯场次") or 0),
            int(row.get("海克斯胜率排名") or 1_000_000),
        )

    def _best_by_win_rate(
        self, hero: str, choices: Sequence[Mapping[str, str]]
    ) -> tuple[Mapping[str, str], float | None]:
        ranked = sorted(
            choices,
            key=lambda card: (
                -(self._win_rate(hero, card["name"])[0] or -1.0),
                -self._win_rate(hero, card["name"])[1],
                self._win_rate(hero, card["name"])[2],
                SLOTS.index(card["slot"]),
            ),
        )
        card = ranked[0]
        return card, self._win_rate(hero, card["name"])[0]

    def recommend(
        self,
        *,
        hero: str,
        strategy_id: str,
        choices: Sequence[Mapping[str, str]],
        selected_augments: Sequence[str] = (),
    ) -> Recommendation | None:
        normalized_cards = self._normalize_choices(choices)
        if normalized_cards is None:
            return None
        plan = self._plan(hero, strategy_id)
        if strategy_id == MODE_WIN_RATE:
            card, rate = self._best_by_win_rate(hero, normalized_cards)
            scope = "这三张" if len(normalized_cards) == 3 else "已识别候选"
            reason = (
                f"它是{scope}中当前英雄胜率最高的选择（{rate:.2f}%）。"
                if rate is not None
                else (
                    f"胜率池暂无{scope}的有效数据，"
                    f"临时选择「{card['name']}」海克斯。"
                )
            )
            return Recommendation(
                augment=card["name"],
                slot=card["slot"],
                position=SLOT_LABELS[card["slot"]],
                reason=reason,
                matched_by="win_rate",
                win_rate=rate,
                strategy_id=strategy_id,
                plan_id=None,
            )
        if plan is None:
            return None

        selected = {normalize_name(item) for item in selected_augments}
        priority = {
            normalize_name(name): index for index, name in enumerate(plan.augments)
        }
        matching = [
            card
            for card in normalized_cards
            if normalize_name(card["name"]) in priority
            and normalize_name(card["name"]) not in selected
        ]
        if matching:
            card = min(
                matching,
                key=lambda item: (
                    priority[normalize_name(item["name"])],
                    SLOTS.index(item["slot"]),
                ),
            )
            index = priority[normalize_name(card["name"])]
            matched_by = "core" if index < len(plan.core_augments) else "synergy"
            label = "核心海克斯" if matched_by == "core" else "体系搭配"
            return Recommendation(
                augment=card["name"],
                slot=card["slot"],
                position=SLOT_LABELS[card["slot"]],
                reason=f"它是「{plan.name}」的{label}，优先补齐当前玩法。",
                matched_by=matched_by,
                win_rate=self._win_rate(hero, card["name"])[0],
                strategy_id=strategy_id,
                plan_id=plan.id,
            )

        card, rate = self._best_by_win_rate(hero, normalized_cards)
        if rate is not None:
            fallback = f"这轮先按胜率推荐。该海克斯胜率：{rate:.2f}%。"
        else:
            fallback = (
                "本轮也暂无有效胜率数据，"
                f"暂以「{card['name']}」作为备选。"
            )
        return Recommendation(
            augment=card["name"],
            slot=card["slot"],
            position=SLOT_LABELS[card["slot"]],
            reason=(
                f"「{plan.name}」的搭配海克斯这轮没有抽到。{fallback}"
            ),
            matched_by="win_rate_fallback",
            win_rate=rate,
            strategy_id=strategy_id,
            plan_id=plan.id,
        )
