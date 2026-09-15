"""Transparent top-right GameBuddy cat and speech-bubble window."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import queue
from typing import Any, Callable, Mapping


TRANSPARENT = "#010203"
BUBBLE = "#FFF2CE"
BUBBLE_BORDER = "#D9B85E"
INK = "#302817"
MUTED = "#7E6B42"
BUTTON = "#F4E3B7"
BUTTON_DISABLED = "#D8D0BD"


class CatOverlayWindow:
    """Small Tk host; it never draws over the game's three augment cards."""

    def __init__(
        self,
        *,
        cat_path: Path,
        on_strategy: Callable[[str], None],
        on_refresh: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        width: int = 560,
        height: int = 300,
        **_: Any,
    ) -> None:
        self.cat_path = cat_path
        self.on_strategy = on_strategy
        self.on_refresh = on_refresh
        self.on_ready = on_ready
        self.on_close = on_close
        self.width = max(640, width)
        self.height = max(220, min(340, height))
        self._updates: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._view: dict[str, Any] = {
            "state": "waiting",
            "message": "核宝来了。",
            "options": [],
        }
        self._root: Any = None
        self._canvas: Any = None
        self._cat: Any = None
        self._click_regions: list[tuple[int, int, int, int, str]] = []
        self._wndproc: Any = None
        self._old_wndproc: int | None = None
        self._hwnd: int | None = None

    def set_view(self, view: Mapping[str, Any]) -> None:
        self._updates.put(dict(view))

    def set_text(self, text: str) -> None:
        self.set_view({"state": "legacy", "message": text, "options": []})

    def set_title(self, _title: str) -> None:
        return

    @staticmethod
    def _rounded_rectangle(canvas: Any, box: tuple[int, int, int, int], radius: int, **kwargs: Any) -> None:
        x1, y1, x2, y2 = box
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        canvas.create_polygon(points, smooth=True, **kwargs)

    def _set_click_through(self, enabled: bool) -> None:
        if os.name != "nt" or self._root is None:
            return
        hwnd = int(self._root.winfo_id())
        user32 = ctypes.windll.user32
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
        get_long.restype = ctypes.c_ssize_t
        set_long.restype = ctypes.c_ssize_t
        ex_style = get_long(hwnd, -20)
        ws_ex_transparent = 0x00000020
        ws_ex_noactivate = 0x08000000
        if enabled:
            ex_style |= ws_ex_transparent | ws_ex_noactivate
        else:
            ex_style &= ~ws_ex_transparent
        set_long(hwnd, -20, ex_style)

    def _install_hit_test(self) -> None:
        if os.name != "nt" or self._root is None:
            return
        user32 = ctypes.windll.user32
        hwnd = int(self._root.winfo_id())
        wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        user32.SetWindowLongPtrW.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p
        ]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        old_wndproc = 0

        def window_proc(
            window: int, message: int, wparam: int, lparam: int
        ) -> int:
            if message == 0x0084:  # WM_NCHITTEST
                class Point(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                point = Point(
                    ctypes.c_short(lparam & 0xFFFF).value,
                    ctypes.c_short((lparam >> 16) & 0xFFFF).value,
                )
                user32.ScreenToClient(window, ctypes.byref(point))
                for left, top, right, bottom, _ in self._click_regions:
                    if left <= point.x <= right and top <= point.y <= bottom:
                        return 1  # HTCLIENT
                return -1  # HTTRANSPARENT
            return int(
                user32.CallWindowProcW(
                    old_wndproc, window, message, wparam, lparam
                )
            )

        callback = wndproc_type(window_proc)
        callback_address = ctypes.cast(callback, ctypes.c_void_p)
        old_wndproc = int(user32.SetWindowLongPtrW(hwnd, -4, callback_address))
        if old_wndproc:
            self._hwnd = hwnd
            self._old_wndproc = old_wndproc
            self._wndproc = callback

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        self._click_regions = []
        options = self._view.get("options")
        refresh_available = (
            self.on_refresh is not None
            and self._view.get("refresh_available") is True
        )
        clickable = refresh_available or (isinstance(options, list) and any(
            isinstance(item, Mapping) and item.get("available") is True
            for item in options
        ))
        bubble_left, bubble_top = 8, 12
        bubble_right = self.width - 130
        bubble_bottom = self.height - 18
        self._rounded_rectangle(
            canvas,
            (bubble_left, bubble_top, bubble_right, bubble_bottom),
            14,
            fill=BUBBLE,
            outline=BUBBLE_BORDER,
            width=2,
        )
        canvas.create_polygon(
            bubble_right - 2,
            48,
            bubble_right + 22,
            60,
            bubble_right - 2,
            72,
            fill=BUBBLE,
            outline=BUBBLE_BORDER,
        )
        message = str(self._view.get("message") or "")
        message_item = canvas.create_text(
            28,
            30,
            anchor="nw",
            width=bubble_right - 52,
            text=message,
            fill=INK,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        message_box = canvas.bbox(message_item)
        y = max(92, (message_box[3] + 16) if message_box else 92)
        if isinstance(options, list):
            for option in options[:3]:
                if not isinstance(option, Mapping):
                    continue
                available = option.get("available") is True
                option_id = str(option.get("id") or "")
                title = str(option.get("title") or "")
                subtitle = str(option.get("subtitle") or "")
                self._rounded_rectangle(
                    canvas,
                    (24, y, bubble_right - 18, y + 48),
                    9,
                    fill=BUTTON if available else BUTTON_DISABLED,
                    outline=BUBBLE_BORDER if available else BUTTON_DISABLED,
                )
                canvas.create_text(
                    38, y + 8, anchor="nw", text=title, fill=INK,
                    font=("Microsoft YaHei UI", 10, "bold"),
                )
                canvas.create_text(
                    38, y + 27, anchor="nw", width=bubble_right - 72,
                    text=subtitle, fill=MUTED, font=("Microsoft YaHei UI", 8),
                )
                if available and option_id:
                    self._click_regions.append((24, y, bubble_right - 18, y + 48, option_id))
                y += 54
        if self._cat is not None:
            canvas.create_image(self.width - 72, 74, image=self._cat, anchor="center")
            if refresh_available:
                self._click_regions.append(
                    (self.width - 130, 16, self.width - 12, 140, "__refresh__")
                )
        self._set_click_through(not clickable)

    def _on_click(self, event: Any) -> None:
        for left, top, right, bottom, option_id in self._click_regions:
            if left <= event.x <= right and top <= event.y <= bottom:
                if option_id == "__refresh__" and self.on_refresh is not None:
                    self.on_refresh()
                else:
                    self.on_strategy(option_id)
                return

    def _poll(self) -> None:
        changed = False
        while True:
            try:
                self._view = self._updates.get_nowait()
                changed = True
            except queue.Empty:
                break
        if changed:
            self._draw()
        if self._root is not None:
            self._root.after(50, self._poll)

    def _close(self) -> None:
        if self.on_close is not None:
            self.on_close()
        if os.name == "nt" and self._hwnd and self._old_wndproc:
            ctypes.windll.user32.SetWindowLongPtrW(
                self._hwnd, -4, self._old_wndproc
            )
            self._old_wndproc = None
            self._wndproc = None
        if self._root is not None:
            self._root.destroy()

    def run(self) -> int:
        if os.name != "nt":
            raise RuntimeError("cat overlay requires Windows")
        import tkinter as tk

        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        root.wm_attributes("-transparentcolor", TRANSPARENT)
        screen_width = root.winfo_screenwidth()
        root.geometry(f"{self.width}x{self.height}+{max(0, screen_width - self.width - 12)}+12")
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
        self._install_hit_test()
        image = tk.PhotoImage(file=str(self.cat_path))
        factor = max(1, image.width() // 112)
        self._cat = image.subsample(factor, factor)
        canvas.bind("<Button-1>", self._on_click)
        root.bind("<Escape>", lambda _event: self._close())
        self._draw()
        root.after(50, self._poll)
        if self.on_ready is not None:
            root.after(0, self.on_ready)
        root.mainloop()
        return 0
