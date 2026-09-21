from __future__ import annotations

import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
import zlib

from cat_animation import (
    ANIMATION_STATES,
    AnimationClip,
    AnimationManifest,
    AnimationStateController,
    AnimationTimeline,
    load_animation_manifest,
    select_base_state,
)


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]


def _rgba_pixels(path: Path) -> tuple[int, int, bytes]:
    payload = path.read_bytes()
    offset = 8
    compressed = bytearray()
    width = height = 0
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        value = payload[offset + 8 : offset + 8 + length]
        offset += length + 12
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", value)
            if (depth, color, interlace) != (8, 6, 0):
                raise AssertionError(f"unsupported PNG format: {path}")
        elif kind == b"IDAT":
            compressed.extend(value)
    raw = zlib.decompress(bytes(compressed))
    stride = width * 4
    previous = bytearray(stride)
    decoded = bytearray()
    for row_index in range(height):
        start = row_index * (stride + 1)
        filter_type = raw[start]
        row = bytearray(raw[start + 1 : start + 1 + stride])
        for index in range(stride):
            left = row[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            predictors = (0, left, above, (left + above) // 2, _paeth(left, above, upper_left))
            row[index] = (row[index] + predictors[filter_type]) & 0xFF
        decoded.extend(row)
        previous = row
    return width, height, bytes(decoded)


def _changed_pixels(first: Path, second: Path) -> set[tuple[int, int]]:
    width, height, first_pixels = _rgba_pixels(first)
    other_width, other_height, second_pixels = _rgba_pixels(second)
    if (width, height) != (other_width, other_height):
        raise AssertionError("animation frame sizes differ")
    return {
        (index % width, index // width)
        for index in range(width * height)
        if first_pixels[index * 4 : index * 4 + 4] != second_pixels[index * 4 : index * 4 + 4]
    }


class AnimationStateTests(unittest.TestCase):
    def test_startup_greeting_lasts_one_three_second_cycle(self) -> None:
        controller = AnimationStateController()
        view = {"state": "waiting", "bubble_visible": False}
        self.assertEqual("greeting", controller.update(view, 0.0))
        self.assertEqual("greeting", controller.update(view, 2.99))
        self.assertEqual("idle", controller.update(view, 3.0))
        self.assertEqual("idle", controller.update(view, 10.0))

    def test_ocr_uses_head_scratch_immediately(self) -> None:
        controller = AnimationStateController()
        controller.update({"state": "waiting"}, 0.0)
        view = {"state": "ocr_reading", "bubble_visible": True}
        self.assertEqual("thinking", controller.update(view, 3.1))

    def test_dragging_overrides_and_release_restores_business_state(self) -> None:
        controller = AnimationStateController()
        controller.update({"state": "waiting"}, 0.0)
        view = {"state": "recommendation", "bubble_visible": True}
        self.assertEqual("thinking", controller.update(view, 4.0, dragging=True))
        self.assertEqual("success", controller.update(view, 4.1, dragging=False))

    def test_failure_preempts_transition(self) -> None:
        controller = AnimationStateController()
        controller.update({"state": "recommendation", "bubble_visible": True}, 0.0)
        view = {"state": "ocr_error", "bubble_visible": True}
        self.assertEqual("failure", controller.update(view, 0.1))

    def test_base_mapping(self) -> None:
        cases = (
            ({"state": "waiting", "bubble_visible": False}, "idle"),
            ({"state": "champ_select", "bubble_visible": True}, "listening"),
            ({"state": "ocr_confirming", "bubble_visible": True}, "thinking"),
            ({"state": "recommendation", "bubble_visible": True}, "success"),
            ({"state": "recommendation_unavailable", "bubble_visible": True}, "failure"),
        )
        for view, expected in cases:
            with self.subTest(view=view):
                self.assertEqual(expected, select_base_state(view))


class AnimationTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        clips = {
            state: AnimationClip(
                frames=(Path(state) / "000.png", Path(state) / "001.png"),
                frame_ms=100,
                loop=True,
            )
            for state in ANIMATION_STATES
        }
        self.timeline = AnimationTimeline(AnimationManifest(clips))

    def test_state_switch_invalidates_old_frame(self) -> None:
        self.timeline.set_state("thinking", 1.0)
        self.timeline.set_state("failure", 1.05)
        self.assertEqual(Path("failure/000.png"), self.timeline.frame_path(1.06))

    def test_freeze_preserves_frame_until_resume(self) -> None:
        self.timeline.set_state("success", 0.0)
        self.timeline.set_frozen(True, 0.15)
        frozen = self.timeline.frame_path(2.0)
        self.timeline.set_frozen(False, 2.0)
        self.assertEqual(frozen, self.timeline.frame_path(2.01))


class ManifestTests(unittest.TestCase):
    def _write_manifest(self, root: Path, *, missing_frame: bool = False) -> None:
        (root.parent / "gamebuddy-cat.png").write_bytes(b"fallback")
        clips = {}
        for state in ANIMATION_STATES:
            state_dir = root / state
            state_dir.mkdir(parents=True, exist_ok=True)
            if not missing_frame or state != "failure":
                (state_dir / "000.png").write_bytes(b"frame")
            clips[state] = {
                "frame_ms": 100,
                "loop": True,
                "frames": [f"{state}/000.png"],
            }
        payload = {
            "schema_version": 1,
            "fallback": "../gamebuddy-cat.png",
            "clips": clips,
        }
        (root / "animations.json").write_text(json.dumps(payload), "utf-8")

    def test_loads_complete_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "gamebuddy"
            root.mkdir()
            self._write_manifest(root)
            self.assertIsNotNone(load_animation_manifest(root))

    def test_missing_frame_leaves_approved_static_fallback_available(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "gamebuddy"
            root.mkdir()
            self._write_manifest(root, missing_frame=True)
            self.assertIsNone(load_animation_manifest(root))
            self.assertEqual(b"fallback", (root.parent / "gamebuddy-cat.png").read_bytes())

    def test_rejects_malformed_json(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "gamebuddy"
            root.mkdir()
            (root / "animations.json").write_text("{", "utf-8")
            self.assertIsNone(load_animation_manifest(root))

    def test_product_loops_are_three_seconds_and_contain_a_blink(self) -> None:
        root = Path(__file__).resolve().parents[2] / "assets" / "gamebuddy"
        manifest = load_animation_manifest(root)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        for state, clip in manifest.clips.items():
            with self.subTest(state=state):
                duration_ms = len(clip.frames) * clip.frame_ms
                self.assertGreaterEqual(duration_ms, 2800)
                self.assertLessEqual(duration_ms, 3200)
                self.assertGreater(len({path.read_bytes() for path in clip.frames}), 1)

    def test_waiting_and_listening_share_the_tilted_pose_frames(self) -> None:
        root = Path(__file__).resolve().parents[2] / "assets" / "gamebuddy"
        manifest = load_animation_manifest(root)
        assert manifest is not None
        idle = [path.read_bytes() for path in manifest.clips["idle"].frames]
        listening = [path.read_bytes() for path in manifest.clips["listening"].frames]
        self.assertEqual(idle, listening)

    def test_motion_frames_lock_pixels_outside_the_moving_limbs(self) -> None:
        root = Path(__file__).resolve().parents[2] / "assets" / "gamebuddy"
        cases = {
            "greeting": lambda x, y: 43 <= x <= 83 and 79 <= y <= 139,
            "thinking": lambda x, y: (
                38 <= x <= 90 and 70 <= y <= 173
            ) or (
                64 <= x <= 106 and 53 <= y <= 113
            ),
        }
        for state, allowed in cases.items():
            with self.subTest(state=state):
                changed = _changed_pixels(root / state / "000.png", root / state / "001.png")
                self.assertTrue(changed)
                self.assertFalse({point for point in changed if not allowed(*point)})


if __name__ == "__main__":
    unittest.main()
