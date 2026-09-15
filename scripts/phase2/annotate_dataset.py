#!/usr/bin/env python3
"""Pure CLI annotation tool for the Phase2 augment-offer dataset.

The tool never infers a label. A valid augment ID and localized name must both
be supplied by the operator. Images are opened only when --open is explicit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_ROOT = REPO_ROOT / "data" / "dataset" / "augment_offers"
DEFAULT_KNOWLEDGE = REPO_ROOT / "data" / "knowledge" / "augments.zh-CN.json"
PROVENANCES = ("real", "synthetic", "unknown")
SIDES = ("left", "center", "right")
ARTIFACT_NAMES = {
    "raw": "RAW.png",
    "left": "LEFT_CARD.png",
    "center": "CENTER_CARD.png",
    "right": "RIGHT_CARD.png",
    "metadata": "metadata.json",
    "annotation": "annotation.json",
}
LEGACY_CARD_NAMES = {
    "left": "LEFT.png",
    "center": "CENTER.png",
    "right": "RIGHT.png",
}
SAMPLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DatasetError(ValueError):
    """A user-facing dataset or annotation error."""


def reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数值: {value}")


def object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON object 包含重复字段: {key!r}")
        result[key] = value
    return result


def loads_strict_json(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=object_without_duplicate_keys,
        parse_constant=reject_json_constant,
    )


def configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (LookupError, OSError):
                pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def validate_sample_id(sample_id: str) -> None:
    if sample_id in {".", ".."} or SAMPLE_ID_PATTERN.fullmatch(sample_id) is None:
        raise DatasetError(
            "sample-id 只能包含 ASCII 字母、数字、点、下划线和连字符，长度为 1–128"
        )


def resolve_dataset_root(path: Path) -> Path:
    try:
        root = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise DatasetError(f"dataset root 不可访问: {path}: {exc}") from exc
    if not root.is_dir():
        raise DatasetError(f"dataset root 不是目录: {root}")
    return root


def require_inside(root: Path, path: Path, *, must_exist: bool = False) -> Path:
    if path.is_symlink():
        raise DatasetError(f"dataset 不允许符号链接: {path}")
    try:
        resolved = path.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DatasetError(f"路径逃逸 dataset root: {path}") from exc
    return resolved


def locate_sample(root: Path, sample_id: str) -> tuple[str, Path]:
    validate_sample_id(sample_id)
    matches: list[tuple[str, Path]] = []
    for provenance in PROVENANCES:
        candidate = root / provenance / sample_id
        require_inside(root, candidate)
        if candidate.is_dir():
            require_inside(root, candidate, must_exist=True)
            matches.append((provenance, candidate))
    if not matches:
        raise DatasetError(f"未找到样本: {sample_id}")
    if len(matches) != 1:
        raise DatasetError(f"sample-id 在多个 provenance 中重复: {sample_id}")
    return matches[0]


def discover_samples(root: Path) -> list[tuple[str, str, Path]]:
    samples: list[tuple[str, str, Path]] = []
    for provenance in PROVENANCES:
        provenance_dir = root / provenance
        require_inside(root, provenance_dir)
        if not provenance_dir.is_dir():
            continue
        for entry in sorted(provenance_dir.iterdir(), key=lambda item: item.name):
            if entry.name == ".gitkeep":
                continue
            if entry.is_symlink():
                raise DatasetError(f"dataset 不允许符号链接: {entry}")
            if not entry.is_dir():
                continue
            validate_sample_id(entry.name)
            require_inside(root, entry, must_exist=True)
            samples.append((entry.name, provenance, entry))
    return sorted(samples, key=lambda item: (item[0], item[1]))


def resolve_artifact_layout(
    root: Path, sample_dir: Path
) -> tuple[dict[str, Path], str, list[dict[str, str]]]:
    canonical_present = {
        side for side in SIDES if (sample_dir / ARTIFACT_NAMES[side]).exists()
    }
    legacy_present = {
        side for side in SIDES if (sample_dir / LEGACY_CARD_NAMES[side]).exists()
    }
    if canonical_present and legacy_present:
        raise DatasetError(
            "样本混用了 canonical *_CARD.png 与 deprecated LEFT/CENTER/RIGHT.png"
        )
    if canonical_present and canonical_present != set(SIDES):
        raise DatasetError("canonical 三卡文件不完整，必须同时提供三个 *_CARD.png")
    if legacy_present and legacy_present != set(SIDES):
        raise DatasetError("deprecated 三卡文件不完整，禁止与 canonical 命名静默混用")

    card_names = ARTIFACT_NAMES
    naming = "canonical"
    warnings: list[dict[str, str]] = []
    if legacy_present == set(SIDES):
        card_names = {**ARTIFACT_NAMES, **LEGACY_CARD_NAMES}
        naming = "legacy_deprecated"
        warnings.append(
            {
                "code": "deprecated_artifact_names",
                "message": (
                    "LEFT.png/CENTER.png/RIGHT.png 仅用于一次迁移兼容；"
                    "请迁移为 LEFT_CARD.png/CENTER_CARD.png/RIGHT_CARD.png"
                ),
            }
        )

    paths = {
        key: require_inside(root, sample_dir / filename)
        for key, filename in card_names.items()
    }
    return paths, naming, warnings


def sample_paths(root: Path, sample_dir: Path) -> dict[str, Path]:
    paths, _naming, _warnings = resolve_artifact_layout(root, sample_dir)
    return paths


def load_json_object(root: Path, path: Path) -> dict[str, Any]:
    require_inside(root, path, must_exist=True)
    try:
        value = loads_strict_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise DatasetError(f"无法读取 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"JSON 根节点必须是 object: {path}")
    return value


def empty_annotation(sample_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sample_id": sample_id,
        "status": "in_progress",
        "cards": {},
        "skip_reason": None,
        "updated_at_utc": utc_now(),
    }


def load_annotation(root: Path, sample_dir: Path, sample_id: str) -> dict[str, Any]:
    path = sample_dir / ARTIFACT_NAMES["annotation"]
    if not path.exists():
        return empty_annotation(sample_id)
    value = load_json_object(root, path)
    if value.get("sample_id") != sample_id:
        raise DatasetError(f"annotation sample_id 与目录不一致: {path}")
    cards = value.get("cards")
    if not isinstance(cards, dict):
        raise DatasetError(f"annotation.cards 必须是 object: {path}")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def atomic_write_json(path: Path, value: Any, dataset_root: Path) -> None:
    """Atomically replace a JSON file without allowing writes outside root."""

    root = resolve_dataset_root(dataset_root)
    if path.name != ARTIFACT_NAMES["annotation"]:
        raise DatasetError("标注工具只允许写 annotation.json")
    parent = require_inside(root, path.parent, must_exist=True)
    require_inside(root, path)
    rendered = canonical_json_bytes(value)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(rendered)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def save_annotation(
    root: Path, sample_dir: Path, annotation: dict[str, Any]
) -> None:
    annotation["updated_at_utc"] = utc_now()
    atomic_write_json(
        sample_dir / ARTIFACT_NAMES["annotation"], annotation, dataset_root=root
    )


def recompute_status(annotation: dict[str, Any]) -> None:
    cards = annotation["cards"]
    annotation["status"] = (
        "complete" if all(side in cards for side in SIDES) else "in_progress"
    )
    annotation["skip_reason"] = None


def set_card_annotation(
    root: Path,
    sample_dir: Path,
    sample_id: str,
    side: str,
    *,
    valid: bool,
    augment_id: str | None,
    augment_name: str | None,
) -> dict[str, Any]:
    sample_paths(root, sample_dir)
    if side not in SIDES:
        raise DatasetError(f"未知卡位: {side}")
    if valid:
        if not isinstance(augment_id, str) or not augment_id.strip():
            raise DatasetError("valid=true 时必须提供非空 augment_id")
        if not isinstance(augment_name, str) or not augment_name.strip():
            raise DatasetError("valid=true 时必须提供非空 augment_name")
        augment_id = augment_id.strip()
        augment_name = augment_name.strip()
    elif augment_id is not None or augment_name is not None:
        raise DatasetError("valid=false 时 augment_id/augment_name 必须省略")

    annotation = load_annotation(root, sample_dir, sample_id)
    annotation["schema_version"] = 1
    annotation["cards"][side] = {
        "augment_id": augment_id,
        "augment_name": augment_name,
        "valid": valid,
    }
    recompute_status(annotation)
    save_annotation(root, sample_dir, annotation)
    return annotation


def clear_annotation(
    root: Path, sample_dir: Path, sample_id: str, side: str | None
) -> dict[str, Any]:
    sample_paths(root, sample_dir)
    annotation = load_annotation(root, sample_dir, sample_id)
    if side is None:
        annotation["cards"] = {}
    else:
        annotation["cards"].pop(side, None)
    recompute_status(annotation)
    save_annotation(root, sample_dir, annotation)
    return annotation


def skip_annotation(
    root: Path, sample_dir: Path, sample_id: str, reason: str
) -> dict[str, Any]:
    sample_paths(root, sample_dir)
    annotation = load_annotation(root, sample_dir, sample_id)
    annotation["status"] = "skipped"
    annotation["skip_reason"] = reason.strip() or "operator skipped"
    save_annotation(root, sample_dir, annotation)
    return annotation


def load_catalog(path: Path) -> dict[str, str]:
    try:
        value = loads_strict_json(
            path.expanduser().resolve(strict=True).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise DatasetError(f"无法读取知识库: {path}: {exc}") from exc
    rows = value.get("augments") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise DatasetError("知识库 augments 必须是 array")
    catalog: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DatasetError(f"知识库 augments[{index}] 必须是 object")
        augment_id = row.get("id")
        display_name = row.get("display_name")
        if not isinstance(augment_id, str) or not augment_id:
            raise DatasetError(f"知识库 augments[{index}].id 非法")
        if not isinstance(display_name, str) or not display_name:
            raise DatasetError(f"知识库 augments[{index}].display_name 非法")
        if augment_id in catalog:
            raise DatasetError(f"知识库存在重复 augment id: {augment_id}")
        catalog[augment_id] = display_name
    return catalog


def verify_operator_label(
    catalog: dict[str, str], augment_id: str | None, augment_name: str | None
) -> None:
    if augment_id not in catalog:
        raise DatasetError(f"augment_id 不在知识库中: {augment_id}")
    expected = catalog[augment_id]
    if augment_name != expected:
        raise DatasetError(
            f"augment_name 与知识库不一致: 输入={augment_name!r}, 期望={expected!r}"
        )


def annotation_status(root: Path, sample_dir: Path, sample_id: str) -> str:
    path = sample_dir / ARTIFACT_NAMES["annotation"]
    if not path.exists():
        return "unannotated"
    try:
        return str(load_annotation(root, sample_dir, sample_id).get("status", "malformed"))
    except DatasetError:
        return "malformed"


def path_payload(
    root: Path, sample_id: str, provenance: str, sample_dir: Path
) -> dict[str, Any]:
    paths, artifact_naming, warnings = resolve_artifact_layout(root, sample_dir)
    return {
        "sample_id": sample_id,
        "provenance": provenance,
        "status": annotation_status(root, sample_dir, sample_id),
        "artifact_naming": artifact_naming,
        "warnings": warnings,
        "paths": {key: str(path) for key, path in paths.items()},
    }


def open_with_default_viewer(path: Path) -> None:
    if not path.is_file():
        raise DatasetError(f"图片不存在: {path}")
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def print_payload(value: Any) -> None:
    print(
        json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        )
    )


def print_paths(payload: dict[str, Any]) -> None:
    print(f"sample_id: {payload['sample_id']}")
    print(f"provenance: {payload['provenance']}")
    print(f"status: {payload['status']}")
    print(f"artifact_naming: {payload['artifact_naming']}")
    for warning in payload["warnings"]:
        print(f"warning[{warning['code']}]: {warning['message']}")
    for key, path in payload["paths"].items():
        print(f"{key}: {path}")


def command_list(args: argparse.Namespace, root: Path) -> int:
    rows = []
    for sample_id, provenance, sample_dir in discover_samples(root):
        payload = path_payload(root, sample_id, provenance, sample_dir)
        if args.all or payload["status"] != "complete":
            rows.append(payload)
    if args.json:
        print_payload({"count": len(rows), "samples": rows})
    elif not rows:
        print("没有未完整标注的样本。")
    else:
        for payload in rows:
            print(
                f"{payload['sample_id']}\t{payload['provenance']}\t{payload['status']}"
            )
    return 0


def command_show(args: argparse.Namespace, root: Path) -> int:
    provenance, sample_dir = locate_sample(root, args.sample_id)
    payload = path_payload(root, args.sample_id, provenance, sample_dir)
    if args.json:
        print_payload(payload)
    else:
        print_paths(payload)
    if args.open:
        for key in ("raw", *SIDES):
            open_with_default_viewer(Path(payload["paths"][key]))
    return 0


def command_set(args: argparse.Namespace, root: Path) -> int:
    _provenance, sample_dir = locate_sample(root, args.sample_id)
    if args.valid:
        catalog = load_catalog(args.knowledge)
        verify_operator_label(catalog, args.augment_id, args.augment_name)
    annotation = set_card_annotation(
        root,
        sample_dir,
        args.sample_id,
        args.side,
        valid=args.valid,
        augment_id=args.augment_id,
        augment_name=args.augment_name,
    )
    print_payload(annotation)
    return 0


def command_clear(args: argparse.Namespace, root: Path) -> int:
    _provenance, sample_dir = locate_sample(root, args.sample_id)
    print_payload(clear_annotation(root, sample_dir, args.sample_id, args.side))
    return 0


def command_skip(args: argparse.Namespace, root: Path) -> int:
    _provenance, sample_dir = locate_sample(root, args.sample_id)
    print_payload(skip_annotation(root, sample_dir, args.sample_id, args.reason))
    return 0


def first_pending_sample(root: Path) -> tuple[str, str, Path]:
    for sample_id, provenance, sample_dir in discover_samples(root):
        if annotation_status(root, sample_dir, sample_id) != "complete":
            return sample_id, provenance, sample_dir
    raise DatasetError("没有未完整标注的样本")


def command_interactive(args: argparse.Namespace, root: Path) -> int:
    if args.sample_id is None:
        sample_id, provenance, sample_dir = first_pending_sample(root)
    else:
        provenance, sample_dir = locate_sample(root, args.sample_id)
        sample_id = args.sample_id
    payload = path_payload(root, sample_id, provenance, sample_dir)
    print_paths(payload)
    if args.open:
        for key in ("raw", *SIDES):
            open_with_default_viewer(Path(payload["paths"][key]))

    catalog = load_catalog(args.knowledge)
    for side in SIDES:
        while True:
            print(f"\n[{side}] {payload['paths'][side]}")
            action = input("有效卡? [y]是/[n]否/[s]跳过卡/[c]清除卡/[q]跳过样本: ").strip().lower()
            if action == "s":
                break
            if action == "c":
                clear_annotation(root, sample_dir, sample_id, side)
                print(f"已清除 {side}")
                break
            if action == "q":
                reason = input("跳过原因: ").strip()
                print_payload(skip_annotation(root, sample_dir, sample_id, reason))
                return 0
            if action == "n":
                set_card_annotation(
                    root,
                    sample_dir,
                    sample_id,
                    side,
                    valid=False,
                    augment_id=None,
                    augment_name=None,
                )
                print(f"已标记 {side} 为 invalid")
                break
            if action == "y":
                augment_id = input("augment_id: ").strip()
                augment_name = input("augment_name: ").strip()
                try:
                    verify_operator_label(catalog, augment_id, augment_name)
                    set_card_annotation(
                        root,
                        sample_dir,
                        sample_id,
                        side,
                        valid=True,
                        augment_id=augment_id,
                        augment_name=augment_name,
                    )
                except DatasetError as exc:
                    print(f"输入无效: {exc}", file=sys.stderr)
                    continue
                print(f"已保存 {side}")
                break
            print("请输入 y/n/s/c/q。", file=sys.stderr)

    print_payload(load_annotation(root, sample_dir, sample_id))
    return 0


def parse_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise argparse.ArgumentTypeError("必须是 true 或 false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT, help="数据集根目录"
    )
    parser.add_argument(
        "--knowledge", type=Path, default=DEFAULT_KNOWLEDGE, help="强化符文知识库"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出未完整标注样本")
    list_parser.add_argument("--all", action="store_true", help="包括已完整标注样本")
    list_parser.add_argument("--json", action="store_true", help="输出 JSON")

    show_parser = subparsers.add_parser("show", help="显示样本关键路径")
    show_parser.add_argument("sample_id")
    show_parser.add_argument("--json", action="store_true", help="输出 JSON")
    show_parser.add_argument(
        "--open", action="store_true", help="用系统默认查看器打开四张图片"
    )

    set_parser = subparsers.add_parser("set", help="非交互设置单卡人工标签")
    set_parser.add_argument("sample_id")
    set_parser.add_argument("side", choices=SIDES)
    set_parser.add_argument("--valid", required=True, type=parse_bool)
    set_parser.add_argument("--augment-id")
    set_parser.add_argument("--augment-name")

    clear_parser = subparsers.add_parser("clear", help="清除单卡或整个样本标签")
    clear_parser.add_argument("sample_id")
    clear_parser.add_argument("--side", choices=SIDES)

    skip_parser = subparsers.add_parser("skip", help="将样本标为 skipped")
    skip_parser.add_argument("sample_id")
    skip_parser.add_argument("--reason", default="operator skipped")

    interactive_parser = subparsers.add_parser("interactive", help="交互逐卡标注")
    interactive_parser.add_argument("sample_id", nargs="?")
    interactive_parser.add_argument(
        "--open", action="store_true", help="用系统默认查看器打开四张图片"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = resolve_dataset_root(args.dataset_root)
        handlers = {
            "list": command_list,
            "show": command_show,
            "set": command_set,
            "clear": command_clear,
            "skip": command_skip,
            "interactive": command_interactive,
        }
        return handlers[args.command](args, root)
    except (DatasetError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
