from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from cat_overlay import CatOverlayWindow, HTCLIENT, HTTRANSPARENT
from overlay_interaction import Point, Rect
from rich_text_layout import LaidOutLine, TextRun


class FakeRoot:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.destroyed = False
        self.geometries: list[str] = []

    def after(self, _milliseconds, _callback):
        return "after-id"

    def after_cancel(self, callback_id):
        self.cancelled.append(callback_id)

    def destroy(self):
        self.destroyed = True

    def geometry(self, value):
        self.geometries.append(value)

    def winfo_x(self):
        return 500

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

class FakeCanvas:
    def __init__(self) -> None:
        self.grabbed = False

    def grab_set(self):
        self.grabbed = True

    def grab_release(self):
        self.grabbed = False

    def configure(self, **_kwargs):
        return None


class FakeMenu:
    def __init__(self, *_args, **_kwargs) -> None:
        self.labels: list[str] = []
        self.command = None
        self.popup: tuple[int, int] | None = None
        self.unposted = False

    def add_command(self, *, label, command):
        self.labels.append(label)
        self.command = command

    def bind(self, *_args):
        return None

    def update_idletasks(self):
        return None

    def winfo_reqwidth(self):
        return 40

    def winfo_reqheight(self):
        return 30

    def tk_popup(self, x, y):
        self.popup = (x, y)

    def grab_release(self):
        return None

    def unpost(self):
        self.unposted = True


class FakeTk:
    Menu = FakeMenu


class FakeTimeline:
    def __init__(self) -> None:
        self.frozen: list[bool] = []

    def set_frozen(self, frozen, _now):
        self.frozen.append(frozen)


class RecordingCanvas(FakeCanvas):
    def __init__(self) -> None:
        super().__init__()
        self.items = {}
        self.next_id = 1

    def create_text(self, *_args, **kwargs):
        item_id = self.next_id
        self.next_id += 1
        self.items[item_id] = kwargs
        return item_id

    def bbox(self, item_id):
        item = self.items[item_id]
        width = len(item.get("text", "")) * 10
        return (0, 0, width, 16)

    def delete(self, item_id):
        if item_id == "all":
            self.items.clear()
        else:
            self.items.pop(item_id, None)

    def create_polygon(self, *_args, **kwargs):
        return self.create_text(**kwargs)

    def create_line(self, *_args, **kwargs):
        return self.create_text(**kwargs)

    def create_image(self, *_args, **kwargs):
        return self.create_text(**kwargs)

    def tag_lower(self, _item_id):
        return None


def event_at(x, y, *, x_root=None, y_root=None):
    return SimpleNamespace(
        x=x,
        y=y,
        x_root=x if x_root is None else x_root,
        y_root=y if y_root is None else y_root,
    )


def make_window(*, on_refresh=None, on_close=None, refresh_available=False):
    window = CatOverlayWindow(
        cat_path=Path("missing.png"),
        on_strategy=Mock(),
        on_refresh=on_refresh,
        on_close=on_close,
    )
    window._root = FakeRoot()
    window._canvas = FakeCanvas()
    window._view["refresh_available"] = refresh_available
    return window


class CatOverlayInteractionTests(unittest.TestCase):
    def test_negative_position_uses_native_move_not_tk_geometry(self) -> None:
        window = make_window()
        window._native_hwnd = 7
        window._native_move = Mock(return_value=True)
        self.assertTrue(window._set_native_bounds(Rect(-900, 40, -100, 340)))
        window._native_move.assert_called_once_with(7, -900, 40, 800, 300)
        self.assertEqual("800x300", window._root.geometries[-1])

    def test_monitor_failure_reuses_last_valid_work_area(self) -> None:
        window = make_window()
        expected = Rect(-1920, 0, 0, 1040)
        window._last_work_area = expected
        window._query_native_work_area = Mock(return_value=None)
        self.assertEqual(expected, window._monitor_work_area(SimpleNamespace(x=-200, y=100)))

    def test_monitor_failure_without_history_uses_tk_screen(self) -> None:
        window = make_window()
        window._query_native_work_area = Mock(return_value=None)
        self.assertEqual(Rect(0, 0, 1920, 1080), window._monitor_work_area(SimpleNamespace(x=2, y=3)))

    def test_successful_monitor_query_updates_fallback(self) -> None:
        window = make_window()
        expected = Rect(1920, 0, 3840, 1040)
        window._query_native_work_area = Mock(return_value=expected)
        self.assertEqual(expected, window._monitor_work_area(SimpleNamespace(x=2000, y=100)))
        self.assertEqual(expected, window._last_work_area)

    def test_root_move_failure_keeps_absolute_cat_rectangle(self) -> None:
        window = make_window()
        before = Rect(1000, 64, 1118, 188)
        window._cat_screen_rect = before
        window._cat_local_rect = Rect(20, 30, 138, 154)
        window._click_regions = [(1, 2, 3, 4, "old")]
        before_local = window._cat_local_rect
        before_regions = list(window._click_regions)
        window._native_hwnd = 7
        window._native_move = Mock(return_value=False)
        with self.assertLogs("cat_overlay", level="ERROR"):
            self.assertFalse(window._move_cat_to(Rect(1200, 64, 1318, 188)))
        self.assertEqual(before, window._cat_screen_rect)
        self.assertEqual(before_local, window._cat_local_rect)
        self.assertEqual(before_regions, window._click_regions)
        self.assertEqual([], window._root.geometries)

    def test_native_move_failure_logs_target_and_system_error(self) -> None:
        window = make_window()
        window._native_hwnd = 7
        window._native_move = Mock(return_value=False)
        window._native_error_code = Mock(return_value=123)
        with self.assertLogs("cat_overlay", level="ERROR") as captured:
            self.assertFalse(window._set_native_bounds(Rect(-900, 40, -100, 340)))
        self.assertIn("left=-900 top=40 width=800 height=300 error=123", captured.output[0])

    def test_reflow_keeps_absolute_cat_rectangle(self) -> None:
        window = make_window()
        window._canvas = RecordingCanvas()
        before = Rect(1000, 64, 1118, 188)
        window._cat_screen_rect = before
        window._native_hwnd = 7
        window._native_move = Mock(return_value=True)
        window._monitor_work_area = Mock(return_value=Rect(0, 0, 1920, 1040))
        window._view.update(
            {
                "state": "recommendation",
                "bubble_visible": True,
                "message": "很长的内容" * 20,
            }
        )
        window._draw()
        self.assertEqual(before, window._cat_screen_rect)

    def test_wide_option_pill_sets_content_width(self) -> None:
        window = make_window()
        window._canvas = RecordingCanvas()
        window._view.update(
            {
                "bubble_visible": True,
                "message": "短",
                "options": [{"id": "wide", "title": "甲" * 28, "available": True}],
            }
        )
        self.assertEqual(300, window._bubble_model()["text_width"])

    def test_introduction_width_uses_its_actual_smaller_font(self) -> None:
        window = make_window()
        window._canvas = RecordingCanvas()
        window._text_width = lambda text, font: len(text) * (10 if font[1] == 10 else 20)
        window._view.update(
            {
                "bubble_visible": True,
                "introduction": "引导文字共十五字整整整整整整整",
                "message": "正文六字整整整",
            }
        )
        self.assertEqual(150, window._bubble_model()["text_width"])

    def test_drag_selects_monitor_from_candidate_cat_center(self) -> None:
        window = make_window()
        window._cat_screen_rect = Rect(-300, 40, -182, 164)
        window._drag.press(Point(0, 0), Point(-300, 40), 1.0)
        window._clock = lambda: 1.5
        window._monitor_work_area = Mock(return_value=Rect(-1920, 0, 0, 1040))
        window._move_cat_to = Mock(return_value=True)
        window._on_left_motion(event_at(0, 0, x_root=40, y_root=20))
        window._monitor_work_area.assert_called_once_with(Point(-201, 122))

    def test_long_message_draws_once_without_page_actions(self) -> None:
        window = make_window()
        window._canvas = RecordingCanvas()
        message = "甲乙丙丁戊己庚辛" * 30
        window._view.update({"bubble_visible": True, "message": message})
        window._monitor_work_area = Mock(return_value=Rect(0, 0, 1920, 1040))
        window._draw()
        runs = [
            item.get("text", "")
            for item in window._canvas.items.values()
            if item.get("tags") == ("recommendation",)
        ]
        self.assertEqual(message, "".join(runs))
        self.assertFalse(any(region[4].startswith("__detail_") for region in window._click_regions))

    def test_hit_test_accepts_cat_not_transparent_corner(self) -> None:
        window = make_window()
        self.assertEqual("__cat__", window._target_at(window.width - 72, 74))
        self.assertIsNone(window._target_at(2, window.height - 2))
        self.assertEqual(
            HTCLIENT,
            window._native_hit_result(
                screen_x=window.width - 72,
                screen_y=74,
                window_left=0,
                window_top=0,
            ),
        )
        self.assertEqual(
            HTTRANSPARENT,
            window._native_hit_result(screen_x=2, screen_y=338, window_left=0, window_top=0),
        )

    def test_short_cat_click_keeps_refresh(self) -> None:
        refresh = Mock()
        window = make_window(on_refresh=refresh, refresh_available=True)
        times = iter((1.0, 1.2))
        window._clock = lambda: next(times)
        event = event_at(window.width - 72, 74, x_root=1000, y_root=100)
        window._on_left_press(event)
        window._on_left_release(event)
        refresh.assert_called_once_with()

    def test_action_button_never_begins_drag(self) -> None:
        window = make_window()
        window._click_regions = [(10, 10, 80, 40, "strategy")]
        window._on_left_press(event_at(20, 20))
        self.assertFalse(window._drag.active)

    def test_refresh_action_stays_available_without_stealing_cat_target(self) -> None:
        window = make_window(on_refresh=Mock(), refresh_available=True)
        bounds = window._cat_bounds()
        window._click_regions = [
            (bounds.left, bounds.top, bounds.right, bounds.bottom, "__refresh__")
        ]
        self.assertEqual("__cat__", window._target_at(window.width - 72, 74))
        window._invoke_action("__refresh__")
        window.on_refresh.assert_called_once_with()

    def test_drag_freezes_then_release_resumes_animation(self) -> None:
        window = make_window()
        timeline = FakeTimeline()
        window._animation_timeline = timeline
        times = iter((1.0, 1.5, 1.6))
        window._clock = lambda: next(times)
        pressed = event_at(window.width - 72, 74, x_root=1000, y_root=100)
        moved = event_at(window.width - 72, 74, x_root=1040, y_root=120)
        window._on_left_press(pressed)
        window._activate_long_press()
        window._on_left_release(moved)
        self.assertEqual([True, False], timeline.frozen)

    def test_capture_loss_cancels_active_drag(self) -> None:
        window = make_window()
        timeline = FakeTimeline()
        window._animation_timeline = timeline
        times = iter((1.0, 1.5, 1.5))
        window._clock = lambda: next(times)
        pressed = event_at(window.width - 72, 74, x_root=1000, y_root=100)
        window._on_left_press(pressed)
        window._activate_long_press()
        window._cancel_pointer()
        self.assertFalse(window._drag.active)
        self.assertEqual([True, False], timeline.frozen)

    def test_right_click_outside_cat_is_ignored(self) -> None:
        window = make_window()
        window._exit_menu = FakeMenu()
        window._on_right_click(event_at(2, 2))
        self.assertIsNone(window._exit_menu.popup)

    def test_exit_menu_has_one_item_and_clamps_to_work_area(self) -> None:
        window = make_window()
        window._exit_menu = window._create_exit_menu(FakeTk)
        window._monitor_work_area = lambda _point: Rect(0, 0, 100, 100)
        event = event_at(window.width - 72, 74, x_root=95, y_root=95)
        window._on_right_click(event)
        self.assertEqual(["退出"], window._exit_menu.labels)
        self.assertEqual((60, 70), window._exit_menu.popup)

    def test_escape_dismisses_posted_menu_without_closing(self) -> None:
        closed = Mock()
        window = make_window(on_close=closed)
        window._exit_menu = FakeMenu()
        window._menu_posted = True
        window._on_escape(None)
        self.assertTrue(window._exit_menu.unposted)
        closed.assert_not_called()

    def test_close_cancels_poll_and_notifies_once(self) -> None:
        closed = Mock()
        window = make_window(on_close=closed)
        window._poll_after_id = "poll-id"
        window._close()
        window._close()
        self.assertEqual(["poll-id"], window._root.cancelled)
        self.assertTrue(window._root.destroyed)
        closed.assert_called_once_with()

    def test_draws_label_normal_and_value_bold(self) -> None:
        window = make_window()
        window._canvas = RecordingCanvas()
        normal_font = ("Microsoft YaHei UI", 11)
        bold_font = ("Microsoft YaHei UI", 11, "bold")
        lines = (
            LaidOutLine(
                (TextRun("所需装备 "), TextRun("无尽之刃", True)),
                80,
            ),
        )
        bottom = window._draw_rich_page(lines, 10, 20, normal_font, bold_font, 18)
        drawn = [item for item in window._canvas.items.values() if item.get("tags")]
        self.assertEqual(normal_font, drawn[0]["font"])
        self.assertEqual(bold_font, drawn[1]["font"])
        self.assertEqual("所需装备 ", drawn[0]["text"])
        self.assertEqual("无尽之刃", drawn[1]["text"])
        self.assertEqual(38, bottom)

    def test_plain_wrap_avoids_two_character_last_line(self) -> None:
        window = make_window()
        window._canvas = RecordingCanvas()
        wrapped = window._wrap_text("甲乙丙丁戊己庚", 50, ("Microsoft YaHei UI", 11))
        self.assertEqual("甲乙丙丁\n戊己庚", wrapped)


if __name__ == "__main__":
    unittest.main()
