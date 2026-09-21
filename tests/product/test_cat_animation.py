from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cat_animation import (
    ANIMATION_STATES,
    AnimationClip,
    AnimationManifest,
    AnimationStateController,
    AnimationTimeline,
    load_animation_manifest,
    select_base_state,
)


class AnimationStateTests(unittest.TestCase):
    def test_visible_edge_greets_then_uses_business_state(self) -> None:
        controller = AnimationStateController()
        controller.update({"state": "in_game", "bubble_visible": False}, 0.0)
        view = {"state": "recommendation", "bubble_visible": True}
        self.assertEqual("greeting", controller.update(view, 0.1))
        self.assertEqual("success", controller.update(view, 1.31))

    def test_new_ocr_cycle_listens_then_thinks(self) -> None:
        controller = AnimationStateController()
        view = {"state": "ocr_reading", "bubble_visible": True}
        self.assertEqual("listening", controller.update(view, 2.0))
        self.assertEqual("thinking", controller.update(view, 2.61))

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

    def test_rejects_missing_frame(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "gamebuddy"
            root.mkdir()
            self._write_manifest(root, missing_frame=True)
            self.assertIsNone(load_animation_manifest(root))

    def test_rejects_malformed_json(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "gamebuddy"
            root.mkdir()
            (root / "animations.json").write_text("{", "utf-8")
            self.assertIsNone(load_animation_manifest(root))


if __name__ == "__main__":
    unittest.main()
