"""Pure message and scheduling primitives for GameBuddy speech."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol


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

    def wait_finished(self, timeout: float | None = None) -> bool:
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
