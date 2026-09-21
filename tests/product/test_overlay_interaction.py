from __future__ import annotations

import unittest

from overlay_interaction import LongPressDrag, Point, Rect, clamp_origin


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


if __name__ == "__main__":
    unittest.main()
