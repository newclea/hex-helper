#!/usr/bin/env python3
"""Produce auditable Phase2 Dataset -> Replay -> Benchmark results.

The product process is the only prediction source. Dataset annotations are read
only when building the benchmark ground-truth stream; they are never passed to
the replay parser or prediction builder.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_ROOT = REPO_ROOT / "data" / "dataset" / "augment_offers"
DEFAULT_KNOWLEDGE = REPO_ROOT / "data" / "knowledge" / "augments.zh-CN.json"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "phase2" / "validate_dataset.py"
BENCHMARK_PATH = REPO_ROOT / "scripts" / "benchmark_phase2.py"
PREDICTION_SCHEMA = "phase2_replay_prediction_v1"
PRODUCER_REPORT_SCHEMA = "phase2_replay_benchmark_producer_report_v1"
SLOTS = ("LEFT", "CENTER", "RIGHT")
SUCCESS_SESSION_STATUSES = {
    "completed",
    "completed_unknown",
    "completed_no_stable_observation",
}


class ProducerError(ValueError):
    """Raised for invalid producer input or strict Replay protocol output."""


@dataclass(frozen=True)
class Sample:
    sample_id: str
    provenance: str
    directory: Path
    raw_path: Path
    metadata: dict[str, Any]
    annotation: dict[str, Any]
    benchmark_eligible: bool
    source_kind: str | None
    capture_boundary: str | None
    benchmark_use: str | None


@dataclass(frozen=True)
class ProcessCapture:
    returncode: int | None
    stdout: str
    stderr: str
    wall_latency_ms: float
    timed_out: bool
    decode_error: str | None = None
    launch_error: str | None = None


def _load_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ProducerError(f"cannot load Python module: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
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
        raise ProducerError(f"{location}: invalid strict JSON: {error}") from error


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = _loads_strict_json(
            path.read_text(encoding="utf-8", errors="strict"), str(path)
        )
    except (OSError, UnicodeError) as error:
        raise ProducerError(f"cannot read UTF-8 JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ProducerError(f"{path}: JSON root must be an object")
    return value


def _strict_json_text(value: Any, *, pretty: bool = True) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
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


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, _strict_json_text(value))


def _atomic_write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    text = "".join(_strict_json_text(record, pretty=False) for record in records)
    _atomic_write_text(path, text)


def _finite_number(value: Any, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProducerError(f"{location}: must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ProducerError(f"{location}: must be a finite number")
    return number


def _non_negative_number(value: Any, location: str) -> float:
    number = _finite_number(value, location)
    if number < 0:
        raise ProducerError(f"{location}: must be non-negative")
    return number


def _required_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProducerError(f"{location}: must be an object")
    return value


def _required_bool(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise ProducerError(f"{location}: must be a boolean")
    return value


def _required_text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProducerError(f"{location}: must be a non-empty string")
    return value


def _optional_text(value: Any, location: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, location)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ProducerError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest().upper()


def _path_equal(left: Path, right: Path) -> bool:
    left_text = str(left.resolve(strict=False))
    right_text = str(right.resolve(strict=False))
    if os.name == "nt":
        return os.path.normcase(left_text) == os.path.normcase(right_text)
    return left_text == right_text


def _is_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _png_dimensions(path: Path) -> tuple[int, int]:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
    except OSError as error:
        raise ProducerError(f"cannot read RAW image {path}: {error}") from error
    if (
        len(header) != 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        raise ProducerError(f"{path}: RAW.png has no valid PNG IHDR")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ProducerError(f"{path}: RAW.png dimensions must be positive")
    return width, height


def _eligible_samples(
    dataset_root: Path, knowledge: Path
) -> tuple[list[Sample], dict[str, Any]]:
    validator = _load_module("phase2_dataset_validator_for_producer", VALIDATOR_PATH)
    validation = validator.validate_dataset(dataset_root, knowledge)

    global_issues = [
        issue for issue in validation.get("issues", []) if "sample_id" not in issue
    ]
    if global_issues:
        rendered = "; ".join(
            f"{issue.get('code', 'dataset_error')}: {issue.get('message', '')}"
            for issue in global_issues
        )
        raise ProducerError(f"dataset validation has global errors: {rendered}")

    samples: list[Sample] = []
    for record in validation.get("samples", []):
        relative = Path(record["path"])
        directory = (dataset_root / relative).resolve(strict=True)
        if not _is_inside(dataset_root, directory):
            raise ProducerError(f"sample path escapes dataset root: {directory}")

        effectively_valid = bool(record.get("valid"))
        if not effectively_valid and record.get("issues"):
            # The canonical dataset validator predates recognition.json and the
            # requested scope forbids changing it. Permit only this producer's
            # own, schema-tagged result so the default in-place mode is rerunnable.
            issues = record["issues"]
            only_owned_result_issue = all(
                issue.get("code") == "unexpected_sample_entry"
                and Path(str(issue.get("path", ""))).name == "recognition.json"
                for issue in issues
            )
            if only_owned_result_issue:
                try:
                    existing = _load_json(directory / "recognition.json")
                    effectively_valid = bool(
                        existing.get("schema_version") == PREDICTION_SCHEMA
                        and existing.get("sample_id") == record.get("sample_id")
                        and existing.get("provenance") == record.get("provenance")
                        and isinstance(existing.get("benchmark_recognition_result"), dict)
                    )
                except ProducerError:
                    effectively_valid = False

        if not effectively_valid or record.get("annotation_status") != "complete":
            continue
        provenance = record.get("provenance")
        # The unchanged benchmark core accepts real and synthetic only. Unknown
        # provenance remains explicitly excluded rather than relabelled.
        if provenance not in {"real", "synthetic"}:
            continue
        benchmark_eligible = bool(record.get("benchmark_eligible"))
        if (
            provenance == "real"
            and effectively_valid
            and record.get("source_kind") == "windows_graphics_capture"
            and record.get("derived_from_preview") is not True
            and record.get("capture_boundary") == "original_wgc"
            and record.get("benchmark_use") == "original_wgc_metrics"
        ):
            # Preserve eligibility on reruns where this producer's own
            # recognition.json is the sole validator issue.
            benchmark_eligible = True
        raw_path = (directory / "RAW.png").resolve(strict=True)
        samples.append(
            Sample(
                sample_id=record["sample_id"],
                provenance=provenance,
                directory=directory,
                raw_path=raw_path,
                metadata=_load_json(directory / "metadata.json"),
                annotation=_load_json(directory / "annotation.json"),
                benchmark_eligible=benchmark_eligible,
                source_kind=record.get("source_kind"),
                capture_boundary=record.get("capture_boundary"),
                benchmark_use=record.get("benchmark_use"),
            )
        )
    samples.sort(key=lambda sample: (sample.sample_id, sample.provenance))
    return samples, validation


def _decode_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _executable_prefix(executable: Path) -> list[str]:
    # Python fake executables are supported for hermetic contract tests. Product
    # execution uses the supplied native .exe directly.
    if executable.suffix.lower() == ".py":
        return [sys.executable, "-B", str(executable)]
    return [str(executable)]


def _run_replay_process(
    executable: Path,
    raw_path: Path,
    knowledge: Path,
    workspace: Path,
    timeout_seconds: float,
) -> tuple[ProcessCapture, list[str]]:
    command = [
        *_executable_prefix(executable),
        "--replay",
        str(raw_path),
        "--mode",
        "KIWI",
        "--knowledge",
        str(knowledge),
        "--workspace",
        str(workspace),
        "--max-seconds",
        format(timeout_seconds, ".6g"),
        "--once",
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
            check=False,
            creationflags=creationflags,
        )
        elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
        capture = ProcessCapture(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            elapsed,
            False,
        )
    except subprocess.TimeoutExpired as error:
        elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
        capture = ProcessCapture(
            None,
            _decode_timeout_stream(error.stdout),
            _decode_timeout_stream(error.stderr),
            elapsed,
            True,
        )
    except UnicodeDecodeError as error:
        elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
        capture = ProcessCapture(None, "", "", elapsed, False, str(error))
    except OSError as error:
        elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
        capture = ProcessCapture(
            None, "", "", elapsed, False, launch_error=str(error)
        )
    return capture, command


def _validate_detector(value: Any, location: str) -> dict[str, Any]:
    detector = _required_object(value, location)
    visible = _required_bool(detector.get("visible"), f"{location}.visible")
    confidence = _finite_number(
        detector.get("confidence"), f"{location}.confidence"
    )
    if not 0.0 <= confidence <= 1.0:
        raise ProducerError(f"{location}.confidence: must be in [0, 1]")
    reason = _required_text(detector.get("reason"), f"{location}.reason")
    frame_id = detector.get("frame_id")
    if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
        raise ProducerError(f"{location}.frame_id: must be a non-negative integer")
    return {
        "frame_id": frame_id,
        "visible": visible,
        "confidence": confidence,
        "reason": reason,
    }


def _validate_frame_result(event: dict[str, Any], location: str) -> dict[str, Any]:
    if event.get("type") != "frame_result":
        raise ProducerError(f"{location}.type: must be frame_result")
    return {
        "raw_detector": _validate_detector(
            event.get("raw_detector"), f"{location}.raw_detector"
        ),
        "stable_detector": _validate_detector(
            event.get("stable_detector"), f"{location}.stable_detector"
        ),
        "ocr_executed": _required_bool(
            event.get("ocr_executed"), f"{location}.ocr_executed"
        ),
        "accepted": _required_bool(event.get("accepted"), f"{location}.accepted"),
        "reason": _required_text(event.get("reason"), f"{location}.reason"),
        "latency_ms": (
            _non_negative_number(event["latency_ms"], f"{location}.latency_ms")
            if "latency_ms" in event
            else None
        ),
    }


def _validate_session_start(
    event: dict[str, Any], location: str, knowledge: Path, workspace: Path
) -> dict[str, Any]:
    manual = _required_object(event.get("manual"), f"{location}.manual")
    paths = _required_object(event.get("paths"), f"{location}.paths")
    mode = _required_text(manual.get("mode"), f"{location}.manual.mode")
    if mode != "KIWI":
        raise ProducerError(f"{location}.manual.mode: expected KIWI, got {mode!r}")
    preview = _required_bool(
        event.get("preview_requested"), f"{location}.preview_requested"
    )
    if preview:
        raise ProducerError(f"{location}: Replay unexpectedly requested a preview")
    actual_knowledge = Path(
        _required_text(paths.get("knowledge"), f"{location}.paths.knowledge")
    )
    actual_workspace = Path(
        _required_text(paths.get("workspace"), f"{location}.paths.workspace")
    )
    if not actual_knowledge.is_absolute() or not _path_equal(actual_knowledge, knowledge):
        raise ProducerError(f"{location}: product did not report the absolute knowledge path")
    if not actual_workspace.is_absolute() or not _path_equal(actual_workspace, workspace):
        raise ProducerError(f"{location}: product did not report the temporary workspace path")
    session_id = _required_text(event.get("session_id"), f"{location}.session_id")
    database = Path(_required_text(event.get("database"), f"{location}.database"))
    if not database.is_absolute():
        raise ProducerError(f"{location}.database: must be an absolute path")
    try:
        database = database.resolve(strict=True)
    except OSError as error:
        raise ProducerError(f"{location}.database: cannot resolve database: {error}") from error
    if not database.is_file() or not _is_inside(workspace, database):
        raise ProducerError(
            f"{location}.database: must be a file inside the temporary workspace"
        )
    if "static_replay" in event and event["static_replay"] is not True:
        raise ProducerError(f"{location}.static_replay: must be true")
    return {
        "mode": mode,
        "preview_requested": preview,
        "session_id": session_id,
        "database": str(database),
    }


def _normalize_state(value: Any, location: str, *, unavailable: bool) -> str:
    if value is None and unavailable:
        return "UNAVAILABLE"
    state = _required_text(value, location).upper()
    if state == "NOT_RECOGNIZED":
        state = "UNKNOWN"
    allowed = {"RECOGNIZED", "UNKNOWN"}
    if unavailable:
        allowed.add("UNAVAILABLE")
    if state not in allowed:
        raise ProducerError(f"{location}: unsupported state {state!r}")
    return state


def _stage(
    raw: Any,
    location: str,
    *,
    value_keys: Sequence[str],
    value_name: str,
) -> dict[str, Any]:
    if raw is None:
        return {"state": "UNAVAILABLE", value_name: None}
    stage = _required_object(raw, location)
    state = _normalize_state(stage.get("state"), f"{location}.state", unavailable=True)
    found: Any = None
    for key in value_keys:
        if key in stage:
            found = stage[key]
            break
    if state == "RECOGNIZED":
        value = _required_text(found, f"{location}.{value_name}")
    else:
        if found is not None:
            raise ProducerError(
                f"{location}.{value_name}: must be null for {state} stage"
            )
        value = None
    return {"state": state, value_name: value}


def _final_stage(raw: Any, location: str) -> dict[str, Any]:
    if raw is None:
        return {"state": "UNKNOWN", "augment_id": None, "display_name": None}
    final = _required_object(raw, location)
    state = _normalize_state(final.get("state"), f"{location}.state", unavailable=False)
    augment_id = final.get("augment_id")
    display_name = final.get("display_name")
    if state == "RECOGNIZED":
        augment_id = _required_text(augment_id, f"{location}.augment_id")
        if display_name is not None:
            display_name = _required_text(display_name, f"{location}.display_name")
    else:
        if augment_id is not None or display_name is not None:
            raise ProducerError(
                f"{location}: UNKNOWN final must not contain augment_id/display_name"
            )
        augment_id = None
        display_name = None
    return {
        "state": state,
        "augment_id": augment_id,
        "display_name": display_name,
    }


def _normalize_card(raw: Any, slot: str, location: str) -> dict[str, Any]:
    if raw is None:
        return {
            "slot": slot,
            "ocr": {"state": "UNAVAILABLE", "text": None},
            "icon": {"state": "UNAVAILABLE", "augment_id": None},
            "final": {"state": "UNKNOWN", "augment_id": None, "display_name": None},
        }
    card = _required_object(raw, location)
    actual_slot = _required_text(card.get("slot"), f"{location}.slot").upper()
    if actual_slot != slot:
        raise ProducerError(f"{location}.slot: expected {slot}, got {actual_slot}")

    ocr_raw = card.get("ocr")
    if ocr_raw is None and "ocr_text" in card:
        ocr_raw = {
            "state": "RECOGNIZED" if card["ocr_text"] is not None else "UNKNOWN",
            "text": card["ocr_text"],
        }
    icon_raw = card.get("icon")
    if icon_raw is None and "icon_id" in card:
        icon_raw = {
            "state": "RECOGNIZED" if card["icon_id"] is not None else "UNKNOWN",
            "augment_id": card["icon_id"],
        }
    final_raw = card.get("final")
    if final_raw is None and ("state" in card or "augment_id" in card):
        final_raw = {
            "state": card.get("state"),
            "augment_id": card.get("augment_id"),
            "display_name": card.get("display_name"),
        }
    return {
        "slot": slot,
        "ocr": _stage(
            ocr_raw,
            f"{location}.ocr",
            value_keys=("text", "raw_text", "ocr_text"),
            value_name="text",
        ),
        "icon": _stage(
            icon_raw,
            f"{location}.icon",
            value_keys=("augment_id", "icon_id"),
            value_name="augment_id",
        ),
        "final": _final_stage(final_raw, f"{location}.final"),
    }


def _offer_payload(event: dict[str, Any], location: str) -> dict[str, Any]:
    if event.get("type") == "offer" and "offer" in event:
        return _required_object(event["offer"], f"{location}.offer")
    return event


def _validate_offer(event: dict[str, Any], location: str) -> dict[str, Any]:
    payload = _offer_payload(event, location)
    if "current_offer" in payload:
        payload = _required_object(payload["current_offer"], f"{location}.current_offer")
    detection = payload.get("screen_detection")
    detected: bool | None = None
    if detection is not None:
        detection_object = _required_object(detection, f"{location}.screen_detection")
        state = _required_text(
            detection_object.get("state"), f"{location}.screen_detection.state"
        ).upper()
        if state not in {"DETECTED", "NOT_DETECTED", "UNKNOWN"}:
            raise ProducerError(
                f"{location}.screen_detection.state: unsupported state {state!r}"
            )
        detected = state == "DETECTED"

    raw_cards = payload.get("cards")
    if raw_cards is None:
        raw_cards = payload.get("recognitions")
    if not isinstance(raw_cards, list) or len(raw_cards) != len(SLOTS):
        raise ProducerError(f"{location}: offer must contain exactly three cards")

    cards: list[dict[str, Any]] = []
    for index, (slot, raw_card) in enumerate(zip(SLOTS, raw_cards)):
        # Legacy product recognitions may use null placeholders. Typed cards and
        # current product recognitions both carry an explicit slot otherwise.
        cards.append(_normalize_card(raw_card, slot, f"{location}.cards[{index}]"))
    latency = event.get("latency_ms", payload.get("latency_ms"))
    return {
        "screen_detected": detected,
        "cards": cards,
        "latency_ms": (
            _non_negative_number(latency, f"{location}.latency_ms")
            if latency is not None
            else None
        ),
    }


def _validate_session_end(event: dict[str, Any], location: str) -> dict[str, Any]:
    status = _required_text(event.get("status"), f"{location}.status")
    close_ok = _required_bool(event.get("close_ok"), f"{location}.close_ok")
    frames_processed = event.get("frames_processed")
    if (
        isinstance(frames_processed, bool)
        or not isinstance(frames_processed, int)
        or frames_processed < 0
    ):
        raise ProducerError(
            f"{location}.frames_processed: must be a non-negative integer"
        )
    _required_object(event.get("source_summary"), f"{location}.source_summary")
    return {
        "status": status,
        "close_ok": close_ok,
        "frames_processed": frames_processed,
    }


def _looks_like_legacy_offer(event: dict[str, Any]) -> bool:
    return {
        "champion",
        "offer_round",
        "selected_augments",
        "current_offer",
        "metadata",
    }.issubset(event)


def _parse_replay_jsonl(
    stdout: str, knowledge: Path, workspace: Path
) -> dict[str, Any]:
    starts: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    ends: list[dict[str, Any]] = []
    nonblank_lines = 0
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        nonblank_lines += 1
        location = f"stdout:{line_number}"
        event = _loads_strict_json(line, location)
        if not isinstance(event, dict):
            raise ProducerError(f"{location}: each JSONL line must be an object")
        event_type = event.get("type")
        if event_type == "session_start":
            starts.append(_validate_session_start(event, location, knowledge, workspace))
        elif event_type == "frame_result":
            frames.append(_validate_frame_result(event, location))
        elif event_type == "offer" or (
            event_type is None and _looks_like_legacy_offer(event)
        ):
            offers.append(_validate_offer(event, location))
        elif event_type == "session_end":
            ends.append(_validate_session_end(event, location))
        else:
            raise ProducerError(f"{location}: unknown Replay JSONL event {event_type!r}")

    if len(starts) != 1:
        raise ProducerError(f"Replay JSONL must contain one session_start; got {len(starts)}")
    if not frames:
        raise ProducerError("Replay JSONL must contain at least one frame_result")
    if len(offers) > 1:
        raise ProducerError(f"Replay JSONL must contain at most one offer; got {len(offers)}")
    if len(ends) != 1:
        raise ProducerError(f"Replay JSONL must contain one session_end; got {len(ends)}")

    accepted = [frame for frame in frames if frame["accepted"]]
    if bool(accepted) != bool(offers):
        raise ProducerError("accepted frame_result and offer presence disagree")
    latest = accepted[-1] if accepted else frames[-1]
    offer = offers[0] if offers else None
    if offer is not None and offer["screen_detected"] is False:
        raise ProducerError("accepted offer reports screen_detection=NOT_DETECTED")
    if offer is not None and not latest["stable_detector"]["visible"]:
        raise ProducerError("accepted offer has stable_detector.visible=false")

    session_end = ends[0]
    latency_candidates = [
        candidate
        for candidate in (
            offer.get("latency_ms") if offer else None,
            latest.get("latency_ms"),
        )
        if candidate is not None
    ]
    return {
        "line_count": nonblank_lines,
        "frame_result_count": len(frames),
        "offer_seen": offer is not None,
        "session_status": session_end["status"],
        "close_ok": session_end["close_ok"],
        "frames_processed": session_end["frames_processed"],
        "raw_detector": latest["raw_detector"],
        "stable_detector": latest["stable_detector"],
        "ocr_executed": latest["ocr_executed"],
        "cards": offer["cards"] if offer else None,
        "reported_latency_ms": latency_candidates[0] if latency_candidates else None,
        "session_id": starts[0]["session_id"],
        "database": starts[0]["database"],
    }


def _unit_optional_number(value: Any, location: str) -> float | None:
    if value is None:
        return None
    number = _finite_number(value, location)
    if not 0.0 <= number <= 1.0:
        raise ProducerError(f"{location}: must be in [0, 1]")
    return number


def _database_stage_cards(
    protocol: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read component evidence only from the product session database."""

    database = Path(protocol["database"])
    database_hash = _sha256_file(database)
    session_id = protocol["session_id"]
    try:
        connection = sqlite3.connect(
            f"{database.as_uri()}?mode=ro", uri=True, timeout=1.0
        )
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT slot, recognition_state, augment_id, display_name, "
                "confidence, raw_json FROM recognition_results "
                "WHERE session_id = ? ORDER BY slot",
                (session_id,),
            ).fetchall()
            other_session_rows = connection.execute(
                "SELECT COUNT(*) FROM recognition_results WHERE session_id <> ?",
                (session_id,),
            ).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise ProducerError(f"database recognition_results read failed: {error}") from error

    if other_session_rows:
        raise ProducerError(
            "database recognition_results contains rows for a different session_id"
        )
    stdout_cards = protocol["cards"]
    expected_count = len(SLOTS) if stdout_cards is not None else 0
    if len(rows) != expected_count:
        raise ProducerError(
            "database/stdout recognition count mismatch: "
            f"database={len(rows)}, stdout={expected_count}"
        )

    evidence = {
        "prediction_source": "session_start.database:recognition_results.raw_json",
        "database_path": str(database),
        "database_sha256": database_hash,
        "session_id": session_id,
        "recognition_row_count": len(rows),
    }
    if stdout_cards is None:
        return _unknown_cards(), evidence

    cards: list[dict[str, Any]] = []
    for index, (slot, row, stdout_card) in enumerate(zip(SLOTS, rows, stdout_cards)):
        location = f"recognition_results[{index}]"
        if row["slot"] != index + 1:
            raise ProducerError(
                f"{location}.slot: expected {index + 1} ({slot}), got {row['slot']!r}"
            )
        if row["recognition_state"] != 2:
            raise ProducerError(
                f"{location}.recognition_state: accepted offer row must be 2"
            )
        raw = _loads_strict_json(row["raw_json"], f"{location}.raw_json")
        raw = _required_object(raw, f"{location}.raw_json")
        required = {
            "state",
            "raw_text",
            "backend",
            "ocr_confidence",
            "match_confidence",
            "final_confidence",
            "augment_id",
            "display_name",
            "reason",
            "icon_match",
        }
        missing = sorted(required.difference(raw))
        if missing:
            raise ProducerError(
                f"{location}.raw_json: missing fields {', '.join(missing)}"
            )
        state = _required_text(raw["state"], f"{location}.raw_json.state").upper()
        if state != "RECOGNIZED":
            raise ProducerError(
                f"{location}.raw_json.state: accepted row must be RECOGNIZED"
            )
        raw_text = _required_text(raw["raw_text"], f"{location}.raw_json.raw_text")
        backend = _required_text(raw["backend"], f"{location}.raw_json.backend")
        augment_id = _required_text(
            raw["augment_id"], f"{location}.raw_json.augment_id"
        )
        display_name = _optional_text(
            raw["display_name"], f"{location}.raw_json.display_name"
        )
        reason = _required_text(raw["reason"], f"{location}.raw_json.reason")
        ocr_confidence = _unit_optional_number(
            raw["ocr_confidence"], f"{location}.raw_json.ocr_confidence"
        )
        match_confidence = _unit_optional_number(
            raw["match_confidence"], f"{location}.raw_json.match_confidence"
        )
        final_confidence = _unit_optional_number(
            raw["final_confidence"], f"{location}.raw_json.final_confidence"
        )
        row_confidence = _unit_optional_number(
            row["confidence"], f"{location}.confidence"
        )
        if (
            row["augment_id"] != augment_id
            or row["display_name"] != display_name
            or final_confidence is None
            or row_confidence is None
            or not math.isclose(row_confidence, final_confidence, abs_tol=1e-6)
        ):
            raise ProducerError(f"{location}: database columns and raw_json disagree")

        stdout_final = stdout_card["final"]
        if (
            stdout_final["state"] != "RECOGNIZED"
            or stdout_final["augment_id"] != augment_id
            or stdout_final["display_name"] != display_name
        ):
            raise ProducerError(f"{location}: database raw_json and stdout offer disagree")

        icon_raw = _required_object(
            raw["icon_match"], f"{location}.raw_json.icon_match"
        )
        icon_state = _required_text(
            icon_raw.get("state"), f"{location}.raw_json.icon_match.state"
        ).upper()
        if icon_state not in {"MATCHED", "UNKNOWN", "UNAVAILABLE"}:
            raise ProducerError(
                f"{location}.raw_json.icon_match.state: unsupported state {icon_state!r}"
            )
        icon_id = _optional_text(
            icon_raw.get("augment_id"),
            f"{location}.raw_json.icon_match.augment_id",
        )
        if (icon_state == "MATCHED") != (icon_id is not None):
            raise ProducerError(
                f"{location}.raw_json.icon_match: MATCHED alone requires augment_id"
            )
        icon_confidence = _unit_optional_number(
            icon_raw.get("confidence"),
            f"{location}.raw_json.icon_match.confidence",
        )
        icon_reason = _required_text(
            icon_raw.get("reason"), f"{location}.raw_json.icon_match.reason"
        )
        cards.append(
            {
                "slot": slot,
                "ocr": {
                    "state": "RECOGNIZED",
                    "text": raw_text,
                    "backend": backend,
                    "confidence": ocr_confidence,
                },
                "icon": {
                    "state": icon_state,
                    "augment_id": icon_id,
                    "confidence": icon_confidence,
                    "reason": icon_reason,
                },
                "final": {
                    "state": "RECOGNIZED",
                    "augment_id": augment_id,
                    "display_name": display_name,
                    "match_confidence": match_confidence,
                    "confidence": final_confidence,
                    "reason": reason,
                },
            }
        )
    return cards, evidence


def _unknown_cards() -> list[dict[str, Any]]:
    return [
        {
            "slot": slot,
            "ocr": {"state": "UNAVAILABLE", "text": None},
            "icon": {"state": "UNAVAILABLE", "augment_id": None},
            "final": {"state": "UNKNOWN", "augment_id": None, "display_name": None},
        }
        for slot in SLOTS
    ]


def _benchmark_result(
    screen_detected: bool, latency_ms: float, cards: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    benchmark_cards: list[dict[str, Any]] = []
    for card in cards:
        final = card["final"]
        if not screen_detected or final["state"] != "RECOGNIZED":
            benchmark_cards.append({"slot": card["slot"], "state": "UNKNOWN"})
            continue
        benchmark_cards.append(
            {
                "slot": card["slot"],
                "state": "RECOGNIZED",
                "augment_id": final["augment_id"],
                "ocr_text": (
                    card["ocr"]["text"]
                    if card["ocr"]["state"] == "RECOGNIZED"
                    else None
                ),
                "ocr_state": card["ocr"]["state"],
                "ocr_backend": card["ocr"].get("backend"),
                "icon_id": (
                    card["icon"]["augment_id"]
                    if card["icon"]["state"] in {"RECOGNIZED", "MATCHED"}
                    else None
                ),
                "icon_state": card["icon"]["state"],
                "icon_confidence": card["icon"].get("confidence"),
            }
        )
    return {
        "screen_detected": screen_detected,
        "latency_ms": latency_ms,
        "cards": benchmark_cards,
    }


def _failure_prediction(
    sample_id: str,
    provenance: str,
    capture: ProcessCapture,
    command: Sequence[str],
    code: str,
    message: str,
    protocol: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latency = round(capture.wall_latency_ms, 6)
    cards = _unknown_cards()
    return {
        "schema_version": PREDICTION_SCHEMA,
        "sample_id": sample_id,
        "provenance": provenance,
        "prediction_status": "failure",
        "failure": {"code": code, "message": message},
        "execution": {
            "headless": True,
            "mode": "KIWI",
            "returncode": capture.returncode,
            "timed_out": capture.timed_out,
            "wall_latency_ms": latency,
            "stderr_tail": capture.stderr[-2000:] or None,
            "argument_names": [argument for argument in command if argument.startswith("--")],
        },
        "protocol": protocol,
        "prediction_source": (
            protocol.get("database_evidence") if protocol is not None else None
        ),
        "detector": {"raw": None, "stable": None, "screen_detected": False},
        "cards": cards,
        "latency_ms": latency,
        "latency_source": "producer_wall_clock",
        "benchmark_recognition_result": _benchmark_result(False, latency, cards),
    }


def _produce_prediction(
    sample_id: str,
    provenance: str,
    capture: ProcessCapture,
    command: Sequence[str],
    knowledge: Path,
    workspace: Path,
) -> dict[str, Any]:
    if capture.launch_error is not None:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "replay_launch_failed",
            capture.launch_error,
        )
    if capture.timed_out:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "replay_timeout",
            "Replay process exceeded the producer timeout",
        )
    if capture.decode_error is not None:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "stdout_not_utf8",
            capture.decode_error,
        )
    try:
        protocol = _parse_replay_jsonl(capture.stdout, knowledge, workspace)
    except ProducerError as error:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "malformed_replay_jsonl",
            str(error),
        )

    if capture.returncode != 0:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "replay_exit_nonzero",
            f"Replay process exited with code {capture.returncode}",
            protocol,
        )
    if not protocol["close_ok"]:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "replay_close_failed",
            "session_end.close_ok is false",
            protocol,
        )
    if protocol["session_status"] not in SUCCESS_SESSION_STATUSES:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "replay_session_failed",
            f"unexpected session status {protocol['session_status']!r}",
            protocol,
        )

    try:
        cards, database_evidence = _database_stage_cards(protocol)
        protocol["database_evidence"] = database_evidence
    except (OSError, ProducerError, TypeError) as error:
        return _failure_prediction(
            sample_id,
            provenance,
            capture,
            command,
            "database_evidence_invalid",
            str(error),
            protocol,
        )

    screen_detected = bool(protocol["stable_detector"]["visible"])
    if not screen_detected:
        cards = _unknown_cards()
        prediction_status = "detector_not_visible"
    elif all(card["final"]["state"] == "RECOGNIZED" for card in cards):
        prediction_status = "recognized"
    else:
        prediction_status = "unknown"

    reported_latency = protocol["reported_latency_ms"]
    if reported_latency is None:
        latency = round(capture.wall_latency_ms, 6)
        latency_source = "producer_wall_clock"
    else:
        latency = reported_latency
        latency_source = "replay_jsonl"
    benchmark = _benchmark_result(screen_detected, latency, cards)
    return {
        "schema_version": PREDICTION_SCHEMA,
        "sample_id": sample_id,
        "provenance": provenance,
        "prediction_status": prediction_status,
        "failure": None,
        "execution": {
            "headless": True,
            "mode": "KIWI",
            "returncode": capture.returncode,
            "timed_out": False,
            "wall_latency_ms": round(capture.wall_latency_ms, 6),
            "stderr_tail": capture.stderr[-2000:] or None,
            "argument_names": [argument for argument in command if argument.startswith("--")],
        },
        "protocol": protocol,
        "prediction_source": database_evidence,
        "detector": {
            "raw": protocol["raw_detector"],
            "stable": protocol["stable_detector"],
            "screen_detected": screen_detected,
        },
        "cards": cards,
        "latency_ms": latency,
        "latency_source": latency_source,
        "benchmark_recognition_result": benchmark,
    }


def _metadata_record(sample: Sample) -> dict[str, Any]:
    width, height = _png_dimensions(sample.raw_path)
    ui_scale = sample.metadata.get("ui_scale")
    if (
        ui_scale is None
        or isinstance(ui_scale, bool)
        or (isinstance(ui_scale, str) and not ui_scale.strip())
        or (
            isinstance(ui_scale, (int, float))
            and (not math.isfinite(float(ui_scale)) or float(ui_scale) <= 0)
        )
        or not isinstance(ui_scale, (str, int, float))
    ):
        ui_scale = "unknown"
    record: dict[str, Any] = {
        "sample_id": sample.sample_id,
        "provenance": sample.provenance,
        "resolution": {"width": width, "height": height},
        "ui_scale": ui_scale,
        "ocr_preprocessing_variant": {
            "grayscale": True,
            "contrast": None,
            "threshold": None,
            "scale": "1x",
            "variant_id": "product-default-gray-1x",
        },
    }
    if sample.source_kind is not None:
        source: dict[str, Any] = {"kind": sample.source_kind}
        source_metadata = sample.metadata.get("source")
        if isinstance(source_metadata, dict):
            for key in ("reference", "id"):
                value = source_metadata.get(key)
                if isinstance(value, str) and value.strip():
                    source[key] = value
        record["source"] = source
        record["derived_from_preview"] = False
    if sample.capture_boundary is not None:
        record["capture_boundary"] = sample.capture_boundary
    if sample.benchmark_use is not None:
        record["benchmark_use"] = sample.benchmark_use
    return record


def _annotation_record(sample: Sample) -> dict[str, Any]:
    cards = _required_object(sample.annotation.get("cards"), "annotation.cards")
    result_cards: list[dict[str, Any]] = []
    for lower_slot, slot in zip(("left", "center", "right"), SLOTS):
        source = _required_object(cards.get(lower_slot), f"annotation.cards.{lower_slot}")
        valid = _required_bool(source.get("valid"), f"annotation.cards.{lower_slot}.valid")
        if valid:
            augment_id = _required_text(
                source.get("augment_id"), f"annotation.cards.{lower_slot}.augment_id"
            )
            augment_name = _required_text(
                source.get("augment_name"),
                f"annotation.cards.{lower_slot}.augment_name",
            )
            result_cards.append(
                {
                    "slot": slot,
                    "valid": True,
                    "augment_id": augment_id,
                    "ocr_text": augment_name,
                    "icon_id": augment_id,
                }
            )
        else:
            result_cards.append({"slot": slot, "valid": False})
    return {
        "sample_id": sample.sample_id,
        "screen_present": True,
        "cards": result_cards,
    }


def _recognition_record(sample_id: str, prediction: dict[str, Any]) -> dict[str, Any]:
    return {"sample_id": sample_id, **prediction["benchmark_recognition_result"]}


def _write_join_streams(
    directory: Path,
    metadata_records: Sequence[dict[str, Any]],
    annotation_records: Sequence[dict[str, Any]],
    recognition_records: Sequence[dict[str, Any]],
) -> None:
    _atomic_write_jsonl(directory / "metadata.jsonl", metadata_records)
    _atomic_write_jsonl(directory / "annotations.jsonl", annotation_records)
    _atomic_write_jsonl(directory / "recognition_results.jsonl", recognition_records)


def _benchmark_join(
    join_directory: Path,
    minimum_real_samples: int,
    max_failures: int,
) -> tuple[Any, dict[str, Any]]:
    benchmark = _load_module("phase2_benchmark_for_replay_producer", BENCHMARK_PATH)
    loaded = benchmark.load_dataset(join_directory)
    report = benchmark.build_report(
        loaded,
        join_directory,
        minimum_real_samples=minimum_real_samples,
        max_failures=max_failures,
    )
    return benchmark, report


def _sample_output_path(
    sample: Sample, output_directory: Path | None
) -> Path:
    if output_directory is None:
        return sample.directory / "recognition.json"
    return output_directory / sample.provenance / sample.sample_id / "recognition.json"


def _summary_result(
    sample: Sample, prediction: dict[str, Any], path: Path | None
) -> dict[str, Any]:
    return {
        "sample_id": sample.sample_id,
        "provenance": sample.provenance,
        "prediction_status": prediction["prediction_status"],
        "failure": prediction["failure"],
        "prediction_path": str(path) if path is not None else None,
        "prediction_source": prediction["prediction_source"],
        "recognition_result": prediction["benchmark_recognition_result"],
    }


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


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
        description="Replay every valid, complete Phase2 sample and run the unchanged benchmark core."
    )
    parser.add_argument("--exe", type=Path, required=True, help="Product executable")
    parser.add_argument(
        "--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT, help="Dataset root"
    )
    parser.add_argument(
        "--knowledge", type=Path, default=DEFAULT_KNOWLEDGE, help="Knowledge JSON"
    )
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "--output-dir",
        type=Path,
        help="Write predictions and benchmark artifacts outside the dataset",
    )
    destination.add_argument(
        "--dry-run",
        action="store_true",
        help="Run Replay and benchmark join without persistent writes",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=30.0,
        help="Per-sample process timeout (default: 30)",
    )
    parser.add_argument(
        "--minimum-real-samples", type=_positive_integer, default=1
    )
    parser.add_argument("--max-failures", type=_non_negative_integer, default=20)
    return parser.parse_args(argv)


def run(arguments: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if arguments.timeout_seconds > 86400:
        raise ProducerError("--timeout-seconds must not exceed the product limit 86400")
    executable = arguments.exe.expanduser().resolve(strict=True)
    if not executable.is_file():
        raise ProducerError(f"--exe is not a file: {executable}")
    executable_sha256 = _sha256_file(executable)
    dataset_root = arguments.dataset_root.expanduser().resolve(strict=True)
    knowledge = arguments.knowledge.expanduser().resolve(strict=True)
    if not knowledge.is_file():
        raise ProducerError(f"--knowledge is not a file: {knowledge}")
    output_directory: Path | None = None
    if arguments.output_dir is not None:
        output_directory = arguments.output_dir.expanduser().resolve(strict=False)
        if _is_inside(dataset_root, output_directory):
            raise ProducerError("--output-dir must be outside --dataset-root")
        output_directory.mkdir(parents=True, exist_ok=True)

    samples, validation = _eligible_samples(dataset_root, knowledge)
    predictions: list[dict[str, Any]] = []
    metadata_records: list[dict[str, Any]] = []
    annotation_records: list[dict[str, Any]] = []
    recognition_records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    benchmark_join_sample_count = 0

    for sample in samples:
        with tempfile.TemporaryDirectory(
            prefix=f"phase2-replay-{sample.sample_id[:32]}-"
        ) as temporary:
            workspace = Path(temporary).resolve(strict=True)
            capture, command = _run_replay_process(
                executable,
                sample.raw_path,
                knowledge,
                workspace,
                arguments.timeout_seconds,
            )
            prediction = _produce_prediction(
                sample.sample_id,
                sample.provenance,
                capture,
                command,
                knowledge,
                workspace,
            )
        predictions.append(prediction)
        # Replay every legal complete sample, but never let a preview-derived
        # real sample enter real metrics. Synthetic records are retained because
        # the unchanged benchmark core explicitly excludes and counts them.
        if sample.benchmark_eligible or sample.provenance == "synthetic":
            metadata_records.append(_metadata_record(sample))
            annotation_records.append(_annotation_record(sample))
            recognition_records.append(
                _recognition_record(sample.sample_id, prediction)
            )
            benchmark_join_sample_count += 1

        prediction_path: Path | None = None
        if not arguments.dry_run:
            prediction_path = _sample_output_path(sample, output_directory)
            _atomic_write_json(prediction_path, prediction)
        summaries.append(_summary_result(sample, prediction, prediction_path))

    temporary_join: tempfile.TemporaryDirectory[str] | None = None
    try:
        if output_directory is not None:
            join_directory = output_directory / "benchmark_input"
        else:
            temporary_join = tempfile.TemporaryDirectory(
                prefix="phase2-replay-benchmark-join-"
            )
            join_directory = Path(temporary_join.name).resolve(strict=True)
        _write_join_streams(
            join_directory, metadata_records, annotation_records, recognition_records
        )
        benchmark, benchmark_report = _benchmark_join(
            join_directory, arguments.minimum_real_samples, arguments.max_failures
        )

        failure_count = sum(
            prediction["prediction_status"] == "failure" for prediction in predictions
        )
        report: dict[str, Any] = {
            "schema_version": PRODUCER_REPORT_SCHEMA,
            "status": "completed_with_failures" if failure_count else "ok",
            "dataset_root": str(dataset_root),
            "executable": str(executable),
            "executable_sha256": executable_sha256,
            "prediction_source": "session_start.database:recognition_results.raw_json",
            "database_hashes": [
                {
                    "sample_id": prediction["sample_id"],
                    "database_sha256": prediction["prediction_source"]["database_sha256"],
                    "session_id": prediction["prediction_source"]["session_id"],
                }
                for prediction in predictions
                if prediction["prediction_source"] is not None
                and "database_sha256" in prediction["prediction_source"]
            ],
            "knowledge": str(knowledge),
            "mode": "KIWI",
            "headless": True,
            "dry_run": bool(arguments.dry_run),
            "output_directory": str(output_directory) if output_directory else None,
            "dataset_validation": {
                "valid": validation["valid"],
                "discovered_sample_count": validation["sample_count"],
                "eligible_sample_count": len(samples),
                "replayable_sample_count": len(samples),
                "benchmark_join_sample_count": benchmark_join_sample_count,
                "validator_benchmark_eligible_sample_count": validation[
                    "benchmark_eligible_sample_count"
                ],
                "skipped_excluded_sample_count": validation[
                    "skipped_excluded_sample_count"
                ],
                "invalid_or_unannotated_real": validation[
                    "benchmark_excluded_counts"
                ]["invalid_or_unannotated_real"],
            },
            "prediction_counts": {
                "total": len(predictions),
                "recognized": sum(
                    prediction["prediction_status"] == "recognized"
                    for prediction in predictions
                ),
                "unknown": sum(
                    prediction["prediction_status"] == "unknown"
                    for prediction in predictions
                ),
                "detector_not_visible": sum(
                    prediction["prediction_status"] == "detector_not_visible"
                    for prediction in predictions
                ),
                "failure": failure_count,
                "unknown_card_count": sum(
                    card["final"]["state"] == "UNKNOWN"
                    for prediction in predictions
                    for card in prediction["cards"]
                ),
            },
            "results": summaries,
            "benchmark": benchmark_report,
        }
        if output_directory is not None:
            _atomic_write_jsonl(
                output_directory / "producer_results.jsonl", predictions
            )
            _atomic_write_json(output_directory / "producer_report.json", report)
            _atomic_write_text(
                output_directory / "benchmark_report.json",
                benchmark._strict_json_text(benchmark_report),
            )
            _atomic_write_text(
                output_directory / "benchmark_report.md",
                benchmark.render_markdown(benchmark_report),
            )
        return (1 if failure_count else 0), report
    finally:
        if temporary_join is not None:
            temporary_join.cleanup()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    try:
        exit_code, report = run(arguments)
    except (OSError, ProducerError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(_strict_json_text(report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
