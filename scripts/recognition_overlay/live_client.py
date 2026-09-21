"""Read-only Live Client Data poller for in-game champion and mode.

Host and port are fixed to https://127.0.0.1:2999.  There is no token, and
the current hexcore offer is never taken from this API.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.request import Request


LIVE_CLIENT_HOST = "127.0.0.1"
LIVE_CLIENT_PORT = 2999
LIVE_CLIENT_TIMEOUT_SECONDS = 0.5
MAXIMUM_RESPONSE_BYTES = 1 * 1024 * 1024
ACTIVE_PLAYER_NAME_PATH = "/liveclientdata/activeplayername"
PLAYER_LIST_PATH = "/liveclientdata/playerlist"
GAME_STATS_PATH = "/liveclientdata/gamestats"
ALLOWED_PATHS = frozenset(
    {ACTIVE_PLAYER_NAME_PATH, PLAYER_LIST_PATH, GAME_STATS_PATH}
)


@dataclass(frozen=True)
class LiveClientHttpResult:
    ok: bool
    status_code: int = 0
    body: str = ""
    error_code: str = ""


@dataclass(frozen=True)
class LiveClientSnapshot:
    status: str
    reason: str
    champion_name: str | None = None
    game_mode: str | None = None
    level: int | None = None
    is_dead: bool | None = None
    respawn_timer: float | None = None
    game_time: float | None = None

    def identity(self) -> tuple[Any, ...]:
        start_fountain = (
            self.game_time is not None and 0.0 <= self.game_time <= 40.0
        )
        return (
            self.status,
            self.reason,
            self.champion_name,
            self.game_mode,
            self.level,
            self.is_dead,
            start_fountain,
        )


class LiveClientHttpsTransport:
    def __init__(self) -> None:
        self._context = ssl._create_unverified_context()

    def get(self, path: str) -> LiveClientHttpResult:
        if path not in ALLOWED_PATHS:
            return LiveClientHttpResult(ok=False, error_code="path_not_allowed")
        url = f"https://{LIVE_CLIENT_HOST}:{LIVE_CLIENT_PORT}{path}"
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(
                request, timeout=LIVE_CLIENT_TIMEOUT_SECONDS, context=self._context
            ) as response:
                status_code = int(getattr(response, "status", 0) or 0)
                raw = response.read(MAXIMUM_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            return LiveClientHttpResult(
                ok=False,
                status_code=int(error.code),
                error_code=f"http_{error.code}",
            )
        except Exception:
            return LiveClientHttpResult(ok=False, error_code="connect_failed")
        if status_code != 200:
            return LiveClientHttpResult(
                ok=False, status_code=status_code, error_code=f"http_{status_code}"
            )
        if len(raw) > MAXIMUM_RESPONSE_BYTES:
            return LiveClientHttpResult(ok=False, error_code="response_too_large")
        return LiveClientHttpResult(
            ok=True, status_code=status_code, body=raw.decode("utf-8", errors="replace")
        )


def _visible_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text or len(text) > limit:
        return None
    if any(ord(character) < 0x20 or character == "\x7f" for character in text):
        return None
    return text


def parse_active_player_name(body: str) -> str | None:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return _visible_text(value, limit=256)


def parse_game_mode(body: str) -> str | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None
    mode = _visible_text(data.get("gameMode"), limit=32)
    if mode is None or not mode.replace("_", "").isalnum():
        return None
    return mode


def parse_game_time(body: str) -> float | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None
    value = data.get("gameTime")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return float(value)


def _identity_matches(player: Mapping[str, Any], active_name: str) -> bool:
    candidates: list[str] = []
    for key in ("summonerName", "riotId", "riotIdGameName"):
        text = _visible_text(player.get(key), limit=256)
        if text:
            candidates.append(text)
    game_name = _visible_text(player.get("riotIdGameName"), limit=256)
    tag_line = _visible_text(player.get("riotIdTagLine"), limit=32)
    if game_name and tag_line:
        candidates.append(f"{game_name}#{tag_line}")
    return active_name in candidates


def _match_player(
    player_list_body: str, active_name: str
) -> Mapping[str, Any] | None:
    try:
        data = json.loads(player_list_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not active_name:
        return None
    matched: Mapping[str, Any] | None = None
    for item in data:
        if not isinstance(item, Mapping):
            return None
        if not _identity_matches(item, active_name):
            continue
        if matched is not None:
            return None
        matched = item
    if matched is None:
        for item in data:
            if isinstance(item, Mapping) and item.get("isLocalPlayer") is True:
                if matched is not None:
                    return None
                matched = item
    return matched


def _bounded_level(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if type(value) is not int or not 1 <= value <= 255:
        return None
    return value


def parse_player_state(
    player_list_body: str, active_name: str
) -> dict[str, Any] | None:
    matched = _match_player(player_list_body, active_name)
    if matched is None:
        return None
    champion = _visible_text(matched.get("championName"), limit=64)
    if champion is None:
        return None
    respawn = matched.get("respawnTimer")
    if isinstance(respawn, bool) or not isinstance(respawn, (int, float)):
        respawn = None
    else:
        respawn = float(respawn)
    is_dead = matched.get("isDead") if type(matched.get("isDead")) is bool else None
    return {
        "championName": champion,
        "level": _bounded_level(matched.get("level")),
        "isDead": is_dead,
        "respawnTimer": respawn,
    }


def parse_champion_name(player_list_body: str, active_name: str) -> str | None:
    state = parse_player_state(player_list_body, active_name)
    if state is None:
        return None
    return str(state["championName"])


def read_live_client_snapshot(
    transport: LiveClientHttpsTransport | None = None,
) -> LiveClientSnapshot:
    client = transport or LiveClientHttpsTransport()
    name_response = client.get(ACTIVE_PLAYER_NAME_PATH)
    if not name_response.ok:
        reason = name_response.error_code or "unavailable"
        status = "UNAVAILABLE" if reason == "connect_failed" else "INVALID_RESPONSE"
        return LiveClientSnapshot(status=status, reason=reason)
    active_name = parse_active_player_name(name_response.body)
    if active_name is None:
        return LiveClientSnapshot(status="INVALID_RESPONSE", reason="active_player_name")

    list_response = client.get(PLAYER_LIST_PATH)
    if not list_response.ok:
        return LiveClientSnapshot(
            status="INVALID_RESPONSE",
            reason=list_response.error_code or "player_list",
        )
    player = parse_player_state(list_response.body, active_name)
    if player is None:
        return LiveClientSnapshot(status="INVALID_RESPONSE", reason="champion_unavailable")

    game_mode: str | None = None
    game_time: float | None = None
    stats_response = client.get(GAME_STATS_PATH)
    if stats_response.ok:
        game_mode = parse_game_mode(stats_response.body)
        game_time = parse_game_time(stats_response.body)

    return LiveClientSnapshot(
        status="READY",
        reason="ok",
        champion_name=str(player["championName"]),
        game_mode=game_mode,
        level=player.get("level") if type(player.get("level")) is int else None,
        is_dead=player.get("isDead") if type(player.get("isDead")) is bool else None,
        respawn_timer=player.get("respawnTimer")
        if isinstance(player.get("respawnTimer"), float)
        else None,
        game_time=game_time,
    )


def snapshot_to_event(snapshot: LiveClientSnapshot) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "live_client_state",
        "status": snapshot.status,
        "reason": snapshot.reason,
        "championName": snapshot.champion_name,
        "gameMode": snapshot.game_mode,
        "gameTime": snapshot.game_time,
    }
    if snapshot.champion_name is not None:
        event["player"] = {
            "championName": snapshot.champion_name,
            "level": snapshot.level,
            "isDead": snapshot.is_dead,
            "respawnTimer": snapshot.respawn_timer,
        }
    return event


class LiveClientPoller:
    def __init__(
        self,
        emit: Callable[[Mapping[str, Any]], None],
        *,
        interval_seconds: float = 1.0,
        heartbeat_seconds: float = 15.0,
        transport: LiveClientHttpsTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds <= 0 or heartbeat_seconds <= 0:
            raise ValueError("Live Client poll intervals must be positive")
        self._emit = emit
        self._interval = interval_seconds
        self._heartbeat = heartbeat_seconds
        self._transport = transport
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous: tuple[Any, ...] | None = None
        self._last_emit = 0.0

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Live Client poller is already running")
        self._stop.clear()
        self._previous = None
        self._last_emit = 0.0
        self._thread = threading.Thread(
            target=self._run, name="live-client-poller", daemon=True
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
                snapshot = read_live_client_snapshot(self._transport)
            except Exception:
                snapshot = LiveClientSnapshot(status="UNAVAILABLE", reason="connect_failed")
            if self._stop.is_set():
                break
            identity = snapshot.identity()
            now = self._clock()
            changed = identity != self._previous
            due = self._previous is None or now - self._last_emit >= self._heartbeat
            if changed or due:
                self._previous = identity
                self._last_emit = now
                try:
                    self._emit(snapshot_to_event(snapshot))
                except Exception:
                    pass
            self._stop.wait(self._interval)
