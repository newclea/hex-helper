#!/usr/bin/env python3.11
"""Import deterministic KIWI/KIWI_JADE augment icon templates from local assets.

The importer is intentionally offline-only. It can inspect already extracted
asset trees and, when explicitly given a local WAD plus a local wadtools binary
and local hash tables, unpack only static PNG resources. It never starts or
inspects a League process and never downloads missing inputs.
"""

from __future__ import annotations

import argparse
import collections
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tempfile
from typing import Any, NoReturn
import zlib


EXPECTED_PYTHON = (3, 11)
TARGET_MODES = ("KIWI", "KIWI_JADE")
ICON_URI_PREFIX = "/lol-game-data/assets/"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class Candidate:
    augment_id: str
    icon_path: str
    modes: tuple[str, ...]


@dataclass(frozen=True)
class AssetPayload:
    data: bytes
    source_label: str


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def canonical_bytes(value: Any) -> bytes:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return (rendered + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        fail(f"cannot hash {path}: {exc}")
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path}: {exc}")


def stable_source_path(path: Path) -> str:
    try:
        return path.resolve(strict=True).as_posix()
    except OSError as exc:
        fail(f"cannot resolve source path {path}: {exc}")


def icon_relative_path(icon_path: str) -> str:
    if not icon_path.casefold().startswith(ICON_URI_PREFIX.casefold()):
        fail(f"unsupported icon URI outside {ICON_URI_PREFIX!r}: {icon_path!r}")
    relative_text = icon_path[len(ICON_URI_PREFIX) :]
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or not relative.parts or any(
        part in ("", ".", "..") for part in relative.parts
    ):
        fail(f"unsafe icon URI: {icon_path!r}")
    if relative.suffix.casefold() != ".png":
        fail(f"icon URI is not PNG: {icon_path!r}")
    return relative.as_posix().casefold()


def load_candidates(catalog_path: Path) -> tuple[dict[str, Any], list[Candidate]]:
    root = load_json(catalog_path)
    if not isinstance(root, dict):
        fail("catalog root must be an object")
    catalog_version = root.get("catalog_version")
    rows = root.get("augments")
    if not isinstance(catalog_version, str) or not catalog_version:
        fail("catalog_version must be a non-empty string")
    if not isinstance(rows, list):
        fail("catalog augments must be an array")

    candidates: list[Candidate] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            fail(f"catalog augments[{index}] must be an object")
        augment_id = row.get("id")
        icon_path = row.get("icon")
        modes = row.get("modes")
        if not isinstance(augment_id, str) or not augment_id:
            fail(f"catalog augments[{index}].id must be a non-empty string")
        if augment_id in seen_ids:
            fail(f"duplicate catalog augment ID: {augment_id}")
        seen_ids.add(augment_id)
        if not isinstance(icon_path, str) or not icon_path:
            fail(f"catalog augments[{index}].icon must be a non-empty string")
        if not isinstance(modes, list) or not all(
            isinstance(mode, str) and mode for mode in modes
        ):
            fail(f"catalog augments[{index}].modes must be non-empty strings")
        selected_modes = tuple(mode for mode in TARGET_MODES if mode in modes)
        if not selected_modes:
            continue
        icon_relative_path(icon_path)
        candidates.append(Candidate(augment_id, icon_path, selected_modes))
    candidates.sort(key=lambda candidate: candidate.augment_id)
    return root, candidates


def collect_asset_tree(
    root: Path,
    source_label: str,
    wanted_by_relative: dict[str, str],
    payloads_by_icon: dict[str, list[AssetPayload]],
) -> None:
    if not root.is_dir():
        fail(f"asset root is not a directory: {root}")
    try:
        files = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix().casefold(),
        )
    except OSError as exc:
        fail(f"cannot enumerate asset root {root}: {exc}")

    relative_keys = tuple(sorted(wanted_by_relative))
    for path in files:
        if path.suffix.casefold() != ".png":
            continue
        normalized = path.relative_to(root).as_posix().casefold()
        matched_relative = next(
            (
                relative
                for relative in relative_keys
                if normalized == relative or normalized.endswith("/" + relative)
            ),
            None,
        )
        if matched_relative is None:
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            fail(f"cannot read asset {path}: {exc}")
        icon_path = wanted_by_relative[matched_relative]
        payloads_by_icon[icon_path].append(AssetPayload(data, source_label))


def validate_wad_inputs(args: argparse.Namespace) -> None:
    if not args.wad:
        return
    if args.wadtools is None or not args.wadtools.is_file():
        fail("--wad requires a local --wadtools executable")
    if args.hashtable_dir is None or not args.hashtable_dir.is_dir():
        fail("--wad requires a local --hashtable-dir")
    if not any(args.hashtable_dir.glob("lcu-*.lhdb")):
        fail("--hashtable-dir contains no local lcu-*.lhdb table")
    for wad in args.wad:
        if not wad.is_file():
            fail(f"WAD source is not a file: {wad}")


def extract_wad_assets(
    args: argparse.Namespace,
    staging_root: Path,
    wanted_by_relative: dict[str, str],
    payloads_by_icon: dict[str, list[AssetPayload]],
) -> None:
    validate_wad_inputs(args)
    relative_paths = sorted(wanted_by_relative)
    relative_batches = [
        relative_paths[index : index + 40]
        for index in range(0, len(relative_paths), 40)
    ]
    for index, wad in enumerate(args.wad):
        destination = staging_root / f"wad-{index:02d}"
        for batch in relative_batches:
            escaped_paths = "|".join(re.escape(path) for path in batch)
            pattern = (
                r"^plugins/rcp-be-lol-game-data/global/default/(?:"
                + escaped_paths
                + r")$"
            )
            command = [
                str(args.wadtools.resolve(strict=True)),
                "--hashtable-dir",
                str(args.hashtable_dir.resolve(strict=True)),
                "extract",
                "-i",
                str(wad.resolve(strict=True)),
                "-o",
                str(destination),
                "-f",
                "png",
                "-x",
                pattern,
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                fail(f"wadtools extraction failed for {wad}: {detail}")
        collect_asset_tree(
            destination,
            f"wad:{stable_source_path(wad)}",
            wanted_by_relative,
            payloads_by_icon,
        )


def paeth_predictor(left: int, up: int, up_left: int) -> int:
    prediction = left + up - up_left
    left_distance = abs(prediction - left)
    up_distance = abs(prediction - up)
    up_left_distance = abs(prediction - up_left)
    if left_distance <= up_distance and left_distance <= up_left_distance:
        return left
    if up_distance <= up_left_distance:
        return up
    return up_left


def decode_png_luma(data: bytes) -> tuple[int, int, list[int]]:
    if not data.startswith(PNG_SIGNATURE):
        fail("invalid PNG signature")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    palette: list[tuple[int, int, int]] | None = None
    idat_parts: list[bytes] = []

    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(data):
            fail("truncated PNG chunk")
        chunk_data = data[data_start:data_end]
        expected_crc = struct.unpack_from(">I", data, data_end)[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(chunk_data, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            fail(f"PNG chunk CRC mismatch for {chunk_type!r}")
        offset = crc_end

        if chunk_type == b"IHDR":
            if length != 13 or width is not None:
                fail("invalid or duplicate PNG IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", chunk_data)
            )
            if width == 0 or height == 0 or width > 4096 or height > 4096:
                fail("PNG dimensions are invalid or unexpectedly large")
            if compression != 0 or filtering != 0 or interlace != 0:
                fail("unsupported PNG compression, filter, or interlace method")
        elif chunk_type == b"PLTE":
            if length == 0 or length % 3 != 0:
                fail("invalid PNG palette")
            palette = [
                tuple(chunk_data[index : index + 3])
                for index in range(0, length, 3)
            ]
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if None in (width, height, bit_depth, color_type, interlace):
        fail("PNG has no valid IHDR")
    if bit_depth != 8:
        fail(f"unsupported PNG bit depth {bit_depth}; expected 8")
    channels_by_color_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_by_color_type.get(color_type)
    if channels is None:
        fail(f"unsupported PNG color type {color_type}")
    if color_type == 3 and palette is None:
        fail("indexed PNG has no palette")
    if not idat_parts:
        fail("PNG has no IDAT data")

    row_bytes = width * channels
    try:
        filtered = zlib.decompress(b"".join(idat_parts))
    except zlib.error as exc:
        fail(f"cannot decompress PNG IDAT: {exc}")
    expected_size = height * (row_bytes + 1)
    if len(filtered) != expected_size:
        fail(
            f"unexpected PNG scanline size {len(filtered)}; expected {expected_size}"
        )

    rows: list[bytearray] = []
    cursor = 0
    previous = bytearray(row_bytes)
    for _ in range(height):
        filter_type = filtered[cursor]
        cursor += 1
        source = filtered[cursor : cursor + row_bytes]
        cursor += row_bytes
        current = bytearray(row_bytes)
        for index, value in enumerate(source):
            left = current[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                reconstructed = value
            elif filter_type == 1:
                reconstructed = value + left
            elif filter_type == 2:
                reconstructed = value + up
            elif filter_type == 3:
                reconstructed = value + ((left + up) // 2)
            elif filter_type == 4:
                reconstructed = value + paeth_predictor(left, up, up_left)
            else:
                fail(f"unsupported PNG row filter {filter_type}")
            current[index] = reconstructed & 0xFF
        rows.append(current)
        previous = current

    luma: list[int] = []
    for row in rows:
        for x in range(width):
            index = x * channels
            if color_type in (0, 4):
                red = green = blue = row[index]
            elif color_type == 3:
                palette_index = row[index]
                if palette is None or palette_index >= len(palette):
                    fail("PNG palette index is out of range")
                red, green, blue = palette[palette_index]
            else:
                red, green, blue = row[index : index + 3]
            luma.append((29 * blue + 150 * green + 77 * red) >> 8)
    return width, height, luma


def resized_luma(
    pixels: list[int],
    width: int,
    height: int,
    target_x: int,
    target_y: int,
) -> int:
    x_denominator = 8
    y_denominator = 7
    x_numerator = target_x * (width - 1)
    y_numerator = target_y * (height - 1)
    x0, x_remainder = divmod(x_numerator, x_denominator)
    y0, y_remainder = divmod(y_numerator, y_denominator)
    x1 = min(x0 + 1, width - 1)
    y1 = min(y0 + 1, height - 1)
    top = (
        pixels[y0 * width + x0] * (x_denominator - x_remainder)
        + pixels[y0 * width + x1] * x_remainder
    )
    bottom = (
        pixels[y1 * width + x0] * (x_denominator - x_remainder)
        + pixels[y1 * width + x1] * x_remainder
    )
    denominator = x_denominator * y_denominator
    value = (
        top * (y_denominator - y_remainder) + bottom * y_remainder
    )
    return (value + denominator // 2) // denominator


def difference_hash(pixels: list[int], width: int, height: int) -> int:
    result = 0
    bit = 0
    for y in range(8):
        for x in range(8):
            if resized_luma(pixels, width, height, x, y) < resized_luma(
                pixels, width, height, x + 1, y
            ):
                result |= 1 << bit
            bit += 1
    return result


def source_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for root in sorted(args.asset_root, key=lambda path: stable_source_path(path)):
        records.append(
            {"kind": "asset_root", "path": stable_source_path(root)}
        )
    for wad in sorted(args.wad, key=lambda path: stable_source_path(path)):
        records.append(
            {
                "kind": "wad",
                "path": stable_source_path(wad),
                "sha256": sha256_file(wad),
                "size": wad.stat().st_size,
            }
        )
    if args.wad:
        records.append(
            {
                "kind": "extractor",
                "path": stable_source_path(args.wadtools),
                "sha256": sha256_file(args.wadtools),
            }
        )
        hash_manifest = args.hashtable_dir / "manifest.json"
        records.append(
            {
                "kind": "hashtable_manifest",
                "path": stable_source_path(hash_manifest),
                "sha256": sha256_file(hash_manifest),
            }
        )
    return sorted(records, key=lambda record: (record["kind"], record["path"]))


def build_manifest(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, bytes]]:
    catalog, candidates = load_candidates(args.catalog)
    candidates_by_icon: dict[str, list[Candidate]] = collections.defaultdict(list)
    wanted_by_relative: dict[str, str] = {}
    for candidate in candidates:
        candidates_by_icon[candidate.icon_path].append(candidate)
        relative = icon_relative_path(candidate.icon_path)
        previous = wanted_by_relative.setdefault(relative, candidate.icon_path)
        if previous != candidate.icon_path:
            fail(
                "case-insensitive icon path collision: "
                f"{previous!r} and {candidate.icon_path!r}"
            )

    payloads_by_icon: dict[str, list[AssetPayload]] = collections.defaultdict(list)
    for root in args.asset_root:
        collect_asset_tree(
            root,
            f"asset_root:{stable_source_path(root)}",
            wanted_by_relative,
            payloads_by_icon,
        )

    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=args.output) as temporary:
        extract_wad_assets(
            args,
            Path(temporary),
            wanted_by_relative,
            payloads_by_icon,
        )

    templates: list[dict[str, Any]] = []
    images: dict[str, bytes] = {}
    unresolved: list[dict[str, Any]] = []
    imported_icon_paths: set[str] = set()
    for icon_path in sorted(candidates_by_icon):
        path_candidates = candidates_by_icon[icon_path]
        payloads = payloads_by_icon.get(icon_path, [])
        if not payloads:
            unresolved.append(
                {
                    "candidate_ids": sorted(
                        candidate.augment_id for candidate in path_candidates
                    ),
                    "icon_path": icon_path,
                    "reason": "asset_not_found",
                }
            )
            continue
        payloads_by_hash: dict[str, list[AssetPayload]] = collections.defaultdict(list)
        for payload in payloads:
            payloads_by_hash[sha256_bytes(payload.data)].append(payload)
        if len(payloads_by_hash) != 1:
            unresolved.append(
                {
                    "candidate_ids": sorted(
                        candidate.augment_id for candidate in path_candidates
                    ),
                    "icon_path": icon_path,
                    "reason": "source_content_conflict",
                    "source_hashes": sorted(payloads_by_hash),
                }
            )
            continue

        icon_sha256, equivalent_payloads = next(iter(payloads_by_hash.items()))
        data = equivalent_payloads[0].data
        try:
            width, height, luma = decode_png_luma(data)
            dhash = difference_hash(luma, width, height)
        except ValueError as exc:
            unresolved.append(
                {
                    "candidate_ids": sorted(
                        candidate.augment_id for candidate in path_candidates
                    ),
                    "icon_path": icon_path,
                    "reason": f"png_decode_failed:{exc}",
                }
            )
            continue

        images.setdefault(icon_sha256, data)
        imported_icon_paths.add(icon_path)
        shared_ids = sorted(candidate.augment_id for candidate in path_candidates)
        source_labels = sorted(
            {payload.source_label for payload in equivalent_payloads}
        )
        for candidate in path_candidates:
            templates.append(
                {
                    "augment_id": candidate.augment_id,
                    "candidate_ids_for_source_icon": shared_ids,
                    "difference_hash": f"{dhash:016x}",
                    "file": f"icons/{icon_sha256}.png",
                    "height": height,
                    "icon_sha256": icon_sha256,
                    "modes": list(candidate.modes),
                    "source_icon_path": icon_path,
                    "sources": source_labels,
                    "width": width,
                }
            )
    templates.sort(key=lambda template: template["augment_id"])
    unresolved.sort(key=lambda item: item["icon_path"])

    mode_distribution = {
        mode: sum(mode in template["modes"] for template in templates)
        for mode in TARGET_MODES
    }
    requested_mode_distribution = {
        mode: sum(mode in candidate.modes for candidate in candidates)
        for mode in TARGET_MODES
    }
    hash_groups: dict[str, set[str]] = collections.defaultdict(set)
    for template in templates:
        hash_groups[template["difference_hash"]].add(template["augment_id"])

    catalog_bytes = args.catalog.read_bytes()
    sources = source_records(args)
    summary = {
        "difference_hash_ambiguity_groups": sum(
            len(ids) > 1 for ids in hash_groups.values()
        ),
        "imported_mode_distribution": mode_distribution,
        "imported_source_icon_paths": len(imported_icon_paths),
        "imported_template_count": len(templates),
        "missing_template_count": len(candidates) - len(templates),
        "requested_mode_distribution": requested_mode_distribution,
        "requested_source_icon_paths": len(candidates_by_icon),
        "requested_template_count": len(candidates),
        "shared_source_icon_groups": sum(
            len(path_candidates) > 1
            for path_candidates in candidates_by_icon.values()
        ),
        "unique_image_files": len(images),
        "unresolved": unresolved,
    }
    manifest_without_content_hash = {
        "catalog": {
            "sha256": sha256_bytes(catalog_bytes),
            "source": stable_source_path(args.catalog),
            "version": catalog["catalog_version"],
        },
        "patch_version": args.patch_version,
        "schema_version": 1,
        "sources": sources,
        "summary": summary,
        "templates": templates,
    }
    content_hash = sha256_bytes(canonical_bytes(manifest_without_content_hash))
    manifest = dict(manifest_without_content_hash)
    manifest["content_hash"] = content_hash
    return manifest, images


def verify_generated_tree(
    output: Path, manifest_bytes: bytes, images: dict[str, bytes]
) -> None:
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file() or manifest_path.read_bytes() != manifest_bytes:
        fail(f"generated manifest differs from {manifest_path}")
    icons_dir = output / "icons"
    expected_names = {f"{digest}.png" for digest in images}
    actual_names = (
        {path.name for path in icons_dir.iterdir() if path.is_file()}
        if icons_dir.is_dir()
        else set()
    )
    if actual_names != expected_names:
        fail(
            "generated icon file set differs: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )
    for digest, data in images.items():
        path = icons_dir / f"{digest}.png"
        if path.read_bytes() != data or sha256_file(path) != digest:
            fail(f"generated icon bytes differ from expected source: {path}")


def write_generated_tree(
    output: Path, manifest_bytes: bytes, images: dict[str, bytes]
) -> None:
    icons_dir = output / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {f"{digest}.png" for digest in images}
    for existing in icons_dir.iterdir():
        if not existing.is_file() or existing.suffix.casefold() != ".png":
            fail(f"refusing to replace unexpected entry in generated icon dir: {existing}")
        if existing.name not in expected_names:
            existing.unlink()
    for digest, data in sorted(images.items()):
        destination = icons_dir / f"{digest}.png"
        if destination.is_file() and destination.read_bytes() == data:
            continue
        temporary = icons_dir / f".{digest}.tmp"
        temporary.write_bytes(data)
        os.replace(temporary, destination)
    temporary_manifest = output / ".manifest.json.tmp"
    temporary_manifest.write_bytes(manifest_bytes)
    os.replace(temporary_manifest, output / "manifest.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/knowledge/augments.zh-CN.json"),
    )
    parser.add_argument(
        "--asset-root",
        type=Path,
        action="append",
        default=[],
        help="already extracted local asset tree; repeatable",
    )
    parser.add_argument(
        "--wad",
        type=Path,
        action="append",
        default=[],
        help="local static WAD archive; repeatable and never downloaded",
    )
    parser.add_argument("--wadtools", type=Path)
    parser.add_argument("--hashtable-dir", type=Path)
    parser.add_argument("--patch-version", default="unknown")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/knowledge/augment_icons"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify byte-for-byte output without replacing generated files",
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
        if not args.patch_version.strip():
            fail("--patch-version must be non-empty; use 'unknown' when unavailable")
        if not args.catalog.is_file():
            fail(f"catalog is not a file: {args.catalog}")
        for root in args.asset_root:
            if not root.is_dir():
                fail(f"asset root is not a directory: {root}")
        validate_wad_inputs(args)
        manifest, images = build_manifest(args)
        rendered = canonical_bytes(manifest)
        if args.check:
            verify_generated_tree(args.output, rendered, images)
        else:
            write_generated_tree(args.output, rendered, images)
        print(json.dumps(manifest["summary"], ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
