#!/usr/bin/env python3
"""Strictly validate the Phase2 augment-offer dataset and emit one JSON report."""

from __future__ import annotations

import argparse
import collections
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from typing import Any
import zlib

try:
    from PIL import Image
except ImportError:  # Pillow is optional; the standard-library PNG checks remain.
    Image = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_ROOT = REPO_ROOT / "data" / "dataset" / "augment_offers"
DEFAULT_KNOWLEDGE = REPO_ROOT / "data" / "knowledge" / "augments.zh-CN.json"
PROVENANCES = ("real", "synthetic", "unknown")
CAPTURE_BOUNDARIES = (
    "original_wgc",
    "preview_derived",
    "other_real_capture",
    "synthetic",
    "unknown",
)
BENCHMARK_USES = (
    "original_wgc_metrics",
    "real_scenario_calibration",
    "excluded",
)
PREVIEW_SOURCE_KINDS = {"manual_capture", "debug_preview"}
SIDES = ("left", "center", "right")
IMAGE_ARTIFACTS = {
    "RAW": "RAW.png",
    "LEFT": "LEFT_CARD.png",
    "CENTER": "CENTER_CARD.png",
    "RIGHT": "RIGHT_CARD.png",
}
LEGACY_CARD_ARTIFACTS = {
    "LEFT": "LEFT.png",
    "CENTER": "CENTER.png",
    "RIGHT": "RIGHT.png",
}
REQUIRED_NON_IMAGE_FILES = ("metadata.json", "annotation.json")
SAMPLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def configure_utf8_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except (LookupError, OSError):
            pass


def new_report(dataset_root: Path, knowledge_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_root": str(dataset_root.expanduser().absolute()),
        "knowledge_path": str(knowledge_path.expanduser().absolute()),
        "valid": False,
        "sample_count": 0,
        "real_sample_count": 0,
        "synthetic_sample_count": 0,
        "unknown_sample_count": 0,
        "annotated_sample_count": 0,
        "unannotated_sample_count": 0,
        "benchmark_eligible_sample_count": 0,
        "original_wgc_benchmark_eligible_sample_count": 0,
        "preview_derived_sample_count": 0,
        "preview_derived_benchmark_excluded_sample_count": 0,
        "skipped_excluded_sample_count": 0,
        "benchmark_excluded_counts": {
            "synthetic": 0,
            "unknown": 0,
            "invalid_or_unannotated_real": 0,
        },
        "valid_card_count": 0,
        "invalid_card_count": 0,
        "provenance_counts": {provenance: 0 for provenance in PROVENANCES},
        "capture_boundary_counts": {
            boundary: 0 for boundary in CAPTURE_BOUNDARIES
        },
        "benchmark_use_counts": {
            benchmark_use: 0 for benchmark_use in BENCHMARK_USES
        },
        "resolution_distribution": {
            artifact: {} for artifact in IMAGE_ARTIFACTS
        },
        "duplicate_sample_ids": [],
        "duplicate_sample_hashes": [],
        "samples": [],
        "issues": [],
        "warnings": [],
        "warning_count": 0,
    }


def make_issue(
    code: str,
    message: str,
    *,
    path: str | None = None,
    sample_id: str | None = None,
    provenance: str | None = None,
    side: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "message": message}
    for key, value in (
        ("path", path),
        ("sample_id", sample_id),
        ("provenance", provenance),
        ("side", side),
    ):
        if value is not None:
            issue[key] = value
    return issue


def add_global_issue(
    report: dict[str, Any], code: str, message: str, *, path: str | None = None
) -> None:
    report["issues"].append(make_issue(code, message, path=path))


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.absolute().relative_to(root).as_posix()
    except ValueError:
        return str(path.absolute())


def add_sample_issue(
    report: dict[str, Any],
    sample: dict[str, Any],
    code: str,
    message: str,
    *,
    path: Path | None = None,
    side: str | None = None,
) -> None:
    issue = make_issue(
        code,
        message,
        path=relative_path(Path(report["dataset_root"]), path) if path else None,
        sample_id=sample["sample_id"],
        provenance=sample["provenance"],
        side=side,
    )
    sample["issues"].append(issue)
    report["issues"].append(dict(issue))


def add_sample_warning(
    report: dict[str, Any],
    sample: dict[str, Any],
    code: str,
    message: str,
    *,
    path: Path | None = None,
) -> None:
    warning = make_issue(
        code,
        message,
        path=relative_path(Path(report["dataset_root"]), path) if path else None,
        sample_id=sample["sample_id"],
        provenance=sample["provenance"],
    )
    sample["warnings"].append(warning)
    report["warnings"].append(dict(warning))


def reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def loads_strict_json(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=object_without_duplicate_keys,
        parse_constant=reject_json_constant,
    )


def load_catalog(report: dict[str, Any], path: Path) -> dict[str, str]:
    try:
        resolved = path.expanduser().resolve(strict=True)
        value = loads_strict_json(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        add_global_issue(
            report, "knowledge_unreadable", f"无法读取知识库: {exc}", path=str(path)
        )
        return {}
    rows = value.get("augments") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        add_global_issue(
            report,
            "knowledge_invalid",
            "知识库根节点必须包含 augments array",
            path=str(resolved),
        )
        return {}

    catalog: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            add_global_issue(
                report,
                "knowledge_record_invalid",
                f"augments[{index}] 必须是 object",
                path=str(resolved),
            )
            continue
        augment_id = row.get("id")
        display_name = row.get("display_name")
        if not isinstance(augment_id, str) or not augment_id:
            add_global_issue(
                report,
                "knowledge_id_invalid",
                f"augments[{index}].id 必须是非空字符串",
                path=str(resolved),
            )
            continue
        if not isinstance(display_name, str) or not display_name:
            add_global_issue(
                report,
                "knowledge_name_invalid",
                f"augments[{index}].display_name 必须是非空字符串",
                path=str(resolved),
            )
            continue
        if augment_id in catalog:
            add_global_issue(
                report,
                "knowledge_duplicate_id",
                f"知识库 augment id 重复: {augment_id}",
                path=str(resolved),
            )
            continue
        catalog[augment_id] = display_name
    return catalog


def is_inside(root: Path, path: Path, *, strict: bool) -> bool:
    try:
        path.resolve(strict=strict).relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def safe_regular_file(
    report: dict[str, Any], sample: dict[str, Any], root: Path, path: Path
) -> bool:
    if path.is_symlink():
        add_sample_issue(
            report, sample, "path_symlink", "dataset artifact 不允许是符号链接", path=path
        )
        return False
    if not path.exists():
        add_sample_issue(
            report, sample, "missing_artifact", "缺少必需文件", path=path
        )
        return False
    if not path.is_file():
        add_sample_issue(
            report, sample, "artifact_not_file", "artifact 必须是普通文件", path=path
        )
        return False
    if not is_inside(root, path, strict=True):
        add_sample_issue(
            report,
            sample,
            "path_outside_dataset_root",
            "artifact 路径逃逸 dataset root",
            path=path,
        )
        return False
    return True


def parse_png(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("PNG signature 无效")
    offset = len(PNG_SIGNATURE)
    width: int | None = None
    height: int | None = None
    saw_idat = False
    saw_iend = False
    idat = bytearray()
    chunk_index = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("PNG chunk 被截断")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise ValueError("PNG chunk 长度越界")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError(f"PNG {chunk_type!r} CRC 无效")
        if chunk_index == 0 and chunk_type != b"IHDR":
            raise ValueError("PNG 首个 chunk 不是 IHDR")
        if chunk_type == b"IHDR":
            if length != 13 or width is not None:
                raise ValueError("PNG IHDR 无效")
            width, height = struct.unpack(">II", payload[:8])
            if width <= 0 or height <= 0:
                raise ValueError("PNG 分辨率必须为正数")
        elif chunk_type == b"IDAT":
            saw_idat = True
            idat.extend(payload)
        elif chunk_type == b"IEND":
            if length != 0:
                raise ValueError("PNG IEND 长度无效")
            saw_iend = True
            offset = chunk_end
            break
        offset = chunk_end
        chunk_index += 1
    if width is None or height is None or not saw_idat or not saw_iend:
        raise ValueError("PNG 缺少 IHDR/IDAT/IEND")
    if offset != len(data):
        raise ValueError("PNG IEND 后存在额外数据")
    try:
        zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f"PNG IDAT 无法解压: {exc}") from exc

    if Image is not None:
        try:
            with Image.open(path) as image:
                if image.format != "PNG":
                    raise ValueError(f"图片格式不是 PNG: {image.format}")
                if image.size != (width, height):
                    raise ValueError("Pillow 与 IHDR 分辨率不一致")
                image.verify()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Pillow 校验失败: {exc}") from exc
    return width, height


def load_json_file(path: Path) -> Any:
    return loads_strict_json(path.read_text(encoding="utf-8"))


def valid_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_metadata(
    report: dict[str, Any],
    sample: dict[str, Any],
    root: Path,
    sample_dir: Path,
) -> None:
    path = sample_dir / "metadata.json"
    if not safe_regular_file(report, sample, root, path):
        return
    try:
        value = load_json_file(path)
    except (OSError, UnicodeError, ValueError) as exc:
        add_sample_issue(
            report, sample, "metadata_unreadable", f"metadata JSON 无法读取: {exc}", path=path
        )
        return
    if not isinstance(value, dict):
        add_sample_issue(
            report, sample, "metadata_invalid", "metadata 根节点必须是 object", path=path
        )
        return
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        add_sample_issue(
            report, sample, "metadata_schema_version", "metadata.schema_version 必须为 1", path=path
        )
    if value.get("sample_id") != sample["sample_id"]:
        add_sample_issue(
            report,
            sample,
            "metadata_sample_id_mismatch",
            "metadata.sample_id 与目录名不一致",
            path=path,
        )
    metadata_provenance = value.get("provenance")
    if metadata_provenance not in PROVENANCES:
        add_sample_issue(
            report,
            sample,
            "metadata_provenance_invalid",
            "metadata.provenance 必须是 real、synthetic 或 unknown",
            path=path,
        )
    if metadata_provenance != sample["provenance"]:
        add_sample_issue(
            report,
            sample,
            "metadata_provenance_mismatch",
            "metadata.provenance 与父目录不一致",
            path=path,
        )
    if "sample_kind" in value and value.get("sample_kind") != metadata_provenance:
        add_sample_issue(
            report,
            sample,
            "metadata_sample_kind_mismatch",
            "兼容字段 metadata.sample_kind 必须与 provenance 一致",
            path=path,
        )
    source_kind: str | None = None
    source = value.get("source")
    if not isinstance(source, dict):
        add_sample_issue(
            report, sample, "metadata_source_invalid", "metadata.source 必须是 object", path=path
        )
    else:
        kind = source.get("kind")
        source_kind = kind if isinstance(kind, str) else None
        reference = source.get("reference")
        allowed_kinds = {
            "real": {
                "windows_graphics_capture",
                "desktop_duplication",
                "phase1_capture",
                "manual_capture",
                "debug_preview",
            },
            "synthetic": {"replay", "stub", "synthetic"},
            "unknown": {"unknown", "replay", "stub"},
        }[sample["provenance"]]
        if kind not in allowed_kinds:
            add_sample_issue(
                report,
                sample,
                "metadata_source_kind",
                f"{sample['provenance']} provenance 的 source.kind 非法",
                path=path,
            )
        if not isinstance(reference, str) or not reference.strip():
            add_sample_issue(
                report,
                sample,
                "metadata_source_reference",
                "metadata.source.reference 必须是非空字符串",
                path=path,
            )
    derived_from_preview = value.get("derived_from_preview")
    if "derived_from_preview" in value and not isinstance(derived_from_preview, bool):
        add_sample_issue(
            report,
            sample,
            "metadata_derived_from_preview",
            "metadata.derived_from_preview 必须是 boolean",
            path=path,
        )
        derived_from_preview = None

    capture_boundary = value.get("capture_boundary")
    if "capture_boundary" in value and capture_boundary not in CAPTURE_BOUNDARIES:
        add_sample_issue(
            report,
            sample,
            "metadata_capture_boundary",
            "metadata.capture_boundary 必须是 typed capture boundary",
            path=path,
        )
        capture_boundary = None

    benchmark_use = value.get("benchmark_use")
    if "benchmark_use" in value and benchmark_use not in BENCHMARK_USES:
        add_sample_issue(
            report,
            sample,
            "metadata_benchmark_use",
            "metadata.benchmark_use 必须是 typed benchmark use",
            path=path,
        )
        benchmark_use = None

    sample["source_kind"] = source_kind
    sample["derived_from_preview"] = derived_from_preview
    preview_signals: list[str] = []
    if derived_from_preview is True:
        preview_signals.append("derived_from_preview=true")
    if source_kind in PREVIEW_SOURCE_KINDS:
        preview_signals.append(f"source.kind={source_kind}")
    if capture_boundary == "preview_derived":
        preview_signals.append("capture_boundary=preview_derived")

    if sample["provenance"] == "real" and (
        "capture_boundary" not in value or "benchmark_use" not in value
    ):
        add_sample_warning(
            report,
            sample,
            "legacy_benchmark_boundary_inferred",
            "legacy metadata 缺少 capture_boundary/benchmark_use；validator 已按现有来源信号保守推断",
            path=path,
        )

    if preview_signals:
        sample["capture_boundary"] = "preview_derived"
        sample["benchmark_use"] = "real_scenario_calibration"
        if sample["provenance"] == "real":
            add_sample_warning(
                report,
                sample,
                "preview_derived_excluded_from_original_wgc_benchmark",
                "preview-derived 真实场景素材仅用于校准，禁止进入 original-WGC 指标；信号: "
                + ", ".join(preview_signals),
                path=path,
            )
    elif sample["provenance"] == "real":
        sample["capture_boundary"] = (
            capture_boundary
            if capture_boundary is not None
            else (
                "original_wgc"
                if source_kind == "windows_graphics_capture"
                else "other_real_capture"
            )
        )
        sample["benchmark_use"] = (
            benchmark_use
            if benchmark_use is not None
            else (
                "original_wgc_metrics"
                if sample["capture_boundary"] == "original_wgc"
                else "real_scenario_calibration"
            )
        )
        if not (
            source_kind == "windows_graphics_capture"
            and sample["capture_boundary"] == "original_wgc"
            and sample["benchmark_use"] == "original_wgc_metrics"
        ):
            add_sample_warning(
                report,
                sample,
                "non_original_wgc_excluded_from_benchmark",
                "只有明确的 direct windows_graphics_capture 可进入 original-WGC 指标",
                path=path,
            )
    elif sample["provenance"] == "synthetic":
        sample["capture_boundary"] = "synthetic"
        sample["benchmark_use"] = "excluded"
    else:
        sample["capture_boundary"] = "unknown"
        sample["benchmark_use"] = "excluded"
    if "captured_at_utc" in value and not valid_datetime(value["captured_at_utc"]):
        add_sample_issue(
            report,
            sample,
            "metadata_captured_at",
            "metadata.captured_at_utc 必须是带时区的 ISO 8601 时间",
            path=path,
        )
    if "timestamp" in value and not valid_datetime(value["timestamp"]):
        add_sample_issue(
            report,
            sample,
            "metadata_timestamp",
            "metadata.timestamp 必须是带时区的 ISO 8601 时间",
            path=path,
        )


def validate_card_annotation(
    report: dict[str, Any],
    sample: dict[str, Any],
    path: Path,
    side: str,
    card: Any,
    catalog: dict[str, str],
) -> None:
    if not isinstance(card, dict):
        add_sample_issue(
            report,
            sample,
            "card_annotation_invalid",
            "card annotation 必须是 object",
            path=path,
            side=side,
        )
        return
    required = {"augment_id", "augment_name", "valid"}
    missing = sorted(required - card.keys())
    extra = sorted(card.keys() - required)
    if missing:
        add_sample_issue(
            report,
            sample,
            "card_annotation_missing_fields",
            f"card annotation 缺少字段: {missing}",
            path=path,
            side=side,
        )
    if extra:
        add_sample_issue(
            report,
            sample,
            "card_annotation_extra_fields",
            f"card annotation 包含未知字段: {extra}",
            path=path,
            side=side,
        )
    valid = card.get("valid")
    if type(valid) is not bool:
        add_sample_issue(
            report,
            sample,
            "invalid_card",
            "card.valid 必须是 boolean",
            path=path,
            side=side,
        )
        return
    if valid:
        report["valid_card_count"] += 1
        augment_id = card.get("augment_id")
        augment_name = card.get("augment_name")
        if not isinstance(augment_id, str) or not augment_id:
            add_sample_issue(
                report,
                sample,
                "augment_id_invalid",
                "valid card 的 augment_id 必须是非空字符串",
                path=path,
                side=side,
            )
            return
        if not isinstance(augment_name, str) or not augment_name:
            add_sample_issue(
                report,
                sample,
                "augment_name_invalid",
                "valid card 的 augment_name 必须是非空字符串",
                path=path,
                side=side,
            )
        expected_name = catalog.get(augment_id)
        if expected_name is None:
            add_sample_issue(
                report,
                sample,
                "unknown_augment_id",
                f"augment_id 不在知识库中: {augment_id}",
                path=path,
                side=side,
            )
        elif augment_name != expected_name:
            add_sample_issue(
                report,
                sample,
                "augment_name_mismatch",
                f"augment_name 与知识库不一致，期望 {expected_name!r}",
                path=path,
                side=side,
            )
    else:
        report["invalid_card_count"] += 1
        if card.get("augment_id") is not None or card.get("augment_name") is not None:
            add_sample_issue(
                report,
                sample,
                "invalid_card_has_label",
                "valid=false 时 augment_id/augment_name 必须为 null",
                path=path,
                side=side,
            )


def validate_annotation(
    report: dict[str, Any],
    sample: dict[str, Any],
    root: Path,
    sample_dir: Path,
    catalog: dict[str, str],
) -> bool:
    path = sample_dir / "annotation.json"
    if not path.exists():
        add_sample_issue(
            report,
            sample,
            "annotation_missing",
            "缺少人工 annotation.json，样本不能计入准确率",
            path=path,
        )
        return False
    if not safe_regular_file(report, sample, root, path):
        return False
    try:
        value = load_json_file(path)
    except (OSError, UnicodeError, ValueError) as exc:
        add_sample_issue(
            report,
            sample,
            "annotation_unreadable",
            f"annotation JSON 无法读取: {exc}",
            path=path,
        )
        return False
    if not isinstance(value, dict):
        add_sample_issue(
            report, sample, "annotation_invalid", "annotation 根节点必须是 object", path=path
        )
        return False
    sample["annotation_status"] = value.get("status")
    sample["skip_reason"] = value.get("skip_reason")
    expected_fields = {
        "schema_version",
        "sample_id",
        "status",
        "cards",
        "skip_reason",
        "updated_at_utc",
    }
    missing_fields = sorted(expected_fields - value.keys())
    extra_fields = sorted(value.keys() - expected_fields)
    if missing_fields:
        add_sample_issue(
            report,
            sample,
            "annotation_missing_fields",
            f"annotation 缺少字段: {missing_fields}",
            path=path,
        )
    if extra_fields:
        add_sample_issue(
            report,
            sample,
            "annotation_extra_fields",
            f"annotation 包含未知字段: {extra_fields}",
            path=path,
        )
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        add_sample_issue(
            report,
            sample,
            "annotation_schema_version",
            "annotation.schema_version 必须为 1",
            path=path,
        )
    if value.get("sample_id") != sample["sample_id"]:
        add_sample_issue(
            report,
            sample,
            "annotation_sample_id_mismatch",
            "annotation.sample_id 与目录名不一致",
            path=path,
        )
    if not valid_datetime(value.get("updated_at_utc")):
        add_sample_issue(
            report,
            sample,
            "annotation_updated_at",
            "annotation.updated_at_utc 必须是带时区的 ISO 8601 时间",
            path=path,
        )

    cards = value.get("cards")
    if not isinstance(cards, dict):
        add_sample_issue(
            report, sample, "annotation_cards_invalid", "annotation.cards 必须是 object", path=path
        )
        return False
    unknown_sides = sorted(set(cards) - set(SIDES))
    if unknown_sides:
        add_sample_issue(
            report,
            sample,
            "annotation_unknown_sides",
            f"annotation.cards 包含未知卡位: {unknown_sides}",
            path=path,
        )
    for side in SIDES:
        if side in cards:
            validate_card_annotation(report, sample, path, side, cards[side], catalog)

    status = value.get("status")
    complete = status == "complete" and set(cards) == set(SIDES)
    if status == "complete":
        missing_sides = sorted(set(SIDES) - set(cards))
        if missing_sides:
            add_sample_issue(
                report,
                sample,
                "annotation_incomplete",
                f"complete annotation 缺少卡位: {missing_sides}",
                path=path,
            )
        if value.get("skip_reason") is not None:
            add_sample_issue(
                report,
                sample,
                "annotation_skip_reason",
                "complete annotation 的 skip_reason 必须为 null",
                path=path,
            )
    elif status == "in_progress":
        add_sample_issue(
            report, sample, "annotation_incomplete", "annotation 尚未完成", path=path
        )
    elif status == "skipped":
        reason = value.get("skip_reason")
        if not isinstance(reason, str) or not reason.strip():
            add_sample_issue(
                report,
                sample,
                "annotation_skip_reason",
                "skipped annotation 必须有非空 skip_reason",
                path=path,
            )
        else:
            add_sample_warning(
                report,
                sample,
                "annotation_skipped",
                f"样本被人工跳过，不能计入准确率；skip_reason={reason!r}",
                path=path,
            )
    else:
        add_sample_issue(
            report,
            sample,
            "annotation_status_invalid",
            f"未知 annotation.status: {status!r}",
            path=path,
        )
    return complete


def validate_sample_files(
    report: dict[str, Any],
    sample: dict[str, Any],
    root: Path,
    sample_dir: Path,
    resolutions: dict[str, collections.Counter[str]],
) -> str | None:
    entries = list(sample_dir.iterdir())
    entry_names = {entry.name for entry in entries}
    canonical_cards = set(IMAGE_ARTIFACTS.values()) - {"RAW.png"}
    legacy_cards = set(LEGACY_CARD_ARTIFACTS.values())
    canonical_present = entry_names & canonical_cards
    legacy_present = entry_names & legacy_cards

    selected_images = IMAGE_ARTIFACTS
    sample["artifact_naming"] = "canonical"
    if canonical_present and legacy_present:
        add_sample_issue(
            report,
            sample,
            "mixed_artifact_names",
            "禁止混用 canonical *_CARD.png 与 deprecated LEFT/CENTER/RIGHT.png",
            path=sample_dir,
        )
    elif legacy_present:
        selected_images = {"RAW": "RAW.png", **LEGACY_CARD_ARTIFACTS}
        sample["artifact_naming"] = "legacy_deprecated"
        add_sample_warning(
            report,
            sample,
            "deprecated_artifact_names",
            (
                "LEFT.png/CENTER.png/RIGHT.png 仅用于一次迁移兼容；"
                "请迁移为 LEFT_CARD.png/CENTER_CARD.png/RIGHT_CARD.png"
            ),
            path=sample_dir,
        )

    expected_names = {*selected_images.values(), *REQUIRED_NON_IMAGE_FILES}
    for entry in entries:
        if entry.name not in expected_names:
            add_sample_issue(
                report,
                sample,
                "unexpected_sample_entry",
                "样本目录包含未声明的文件或目录",
                path=entry,
            )
    card_pngs = [
        entry
        for entry in entries
        if entry.name != "RAW.png" and entry.suffix.lower() == ".png"
    ]
    if len(card_pngs) != 3:
        add_sample_issue(
            report,
            sample,
            "card_count",
            f"三卡图片数量必须恰好为 3，实际为 {len(card_pngs)}",
            path=sample_dir,
        )

    readable_images: dict[str, bytes] = {}
    for artifact, filename in selected_images.items():
        path = sample_dir / filename
        if not safe_regular_file(report, sample, root, path):
            continue
        try:
            width, height = parse_png(path)
            readable_images[filename] = path.read_bytes()
        except (OSError, ValueError) as exc:
            add_sample_issue(
                report,
                sample,
                "invalid_image",
                f"{filename} 不是完整有效的 PNG: {exc}",
                path=path,
                side=artifact.lower() if artifact != "RAW" else None,
            )
            continue
        resolutions[artifact][f"{width}x{height}"] += 1

    if set(readable_images) != set(selected_images.values()):
        return None
    digest = hashlib.sha256()
    for artifact, filename in selected_images.items():
        digest.update(artifact.encode("ascii"))
        digest.update(b"\0")
        digest.update(readable_images[filename])
        digest.update(b"\0")
    return digest.hexdigest()


def validate_dataset(dataset_root: Path, knowledge_path: Path) -> dict[str, Any]:
    report = new_report(dataset_root, knowledge_path)
    catalog = load_catalog(report, knowledge_path)
    requested_root = dataset_root.expanduser()
    if requested_root.is_symlink():
        add_global_issue(
            report,
            "dataset_root_symlink",
            "dataset root 不允许是符号链接",
            path=str(requested_root),
        )
        return report
    try:
        root = requested_root.resolve(strict=True)
    except OSError as exc:
        add_global_issue(
            report,
            "dataset_root_unreadable",
            f"dataset root 不可访问: {exc}",
            path=str(requested_root),
        )
        return report
    report["dataset_root"] = str(root)
    if not root.is_dir():
        add_global_issue(
            report,
            "dataset_root_not_directory",
            "dataset root 必须是目录",
            path=str(root),
        )
        return report

    samples_on_disk: list[tuple[str, str, Path]] = []
    by_id: dict[str, list[tuple[str, Path]]] = collections.defaultdict(list)
    for provenance in PROVENANCES:
        provenance_dir = root / provenance
        if provenance_dir.is_symlink():
            add_global_issue(
                report,
                "provenance_directory_symlink",
                f"{provenance} 目录不允许是符号链接",
                path=str(provenance_dir),
            )
            continue
        if not provenance_dir.is_dir():
            if provenance == "unknown":
                # Optional while empty so existing two-bucket datasets remain
                # valid during the one-time migration window.
                continue
            add_global_issue(
                report,
                "missing_provenance_directory",
                f"缺少 {provenance} provenance 目录",
                path=str(provenance_dir),
            )
            continue
        for entry in sorted(provenance_dir.iterdir(), key=lambda item: item.name):
            if entry.name == ".gitkeep":
                continue
            if entry.is_symlink():
                add_global_issue(
                    report,
                    "sample_path_symlink",
                    "样本路径不允许是符号链接",
                    path=str(entry),
                )
                continue
            if not entry.is_dir():
                add_global_issue(
                    report,
                    "unexpected_dataset_entry",
                    "provenance 目录只能包含样本目录和 .gitkeep",
                    path=str(entry),
                )
                continue
            if not is_inside(root, entry, strict=True):
                add_global_issue(
                    report,
                    "path_outside_dataset_root",
                    "样本路径逃逸 dataset root",
                    path=str(entry),
                )
                continue
            sample_id = entry.name
            if sample_id in {".", ".."} or SAMPLE_ID_PATTERN.fullmatch(sample_id) is None:
                add_global_issue(
                    report,
                    "sample_id_invalid",
                    f"sample-id 格式非法: {sample_id!r}",
                    path=str(entry),
                )
            samples_on_disk.append((sample_id, provenance, entry))
            by_id[sample_id].append((provenance, entry))

    duplicate_ids = {
        sample_id: rows for sample_id, rows in by_id.items() if len(rows) > 1
    }
    report["duplicate_sample_ids"] = [
        {
            "sample_id": sample_id,
            "provenances": sorted(provenance for provenance, _path in rows),
        }
        for sample_id, rows in sorted(duplicate_ids.items())
    ]
    for sample_id, rows in sorted(duplicate_ids.items()):
        add_global_issue(
            report,
            "duplicate_sample_id",
            f"sample-id 在多个目录中重复: {sample_id} ({len(rows)} 次)",
        )

    samples_on_disk.sort(key=lambda item: (item[0], item[1]))
    report["sample_count"] = len(samples_on_disk)
    for _sample_id, provenance, _path in samples_on_disk:
        report["provenance_counts"][provenance] += 1
    report["real_sample_count"] = report["provenance_counts"]["real"]
    report["synthetic_sample_count"] = report["provenance_counts"]["synthetic"]
    report["unknown_sample_count"] = report["provenance_counts"]["unknown"]

    resolutions = {
        artifact: collections.Counter() for artifact in IMAGE_ARTIFACTS
    }
    hashes: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    sample_records: dict[tuple[str, str], dict[str, Any]] = {}
    for sample_id, provenance, sample_dir in samples_on_disk:
        sample = {
            "sample_id": sample_id,
            "provenance": provenance,
            "path": relative_path(root, sample_dir),
            "annotated": False,
            "annotation_status": None,
            "skip_reason": None,
            "sample_sha256": None,
            "valid": False,
            "benchmark_eligible": False,
            "source_kind": None,
            "derived_from_preview": None,
            "capture_boundary": "unknown",
            "benchmark_use": "excluded",
            "artifact_naming": None,
            "issues": [],
            "warnings": [],
        }
        report["samples"].append(sample)
        sample_records[(sample_id, provenance)] = sample
        if sample_id in duplicate_ids:
            add_sample_issue(
                report,
                sample,
                "duplicate_sample_id",
                "该 sample-id 在多个 provenance 分桶中重复",
                path=sample_dir,
            )
        try:
            sample_hash = validate_sample_files(
                report, sample, root, sample_dir, resolutions
            )
            validate_metadata(report, sample, root, sample_dir)
            sample["annotated"] = validate_annotation(
                report, sample, root, sample_dir, catalog
            )
        except OSError as exc:
            add_sample_issue(
                report,
                sample,
                "sample_unreadable",
                f"样本目录无法完整读取: {exc}",
                path=sample_dir,
            )
            sample_hash = None
        if sample_hash is not None:
            sample["sample_sha256"] = sample_hash
            hashes[sample_hash].append(
                {"sample_id": sample_id, "provenance": provenance}
            )

    duplicates = {
        digest: rows for digest, rows in hashes.items() if len(rows) > 1
    }
    report["duplicate_sample_hashes"] = [
        {"sha256": digest, "samples": sorted(rows, key=lambda row: (row["sample_id"], row["provenance"]))}
        for digest, rows in sorted(duplicates.items())
    ]
    for digest, rows in sorted(duplicates.items()):
        add_global_issue(
            report,
            "duplicate_sample_hash",
            f"样本内容哈希重复: {digest} ({len(rows)} 次)",
        )
        for row in rows:
            sample = sample_records[(row["sample_id"], row["provenance"])]
            add_sample_issue(
                report,
                sample,
                "duplicate_sample_hash",
                f"样本内容哈希与其他样本重复: {digest}",
                path=root / sample["path"],
            )

    for sample in report["samples"]:
        sample["valid"] = not sample["issues"]
        sample["benchmark_eligible"] = bool(
            sample["valid"]
            and sample["annotated"]
            and sample["provenance"] == "real"
            and sample["source_kind"] == "windows_graphics_capture"
            and sample["derived_from_preview"] is not True
            and sample["capture_boundary"] == "original_wgc"
            and sample["benchmark_use"] == "original_wgc_metrics"
        )
    report["annotated_sample_count"] = sum(
        bool(sample["annotated"]) for sample in report["samples"]
    )
    report["unannotated_sample_count"] = (
        report["sample_count"] - report["annotated_sample_count"]
    )
    report["benchmark_eligible_sample_count"] = sum(
        bool(sample["benchmark_eligible"]) for sample in report["samples"]
    )
    report["original_wgc_benchmark_eligible_sample_count"] = report[
        "benchmark_eligible_sample_count"
    ]
    for sample in report["samples"]:
        report["capture_boundary_counts"][sample["capture_boundary"]] += 1
        report["benchmark_use_counts"][sample["benchmark_use"]] += 1
    report["preview_derived_sample_count"] = report["capture_boundary_counts"][
        "preview_derived"
    ]
    report["preview_derived_benchmark_excluded_sample_count"] = sum(
        bool(
            sample["provenance"] == "real"
            and sample["capture_boundary"] == "preview_derived"
            and not sample["benchmark_eligible"]
        )
        for sample in report["samples"]
    )
    report["skipped_excluded_sample_count"] = sum(
        bool(
            sample["valid"]
            and sample["annotation_status"] == "skipped"
            and sample["provenance"] == "real"
        )
        for sample in report["samples"]
    )
    report["benchmark_excluded_counts"] = {
        "synthetic": report["provenance_counts"]["synthetic"],
        "unknown": report["provenance_counts"]["unknown"],
        "invalid_or_unannotated_real": (
            report["provenance_counts"]["real"]
            - report["benchmark_eligible_sample_count"]
        ),
    }
    report["resolution_distribution"] = {
        artifact: dict(sorted(counter.items()))
        for artifact, counter in resolutions.items()
    }
    report["warning_count"] = len(report["warnings"])
    report["valid"] = not report["issues"]
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT, help="数据集根目录"
    )
    parser.add_argument(
        "--knowledge", type=Path, default=DEFAULT_KNOWLEDGE, help="强化符文知识库"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        report = validate_dataset(args.dataset_root, args.knowledge)
    except Exception as exc:  # Keep stdout valid JSON even for an unexpected failure.
        report = new_report(args.dataset_root, args.knowledge)
        add_global_issue(
            report,
            "internal_validator_error",
            f"validator 内部错误: {type(exc).__name__}: {exc}",
        )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
