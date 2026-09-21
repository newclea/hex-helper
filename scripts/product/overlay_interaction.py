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
class Size:
    width: int
    height: int


@dataclass(frozen=True)
class BubbleLayout:
    direction: str
    bubble: Rect
    root: Rect
    cat_local: Rect
    bubble_local: Rect


@dataclass(frozen=True)
class DragResult:
    dragging: bool
    requested_origin: Point | None = None


class LongPressDrag:
    def __init__(self, threshold_seconds: float = 0.5) -> None:
        self.threshold_seconds = threshold_seconds
        self._press_point: Point | None = None
        self._cat_origin: Point | None = None
        self._pressed_at: float | None = None
        self.dragging = False

    @property
    def active(self) -> bool:
        return self._pressed_at is not None

    def press(self, point: Point, cat_origin: Point, now: float) -> None:
        self._press_point = point
        self._cat_origin = cat_origin
        self._pressed_at = now
        self.dragging = False

    def move(self, point: Point, now: float) -> DragResult:
        if self._pressed_at is None or self._press_point is None or self._cat_origin is None:
            return DragResult(False)
        self.activate(now)
        if not self.dragging:
            return DragResult(False)
        delta = Point(point.x - self._press_point.x, point.y - self._press_point.y)
        origin = Point(self._cat_origin.x + delta.x, self._cat_origin.y + delta.y)
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
        self._cat_origin = None
        self._pressed_at = None
        self.dragging = False


def clamp_rect(candidate: Rect, work_area: Rect) -> Rect:
    """Clamp a rectangle without translating negative virtual-desktop coordinates."""

    width = candidate.right - candidate.left
    height = candidate.bottom - candidate.top
    work_width = work_area.right - work_area.left
    work_height = work_area.bottom - work_area.top
    if width <= 0 or height <= 0 or work_width < width or work_height < height:
        return candidate
    left = min(max(candidate.left, work_area.left), work_area.right - width)
    top = min(max(candidate.top, work_area.top), work_area.bottom - height)
    return Rect(left, top, left + width, top + height)


def place_bubble(cat: Rect, bubble: Size, work_area: Rect, gap: int = 8) -> BubbleLayout:
    """Place a bubble around an absolute cat rectangle and compose root bounds."""

    direction = _bubble_direction(cat, bubble, work_area, gap)
    bubble_rect = _bubble_rect(cat, bubble, direction, gap)
    root = Rect(
        min(cat.left, bubble_rect.left),
        min(cat.top, bubble_rect.top),
        max(cat.right, bubble_rect.right),
        max(cat.bottom, bubble_rect.bottom),
    )
    return BubbleLayout(
        direction=direction,
        bubble=bubble_rect,
        root=root,
        cat_local=_relative_rect(cat, root),
        bubble_local=_relative_rect(bubble_rect, root),
    )


def _bubble_direction(cat: Rect, bubble: Size, work_area: Rect, gap: int) -> str:
    spaces = {
        "top": cat.top - work_area.top,
        "bottom": work_area.bottom - cat.bottom,
        "left": cat.left - work_area.left,
        "right": work_area.right - cat.right,
    }
    vertical = _single_blocked_edge(spaces, ("top", "bottom"), bubble.height + gap)
    horizontal = _single_blocked_edge(spaces, ("left", "right"), bubble.width + gap)
    opposite = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
    if vertical and horizontal:
        preferred = f"{opposite[vertical]}_{opposite[horizontal]}"
    elif vertical:
        preferred = opposite[vertical]
    elif horizontal:
        preferred = opposite[horizontal]
    else:
        preferred = None
    if preferred and _rect_inside(_bubble_rect(cat, bubble, preferred, gap), work_area):
        return preferred
    requirements = {
        "top": bubble.height + gap,
        "bottom": bubble.height + gap,
        "left": bubble.width + gap,
        "right": bubble.width + gap,
    }
    directions = ("top", "bottom", "left", "right")
    return max(
        directions,
        key=lambda item: (
            _rect_inside(_bubble_rect(cat, bubble, item, gap), work_area),
            spaces[item] - requirements[item],
        ),
    )


def _single_blocked_edge(spaces: dict[str, int], edges: tuple[str, str], required: int) -> str | None:
    blocked = tuple(edge for edge in edges if spaces[edge] < required)
    if len(blocked) != 1:
        return None
    return blocked[0]


def _bubble_rect(cat: Rect, bubble: Size, direction: str, gap: int) -> Rect:
    cat_width = cat.right - cat.left
    cat_height = cat.bottom - cat.top
    if "left" in direction:
        left = cat.left - gap - bubble.width
    elif "right" in direction:
        left = cat.right + gap
    else:
        left = cat.left + (cat_width - bubble.width) // 2
    if "top" in direction:
        top = cat.top - gap - bubble.height
    elif "bottom" in direction:
        top = cat.bottom + gap
    else:
        top = cat.top + (cat_height - bubble.height) // 2
    return Rect(left, top, left + bubble.width, top + bubble.height)


def _relative_rect(rect: Rect, root: Rect) -> Rect:
    return Rect(
        rect.left - root.left,
        rect.top - root.top,
        rect.right - root.left,
        rect.bottom - root.top,
    )


def _rect_inside(rect: Rect, container: Rect) -> bool:
    return (
        rect.left >= container.left
        and rect.top >= container.top
        and rect.right <= container.right
        and rect.bottom <= container.bottom
    )


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
