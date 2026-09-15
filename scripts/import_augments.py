#!/usr/bin/env python3.11
"""Generate the deterministic zh-CN augment catalog from extracted LCU JSON.

This script deliberately requires CPython 3.11. It does not inspect or modify a
game installation; all four inputs must be previously extracted static files.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


EXPECTED_PYTHON = (3, 11)
REQUIRED_AUGMENT_FIELDS = {
    "id",
    "augmentNameId",
    "nameTRA",
    "augmentSmallIconPath",
    "rarity",
}
SUPPORTED_MODES = ("CHERRY", "KIWI", "KIWI_JADE")


def fail(message: str) -> "NoReturn":
    raise ValueError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path}: {exc}")


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        fail(f"cannot hash {path}: {exc}")


def duplicate_values(rows: list[dict[str, Any]], field: str) -> list[str]:
    counts = collections.Counter(str(row.get(field, "")) for row in rows)
    return sorted(value for value, count in counts.items() if value and count > 1)


def validate_augments(label: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail(f"{label}: root must be an array")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"{label}[{index}]: record must be an object")
        missing = REQUIRED_AUGMENT_FIELDS - item.keys()
        if missing:
            fail(f"{label}[{index}]: missing fields {sorted(missing)}")
        if not isinstance(item["id"], int):
            fail(f"{label}[{index}].id: expected integer")
        for field in ("augmentNameId", "nameTRA", "augmentSmallIconPath", "rarity"):
            if not isinstance(item[field], str) or not item[field].strip():
                fail(f"{label}[{index}].{field}: expected non-empty string")
        rows.append(item)

    duplicates = duplicate_values(rows, "augmentNameId")
    if duplicates:
        fail(f"{label}: duplicate technical names: {duplicates}")
    return rows


def validate_lists(label: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail(f"{label}: root must be an array")
    by_mode: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"{label}[{index}]: record must be an object")
        mode = item.get("modeName")
        members = item.get("augmentList")
        if mode not in SUPPORTED_MODES:
            fail(f"{label}[{index}]: unexpected mode {mode!r}")
        if mode in by_mode:
            fail(f"{label}: duplicate mode {mode}")
        if not isinstance(members, list) or not all(
            isinstance(member, str) and member for member in members
        ):
            fail(f"{label}[{index}].augmentList: expected non-empty strings")
        if len(set(members)) != len(members):
            fail(f"{label}[{index}].augmentList: duplicate member path")
        by_mode[mode] = item
    if set(by_mode) != set(SUPPORTED_MODES):
        fail(f"{label}: modes must be exactly {list(SUPPORTED_MODES)}")
    return [by_mode[mode] for mode in SUPPORTED_MODES]


def resolve_mode_members(
    mode_rows: list[dict[str, Any]], technical_ids: set[str]
) -> dict[str, list[str]]:
    resolved: dict[str, list[str]] = {}
    for mode_row in mode_rows:
        mode = mode_row["modeName"]
        members: list[str] = []
        for asset_path in mode_row["augmentList"]:
            basename = asset_path.rsplit("/", 1)[-1]
            if basename in technical_ids:
                technical_id = basename
            elif f"ARAM_{basename}" in technical_ids:
                technical_id = f"ARAM_{basename}"
            else:
                fail(f"{mode}: cannot resolve augment-list member {asset_path!r}")
            members.append(technical_id)
        if len(set(members)) != len(members):
            fail(f"{mode}: multiple asset paths resolve to the same technical name")
        resolved[mode] = members
    return resolved


def canonical_bytes(value: Any) -> bytes:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    paths = {
        "zh_cn_cherry_augments": args.zh_augments,
        "default_cherry_augments": args.default_augments,
        "zh_cn_augment_lists": args.zh_lists,
        "default_augment_lists": args.default_lists,
    }
    hashes = {name: sha256(path) for name, path in paths.items()}

    zh_rows = validate_augments("zh-CN cherry-augments", load_json(args.zh_augments))
    default_rows = validate_augments(
        "default cherry-augments", load_json(args.default_augments)
    )
    zh_lists = validate_lists("zh-CN augment-lists", load_json(args.zh_lists))
    default_lists = validate_lists(
        "default augment-lists", load_json(args.default_lists)
    )

    zh_by_id = {row["augmentNameId"]: row for row in zh_rows}
    default_by_id = {row["augmentNameId"]: row for row in default_rows}
    zh_ids = set(zh_by_id)
    default_ids = set(default_by_id)
    default_only = sorted(default_ids - zh_ids)
    zh_cn_only = sorted(zh_ids - default_ids)
    if default_only or zh_cn_only:
        fail(
            "default/zh-CN technical ID sets differ: "
            f"default_only={default_only}, zh_cn_only={zh_cn_only}"
        )
    if zh_lists != default_lists:
        fail("default/zh-CN augment-lists differ")

    mismatched_numeric_ids = sorted(
        technical_id
        for technical_id in zh_ids
        if zh_by_id[technical_id]["id"] != default_by_id[technical_id]["id"]
    )
    if mismatched_numeric_ids:
        fail(f"default/zh-CN numeric IDs differ: {mismatched_numeric_ids}")

    mode_members = resolve_mode_members(zh_lists, zh_ids)
    modes_by_id: dict[str, list[str]] = {technical_id: [] for technical_id in zh_ids}
    for mode in SUPPORTED_MODES:
        for technical_id in mode_members[mode]:
            modes_by_id[technical_id].append(mode)

    augments = []
    for technical_id in sorted(zh_ids):
        zh = zh_by_id[technical_id]
        default = default_by_id[technical_id]
        augments.append(
            {
                "default_name": default["nameTRA"],
                "display_name": zh["nameTRA"],
                "icon": zh["augmentSmallIconPath"],
                "id": technical_id,
                "modes": modes_by_id[technical_id],
                "numeric_id": zh["id"],
                "rarity": zh["rarity"],
            }
        )

    bundle_hash = hashlib.sha256(
        "\n".join(f"{name}:{hashes[name]}" for name in sorted(hashes)).encode("ascii")
    ).hexdigest()
    duplicate_numeric_ids = sorted(
        numeric_id
        for numeric_id, count in collections.Counter(
            row["id"] for row in zh_rows
        ).items()
        if count > 1
    )
    return {
        "augments": augments,
        "catalog_version": f"lcu-static-sha256-{bundle_hash[:16]}",
        "locale": "zh-CN",
        "schema_version": 1,
        "sources": hashes,
        "validation": {
            "default_only_technical_ids": default_only,
            "default_record_count": len(default_rows),
            "duplicate_numeric_ids": duplicate_numeric_ids,
            "duplicate_technical_ids": [],
            "empty_default_icons": 0,
            "empty_default_names": 0,
            "empty_zh_cn_icons": 0,
            "empty_zh_cn_names": 0,
            "mode_member_counts": {
                mode: len(mode_members[mode]) for mode in SUPPORTED_MODES
            },
            "zh_cn_only_technical_ids": zh_cn_only,
            "zh_cn_record_count": len(zh_rows),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zh-augments", type=Path, required=True)
    parser.add_argument("--default-augments", type=Path, required=True)
    parser.add_argument("--zh-lists", type=Path, required=True)
    parser.add_argument("--default-lists", type=Path, required=True)
    parser.add_argument(
        "--output", required=True, help="output path, or '-' for canonical JSON on stdout"
    )
    parser.add_argument(
        "--check-against", type=Path, help="fail unless generated bytes match this file"
    )
    return parser.parse_args()


def main() -> int:
    if sys.version_info[:2] != EXPECTED_PYTHON:
        print(
            f"error: CPython 3.11 is required, got {sys.version.split()[0]}",
            file=sys.stderr,
        )
        return 2
    args = parse_args()
    try:
        rendered = canonical_bytes(build_catalog(args))
        if args.check_against is not None:
            checked_in = args.check_against.read_bytes()
            if rendered != checked_in:
                fail(f"generated catalog differs from {args.check_against}")
        if args.output == "-":
            sys.stdout.buffer.write(rendered)
        else:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(rendered)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
