from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = WORKSPACE_ROOT / "scripts" / "phase4" / "lcu_champ_select.py"
CATALOG_PATH = WORKSPACE_ROOT / "scripts" / "phase4" / "champion_catalog.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lcu = _load("phase4_lcu_champ_select", MODULE_PATH)
catalog_mod = _load("phase4_champion_catalog", CATALOG_PATH)


class FakeTransport:
    def __init__(self, responses: dict[str, lcu.LcuHttpResult]) -> None:
        self.responses = responses
        self.paths: list[str] = []

    def get(self, connection: lcu.LcuConnection, path: str) -> lcu.LcuHttpResult:
        self.paths.append(path)
        return self.responses[path]


class ChampionCatalogTests(unittest.TestCase):
    def test_poppy_and_caitlyn_use_chinese_titles(self) -> None:
        catalog = catalog_mod.ChampionCatalog.load()
        self.assertEqual(catalog.label(78), "波比")
        self.assertEqual(catalog.label(51), "凯特琳")
        self.assertEqual(catalog.label(9999), "#9999")


class LaunchArgumentTests(unittest.TestCase):
    def test_repeated_matching_arguments_parse(self) -> None:
        parsed = lcu.parse_lcu_launch_arguments(
            "prefix --app-pid=4242 --app-port \"61234\" "
            "--remoting-auth-token='abcDEF123'\n"
            "repeat --app-pid=4242 --app-port=61234 --remoting-auth-token=abcDEF123"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.app_pid, 4242)
        self.assertEqual(parsed.port, 61234)
        self.assertEqual(parsed.token, "abcDEF123")

    def test_conflicting_or_unsafe_arguments_fail_closed(self) -> None:
        self.assertIsNone(
            lcu.parse_lcu_launch_arguments(
                "--app-pid=1 --app-port=61234 --remoting-auth-token=abc "
                "--app-port=61235"
            )
        )
        self.assertIsNone(
            lcu.parse_lcu_launch_arguments(
                "--app-pid=1 --app-port=70000 --remoting-auth-token=abc"
            )
        )
        self.assertIsNone(
            lcu.parse_lcu_launch_arguments(
                "--app-pid=1 --app-port=61234 --remoting-auth-token=has:colon"
            )
        )


class SessionParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = catalog_mod.ChampionCatalog.load()

    def test_aram_bench_and_local_champion(self) -> None:
        session = lcu.parse_champ_select_session(
            """
            {
              "benchEnabled": true,
              "benchChampions": [
                {"championId": 78, "isPriority": false},
                {"championId": 0},
                {"championId": 51}
              ],
              "localPlayerCellId": 2,
              "myTeam": [
                {"cellId": 1, "championId": 103},
                {"cellId": 2, "championId": 51}
              ]
            }
            """,
            self.catalog,
        )
        self.assertIsNotNone(session)
        assert session is not None
        self.assertTrue(session.bench_enabled)
        self.assertEqual(session.champion_id, 51)
        self.assertEqual(
            [(item.champion_id, item.name) for item in session.bench],
            [(78, "波比"), (51, "凯特琳")],
        )

    def test_malformed_bench_fails_closed(self) -> None:
        self.assertIsNone(
            lcu.parse_champ_select_session(
                '{"benchEnabled": true, "benchChampions": [{"championId": "78"}]}',
                self.catalog,
            )
        )
        self.assertIsNone(lcu.parse_champ_select_session("[]", self.catalog))
        self.assertIsNone(lcu.parse_gameflow_phase('{"phase":"ChampSelect"}'))
        self.assertEqual(lcu.parse_gameflow_phase('"ChampSelect"'), "ChampSelect")


class ReaderRoutingTests(unittest.TestCase):
    def test_non_champ_select_does_not_call_session(self) -> None:
        transport = FakeTransport(
            {
                lcu.GAMEFLOW_PATH: lcu.LcuHttpResult(
                    True, 200, '"InProgress"', ""
                )
            }
        )
        snapshot = lcu.read_lcu_snapshot(
            catalog=catalog_mod.ChampionCatalog({}),
            transport=transport,
            connection_factory=lambda: (lcu.LcuConnection(61234, "token-ok"), "ok"),
        )
        self.assertEqual(snapshot.status, "READY")
        self.assertEqual(snapshot.gameflow_phase, "InProgress")
        self.assertEqual(snapshot.bench, ())
        self.assertEqual(transport.paths, [lcu.GAMEFLOW_PATH])

    def test_champ_select_reads_bench_from_session(self) -> None:
        transport = FakeTransport(
            {
                lcu.GAMEFLOW_PATH: lcu.LcuHttpResult(True, 200, '"ChampSelect"', ""),
                lcu.CHAMP_SELECT_SESSION_PATH: lcu.LcuHttpResult(
                    True,
                    200,
                    '{"benchEnabled":true,"benchChampions":[{"championId":78}],'
                    '"localPlayerCellId":0,"myTeam":[{"cellId":0,"championId":51}]}',
                    "",
                ),
            }
        )
        snapshot = lcu.read_lcu_snapshot(
            catalog=catalog_mod.ChampionCatalog.load(),
            transport=transport,
            connection_factory=lambda: (lcu.LcuConnection(61234, "token-ok"), "ok"),
        )
        self.assertEqual(snapshot.status, "READY")
        self.assertEqual(snapshot.champion_id, 51)
        self.assertEqual([item.name for item in snapshot.bench], ["波比"])
        self.assertNotIn(lcu.CURRENT_CHAMPION_PATH, transport.paths)
        event = lcu.snapshot_to_event(snapshot, sequence=1)
        self.assertEqual(event["type"], "lcu_context_state")
        self.assertEqual(event["context"]["benchChampions"][0]["name"], "波比")
        self.assertNotIn("token-ok", str(event))

    def test_session_failure_falls_back_without_inventing_bench(self) -> None:
        transport = FakeTransport(
            {
                lcu.GAMEFLOW_PATH: lcu.LcuHttpResult(True, 200, '"ChampSelect"', ""),
                lcu.CHAMP_SELECT_SESSION_PATH: lcu.LcuHttpResult(
                    False, 404, "", "http_error"
                ),
                lcu.CURRENT_CHAMPION_PATH: lcu.LcuHttpResult(True, 200, "103", ""),
            }
        )
        snapshot = lcu.read_lcu_snapshot(
            catalog=catalog_mod.ChampionCatalog({}),
            transport=transport,
            connection_factory=lambda: (lcu.LcuConnection(61234, "token-ok"), "ok"),
        )
        self.assertEqual(snapshot.status, "PARTIAL")
        self.assertEqual(snapshot.reason, "bench_unavailable")
        self.assertEqual(snapshot.champion_id, 103)
        self.assertEqual(snapshot.bench, ())


if __name__ == "__main__":
    unittest.main()
