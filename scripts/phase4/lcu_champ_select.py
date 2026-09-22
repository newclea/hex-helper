"""Read-only LCU champ-select bench poller.

This is a pre-capture companion to the C++ LCU context reader.  Champion
select happens in the League client before the in-game window exists, so the
Python bridge starts this poller immediately.  Only hardcoded loopback GETs
on an allowlist are used.  The remoting token never enters events, logs, or
exceptions.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import Request

_PHASE4_DIR = Path(__file__).resolve().parent
if str(_PHASE4_DIR) not in sys.path:
    sys.path.insert(0, str(_PHASE4_DIR))

from champion_catalog import ChampionCatalog


MAXIMUM_RESPONSE_BYTES = 1 * 1024 * 1024
MAXIMUM_LOG_BYTES = 2 * 1024 * 1024
MAXIMUM_LOG_CANDIDATES = 16
MAXIMUM_BENCH_CHAMPIONS = 16
GAMEFLOW_PATH = "/lol-gameflow/v1/gameflow-phase"
GAMEFLOW_SESSION_PATH = "/lol-gameflow/v1/session"
END_OF_GAME_STATS_PATH = "/lol-end-of-game/v1/eog-stats-block"
CHAMP_SELECT_SESSION_PATH = "/lol-champ-select/v1/session"
CURRENT_CHAMPION_PATH = "/lol-champ-select/v1/current-champion"
SUBSET_CHAMPIONS_PATH = "/lol-lobby-team-builder/champ-select/v1/subset-champion-list"
ALLOWED_PATHS = frozenset(
    {GAMEFLOW_PATH, GAMEFLOW_SESSION_PATH, END_OF_GAME_STATS_PATH, CHAMP_SELECT_SESSION_PATH,
     CURRENT_CHAMPION_PATH, SUBSET_CHAMPIONS_PATH}
)
LCU_STATUSES = frozenset({"READY", "PARTIAL", "UNAVAILABLE", "INVALID_RESPONSE"})
POST_GAME_PHASES = frozenset({"PreEndOfGame", "WaitingForStats", "EndOfGame"})


@dataclass(frozen=True)
class LcuLaunchArguments:
    app_pid: int
    port: int
    token: str


@dataclass(frozen=True)
class LcuConnection:
    port: int
    token: str


@dataclass(frozen=True)
class LcuHttpResult:
    ok: bool
    status_code: int = 0
    body: str = ""
    error_code: str = ""


@dataclass(frozen=True)
class BenchChampion:
    champion_id: int
    name: str

    def as_dict(self) -> dict[str, Any]:
        return {"championId": self.champion_id, "name": self.name}


@dataclass(frozen=True)
class ChampSelectSession:
    bench_enabled: bool
    bench: tuple[BenchChampion, ...]
    champion_id: int | None
    allow_subset_champion_picks: bool = False


@dataclass(frozen=True)
class LcuContextSnapshot:
    status: str
    reason: str
    gameflow_phase: str | None = None
    champion_id: int | None = None
    bench_enabled: bool | None = None
    bench: tuple[BenchChampion, ...] = ()
    game_id: int | None = None
    game_result: str | None = None
    game_result_raw: str | None = None
    game_result_status: int | None = None
    game_result_error: str | None = None

    def identity(self) -> tuple[Any, ...]:
        return (
            self.status,
            self.reason,
            self.gameflow_phase,
            self.champion_id,
            self.bench_enabled,
            tuple((item.champion_id, item.name) for item in self.bench),
            self.game_id,
            self.game_result,
            self.game_result_raw,
            self.game_result_status,
            self.game_result_error,
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> Request | None:
        return None


def _is_safe_token(token: str) -> bool:
    if not token or len(token) > 1024:
        return False
    return all(0x21 <= ord(character) <= 0x7E and character != ":" for character in token)


def _is_argument_boundary(character: str) -> bool:
    return character.isspace() or character in "\"'[]("


def find_argument_values(text: str, name: str) -> list[str]:
    marker = f"--{name}"
    values: list[str] = []
    search_from = 0
    while search_from < len(text):
        found = text.find(marker, search_from)
        if found < 0:
            break
        search_from = found + len(marker)
        if found > 0 and not _is_argument_boundary(text[found - 1]):
            continue
        cursor = search_from
        if cursor < len(text) and text[cursor] == "=":
            cursor += 1
        elif cursor < len(text) and text[cursor].isspace():
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
        else:
            continue
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text):
            continue
        quote = text[cursor] if text[cursor] in "\"'" else ""
        if quote:
            cursor += 1
            begin = cursor
            while cursor < len(text) and text[cursor] != quote and text[cursor] not in "\r\n":
                cursor += 1
            if cursor >= len(text) or text[cursor] != quote:
                continue
        else:
            begin = cursor
            while (
                cursor < len(text)
                and not text[cursor].isspace()
                and text[cursor] not in "])"
            ):
                cursor += 1
        if begin < cursor <= begin + 2048:
            values.append(text[begin:cursor])
    return values


def _unique_argument(text: str, name: str) -> str | None:
    values = find_argument_values(text, name)
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        return None
    return values[0]


def _positive_int(value: str, *, maximum: int) -> int | None:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        return None
    parsed = int(value)
    if parsed > maximum:
        return None
    return parsed


def parse_lcu_launch_arguments(text: str) -> LcuLaunchArguments | None:
    app_pid_text = _unique_argument(text, "app-pid")
    port_text = _unique_argument(text, "app-port")
    token = _unique_argument(text, "remoting-auth-token")
    if app_pid_text is None or port_text is None or token is None:
        return None
    app_pid = _positive_int(app_pid_text, maximum=4_294_967_295)
    port = _positive_int(port_text, maximum=65_535)
    if app_pid is None or port is None or not _is_safe_token(token):
        return None
    return LcuLaunchArguments(app_pid=app_pid, port=port, token=token)


def parse_gameflow_phase(body: str) -> str | None:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, str):
        return None
    phase = " ".join(value.strip().split())
    if not phase or len(phase) > 64:
        return None
    if any(ord(character) < 0x20 or character == "\x7f" for character in phase):
        return None
    return phase


def parse_current_champion_id(body: str) -> int | None:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    if type(value) is not int or value < 0:
        return None
    if value == 0:
        return None
    if value > 10_000:
        return None
    return value


def parse_gameflow_game_id(body: str, *, phase: str) -> int | None:
    """Read the uint64 game identity from a phase-consistent LCU session."""
    try:
        session = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(session, Mapping) or session.get("phase") != phase:
        return None
    game = session.get("gameData")
    if not isinstance(game, Mapping):
        return None
    game_id = game.get("gameId")
    if type(game_id) is not int or not 1 <= game_id <= 18_446_744_073_709_551_615:
        return None
    return game_id


def parse_end_of_game_stats(body: str) -> tuple[int | None, str | None, str | None]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(data, Mapping):
        return None, None, None
    game_id = data.get("gameId")
    if type(game_id) is not int or not 1 <= game_id <= 18_446_744_073_709_551_615:
        game_id = None
    raw_status = data.get("myTeamStatus")
    if not isinstance(raw_status, str):
        return game_id, None, None
    normalized = raw_status.strip().upper()
    results = {
        "WIN": "WIN",
        "VICTORY": "WIN",
        "WON": "WIN",
        "LOSS": "LOSS",
        "LOSE": "LOSS",
        "LOST": "LOSS",
        "DEFEAT": "LOSS",
    }
    return game_id, results.get(normalized), raw_status[:32]


def parse_champ_select_session(
    body: str,
    catalog: ChampionCatalog,
) -> ChampSelectSession | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    bench_enabled = data.get("benchEnabled")
    if bench_enabled is not None and type(bench_enabled) is not bool:
        return None
    allow_subset = data.get("allowSubsetChampionPicks", False)
    if type(allow_subset) is not bool:
        return None

    raw_bench = data.get("benchChampions", [])
    if raw_bench is None:
        raw_bench = []
    if not isinstance(raw_bench, list) or len(raw_bench) > MAXIMUM_BENCH_CHAMPIONS:
        return None

    bench: list[BenchChampion] = []
    seen: set[int] = set()
    for item in raw_bench:
        if not isinstance(item, Mapping):
            return None
        champion_id = item.get("championId")
        if type(champion_id) is not int or champion_id < 0 or champion_id > 10_000:
            return None
        if champion_id == 0 or champion_id in seen:
            continue
        seen.add(champion_id)
        bench.append(
            BenchChampion(champion_id=champion_id, name=catalog.label(champion_id))
        )

    champion_id: int | None = None
    cell = data.get("localPlayerCellId")
    my_team = data.get("myTeam")
    if type(cell) is int and isinstance(my_team, list):
        for member in my_team:
            if not isinstance(member, Mapping):
                continue
            if member.get("cellId") != cell:
                continue
            local_id = member.get("championId")
            if type(local_id) is int and 1 <= local_id <= 10_000:
                champion_id = local_id
            break

    enabled = bool(bench_enabled) if bench_enabled is not None else bool(bench)
    return ChampSelectSession(
        bench_enabled=enabled,
        bench=tuple(bench),
        champion_id=champion_id,
        allow_subset_champion_picks=allow_subset,
    )


def parse_subset_champions(
    body: str, catalog: ChampionCatalog,
) -> tuple[BenchChampion, ...] | None:
    """Read the limited champion cards offered before a local pick exists."""
    try:
        ids = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(ids, list) or len(ids) > MAXIMUM_BENCH_CHAMPIONS:
        return None
    champions: list[BenchChampion] = []
    seen: set[int] = set()
    for champion_id in ids:
        if type(champion_id) is not int or not 0 <= champion_id <= 10_000:
            return None
        if champion_id == 0 or champion_id in seen:
            continue
        seen.add(champion_id)
        champions.append(BenchChampion(champion_id, catalog.label(champion_id)))
    return tuple(champions)


def is_active_loopback_listener(app_pid: int, port: int) -> bool:
    if os.name != "nt" or app_pid <= 0 or port <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    af_inet = 2
    tcp_table_owner_pid_listener = 3
    error_insufficient_buffer = 122
    no_error = 0
    listen_state = 2

    class TcpRow(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    iphlpapi = ctypes.WinDLL("iphlpapi")
    size = wintypes.DWORD(0)
    status = iphlpapi.GetExtendedTcpTable(
        None,
        ctypes.byref(size),
        False,
        af_inet,
        tcp_table_owner_pid_listener,
        0,
    )
    if status != error_insufficient_buffer or size.value == 0:
        return False
    buffer = ctypes.create_string_buffer(size.value)
    status = iphlpapi.GetExtendedTcpTable(
        buffer,
        ctypes.byref(size),
        False,
        af_inet,
        tcp_table_owner_pid_listener,
        0,
    )
    if status != no_error or size.value < 4:
        return False
    count = int.from_bytes(buffer.raw[:4], "little")
    offset = 4
    row_size = ctypes.sizeof(TcpRow)
    loopback = 0x0100007F
    for _ in range(count):
        if offset + row_size > size.value:
            break
        row = TcpRow.from_buffer_copy(buffer.raw[offset : offset + row_size])
        offset += row_size
        local_port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        if (
            row.dwState == listen_state
            and row.dwOwningPid == app_pid
            and row.dwLocalAddr == loopback
            and local_port == port
        ):
            return True
    return False


def discover_from_league_root(league_root: Path) -> LcuLaunchArguments | None:
    log_directory = league_root / "LeagueClient"
    if not log_directory.is_dir():
        return None
    logs: list[tuple[float, Path]] = []
    try:
        candidates = list(log_directory.iterdir())
    except OSError:
        return None
    for path in candidates:
        try:
            if path.is_file() and path.name.endswith("_LeagueClientUx.log"):
                logs.append((path.stat().st_mtime, path))
        except OSError:
            continue
    logs.sort(key=lambda item: item[0], reverse=True)
    for _, path in logs[:MAXIMUM_LOG_CANDIDATES]:
        try:
            with path.open("rb") as stream:
                text = stream.read(MAXIMUM_LOG_BYTES).decode(
                    "utf-8", errors="ignore"
                )
        except OSError:
            continue
        arguments = parse_lcu_launch_arguments(text)
        if arguments is None:
            continue
        if is_active_loopback_listener(arguments.app_pid, arguments.port):
            return arguments
    return None


class EnvironmentLcuConnectionProvider:
    def __init__(
        self,
        root_resolver: Callable[[], Path | None] | None = None,
    ) -> None:
        self._cached: LcuLaunchArguments | None = None
        self._cached_root: str | None = None
        self._root_resolver = root_resolver

    def invalidate(self) -> None:
        self._cached = None
        self._cached_root = None

    def resolve(self) -> tuple[LcuConnection | None, str]:
        port_text = os.environ.get("LOL_ASSISTANT_LCU_PORT")
        token = os.environ.get("LOL_ASSISTANT_LCU_TOKEN")
        if port_text is not None or token is not None:
            self.invalidate()
            if not port_text or not token or not _is_safe_token(token):
                return None, "auth_unavailable"
            port = _positive_int(port_text, maximum=65_535)
            if port is None:
                return None, "auth_unavailable"
            return LcuConnection(port=port, token=token), "ok"

        league_root = os.environ.get("LOL_ASSISTANT_LEAGUE_ROOT")
        if not league_root and self._root_resolver is not None:
            try:
                discovered = self._root_resolver()
            except Exception:
                discovered = None
            if discovered is not None:
                try:
                    resolved = discovered.expanduser().resolve()
                except OSError:
                    resolved = None
                if resolved is not None and resolved.is_absolute():
                    league_root = str(resolved)
                    os.environ["LOL_ASSISTANT_LEAGUE_ROOT"] = league_root
        if not league_root:
            self.invalidate()
            return None, "auth_unavailable"
        root = Path(league_root)
        if not root.is_absolute():
            self.invalidate()
            return None, "auth_unavailable"
        normalized = str(root.resolve())
        if (
            self._cached is not None
            and self._cached_root == normalized
            and is_active_loopback_listener(self._cached.app_pid, self._cached.port)
        ):
            return (
                LcuConnection(port=self._cached.port, token=self._cached.token),
                "ok",
            )
        self.invalidate()
        arguments = discover_from_league_root(root)
        if arguments is None:
            return None, "auth_unavailable"
        self._cached = arguments
        self._cached_root = normalized
        return LcuConnection(port=arguments.port, token=arguments.token), "ok"


def resolve_lcu_connection() -> tuple[LcuConnection | None, str]:
    return EnvironmentLcuConnectionProvider().resolve()


class LcuHttpsTransport:
    def get(self, connection: LcuConnection, path: str) -> LcuHttpResult:
        if path not in ALLOWED_PATHS or connection.port <= 0 or not _is_safe_token(connection.token):
            return LcuHttpResult(ok=False, error_code="endpoint_not_allowed")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        authorization = base64.b64encode(
            f"riot:{connection.token}".encode("ascii")
        ).decode("ascii")
        request = Request(
            f"https://127.0.0.1:{connection.port}{path}",
            method="GET",
            headers={
                "Authorization": f"Basic {authorization}",
                "Accept": "application/json",
            },
        )
        opener = urllib.request.build_opener(
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        )
        try:
            with opener.open(request, timeout=0.5) as response:
                status_code = int(getattr(response, "status", 200))
                body = response.read(MAXIMUM_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                return LcuHttpResult(
                    ok=False, status_code=error.code, error_code="auth_rejected"
                )
            return LcuHttpResult(
                ok=False, status_code=error.code, error_code="http_error"
            )
        except (urllib.error.URLError, TimeoutError, OSError):
            return LcuHttpResult(ok=False, error_code="connect_failed")
        if len(body) > MAXIMUM_RESPONSE_BYTES:
            return LcuHttpResult(ok=False, error_code="response_too_large")
        if status_code != 200:
            return LcuHttpResult(
                ok=False,
                status_code=status_code,
                error_code="auth_rejected" if status_code in {401, 403} else "http_error",
            )
        try:
            text = body.decode("utf-8")
        except UnicodeError:
            return LcuHttpResult(ok=False, error_code="invalid_response")
        return LcuHttpResult(ok=True, status_code=200, body=text)


def read_lcu_snapshot(
    *,
    catalog: ChampionCatalog,
    transport: LcuHttpsTransport | None = None,
    connection_factory: Callable[[], tuple[LcuConnection | None, str]] | None = None,
    provider: EnvironmentLcuConnectionProvider | None = None,
) -> LcuContextSnapshot:
    factory = connection_factory
    if factory is None and provider is not None:
        factory = provider.resolve
    if factory is None:
        factory = resolve_lcu_connection
    connection, reason = factory()
    if connection is None:
        return LcuContextSnapshot(status="UNAVAILABLE", reason=reason)
    client = transport or LcuHttpsTransport()
    phase_response = client.get(connection, GAMEFLOW_PATH)
    if not phase_response.ok:
        if phase_response.error_code == "auth_rejected" and provider is not None:
            provider.invalidate()
        return LcuContextSnapshot(
            status="UNAVAILABLE",
            reason=phase_response.error_code or "connect_failed",
        )
    phase = parse_gameflow_phase(phase_response.body)
    if phase is None:
        return LcuContextSnapshot(status="INVALID_RESPONSE", reason="gameflow_invalid_response")
    game_id = None
    game_result = None
    game_result_raw = None
    game_result_status = None
    game_result_error = None
    if phase in {"ChampSelect", "GameStart", "InProgress", "Reconnect"}:
        game_response = client.get(connection, GAMEFLOW_SESSION_PATH)
        if game_response.ok:
            game_id = parse_gameflow_game_id(game_response.body, phase=phase)
        elif game_response.error_code == "auth_rejected" and provider is not None:
            provider.invalidate()
    elif phase in POST_GAME_PHASES:
        result_response = client.get(connection, END_OF_GAME_STATS_PATH)
        game_result_status = result_response.status_code
        if result_response.ok:
            game_id, game_result, game_result_raw = parse_end_of_game_stats(
                result_response.body
            )
        else:
            game_result_error = result_response.error_code or "unknown"
            if result_response.error_code == "auth_rejected" and provider is not None:
                provider.invalidate()
    if phase != "ChampSelect":
        return LcuContextSnapshot(
            status="READY",
            reason="ok",
            gameflow_phase=phase,
            game_id=game_id,
            game_result=game_result,
            game_result_raw=game_result_raw,
            game_result_status=game_result_status,
            game_result_error=game_result_error,
        )

    return _read_champ_select_snapshot(
        catalog=catalog,
        client=client,
        connection=connection,
        provider=provider,
        phase=phase,
        game_id=game_id,
    )


def _read_champ_select_snapshot(
    *,
    catalog: ChampionCatalog,
    client: LcuHttpsTransport,
    connection: LcuConnection,
    provider: EnvironmentLcuConnectionProvider | None,
    phase: str,
    game_id: int | None,
) -> LcuContextSnapshot:
    session_response = client.get(connection, CHAMP_SELECT_SESSION_PATH)
    if session_response.ok:
        session = parse_champ_select_session(session_response.body, catalog)
        if session is not None:
            champion_id = session.champion_id
            # During the short ARAM/custom-game transition the session object
            # can be valid while myTeam still reports championId=0.  The
            # dedicated endpoint is often already populated at that point, so
            # use it as a fallback instead of waiting until the session request
            # itself fails.  Missing this window pushed strategy choice into
            # the live game on fast custom lobbies.
            if champion_id is None:
                champion_response = client.get(connection, CURRENT_CHAMPION_PATH)
                if champion_response.ok:
                    champion_id = parse_current_champion_id(champion_response.body)
                elif champion_response.error_code == "auth_rejected" and provider is not None:
                    provider.invalidate()
            bench = session.bench
            subset_failure = None
            if session.allow_subset_champion_picks:
                bench, subset_failure = _read_subset_bench(
                    catalog, client, connection, provider, champion_id, bench
                )
            status = "READY" if champion_id is not None or bench else "PARTIAL"
            reason_text = "ok" if status == "READY" else subset_failure or "champion_unavailable"
            return LcuContextSnapshot(
                status=status,
                reason=reason_text,
                gameflow_phase=phase,
                champion_id=champion_id,
                bench_enabled=session.bench_enabled,
                bench=bench,
                game_id=game_id,
            )
        return LcuContextSnapshot(
            status="PARTIAL",
            reason="session_invalid_response",
            gameflow_phase=phase,
            game_id=game_id,
        )
    if session_response.error_code == "auth_rejected":
        if provider is not None:
            provider.invalidate()
        return LcuContextSnapshot(status="UNAVAILABLE", reason="auth_rejected")

    champion_response = client.get(connection, CURRENT_CHAMPION_PATH)
    champion_id = (
        parse_current_champion_id(champion_response.body)
        if champion_response.ok
        else None
    )
    return LcuContextSnapshot(
        status="PARTIAL",
        reason="bench_unavailable",
        gameflow_phase=phase,
        champion_id=champion_id,
        game_id=game_id,
    )


def _read_subset_bench(
    catalog: ChampionCatalog,
    client: LcuHttpsTransport,
    connection: LcuConnection,
    provider: EnvironmentLcuConnectionProvider | None,
    champion_id: int | None,
    bench: tuple[BenchChampion, ...],
) -> tuple[tuple[BenchChampion, ...], str | None]:
    # In Mayhem's initial pick, the session can omit its two real cards.
    # This endpoint is the candidate set; the owned roster must not replace it.
    subset_response = client.get(connection, SUBSET_CHAMPIONS_PATH)
    subset = (
        parse_subset_champions(subset_response.body, catalog)
        if subset_response.ok else None
    )
    if subset is None:
        if subset_response.error_code == "auth_rejected" and provider is not None:
            provider.invalidate()
        reason = "subset_invalid_response" if subset_response.ok else "subset_unavailable"
        return bench, reason
    seen = {champion_id} if champion_id is not None else set()
    combined = []
    for item in (*bench, *subset):
        if item.champion_id not in seen:
            seen.add(item.champion_id)
            combined.append(item)
    return tuple(combined[:MAXIMUM_BENCH_CHAMPIONS]), None


def snapshot_to_event(snapshot: LcuContextSnapshot, *, sequence: int) -> dict[str, Any]:
    context = None
    if snapshot.gameflow_phase is not None:
        context = {
            "gameflowPhase": snapshot.gameflow_phase,
            "championId": snapshot.champion_id,
            "benchEnabled": snapshot.bench_enabled,
            "benchChampions": [item.as_dict() for item in snapshot.bench],
            "gameId": snapshot.game_id,
            "gameResult": snapshot.game_result,
            "gameResultRaw": snapshot.game_result_raw,
            "gameResultEndpointStatus": snapshot.game_result_status,
            "gameResultEndpointError": snapshot.game_result_error,
        }
    return {
        "type": "lcu_context_state",
        "schema_version": 1,
        "sequence": sequence,
        "observed_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "status": snapshot.status,
        "reason": snapshot.reason,
        "context": context,
    }


class LcuChampSelectPoller:
    def __init__(
        self,
        emit: Callable[[Mapping[str, Any]], None],
        *,
        catalog: ChampionCatalog | None = None,
        interval_seconds: float = 0.5,
        heartbeat_seconds: float = 30.0,
        transport: LcuHttpsTransport | None = None,
        connection_factory: Callable[[], tuple[LcuConnection | None, str]] | None = None,
        provider: EnvironmentLcuConnectionProvider | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds <= 0 or heartbeat_seconds <= 0:
            raise ValueError("LCU poll intervals must be positive")
        self._emit = emit
        self._catalog = catalog or ChampionCatalog.load()
        self._interval = interval_seconds
        self._heartbeat = heartbeat_seconds
        self._transport = transport
        self._provider = provider or EnvironmentLcuConnectionProvider()
        self._connection_factory = connection_factory
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous: tuple[Any, ...] | None = None
        self._last_emit = 0.0
        self._sequence = 0

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("LCU champ-select poller is already running")
        self._stop.clear()
        self._previous = None
        self._last_emit = 0.0
        self._sequence = 0
        self._thread = threading.Thread(
            target=self._run, name="lcu-champ-select-poller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if not thread.is_alive():
                self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = read_lcu_snapshot(
                    catalog=self._catalog,
                    transport=self._transport,
                    connection_factory=self._connection_factory,
                    provider=self._provider,
                )
            except Exception:
                snapshot = LcuContextSnapshot(status="UNAVAILABLE", reason="connect_failed")
            if self._stop.is_set():
                break
            identity = snapshot.identity()
            now = self._clock()
            changed = identity != self._previous
            due = self._previous is None or now - self._last_emit >= self._heartbeat
            if changed or due:
                self._sequence += 1
                self._previous = identity
                self._last_emit = now
                try:
                    self._emit(snapshot_to_event(snapshot, sequence=self._sequence))
                except Exception:
                    pass
            self._stop.wait(self._interval)
