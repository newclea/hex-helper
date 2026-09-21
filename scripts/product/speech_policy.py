"""Convert visible product states into concise, semantic speech messages."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Protocol

from speech import SpeechMessage, SpeechPriority


OCR_STATES = frozenset({"ocr_reading", "ocr_confirming", "ocr_updating"})
POSITION_TOKEN = re.compile(r"\s*[（(](?:LEFT|CENTER|RIGHT)[）)]\s*$", re.IGNORECASE)
NORMAL_SUMMARIES = {
    "champ_select": "正在为你查看可选英雄。",
    "waiting": "核宝已准备好，正在读取本局英雄。",
    "unsupported_mode": "当前模式暂不支持海克斯推荐。",
    "in_game": "已记下当前选择，等待下一轮海克斯。",
}
HIGH_SUMMARIES = {
    "ocr_error": "这次识别没有成功，可以点击猫咪重试。",
    "recommendation_unavailable": "三张海克斯已识别，但当前推荐数据不足。",
}


class CompanionMessageSource(Protocol):
    def start(self, publish: Callable[[SpeechMessage], None]) -> None:
        ...

    def close(self) -> None:
        ...


class CompanionSpeechPolicy:
    def __init__(self) -> None:
        self._last_key: str | None = None
        self._ocr_started_at: float | None = None
        self._ocr_announced = False
        self._sequence = 0

    def update(self, view: Mapping[str, Any], now: float) -> tuple[SpeechMessage, ...]:
        state = str(view.get("state") or "")
        if view.get("bubble_visible") is False:
            state = ""
            self._last_key = None
        self._update_ocr_state(state, now)
        summary, priority, semantic = self._describe(view, state)
        if summary is None or semantic == self._last_key:
            return ()
        self._last_key = semantic
        return (self._message(state, summary, priority, semantic, now),)

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
        if state == "recommendation":
            recommendation = POSITION_TOKEN.sub("", self._block_value(view, "当前推荐")).strip()
            plan = self._block_value(view, "当前玩法").strip() or "胜率优先"
            if recommendation:
                summary = f"推荐选择{recommendation}，当前玩法{plan}。"
                return summary, SpeechPriority.HIGH, f"recommendation:{recommendation}:{plan}"
        if state in HIGH_SUMMARIES:
            return HIGH_SUMMARIES[state], SpeechPriority.HIGH, state
        if state in NORMAL_SUMMARIES:
            return NORMAL_SUMMARIES[state], SpeechPriority.NORMAL, state
        return None, SpeechPriority.NORMAL, state

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
