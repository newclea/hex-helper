from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from augment_catalog import AugmentCatalog
from history_store import HistoryStore
from view_model import RecognitionViewModel


def player(level: int | None, is_dead: bool) -> dict[str, object]:
    payload: dict[str, object] = {"isDead": is_dead, "championName": "Annie"}
    if level is not None:
        payload["level"] = level
    return payload


def confirmed(stage: int) -> dict[str, object]:
    return {
        "stage": stage,
        "source": "hud_icon_template",
        "augment_id": f"augment-{stage}",
        "name": f"海克斯{stage}",
    }


class ViewModelDeathGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        history_path = Path(self.temporary.name) / "history.jsonl"
        self.model = RecognitionViewModel(
            catalog=AugmentCatalog({}),
            store=HistoryStore(history_path),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_death_thresholds_use_confirmed_selections(self) -> None:
        cases = (
            (7, 1, True),
            (7, 2, False),
            (11, 2, True),
            (11, 3, False),
            (15, 3, True),
            (15, 4, False),
        )
        for level, count, expected in cases:
            with self.subTest(level=level, count=count):
                self.model._start_new_match()
                self.model.selected = [confirmed(stage) for stage in range(1, count + 1)]
                self.model._apply_live_player(player(level, True))
                self.assertEqual(level, self.model._latest_death_level)
                self.assertEqual(expected, self.model._death_ocr_allowed)

    def test_missing_level_on_death_does_not_reuse_cached_level(self) -> None:
        self.model.selected = [confirmed(1), confirmed(2)]
        self.model._apply_live_player(player(11, False))
        self.model._apply_live_player(player(None, True))

        self.assertIsNone(self.model._latest_death_level)
        self.assertFalse(self.model.vision_allowed())

    def test_repeated_dead_payload_does_not_recompute_eligibility(self) -> None:
        self.model.selected = [confirmed(1), confirmed(2)]
        self.model._apply_live_player(player(11, True))
        sequence = self.model._death_sequence

        self.model.selected.append(confirmed(3))
        self.model._apply_live_player(player(11, True))

        self.assertEqual(sequence, self.model._death_sequence)
        self.assertTrue(self.model._death_ocr_allowed)

    def test_new_match_clears_death_state(self) -> None:
        self.model.selected = [confirmed(1), confirmed(2)]
        self.model._apply_live_player(player(11, True))

        self.model._start_new_match()

        self.assertIsNone(self.model._latest_death_level)
        self.assertFalse(self.model._death_ocr_allowed)

    def test_real_offer_keeps_vision_allowed(self) -> None:
        self.model.selected = [confirmed(1), confirmed(2)]
        self.model._apply_live_player(player(8, True))
        self.assertFalse(self.model._death_ocr_allowed)
        self.model.offer = [
            {"slot": "LEFT", "name": "海克斯一"},
            {"slot": "CENTER", "name": "海克斯二"},
            {"slot": "RIGHT", "name": "海克斯三"},
        ]

        self.assertTrue(self.model.vision_allowed())


if __name__ == "__main__":
    unittest.main()
