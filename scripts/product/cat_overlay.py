"""Transparent top-right GameBuddy cat and speech-bubble window."""

from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import queue
import time
from typing import Any, Callable, Mapping


TRANSPARENT = "#010203"
BUBBLE = "#FFF2CE"
BUBBLE_BORDER = "#D9B85E"
INK = "#302817"
MUTED = "#7E6B42"
BUTTON = "#F4E3B7"
BUTTON_SELECTED = "#E8CC84"
BUTTON_SELECTED_BORDER = "#967026"
MINIMUM_HEIGHT = 340
# Long recommendations grow downwards within the available screen height.
MAXIMUM_HEIGHT = 720
BUTTON_GAP = 6
BUTTON_MINIMUM_HEIGHT = 30
# Leave the game's top-right score, clock and performance counters readable.
TOP_MARGIN = 64
# The built-in measured layouts end at x <= 0.811 of a full-screen frame.
# Reserve the first 82% for the offer, allowing a little calibration movement.
# Custom ROI layouts and games on another monitor require their actual bounds.
OFFER_RIGHT_BOUNDARY = 0.82
# The measured 1920x1080 champion-select client has its WeGame panel below
# y=278 and hero cards below y=335. Keep this phase's copy in the top band.
# Other screen sizes retain the conservative right-side layout until measured.
CHAMPION_BAND_WIDTH = 560
CHAMPION_BAND_HEIGHT = 206
# Chinese closing punctuation, including ASCII variants used by the copy.
# Automatic line/page breaks must carry the preceding character with these.
LINE_END_PUNCTUATION = frozenset("，。！？；：、）》】〕〉」』”’…％,.;:!?)]}>%~～")

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


class CatOverlayWindow:
    """Transparent host beside the built-in full-screen augment layouts."""

    def __init__(
        self,
        *,
        cat_path: Path,
        on_strategy: Callable[[str], None],
        on_refresh: Callable[[], None] | None = None,
        on_tick: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        width: int = 560,
        height: int = 300,
        **_: Any,
    ) -> None:
        self.cat_path = cat_path
        self.on_strategy = on_strategy
        self.on_refresh = on_refresh
        self.on_tick = on_tick
        self.on_ready = on_ready
        self.on_close = on_close
        self._preferred_width = max(420, min(560, width))
        self.width = self._preferred_width
        self._stacked_layout = False
        self._champion_band = False
        self._screen_width: int | None = None
        self._window_left: int | None = None
        # The window grows downwards for wrapped pills and recommendation text.
        self._base_height = max(MINIMUM_HEIGHT, min(MAXIMUM_HEIGHT, height))
        self.height = self._base_height
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
        self._click_regions: list[tuple[int, int, int, int, str]] = []
        self._pill_regions: dict[str, tuple[int, int, int, int]] = {}
        self._next_tick_at = 0.0
        self._native_hwnd: Any = None
        self._native_user32: Any = None
        self._native_wndproc_type: Any = None
        self._native_wndproc_callback: Any = None
        self._native_original_wndproc: int | None = None
        self._detail_key: tuple[Any, ...] | None = None
        self._detail_page = 0
        self._detail_pages: list[str] = []
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
        return self._action_at(local_x, local_y) is not None

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

    def _native_hit_result(
        self,
        *,
        screen_x: int,
        screen_y: int,
        window_left: int,
        window_top: int,
    ) -> int:
        """Compute WM_NCHITTEST without relying on an earlier cursor poll."""

        option_id = self._action_at(
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
        key = (self._view.get("state"), self._detail_key, self._detail_page,
               self.width, self._stacked_layout)
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

    def _maximum_height(self) -> int:
        maximum_height = CHAMPION_BAND_HEIGHT if self._champion_band else MAXIMUM_HEIGHT
        if self._root is not None:
            screen_height = int(self._root.winfo_screenheight())
            top = max(0, int(self._root.winfo_y()))
            maximum_height = min(
                maximum_height,
                max(1, screen_height - top - 12),
            )
        return maximum_height

    @staticmethod
    def _screen_layout(screen_width: int, preferred_width: int) -> tuple[int, int, bool]:
        """Return width/left/stacked for the clear right side of measured cards."""
        right_margin = 12
        safe_left = math.ceil(screen_width * OFFER_RIGHT_BOUNDARY) + 4
        available = max(1, screen_width - right_margin - safe_left)
        width = min(preferred_width, available)
        return width, max(0, screen_width - width - right_margin), width < preferred_width

    def _fit_to_screen(self, screen_width: int) -> None:
        self._champion_band = self._uses_champion_band(screen_width)
        if self._champion_band:
            self.width, left, self._stacked_layout = CHAMPION_BAND_WIDTH, screen_width - CHAMPION_BAND_WIDTH - 12, False
        else:
            self.width, left, self._stacked_layout = self._screen_layout(screen_width, self._preferred_width)
        self._screen_width = screen_width
        self._window_left = left
        if self._canvas is not None:
            self._canvas.configure(width=self.width)
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        if self._root is None:
            return
        bounds = f"{self.width}x{self.height}"
        if self._window_left is not None:
            # A height-only request in the same event turn must retain the
            # pending target position, not the previous native window position.
            bounds += f"+{self._window_left}+{TOP_MARGIN}"
        self._root.geometry(bounds)

    def _uses_champion_band(self, screen_width: int) -> bool:
        return (
            self._view.get("state") == "champ_select"
            and screen_width == 1920
            and self._root is not None
            and int(self._root.winfo_screenheight()) == 1080
        )

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

    def _resize_height(self, required_height: int) -> None:
        target = min(
            self._maximum_height(),
            max(self._base_height, int(required_height)),
        )
        if target == self.height:
            return
        self.height = target
        if self._canvas is not None:
            self._canvas.configure(height=target)
        self._apply_geometry()

    def _text_height(self, text: str, width: int, font: tuple[Any, ...]) -> int:
        if not text:
            return 0
        item = self._canvas.create_text(
            0, 0, anchor="nw", text=self._wrap_text(text, width, font), font=font,
        )
        box = self._canvas.bbox(item)
        self._canvas.delete(item)
        return box[3] - box[1] if box else 0

    @staticmethod
    def _safe_line_end(text: str, end: int) -> int:
        while end > 1 and text[end:].lstrip(" \t")[:1] in LINE_END_PUNCTUATION:
            end -= 1
        return end

    def _wrap_text(self, text: str, width: int, font: tuple[Any, ...]) -> str:
        """Wrap measured lines without leaving closing punctuation at the start.

        Original paragraph breaks and every source character are retained.
        Canvas automatic wrapping is disabled for this measured output so it
        cannot split the protected character/punctuation pair a second time.
        """
        lines: list[str] = []
        for paragraph in text.split("\n"):
            remaining = paragraph
            if not remaining:
                lines.append("")
            while remaining:
                low, high = 1, len(remaining)
                while low < high:
                    middle = (low + high + 1) // 2
                    if self._text_width(remaining[:middle], font) <= width:
                        low = middle
                    else:
                        high = middle - 1
                end = low
                if end < len(remaining):
                    # Prefer a nearby space, retaining it rather than changing
                    # the copy. Chinese lines otherwise wrap by character.
                    boundary = max(remaining.rfind(mark, end // 2, end) for mark in (" ", "\t"))
                    if boundary >= 0:
                        end = boundary + 1
                    end = self._safe_line_end(remaining, end)
                lines.append(remaining[:end])
                remaining = remaining[end:]
        return "\n".join(lines)

    def _paginate_text(
        self, text: str, width: int, font: tuple[Any, ...], height: int,
    ) -> list[str]:
        """Keep long reasons readable without pushing strategy buttons offscreen."""
        pages: list[str] = []
        remaining = text
        while remaining:
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if self._text_height(remaining[:middle], width, font) <= height:
                    low = middle
                else:
                    high = middle - 1
            end = low
            if end < len(remaining):
                # Prefer a sentence boundary near the page end, retaining every
                # character so the user can read the complete explanation.
                boundary = max(
                    remaining.rfind(mark, end // 2, end)
                    for mark in ("\n", "。", "；", "！", "？")
                )
                if boundary >= 0:
                    end = boundary + 1
                end = self._safe_line_end(remaining, end)
            pages.append(remaining[:end])
            remaining = remaining[end:]
        return pages or [""]

    def _text_width(self, text: str, font: tuple[Any, ...]) -> int:
        item = self._canvas.create_text(0, 0, anchor="nw", text=text, font=font)
        box = self._canvas.bbox(item)
        self._canvas.delete(item)
        return box[2] - box[0] if box else 0

    def _pill_title(self, text: str, width: int, font: tuple[Any, ...]) -> str:
        if self._text_width(text, font) <= width:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self._text_width(text[:middle].rstrip() + "…", font) <= width:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "…"

    def _draw(self) -> None:
        if self._screen_width is not None and self._champion_band != self._uses_champion_band(self._screen_width):
            # Phase changes need a reflow even if the display size is unchanged.
            self._fit_to_screen(self._screen_width)
        canvas = self._canvas
        canvas.delete("all")
        self._click_regions = []
        self._pill_regions = {}
        if self._view.get("bubble_visible") is False:
            self._presented_topmost_key = None
            self._resize_height(self._base_height)
            if self._cat is not None:
                canvas.create_image(self.width - 72, 74, image=self._cat, anchor="center")
            self._set_click_through(True)
            return
        options = self._view.get("options")
        option_rows = [
            item for item in options
            if isinstance(item, Mapping) and item.get("available") is True and item.get("id")
        ][:3] if isinstance(options, list) else []
        refresh_available = (
            self.on_refresh is not None
            and self._view.get("refresh_available") is True
        )
        # Narrow screens place the cat above the bubble instead of reserving
        # another 130 horizontal pixels beside it. Only the safe strip is used.
        bubble_left = 8 if self._stacked_layout else 16
        bubble_top = 156 if self._stacked_layout else 12
        bubble_right = self.width - (8 if self._stacked_layout else 130)
        text_left, text_right = bubble_left + 12, bubble_right - 12
        text_width = text_right - text_left
        introduction = str(self._view.get("introduction") or "")
        message = str(self._view.get("message") or "")
        active_id = str(self._view.get("active_strategy_id") or "")
        detail_key = (
            introduction, message, active_id,
            tuple((str(item.get("id") or ""), str(item.get("title") or ""), bool(item.get("selected"))) for item in option_rows),
        )
        if self._detail_key != detail_key:
            self._detail_key = detail_key
            self._detail_page = 0

        maximum_height = self._maximum_height()
        introduction_font = ("Microsoft YaHei UI", 10)
        title_font = ("Microsoft YaHei UI", 10, "bold")
        introduction_height = self._text_height(introduction, text_width, introduction_font)
        introduction_top = bubble_top + 14
        options_top = introduction_top + introduction_height + (12 if introduction else 0)
        button_height = max(BUTTON_MINIMUM_HEIGHT, self._text_height("国", text_width, title_font) + 10)
        layouts: list[dict[str, Any]] = []
        x, y = text_left, options_top
        for option in option_rows:
            option_id = str(option["id"])
            selected = option_id == active_id if active_id else option.get("selected") is True
            full_title = " ".join(str(option.get("title") or "").split())
            title = self._pill_title(full_title, text_width - 20, title_font)
            button_width = max(button_height, min(text_width, self._text_width(title, title_font) + 20))
            if x > text_left and x + button_width > text_right:
                x, y = text_left, y + button_height + BUTTON_GAP
            box = (x, y, x + button_width, y + button_height)
            layouts.append({"id": option_id, "selected": selected, "title": title, "box": box})
            if selected:
                current_plan = "当前玩法 " + full_title
                if title == full_title:
                    # The active capsule already shows the complete name.
                    # Keep the body for the recommendation and requirements.
                    message = "".join(
                        line for line in message.splitlines(keepends=True)
                        if line.rstrip("\r\n") != current_plan
                    )
                elif current_plan not in message.splitlines():
                    # A shortened capsule still needs its full name, once.
                    message = current_plan + "\n" + message
            x += button_width + BUTTON_GAP
        options_bottom = y + button_height if layouts else options_top
        message_top = options_bottom + 21 if layouts else options_top
        message_budget = max(1, maximum_height - message_top - 40)
        message_font: tuple[Any, ...] = ("Microsoft YaHei UI", 11)
        for size in (11, 10, 9):
            message_font = ("Microsoft YaHei UI", size)
            if self._text_height(message, text_width, message_font) <= message_budget:
                break
        needs_pages = self._text_height(message, text_width, message_font) > message_budget
        page_controls_height = 28 if needs_pages else 0
        self._detail_pages = self._paginate_text(
            message, text_width, message_font,
            max(1, message_budget - page_controls_height),
        )
        self._detail_page = min(self._detail_page, len(self._detail_pages) - 1)
        if introduction:
            canvas.create_text(
                text_left, introduction_top, anchor="nw",
                text=self._wrap_text(introduction, text_width, introduction_font), fill=INK, font=introduction_font,
                tags=("introduction",),
            )
        for option in layouts:
            option_id = option["id"]
            selected = option["selected"]
            left, top, right, bottom = option["box"]
            self._capsule(
                canvas, option["box"],
                fill=BUTTON_SELECTED if selected else BUTTON,
                outline=BUTTON_SELECTED_BORDER if selected else BUBBLE_BORDER,
                width=2 if selected else 1,
                tags=(f"option-button:{option_id}",),
            )
            canvas.create_text(
                (left + right) / 2, (top + bottom) / 2,
                anchor="center", text=option["title"], fill=INK,
                font=title_font, tags=(f"option-title:{option_id}",),
            )
            self._click_regions.append((left, top, right, bottom, option_id))
            self._pill_regions[option_id] = option["box"]
        if layouts:
            canvas.create_line(
                text_left, message_top - 10, text_right, message_top - 10,
                fill=BUBBLE_BORDER, tags=("recommendation-divider",),
            )
        message_item = canvas.create_text(
            text_left, message_top, anchor="nw",
            text=self._wrap_text(self._detail_pages[self._detail_page], text_width, message_font), fill=INK,
            font=message_font, tags=("recommendation",),
        )
        message_box = canvas.bbox(message_item)
        message_bottom = message_box[3] if message_box is not None else message_top
        y = message_bottom + 12
        content_bottom = message_bottom + 18
        if len(self._detail_pages) > 1:
            for left, right, label, action, enabled in (
                (text_left, text_left + 32, "‹", "__detail_previous__", self._detail_page > 0),
                (text_right - 32, text_right, "›", "__detail_next__", self._detail_page + 1 < len(self._detail_pages)),
            ):
                canvas.create_text((left + right) // 2, y + 9, text=label, fill=INK if enabled else MUTED, font=("Microsoft YaHei UI", 12, "bold"))
                if enabled:
                    self._click_regions.append((left, y - 3, right, y + 23, action))
            canvas.create_text(
                (text_left + text_right) // 2, y + 9,
                text=f"说明 {self._detail_page + 1}/{len(self._detail_pages)}",
                fill=MUTED, font=("Microsoft YaHei UI", 8),
            )
            content_bottom = y + page_controls_height + 8
        desired_bubble_bottom = max(bubble_top + 88, content_bottom)
        self._resize_height(desired_bubble_bottom + 18)
        bubble_bottom = min(self.height - 18, desired_bubble_bottom)
        background = self._rounded_rectangle(
            canvas, (bubble_left, bubble_top, bubble_right, bubble_bottom), 14,
            fill=BUBBLE, outline=BUBBLE_BORDER, width=2,
            tags=("bubble-background",),
        )
        tail_points = (
            (self.width - 94, bubble_top + 2, self.width - 72, bubble_top - 18, self.width - 50, bubble_top + 2)
            if self._stacked_layout else
            (bubble_right - 2, 48, bubble_right + 22, 60, bubble_right - 2, 72)
        )
        tail = canvas.create_polygon(*tail_points, fill=BUBBLE, outline=BUBBLE_BORDER)
        canvas.tag_lower(background)
        canvas.tag_lower(tail)
        if self._cat is not None:
            canvas.create_image(self.width - 72, 74, image=self._cat, anchor="center")
            if refresh_available:
                self._click_regions.append(
                    (self.width - 130, 16, self.width - 12, 140, "__refresh__")
                )
        self._update_pointer_passthrough()
        self._raise_for_visible_change()

    def _on_click(self, event: Any) -> None:
        option_id = self._action_at(int(event.x), int(event.y))
        if option_id is None:
            return
        self._invoke_action(option_id)

    def _invoke_action(self, option_id: str) -> None:
        if option_id in {"__detail_previous__", "__detail_next__"}:
            step = 1 if option_id == "__detail_next__" else -1
            self._detail_page = max(0, min(len(self._detail_pages) - 1, self._detail_page + step))
            self._draw()
        elif option_id == "__refresh__" and self.on_refresh is not None:
            self.on_refresh()
        else:
            self.on_strategy(option_id)

    def _poll(self) -> None:
        now = time.monotonic()
        if self.on_tick is not None and now >= self._next_tick_at:
            self._next_tick_at = now + 0.25
            self.on_tick()
        changed = False
        if self._root is not None and self._screen_width is not None:
            screen_width = int(self._root.winfo_screenwidth())
            if screen_width != self._screen_width:
                self._fit_to_screen(screen_width)
                changed = True
        while True:
            try:
                self._view = self._updates.get_nowait()
                changed = True
            except queue.Empty:
                break
        if changed:
            self._draw()
        else:
            self._update_pointer_passthrough()
        if self._root is not None:
            self._root.after(50, self._poll)

    def _close(self) -> None:
        if self.on_close is not None:
            self.on_close()
        if self._root is not None:
            self._restore_native_hit_test()
            self._root.destroy()

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
        image = tk.PhotoImage(file=str(self.cat_path))
        factor = max(1, image.width() // 112)
        self._cat = image.subsample(factor, factor)
        canvas.bind("<Button-1>", self._on_click)
        root.bind("<Escape>", lambda _event: self._close())
        self._draw()
        root.deiconify()
        root.after(50, self._poll)
        if self.on_ready is not None:
            root.after(0, self.on_ready)
        try:
            root.mainloop()
        finally:
            self._restore_native_hit_test()
        return 0
