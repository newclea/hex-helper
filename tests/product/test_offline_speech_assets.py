from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from offline_speech_assets import (
    REQUIRED_MODEL_FILES,
    resolve_offline_speech_paths,
    verify_manifest,
)


class OfflineSpeechAssetTests(unittest.TestCase):
    def make_bundle(self, root: Path) -> None:
        model_root = root / "assets" / "speech" / "melo-tts-zh_en-int8"
        for relative in REQUIRED_MODEL_FILES:
            path = model_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative, encoding="utf-8")
        (root / "vendor" / "speech" / "wheels" / "cp311-win_amd64").mkdir(
            parents=True
        )

    def test_resolve_requires_model_tokens_lexicon_fsts_and_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_bundle(root)
            paths = resolve_offline_speech_paths(root)
            self.assertEqual("model.int8.onnx", paths.model.name)
            self.assertEqual("tokens.txt", paths.tokens.name)
            self.assertEqual("lexicon.txt", paths.lexicon.name)
            self.assertEqual("dict", paths.data_dir.name)
            self.assertEqual(4, len(paths.rule_fsts))
            self.assertEqual("cp311-win_amd64", paths.wheel_dir.name)

    def test_missing_required_model_file_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_bundle(root)
            missing = root / "assets" / "speech" / "melo-tts-zh_en-int8" / "model.int8.onnx"
            missing.unlink()
            with self.assertRaisesRegex(ValueError, "model.int8.onnx"):
                resolve_offline_speech_paths(root)

    def test_manifest_accepts_matching_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / "payload.txt"
            payload.write_bytes(b"hello")
            digest = hashlib.sha256(b"hello").hexdigest()
            manifest = root / "SHA256SUMS"
            manifest.write_text(f"{digest}  payload.txt\n", encoding="utf-8")
            self.assertEqual([], verify_manifest(root, manifest))

    def test_manifest_rejects_missing_changed_and_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "changed.txt").write_bytes(b"changed")
            digest = hashlib.sha256(b"expected").hexdigest()
            manifest = root / "SHA256SUMS"
            manifest.write_text(
                f"{digest}  missing.txt\n"
                f"{digest}  changed.txt\n"
                f"{digest}  ../escape\n",
                encoding="utf-8",
            )
            errors = verify_manifest(root, manifest)
            self.assertTrue(any("missing.txt" in error for error in errors))
            self.assertTrue(any("changed.txt" in error for error in errors))
            self.assertTrue(any("../escape" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
