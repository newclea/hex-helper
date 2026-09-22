"""Convert visible product states into concise, semantic speech messages."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Protocol

from speech import SpeechMessage, SpeechPriority
from scoped_debug import scoped_debug


OCR_STATES = frozenset({"ocr_reading", "ocr_confirming", "ocr_updating"})
POSITION_TOKEN = re.compile(r"\s*[（(](?:LEFT|CENTER|RIGHT)[）)]\s*$", re.IGNORECASE)
NORMAL_SUMMARIES = {
    "waiting": "核宝已准备好，正在读取本局英雄。",
    "unsupported_mode": "当前模式暂不支持海克斯推荐。",
    "in_game": "已记下当前选择，等待下一轮海克斯。",
}
STARTUP_GREETING = (
    "召唤师你好，我是你的联盟专属陪玩悠米！"
    "快去开启一场紧张刺激的海克斯大乱斗吧。"
)
HIGH_SUMMARIES = {
    "ocr_error": "这次识别没有成功，可以点击猫咪重试。",
    "recommendation_unavailable": "三张海克斯已识别，但当前推荐数据不足。",
}


def recommendation_summary(view: Mapping[str, Any]) -> str | None:
    recommendation = POSITION_TOKEN.sub(
        "", CompanionSpeechPolicy._block_value(view, "当前推荐")
    ).strip()
    plan = CompanionSpeechPolicy._block_value(view, "当前玩法").strip() or "胜率优先"
    if not recommendation:
        return None
    return f"推荐选择{recommendation}，当前玩法{plan}。"


class CompanionMessageSource(Protocol):
    def start(self, publish: Callable[[SpeechMessage], None]) -> None:
        ...

    def close(self) -> None:
        ...


class CompanionSpeechPolicy:
    def __init__(self) -> None:
        self._last_key: str | None = None
        self._recommendation_active = False
        self._ocr_started_at: float | None = None
        self._ocr_announced = False
        self._sequence = 0
        self._startup_announced = False
        self._last_game_result_key: str | None = None

    def startup(self, now: float) -> tuple[SpeechMessage, ...]:
        if self._startup_announced:
            return ()
        self._startup_announced = True
        return (
            self._message(
                "startup",
                STARTUP_GREETING,
                SpeechPriority.NORMAL,
                "startup",
                now,
            ),
        )

    def update(self, view: Mapping[str, Any], now: float) -> tuple[SpeechMessage, ...]:
        game_result = self._game_result_message(view, now)
        if game_result:
            return game_result
        state = str(view.get("state") or "")
        if view.get("bubble_visible") is False:
            state = ""
            self._last_key = None
        self._update_ocr_state(state, now)
        summary, priority, semantic = self._describe(view, state)
        is_recommendation = state == "recommendation" and summary is not None
        starts_recommendation = is_recommendation and not self._recommendation_active
        self._recommendation_active = is_recommendation
        if summary is None or (semantic == self._last_key and not starts_recommendation):
            return ()
        self._last_key = semantic
        dedupe_key = semantic
        if is_recommendation:
            dedupe_key = f"{semantic}:occurrence:{self._sequence + 1}"
        return (self._message(state, summary, priority, dedupe_key, now),)

    def tick(self, now: float) -> tuple[SpeechMessage, ...]:
        if self._ocr_started_at is None or self._ocr_announced:
            return ()
        if now - self._ocr_started_at <= 2.0:
            return ()
        self._ocr_announced = True
        message = self._message(
            "ocr_progress",
            "正在识别海克斯，请稍候。",
            SpeechPriority.NORMAL,
            "ocr_progress",
            now,
        )
        return (message,)

    def _update_ocr_state(self, state: str, now: float) -> None:
        if state in OCR_STATES:
            if self._ocr_started_at is None:
                self._ocr_started_at = now
                self._ocr_announced = False
            return
        self._ocr_started_at = None
        self._ocr_announced = False

    def _describe(
        self,
        view: Mapping[str, Any],
        state: str,
    ) -> tuple[str | None, SpeechPriority, str]:
        if state == "champ_select":
            recommendations = view.get("recommended_champions")
            if isinstance(recommendations, list) and len(recommendations) == 3:
                names = [str(item).strip() for item in recommendations]
                if all(names):
                    summary = f"根据当前英雄强度，推荐选择{'、'.join(names)}三个英雄哦。"
                    return summary, SpeechPriority.NORMAL, f"champ_select:{':'.join(names)}"
        if state in HIGH_SUMMARIES:
            return HIGH_SUMMARIES[state], SpeechPriority.HIGH, state
        if state in NORMAL_SUMMARIES:
            return NORMAL_SUMMARIES[state], SpeechPriority.NORMAL, state
        return None, SpeechPriority.NORMAL, state

    def _game_result_message(
        self,
        view: Mapping[str, Any],
        now: float,
    ) -> tuple[SpeechMessage, ...]:
        result = str(view.get("game_result") or "").upper()
        match_id = str(view.get("match_id") or "").strip()
        if result not in {"WIN", "LOSS"} or not match_id:
            return ()
        key = f"game_result:{match_id}:{result}"
        if key == self._last_game_result_key:
            return ()
        self._last_game_result_key = key
        summary = "耶，赢啦！" if result == "WIN" else "惜败惜败，再开一局吧。"
        scoped_debug("game-result", "match_id=%s result=%s announced=false", match_id, result)
        return (self._message("game_result", summary, SpeechPriority.HIGH, key, now),)

    @staticmethod
    def _block_value(view: Mapping[str, Any], label: str) -> str:
        blocks = view.get("message_blocks")
        if not isinstance(blocks, list):
            return ""
        for block in blocks:
            if isinstance(block, Mapping) and block.get("label") == label:
                return str(block.get("value") or "")
        return ""

    def _message(
        self,
        kind: str,
        summary: str,
        priority: SpeechPriority,
        semantic: str,
        now: float,
    ) -> SpeechMessage:
        self._sequence += 1
        return SpeechMessage(
            message_id=f"local-{self._sequence}",
            source="local",
            kind=kind,
            summary=summary,
            priority=priority,
            dedupe_key=semantic,
            created_at=now,
            expires_at=now + 30.0,
        )
