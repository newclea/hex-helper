from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from augment_catalog import AugmentCatalog
from history_store import HistoryStore
from view_model import RecognitionViewModel


def lcu_event(phase: str, game_id: int, result: str | None = None) -> dict[str, object]:
    return {
        "status": "READY",
        "reason": "ok",
        "context": {
            "gameflowPhase": phase,
            "gameId": game_id,
            "gameResult": result,
        },
    }


class ViewModelGameResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.model = RecognitionViewModel(
            catalog=AugmentCatalog({}),
            store=HistoryStore(Path(self.temporary.name) / "history.jsonl"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_result_is_accepted_only_after_live_match_for_same_game(self) -> None:
        self.model.apply_lcu(lcu_event("InProgress", 123))
        self.model.apply_lcu(lcu_event("EndOfGame", 123, "WIN"))
        self.assertEqual("WIN", self.model.snapshot()["game_result"])

        self.model.apply_lcu(lcu_event("ChampSelect", 124))
        self.assertIsNone(self.model.snapshot()["game_result"])

    def test_result_for_different_game_is_ignored(self) -> None:
        self.model.apply_lcu(lcu_event("InProgress", 123))
        self.model.apply_lcu(lcu_event("EndOfGame", 999, "LOSS"))
        self.assertIsNone(self.model.snapshot()["game_result"])

    def test_post_game_without_live_match_is_ignored(self) -> None:
        self.model.apply_lcu(lcu_event("EndOfGame", 123, "WIN"))
        self.assertIsNone(self.model.snapshot()["game_result"])

    def test_missing_live_game_id_cannot_accept_a_stale_result(self) -> None:
        self.model.apply_lcu(lcu_event("InProgress", None))
        self.model.apply_lcu(lcu_event("Lobby", 999, "WIN"))
        self.assertIsNone(self.model.snapshot()["game_result"])

    def test_delayed_result_after_return_to_lobby(self) -> None:
        self.model.apply_lcu(lcu_event("InProgress", 123))
        self.model.apply_lcu(lcu_event("EndOfGame", 123))
        self.model.apply_lcu(lcu_event("Lobby", 123, "WIN"))
        self.assertEqual("WIN", self.model.snapshot()["game_result"])


if __name__ == "__main__":
    unittest.main()
