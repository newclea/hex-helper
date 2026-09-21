from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from overlay_config import (
    VoiceSettings,
    load_voice_settings,
    read_config_value,
    update_overlay_config,
)


class OverlayConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.primary = root / "current" / "overlay.json"
        self.legacy = root / "legacy" / "overlay.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_voice_defaults_to_enabled(self) -> None:
        settings = load_voice_settings(self.primary, self.legacy)
        self.assertEqual(VoiceSettings(enabled=True), settings)

    def test_voice_name_is_ignored_when_loading_runtime_settings(self) -> None:
        self.write_json(self.legacy, {
            "voice_enabled": False,
            "voice_name": "Legacy voice",
        })
        self.write_json(self.primary, {"voice_enabled": True})

        settings = load_voice_settings(self.primary, self.legacy)

        self.assertEqual(VoiceSettings(enabled=True), settings)

    def test_corrupt_primary_falls_back_to_legacy(self) -> None:
        self.write_json(self.legacy, {
            "voice_enabled": False,
            "voice_name": "Xiaoxiao",
        })
        self.primary.parent.mkdir(parents=True)
        self.primary.write_text("{not json", encoding="utf-8")

        settings = load_voice_settings(self.primary, self.legacy)

        self.assertEqual(VoiceSettings(enabled=False), settings)

    def test_invalid_voice_values_use_defaults(self) -> None:
        self.write_json(self.primary, {
            "voice_enabled": "false",
            "voice_name": {"invalid": "runtime must ignore this"},
        })

        settings = load_voice_settings(self.primary, self.legacy)

        self.assertEqual(VoiceSettings(enabled=True), settings)

    def test_read_config_value_uses_first_file_containing_key(self) -> None:
        self.write_json(self.primary, {"other": 1})
        self.write_json(self.legacy, {"league_root": "E:\\League"})

        value = read_config_value("league_root", (self.primary, self.legacy))

        self.assertEqual("E:\\League", value)

    def test_update_preserves_unrelated_fields(self) -> None:
        self.write_json(self.primary, {
            "league_root": "E:\\League",
            "voice_name": "Xiaoxiao",
        })

        updated = update_overlay_config(self.primary, {"voice_enabled": False})

        self.assertTrue(updated)
        saved = json.loads(self.primary.read_text(encoding="utf-8"))
        self.assertEqual("E:\\League", saved["league_root"])
        self.assertEqual("Xiaoxiao", saved["voice_name"])
        self.assertFalse(saved["voice_enabled"])
        self.assertFalse(self.primary.with_suffix(".json.tmp").exists())

    def test_update_creates_parent_and_config(self) -> None:
        updated = update_overlay_config(self.primary, {"voice_enabled": False})

        self.assertTrue(updated)
        saved = json.loads(self.primary.read_text(encoding="utf-8"))
        self.assertEqual({"voice_enabled": False}, saved)

    def test_failed_replace_keeps_original_and_removes_temporary(self) -> None:
        self.write_json(self.primary, {"voice_enabled": True})

        with patch("pathlib.Path.replace", side_effect=OSError("locked")), patch(
            "overlay_config.LOGGER.exception",
        ):
            updated = update_overlay_config(self.primary, {"voice_enabled": False})

        self.assertFalse(updated)
        saved = json.loads(self.primary.read_text(encoding="utf-8"))
        self.assertTrue(saved["voice_enabled"])
        self.assertFalse(self.primary.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
