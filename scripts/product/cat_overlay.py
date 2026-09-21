"""Transparent top-right GameBuddy cat and speech-bubble window."""

from __future__ import annotations

import ctypes
import logging
import math
import os
from pathlib import Path
import queue
import time
from typing import Any, Callable, Mapping

from cat_animation import (
    AnimationStateController,
    AnimationTimeline,
    load_animation_manifest,
)
from overlay_interaction import BubbleLayout, LongPressDrag, Point, Rect, Size, clamp_rect, place_bubble
from rich_text_layout import LaidOutLine, TextRun, content_width, normalize_blocks, wrap_paragraph


TRANSPARENT = "#010203"
BUBBLE = "#FFF2CE"
BUBBLE_BORDER = "#D9B85E"
INK = "#302817"
BUTTON = "#F4E3B7"
BUTTON_SELECTED = "#E8CC84"
BUTTON_SELECTED_BORDER = "#967026"
CAT_WIDTH = 118
CAT_HEIGHT = 124
BUBBLE_MINIMUM_CONTENT_WIDTH = 120
BUBBLE_MAXIMUM_CONTENT_WIDTH = 780
BUBBLE_PADDING = 12
BUTTON_GAP = 6
BUTTON_MINIMUM_HEIGHT = 30
TOP_MARGIN = 64
# Win32 extended-window/message constants.  Keep the overlay non-activating so
# choosing a strategy never takes keyboard focus away from the game.  Button
# hit-testing is handled synchronously through WM_NCHITTEST; cursor polling is
# only used to update the cursor artwork.
GWL_EXSTYLE = -20
GWLP_WNDPROC = -4
GA_ROOT = 2
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WM_MOUSEACTIVATE = 0x0021
WM_NCHITTEST = 0x0084
# Same-integrity UI automation cannot use SendInput while an elevated game is
# foreground because UIPI rejects the injected stream before hit-testing.  A
# narrow window-local message keeps end-to-end accessibility/automation
# deterministic without granting control over the game process.
WM_APP_INVOKE_ACTION = 0x83A7
HTCLIENT = 1
HTTRANSPARENT = -1
MA_NOACTIVATE = 3
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
MONITOR_DEFAULTTONEAREST = 2
WM_CAPTURECHANGED = 0x0215


LOGGER = logging.getLogger(__name__)


class CatOverlayWindow:
    """Transparent host beside the built-in full-screen augment layouts."""

    def __init__(
        self,
        *,
        cat_path: Path,
        animation_root: Path | None = None,
        on_strategy: Callable[[str], None],
        on_refresh: Callable[[], None] | None = None,
        on_tick: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        on_voice_toggle: Callable[[bool], None] | None = None,
        on_greeting_start: Callable[[float], None] | None = None,
        voice_enabled: bool = True,
        width: int = 560,
        height: int = 300,
        **_: Any,
    ) -> None:
        self.cat_path = cat_path
        self.animation_root = animation_root
        self.on_strategy = on_strategy
        self.on_refresh = on_refresh
        self.on_tick = on_tick
        self.on_ready = on_ready
        self.on_close = on_close
        self.on_voice_toggle = on_voice_toggle
        self.on_greeting_start = on_greeting_start
        self._voice_enabled = voice_enabled
        self.width = max(CAT_WIDTH, width)
        self.height = max(CAT_HEIGHT, height)
        self._cat_screen_rect = Rect(0, TOP_MARGIN, CAT_WIDTH, TOP_MARGIN + CAT_HEIGHT)
        self._cat_local_rect = Rect(self.width - CAT_WIDTH, 0, self.width, CAT_HEIGHT)
        self._last_work_area: Rect | None = None
        self._updates: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._view: dict[str, Any] = {
            "state": "waiting",
            "bubble_visible": False,
            "message": "核宝来了。",
            "options": [],
        }
        self._root: Any = None
        self._canvas: Any = None
        self._cat: Any = None
        self._cat_item: Any = None
        self._cat_frames: dict[Path, Any] = {}
        self._animation_state = AnimationStateController()
        self._greeting_started = False
        manifest = load_animation_manifest(animation_root) if animation_root is not None else None
        self._animation_timeline = AnimationTimeline(manifest) if manifest is not None else None
        self._clock = time.monotonic
        self._drag = LongPressDrag()
        self._pressed_action: str | None = None
        self._long_press_after_id: Any = None
        self._poll_after_id: Any = None
        self._exit_menu: Any = None
        self._menu_posted = False
        self._closed = False
        self._click_regions: list[tuple[int, int, int, int, str]] = []
        self._pill_regions: dict[str, tuple[int, int, int, int]] = {}
        self._next_tick_at = 0.0
        self._native_hwnd: Any = None
        self._native_user32: Any = None
        self._native_wndproc_type: Any = None
        self._native_wndproc_callback: Any = None
        self._native_original_wndproc: int | None = None
        self._native_move: Callable[[Any, int, int, int, int], bool] = self._move_native_window
        self._detail_key: tuple[Any, ...] | None = None
        self._presented_topmost_key: tuple[Any, ...] | None = None

    def set_view(self, view: Mapping[str, Any]) -> None:
        self._updates.put(dict(view))

    def set_text(self, text: str) -> None:
        self.set_view({"state": "legacy", "message": text, "options": []})

    def set_title(self, _title: str) -> None:
        return

    @staticmethod
    def _rounded_rectangle(canvas: Any, box: tuple[int, int, int, int], radius: int, **kwargs: Any) -> int:
        x1, y1, x2, y2 = box
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return int(canvas.create_polygon(points, smooth=True, **kwargs))

    @staticmethod
    def _capsule(canvas: Any, box: tuple[int, int, int, int], **kwargs: Any) -> int:
        """Draw parallel top/bottom edges joined by semicircular ends."""
        left, top, right, bottom = box
        radius = (bottom - top) / 2
        center_y = (top + bottom) / 2
        points: list[float] = []
        for center_x, start in ((right - radius, -90), (left + radius, 90)):
            for step in range(21):
                angle = math.radians(start + step * 9)
                points.extend((center_x + radius * math.cos(angle), center_y + radius * math.sin(angle)))
        return int(canvas.create_polygon(points, smooth=False, **kwargs))

    def _pointer_over_action(self) -> bool:
        if os.name != "nt" or self._root is None:
            return bool(self._click_regions)
        from ctypes import wintypes

        class Point(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        point = Point()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetCursorPos.argtypes = [ctypes.POINTER(Point)]
        user32.GetCursorPos.restype = wintypes.BOOL
        if not user32.GetCursorPos(ctypes.byref(point)):
            return False
        local_x = point.x - self._root.winfo_rootx()
        local_y = point.y - self._root.winfo_rooty()
        return self._target_at(local_x, local_y) is not None

    def _action_at(self, local_x: int, local_y: int) -> str | None:
        """Use the drawn pill outline, leaving its transparent corners to League."""

        for left, top, right, bottom, option_id in self._click_regions:
            if left <= local_x <= right and top <= local_y <= bottom:
                if option_id in self._pill_regions:
                    radius = (bottom - top) / 2
                    center_x = min(max(local_x, left + radius), right - radius)
                    center_y = (top + bottom) / 2
                    if (local_x - center_x) ** 2 + (local_y - center_y) ** 2 > radius ** 2:
                        continue
                return option_id
        return None

    def _cat_bounds(self) -> Rect:
        return self._cat_local_rect

    def _target_at(self, local_x: int, local_y: int) -> str | None:
        action = self._action_at(local_x, local_y)
        if action is not None and action != "__refresh__":
            return action
        if self._cat_bounds().contains(Point(local_x, local_y)):
            return "__cat__"
        return action

    def _native_hit_result(
        self,
        *,
        screen_x: int,
        screen_y: int,
        window_left: int,
        window_top: int,
    ) -> int:
        """Compute WM_NCHITTEST without relying on an earlier cursor poll."""

        option_id = self._target_at(
            screen_x - window_left,
            screen_y - window_top,
        )
        return HTCLIENT if option_id is not None else HTTRANSPARENT

    @staticmethod
    def _signed_word(value: int) -> int:
        value &= 0xFFFF
        return value - 0x10000 if value & 0x8000 else value

    @staticmethod
    def _input_ex_style(
        ex_style: int, *, click_through: bool, native_hit_test: bool
    ) -> int:
        """Keep focus in League and select the appropriate hit-test mode."""

        updated = ex_style | WS_EX_NOACTIVATE
        if click_through and not native_hit_test:
            return updated | WS_EX_TRANSPARENT
        return updated & ~WS_EX_TRANSPARENT

    def _native_window_proc(
        self, hwnd: Any, message: int, wparam: int, lparam: int
    ) -> int:
        """Route only action rectangles to Tk and pass all other clicks through."""

        try:
            if message == WM_NCHITTEST:
                from ctypes import wintypes

                rect = wintypes.RECT()
                if self._native_user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    screen_x = self._signed_word(int(lparam))
                    screen_y = self._signed_word(int(lparam) >> 16)
                    return self._native_hit_result(
                        screen_x=screen_x,
                        screen_y=screen_y,
                        window_left=int(rect.left),
                        window_top=int(rect.top),
                    )
                return HTTRANSPARENT
            if message == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if message == WM_CAPTURECHANGED:
                if self._root is not None:
                    self._root.after(0, self._cancel_pointer)
                return 0
            if message == WM_APP_INVOKE_ACTION:
                action_ids = [region[4] for region in self._click_regions]
                action_index = int(wparam) - 1
                if 0 <= action_index < len(action_ids):
                    self._invoke_action(action_ids[action_index])
                return 0
            return int(
                self._native_user32.CallWindowProcW(
                    self._native_original_wndproc,
                    hwnd,
                    message,
                    wparam,
                    lparam,
                )
            )
        except Exception:
            # An exception must never escape a ctypes window callback.  Keep the
            # game usable by failing transparent for input-routing messages.
            if message == WM_NCHITTEST:
                return HTTRANSPARENT
            if message == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if self._native_user32 is not None and self._native_original_wndproc:
                return int(
                    self._native_user32.CallWindowProcW(
                        self._native_original_wndproc,
                        hwnd,
                        message,
                        wparam,
                        lparam,
                    )
                )
            return 0

    def _install_native_hit_test(self) -> None:
        """Subclass the Tk top-level for race-free per-button hit-testing."""

        if os.name != "nt" or self._root is None or self._native_hwnd is not None:
            return
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_ssize_t,
        ]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        hwnd = user32.GetAncestor(self._root.winfo_id(), GA_ROOT)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        original = int(user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC))
        if not original:
            raise ctypes.WinError(ctypes.get_last_error())

        wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._native_user32 = user32
        self._native_hwnd = hwnd
        self._native_wndproc_type = wndproc_type
        self._native_original_wndproc = original
        self._native_wndproc_callback = wndproc_type(self._native_window_proc)
        ctypes.set_last_error(0)
        previous = int(
            user32.SetWindowLongPtrW(
                hwnd,
                GWLP_WNDPROC,
                ctypes.cast(self._native_wndproc_callback, ctypes.c_void_p).value,
            )
        )
        if not previous and ctypes.get_last_error():
            error = ctypes.WinError(ctypes.get_last_error())
            self._native_hwnd = None
            self._native_original_wndproc = None
            self._native_wndproc_callback = None
            self._native_user32 = None
            raise error

        # WM_NCHITTEST now decides whether a point belongs to a button.  A
        # process-wide cursor poll is no longer allowed to make the whole
        # top-level transparent, and NOACTIVATE must remain set at all times.
        self._set_click_through(False)

    def _restore_native_hit_test(self) -> None:
        if (
            self._native_user32 is None
            or self._native_hwnd is None
            or self._native_original_wndproc is None
        ):
            return
        try:
            self._native_user32.SetWindowLongPtrW(
                self._native_hwnd,
                GWLP_WNDPROC,
                self._native_original_wndproc,
            )
        finally:
            self._native_hwnd = None
            self._native_original_wndproc = None
            self._native_wndproc_callback = None
            self._native_wndproc_type = None
            self._native_user32 = None

    def _update_pointer_passthrough(self) -> None:
        over_action = self._pointer_over_action()
        # With the native hit-test installed, input routing is synchronous and
        # must not depend on this 50 ms visual poll.  Retain the old style
        # fallback only for the short interval before subclass installation.
        if self._native_hwnd is None:
            self._set_click_through(not over_action)
        if self._canvas is not None:
            self._canvas.configure(cursor="hand2" if over_action else "")

    def _raise_for_visible_change(self) -> None:
        """Present new information once without activating or fighting other apps."""
        if self._view.get("bubble_visible") is False:
            self._presented_topmost_key = None
            return
        key = (self._view.get("state"), self._detail_key, self.width, self.height)
        if key == self._presented_topmost_key:
            return
        if self._native_hwnd is None or self._native_user32 is None:
            return
        # Another topmost window may have opened after our startup. Reorder
        # only on an actual presentation change, never on the pointer timer or
        # repeated identical snapshots. An unsuccessful attempt is also bounded.
        self._presented_topmost_key = key
        if self._root is not None:
            # Tk queues geometry changes. Apply the phase's complete bounds
            # before a native NOMOVE/NOSIZE call can report the old rectangle
            # back to Tk through WM_WINDOWPOSCHANGED.
            self._root.update_idletasks()
        self._native_user32.SetWindowPos(
            self._native_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE,
        )

    def _set_click_through(self, enabled: bool) -> None:
        if os.name != "nt" or self._root is None:
            return
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(self._root.winfo_id(), 2)
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        ex_style = get_long(hwnd, GWL_EXSTYLE)
        # Never remove NOACTIVATE: a strategy click must leave keyboard focus
        # in League.  Once native hit-testing is active, WS_EX_TRANSPARENT must
        # stay clear so Windows asks WM_NCHITTEST for the point under the click.
        updated = self._input_ex_style(
            int(ex_style),
            click_through=enabled,
            native_hit_test=self._native_hwnd is not None,
        )
        if updated != ex_style:
            set_long(hwnd, GWL_EXSTYLE, updated)
            user32.SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                SWP_NOSIZE
                | SWP_NOMOVE
                | SWP_NOZORDER
                | SWP_NOACTIVATE
                | SWP_FRAMECHANGED,
            )

    def _fit_to_screen(self, screen_width: int) -> None:
        screen_height = int(self._root.winfo_screenheight()) if self._root is not None else self.height
        work = self._monitor_work_area(Point(screen_width // 2, screen_height // 2))
        left = max(work.left, work.right - CAT_WIDTH - 12)
        top = min(max(TOP_MARGIN, work.top), work.bottom - CAT_HEIGHT)
        self._cat_screen_rect = Rect(left, top, left + CAT_WIDTH, top + CAT_HEIGHT)

    def _move_native_window(self, hwnd: Any, left: int, top: int, width: int, height: int) -> bool:
        if self._native_user32 is None:
            return False
        return bool(
            self._native_user32.SetWindowPos(
                hwnd,
                None,
                left,
                top,
                width,
                height,
                SWP_NOACTIVATE | SWP_NOZORDER,
            )
        )

    @staticmethod
    def _native_error_code() -> int:
        get_last_error = getattr(ctypes, "get_last_error", None)
        return int(get_last_error()) if get_last_error is not None else 0

    def _set_native_bounds(self, bounds: Rect) -> bool:
        if self._root is None:
            return False
        width = bounds.right - bounds.left
        height = bounds.bottom - bounds.top
        if self._native_hwnd is not None:
            moved = self._native_move(self._native_hwnd, bounds.left, bounds.top, width, height)
            if not moved:
                LOGGER.error(
                    "native window move failed left=%d top=%d width=%d height=%d error=%d",
                    bounds.left,
                    bounds.top,
                    width,
                    height,
                    self._native_error_code(),
                )
                return False
            self._root.geometry(f"{width}x{height}")
        else:
            self._root.geometry(f"{width}x{height}{bounds.left:+d}{bounds.top:+d}")
            moved = True
        if moved:
            self.width, self.height = width, height
            if self._canvas is not None:
                self._canvas.configure(width=width, height=height)
        return moved

    @staticmethod
    def _enable_dpi_awareness() -> None:
        """Use physical screen coordinates before Tk creates its first window."""
        if os.name != "nt":
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        try:
            enable = user32.SetProcessDpiAwarenessContext
            enable.argtypes = [ctypes.c_void_p]
            enable.restype = ctypes.c_bool
            # Already configured processes keep their existing awareness.
            enable(ctypes.c_void_p(-4))
        except AttributeError:
            user32.SetProcessDPIAware()

    def _text_height(self, text: str, width: int, font: tuple[Any, ...]) -> int:
        if not text:
            return 0
        item = self._canvas.create_text(
            0, 0, anchor="nw", text=self._wrap_text(text, width, font), font=font,
        )
        box = self._canvas.bbox(item)
        self._canvas.delete(item)
        return box[3] - box[1] if box else 0

    def _wrap_text(self, text: str, width: int, font: tuple[Any, ...]) -> str:
        measure = lambda value, _bold: self._text_width(value, font)
        lines = wrap_paragraph((TextRun(text),), width, measure)
        return "\n".join(line.text for line in lines)

    def _text_width(self, text: str, font: tuple[Any, ...]) -> int:
        item = self._canvas.create_text(0, 0, anchor="nw", text=text, font=font)
        box = self._canvas.bbox(item)
        self._canvas.delete(item)
        return box[2] - box[0] if box else 0

    def _layout_rich_lines(
        self,
        blocks: object,
        fallback: str,
        width: int,
        normal_font: tuple[Any, ...],
        bold_font: tuple[Any, ...],
    ) -> tuple[LaidOutLine, ...]:
        measure = lambda value, bold: self._text_width(
            value,
            bold_font if bold else normal_font,
        )
        lines: list[LaidOutLine] = []
        for paragraph in normalize_blocks(blocks, fallback):
            lines.extend(wrap_paragraph(paragraph, width, measure))
        return tuple(lines)

    def _draw_rich_page(
        self,
        lines: tuple[LaidOutLine, ...],
        x: int,
        y: int,
        normal_font: tuple[Any, ...],
        bold_font: tuple[Any, ...],
        line_height: int,
    ) -> int:
        current_y = y
        for line in lines:
            current_x = x
            for run in line.runs:
                font = bold_font if run.bold else normal_font
                self._canvas.create_text(
                    current_x,
                    current_y,
                    anchor="nw",
                    text=run.text,
                    fill=INK,
                    font=font,
                    tags=("recommendation",),
                )
                current_x += self._text_width(run.text, font)
            current_y += line_height
        return current_y

    def _current_cat_image(self) -> Any:
        if self._animation_timeline is None:
            return self._cat
        path = self._animation_timeline.frame_path(self._clock())
        return self._cat_frames.get(path, self._cat)

    def _draw_cat(self) -> None:
        image = self._current_cat_image()
        self._cat_item = None
        if image is not None:
            bounds = self._cat_bounds()
            self._cat_item = self._canvas.create_image(
                (bounds.left + bounds.right) / 2,
                (bounds.top + bounds.bottom) / 2,
                image=image,
                anchor="center",
                tags=("cat",),
            )

    def _sync_animation_state(self, now: float) -> None:
        if not self._greeting_started:
            self._greeting_started = True
            if self.on_greeting_start is not None:
                self.on_greeting_start(now)
        if self._animation_timeline is None:
            return
        state = self._animation_state.update(self._view, now, dragging=self._drag.dragging)
        self._animation_timeline.set_state(state, now)

    def _advance_animation(self, now: float) -> None:
        if self._animation_timeline is None or self._cat_item is None:
            return
        path = self._animation_timeline.frame_path(now)
        image = self._cat_frames.get(path)
        if image is not None:
            try:
                self._canvas.itemconfigure(self._cat_item, image=image)
            except Exception:
                LOGGER.exception("animation update failed; keeping last frame")
                self._animation_timeline = None

    def _visible_options(self) -> list[Mapping[str, Any]]:
        options = self._view.get("options")
        if not isinstance(options, list):
            return []
        return [
            item
            for item in options
            if isinstance(item, Mapping) and item.get("available") is True and item.get("id")
        ][:3]

    def _content_model(self) -> dict[str, Any]:
        introduction = str(self._view.get("introduction") or "")
        message = str(self._view.get("message") or "")
        blocks = self._view.get("message_blocks")
        options = self._visible_options()
        active_id = str(self._view.get("active_strategy_id") or "")
        titles = [" ".join(str(item.get("title") or "").split()) for item in options]
        for item, title in zip(options, titles):
            selected = str(item["id"]) == active_id if active_id else item.get("selected") is True
            if selected:
                current_plan = "当前玩法 " + title
                message = "".join(
                    line for line in message.splitlines(keepends=True)
                    if line.rstrip("\r\n") != current_plan
                )
                if isinstance(blocks, list):
                    blocks = [
                        block for block in blocks
                        if not (
                            isinstance(block, Mapping)
                            and block.get("label") == "当前玩法"
                            and block.get("value") == title
                        )
                    ]
        return {
            "introduction": introduction,
            "message": message,
            "blocks": blocks,
            "options": options,
            "titles": titles,
            "active_id": active_id,
        }

    def _measure_content(self, model: dict[str, Any]) -> dict[str, Any]:
        intro_font = ("Microsoft YaHei UI", 10)
        normal_font = ("Microsoft YaHei UI", 11)
        bold_font = ("Microsoft YaHei UI", 11, "bold")
        title_font = ("Microsoft YaHei UI", 10, "bold")
        paragraphs = normalize_blocks(model["blocks"], model["message"])
        body_measure = lambda value, bold: self._text_width(value, bold_font if bold else normal_font)
        intro_measure = lambda value, _bold: self._text_width(value, intro_font)
        pill_width = max(
            (self._text_width(title, title_font) + 20 for title in model["titles"]),
            default=BUBBLE_MINIMUM_CONTENT_WIDTH,
        )
        minimum_width = min(
            BUBBLE_MAXIMUM_CONTENT_WIDTH,
            max(BUBBLE_MINIMUM_CONTENT_WIDTH, pill_width),
        )
        body_width = content_width(
            paragraphs,
            body_measure,
            minimum_width,
            BUBBLE_MAXIMUM_CONTENT_WIDTH,
        )
        intro_width = content_width(
            normalize_blocks(None, model["introduction"]) if model["introduction"] else (),
            intro_measure,
            minimum_width,
            BUBBLE_MAXIMUM_CONTENT_WIDTH,
        )
        text_width = max(body_width, intro_width)
        model.update(
            {
                "intro_font": intro_font,
                "normal_font": normal_font,
                "bold_font": bold_font,
                "title_font": title_font,
                "text_width": text_width,
            }
        )
        return model

    def _layout_options(self, model: dict[str, Any], start_y: int) -> tuple[list[dict[str, Any]], int]:
        text_width = int(model["text_width"])
        title_font = model["title_font"]
        layouts: list[dict[str, Any]] = []
        x, y, row_height = 0, start_y, 0
        for option, title in zip(model["options"], model["titles"]):
            button_width = min(text_width, max(BUTTON_MINIMUM_HEIGHT, self._text_width(title, title_font) + 20))
            wrapped = self._wrap_text(title, max(1, button_width - 20), title_font)
            button_height = max(BUTTON_MINIMUM_HEIGHT, self._text_height(wrapped, button_width - 20, title_font) + 10)
            if x and x + button_width > text_width:
                x, y, row_height = 0, y + row_height + BUTTON_GAP, 0
            selected = str(option["id"]) == model["active_id"] if model["active_id"] else option.get("selected") is True
            layouts.append(
                {
                    "id": str(option["id"]),
                    "selected": selected,
                    "title": wrapped,
                    "box": (x, y, x + button_width, y + button_height),
                }
            )
            x += button_width + BUTTON_GAP
            row_height = max(row_height, button_height)
        return layouts, y + row_height if layouts else start_y

    def _bubble_model(self) -> dict[str, Any]:
        model = self._measure_content(self._content_model())
        text_width = int(model["text_width"])
        intro_font = model["intro_font"]
        intro = model["introduction"]
        intro_text = self._wrap_text(intro, text_width, intro_font) if intro else ""
        intro_height = self._text_height(intro_text, text_width, intro_font)
        options_top = 14 + intro_height + (12 if intro else 0)
        option_layouts, options_bottom = self._layout_options(model, options_top)
        message_top = options_bottom + 21 if option_layouts else options_top
        lines = self._layout_rich_lines(
            model["blocks"],
            model["message"],
            text_width,
            model["normal_font"],
            model["bold_font"],
        )
        line_height = max(
            self._text_height("国", text_width, model["normal_font"]),
            self._text_height("国", text_width, model["bold_font"]),
        ) + 3
        content_bottom = max(88, message_top + len(lines) * line_height + 18)
        model.update(
            {
                "intro_font": intro_font,
                "intro_text": intro_text,
                "options_top": options_top,
                "option_layouts": option_layouts,
                "message_top": message_top,
                "lines": lines,
                "line_height": line_height,
                "bubble_size": Size(text_width + BUBBLE_PADDING * 2, content_bottom),
            }
        )
        return model

    @staticmethod
    def _tail_points(direction: str, bubble: Rect, cat: Rect) -> tuple[int, ...]:
        cat_x = (cat.left + cat.right) // 2
        cat_y = (cat.top + cat.bottom) // 2
        if "left" in direction or direction == "left":
            center = min(max(cat_y, bubble.top + 18), bubble.bottom - 18)
            return bubble.right, center - 10, cat.left, cat_y, bubble.right, center + 10
        if "right" in direction or direction == "right":
            center = min(max(cat_y, bubble.top + 18), bubble.bottom - 18)
            return bubble.left, center - 10, cat.right, cat_y, bubble.left, center + 10
        if "top" in direction or direction == "top":
            center = min(max(cat_x, bubble.left + 18), bubble.right - 18)
            return center - 10, bubble.bottom, cat_x, cat.top, center + 10, bubble.bottom
        center = min(max(cat_x, bubble.left + 18), bubble.right - 18)
        return center - 10, bubble.top, cat_x, cat.bottom, center + 10, bubble.top

    def _draw_options(self, model: dict[str, Any], bubble: Rect) -> None:
        for option in model["option_layouts"]:
            left, top, right, bottom = option["box"]
            box = (left + bubble.left, top + bubble.top, right + bubble.left, bottom + bubble.top)
            option_id = option["id"]
            self._capsule(
                self._canvas,
                box,
                fill=BUTTON_SELECTED if option["selected"] else BUTTON,
                outline=BUTTON_SELECTED_BORDER if option["selected"] else BUBBLE_BORDER,
                width=2 if option["selected"] else 1,
                tags=(f"option-button:{option_id}",),
            )
            self._canvas.create_text(
                (box[0] + box[2]) / 2,
                (box[1] + box[3]) / 2,
                anchor="center",
                text=option["title"],
                fill=INK,
                font=model["title_font"],
                tags=(f"option-title:{option_id}",),
            )
            self._click_regions.append((*box, option_id))
            self._pill_regions[option_id] = box

    def _paint_bubble(self, model: dict[str, Any], layout: Any) -> None:
        bubble = layout.bubble_local
        background = self._rounded_rectangle(
            self._canvas,
            (bubble.left, bubble.top, bubble.right, bubble.bottom),
            14,
            fill=BUBBLE,
            outline=BUBBLE_BORDER,
            width=2,
            tags=("bubble-background",),
        )
        tail = self._canvas.create_polygon(
            *self._tail_points(layout.direction, bubble, layout.cat_local),
            fill=BUBBLE,
            outline=BUBBLE_BORDER,
        )
        text_left = bubble.left + BUBBLE_PADDING
        if model["intro_text"]:
            self._canvas.create_text(
                text_left,
                bubble.top + 14,
                anchor="nw",
                text=model["intro_text"],
                fill=INK,
                font=model["intro_font"],
                tags=("introduction",),
            )
        self._draw_options(model, Rect(text_left, bubble.top, bubble.right, bubble.bottom))
        if model["option_layouts"]:
            y = bubble.top + model["message_top"] - 10
            self._canvas.create_line(
                text_left,
                y,
                bubble.right - BUBBLE_PADDING,
                y,
                fill=BUBBLE_BORDER,
                tags=("recommendation-divider",),
            )
        self._draw_rich_page(
            model["lines"],
            text_left,
            bubble.top + model["message_top"],
            model["normal_font"],
            model["bold_font"],
            model["line_height"],
        )
        self._canvas.tag_lower(background)
        self._canvas.tag_lower(tail)

    def _layout_for_cat(self, cat: Rect) -> tuple[Any, dict[str, Any] | None]:
        center = Point((cat.left + cat.right) // 2, (cat.top + cat.bottom) // 2)
        work = self._monitor_work_area(center)
        if self._view.get("bubble_visible") is False:
            layout = BubbleLayout(
                "none",
                cat,
                cat,
                Rect(0, 0, CAT_WIDTH, CAT_HEIGHT),
                Rect(0, 0, 0, 0),
            )
            return layout, None
        model = self._bubble_model()
        return place_bubble(cat, model["bubble_size"], work), model

    def _move_cat_to(self, candidate: Rect) -> bool:
        layout, model = self._layout_for_cat(candidate)
        if not self._set_native_bounds(layout.root):
            return False
        self._cat_screen_rect = candidate
        self._cat_local_rect = layout.cat_local
        self._canvas.delete("all")
        self._click_regions = []
        self._pill_regions = {}
        if model is not None:
            self._paint_bubble(model, layout)
        else:
            self._presented_topmost_key = None
        self._draw_cat()
        if self._cat_item is not None and self.on_refresh is not None:
            if self._view.get("refresh_available") is True:
                bounds = self._cat_bounds()
                self._click_regions.append(
                    (bounds.left, bounds.top, bounds.right, bounds.bottom, "__refresh__")
                )
        self._update_pointer_passthrough()
        self._raise_for_visible_change()
        return True

    def _draw(self) -> None:
        self._detail_key = (
            self._view.get("introduction"),
            self._view.get("message"),
            repr(self._view.get("message_blocks")),
            repr(self._view.get("options")),
        )
        self._move_cat_to(self._cat_screen_rect)

    def _query_native_work_area(self, point: Point) -> Rect | None:
        if os.name != "nt":
            return None
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        native_point = wintypes.POINT(point.x, point.y)
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor = user32.MonitorFromPoint(native_point, MONITOR_DEFAULTTONEAREST)
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            return Rect(int(work.left), int(work.top), int(work.right), int(work.bottom))
        return None

    def _monitor_work_area(self, point: Point) -> Rect:
        work = self._query_native_work_area(point)
        if work is not None:
            self._last_work_area = work
            return work
        if self._last_work_area is not None:
            return self._last_work_area
        width = int(self._root.winfo_screenwidth()) if self._root is not None else self.width
        height = int(self._root.winfo_screenheight()) if self._root is not None else self.height
        return Rect(0, 0, width, height)

    def _cancel_long_press_timer(self) -> None:
        if self._root is not None and self._long_press_after_id is not None:
            try:
                self._root.after_cancel(self._long_press_after_id)
            except Exception:
                LOGGER.debug("long-press timer was already released", exc_info=True)
        self._long_press_after_id = None

    def _release_pointer_capture(self) -> None:
        if self._canvas is None:
            return
        try:
            self._canvas.grab_release()
        except Exception:
            LOGGER.debug("pointer capture was already released", exc_info=True)

    def _activate_long_press(self) -> None:
        self._long_press_after_id = None
        now = self._clock()
        if not self._drag.activate(now):
            return
        self._sync_animation_state(now)

    def _on_left_press(self, event: Any) -> None:
        target = self._target_at(int(event.x), int(event.y))
        if target != "__cat__":
            self._pressed_action = target
            return
        self._pressed_action = None
        origin = Point(self._cat_screen_rect.left, self._cat_screen_rect.top)
        self._drag.press(Point(int(event.x_root), int(event.y_root)), origin, self._clock())
        if self._canvas is not None:
            try:
                self._canvas.grab_set()
            except Exception:
                LOGGER.exception("pointer capture failed")
        if self._root is not None:
            self._long_press_after_id = self._root.after(500, self._activate_long_press)

    def _on_left_motion(self, event: Any) -> None:
        now = self._clock()
        result = self._drag.move(Point(int(event.x_root), int(event.y_root)), now)
        if not result.dragging or result.requested_origin is None:
            return
        candidate = Rect(
            result.requested_origin.x,
            result.requested_origin.y,
            result.requested_origin.x + CAT_WIDTH,
            result.requested_origin.y + CAT_HEIGHT,
        )
        center = Point((candidate.left + candidate.right) // 2, (candidate.top + candidate.bottom) // 2)
        final_cat = clamp_rect(candidate, self._monitor_work_area(center))
        self._move_cat_to(final_cat)

    def _on_left_release(self, event: Any) -> None:
        self._cancel_long_press_timer()
        self._release_pointer_capture()
        target = self._target_at(int(event.x), int(event.y))
        if self._pressed_action is not None:
            pressed = self._pressed_action
            self._pressed_action = None
            if target == pressed:
                self._invoke_action(pressed)
            return
        now = self._clock()
        result = self._drag.release(
            Point(int(event.x_root), int(event.y_root)),
            now,
            inside_cat=target == "__cat__",
        )
        self._sync_animation_state(now)
        if result == "click" and self._view.get("refresh_available") is True:
            if self.on_refresh is not None:
                self.on_refresh()

    def _cancel_pointer(self) -> None:
        was_dragging = self._drag.dragging
        self._cancel_long_press_timer()
        self._release_pointer_capture()
        self._drag.cancel()
        self._pressed_action = None
        if was_dragging:
            self._sync_animation_state(self._clock())

    def _dismiss_menu(self) -> None:
        if self._exit_menu is not None and self._menu_posted:
            try:
                self._exit_menu.unpost()
            except Exception:
                LOGGER.debug("exit menu was already dismissed", exc_info=True)
        self._menu_posted = False

    def _on_right_click(self, event: Any) -> None:
        if self._target_at(int(event.x), int(event.y)) != "__cat__":
            return
        if self._menu_posted:
            self._dismiss_menu()
            return
        self._cancel_pointer()
        if self._exit_menu is None:
            return
        self._refresh_context_menu()
        self._exit_menu.update_idletasks()
        work = self._monitor_work_area(Point(int(event.x_root), int(event.y_root)))
        maximum_left = max(work.left, work.right - self._exit_menu.winfo_reqwidth())
        maximum_top = max(work.top, work.bottom - self._exit_menu.winfo_reqheight())
        left = min(max(int(event.x_root), work.left), maximum_left)
        top = min(max(int(event.y_root), work.top), maximum_top)
        self._menu_posted = True
        try:
            self._exit_menu.tk_popup(left, top)
        except Exception:
            LOGGER.exception("exit menu popup failed")
            self._menu_posted = False
        finally:
            try:
                self._exit_menu.grab_release()
            except Exception:
                LOGGER.debug("exit menu grab was already released", exc_info=True)

    def _on_escape(self, _event: Any) -> None:
        if self._menu_posted:
            self._dismiss_menu()
        else:
            self._close()

    def _menu_labels(self) -> list[str]:
        voice_label = "关闭语音" if self._voice_enabled else "开启语音"
        return [voice_label, "退出"]

    def _toggle_voice(self) -> None:
        if self.on_voice_toggle is not None:
            self.on_voice_toggle(not self._voice_enabled)

    def set_voice_enabled(self, enabled: bool) -> None:
        self._voice_enabled = bool(enabled)
        self._refresh_context_menu()

    def _refresh_context_menu(self) -> None:
        if self._exit_menu is None:
            return
        try:
            self._exit_menu.entryconfigure(0, label=self._menu_labels()[0])
        except Exception:
            LOGGER.debug("voice menu label update failed", exc_info=True)

    def _create_context_menu(self, tk: Any) -> Any:
        menu = tk.Menu(self._root, tearoff=False)
        menu.add_command(label=self._menu_labels()[0], command=self._toggle_voice)
        menu.add_command(label="退出", command=self._close)
        menu.bind("<Unmap>", lambda _event: setattr(self, "_menu_posted", False))
        return menu

    def _invoke_action(self, option_id: str) -> None:
        if option_id == "__refresh__" and self.on_refresh is not None:
            self.on_refresh()
        else:
            self.on_strategy(option_id)

    def _poll(self) -> None:
        if self._closed:
            return
        now = self._clock()
        if self.on_tick is not None and now >= self._next_tick_at:
            self._next_tick_at = now + 0.25
            self.on_tick()
        changed = False
        while True:
            try:
                self._view = self._updates.get_nowait()
                changed = True
            except queue.Empty:
                break
        self._sync_animation_state(now)
        if changed:
            self._draw()
        else:
            self._advance_animation(now)
            self._update_pointer_passthrough()
        if self._root is not None:
            self._poll_after_id = self._root.after(50, self._poll)

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._dismiss_menu()
        self._cancel_pointer()
        if self._root is not None and self._poll_after_id is not None:
            try:
                self._root.after_cancel(self._poll_after_id)
            except Exception:
                LOGGER.debug("poll timer was already released", exc_info=True)
        self._poll_after_id = None
        try:
            if self.on_close is not None:
                self.on_close()
        finally:
            if self._root is not None:
                self._restore_native_hit_test()
                self._root.destroy()

    def _load_cat_images(self, tk: Any) -> None:
        image = tk.PhotoImage(file=str(self.cat_path))
        factor = max(1, image.width() // 112)
        self._cat = image.subsample(factor, factor)
        if self._animation_timeline is None:
            return
        try:
            paths = {
                path
                for clip in self._animation_timeline.manifest.clips.values()
                for path in clip.frames
            }
            frames: dict[Path, Any] = {}
            for path in paths:
                frame = tk.PhotoImage(file=str(path))
                frame_factor = max(1, frame.width() // 112)
                frames[path] = frame.subsample(frame_factor, frame_factor)
            self._cat_frames = frames
        except Exception:
            LOGGER.exception("animation frames failed to load; using static cat")
            self._cat_frames = {}
            self._animation_timeline = None

    def run(self) -> int:
        if os.name != "nt":
            raise RuntimeError("cat overlay requires Windows")
        import tkinter as tk

        self._enable_dpi_awareness()
        root = tk.Tk()
        # Configure the native no-activate style before the first visible
        # frame so starting the assistant cannot pull keyboard focus from an
        # already-running game.
        root.withdraw()
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        root.wm_attributes("-transparentcolor", TRANSPARENT)
        self._fit_to_screen(int(root.winfo_screenwidth()))
        canvas = tk.Canvas(
            root,
            width=self.width,
            height=self.height,
            bg=TRANSPARENT,
            highlightthickness=0,
            borderwidth=0,
        )
        self._canvas = canvas
        canvas.pack(fill="both", expand=True)
        root.update_idletasks()
        self._install_native_hit_test()
        self._load_cat_images(tk)
        try:
            self._exit_menu = self._create_context_menu(tk)
        except Exception:
            LOGGER.exception("exit menu creation failed")
            self._exit_menu = None
        canvas.bind("<ButtonPress-1>", self._on_left_press)
        canvas.bind("<B1-Motion>", self._on_left_motion)
        canvas.bind("<ButtonRelease-1>", self._on_left_release)
        canvas.bind("<Button-3>", self._on_right_click)
        root.bind("<Escape>", self._on_escape)
        self._sync_animation_state(self._clock())
        self._draw()
        root.deiconify()
        self._poll_after_id = root.after(50, self._poll)
        if self.on_ready is not None:
            root.after(0, self.on_ready)
        try:
            root.mainloop()
        finally:
            self._restore_native_hit_test()
        return 0
