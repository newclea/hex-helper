from __future__ import annotations

import unittest

from overlay_interaction import (
    LongPressDrag,
    Point,
    Rect,
    Size,
    clamp_origin,
    clamp_rect,
    place_bubble,
)


class LongPressDragTests(unittest.TestCase):
    def test_release_at_499ms_is_click(self) -> None:
        drag = LongPressDrag(threshold_seconds=0.5)
        drag.press(Point(100, 100), Point(500, 40), 1.0)
        self.assertEqual("click", drag.release(Point(100, 100), 1.499))

    def test_move_at_500ms_starts_drag(self) -> None:
        drag = LongPressDrag(threshold_seconds=0.5)
        drag.press(Point(100, 100), Point(500, 40), 1.0)
        result = drag.move(Point(140, 120), 1.5)
        self.assertEqual(Point(540, 60), result.requested_origin)
        self.assertEqual("drag_end", drag.release(Point(140, 120), 1.6))

    def test_drag_result_is_a_proposed_cat_origin(self) -> None:
        drag = LongPressDrag(threshold_seconds=0.5)
        drag.press(Point(100, 100), Point(-300, 40), 1.0)
        result = drag.move(Point(140, 120), 1.5)
        self.assertEqual(Point(-260, 60), result.requested_origin)

    def test_release_at_500ms_is_not_a_click(self) -> None:
        drag = LongPressDrag(threshold_seconds=0.5)
        drag.press(Point(100, 100), Point(500, 40), 1.0)
        self.assertEqual("drag_end", drag.release(Point(100, 100), 1.5))

    def test_release_outside_cat_is_not_click(self) -> None:
        drag = LongPressDrag()
        drag.press(Point(100, 100), Point(500, 40), 1.0)
        self.assertEqual(
            "none",
            drag.release(Point(400, 400), 1.2, inside_cat=False),
        )

    def test_cancel_clears_pending_press_and_drag(self) -> None:
        drag = LongPressDrag()
        drag.press(Point(100, 100), Point(500, 40), 1.0)
        drag.move(Point(120, 120), 1.5)
        drag.cancel()
        self.assertFalse(drag.active)
        self.assertFalse(drag.dragging)
        self.assertEqual("none", drag.release(Point(120, 120), 1.6))


class ClampOriginTests(unittest.TestCase):
    cat_box = Rect(430, 16, 548, 140)
    window_size = Point(560, 340)

    def test_negative_monitor_coordinates_keep_cat_visible(self) -> None:
        work_area = Rect(-1920, 0, 0, 1040)
        origin = clamp_origin(Point(-2500, -100), self.window_size, self.cat_box, work_area)
        self.assertEqual(Point(-2350, -16), origin)

    def test_right_taskbar_reduces_available_work_area(self) -> None:
        work_area = Rect(0, 0, 1880, 1080)
        origin = clamp_origin(Point(1500, 100), self.window_size, self.cat_box, work_area)
        self.assertEqual(Point(1332, 100), origin)

    def test_bottom_taskbar_clamps_cat_bottom(self) -> None:
        work_area = Rect(0, 0, 1920, 1040)
        origin = clamp_origin(Point(1300, 1000), self.window_size, self.cat_box, work_area)
        self.assertEqual(Point(1300, 900), origin)

    def test_left_and_top_taskbars_clamp_cat_edges(self) -> None:
        work_area = Rect(40, 36, 1920, 1080)
        origin = clamp_origin(Point(-500, -500), self.window_size, self.cat_box, work_area)
        self.assertEqual(Point(-390, 20), origin)

    def test_invalid_work_area_returns_requested_origin(self) -> None:
        requested = Point(10, 20)
        work_area = Rect(0, 0, 0, 0)
        self.assertEqual(
            requested,
            clamp_origin(requested, self.window_size, self.cat_box, work_area),
        )


class ClampRectTests(unittest.TestCase):
    def test_clamp_rect_keeps_cat_on_negative_monitor(self) -> None:
        work_area = Rect(-1920, 0, 0, 1040)
        cat = Rect(-2100, -20, -1988, 104)
        self.assertEqual(Rect(-1920, 0, -1808, 124), clamp_rect(cat, work_area))

    def test_invalid_work_area_returns_candidate(self) -> None:
        cat = Rect(10, 20, 122, 144)
        self.assertEqual(cat, clamp_rect(cat, Rect(0, 0, 100, 100)))


class BubblePlacementTests(unittest.TestCase):
    work_area = Rect(0, 0, 1920, 1040)
    bubble_size = Size(420, 180)

    def assert_direction(self, direction: str, cat: Rect) -> None:
        self.assertEqual(direction, place_bubble(cat, self.bubble_size, self.work_area).direction)

    def test_edges_put_bubble_on_opposite_side(self) -> None:
        cases = (
            ("bottom", Rect(904, 0, 1016, 124)),
            ("top", Rect(904, 916, 1016, 1040)),
            ("right", Rect(0, 458, 112, 582)),
            ("left", Rect(1808, 458, 1920, 582)),
        )
        for direction, cat in cases:
            with self.subTest(direction=direction):
                self.assert_direction(direction, cat)

    def test_corners_put_bubble_diagonally_opposite(self) -> None:
        cases = (
            ("bottom_right", Rect(0, 0, 112, 124)),
            ("bottom_left", Rect(1808, 0, 1920, 124)),
            ("top_right", Rect(0, 916, 112, 1040)),
            ("top_left", Rect(1808, 916, 1920, 1040)),
        )
        for direction, cat in cases:
            with self.subTest(direction=direction):
                self.assert_direction(direction, cat)

    def test_top_right_cat_puts_large_bubble_bottom_left(self) -> None:
        cat = Rect(1796, 0, 1908, 124)
        layout = place_bubble(cat, Size(820, 240), self.work_area)
        self.assertEqual("bottom_left", layout.direction)
        self.assertLessEqual(layout.bubble.right, cat.left)
        self.assertGreaterEqual(layout.bubble.top, cat.top)

    def test_centered_cat_uses_side_with_most_remaining_space(self) -> None:
        cat = Rect(704, 458, 816, 582)
        self.assert_direction("right", cat)

    def test_layout_uses_absolute_negative_coordinates(self) -> None:
        work_area = Rect(-1920, 0, 0, 1040)
        cat = Rect(-1910, 400, -1798, 524)
        layout = place_bubble(cat, Size(420, 180), work_area)
        self.assertEqual("right", layout.direction)
        self.assertEqual(cat.left - layout.root.left, layout.cat_local.left)
        self.assertEqual(layout.bubble.left - layout.root.left, layout.bubble_local.left)


if __name__ == "__main__":
    unittest.main()
