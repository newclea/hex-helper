from __future__ import annotations

import unittest

from hexcore_gate import death_ocr_allowed, hexcore_ocr_open


class DeathOcrEligibilityTests(unittest.TestCase):
    def test_thresholds_use_confirmed_count(self) -> None:
        cases = (
            (None, 0, False),
            (6, 0, False),
            (7, 1, True),
            (7, 2, False),
            (10, 1, True),
            (11, 2, True),
            (11, 3, False),
            (14, 2, True),
            (15, 3, True),
            (15, 4, False),
        )
        for level, count, expected in cases:
            with self.subTest(level=level, count=count):
                self.assertEqual(expected, death_ocr_allowed(level, count))

    def test_real_offer_overrides_ineligible_death(self) -> None:
        self.assertTrue(hexcore_ocr_open(
            completed=2,
            level=8,
            is_dead=True,
            round_closed=False,
            offer_visible=True,
            death_scan_allowed=False,
        ))

    def test_level_three_initial_rule_does_not_turn_low_level_death_into_probe(self) -> None:
        self.assertTrue(hexcore_ocr_open(
            completed=0,
            level=3,
            is_dead=False,
            round_closed=False,
            death_scan_allowed=False,
        ))
        self.assertFalse(hexcore_ocr_open(
            completed=0,
            level=5,
            is_dead=True,
            round_closed=False,
            death_scan_allowed=False,
        ))

    def test_respawn_window_requires_eligible_death(self) -> None:
        self.assertFalse(hexcore_ocr_open(
            completed=2,
            level=12,
            is_dead=False,
            round_closed=False,
            seconds_since_respawn=1.0,
            death_scan_allowed=False,
        ))
        self.assertTrue(hexcore_ocr_open(
            completed=2,
            level=12,
            is_dead=False,
            round_closed=False,
            seconds_since_respawn=1.0,
            death_scan_allowed=True,
        ))


if __name__ == "__main__":
    unittest.main()
