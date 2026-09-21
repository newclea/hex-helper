from __future__ import annotations

import unittest
from pathlib import Path


class OfflineSpeechBootstrapTests(unittest.TestCase):
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
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, script)
        forbidden = ("https://", "http://", "Invoke-WebRequest", "Start-BitsTransfer")
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, script)


if __name__ == "__main__":
    unittest.main()
