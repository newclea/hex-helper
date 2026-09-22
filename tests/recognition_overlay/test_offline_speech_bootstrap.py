from __future__ import annotations

import unittest
from pathlib import Path


class OfflineSpeechBootstrapTests(unittest.TestCase):
    EXPECTED_WHEELS = {
        "numpy-1.26.4-cp311-cp311-win_amd64.whl",
        "cffi-2.1.1-cp311-cp311-win_amd64.whl",
        "pycparser-3.0-py3-none-any.whl",
        "sherpa_onnx-1.13.8-cp311-cp311-win_amd64.whl",
        "sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl",
        "sounddevice-0.5.3-py3-none-win_amd64.whl",
    }
    EXPECTED_INSTALLS = (
        "numpy==1.26.4",
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
        self.assertIn("1.13.8-py311-numpy1.26.4", script)

    def test_wheelhouse_contains_pinned_streaming_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[2]
        wheel_dir = root / "vendor" / "speech" / "wheels" / "cp311-win_amd64"
        actual = {path.name for path in wheel_dir.glob("*.whl")}
        self.assertEqual(self.EXPECTED_WHEELS, actual)

    def test_speech_debug_launcher_uses_scoped_mode(self) -> None:
        root = Path(__file__).resolve().parents[2]
        launcher = (root / "一键启动小猫-Debug-语音.cmd").read_text(encoding="utf-8")

        self.assertIn("run_recognition_overlay.ps1", launcher)
        self.assertIn("-DebugSubmode speech", launcher)

    def test_hex_refresh_debug_launcher_uses_scoped_mode(self) -> None:
        root = Path(__file__).resolve().parents[2]
        launcher = (root / "一键启动小猫-Debug-海克斯刷新.cmd").read_text(
            encoding="utf-8"
        )

        self.assertIn("run_recognition_overlay.ps1", launcher)
        self.assertIn("-DebugSubmode hex-refresh", launcher)


if __name__ == "__main__":
    unittest.main()
