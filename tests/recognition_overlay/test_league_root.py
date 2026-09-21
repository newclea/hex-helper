from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from league_root import _read_config_root, persist_league_root


class LeagueRootConfigTests(unittest.TestCase):
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

    def test_read_config_root_uses_legacy_when_primary_lacks_key(self) -> None:
        self.write_json(self.primary, {"voice_enabled": False})
        self.write_json(self.legacy, {"league_root": "E:\\League"})

        with patch("league_root.overlay_config_path", return_value=self.primary), patch(
            "league_root.legacy_overlay_config_path",
            return_value=self.legacy,
        ):
            root = _read_config_root()

        self.assertEqual(Path("E:\\League"), root)

    def test_read_config_root_uses_legacy_when_primary_value_is_invalid(self) -> None:
        self.write_json(self.primary, {"league_root": False})
        self.write_json(self.legacy, {"league_root": "E:\\League"})

        with patch("league_root.overlay_config_path", return_value=self.primary), patch(
            "league_root.legacy_overlay_config_path",
            return_value=self.legacy,
        ):
            root = _read_config_root()

        self.assertEqual(Path("E:\\League"), root)

    def test_persist_league_root_preserves_voice_fields(self) -> None:
        self.write_json(self.primary, {
            "voice_enabled": False,
            "voice_name": "Xiaoxiao",
            "unrelated": {"keep": True},
        })

        with patch("league_root.overlay_config_path", return_value=self.primary):
            persist_league_root(Path("E:\\League"))

        saved = json.loads(self.primary.read_text(encoding="utf-8"))
        self.assertFalse(saved["voice_enabled"])
        self.assertEqual("Xiaoxiao", saved["voice_name"])
        self.assertEqual({"keep": True}, saved["unrelated"])
        self.assertEqual("E:\\League", saved["league_root"])


if __name__ == "__main__":
    unittest.main()
