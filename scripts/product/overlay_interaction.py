"""Pure pointer and monitor geometry for the GameBuddy overlay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    def contains(self, point: Point) -> bool:
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom


@dataclass(frozen=True)
class DragResult:
    dragging: bool
    requested_origin: Point | None = None


class LongPressDrag:
    def __init__(self, threshold_seconds: float = 0.5) -> None:
        self.threshold_seconds = threshold_seconds
        self._press_point: Point | None = None
        self._window_origin: Point | None = None
        self._pressed_at: float | None = None
        self.dragging = False

    @property
    def active(self) -> bool:
        return self._pressed_at is not None

    def press(self, point: Point, window_origin: Point, now: float) -> None:
        self._press_point = point
        self._window_origin = window_origin
        self._pressed_at = now
        self.dragging = False

    def move(self, point: Point, now: float) -> DragResult:
        if self._pressed_at is None or self._press_point is None or self._window_origin is None:
            return DragResult(False)
        self.activate(now)
        if not self.dragging:
            return DragResult(False)
        delta = Point(point.x - self._press_point.x, point.y - self._press_point.y)
        origin = Point(self._window_origin.x + delta.x, self._window_origin.y + delta.y)
        return DragResult(True, origin)

    def activate(self, now: float) -> bool:
        if self._pressed_at is not None and now - self._pressed_at >= self.threshold_seconds:
            self.dragging = True
        return self.dragging

    def release(self, point: Point, now: float, *, inside_cat: bool = True) -> str:
        del point
        was_dragging = self.activate(now)
        was_click = (
            self._pressed_at is not None
            and inside_cat
            and now - self._pressed_at < self.threshold_seconds
        )
        self.cancel()
        if was_dragging:
            return "drag_end"
        return "click" if was_click else "none"

    def cancel(self) -> None:
        self._press_point = None
        self._window_origin = None
        self._pressed_at = None
        self.dragging = False


def clamp_origin(requested: Point, window_size: Point, cat_box: Rect, work_area: Rect) -> Point:
    """Clamp only as far as needed to keep the cat inside the monitor work area."""

    del window_size
    minimum_x = work_area.left - cat_box.left
    maximum_x = work_area.right - cat_box.right
    minimum_y = work_area.top - cat_box.top
    maximum_y = work_area.bottom - cat_box.bottom
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return requested
    return Point(
        min(max(requested.x, minimum_x), maximum_x),
        min(max(requested.y, minimum_y), maximum_y),
    )
