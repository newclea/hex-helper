from __future__ import annotations

import unittest
from pathlib import Path


class OfflineSpeechBootstrapTests(unittest.TestCase):
    EXPECTED_WHEELS = {
        "cffi-2.1.1-cp311-cp311-win_amd64.whl",
        "pycparser-3.0-py3-none-any.whl",
        "sherpa_onnx-1.13.8-cp311-cp311-win_amd64.whl",
        "sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl",
        "sounddevice-0.5.3-py3-none-win_amd64.whl",
    }
    EXPECTED_INSTALLS = (
        "sherpa-onnx==1.13.8",
        "sherpa-onnx-core==1.13.8",
        "sounddevice==0.5.3",
        "cffi==2.1.1",
        "pycparser==3.0",
    )

    def test_launcher_uses_only_the_bundled_runtime(self) -> None:
        root = Path(__file__).resolve().parents[2]
        script = (root / "scripts" / "run_recognition_overlay.ps1").read_text(
            encoding="utf-8"
        )
        required = (
            "--no-index",
            "--disable-pip-version-check",
            "vendor\\speech\\wheels\\cp311-win_amd64",
            "offline_speech_assets.py",
            "PYTHONPATH",
            "--no-deps",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, script)
        forbidden = ("https://", "http://", "Invoke-WebRequest", "Start-BitsTransfer")
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, script)
        for requirement in self.EXPECTED_INSTALLS:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, script)
        self.assertNotIn("numpy", script.lower())

    def test_wheelhouse_contains_exactly_five_pinned_wheels_without_numpy(self) -> None:
        root = Path(__file__).resolve().parents[2]
        wheel_dir = root / "vendor" / "speech" / "wheels" / "cp311-win_amd64"
        actual = {path.name for path in wheel_dir.glob("*.whl")}
        self.assertEqual(self.EXPECTED_WHEELS, actual)
        self.assertFalse(any("numpy" in name.lower() for name in actual))


if __name__ == "__main__":
    unittest.main()
