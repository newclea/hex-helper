#!/usr/bin/env python3
"""Hermetic fake of the product Replay JSONL contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


def emit(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ),
        flush=True,
    )


def detector(visible: bool, reason: str) -> dict[str, object]:
    return {
        "frame_id": 1,
        "visible": visible,
        "confidence": 0.95 if visible else 0.15,
        "reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--max-seconds", required=True)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()

    if arguments.mode != "KIWI":
        return 91
    if not arguments.replay.is_absolute() or arguments.replay.name != "RAW.png":
        return 92
    if not arguments.knowledge.is_absolute() or not arguments.workspace.is_absolute():
        return 93
    if not arguments.workspace.is_dir() or not arguments.once:
        return 94

    sample_id = arguments.replay.parent.name
    if sample_id.startswith("timeout"):
        time.sleep(2.0)
        return 0

    emit(
        {
            "type": "session_start",
            "session_id": "fake-session",
            "manual": {"champion": None, "mode": arguments.mode, "selected": None},
            "paths": {
                "knowledge": str(arguments.knowledge.resolve()),
                "workspace": str(arguments.workspace.resolve()),
                "dataset_root": str(arguments.replay.parents[2].resolve()),
            },
            "preview_requested": False,
            "static_replay": True,
        }
    )

    if sample_id.startswith("malformed"):
        # Duplicate keys are accepted by permissive json.loads but must be
        # rejected by the producer's strict JSONL parser.
        print('{"type":"frame_result","type":"frame_result"}', flush=True)
        return 0

    visible = not sample_id.startswith("detector-false")
    is_unknown = sample_id.startswith("unknown")
    accepted = visible and not is_unknown
    reason = (
        "accepted"
        if accepted
        else "recognition_unknown"
        if is_unknown
        else "insufficient_luma"
    )
    emit(
        {
            "type": "frame_result",
            "raw_detector": detector(visible, reason),
            "stable_detector": detector(visible, reason),
            "ocr_executed": visible,
            "accepted": accepted,
            "duplicate": False,
            "reason": reason,
            "latency_ms": 8.0,
        }
    )

    if accepted:
        cards = []
        for slot, suffix, display_name in (
            ("LEFT", "left", "Pred Left"),
            ("CENTER", "center", "Pred Center"),
            ("RIGHT", "right", "Pred Right"),
        ):
            augment_id = f"pred.{suffix}"
            cards.append(
                {
                    "slot": slot,
                    "ocr": {"state": "RECOGNIZED", "text": display_name},
                    "icon": {"state": "RECOGNIZED", "augment_id": augment_id},
                    "final": {
                        "state": "RECOGNIZED",
                        "augment_id": augment_id,
                        "display_name": display_name,
                    },
                }
            )
        emit(
            {
                "type": "offer",
                "latency_ms": 12.5,
                "screen_detection": {"state": "DETECTED"},
                "cards": cards,
            }
        )

    status = (
        "completed"
        if accepted
        else "completed_unknown"
        if is_unknown
        else "completed_no_stable_observation"
    )
    emit(
        {
            "type": "session_end",
            "session_id": "fake-session",
            "status": status,
            "frames_processed": 1,
            "close_ok": True,
            "source_summary": {
                "kind": "replay",
                "static_replay": True,
                "recognition_attempt_completed": visible,
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
