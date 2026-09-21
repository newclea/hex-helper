"""Pure message and scheduling primitives for GameBuddy speech."""

from __future__ import annotations

import heapq
import logging
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Protocol


LOGGER = logging.getLogger(__name__)


class SpeechPriority(IntEnum):
    LOW = 10
    NORMAL = 20
    HIGH = 30


@dataclass(frozen=True)
class SpeechMessage:
    message_id: str
    source: str
    kind: str
    summary: str
    priority: SpeechPriority
    dedupe_key: str
    created_at: float
    expires_at: float


class SpeechAdapter(Protocol):
    def start(self) -> bool:
        ...

    def speak(self, text: str) -> bool:
        ...

    def wait_finished(self, timeout: float | None = None) -> bool | None:
        ...

    def cancel(self) -> None:
        ...

    def close(self) -> None:
        ...


class SpeechQueue:
    def __init__(self, low_interval: float = 60.0) -> None:
        self._heap: list[tuple[int, int, SpeechMessage]] = []
        self._dedupe_expiry: dict[str, float] = {}
        self._sequence = 0
        self._last_low_dispatch: float | None = None
        self._low_interval = low_interval
        self._muted = False

    def publish(self, message: SpeechMessage, now: float) -> bool:
        self._prune_dedupe(now)
        if self._muted or message.expires_at <= now:
            return False
        if message.dedupe_key in self._dedupe_expiry:
            return False
        entry = (-int(message.priority), self._sequence, message)
        heapq.heappush(self._heap, entry)
        self._dedupe_expiry[message.dedupe_key] = message.expires_at
        self._sequence += 1
        return True

    def next(self, now: float) -> SpeechMessage | None:
        self._discard_expired(now)
        if self._muted or not self._heap:
            return None
        message = self._heap[0][2]
        if self._low_is_throttled(message, now):
            return None
        heapq.heappop(self._heap)
        if message.priority == SpeechPriority.LOW:
            self._last_low_dispatch = now
        return message

    def mute(self) -> None:
        self._muted = True
        self._heap = [entry for entry in self._heap if entry[2].priority != SpeechPriority.LOW]
        heapq.heapify(self._heap)

    def unmute(self) -> None:
        self._muted = False

    def _discard_expired(self, now: float) -> None:
        self._heap = [entry for entry in self._heap if entry[2].expires_at > now]
        heapq.heapify(self._heap)
        self._prune_dedupe(now)

    def _prune_dedupe(self, now: float) -> None:
        expired = [key for key, expires_at in self._dedupe_expiry.items() if expires_at <= now]
        for key in expired:
            del self._dedupe_expiry[key]

    def _low_is_throttled(self, message: SpeechMessage, now: float) -> bool:
        if message.priority != SpeechPriority.LOW or self._last_low_dispatch is None:
            return False
        return now - self._last_low_dispatch < self._low_interval


def should_interrupt(current: SpeechMessage, incoming: SpeechMessage) -> bool:
    return current.priority == SpeechPriority.LOW and incoming.priority == SpeechPriority.HIGH


class SpeechService:
    def __init__(
        self,
        adapter: SpeechAdapter,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter = adapter
        self._clock = clock
        self._queue = SpeechQueue()
        self._condition = threading.Condition()
        self._current: SpeechMessage | None = None
        self._closed = False
        self._enabled = enabled
        self._dispatch_generation = 0
        self._adapter_available: bool | None = None
        if not enabled:
            self._queue.mute()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def publish(self, message: SpeechMessage) -> bool:
        with self._condition:
            accepted = self._queue.publish(message, self._clock())
            if accepted and self._current is not None:
                if should_interrupt(self._current, message):
                    self._dispatch_generation += 1
                    self._cancel_adapter()
            if accepted:
                self._condition.notify()
            return accepted

    def set_enabled(self, enabled: bool) -> None:
        with self._condition:
            if enabled:
                self._enabled = True
                self._queue.unmute()
            else:
                self._enabled = False
                self._dispatch_generation += 1
                self._queue.mute()
                self._cancel_adapter()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._dispatch_generation += 1
            self._cancel_adapter()
            self._condition.notify_all()
        self._thread.join(timeout=2.0)
        self._close_adapter()

    def _run(self) -> None:
        while True:
            dispatch = self._next_message()
            if dispatch is None:
                return
            message, generation = dispatch
            if not self._ensure_adapter() or not self._dispatch_is_valid(message, generation):
                self._clear_current()
                continue
            if not self._speak(message.summary):
                self._clear_current()
                continue
            if not self._dispatch_is_valid(message, generation):
                self._cancel_adapter()
                self._clear_current()
                continue
            self._wait_for_speech()
            self._clear_current()

    def _next_message(self) -> tuple[SpeechMessage, int] | None:
        with self._condition:
            while not self._closed:
                message = self._queue.next(self._clock())
                if message is not None:
                    self._current = message
                    return message, self._dispatch_generation
                self._condition.wait(timeout=0.25)
            return None

    def _dispatch_is_valid(self, message: SpeechMessage, generation: int) -> bool:
        with self._condition:
            return (
                not self._closed
                and self._enabled
                and self._current is message
                and self._dispatch_generation == generation
            )

    def _ensure_adapter(self) -> bool:
        if self._adapter_available is None:
            try:
                self._adapter_available = self._adapter.start()
            except Exception:
                LOGGER.exception("speech adapter start failed")
                self._adapter_available = False
        return self._adapter_available

    def _wait_for_speech(self) -> None:
        while self._speech_completion() is None:
            with self._condition:
                if self._closed:
                    return

    def _clear_current(self) -> None:
        with self._condition:
            self._current = None

    def _speak(self, summary: str) -> bool:
        try:
            return self._adapter.speak(summary)
        except Exception:
            LOGGER.exception("speech adapter playback failed")
            return False

    def _speech_completion(self) -> bool | None:
        try:
            return self._adapter.wait_finished(timeout=0.1)
        except Exception:
            LOGGER.exception("speech adapter wait failed")
            return False

    def _cancel_adapter(self) -> None:
        try:
            self._adapter.cancel()
        except Exception:
            LOGGER.exception("speech adapter cancel failed")

    def _close_adapter(self) -> None:
        try:
            self._adapter.close()
        except Exception:
            LOGGER.exception("speech adapter close failed")
