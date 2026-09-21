"""Display estimates and OCR timing, kept separate from recorded pick progress.

Death scanning depends on the level captured at the death edge and confirmed
pick count. A real offer remains observable regardless of that decision.
"""

from __future__ import annotations

HEXCORE_LEVELS = (3, 7, 11, 15)
START_FOUNTAIN_SECONDS = 180.0
RESPAWN_FOUNTAIN_SECONDS = 75.0
PICK_PROBE_SECONDS = 90.0


def next_hexcore_level(completed: int) -> int | None:
    if type(completed) is not int or completed < 0 or completed >= 4:
        return None
    return HEXCORE_LEVELS[completed]


def eligible_offer_count(level: int | None) -> int:
    if type(level) is not int or level < 3:
        return 0
    if level >= 15:
        return 4
    if level >= 11:
        return 3
    if level >= 7:
        return 2
    return 1


def pending_offer_count(level: int | None, completed: int) -> int:
    if type(completed) is not int or completed < 0:
        return 0
    pending = eligible_offer_count(level) - completed
    return pending if pending > 0 else 0


def death_ocr_allowed(level: int | None, confirmed_count: int) -> bool:
    if type(level) is not int or type(confirmed_count) is not int or confirmed_count < 0:
        return False
    if level < 7:
        return False
    if level < 11:
        return confirmed_count < 2
    if level < 15:
        return confirmed_count < 3
    return confirmed_count < 4


def in_hexcore_fountain(
    *,
    is_dead: bool | None,
    game_time: float | None,
    seconds_since_respawn: float | None,
) -> bool:
    if is_dead is True:
        return True
    if game_time is not None and 0.0 <= game_time <= START_FOUNTAIN_SECONDS:
        return True
    if (
        seconds_since_respawn is not None
        and 0.0 <= seconds_since_respawn <= RESPAWN_FOUNTAIN_SECONDS
    ):
        return True
    return False


def hexcore_screen_open(
    *,
    level: int | None,
    is_dead: bool | None,
    game_time: float | None,
    completed: int,
    seconds_since_respawn: float | None,
    offer_visible: bool = False,
    probe_active: bool = False,
    owed: int | None = None,
) -> bool:
    if offer_visible or probe_active:
        return True
    del is_dead, game_time, seconds_since_respawn
    if type(owed) is int and owed > 0:
        return True
    return pending_offer_count(level, completed) > 0


def hexcore_ocr_open(
    *,
    completed: int,
    level: int | None,
    is_dead: bool | None,
    round_closed: bool,
    offer_visible: bool = False,
    seconds_since_respawn: float | None = None,
    death_scan_allowed: bool = False,
) -> bool:
    if type(completed) is not int or completed < 0:
        return False
    if offer_visible and not round_closed:
        return True
    if is_dead is True:
        return death_scan_allowed
    if (
        seconds_since_respawn is not None
        and 0.0 <= seconds_since_respawn <= RESPAWN_FOUNTAIN_SECONDS
    ):
        return death_scan_allowed
    # The initial offer can appear while alive. Subsequent empty-death probes
    # end with the respawn window; an already accepted offer is kept open by
    # offer_visible above, including when its selection is delayed.
    return (
        not round_closed
        and completed == 0
        and type(level) is int
        and level >= HEXCORE_LEVELS[0]
        and is_dead is not True
    )
