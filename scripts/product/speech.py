"""Pure message and scheduling primitives for GameBuddy speech."""

from __future__ import annotations

import heapq
import logging
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Protocol

from scoped_debug import scoped_debug


LOGGER = logging.getLogger(__name__)
DEFAULT_START_TIMEOUT_SECONDS = 45.0
DEFAULT_PLAYBACK_TIMEOUT_SECONDS = 30.0
PLAYBACK_POLL_SECONDS = 0.1


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

    def wait_started(self, timeout: float | None = None) -> bool | None:
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

    def discard_kind(self, kind: str) -> None:
        self._heap = [entry for entry in self._heap if entry[2].kind != kind]
        heapq.heapify(self._heap)

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
    if incoming.kind == "game_result" and current.priority < incoming.priority:
        return True
    return current.priority == SpeechPriority.LOW and incoming.priority == SpeechPriority.HIGH


class SpeechService:
    def __init__(
        self,
        adapter: SpeechAdapter,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
        start_timeout_seconds: float = DEFAULT_START_TIMEOUT_SECONDS,
        playback_timeout_seconds: float = DEFAULT_PLAYBACK_TIMEOUT_SECONDS,
    ) -> None:
        self._adapter = adapter
        self._clock = clock
        self._queue = SpeechQueue()
        self._condition = threading.Condition()
        self._current: SpeechMessage | None = None
        self._closed = False
        self._enabled = enabled
        self._start_timeout_seconds = max(0.01, start_timeout_seconds)
        self._playback_timeout_seconds = max(0.01, playback_timeout_seconds)
        self._dispatch_generation = 0
        self._adapter_available: bool | None = None
        self._adapter_start_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
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
                    self._cancel_adapter("high_priority_interrupt")
            if accepted:
                LOGGER.info(
                    "speech queued id=%s kind=%s priority=%s",
                    message.message_id,
                    message.kind,
                    message.priority.name,
                )
                scoped_debug(
                    "speech", "queue accepted id=%s kind=%s priority=%s chars=%d",
                    message.message_id, message.kind, message.priority.name, len(message.summary),
                )
                self._condition.notify()
            else:
                scoped_debug(
                    "speech", "queue rejected id=%s kind=%s enabled=%s",
                    message.message_id, message.kind, self._enabled,
                )
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
                self._cancel_adapter("voice_disabled")
            self._condition.notify_all()
        if enabled:
            self.preload()

    def cancel_kind(self, kind: str) -> None:
        with self._condition:
            self._queue.discard_kind(kind)
            if self._current is not None and self._current.kind == kind:
                self._dispatch_generation += 1
                self._cancel_adapter("state_no_longer_active")
                self._current = None
                self._condition.notify_all()

    def preload(self) -> None:
        with self._condition:
            if self._closed or not self._enabled:
                return
        self._start_preload()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._dispatch_generation += 1
            self._cancel_adapter("service_close")
            self._condition.notify_all()
        self._thread.join(timeout=2.0)
        self._close_adapter()
        if self._preload_thread is not None:
            self._preload_thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            dispatch = self._next_message()
            if dispatch is None:
                return
            message, generation = dispatch
            scoped_debug(
                "speech", "dispatch id=%s kind=%s generation=%d",
                message.message_id, message.kind, generation,
            )
            if not self._ensure_adapter() or not self._dispatch_is_valid(message, generation):
                scoped_debug(
                    "speech", "dispatch dropped id=%s kind=%s adapter_available=%s",
                    message.message_id, message.kind, self._adapter_available,
                )
                self._clear_current()
                continue
            if not self._speak(message.summary):
                scoped_debug("speech", "command failed id=%s kind=%s", message.message_id, message.kind)
                self._clear_current()
                continue
            if not self._dispatch_is_valid(message, generation):
                self._cancel_adapter("dispatch_invalidated")
                self._clear_current()
                continue
            if self._wait_for_speech(message):
                LOGGER.info("speech finished id=%s kind=%s", message.message_id, message.kind)
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
        with self._adapter_start_lock:
            if self._adapter_available is None:
                started_at = time.monotonic()
                scoped_debug("speech", "adapter start requested")
                try:
                    self._adapter_available = self._adapter.start()
                except Exception:
                    LOGGER.exception("speech adapter start failed")
                    self._adapter_available = False
                scoped_debug(
                    "speech", "adapter start finished available=%s elapsed_ms=%d",
                    self._adapter_available,
                    int((time.monotonic() - started_at) * 1000),
                )
            return self._adapter_available

    def _start_preload(self) -> None:
        if self._adapter_available is not None:
            return
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return
        self._preload_thread = threading.Thread(target=self._ensure_adapter, daemon=True)
        self._preload_thread.start()

    def _wait_for_speech(self, message: SpeechMessage) -> bool:
        wait_started_at = time.monotonic()
        deadline = wait_started_at + self._start_timeout_seconds
        scoped_debug(
            "speech", "waiting start id=%s kind=%s timeout_ms=%d",
            message.message_id, message.kind, int(self._start_timeout_seconds * 1000),
        )
        while True:
            completion = self._speech_completion(0)
            if completion is not None:
                return self._terminal_before_start(message, completion, wait_started_at)
            if self._speech_started(0):
                started_at = time.monotonic()
                LOGGER.info("speech started id=%s kind=%s", message.message_id, message.kind)
                scoped_debug(
                    "speech", "started id=%s kind=%s wait_ms=%d",
                    message.message_id, message.kind, int((started_at - wait_started_at) * 1000),
                )
                return self._wait_for_completion(message, started_at)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                LOGGER.warning("speech start timed out id=%s kind=%s", message.message_id, message.kind)
                scoped_debug("speech", "timeout id=%s kind=%s phase=start", message.message_id, message.kind)
                self._cancel_adapter("start_timeout")
                return False
            completion = self._speech_completion(min(PLAYBACK_POLL_SECONDS, remaining))
            if completion is not None:
                return self._terminal_before_start(message, completion, wait_started_at)
            with self._condition:
                if self._closed:
                    return False

    def _wait_for_completion(self, message: SpeechMessage, started_at: float) -> bool:
        timeout = self._playback_timeout(message)
        deadline = started_at + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                LOGGER.warning("speech playback timed out id=%s kind=%s", message.message_id, message.kind)
                scoped_debug(
                    "speech", "timeout id=%s kind=%s phase=playback elapsed_ms=%d",
                    message.message_id, message.kind, int((time.monotonic() - started_at) * 1000),
                )
                self._cancel_adapter("playback_timeout")
                return False
            completion = self._speech_completion(min(PLAYBACK_POLL_SECONDS, remaining))
            if completion is not None:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                scoped_debug(
                    "speech", "completed id=%s kind=%s ok=%s playback_ms=%d",
                    message.message_id, message.kind, completion, elapsed_ms,
                )
                if not completion:
                    LOGGER.warning("speech failed id=%s kind=%s", message.message_id, message.kind)
                return completion
            with self._condition:
                if self._closed:
                    return False

    def _terminal_before_start(
        self,
        message: SpeechMessage,
        completion: bool,
        wait_started_at: float,
    ) -> bool:
        elapsed_ms = int((time.monotonic() - wait_started_at) * 1000)
        scoped_debug(
            "speech", "completed before start id=%s kind=%s ok=%s elapsed_ms=%d",
            message.message_id, message.kind, completion, elapsed_ms,
        )
        if not completion:
            LOGGER.warning("speech failed id=%s kind=%s", message.message_id, message.kind)
        return completion

    def _playback_timeout(self, message: SpeechMessage) -> float:
        del message
        return self._playback_timeout_seconds

    def _clear_current(self) -> None:
        with self._condition:
            self._current = None

    def _speak(self, summary: str) -> bool:
        try:
            return self._adapter.speak(summary)
        except Exception:
            LOGGER.exception("speech adapter playback failed")
            return False

    def _speech_completion(self, timeout: float) -> bool | None:
        try:
            return self._adapter.wait_finished(timeout=timeout)
        except Exception:
            LOGGER.exception("speech adapter wait failed")
            return False

    def _speech_started(self, timeout: float) -> bool | None:
        try:
            return self._adapter.wait_started(timeout=timeout)
        except Exception:
            LOGGER.exception("speech adapter start wait failed")
            return False

    def _cancel_adapter(self, reason: str) -> None:
        current = self._current
        scoped_debug(
            "speech", "cancel requested reason=%s id=%s kind=%s",
            reason,
            current.message_id if current is not None else "",
            current.kind if current is not None else "",
        )
        try:
            self._adapter.cancel()
        except Exception:
            LOGGER.exception("speech adapter cancel failed")

    def _close_adapter(self) -> None:
        try:
            self._adapter.close()
        except Exception:
            LOGGER.exception("speech adapter close failed")
