"""Replay gold/prism clips: two cards vanish, one remains. No brightness pick."""

from __future__ import annotations

import unittest

PHASE2 = [
    (0.19765625, 0.17777777777777778, 0.1859375, 0.49027777777777776),
    (0.4109375, 0.17777777777777778, 0.1859375, 0.49027777777777776),
    (0.62421875, 0.17777777777777778, 0.1859375, 0.49027777777777776),
]

# Background-likeness from d:\\Arahat0\\Documents\\棱彩.mp4.
PRISM_BG = [
    (0.12, 0.07, 0.18),
    (0.11, 0.07, 0.17),
    (0.11, 0.07, 0.17),
    (0.22, 0.04, -0.01),
    (0.14, 0.21, 0.07),
    (0.14, 0.47, 0.33),
    (0.09, 0.46, 0.44),  # left remains, center/right look like the alley
    (0.86, 0.64, 0.58),
]

# Background-likeness from d:\\Arahat0\\Documents\\黄金.mp4.
GOLD_BG = [
    (0.07, 0.10, 0.16),
    (0.07, 0.10, 0.17),
    (0.07, 0.10, 0.17),
    (0.13, 0.01, 0.15),
    (0.34, 0.02, 0.18),
    (0.43, -0.44, 0.47),  # center remains, left/right look like the alley
    (0.58, 0.48, 0.66),
]


def card_present(value: float) -> bool:
    return value < 0.28


def card_gone(value: float) -> bool:
    return value >= 0.40


def detect_sole(likeness: tuple[float, float, float]) -> int | None:
    present = [index for index, value in enumerate(likeness) if card_present(value)]
    gone = [index for index, value in enumerate(likeness) if card_gone(value)]
    if len(present) == 1 and len(gone) == 2:
        return present[0]
    return None


def replay(frames: list[tuple[float, float, float]]) -> int | None:
    armed = False
    picked = None
    for likeness in frames:
        remaining = detect_sole(likeness) if armed else None
        if sum(1 for value in likeness if card_present(value)) == 3:
            armed = True
        if remaining is not None and picked is None:
            picked = remaining
    return picked


def _boxes(width: int, height: int):
    return [
        (int(x * width), int(y * height), int(bw * width), int(bh * height))
        for x, y, bw, bh in PHASE2
    ]


def _live_likeness(bgr, rois):
    import numpy as np

    def patch(x, y, w, h):
        roi = bgr[y : y + h, x : x + w]
        return __import__("cv2").resize(roi, (40, 56), interpolation=1)

    def corr(a, b):
        aa = a.astype(np.float32).reshape(-1)
        bb = b.astype(np.float32).reshape(-1)
        aa -= aa.mean()
        bb -= bb.mean()
        denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
        return float(np.dot(aa, bb) / denom) if denom > 1e-6 else 0.0

    left, center, right = rois
    gap1 = (
        left[0] + left[2] + 4,
        left[1] + left[3] // 6,
        max(12, center[0] - (left[0] + left[2]) - 8),
        left[3] * 2 // 3,
    )
    gap2 = (
        center[0] + center[2] + 4,
        center[1] + center[3] // 6,
        max(12, right[0] - (center[0] + center[2]) - 8),
        center[3] * 2 // 3,
    )
    bg = (patch(*gap1).astype(np.float32) + patch(*gap2).astype(np.float32)) / 2.0
    return tuple(corr(patch(*roi), bg) for roi in rois)


class CardPickReplayTests(unittest.TestCase):
    def test_phase2_layout_matches_engine_seed(self) -> None:
        boxes = _boxes(1920, 1080)
        self.assertEqual(boxes[0], (379, 192, 357, 529))
        self.assertEqual(boxes[1], (789, 192, 357, 529))
        self.assertEqual(boxes[2], (1198, 192, 357, 529))

    def test_prism_clip_picks_left(self) -> None:
        self.assertEqual(replay(PRISM_BG), 0)

    def test_gold_clip_picks_center(self) -> None:
        self.assertEqual(replay(GOLD_BG), 1)

    def test_hover_is_not_a_pick(self) -> None:
        self.assertIsNone(detect_sole((0.18, 0.05, 0.17)))

    def test_live_videos_still_match_if_present(self) -> None:
        try:
            import cv2  # type: ignore
        except ImportError:
            self.skipTest("opencv is not installed")

        videos = [
            (r"d:\Arahat0\Documents\棱彩.mp4", 0),
            (r"d:\Arahat0\Documents\黄金.mp4", 1),
        ]
        for src, expected in videos:
            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                cap.release()
                self.skipTest(f"missing {src}")
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            rois = _boxes(width, height)
            frames = []
            while True:
                ok, bgr = cap.read()
                if not ok:
                    break
                frames.append(_live_likeness(bgr, rois))
            cap.release()
            self.assertEqual(replay(frames), expected, src)


if __name__ == "__main__":
    unittest.main()
