"""State selection and deterministic timelines for the GameBuddy cat."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any, Mapping


LOGGER = logging.getLogger(__name__)
ANIMATION_STATES = (
    "idle",
    "greeting",
    "thinking",
    "listening",
    "success",
    "failure",
)
GREETING_SECONDS = 1.2
LISTENING_SECONDS = 0.6
FAILURE_STATES = frozenset({"ocr_error", "recommendation_unavailable", "unsupported_mode"})
THINKING_STATES = frozenset({"ocr_reading", "ocr_confirming", "ocr_updating"})


@dataclass(frozen=True)
class AnimationClip:
    frames: tuple[Path, ...]
    frame_ms: int
    loop: bool


@dataclass(frozen=True)
class AnimationManifest:
    clips: Mapping[str, AnimationClip]
    fallback: Path | None = None


def select_base_state(view: Mapping[str, Any]) -> str:
    """Map a product view to its persistent animation state."""

    if view.get("bubble_visible") is False:
        return "idle"
    state = str(view.get("state") or "")
    if state in FAILURE_STATES:
        return "failure"
    if state in THINKING_STATES:
        return "thinking"
    if state == "recommendation":
        return "success"
    if state == "champ_select":
        return "listening"
    return "idle"


class AnimationStateController:
    """Add finite greeting/listening transitions to persistent view states."""

    def __init__(self) -> None:
        self._last_visible = False
        self._last_base = "idle"
        self._transition: str | None = None
        self._transition_until = 0.0

    def update(self, view: Mapping[str, Any], now: float) -> str:
        visible = view.get("bubble_visible") is not False
        base = select_base_state(view)
        became_visible = visible and not self._last_visible
        entered_thinking = base == "thinking" and self._last_base != "thinking"
        if not visible or base == "failure":
            self._transition = None
        elif entered_thinking:
            self._transition = "listening"
            self._transition_until = now + LISTENING_SECONDS
        elif became_visible and base not in {"thinking", "failure"}:
            self._transition = "greeting"
            self._transition_until = now + GREETING_SECONDS
        elif self._transition is not None and now >= self._transition_until:
            self._transition = None
        self._last_visible = visible
        self._last_base = base
        return self._transition or base


def _safe_frame_path(root: Path, value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def load_animation_manifest(root: Path) -> AnimationManifest | None:
    """Load a complete safe manifest, returning None for any invalid clip."""

    try:
        payload = json.loads((root / "animations.json").read_text("utf-8"))
        clips_value = payload.get("clips") if isinstance(payload, Mapping) else None
        if not isinstance(clips_value, Mapping) or set(clips_value) != set(ANIMATION_STATES):
            return None
        if payload.get("fallback") != "../gamebuddy-cat.png":
            return None
        fallback = (root / ".." / "gamebuddy-cat.png").resolve()
        if not fallback.is_file():
            return None
        clips: dict[str, AnimationClip] = {}
        for state in ANIMATION_STATES:
            value = clips_value.get(state)
            if not isinstance(value, Mapping):
                return None
            frame_ms = value.get("frame_ms")
            frames_value = value.get("frames")
            if not isinstance(frame_ms, int) or not 40 <= frame_ms <= 1000:
                return None
            if not isinstance(value.get("loop"), bool):
                return None
            if not isinstance(frames_value, list) or not frames_value:
                return None
            frames = tuple(_safe_frame_path(root, item) for item in frames_value)
            if any(frame is None for frame in frames):
                return None
            clips[state] = AnimationClip(
                frames=tuple(frame for frame in frames if frame is not None),
                frame_ms=frame_ms,
                loop=bool(value["loop"]),
            )
        return AnimationManifest(clips=clips, fallback=fallback)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        LOGGER.exception("animation manifest load failed root=%s", root)
        return None


class AnimationTimeline:
    """Select a frame from monotonic time without owning a UI timer."""

    def __init__(self, manifest: AnimationManifest) -> None:
        self.manifest = manifest
        self._state = "idle"
        self._started_at = 0.0
        self._frozen_at: float | None = None

    def set_state(self, state: str, now: float) -> None:
        if state not in self.manifest.clips or state == self._state:
            return
        self._state = state
        self._started_at = now
        self._frozen_at = now if self._frozen_at is not None else None

    def frame_path(self, now: float) -> Path | None:
        clip = self.manifest.clips.get(self._state)
        if clip is None or not clip.frames:
            return None
        current = self._frozen_at if self._frozen_at is not None else now
        elapsed_ms = max(0, int((current - self._started_at) * 1000))
        index = elapsed_ms // clip.frame_ms
        if clip.loop:
            index %= len(clip.frames)
        else:
            index = min(index, len(clip.frames) - 1)
        return clip.frames[index]

    def set_frozen(self, frozen: bool, now: float) -> None:
        if frozen and self._frozen_at is None:
            self._frozen_at = now
        elif not frozen and self._frozen_at is not None:
            self._started_at += now - self._frozen_at
            self._frozen_at = None
