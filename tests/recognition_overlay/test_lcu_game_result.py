from __future__ import annotations

import unittest
from unittest.mock import patch

from lcu_champ_select import (
    END_OF_GAME_STATS_PATH,
    GAMEFLOW_PATH,
    LcuConnection,
    LcuContextSnapshot,
    LcuHttpResult,
    parse_end_of_game_stats,
    read_lcu_snapshot,
    snapshot_to_event,
)
from scoped_debug import configure_debug_submodes, scoped_debug


class FakeTransport:
    def __init__(self, responses: dict[str, LcuHttpResult]) -> None:
        self.responses = responses
        self.paths: list[str] = []

    def get(self, connection: LcuConnection, path: str) -> LcuHttpResult:
        del connection
        self.paths.append(path)
        return self.responses[path]


class LcuGameResultTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_debug_submodes(())

    def test_parser_normalizes_win_and_loss_without_guessing_unknown(self) -> None:
        self.assertEqual((123, "WIN", "Win"), parse_end_of_game_stats(
            '{"gameId":123,"myTeamStatus":"Win"}'
        ))
        self.assertEqual((124, "LOSS", "Defeat"), parse_end_of_game_stats(
            '{"gameId":124,"myTeamStatus":"Defeat"}'
        ))
        self.assertEqual((125, None, "Unknown"), parse_end_of_game_stats(
            '{"gameId":125,"myTeamStatus":"Unknown"}'
        ))

    def test_post_game_snapshot_reads_result_endpoint(self) -> None:
        transport = FakeTransport({
            GAMEFLOW_PATH: LcuHttpResult(True, 200, '"EndOfGame"'),
            END_OF_GAME_STATS_PATH: LcuHttpResult(
                True, 200, '{"gameId":123,"myTeamStatus":"Win"}'
            ),
        })

        snapshot = read_lcu_snapshot(
            catalog=object(),
            transport=transport,
            connection_factory=lambda: (LcuConnection(2999, "token"), "ok"),
        )

        self.assertEqual([GAMEFLOW_PATH, END_OF_GAME_STATS_PATH], transport.paths)
        self.assertEqual(123, snapshot.game_id)
        self.assertEqual("WIN", snapshot.game_result)

    def test_event_exposes_only_normalized_result(self) -> None:
        event = snapshot_to_event(
            LcuContextSnapshot(
                status="READY",
                reason="ok",
                gameflow_phase="EndOfGame",
                game_id=123,
                game_result="LOSS",
            ),
            sequence=1,
        )
        self.assertEqual("LOSS", event["context"]["gameResult"])
        self.assertNotIn("myTeamStatus", event["context"])

    def test_scoped_debug_logs_only_when_game_result_mode_is_enabled(self) -> None:
        with patch("scoped_debug.logging.info") as log:
            scoped_debug("game-result", "result=%s", "WIN")
        log.assert_not_called()
        configure_debug_submodes(("game-result",))
        with patch("scoped_debug.logging.info") as log:
            scoped_debug("game-result", "result=%s", "WIN")
        log.assert_called_once()
        self.assertEqual("DEBUG[%s] result=%s", log.call_args.args[0])
        self.assertEqual("game-result", log.call_args.args[1])
        self.assertEqual("WIN", log.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
