from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from speech import SpeechMessage, SpeechPriority, SpeechQueue, SpeechService, should_interrupt


def message(
    key: str,
    priority: SpeechPriority = SpeechPriority.NORMAL,
    expires_at: float = 100.0,
) -> SpeechMessage:
    return SpeechMessage(
        message_id=key,
        source="local",
        kind="recommendation",
        summary=key,
        priority=priority,
        dedupe_key=key,
        created_at=0.0,
        expires_at=expires_at,
    )


class SpeechMessageTests(unittest.TestCase):
    def test_priorities_have_approved_values(self) -> None:
        self.assertEqual(10, SpeechPriority.LOW)
        self.assertEqual(20, SpeechPriority.NORMAL)
        self.assertEqual(30, SpeechPriority.HIGH)

    def test_message_is_immutable(self) -> None:
        item = message("fixed")
        with self.assertRaises(FrozenInstanceError):
            item.summary = "changed"


class SpeechQueueTests(unittest.TestCase):
    def test_duplicate_and_expired_messages_are_rejected(self) -> None:
        queue = SpeechQueue()
        self.assertTrue(queue.publish(message("same"), 1.0))
        self.assertFalse(queue.publish(message("same"), 2.0))
        self.assertFalse(queue.publish(message("expired", expires_at=1.0), 2.0))

    def test_dedupe_key_can_be_reused_after_expiry(self) -> None:
        queue = SpeechQueue()
        self.assertTrue(queue.publish(message("same", expires_at=2.0), 1.0))
        self.assertTrue(queue.publish(message("same", expires_at=5.0), 2.0))
        self.assertEqual("same", queue.next(2.0).dedupe_key)

    def test_high_priority_precedes_low_priority(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("low", SpeechPriority.LOW), 1.0)
        queue.publish(message("high", SpeechPriority.HIGH), 1.0)
        self.assertEqual("high", queue.next(1.0).dedupe_key)

    def test_equal_priority_order_is_stable(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("first"), 1.0)
        queue.publish(message("second"), 1.0)
        self.assertEqual("first", queue.next(1.0).dedupe_key)
        self.assertEqual("second", queue.next(1.0).dedupe_key)

    def test_next_discards_messages_that_expired_in_queue(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("old", expires_at=2.0), 1.0)
        self.assertIsNone(queue.next(2.0))

    def test_low_priority_dispatches_have_sixty_second_interval(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("first", SpeechPriority.LOW, 200.0), 1.0)
        queue.publish(message("second", SpeechPriority.LOW, 200.0), 1.0)
        self.assertEqual("first", queue.next(1.0).dedupe_key)
        self.assertIsNone(queue.next(60.99))
        self.assertEqual("second", queue.next(61.0).dedupe_key)

    def test_throttled_low_does_not_block_normal_message(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("first", SpeechPriority.LOW, 200.0), 1.0)
        self.assertEqual("first", queue.next(1.0).dedupe_key)
        queue.publish(message("low", SpeechPriority.LOW, 200.0), 2.0)
        queue.publish(message("normal", SpeechPriority.NORMAL, 200.0), 2.0)
        self.assertEqual("normal", queue.next(2.0).dedupe_key)

    def test_throttled_low_does_not_block_high_message(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("first", SpeechPriority.LOW, 200.0), 1.0)
        self.assertEqual("first", queue.next(1.0).dedupe_key)
        queue.publish(message("low", SpeechPriority.LOW, 200.0), 2.0)
        queue.publish(message("high", SpeechPriority.HIGH, 200.0), 2.0)
        self.assertEqual("high", queue.next(2.0).dedupe_key)

    def test_dedupe_survives_dispatch_until_expiry(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("same", expires_at=10.0), 1.0)
        self.assertEqual("same", queue.next(2.0).dedupe_key)
        self.assertFalse(queue.publish(message("same", expires_at=20.0), 3.0))
        self.assertTrue(queue.publish(message("same", expires_at=20.0), 10.0))

    def test_mute_clears_low_and_rejects_new_messages(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("low", SpeechPriority.LOW), 1.0)
        queue.publish(message("normal"), 1.0)
        queue.mute()
        self.assertFalse(queue.publish(message("new"), 2.0))
        self.assertIsNone(queue.next(2.0))
        queue.unmute()
        self.assertEqual("normal", queue.next(2.0).dedupe_key)
        self.assertIsNone(queue.next(2.0))

    def test_queued_normal_and_high_expire_while_muted(self) -> None:
        queue = SpeechQueue()
        queue.publish(message("normal", expires_at=2.0), 1.0)
        queue.publish(message("high", SpeechPriority.HIGH, 3.0), 1.0)
        queue.mute()
        self.assertIsNone(queue.next(3.0))
        queue.unmute()
        self.assertIsNone(queue.next(3.0))


class SpeechInterruptionTests(unittest.TestCase):
    def test_only_high_interrupts_current_low(self) -> None:
        low = message("low", SpeechPriority.LOW)
        normal = message("normal", SpeechPriority.NORMAL)
        high = message("high", SpeechPriority.HIGH)
        self.assertTrue(should_interrupt(low, high))
        self.assertFalse(should_interrupt(low, normal))
        self.assertFalse(should_interrupt(normal, high))
        self.assertFalse(should_interrupt(high, high))


class FakeAdapter:
    def __init__(self, start_ok: bool = True) -> None:
        self.start_ok = start_ok
        self.started = 0
        self.spoken: list[str] = []
        self.cancelled = 0
        self.closed = 0
        self.finished = __import__("threading").Event()

    def start(self) -> bool:
        self.started += 1
        return self.start_ok

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def wait_finished(self, timeout=None) -> bool:
        return self.finished.wait(timeout)

    def cancel(self) -> None:
        self.cancelled += 1
        self.finished.set()

    def close(self) -> None:
        self.closed += 1
        self.finished.set()


class BlockingStartAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.start_entered = __import__("threading").Event()
        self.start_release = __import__("threading").Event()

    def start(self) -> bool:
        self.started += 1
        self.start_entered.set()
        return self.start_release.wait(1.0)


class SpeechServiceTests(unittest.TestCase):
    def test_adapter_starts_lazily_and_failure_does_not_raise(self) -> None:
        import time

        adapter = FakeAdapter(start_ok=False)
        service = SpeechService(adapter, clock=lambda: 1.0)
        self.assertEqual(0, adapter.started)
        service.publish(message("one"))
        deadline = time.monotonic() + 1.0
        while adapter.started == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        service.close()
        self.assertEqual(1, adapter.started)
        self.assertEqual(1, adapter.closed)

    def test_high_interrupts_playing_low(self) -> None:
        import time

        adapter = FakeAdapter()
        service = SpeechService(adapter, clock=time.monotonic)
        now = time.monotonic()
        service.publish(message("low", SpeechPriority.LOW, now + 10.0))
        deadline = time.monotonic() + 1.0
        while not adapter.spoken and time.monotonic() < deadline:
            time.sleep(0.01)
        service.publish(message("high", SpeechPriority.HIGH, now + 10.0))
        self.assertGreaterEqual(adapter.cancelled, 1)
        service.close()

    def test_high_invalidates_low_while_adapter_is_starting(self) -> None:
        import time

        adapter = BlockingStartAdapter()
        service = SpeechService(adapter, clock=time.monotonic)
        now = time.monotonic()
        service.publish(message("low", SpeechPriority.LOW, now + 10.0))
        self.assertTrue(adapter.start_entered.wait(1.0))
        service.publish(message("high", SpeechPriority.HIGH, now + 10.0))
        adapter.finished.set()
        adapter.start_release.set()
        deadline = time.monotonic() + 1.0
        while adapter.spoken != ["high"] and time.monotonic() < deadline:
            time.sleep(0.01)
        service.close()
        self.assertEqual(["high"], adapter.spoken)

    def test_mute_invalidates_normal_while_adapter_is_starting(self) -> None:
        import time

        adapter = BlockingStartAdapter()
        service = SpeechService(adapter, clock=time.monotonic)
        now = time.monotonic()
        service.publish(message("normal", expires_at=now + 10.0))
        self.assertTrue(adapter.start_entered.wait(1.0))
        service.set_enabled(False)
        adapter.start_release.set()
        adapter.finished.set()
        service.set_enabled(True)
        service.publish(message("high", SpeechPriority.HIGH, now + 10.0))
        deadline = time.monotonic() + 1.0
        while adapter.spoken != ["high"] and time.monotonic() < deadline:
            time.sleep(0.01)
        service.close()
        self.assertEqual(["high"], adapter.spoken)

    def test_mute_cancels_synchronously_and_close_is_idempotent(self) -> None:
        adapter = FakeAdapter()
        service = SpeechService(adapter)
        service.set_enabled(False)
        self.assertEqual(1, adapter.cancelled)
        service.close()
        service.close()
        self.assertEqual(1, adapter.closed)

    def test_adapter_exceptions_do_not_escape_ui_or_shutdown(self) -> None:
        adapter = FakeAdapter()
        adapter.cancel = Mock(side_effect=RuntimeError("cancel failed"))
        adapter.close = Mock(side_effect=RuntimeError("close failed"))
        service = SpeechService(adapter)
        with self.assertLogs("speech", level="ERROR"):
            service.set_enabled(False)
            service.close()


if __name__ == "__main__":
    unittest.main()
