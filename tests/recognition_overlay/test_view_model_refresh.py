from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from history_store import HistoryStore
from view_model import RecognitionViewModel


class FakeCatalog:
    database: "FakeCatalog"

    def __init__(self) -> None:
        self.database = self

    @staticmethod
    def contains_id(value) -> bool:
        return isinstance(value, str) and bool(value)

    @staticmethod
    def contains_name(value) -> bool:
        return isinstance(value, str) and bool(value)

    @staticmethod
    def resolve(augment_id=None, display_name=None, raw_text=None):
        value = augment_id or display_name or raw_text
        return SimpleNamespace(augment_id=value, name=display_name or value) if value else None

    def complete_offer(self, cards):
        if not isinstance(cards, list) or len(cards) != 3:
            return None
        return [
            {
                "slot": card["slot"],
                "augment_id": card["augment_id"],
                "name": card["display_name"],
            }
            for card in cards
        ]

    def align_offer(self, cards):
        return self.complete_offer(cards) or []


class ViewModelRefreshTests(unittest.TestCase):
    def test_legacy_round_conflict_stays_in_refreshing_state(self) -> None:
        with TemporaryDirectory() as directory:
            model = RecognitionViewModel(
                catalog=FakeCatalog(),
                store=HistoryStore(Path(directory) / "history.jsonl"),
            )
            model.phase = "InProgress"
            model.offer_visible = True
            model.offer_round = 2
            model.offer = [
                {"slot": "LEFT", "augment_id": "old-1", "name": "旧一"},
                {"slot": "CENTER", "augment_id": "old-2", "name": "旧二"},
                {"slot": "RIGHT", "augment_id": "old-3", "name": "旧三"},
            ]
            cards = [
                {
                    "slot": slot,
                    "state": "RECOGNIZED",
                    "augment_id": f"new-{index}",
                    "display_name": f"新{index}",
                    "raw_text": f"新{index}",
                }
                for index, slot in enumerate(("LEFT", "CENTER", "RIGHT"), 1)
            ]

            model.apply_frame_result(
                {
                    "reason": "session_invalid_offer",
                    "accepted": False,
                    "raw_detector": {"visible": True, "reason": "visible"},
                    "recognition_debug": {"cards": cards},
                }
            )

        self.assertTrue(model.offer_refreshing)
        self.assertEqual("ocr_updating", model.ocr_feedback["state"])
        self.assertIn("候选已更新", model.ocr_feedback["message"])
        self.assertNotIn("校验未通过", model.ocr_feedback["message"])


if __name__ == "__main__":
    unittest.main()
