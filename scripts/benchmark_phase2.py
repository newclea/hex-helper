#!/usr/bin/env python3
"""Benchmark Phase 2 recognition results against explicitly annotated samples.

Canonical input is either one JSON bundle or a directory containing three JSONL
streams. All JSON is UTF-8 and duplicate object keys, NaN, and Infinity are
rejected.

Bundle schema (``phase2_benchmark_input_v1``)::

    {
      "schema_version": "phase2_benchmark_input_v1",
      "samples": [{
        "sample_id": "real-001",
        "metadata": {
          "provenance": "real",                 # real | synthetic
          "source": {"kind": "windows_graphics_capture"},
          "derived_from_preview": false,
          "capture_boundary": "original_wgc",
          "benchmark_use": "original_wgc_metrics",
          "resolution": {"width": 1920, "height": 1080},
          "ui_scale": "100%",                  # non-empty string or >0 number
          "ocr_preprocessing_variant": {
            "grayscale": true,
            "contrast": 1.25,                   # >0 number or null
            "threshold": 128,                   # integer 0..255 or null
            "scale": "2x",                     # positive integer followed by x
            "variant_id": "gray-c125-t128-2x"  # optional
          }
        },
        "annotation": {
          "screen_present": true,
          "cards": [
            {
              "slot": "LEFT",                  # LEFT | CENTER | RIGHT
              "valid": true,
              "augment_id": "augment.left",
              "ocr_text": "Annotated title",
              "icon_id": "icon.left"
            },
            {"slot": "CENTER", "valid": false},
            {"slot": "RIGHT", "valid": false}
          ]
        },
        "recognition_result": {
          "screen_detected": true,
          "latency_ms": 12.5,
          "cards": [{
            "slot": "LEFT",
            "state": "RECOGNIZED",             # RECOGNIZED | UNKNOWN
            "augment_id": "augment.left",
            "ocr_text": "Annotated title",
            "icon_id": "icon.left"
          }]
        }
      }]
    }

For split input, the directory must contain ``metadata.jsonl``,
``annotations.jsonl``, and ``recognition_results.jsonl``. Each non-blank line is
the corresponding nested object above plus ``sample_id``. The three streams are
strictly joined by identical sample-id sets.

Acceptance scope is deliberately fixed to original WGC rows: ``provenance=real``
plus a clear direct ``windows_graphics_capture`` boundary and
``benchmark_use=original_wgc_metrics``. Any preview-derived signal forces
calibration-only exclusion. Legacy benchmark metadata without boundary fields is
accepted with an explicit warning. Synthetic and excluded-real rows never enter
distributions, rates, or latency. Screen detection uses every selected sample.
Per-card metrics use only cards whose annotation has ``valid=true``. The
three-card metric uses only samples for which LEFT, CENTER, and RIGHT are all
valid. UNKNOWN/missing final predictions are incorrect; an incorrect non-UNKNOWN
augment prediction is a false match. OCR uses deterministic NFKC+casefold title
equality after removing Unicode whitespace and punctuation (never fuzzy
matching). Icon ID and final augment ID remain exact. An independent stage marked
UNAVAILABLE is excluded only from that stage's denominator; stage UNKNOWN is
incorrect. Rates with no denominator are JSON null, never fabricated zeroes.
P95 uses the nearest-rank definition.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from typing import Any, Iterable, Sequence


INPUT_SCHEMA_VERSION = "phase2_benchmark_input_v1"
REPORT_SCHEMA_VERSION = "phase2_benchmark_report_v1"
SLOTS = ("LEFT", "CENTER", "RIGHT")
UNKNOWN_STATES = {"UNKNOWN", "NOT_RECOGNIZED"}
VARIANT_KEYS = {"grayscale", "contrast", "threshold", "scale", "variant_id"}
CAPTURE_BOUNDARIES = {
    "original_wgc",
    "preview_derived",
    "other_real_capture",
    "synthetic",
    "unknown",
}
BENCHMARK_USES = {
    "original_wgc_metrics",
    "real_scenario_calibration",
    "excluded",
}
SOURCE_KINDS = {
    "windows_graphics_capture",
    "desktop_duplication",
    "replay",
    "stub",
    "unknown",
    "phase1_capture",
    "manual_capture",
    "debug_preview",
    "synthetic",
}
PREVIEW_SOURCE_KINDS = {"manual_capture", "debug_preview"}


class SchemaError(ValueError):
    """Raised when benchmark input does not satisfy the documented schema."""


def _schema_error(location: str, message: str) -> SchemaError:
    return SchemaError(f"{location}: {message}")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _loads_strict_json(text: str, location: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise _schema_error(location, f"invalid strict JSON: {error}") from error


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as error:
        raise _schema_error(str(path), f"cannot read UTF-8 JSON: {error}") from error
    return _loads_strict_json(text, str(path))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as error:
        raise _schema_error(str(path), f"cannot read UTF-8 JSONL: {error}") from error

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        location = f"{path}:{line_number}"
        record = _loads_strict_json(line, location)
        if not isinstance(record, dict):
            raise _schema_error(location, "each JSONL record must be an object")
        records.append(record)
    return records


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _schema_error(location, "must be an object")
    return value


def _require_array(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise _schema_error(location, "must be an array")
    return value


def _require_bool(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise _schema_error(location, "must be a boolean")
    return value


def _require_non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _schema_error(location, "must be a non-empty string")
    return value


def _require_finite_number(value: Any, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _schema_error(location, "must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise _schema_error(location, "must be a finite number")
    return number


def _reject_unknown_keys(
    value: dict[str, Any], allowed: set[str], location: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _schema_error(location, f"unknown fields: {', '.join(unknown)}")


def _require_keys(value: dict[str, Any], required: set[str], location: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise _schema_error(location, f"missing fields: {', '.join(missing)}")


def _optional_text(value: Any, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _schema_error(location, "must be a string or null")
    return value


def _validate_variant(value: Any, location: str) -> dict[str, Any]:
    variant = _require_object(value, location)
    _require_keys(variant, {"grayscale", "contrast", "threshold", "scale"}, location)
    _reject_unknown_keys(variant, VARIANT_KEYS, location)

    grayscale = _require_bool(variant["grayscale"], f"{location}.grayscale")

    contrast_value = variant["contrast"]
    if contrast_value is None:
        contrast: float | int | None = None
    else:
        contrast_number = _require_finite_number(
            contrast_value, f"{location}.contrast"
        )
        if contrast_number <= 0:
            raise _schema_error(f"{location}.contrast", "must be greater than zero")
        contrast = contrast_value

    threshold_value = variant["threshold"]
    if threshold_value is None:
        threshold: int | None = None
    elif (
        isinstance(threshold_value, bool)
        or not isinstance(threshold_value, int)
        or not 0 <= threshold_value <= 255
    ):
        raise _schema_error(
            f"{location}.threshold", "must be an integer from 0 through 255 or null"
        )
    else:
        threshold = threshold_value

    scale = _require_non_empty_string(variant["scale"], f"{location}.scale")
    if re.fullmatch(r"[1-9][0-9]*x", scale) is None:
        raise _schema_error(
            f"{location}.scale", "must be a positive integer followed by 'x'"
        )

    normalized: dict[str, Any] = {
        "grayscale": grayscale,
        "contrast": contrast,
        "threshold": threshold,
        "scale": scale,
    }
    if "variant_id" in variant:
        normalized["variant_id"] = _require_non_empty_string(
            variant["variant_id"], f"{location}.variant_id"
        )
    return normalized


def _validate_metadata(value: Any, location: str) -> dict[str, Any]:
    metadata = _require_object(value, location)
    required = {"provenance", "resolution", "ui_scale", "ocr_preprocessing_variant"}
    allowed = required | {
        "source",
        "derived_from_preview",
        "capture_boundary",
        "benchmark_use",
        "data_boundary",
    }
    _require_keys(metadata, required, location)
    _reject_unknown_keys(metadata, allowed, location)

    provenance = _require_non_empty_string(
        metadata["provenance"], f"{location}.provenance"
    )
    if provenance not in {"real", "synthetic"}:
        raise _schema_error(
            f"{location}.provenance", "must be exactly 'real' or 'synthetic'"
        )

    source_kind: str | None = None
    if "source" in metadata:
        source_location = f"{location}.source"
        source = _require_object(metadata["source"], source_location)
        source_allowed = {"kind", "reference", "id"}
        _require_keys(source, {"kind"}, source_location)
        _reject_unknown_keys(source, source_allowed, source_location)
        source_kind = _require_non_empty_string(
            source["kind"], f"{source_location}.kind"
        )
        if source_kind not in SOURCE_KINDS:
            raise _schema_error(
                f"{source_location}.kind", "must be a recognized capture source kind"
            )
        for optional_key in ("reference", "id"):
            if optional_key in source:
                _require_non_empty_string(
                    source[optional_key], f"{source_location}.{optional_key}"
                )

    derived_from_preview: bool | None = None
    if "derived_from_preview" in metadata:
        derived_from_preview = _require_bool(
            metadata["derived_from_preview"],
            f"{location}.derived_from_preview",
        )

    capture_boundary: str | None = None
    if "capture_boundary" in metadata:
        capture_boundary = _require_non_empty_string(
            metadata["capture_boundary"], f"{location}.capture_boundary"
        )
        if capture_boundary not in CAPTURE_BOUNDARIES:
            raise _schema_error(
                f"{location}.capture_boundary", "must be a typed capture boundary"
            )

    benchmark_use: str | None = None
    if "benchmark_use" in metadata:
        benchmark_use = _require_non_empty_string(
            metadata["benchmark_use"], f"{location}.benchmark_use"
        )
        if benchmark_use not in BENCHMARK_USES:
            raise _schema_error(
                f"{location}.benchmark_use", "must be a typed benchmark use"
            )

    data_boundary: str | None = None
    if "data_boundary" in metadata:
        data_boundary = _require_non_empty_string(
            metadata["data_boundary"], f"{location}.data_boundary"
        )

    preview_signals: list[str] = []
    if derived_from_preview is True:
        preview_signals.append("derived_from_preview=true")
    if source_kind in PREVIEW_SOURCE_KINDS:
        preview_signals.append(f"source.kind={source_kind}")
    if capture_boundary == "preview_derived":
        preview_signals.append("capture_boundary=preview_derived")
    if data_boundary is not None and "preview" in data_boundary.lower():
        preview_signals.append("legacy data_boundary contains preview")

    boundary_warnings: list[dict[str, str]] = []
    legacy_unspecified = not any(
        key in metadata
        for key in (
            "source",
            "derived_from_preview",
            "capture_boundary",
            "benchmark_use",
            "data_boundary",
        )
    )
    if preview_signals:
        effective_capture_boundary = "preview_derived"
        effective_benchmark_use = "real_scenario_calibration"
        benchmark_eligible = False
        if provenance == "real":
            boundary_warnings.append(
                {
                    "code": "preview_derived_excluded_from_original_wgc_benchmark",
                    "message": (
                        "preview-derived real-scene material is calibration-only and "
                        "excluded from original-WGC metrics; signals: "
                        + ", ".join(preview_signals)
                    ),
                }
            )
    elif provenance == "synthetic":
        effective_capture_boundary = "synthetic"
        effective_benchmark_use = "excluded"
        benchmark_eligible = False
    elif legacy_unspecified:
        effective_capture_boundary = "original_wgc"
        effective_benchmark_use = "original_wgc_metrics"
        benchmark_eligible = True
        boundary_warnings.append(
            {
                "code": "legacy_benchmark_boundary_assumed_original_wgc",
                "message": (
                    "legacy benchmark metadata has no capture-boundary fields; "
                    "it remains included for compatibility and should be migrated"
                ),
            }
        )
    else:
        effective_capture_boundary = (
            capture_boundary
            if capture_boundary is not None
            else (
                "original_wgc"
                if source_kind == "windows_graphics_capture"
                else "other_real_capture"
            )
        )
        effective_benchmark_use = (
            benchmark_use
            if benchmark_use is not None
            else (
                "original_wgc_metrics"
                if effective_capture_boundary == "original_wgc"
                else "real_scenario_calibration"
            )
        )
        benchmark_eligible = bool(
            source_kind == "windows_graphics_capture"
            and derived_from_preview is not True
            and effective_capture_boundary == "original_wgc"
            and effective_benchmark_use == "original_wgc_metrics"
        )
        if "capture_boundary" not in metadata or "benchmark_use" not in metadata:
            boundary_warnings.append(
                {
                    "code": "legacy_benchmark_boundary_inferred",
                    "message": (
                        "capture_boundary/benchmark_use was inferred from legacy "
                        "metadata source signals"
                    ),
                }
            )
        if not benchmark_eligible:
            boundary_warnings.append(
                {
                    "code": "non_original_wgc_excluded_from_benchmark",
                    "message": (
                        "only a clear direct windows_graphics_capture sample with "
                        "benchmark_use=original_wgc_metrics is eligible"
                    ),
                }
            )

    resolution_location = f"{location}.resolution"
    resolution = _require_object(metadata["resolution"], resolution_location)
    _require_keys(resolution, {"width", "height"}, resolution_location)
    _reject_unknown_keys(resolution, {"width", "height"}, resolution_location)
    dimensions: dict[str, int] = {}
    for name in ("width", "height"):
        raw_dimension = resolution[name]
        if (
            isinstance(raw_dimension, bool)
            or not isinstance(raw_dimension, int)
            or raw_dimension <= 0
        ):
            raise _schema_error(
                f"{resolution_location}.{name}", "must be a positive integer"
            )
        dimensions[name] = raw_dimension

    ui_scale = metadata["ui_scale"]
    if isinstance(ui_scale, str):
        if not ui_scale.strip():
            raise _schema_error(f"{location}.ui_scale", "must not be empty")
    else:
        ui_scale_number = _require_finite_number(ui_scale, f"{location}.ui_scale")
        if ui_scale_number <= 0:
            raise _schema_error(f"{location}.ui_scale", "must be greater than zero")

    return {
        "provenance": provenance,
        "source_kind": source_kind,
        "derived_from_preview": derived_from_preview,
        "capture_boundary": effective_capture_boundary,
        "benchmark_use": effective_benchmark_use,
        "benchmark_eligible": benchmark_eligible,
        "boundary_warnings": boundary_warnings,
        "resolution": dimensions,
        "ui_scale": ui_scale,
        "ocr_preprocessing_variant": _validate_variant(
            metadata["ocr_preprocessing_variant"],
            f"{location}.ocr_preprocessing_variant",
        ),
    }


def _validate_annotation_card(value: Any, location: str) -> dict[str, Any]:
    card = _require_object(value, location)
    allowed = {"slot", "valid", "augment_id", "ocr_text", "icon_id"}
    _require_keys(card, {"slot", "valid"}, location)
    _reject_unknown_keys(card, allowed, location)

    slot = _require_non_empty_string(card["slot"], f"{location}.slot")
    if slot not in SLOTS:
        raise _schema_error(f"{location}.slot", f"must be one of {', '.join(SLOTS)}")
    valid = _require_bool(card["valid"], f"{location}.valid")

    if not valid:
        for field in ("augment_id", "ocr_text", "icon_id"):
            if field in card and card[field] is not None:
                raise _schema_error(
                    f"{location}.{field}", "must be absent or null when valid=false"
                )
        return {"slot": slot, "valid": False}

    required_labels = {"augment_id", "ocr_text", "icon_id"}
    _require_keys(card, required_labels, location)
    return {
        "slot": slot,
        "valid": True,
        "augment_id": _require_non_empty_string(
            card["augment_id"], f"{location}.augment_id"
        ),
        "ocr_text": _require_non_empty_string(card["ocr_text"], f"{location}.ocr_text"),
        "icon_id": _require_non_empty_string(card["icon_id"], f"{location}.icon_id"),
    }


def _validate_annotation(value: Any, location: str) -> dict[str, Any]:
    annotation = _require_object(value, location)
    allowed = {"screen_present", "cards"}
    _require_keys(annotation, allowed, location)
    _reject_unknown_keys(annotation, allowed, location)
    screen_present = _require_bool(
        annotation["screen_present"], f"{location}.screen_present"
    )

    raw_cards = _require_array(annotation["cards"], f"{location}.cards")
    if len(raw_cards) != len(SLOTS):
        raise _schema_error(
            f"{location}.cards", "must contain exactly LEFT, CENTER, and RIGHT"
        )
    cards = [
        _validate_annotation_card(card, f"{location}.cards[{index}]")
        for index, card in enumerate(raw_cards)
    ]
    slots = [card["slot"] for card in cards]
    if set(slots) != set(SLOTS) or len(set(slots)) != len(SLOTS):
        raise _schema_error(
            f"{location}.cards", "must contain each of LEFT, CENTER, and RIGHT once"
        )
    if not screen_present and any(card["valid"] for card in cards):
        raise _schema_error(
            location, "screen_present=false cannot have valid card annotations"
        )
    cards.sort(key=lambda card: SLOTS.index(card["slot"]))
    return {"screen_present": screen_present, "cards": cards}


def _validate_recognition_card(value: Any, location: str) -> dict[str, Any]:
    card = _require_object(value, location)
    allowed = {
        "slot", "state", "augment_id", "ocr_text", "ocr_state",
        "ocr_backend", "icon_id", "icon_state", "icon_confidence",
    }
    _require_keys(card, {"slot", "state"}, location)
    _reject_unknown_keys(card, allowed, location)

    slot = _require_non_empty_string(card["slot"], f"{location}.slot")
    if slot not in SLOTS:
        raise _schema_error(f"{location}.slot", f"must be one of {', '.join(SLOTS)}")
    state = _require_non_empty_string(card["state"], f"{location}.state")
    if state not in {"RECOGNIZED", *UNKNOWN_STATES}:
        raise _schema_error(
            f"{location}.state",
            "must be RECOGNIZED, UNKNOWN, or NOT_RECOGNIZED",
        )

    if state in UNKNOWN_STATES:
        for field in ("augment_id", "ocr_text", "icon_id"):
            if field in card and card[field] is not None:
                raise _schema_error(
                    f"{location}.{field}",
                    "must be absent or null for an UNKNOWN prediction",
                )
        augment_id = None
    else:
        _require_keys(card, {"augment_id"}, location)
        augment_id = _require_non_empty_string(
            card["augment_id"], f"{location}.augment_id"
        )

    ocr_text = _optional_text(card.get("ocr_text"), f"{location}.ocr_text")
    ocr_state = card.get(
        "ocr_state", "RECOGNIZED" if ocr_text is not None else "UNAVAILABLE"
    )
    ocr_state = _require_non_empty_string(ocr_state, f"{location}.ocr_state")
    if ocr_state not in {"RECOGNIZED", "UNKNOWN", "UNAVAILABLE"}:
        raise _schema_error(
            f"{location}.ocr_state", "must be RECOGNIZED, UNKNOWN, or UNAVAILABLE"
        )
    if (ocr_state == "RECOGNIZED") != (ocr_text is not None):
        raise _schema_error(
            f"{location}.ocr_text", "must be non-empty only for RECOGNIZED OCR"
        )
    ocr_backend = _optional_text(
        card.get("ocr_backend"), f"{location}.ocr_backend"
    )

    icon_id = _optional_text(card.get("icon_id"), f"{location}.icon_id")
    icon_state = card.get(
        "icon_state", "MATCHED" if icon_id is not None else "UNAVAILABLE"
    )
    icon_state = _require_non_empty_string(icon_state, f"{location}.icon_state")
    if icon_state not in {"MATCHED", "UNKNOWN", "UNAVAILABLE"}:
        raise _schema_error(
            f"{location}.icon_state", "must be MATCHED, UNKNOWN, or UNAVAILABLE"
        )
    if (icon_state == "MATCHED") != (icon_id is not None):
        raise _schema_error(
            f"{location}.icon_id", "must be non-empty only for MATCHED icon"
        )
    icon_confidence = card.get("icon_confidence")
    if icon_confidence is not None:
        icon_confidence = _require_finite_number(
            icon_confidence, f"{location}.icon_confidence"
        )
        if not 0.0 <= icon_confidence <= 1.0:
            raise _schema_error(
                f"{location}.icon_confidence", "must be between 0 and 1"
            )

    return {
        "slot": slot,
        "state": "UNKNOWN" if state in UNKNOWN_STATES else "RECOGNIZED",
        "augment_id": augment_id,
        "ocr_text": ocr_text,
        "ocr_state": ocr_state,
        "ocr_backend": ocr_backend,
        "icon_id": icon_id,
        "icon_state": icon_state,
        "icon_confidence": icon_confidence,
    }


def _validate_recognition_result(value: Any, location: str) -> dict[str, Any]:
    result = _require_object(value, location)
    allowed = {"screen_detected", "latency_ms", "cards"}
    _require_keys(result, allowed, location)
    _reject_unknown_keys(result, allowed, location)

    screen_detected = _require_bool(
        result["screen_detected"], f"{location}.screen_detected"
    )
    latency_ms = _require_finite_number(result["latency_ms"], f"{location}.latency_ms")
    if latency_ms < 0:
        raise _schema_error(f"{location}.latency_ms", "must be non-negative")

    raw_cards = _require_array(result["cards"], f"{location}.cards")
    if len(raw_cards) > len(SLOTS):
        raise _schema_error(f"{location}.cards", "must contain at most three cards")
    cards = [
        _validate_recognition_card(card, f"{location}.cards[{index}]")
        for index, card in enumerate(raw_cards)
    ]
    slots = [card["slot"] for card in cards]
    if len(set(slots)) != len(slots):
        raise _schema_error(f"{location}.cards", "contains duplicate card slots")
    cards.sort(key=lambda card: SLOTS.index(card["slot"]))
    return {
        "screen_detected": screen_detected,
        "latency_ms": latency_ms,
        "cards": cards,
    }


def _validate_sample(value: Any, location: str) -> dict[str, Any]:
    sample = _require_object(value, location)
    allowed = {"sample_id", "metadata", "annotation", "recognition_result"}
    _require_keys(sample, allowed, location)
    _reject_unknown_keys(sample, allowed, location)
    return {
        "sample_id": _require_non_empty_string(
            sample["sample_id"], f"{location}.sample_id"
        ),
        "metadata": _validate_metadata(sample["metadata"], f"{location}.metadata"),
        "annotation": _validate_annotation(
            sample["annotation"], f"{location}.annotation"
        ),
        "recognition_result": _validate_recognition_result(
            sample["recognition_result"], f"{location}.recognition_result"
        ),
    }


def _validate_samples(raw_samples: Iterable[Any], location: str) -> list[dict[str, Any]]:
    samples = [
        _validate_sample(sample, f"{location}.samples[{index}]")
        for index, sample in enumerate(raw_samples)
    ]
    seen: set[str] = set()
    for sample in samples:
        sample_id = sample["sample_id"]
        if sample_id in seen:
            raise _schema_error(location, f"duplicate sample_id {sample_id!r}")
        seen.add(sample_id)
    return samples


def _load_bundle(path: Path) -> list[dict[str, Any]]:
    document = _require_object(_read_json(path), str(path))
    allowed = {"schema_version", "samples"}
    _require_keys(document, allowed, str(path))
    _reject_unknown_keys(document, allowed, str(path))
    if document["schema_version"] != INPUT_SCHEMA_VERSION:
        raise _schema_error(
            f"{path}.schema_version",
            f"must be exactly {INPUT_SCHEMA_VERSION!r}",
        )
    raw_samples = _require_array(document["samples"], f"{path}.samples")
    return _validate_samples(raw_samples, str(path))


def _records_by_id(
    records: list[dict[str, Any]], path: Path
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        location = f"{path}:{index + 1}"
        if "sample_id" not in record:
            raise _schema_error(location, "missing field: sample_id")
        sample_id = _require_non_empty_string(record["sample_id"], f"{location}.sample_id")
        if sample_id in indexed:
            raise _schema_error(location, f"duplicate sample_id {sample_id!r}")
        indexed[sample_id] = record
    return indexed


def _load_split_directory(directory: Path) -> list[dict[str, Any]]:
    paths = {
        "metadata": directory / "metadata.jsonl",
        "annotation": directory / "annotations.jsonl",
        "recognition_result": directory / "recognition_results.jsonl",
    }
    indexed = {
        name: _records_by_id(_read_jsonl(path), path) for name, path in paths.items()
    }
    id_sets = {name: set(records) for name, records in indexed.items()}
    if len({frozenset(ids) for ids in id_sets.values()}) != 1:
        details = ", ".join(
            f"{name}={len(ids)}" for name, ids in sorted(id_sets.items())
        )
        raise _schema_error(
            str(directory), f"split streams must have identical sample-id sets ({details})"
        )

    samples: list[dict[str, Any]] = []
    for sample_id in indexed["metadata"]:
        nested: dict[str, Any] = {"sample_id": sample_id}
        for name in ("metadata", "annotation", "recognition_result"):
            record = dict(indexed[name][sample_id])
            record.pop("sample_id")
            nested[name] = record
        samples.append(nested)
    return _validate_samples(samples, str(directory))


def load_dataset(source: str | Path) -> dict[str, Any]:
    """Load and validate input, returning records plus source-state evidence."""

    path = Path(source)
    if not path.exists():
        return {"samples": [], "source_state": "missing", "raw_asset_count": 0}
    if path.is_file():
        return {
            "samples": _load_bundle(path),
            "source_state": "bundle_loaded",
            "raw_asset_count": 1,
        }
    if not path.is_dir():
        raise _schema_error(str(path), "must be a JSON file or directory")

    bundle_paths = [
        candidate
        for candidate in (path / "dataset.json", path / "samples.json")
        if candidate.is_file()
    ]
    split_paths = [
        path / "metadata.jsonl",
        path / "annotations.jsonl",
        path / "recognition_results.jsonl",
    ]
    split_present = [candidate.is_file() for candidate in split_paths]
    if bundle_paths and any(split_present):
        raise _schema_error(
            str(path), "contains both bundle and split schemas; choose exactly one"
        )
    if len(bundle_paths) > 1:
        raise _schema_error(str(path), "contains both dataset.json and samples.json")
    if bundle_paths:
        return {
            "samples": _load_bundle(bundle_paths[0]),
            "source_state": "bundle_loaded",
            "raw_asset_count": sum(1 for candidate in path.rglob("*") if candidate.is_file()),
        }
    if any(split_present) and not all(split_present):
        missing = [
            candidate.name
            for candidate, present in zip(split_paths, split_present)
            if not present
        ]
        raise _schema_error(
            str(path), f"incomplete split schema; missing {', '.join(missing)}"
        )
    if all(split_present):
        return {
            "samples": _load_split_directory(path),
            "source_state": "split_jsonl_loaded",
            "raw_asset_count": sum(1 for candidate in path.rglob("*") if candidate.is_file()),
        }

    return {
        "samples": [],
        "source_state": "no_benchmark_records",
        "raw_asset_count": sum(1 for candidate in path.rglob("*") if candidate.is_file()),
    }


def _new_accumulator() -> dict[str, Any]:
    return {
        "sample_count": 0,
        "card_count": 0,
        "three_card_sample_count": 0,
        "screen_correct": 0,
        "screen_denominator": 0,
        "ocr_correct": 0,
        "ocr_denominator": 0,
        "icon_correct": 0,
        "icon_denominator": 0,
        "card_correct": 0,
        "card_denominator": 0,
        "three_cards_correct": 0,
        "three_cards_denominator": 0,
        "unknown_count": 0,
        "false_match_count": 0,
        "latencies": [],
        "failures": [],
    }


def _normalize_lol_title(value: str) -> str:
    """Deterministic title equality: NFKC/casefold, then remove whitespace/punctuation."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _add_sample(
    accumulator: dict[str, Any], sample: dict[str, Any], collect_failures: bool
) -> None:
    accumulator["sample_count"] += 1
    annotation = sample["annotation"]
    result = sample["recognition_result"]
    accumulator["latencies"].append(result["latency_ms"])

    accumulator["screen_denominator"] += 1
    reasons: list[dict[str, Any]] = []
    if annotation["screen_present"] == result["screen_detected"]:
        accumulator["screen_correct"] += 1
    else:
        reasons.append(
            {
                "code": "screen_detection_mismatch",
                "expected": annotation["screen_present"],
                "predicted": result["screen_detected"],
            }
        )

    predicted_by_slot = {card["slot"]: card for card in result["cards"]}
    valid_cards = [card for card in annotation["cards"] if card["valid"]]
    accumulator["card_count"] += len(valid_cards)
    all_three_valid = len(valid_cards) == len(SLOTS)
    all_three_correct = all_three_valid
    if all_three_valid:
        accumulator["three_card_sample_count"] += 1
        accumulator["three_cards_denominator"] += 1

    for expected in valid_cards:
        slot = expected["slot"]
        predicted = predicted_by_slot.get(slot)
        is_unknown = predicted is None or predicted["state"] == "UNKNOWN"

        accumulator["card_denominator"] += 1

        if predicted is not None and predicted["ocr_state"] != "UNAVAILABLE":
            accumulator["ocr_denominator"] += 1
            if (
                predicted["ocr_state"] == "RECOGNIZED"
                and _normalize_lol_title(predicted["ocr_text"])
                == _normalize_lol_title(expected["ocr_text"])
            ):
                accumulator["ocr_correct"] += 1
            else:
                reasons.append(
                    {
                        "code": "ocr_mismatch",
                        "slot": slot,
                        "expected_ocr_text": expected["ocr_text"],
                        "predicted_ocr_text": predicted["ocr_text"],
                    }
                )

        if predicted is not None and predicted["icon_state"] != "UNAVAILABLE":
            accumulator["icon_denominator"] += 1
            if (
                predicted["icon_state"] == "MATCHED"
                and predicted["icon_id"] == expected["icon_id"]
            ):
                accumulator["icon_correct"] += 1
            else:
                reasons.append(
                    {
                        "code": "icon_mismatch",
                        "slot": slot,
                        "expected_icon_id": expected["icon_id"],
                        "predicted_icon_id": predicted["icon_id"],
                        "predicted_icon_state": predicted["icon_state"],
                    }
                )

        if is_unknown:
            accumulator["unknown_count"] += 1
            all_three_correct = False
            reasons.append(
                {
                    "code": "unknown",
                    "slot": slot,
                    "expected_augment_id": expected["augment_id"],
                }
            )
            continue

        augment_correct = predicted["augment_id"] == expected["augment_id"]
        if augment_correct:
            accumulator["card_correct"] += 1
        else:
            accumulator["false_match_count"] += 1
            all_three_correct = False
            reasons.append(
                {
                    "code": "false_match",
                    "slot": slot,
                    "expected_augment_id": expected["augment_id"],
                    "predicted_augment_id": predicted["augment_id"],
                }
            )

    if all_three_valid and all_three_correct:
        accumulator["three_cards_correct"] += 1
    if collect_failures and reasons:
        accumulator["failures"].append(
            {"sample_id": sample["sample_id"], "reasons": reasons}
        )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _finalize_metrics(accumulator: dict[str, Any]) -> dict[str, Any]:
    latencies: list[float] = accumulator["latencies"]
    latency_count = len(latencies)
    return {
        "sample_count": accumulator["sample_count"],
        "card_count": accumulator["card_count"],
        "three_card_sample_count": accumulator["three_card_sample_count"],
        "screen_detection_accuracy": _ratio(
            accumulator["screen_correct"], accumulator["screen_denominator"]
        ),
        "ocr_accuracy": _ratio(
            accumulator["ocr_correct"], accumulator["ocr_denominator"]
        ),
        "icon_accuracy": _ratio(
            accumulator["icon_correct"], accumulator["icon_denominator"]
        ),
        "card_recognition_accuracy": _ratio(
            accumulator["card_correct"], accumulator["card_denominator"]
        ),
        "three_cards_all_correct_rate": _ratio(
            accumulator["three_cards_correct"],
            accumulator["three_cards_denominator"],
        ),
        "unknown_rate": _ratio(
            accumulator["unknown_count"], accumulator["card_denominator"]
        ),
        "false_match_rate": _ratio(
            accumulator["false_match_count"], accumulator["card_denominator"]
        ),
        "average_latency_ms": sum(latencies) / latency_count if latency_count else None,
        "p95_latency_ms": _nearest_rank_percentile(latencies, 0.95),
        "metric_counts": {
            "screen_detection_accuracy": {
                "numerator": accumulator["screen_correct"],
                "denominator": accumulator["screen_denominator"],
            },
            "ocr_accuracy": {
                "numerator": accumulator["ocr_correct"],
                "denominator": accumulator["ocr_denominator"],
            },
            "icon_accuracy": {
                "numerator": accumulator["icon_correct"],
                "denominator": accumulator["icon_denominator"],
            },
            "card_recognition_accuracy": {
                "numerator": accumulator["card_correct"],
                "denominator": accumulator["card_denominator"],
            },
            "three_cards_all_correct_rate": {
                "numerator": accumulator["three_cards_correct"],
                "denominator": accumulator["three_cards_denominator"],
            },
            "unknown_rate": {
                "numerator": accumulator["unknown_count"],
                "denominator": accumulator["card_denominator"],
            },
            "false_match_rate": {
                "numerator": accumulator["false_match_count"],
                "denominator": accumulator["card_denominator"],
            },
            "average_latency_ms": {"denominator": latency_count},
            "p95_latency_ms": {"denominator": latency_count},
        },
    }


def _resolution_key(sample: dict[str, Any]) -> str:
    resolution = sample["metadata"]["resolution"]
    return f"{resolution['width']}x{resolution['height']}"


def _ui_scale_key(sample: dict[str, Any]) -> str:
    value = sample["metadata"]["ui_scale"]
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _variant_key(sample: dict[str, Any]) -> str:
    variant = sample["metadata"]["ocr_preprocessing_variant"]
    fields = [
        f"grayscale={str(variant['grayscale']).lower()}",
        f"contrast={variant['contrast'] if variant['contrast'] is not None else 'none'}",
        f"threshold={variant['threshold'] if variant['threshold'] is not None else 'none'}",
        f"scale={variant['scale']}",
    ]
    if "variant_id" in variant:
        fields.append(f"variant_id={variant['variant_id']}")
    return ";".join(fields)


def _build_strata(
    samples: list[dict[str, Any]], dimension: str
) -> list[dict[str, Any]]:
    if dimension == "resolution":
        key_function = _resolution_key
    elif dimension == "ui_scale":
        key_function = _ui_scale_key
    elif dimension == "ocr_preprocessing_variant":
        key_function = _variant_key
    else:
        raise ValueError(f"unsupported stratum dimension {dimension!r}")

    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        groups[key_function(sample)].append(sample)

    rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        accumulator = _new_accumulator()
        for sample in groups[key]:
            _add_sample(accumulator, sample, collect_failures=False)
        row: dict[str, Any] = {"key": key, **_finalize_metrics(accumulator)}
        if dimension == "ocr_preprocessing_variant":
            row["variant"] = groups[key][0]["metadata"]["ocr_preprocessing_variant"]
        rows.append(row)
    return rows


DENOMINATOR_SEMANTICS = {
    "sample_count": (
        "All schema-valid original-WGC benchmark-eligible real samples; preview-derived, other-real, and synthetic samples are excluded."
    ),
    "card_count": (
        "All valid=true card annotations on selected original-WGC samples; invalid cards are excluded."
    ),
    "screen_detection_accuracy": (
        "Correct screen_present versus screen_detected decisions / all selected original-WGC samples."
    ),
    "ocr_accuracy": (
        "NFKC+casefold OCR title matches after removing Unicode whitespace and punctuation / valid annotations whose OCR stage is not UNAVAILABLE. OCR UNKNOWN is incorrect; OCR UNAVAILABLE is excluded."
    ),
    "icon_accuracy": (
        "Exact MATCHED icon_id matches / valid annotations whose icon stage is not UNAVAILABLE. Icon UNKNOWN is incorrect; icon UNAVAILABLE is excluded."
    ),
    "card_recognition_accuracy": (
        "Exact augment_id matches / valid card annotations. UNKNOWN is incorrect."
    ),
    "three_cards_all_correct_rate": (
        "Samples with all three augment_id predictions correct / samples where LEFT, CENTER, and RIGHT annotations are all valid."
    ),
    "unknown_rate": (
        "Missing or explicit UNKNOWN card predictions / valid card annotations; UNKNOWN is not correct."
    ),
    "false_match_rate": (
        "Incorrect non-UNKNOWN augment_id predictions / valid card annotations."
    ),
    "average_latency_ms": "Arithmetic mean over selected original-WGC sample latency_ms values.",
    "p95_latency_ms": (
        "Nearest-rank P95 over selected original-WGC sample latency_ms values: sorted[ceil(0.95*n)-1]."
    ),
}


def build_report(
    loaded: dict[str, Any],
    source: str | Path,
    minimum_real_samples: int = 1,
    max_failures: int = 20,
) -> dict[str, Any]:
    """Build a deterministic, JSON-safe report from a validated dataset."""

    if minimum_real_samples < 1:
        raise ValueError("minimum_real_samples must be at least 1")
    if max_failures < 0:
        raise ValueError("max_failures must be non-negative")

    all_samples: list[dict[str, Any]] = loaded["samples"]
    real_input_samples = [
        sample for sample in all_samples if sample["metadata"]["provenance"] == "real"
    ]
    real_samples = [
        sample
        for sample in real_input_samples
        if sample["metadata"]["benchmark_eligible"]
    ]
    synthetic_count = sum(
        sample["metadata"]["provenance"] == "synthetic" for sample in all_samples
    )
    preview_derived_count = sum(
        sample["metadata"]["capture_boundary"] == "preview_derived"
        for sample in real_input_samples
    )
    other_excluded_real_count = (
        len(real_input_samples) - len(real_samples) - preview_derived_count
    )
    boundary_warnings = [
        {"sample_id": sample["sample_id"], **warning}
        for sample in all_samples
        for warning in sample["metadata"]["boundary_warnings"]
    ]

    accumulator = _new_accumulator()
    for sample in real_samples:
        _add_sample(accumulator, sample, collect_failures=True)
    metrics = _finalize_metrics(accumulator)

    status_reasons: list[str] = []
    if metrics["three_card_sample_count"] < minimum_real_samples:
        status_reasons.append(
            "three_card_real_sample_count_below_minimum:"
            f"{metrics['three_card_sample_count']}<{minimum_real_samples}"
        )
    if metrics["three_card_sample_count"] == 0:
        status_reasons.append("no_real_sample_has_three_valid_card_annotations")
    if loaded["source_state"] in {"missing", "no_benchmark_records"}:
        status_reasons.append(f"input_source_state:{loaded['source_state']}")
    status = "insufficient_real_data" if status_reasons else "ok"

    resolution_distribution: dict[str, int] = {}
    ui_scale_distribution: dict[str, int] = {}
    for sample in real_samples:
        resolution_key = _resolution_key(sample)
        ui_scale_key = _ui_scale_key(sample)
        resolution_distribution[resolution_key] = (
            resolution_distribution.get(resolution_key, 0) + 1
        )
        ui_scale_distribution[ui_scale_key] = ui_scale_distribution.get(ui_scale_key, 0) + 1

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "status_reasons": status_reasons,
        "scope": {
            "provenance": "real",
            "capture_boundary": "original_wgc",
            "benchmark_use": "original_wgc_metrics",
            "synthetic_included": False,
            "preview_derived_included": False,
            "legacy_unspecified_real_included_with_warning": True,
            "minimum_three_card_real_samples": minimum_real_samples,
        },
        "input": {
            "source": str(source),
            "source_state": loaded["source_state"],
            "raw_asset_count": loaded["raw_asset_count"],
            "input_sample_count": len(all_samples),
            "excluded_synthetic_sample_count": synthetic_count,
            "excluded_preview_derived_sample_count": preview_derived_count,
            "excluded_other_real_sample_count": other_excluded_real_count,
            "benchmark_eligible_original_wgc_sample_count": len(real_samples),
            "boundary_warning_count": len(boundary_warnings),
        },
        "boundary_warnings": boundary_warnings,
        "ocr_normalization": {
            "unicode_form": "NFKC",
            "casefold": True,
            "remove_unicode_whitespace": True,
            "remove_unicode_punctuation_categories": "P*",
            "fuzzy_matching": False,
        },
        **metrics,
        "resolution_distribution": dict(sorted(resolution_distribution.items())),
        "ui_scale_distribution": dict(sorted(ui_scale_distribution.items())),
        "stratified": {
            "resolution": _build_strata(real_samples, "resolution"),
            "ui_scale": _build_strata(real_samples, "ui_scale"),
            "ocr_preprocessing_variant": _build_strata(
                real_samples, "ocr_preprocessing_variant"
            ),
        },
        "typical_failures": accumulator["failures"][:max_failures],
        "failure_sample_count": len(accumulator["failures"]),
        "failure_samples_truncated": len(accumulator["failures"]) > max_failures,
        "denominator_semantics": DENOMINATOR_SEMANTICS,
        "latency_percentile_method": "nearest_rank",
        "notes": [
            "Strata are descriptive comparisons only; this benchmark does not select OCR preprocessing parameters.",
            "Unavailable rates and latency values are null rather than zero.",
        ],
    }


RATE_METRICS = (
    "screen_detection_accuracy",
    "ocr_accuracy",
    "icon_accuracy",
    "card_recognition_accuracy",
    "three_cards_all_correct_rate",
    "unknown_rate",
    "false_match_rate",
)
LATENCY_METRICS = ("average_latency_ms", "p95_latency_ms")


def _format_rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.6f} ({value * 100:.2f}%)"


def _format_latency(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _distribution_markdown(distribution: dict[str, int]) -> list[str]:
    if not distribution:
        return ["No selected real samples."]
    lines = ["| Value | Samples |", "|---|---:|"]
    lines.extend(f"| `{key}` | {count} |" for key, count in distribution.items())
    return lines


def _stratum_markdown(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No selected real samples."]
    lines = [
        "| Stratum | Samples | Cards | Screen | OCR | Icon | Card | All 3 | Unknown | False match | Avg ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['key']}`",
                    str(row["sample_count"]),
                    str(row["card_count"]),
                    _format_rate(row["screen_detection_accuracy"]),
                    _format_rate(row["ocr_accuracy"]),
                    _format_rate(row["icon_accuracy"]),
                    _format_rate(row["card_recognition_accuracy"]),
                    _format_rate(row["three_cards_all_correct_rate"]),
                    _format_rate(row["unknown_rate"]),
                    _format_rate(row["false_match_rate"]),
                    _format_latency(row["average_latency_ms"]),
                    _format_latency(row["p95_latency_ms"]),
                ]
            )
            + " |"
        )
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    """Render a human-readable report without changing metric semantics."""

    lines = [
        "# Phase 2 Real Dataset Benchmark",
        "",
        f"- Status: `{report['status']}`",
        f"- Input source: `{report['input']['source']}`",
        f"- Input state: `{report['input']['source_state']}`",
        "- Evaluation scope: `provenance=real`, `capture_boundary=original_wgc`, `benchmark_use=original_wgc_metrics`.",
        "- Preview-derived and other non-original-WGC real samples are excluded from every metric.",
        f"- Raw assets observed: {report['input']['raw_asset_count']}",
        f"- Schema-valid input samples: {report['input']['input_sample_count']}",
        f"- Excluded synthetic samples: {report['input']['excluded_synthetic_sample_count']}",
        f"- Excluded preview-derived real samples: {report['input']['excluded_preview_derived_sample_count']}",
        f"- Excluded other real samples: {report['input']['excluded_other_real_sample_count']}",
        f"- Boundary warnings: {report['input']['boundary_warning_count']}",
        "",
        "## Counts",
        "",
        f"- Benchmark-eligible original-WGC samples: {report['sample_count']}",
        f"- Valid annotated cards: {report['card_count']}",
        f"- Real samples with all three card annotations valid: {report['three_card_sample_count']}",
        "",
        "## Metrics",
        "",
        "| Metric | Value | Numerator | Denominator |",
        "|---|---:|---:|---:|",
    ]
    for metric in RATE_METRICS:
        counts = report["metric_counts"][metric]
        lines.append(
            f"| `{metric}` | {_format_rate(report[metric])} | "
            f"{counts['numerator']} | {counts['denominator']} |"
        )
    for metric in LATENCY_METRICS:
        counts = report["metric_counts"][metric]
        lines.append(
            f"| `{metric}` | {_format_latency(report[metric])} | — | "
            f"{counts['denominator']} samples |"
        )

    lines.extend(["", "## Resolution Distribution", ""])
    lines.extend(_distribution_markdown(report["resolution_distribution"]))
    lines.extend(["", "## UI Scale Distribution", ""])
    lines.extend(_distribution_markdown(report["ui_scale_distribution"]))

    strata_titles = (
        ("resolution", "Resolution"),
        ("ui_scale", "UI Scale"),
        ("ocr_preprocessing_variant", "OCR Preprocessing Variant"),
    )
    for key, title in strata_titles:
        lines.extend(["", f"## Stratified by {title}", ""])
        lines.extend(_stratum_markdown(report["stratified"][key]))

    lines.extend(["", "## Typical Failures", ""])
    if not report["typical_failures"]:
        lines.append("No model failures in the evaluated real samples, or no evaluable real samples.")
    else:
        for failure in report["typical_failures"]:
            rendered_reasons = "; ".join(
                json.dumps(reason, ensure_ascii=False, sort_keys=True, allow_nan=False)
                for reason in failure["reasons"]
            )
            lines.append(f"- `{failure['sample_id']}`: {rendered_reasons}")
        if report["failure_samples_truncated"]:
            lines.append(
                f"- Failure list truncated; total failing samples: {report['failure_sample_count']}."
            )

    lines.extend(["", "## Denominator Semantics", ""])
    for metric, semantics in report["denominator_semantics"].items():
        lines.append(f"- `{metric}`: {semantics}")

    lines.extend(["", "## Capture Boundary Warnings", ""])
    if report["boundary_warnings"]:
        for warning in report["boundary_warnings"]:
            lines.append(
                f"- `{warning['sample_id']}` / `{warning['code']}`: {warning['message']}"
            )
    else:
        lines.append("No capture-boundary warnings.")

    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "- Rates with a zero denominator are `N/A` in Markdown and `null` in JSON; zero is never fabricated.",
            "- P95 uses nearest rank: `sorted[ceil(0.95*n)-1]`.",
            "- OCR preprocessing strata are descriptive only. The benchmark does not select grayscale, contrast, threshold, 2x, or 3x parameters for the caller.",
        ]
    )
    if report["status_reasons"]:
        lines.extend(["", "## Status Reasons", ""])
        lines.extend(f"- `{reason}`" for reason in report["status_reasons"])
    return "\n".join(lines) + "\n"


def _strict_json_text(report: dict[str, Any]) -> str:
    return (
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _write_output(destination: str, text: str) -> None:
    if destination == "-":
        sys.stdout.write(text)
        return
    _atomic_write_text(Path(destination), text)


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Phase 2 dataset records. Only clear original-WGC real samples "
            "enter metrics; preview-derived and synthetic records are excluded."
        )
    )
    parser.add_argument(
        "--dataset",
        "--input",
        required=True,
        help=(
            "JSON bundle, split-JSONL directory, empty directory, or missing path. "
            "Empty/missing input succeeds with status=insufficient_real_data."
        ),
    )
    parser.add_argument(
        "--json-output",
        "--json-out",
        required=True,
        help="Caller-selected JSON report path, or '-' for stdout.",
    )
    parser.add_argument(
        "--markdown-output",
        "--markdown-out",
        required=True,
        help="Caller-selected Markdown report path, or '-' for stdout.",
    )
    parser.add_argument(
        "--minimum-real-samples",
        "--minimum-three-card-samples",
        type=_positive_integer,
        default=1,
        help=(
            "Minimum real sample count with all three card annotations valid "
            "for status=ok (default: 1)."
        ),
    )
    parser.add_argument(
        "--max-failures",
        type=_non_negative_integer,
        default=20,
        help="Maximum typical failure samples included in reports (default: 20).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    if arguments.json_output == "-" and arguments.markdown_output == "-":
        print("error: JSON and Markdown cannot both target stdout", file=sys.stderr)
        return 2
    if (
        arguments.json_output != "-"
        and arguments.markdown_output != "-"
        and Path(arguments.json_output).resolve() == Path(arguments.markdown_output).resolve()
    ):
        print("error: JSON and Markdown output paths must differ", file=sys.stderr)
        return 2

    try:
        loaded = load_dataset(arguments.dataset)
        report = build_report(
            loaded,
            arguments.dataset,
            minimum_real_samples=arguments.minimum_real_samples,
            max_failures=arguments.max_failures,
        )
        json_text = _strict_json_text(report)
        markdown_text = render_markdown(report)
        _write_output(arguments.json_output, json_text)
        _write_output(arguments.markdown_output, markdown_text)
    except (OSError, SchemaError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
