from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from offline_speech_assets import (
    EXPECTED_WHEEL_FILES,
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
        wheel_root = root / "vendor" / "speech" / "wheels" / "cp311-win_amd64"
        wheel_root.mkdir(parents=True)
        for filename in EXPECTED_WHEEL_FILES:
            (wheel_root / filename).write_text(filename, encoding="utf-8")

    def write_manifest(self, root: Path) -> Path:
        artifact_roots = (
            root / "assets" / "speech" / "melo-tts-zh_en-int8",
            root / "vendor" / "speech" / "wheels" / "cp311-win_amd64",
        )
        files = sorted(path for base in artifact_roots for path in base.rglob("*") if path.is_file())
        lines = [
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}"
            for path in files
        ]
        manifest = root / "SHA256SUMS"
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return manifest

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
            self.make_bundle(root)
            manifest = self.write_manifest(root)
            self.assertEqual([], verify_manifest(root, manifest))

    def test_manifest_rejects_missing_changed_and_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_bundle(root)
            manifest = self.write_manifest(root)
            model_root = root / "assets" / "speech" / "melo-tts-zh_en-int8"
            (model_root / "model.int8.onnx").unlink()
            (model_root / "tokens.txt").write_text("changed", encoding="utf-8")
            digest = hashlib.sha256(b"escape").hexdigest()
            with manifest.open("a", encoding="utf-8") as stream:
                stream.write(f"{digest}  ../escape\n")
            errors = verify_manifest(root, manifest)
            self.assertTrue(any("model.int8.onnx" in error for error in errors))
            self.assertTrue(any("tokens.txt" in error for error in errors))
            self.assertTrue(any("../escape" in error for error in errors))

    def test_manifest_rejects_empty_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_bundle(root)
            manifest = root / "SHA256SUMS"
            manifest.write_text("", encoding="utf-8")
            errors = verify_manifest(root, manifest)
            self.assertTrue(any("missing manifest entry" in error for error in errors))

    def test_manifest_rejects_unlisted_extra_model_and_wheel_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_bundle(root)
            manifest = self.write_manifest(root)
            model_root = root / "assets" / "speech" / "melo-tts-zh_en-int8"
            wheel_root = root / "vendor" / "speech" / "wheels" / "cp311-win_amd64"
            (model_root / "unexpected.txt").write_text("extra", encoding="utf-8")
            (wheel_root / "numpy-extra.whl").write_text("extra", encoding="utf-8")
            errors = verify_manifest(root, manifest)
            self.assertTrue(any("unexpected.txt" in error for error in errors))
            self.assertTrue(any("numpy-extra.whl" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
