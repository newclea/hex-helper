from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from speech import SpeechMessage, SpeechPriority, SpeechQueue, should_interrupt


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


if __name__ == "__main__":
    unittest.main()
