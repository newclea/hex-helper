from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from cat_overlay import CatOverlayWindow, HTCLIENT, HTTRANSPARENT
from overlay_interaction import Rect
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
        if item_id != "all":
            self.items.pop(item_id, None)


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
