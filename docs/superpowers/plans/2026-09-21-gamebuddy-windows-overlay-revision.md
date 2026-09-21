# GameBuddy Windows Overlay Revision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix multi-monitor dragging, make the bubble content-sized up to twice its old maximum,
place it around the anchored cat, and replace all incorrect animation frames.

**Architecture:** Keep one transparent Tk window, but store the cat rectangle in absolute virtual-desktop coordinates.
Pure geometry code clamps the cat and composes the bubble around it; the Tk adapter renders local coordinates and uses
Win32 `SetWindowPos` for absolute movement.
Existing product states feed a revised animation controller and five approved visual poses.

**Tech Stack:** Python 3.11 standard library, tkinter, Win32 `ctypes`, PNG assets, JSON, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-21-gamebuddy-windows-overlay-voice-design.md`

## Global Constraints

- Target 64-bit Windows and keep `一键启动小猫.cmd` working.
- Do not add a runtime Python dependency.
- Do not change OCR, recommendations, LCU, Live Client, or the C++ vision engine.
- Keep left-click retry, 500ms long press, right-click, topmost, no-activate, and transparent hit testing intact.
- Bubble text width is content-sized with an effective maximum near 780px; it is not fixed at 780px.
- Do not shrink fonts, summarize visible text, paginate, scroll, or use the old narrow-screen stacked layout.
- Every changed method is at most 80 lines and every changed line is at most 120 characters.
- Make no unrelated rename, formatting change, comment change, or refactor.

## File Structure

- Modify `scripts/product/overlay_interaction.py`: absolute cat geometry, bubble direction, and composition.
- Modify `scripts/product/rich_text_layout.py`: measured content-width selection.
- Modify `scripts/product/cat_overlay.py`: anchored rendering and native absolute movement.
- Modify `scripts/product/cat_animation.py`: direct state mapping and drag override.
- Replace `assets/gamebuddy/**/*.png` and update `assets/gamebuddy/animations.json`.
- Modify focused tests under `tests/product/`.
- Modify `README.md` and `docs/windows-gamebuddy-acceptance.md`.

## Review Focus

1. A monitor left or above the primary monitor uses negative absolute coordinates without right-edge mirroring.
2. The target monitor changes only after the proposed cat center crosses into it, not when the cursor crosses first.
3. Reflowing short or long content changes the root window but never changes the cat's absolute rectangle.
4. Explicit newlines, mixed font weights, empty introductions, and wide option pills all affect content width correctly.
5. Capture loss during a drag restores the current business animation without firing the short-click refresh.

---

### Task 1: Absolute cat and bubble geometry

**Files:**
- Modify: `scripts/product/overlay_interaction.py`
- Modify: `tests/product/test_overlay_interaction.py`

**Interfaces:**
- Consumes: `Point`, `Rect`, a proposed cat `Rect`, bubble `Size`, and monitor work-area `Rect`.
- Produces: `Size(width: int, height: int)`.
- Produces: `BubbleLayout(direction: str, bubble: Rect, root: Rect, cat_local: Rect, bubble_local: Rect)`.
- Produces: `clamp_rect(candidate: Rect, work_area: Rect) -> Rect`.
- Produces: `place_bubble(cat: Rect, bubble: Size, work_area: Rect, gap: int = 8) -> BubbleLayout`.
- Changes: `LongPressDrag.press(point, cat_origin, now)` tracks the cat origin rather than the Tk root origin.

- [ ] **Step 1: Write failing absolute-drag and clamp tests**

```python
def test_drag_result_is_a_proposed_cat_origin(self):
    drag = LongPressDrag(threshold_seconds=0.5)
    drag.press(Point(100, 100), Point(-300, 40), 1.0)
    result = drag.move(Point(140, 120), 1.5)
    self.assertEqual(Point(-260, 60), result.requested_origin)

def test_clamp_rect_keeps_cat_on_negative_monitor(self):
    work = Rect(-1920, 0, 0, 1040)
    cat = Rect(-2100, -20, -1988, 104)
    self.assertEqual(Rect(-1920, 0, -1808, 124), clamp_rect(cat, work))
```

- [ ] **Step 2: Run the focused tests and verify the new imports fail**

Run:

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_overlay_interaction -v
```

Expected: FAIL because `Size`, `BubbleLayout`, `clamp_rect`, and `place_bubble` do not exist.

- [ ] **Step 3: Add immutable geometry types and clamp the cat rectangle**

```python
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


def clamp_rect(candidate: Rect, work_area: Rect) -> Rect:
    width = candidate.right - candidate.left
    height = candidate.bottom - candidate.top
    left = min(max(candidate.left, work_area.left), work_area.right - width)
    top = min(max(candidate.top, work_area.top), work_area.bottom - height)
    return Rect(left, top, left + width, top + height)
```

Return the candidate unchanged when the work area cannot contain the cat. Remove `clamp_origin` only after all callers
and tests have migrated to `clamp_rect`.

- [ ] **Step 4: Write failing direction and composition tests**

Cover all four edges, all four corners, and a centered cat. Pin these examples:

```python
def test_top_right_cat_puts_bubble_bottom_left(self):
    work = Rect(0, 0, 1920, 1040)
    cat = Rect(1796, 0, 1908, 124)
    layout = place_bubble(cat, Size(820, 240), work)
    self.assertEqual("bottom_left", layout.direction)
    self.assertLessEqual(layout.bubble.right, cat.left)
    self.assertGreaterEqual(layout.bubble.top, cat.top)

def test_layout_uses_absolute_negative_coordinates(self):
    work = Rect(-1920, 0, 0, 1040)
    cat = Rect(-1910, 400, -1798, 524)
    layout = place_bubble(cat, Size(420, 180), work)
    self.assertEqual("right", layout.direction)
    self.assertEqual(cat.left - layout.root.left, layout.cat_local.left)
```

- [ ] **Step 5: Implement deterministic bubble placement**

Classify an edge when the free space on that side is smaller than the bubble dimension plus the 8px gap.
At corners, combine vertical and horizontal opposites into `top_left`, `top_right`, `bottom_left`, or `bottom_right`.
At the center, score `top`, `bottom`, `left`, and `right` by whether the complete bubble fits, then by remaining space.
Build `root` as the union of cat and bubble rectangles and derive both local rectangles from that root.

- [ ] **Step 6: Run tests and commit**

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_overlay_interaction -v
git add scripts/product/overlay_interaction.py tests/product/test_overlay_interaction.py
git commit -m "fix: anchor GameBuddy geometry to the active monitor"
```

### Task 2: Content-sized bubble and anchored native window

**Files:**
- Modify: `scripts/product/rich_text_layout.py`
- Modify: `scripts/product/cat_overlay.py`
- Modify: `tests/product/test_rich_text_layout.py`
- Modify: `tests/product/test_cat_overlay.py`

**Interfaces:**
- Consumes: normalized paragraphs, font measurement callback, and measured option-button widths.
- Produces: `content_width(paragraphs, measure, minimum_width, maximum_width) -> int`.
- Produces: `CatOverlayWindow._set_native_bounds(bounds: Rect) -> bool`.
- Stores: `CatOverlayWindow._cat_screen_rect` as the sole absolute position state.
- Stores: `CatOverlayWindow._last_work_area` for Win32 query failure fallback.
- Uses: `place_bubble` from Task 1 after measuring the complete bubble.

- [ ] **Step 1: Write failing adaptive-width tests**

```python
def test_short_text_uses_measured_width(self):
    paragraphs = ((TextRun("推荐：巨人杀手"),),)
    width = content_width(paragraphs, measure, minimum_width=120, maximum_width=780)
    self.assertEqual(140, width)

def test_long_text_stops_at_double_width_cap(self):
    paragraphs = ((TextRun("甲" * 100),),)
    width = content_width(paragraphs, measure, minimum_width=120, maximum_width=780)
    self.assertEqual(780, width)

def test_wide_control_sets_the_minimum_content_width(self):
    paragraphs = ((TextRun("短"),),)
    width = content_width(paragraphs, measure, minimum_width=260, maximum_width=780)
    self.assertEqual(260, width)
```

- [ ] **Step 2: Run the focused layout test and verify failure**

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_rich_text_layout -v
```

Expected: FAIL because `content_width` is absent.

- [ ] **Step 3: Implement measured width selection**

```python
def content_width(paragraphs, measure, minimum_width: int, maximum_width: int) -> int:
    measured = max(
        (sum(measure(run.text, run.bold) for run in paragraph) for paragraph in paragraphs),
        default=0,
    )
    return min(maximum_width, max(minimum_width, measured))
```

The caller supplies the widest complete option pill plus its padding as `minimum_width`. Keep the existing punctuation
and orphan balancing when `measured` exceeds `maximum_width`.

- [ ] **Step 4: Write failing anchored-window tests**

Extend `FakeRoot` and a fake native mover. Verify:

```python
def test_negative_position_uses_native_move_not_tk_geometry(self):
    window = make_window()
    window._native_hwnd = 7
    window._native_move = Mock(return_value=True)
    window._set_native_bounds(Rect(-900, 40, -100, 340))
    window._native_move.assert_called_once_with(7, -900, 40, 800, 300)
    self.assertEqual("800x300", window._root.geometries[-1])

def test_reflow_keeps_absolute_cat_rectangle(self):
    window = make_window()
    window._cat_screen_rect = Rect(1000, 64, 1112, 188)
    before = window._cat_screen_rect
    window.set_view({"state": "recommendation", "message": "很长的内容" * 20})
    window._draw()
    self.assertEqual(before, window._cat_screen_rect)
```

Also make `GetMonitorInfoW` fail in a test. Assert `_monitor_work_area` returns the last valid work area;
when no valid work area exists, assert it returns the Tk-reported screen rectangle.

- [ ] **Step 5: Replace root-origin dragging with cat anchoring**

In `CatOverlayWindow`, initialize the default cat rectangle from the primary monitor work area. On drag, construct the
candidate cat rectangle from `DragResult.requested_origin`, query the work area with the candidate cat center, clamp it,
then redraw from the final cat rectangle. Do not query the monitor from `event.x_root`.

Add a narrow `_set_native_bounds` adapter that requests Tk size as `"{width}x{height}"` and calls `SetWindowPos` with
absolute `left`, `top`, `width`, and `height`. Update the stored anchor only when the native move succeeds.
Save each successful monitor query in `_last_work_area` and use the tested fallback order when Win32 reports failure.

- [ ] **Step 6: Rework bubble drawing without pagination or font shrinking**

Measure introduction, structured paragraphs, and option pills at the existing font sizes. Pass their required width to
`content_width` with the 780px maximum. Wrap only after the width is selected. Calculate the full content height, call
`place_bubble`, resize the root to the returned union, and draw every line once.

Remove `_detail_page`, `_detail_pages`, `paginate_lines` use, page controls, page actions, `_stacked_layout`, and the
1920x1080 champion-band branch. Keep the option-button and refresh hit regions unchanged after converting them to the
new local coordinates.

- [ ] **Step 7: Add the remaining review-focus tests**

Test an explicit newline, mixed normal/bold runs, an empty introduction, a 300px option pill, a bubble content change,
and a root move failure. The failure test must assert that `_cat_screen_rect` keeps its previous value.

- [ ] **Step 8: Run tests and commit**

```bash
PYTHONPATH=scripts/product python3 -m unittest \
  tests.product.test_rich_text_layout \
  tests.product.test_cat_overlay \
  tests.product.test_overlay_interaction -v
git add scripts/product/rich_text_layout.py scripts/product/cat_overlay.py tests/product
git commit -m "fix: place adaptive GameBuddy bubbles around the cat"
```

### Task 3: Approved animation states and assets

**Files:**
- Modify: `scripts/product/cat_animation.py`
- Modify: `tests/product/test_cat_animation.py`
- Replace: `assets/gamebuddy/idle/*.png`
- Replace: `assets/gamebuddy/greeting/*.png`
- Replace: `assets/gamebuddy/listening/*.png`
- Replace: `assets/gamebuddy/thinking/*.png`
- Replace: `assets/gamebuddy/success/*.png`
- Replace: `assets/gamebuddy/failure/*.png`
- Modify: `assets/gamebuddy/animations.json`
- Modify: `scripts/product/cat_overlay.py`
- Modify: `tests/product/test_cat_overlay.py`

**Interfaces:**
- Produces: `AnimationStateController.start(now: float) -> str` returning `greeting` once.
- Produces: `AnimationStateController.update(view, now, dragging=False) -> str`.
- Keeps: `AnimationTimeline.set_state` and `AnimationTimeline.frame_path`.
- Removes: drag-time calls to `AnimationTimeline.set_frozen`.

- [ ] **Step 1: Write failing state-transition tests**

```python
def test_startup_greeting_lasts_one_three_second_cycle(self):
    controller = AnimationStateController()
    self.assertEqual("greeting", controller.update({"state": "waiting"}, 0.0))
    self.assertEqual("greeting", controller.update({"state": "waiting"}, 2.99))
    self.assertEqual("idle", controller.update({"state": "waiting"}, 3.0))

def test_ocr_uses_head_scratch_immediately(self):
    controller = AnimationStateController()
    controller.update({"state": "waiting"}, 0.0)
    self.assertEqual("thinking", controller.update({"state": "ocr_reading"}, 3.1))

def test_dragging_overrides_and_release_restores_business_state(self):
    controller = AnimationStateController()
    view = {"state": "recommendation", "bubble_visible": True}
    self.assertEqual("thinking", controller.update(view, 4.0, dragging=True))
    self.assertEqual("success", controller.update(view, 4.1, dragging=False))
```

- [ ] **Step 2: Run the animation tests and verify transition failures**

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_cat_animation -v
```

Expected: FAIL because greeting lasts 1.2 seconds, OCR has a listening transition, and drag has no override.

- [ ] **Step 3: Implement the revised state controller**

Set the startup greeting deadline to 3 seconds.
Map hidden or waiting views to `idle`, champion selection to `listening`,
OCR reading/confirming/updating to `thinking`, recommendations to `success`, and error states to `failure`.
When `dragging=True`, return `thinking` without modifying the underlying business state.

- [ ] **Step 4: Replace the animation assets using the image-generation skill**

Use the five user-provided images as visual references only:

```text
/var/folders/m_/j_9q4sdd7k51fr8j2311mb5m0000gn/T/codex-clipboard-40edb934-9689-4ef4-bb6a-173301a34581.png
/var/folders/m_/j_9q4sdd7k51fr8j2311mb5m0000gn/T/codex-clipboard-e91d5202-5dde-4320-b411-3ed19419e614.png
/var/folders/m_/j_9q4sdd7k51fr8j2311mb5m0000gn/T/codex-clipboard-52a051fc-d65f-4efe-bfad-9b2188685f8f.png
/var/folders/m_/j_9q4sdd7k51fr8j2311mb5m0000gn/T/codex-clipboard-2f4abd71-436d-4d03-99b2-f7e530facd6f.png
/var/folders/m_/j_9q4sdd7k51fr8j2311mb5m0000gn/T/codex-clipboard-3c598b8c-06ce-4a3c-a2db-4399bcc13979.png
```

Create transparent 112px-compatible frames. Remove the startup name label and every Codex-pet fragment.
Keep the body still during greeting and move only the raised paw. Use the head-scratch pose for both OCR and drag.
Make every three-second cycle contain one blink; use frame repetition in the manifest for still intervals.

- [ ] **Step 5: Validate manifest timing and drag behavior**

Add tests that sum each loop's `len(frames) * frame_ms` to 2800–3200ms and verify at least one distinct blink frame.
Update the overlay tests so long-press activation requests the `thinking` override and never calls `set_frozen`.
On release and capture loss, assert the business state is resynchronized immediately.
Delete one frame in a temporary manifest and assert loading falls back to the approved `gamebuddy-cat.png` path.

- [ ] **Step 6: Run tests, inspect a contact sheet, and commit**

```bash
PYTHONPATH=scripts/product python3 -m unittest \
  tests.product.test_cat_animation tests.product.test_cat_overlay -v
git add assets/gamebuddy scripts/product/cat_animation.py scripts/product/cat_overlay.py tests/product
git commit -m "fix: replace GameBuddy animations with approved poses"
```

Open one contact sheet containing every distinct pose and blink frame. Confirm transparent backgrounds, stable body
alignment, no text, and no foreign pet before committing.

### Task 4: Regression suite and Windows acceptance documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/windows-gamebuddy-acceptance.md`

**Interfaces:**
- Documents: normal startup, adaptive bubble behavior, absolute multi-monitor drag, and revised animation mapping.

- [ ] **Step 1: Update user-facing behavior documentation**

State that the supported launch path is `一键启动小猫.cmd`. Document short-click retry, 500ms long-press drag,
content-sized bubbles, edge-aware placement, five visual references, and the static fallback behavior.

- [ ] **Step 2: Replace obsolete acceptance cases**

Remove checks for frozen drag animation, pagination, and narrow stacked layout.
Replace the relevant cases with items 10.1 through 10.7 from the approved spec.
Keep the existing cat-only right-click exit check until the voice plan adds the dynamic voice item.

- [ ] **Step 3: Run the complete automated suite and static checks**

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts
python3 -m json.tool assets/gamebuddy/animations.json >/dev/null
git diff --check
```

Check every changed Python method is at most 80 lines and every changed line is at most 120 characters.

- [ ] **Step 4: Commit the documentation and test evidence**

```bash
git add README.md docs/windows-gamebuddy-acceptance.md
git commit -m "docs: revise Windows GameBuddy acceptance checks"
```

- [ ] **Step 5: Perform Windows manual acceptance before release**

Run on the user's Windows machine with at least two monitors, including one positioned left of the primary display.
Record each acceptance item as pass or fail. Until all items pass, report the result as
“代码和自动化验证完成，Windows 实机待验收” and do not push a release candidate to either remote.
