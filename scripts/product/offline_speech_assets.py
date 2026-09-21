from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path


MODEL_DIRECTORY = Path("assets/speech/melo-tts-zh_en-int8")
WHEEL_DIRECTORY = Path("vendor/speech/wheels/cp311-win_amd64")
REQUIRED_MODEL_FILES = (
    "model.int8.onnx",
    "tokens.txt",
    "lexicon.txt",
    "date.fst",
    "new_heteronym.fst",
    "number.fst",
    "phone.fst",
    "dict/README.md",
    "dict/hmm_model.utf8",
    "dict/idf.utf8",
    "dict/jieba.dict.utf8",
    "dict/pos_dict/char_state_tab.utf8",
    "dict/pos_dict/prob_emit.utf8",
    "dict/pos_dict/prob_start.utf8",
    "dict/pos_dict/prob_trans.utf8",
    "dict/stop_words.utf8",
    "dict/user.dict.utf8",
)


@dataclass(frozen=True)
class OfflineSpeechPaths:
    model: Path
    tokens: Path
    lexicon: Path
    data_dir: Path
    rule_fsts: tuple[Path, ...]
    wheel_dir: Path


def _require_file(path: Path) -> Path:
    if not path.is_file():
        raise ValueError(f"required offline speech file is missing: {path}")
    return path


def resolve_offline_speech_paths(bundle_root: Path) -> OfflineSpeechPaths:
    root = bundle_root.resolve()
    model_root = root / MODEL_DIRECTORY
    for relative in REQUIRED_MODEL_FILES:
        _require_file(model_root / relative)
    wheel_dir = root / WHEEL_DIRECTORY
    if not wheel_dir.is_dir():
        raise ValueError(f"required offline speech directory is missing: {wheel_dir}")
    return OfflineSpeechPaths(
        model=model_root / "model.int8.onnx",
        tokens=model_root / "tokens.txt",
        lexicon=model_root / "lexicon.txt",
        data_dir=model_root / "dict",
        rule_fsts=tuple(
            model_root / name
            for name in ("date.fst", "new_heteronym.fst", "number.fst", "phone.fst")
        ),
        wheel_dir=wheel_dir,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_target(bundle_root: Path, relative_text: str) -> Path | None:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = bundle_root.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def verify_manifest(bundle_root: Path, manifest_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return [f"cannot read manifest {manifest_path}: {error}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            errors.append(f"invalid manifest line {line_number}: {line}")
            continue
        expected, relative_text = parts[0].lower(), parts[1].strip()
        target = _manifest_target(bundle_root, relative_text)
        if target is None:
            errors.append(f"unsafe manifest path: {relative_text}")
        elif not target.is_file():
            errors.append(f"manifest file is missing: {relative_text}")
        elif _sha256(target) != expected:
            errors.append(f"manifest checksum mismatch: {relative_text}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify bundled offline speech assets")
    parser.add_argument("--verify", action="store_true", required=True)
    parser.add_argument("--bundle-root", type=Path, default=Path(__file__).resolve().parents[2])
    arguments = parser.parse_args(argv)
    root = arguments.bundle_root.resolve()
    manifest = root / "assets" / "speech" / "SHA256SUMS"
    try:
        resolve_offline_speech_paths(root)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    errors = verify_manifest(root, manifest)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
