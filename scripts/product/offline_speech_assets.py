from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path


MODEL_DIRECTORY = Path("assets/speech/melo-tts-zh_en-int8")
WHEEL_DIRECTORY = Path("vendor/speech/wheels/cp311-win_amd64")
EXPECTED_WHEEL_FILES = (
    "cffi-2.1.1-cp311-cp311-win_amd64.whl",
    "pycparser-3.0-py3-none-any.whl",
    "sherpa_onnx-1.13.8-cp311-cp311-win_amd64.whl",
    "sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl",
    "sounddevice-0.5.3-py3-none-win_amd64.whl",
)
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
TEXT_MODEL_FILES = frozenset(
    relative
    for relative in REQUIRED_MODEL_FILES
    if relative not in {
        "model.int8.onnx",
        "date.fst",
        "new_heteronym.fst",
        "number.fst",
        "phone.fst",
    }
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
    for filename in EXPECTED_WHEEL_FILES:
        _require_file(wheel_dir / filename)
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


def _sha256(path: Path, normalize_newlines: bool = False) -> str:
    digest = hashlib.sha256()
    if normalize_newlines:
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        return digest.hexdigest()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_model_text_file(relative_text: str) -> bool:
    path = Path(relative_text)
    try:
        model_relative = path.relative_to(MODEL_DIRECTORY).as_posix()
    except ValueError:
        return False
    return model_relative in TEXT_MODEL_FILES


def _manifest_target(bundle_root: Path, relative_text: str) -> Path | None:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = bundle_root.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _expected_artifacts() -> set[str]:
    model_files = {
        (MODEL_DIRECTORY / relative).as_posix()
        for relative in REQUIRED_MODEL_FILES
    }
    wheel_files = {
        (WHEEL_DIRECTORY / filename).as_posix()
        for filename in EXPECTED_WHEEL_FILES
    }
    return model_files | wheel_files


def _actual_artifacts(bundle_root: Path) -> set[str]:
    root = bundle_root.resolve()
    files: set[str] = set()
    for relative_root in (MODEL_DIRECTORY, WHEEL_DIRECTORY):
        artifact_root = root / relative_root
        if artifact_root.is_dir():
            files.update(
                path.relative_to(root).as_posix()
                for path in artifact_root.rglob("*")
                if path.is_file()
            )
    return files


def _read_manifest(manifest_path: Path) -> tuple[dict[str, str], list[str]]:
    entries: dict[str, str] = {}
    errors: list[str] = []
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        digest = parts[0].lower() if parts else ""
        valid_digest = len(digest) == 64 and all(
            char in "0123456789abcdef" for char in digest
        )
        if len(parts) != 2 or not valid_digest:
            errors.append(f"invalid manifest line {line_number}: {line}")
            continue
        relative_text = parts[1].strip().replace("\\", "/")
        if relative_text in entries:
            errors.append(f"duplicate manifest path: {relative_text}")
        else:
            entries[relative_text] = digest
    return entries, errors


def verify_manifest(bundle_root: Path, manifest_path: Path) -> list[str]:
    try:
        entries, errors = _read_manifest(manifest_path)
    except OSError as error:
        return [f"cannot read manifest {manifest_path}: {error}"]
    expected_paths = _expected_artifacts()
    for relative_text in sorted(set(entries) - expected_paths):
        if _manifest_target(bundle_root, relative_text) is None:
            errors.append(f"unsafe manifest path: {relative_text}")
        else:
            errors.append(f"unexpected manifest path: {relative_text}")
    for relative_text in sorted(_actual_artifacts(bundle_root) - expected_paths):
        errors.append(f"unexpected bundled file: {relative_text}")
    for relative_text in sorted(expected_paths):
        expected = entries.get(relative_text)
        if expected is None:
            errors.append(f"missing manifest entry: {relative_text}")
            continue
        target = _manifest_target(bundle_root, relative_text)
        if target is None:
            errors.append(f"unsafe manifest path: {relative_text}")
        elif not target.is_file():
            errors.append(f"manifest file is missing: {relative_text}")
        else:
            actual = _sha256(target, normalize_newlines=_is_model_text_file(relative_text))
            if actual == expected:
                continue
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
