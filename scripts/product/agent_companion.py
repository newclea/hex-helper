"""Generate Agent speech for each visible hex recommendation."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Mapping

from agent_text import AgentTextProvider
from speech import SpeechMessage, SpeechPriority
from speech_policy import recommendation_summary


LOGGER = logging.getLogger(__name__)
HEX_RECOMMENDATION_PROMPT = (
    "请根据当前英雄、三张候选海克斯和推荐结果，生成一句自然的中文陪玩口播。"
    "必须明确说出推荐的海克斯，只输出一句话，不要使用Markdown，不要解释。"
)
AGENT_SPEECH_LIFETIME_SECONDS = 30.0


def _submit_daemon(task: Callable[[], None]) -> None:
    threading.Thread(target=task, name="hex-recommendation-agent", daemon=True).start()


def _hex_prompt(context: Mapping[str, object]) -> str:
    champion = str(context.get("champion") or "当前英雄")
    choices = "、".join(str(item) for item in context.get("choices") or [])
    recommendation = context.get("recommendation")
    augment = ""
    if isinstance(recommendation, Mapping):
        augment = str(recommendation.get("augment") or "")
    detail = f"当前英雄：{champion}；候选：{choices}；推荐：{augment}。"
    return HEX_RECOMMENDATION_PROMPT + detail


class HexRecommendationAgentSpeech:
    def __init__(
        self,
        provider: AgentTextProvider,
        publish: Callable[[SpeechMessage], object],
        clock: Callable[[], float] = time.monotonic,
        submit: Callable[[Callable[[], None]], None] = _submit_daemon,
    ) -> None:
        self._provider = provider
        self._publish = publish
        self._clock = clock
        self._submit = submit
        self._lock = threading.Lock()
        self._active_key: str | None = None
        self._closed = False
        self._generation = 0

    def update(
        self,
        view: Mapping[str, Any],
        context: Mapping[str, object] | None = None,
    ) -> None:
        request = self._begin_request(view, context)
        if request is None:
            return
        generation, request_context, fallback = request
        try:
            self._submit(lambda: self._generate(generation, request_context, fallback))
        except Exception:
            LOGGER.exception("agent companion worker start failed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._active_key = None

    def _begin_request(
        self,
        view: Mapping[str, Any],
        context: Mapping[str, object] | None,
    ) -> tuple[int, dict[str, object], str] | None:
        state = str(view.get("state") or "")
        key = self._recommendation_key(view, context) if state == "recommendation" else None
        fallback = recommendation_summary(view) if key is not None else None
        with self._lock:
            if self._closed:
                return None
            if key is None or fallback is None:
                if self._active_key is not None:
                    self._generation += 1
                self._active_key = None
                return None
            if key == self._active_key:
                return None
            self._active_key = key
            self._generation += 1
            return self._generation, dict(context or {}), fallback

    @staticmethod
    def _recommendation_key(
        view: Mapping[str, Any],
        context: Mapping[str, object] | None,
    ) -> str | None:
        recommendation = view.get("recommendation")
        if not isinstance(recommendation, Mapping):
            return None
        augment = str(recommendation.get("augment") or "").strip()
        if not augment:
            return None
        values = context or {}
        return ":".join((
            str(values.get("match_id") or ""),
            str(values.get("offer_round") or ""),
            augment,
        ))

    def _generate(
        self,
        generation: int,
        context: Mapping[str, object],
        fallback: str,
    ) -> None:
        try:
            result = self._provider.generate(_hex_prompt(context), context)
        except Exception:
            LOGGER.exception("agent companion request failed error=internal_error")
            self._publish_if_current(generation, fallback, "local")
            return
        if not result.ok:
            LOGGER.warning(
                "agent companion request finished query_id=%s error=%s",
                result.query_id,
                result.error_code or "unknown",
            )
            self._publish_if_current(generation, fallback, "local")
            return
        if result.text.strip().upper() == "OFF":
            LOGGER.info("agent companion returned off query_id=%s", result.query_id)
            self._publish_if_current(generation, fallback, "local")
            return
        self._publish_if_current(generation, result.text, "agent")

    def _publish_if_current(self, generation: int, text: str, source: str) -> None:
        now = self._clock()
        message = SpeechMessage(
            message_id=f"hex-recommendation-{generation}",
            source=source,
            kind="hex_recommendation",
            summary=text,
            priority=SpeechPriority.HIGH,
            dedupe_key=f"hex-recommendation:{generation}",
            created_at=now,
            expires_at=now + AGENT_SPEECH_LIFETIME_SECONDS,
        )
        with self._lock:
            current = not self._closed and self._generation == generation
            if current:
                self._publish(message)
