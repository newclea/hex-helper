"""Generate one disposable Agent speech message per champion-select entry."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Mapping

from agent_text import AgentTextProvider
from speech import SpeechMessage, SpeechPriority


LOGGER = logging.getLogger(__name__)
CHAMPION_SELECT_PROMPT = (
    "请随机生成一句适合英雄联盟选英雄阶段播报的轻松鼓励语。"
    "只输出一句中文，不超过三十个汉字，不要使用Markdown，不要解释。"
)
AGENT_SPEECH_LIFETIME_SECONDS = 30.0


def _submit_daemon(task: Callable[[], None]) -> None:
    threading.Thread(target=task, name="champ-select-agent", daemon=True).start()


class ChampionSelectAgentSpeech:
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
        self._inside_champion_select = False
        self._closed = False
        self._generation = 0

    def update(
        self,
        state: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        request = self._begin_request(state, context)
        if request is None:
            return
        generation, request_context = request
        try:
            self._submit(lambda: self._generate(generation, request_context))
        except Exception:
            LOGGER.warning("agent companion worker start failed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._inside_champion_select = False

    def _begin_request(
        self,
        state: str,
        context: Mapping[str, object] | None,
    ) -> tuple[int, dict[str, object]] | None:
        with self._lock:
            if self._closed:
                return None
            if state != "champ_select":
                if self._inside_champion_select:
                    self._generation += 1
                self._inside_champion_select = False
                return None
            if self._inside_champion_select:
                return None
            self._inside_champion_select = True
            self._generation += 1
            return self._generation, dict(context or {})

    def _generate(self, generation: int, context: Mapping[str, object]) -> None:
        try:
            result = self._provider.generate(CHAMPION_SELECT_PROMPT, context)
        except Exception:
            LOGGER.warning("agent companion request failed error=internal_error")
            return
        if not result.ok:
            LOGGER.info(
                "agent companion request finished query_id=%s error=%s",
                result.query_id,
                result.error_code or "unknown",
            )
            return
        now = self._clock()
        message = SpeechMessage(
            message_id=f"agent-champ-select-{generation}",
            source="agent",
            kind="champ_select_agent",
            summary=result.text,
            priority=SpeechPriority.LOW,
            dedupe_key=f"agent:champ_select:{generation}",
            created_at=now,
            expires_at=now + AGENT_SPEECH_LIFETIME_SECONDS,
        )
        with self._lock:
            current = (
                not self._closed
                and self._inside_champion_select
                and self._generation == generation
            )
            if current:
                self._publish(message)
