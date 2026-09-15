#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / "scripts" / "phase2" / "run_dataset_replay.py"
BENCHMARK = ROOT / "scripts" / "benchmark_phase2.py"
SLOTS = ("left", "center", "right")

FAKE_REPLAY_SOURCE = r'''#!/usr/bin/env python3
import argparse, json, sqlite3, time
from pathlib import Path

def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)

def detector(visible, reason):
    return {"frame_id": 1, "visible": visible,
            "confidence": 0.95 if visible else 0.15, "reason": reason}

parser = argparse.ArgumentParser()
parser.add_argument("--replay", type=Path, required=True)
parser.add_argument("--mode", required=True)
parser.add_argument("--knowledge", type=Path, required=True)
parser.add_argument("--workspace", type=Path, required=True)
parser.add_argument("--max-seconds", required=True)
parser.add_argument("--once", action="store_true")
args = parser.parse_args()
sample_id = args.replay.parent.name
if sample_id.startswith("timeout"):
    time.sleep(2)
    raise SystemExit(0)

session_id = "fake-session"
session_dir = args.workspace / session_id
session_dir.mkdir()
database = session_dir / "session.sqlite3"
connection = sqlite3.connect(database)
connection.execute("""CREATE TABLE recognition_results(
    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, offer_id INTEGER,
    slot INTEGER NOT NULL, recognition_state INTEGER NOT NULL,
    augment_id TEXT, display_name TEXT, confidence REAL NOT NULL,
    observed_at_utc TEXT NOT NULL, raw_json TEXT NOT NULL)""")

accepted = not sample_id.startswith(("unknown", "detector-false", "malformed-case"))
cards = []
if accepted:
    icon_unknown = sample_id.startswith("icon-unknown")
    malformed_db = sample_id.startswith("malformed-db")
    inconsistent_db = sample_id.startswith("inconsistent-db")
    for index, (slot, suffix, title) in enumerate((
        ("LEFT", "left", "Pred Left"),
        ("CENTER", "center", "Pred Center"),
        ("RIGHT", "right", "Pred Right")), 1):
        augment_id = f"pred.{suffix}"
        cards.append({"slot": slot, "final": {"state": "RECOGNIZED",
                      "augment_id": augment_id, "display_name": title}})
        icon = {"state": "UNKNOWN", "augment_id": None, "confidence": 0.75,
                "reason": "hash_distance_above_threshold"} if icon_unknown else {
                "state": "MATCHED", "augment_id": augment_id,
                "confidence": 0.99, "reason": "hash_match"}
        raw = {"state": "RECOGNIZED", "raw_text": title,
               "backend": "fake_ocr:zh-CN", "ocr_confidence": None,
               "match_confidence": 1.0, "final_confidence": 0.85,
               "augment_id": "wrong.id" if inconsistent_db and index == 1 else augment_id,
               "display_name": title, "reason": "normalized_match", "icon_match": icon}
        raw_json = "{" if malformed_db and index == 1 else json.dumps(raw, ensure_ascii=False)
        connection.execute("INSERT INTO recognition_results VALUES(?,?,?,?,?,?,?,?,?,?)",
            (index, session_id, 1, index, 2, augment_id, title, 0.85,
             "2026-08-26T00:00:00Z", raw_json))
connection.commit()
connection.close()

reported_database = args.knowledge if sample_id.startswith("outside-db") else database
emit({"type": "session_start", "session_id": session_id,
      "database": str(reported_database.resolve()),
      "manual": {"champion": None, "mode": args.mode, "selected": None},
      "paths": {"knowledge": str(args.knowledge.resolve()),
                "workspace": str(args.workspace.resolve())},
      "preview_requested": False, "static_replay": True})
if sample_id.startswith("malformed-case"):
    print('{"type":"frame_result","type":"frame_result"}', flush=True)
    raise SystemExit(0)

visible = not sample_id.startswith("detector-false")
final_unknown = sample_id.startswith("unknown")
was_accepted = visible and not final_unknown
reason = "accepted" if was_accepted else "recognition_unknown" if final_unknown else "insufficient_luma"
emit({"type": "frame_result", "raw_detector": detector(visible, reason),
      "stable_detector": detector(visible, reason), "ocr_executed": visible,
      "accepted": was_accepted, "reason": reason, "latency_ms": 8.0})
if was_accepted:
    emit({"type": "offer", "latency_ms": 12.5,
          "screen_detection": {"state": "DETECTED"}, "cards": cards})
status = "completed" if was_accepted else "completed_unknown" if final_unknown else "completed_no_stable_observation"
emit({"type": "session_end", "session_id": session_id, "status": status,
      "frames_processed": 1, "close_ok": True,
      "source_summary": {"kind": "replay", "static_replay": True}})
'''


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, red: int) -> None:
    width = 2
    height = 2
    row = b"\x00" + bytes((red, 40, 90, 255)) * width
    pixels = row * height
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk("IHDR".encode("ascii"), struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(pixels))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(data)


class DatasetFixture:
    def __init__(self, base: Path) -> None:
        self.root = base / "augment_offers"
        (self.root / "real").mkdir(parents=True)
        (self.root / "synthetic").mkdir()
        self.knowledge = base / "augments.zh-CN.json"
        self.executable = base / "fake_replay_exe.py"
        self.executable.write_text(FAKE_REPLAY_SOURCE, encoding="utf-8")
        write_json(
            self.knowledge,
            {
                "augments": [
                    {"id": "pred.left", "display_name": "Pred Left"},
                    {"id": "pred.center", "display_name": "Pred Center"},
                    {"id": "pred.right", "display_name": "Pred Right"},
                    {"id": "truth.left", "display_name": "Secret Truth Left"},
                    {"id": "truth.center", "display_name": "Secret Truth Center"},
                    {"id": "truth.right", "display_name": "Secret Truth Right"},
                ]
            },
        )

    def add_sample(
        self,
        sample_id: str,
        *,
        color: int,
        status: str = "complete",
        matching_truth: bool = True,
        preview_derived: bool = False,
    ) -> Path:
        sample = self.root / "real" / sample_id
        sample.mkdir()
        for index, filename in enumerate(
            ("RAW.png", "LEFT_CARD.png", "CENTER_CARD.png", "RIGHT_CARD.png")
        ):
            write_png(sample / filename, (color + index) % 255)
        write_json(
            sample / "metadata.json",
            {
                "schema_version": 1,
                "sample_id": sample_id,
                "provenance": "real",
                "source": {
                    "kind": (
                        "debug_preview"
                        if preview_derived
                        else "windows_graphics_capture"
                    ),
                    "reference": f"fixture/{sample_id}",
                },
                "derived_from_preview": preview_derived,
                "capture_boundary": (
                    "preview_derived" if preview_derived else "original_wgc"
                ),
                "benchmark_use": (
                    "real_scenario_calibration"
                    if preview_derived
                    else "original_wgc_metrics"
                ),
                "ui_scale": None,
            },
        )
        if status == "skipped":
            annotation = {
                "schema_version": 1,
                "sample_id": sample_id,
                "status": "skipped",
                "cards": {},
                "skip_reason": "fixture exclusion",
                "updated_at_utc": "2026-08-26T00:00:00Z",
            }
        elif status == "in_progress":
            annotation = {
                "schema_version": 1,
                "sample_id": sample_id,
                "status": "in_progress",
                "cards": {},
                "skip_reason": None,
                "updated_at_utc": "2026-08-26T00:00:00Z",
            }
        else:
            prefix = "pred" if matching_truth else "truth"
            names = (
                ("Pred Left", "Pred Center", "Pred Right")
                if matching_truth
                else ("Secret Truth Left", "Secret Truth Center", "Secret Truth Right")
            )
            annotation = {
                "schema_version": 1,
                "sample_id": sample_id,
                "status": "complete",
                "cards": {
                    slot: {
                        "augment_id": f"{prefix}.{slot}",
                        "augment_name": name,
                        "valid": True,
                    }
                    for slot, name in zip(SLOTS, names)
                },
                "skip_reason": None,
                "updated_at_utc": "2026-08-26T00:00:00Z",
            }
        write_json(sample / "annotation.json", annotation)
        return sample


class Phase2DatasetReplayBenchmarkTest(unittest.TestCase):
    def run_producer(
        self,
        fixture: DatasetFixture,
        output: Path | None,
        *,
        timeout: float = 1.0,
        dry_run: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
        command = [
            sys.executable,
            "-B",
            str(PRODUCER),
            "--exe",
            str(fixture.executable),
            "--dataset-root",
            str(fixture.root),
            "--knowledge",
            str(fixture.knowledge),
            "--timeout-seconds",
            str(timeout),
        ]
        if output is not None:
            command.extend(("--output-dir", str(output)))
        if dry_run:
            command.append("--dry-run")
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            check=False,
        )
        parsed = json.loads(completed.stdout) if completed.stdout else None
        return completed, parsed

    def test_success_exports_stage_evidence_and_joined_benchmark(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-replay-success-") as temporary:
            fixture = DatasetFixture(Path(temporary))
            sample = fixture.add_sample("success-case", color=20)
            fixture.add_sample(
                "success-preview-case", color=24, preview_derived=True
            )
            output = Path(temporary) / "producer-output"

            completed, report = self.run_producer(fixture, output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIsNotNone(report)
            self.assertFalse((sample / "recognition.json").exists())
            self.assertTrue(
                (output / "real" / "success-preview-case" / "recognition.json").is_file()
            )
            self.assertEqual(report["dataset_validation"]["replayable_sample_count"], 2)
            self.assertEqual(report["dataset_validation"]["benchmark_join_sample_count"], 1)
            prediction_path = output / "real" / "success-case" / "recognition.json"
            prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
            self.assertEqual(prediction["prediction_status"], "recognized")
            self.assertTrue(prediction["detector"]["raw"]["visible"])
            self.assertTrue(prediction["detector"]["stable"]["visible"])
            self.assertEqual(prediction["latency_ms"], 12.5)
            self.assertEqual(prediction["latency_source"], "replay_jsonl")
            self.assertEqual(
                [card["ocr"]["text"] for card in prediction["cards"]],
                ["Pred Left", "Pred Center", "Pred Right"],
            )
            self.assertTrue(
                all(card["ocr"]["backend"] == "fake_ocr:zh-CN" for card in prediction["cards"])
            )
            self.assertEqual(
                [card["icon"]["augment_id"] for card in prediction["cards"]],
                ["pred.left", "pred.center", "pred.right"],
            )
            self.assertEqual(
                [card["final"]["augment_id"] for card in prediction["cards"]],
                ["pred.left", "pred.center", "pred.right"],
            )
            self.assertEqual(
                prediction["prediction_source"]["prediction_source"],
                "session_start.database:recognition_results.raw_json",
            )
            self.assertEqual(len(prediction["prediction_source"]["database_sha256"]), 64)
            self.assertEqual(len(report["executable_sha256"]), 64)
            self.assertEqual(len(report["database_hashes"]), 2)

            benchmark_report = json.loads(
                (output / "benchmark_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(benchmark_report["sample_count"], 1)
            self.assertEqual(benchmark_report["screen_detection_accuracy"], 1.0)
            self.assertEqual(benchmark_report["ocr_accuracy"], 1.0)
            self.assertEqual(benchmark_report["icon_accuracy"], 1.0)
            self.assertEqual(benchmark_report["three_cards_all_correct_rate"], 1.0)
            self.assertEqual(benchmark_report["average_latency_ms"], 12.5)

    def test_icon_unknown_is_independent_zero_while_ocr_and_final_are_three_of_three(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-replay-icon-unknown-") as temporary:
            fixture = DatasetFixture(Path(temporary))
            fixture.add_sample("icon-unknown-case", color=30)
            output = Path(temporary) / "producer-output"

            completed, _report = self.run_producer(fixture, output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            prediction = json.loads(
                (output / "real" / "icon-unknown-case" / "recognition.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [card["icon"]["state"] for card in prediction["cards"]],
                ["UNKNOWN", "UNKNOWN", "UNKNOWN"],
            )
            benchmark = json.loads(
                (output / "benchmark_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(benchmark["metric_counts"]["ocr_accuracy"], {"numerator": 3, "denominator": 3})
            self.assertEqual(benchmark["metric_counts"]["icon_accuracy"], {"numerator": 0, "denominator": 3})
            self.assertEqual(benchmark["metric_counts"]["card_recognition_accuracy"], {"numerator": 3, "denominator": 3})

    def test_database_malformed_escape_and_stdout_inconsistency_are_failures(self) -> None:
        for sample_id, expected_code in (
            ("malformed-db-case", "database_evidence_invalid"),
            ("outside-db-case", "malformed_replay_jsonl"),
            ("inconsistent-db-case", "database_evidence_invalid"),
        ):
            with self.subTest(sample_id=sample_id), tempfile.TemporaryDirectory(
                prefix=f"phase2-replay-{sample_id}-"
            ) as temporary:
                fixture = DatasetFixture(Path(temporary))
                fixture.add_sample(sample_id, color=35)
                output = Path(temporary) / "producer-output"

                completed, report = self.run_producer(fixture, output)

                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertEqual(report["prediction_counts"]["failure"], 1)
                prediction = json.loads(
                    (output / "real" / sample_id / "recognition.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(prediction["failure"]["code"], expected_code)

    def test_unknown_is_not_filled_from_annotation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-replay-unknown-") as temporary:
            fixture = DatasetFixture(Path(temporary))
            fixture.add_sample(
                "unknown-case", color=40, matching_truth=False
            )
            output = Path(temporary) / "producer-output"

            completed, _report = self.run_producer(fixture, output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            prediction_path = output / "real" / "unknown-case" / "recognition.json"
            prediction_text = prediction_path.read_text(encoding="utf-8")
            prediction = json.loads(prediction_text)
            self.assertEqual(prediction["prediction_status"], "unknown")
            self.assertTrue(prediction["detector"]["screen_detected"])
            self.assertEqual(
                [card["final"]["state"] for card in prediction["cards"]],
                ["UNKNOWN", "UNKNOWN", "UNKNOWN"],
            )
            self.assertNotIn("truth.left", prediction_text)
            self.assertNotIn("Secret Truth Left", prediction_text)
            benchmark_report = json.loads(
                (output / "benchmark_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(benchmark_report["unknown_rate"], 1.0)
            self.assertEqual(benchmark_report["card_recognition_accuracy"], 0.0)

    def test_detector_false_writes_explicit_three_unknown_cards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-replay-detector-") as temporary:
            fixture = DatasetFixture(Path(temporary))
            fixture.add_sample("detector-false-case", color=60)
            output = Path(temporary) / "producer-output"

            completed, _report = self.run_producer(fixture, output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            prediction = json.loads(
                (output / "real" / "detector-false-case" / "recognition.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(prediction["prediction_status"], "detector_not_visible")
            self.assertFalse(prediction["detector"]["screen_detected"])
            self.assertEqual(len(prediction["cards"]), 3)
            self.assertTrue(
                all(card["final"]["state"] == "UNKNOWN" for card in prediction["cards"])
            )
            benchmark_report = json.loads(
                (output / "benchmark_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(benchmark_report["screen_detection_accuracy"], 0.0)

    def test_timeout_and_malformed_each_write_benchmark_readable_failure(self) -> None:
        for sample_id, expected_code, timeout in (
            ("timeout-case", "replay_timeout", 0.15),
            ("malformed-case", "malformed_replay_jsonl", 1.0),
        ):
            with self.subTest(sample_id=sample_id), tempfile.TemporaryDirectory(
                prefix=f"phase2-replay-{sample_id}-"
            ) as temporary:
                fixture = DatasetFixture(Path(temporary))
                fixture.add_sample(sample_id, color=80)
                output = Path(temporary) / "producer-output"

                completed, report = self.run_producer(
                    fixture, output, timeout=timeout
                )

                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertEqual(report["status"], "completed_with_failures")
                prediction = json.loads(
                    (output / "real" / sample_id / "recognition.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(prediction["prediction_status"], "failure")
                self.assertEqual(prediction["failure"]["code"], expected_code)
                self.assertGreaterEqual(prediction["latency_ms"], 0)
                recognition_line = json.loads(
                    (output / "benchmark_input" / "recognition_results.jsonl")
                    .read_text(encoding="utf-8")
                    .strip()
                )
                self.assertEqual(recognition_line["sample_id"], sample_id)
                self.assertEqual(
                    [card["state"] for card in recognition_line["cards"]],
                    ["UNKNOWN", "UNKNOWN", "UNKNOWN"],
                )
                self.assertEqual(
                    json.loads(
                        (output / "benchmark_report.json").read_text(encoding="utf-8")
                    )["sample_count"],
                    1,
                )

    def test_skipped_and_invalid_samples_are_not_replayed_and_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-replay-filter-") as temporary:
            fixture = DatasetFixture(Path(temporary))
            fixture.add_sample("success-case", color=100)
            skipped = fixture.add_sample("skipped-case", color=120, status="skipped")
            invalid = fixture.add_sample("in-progress-case", color=140, status="in_progress")

            completed, report = self.run_producer(
                fixture, None, dry_run=True
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(report["dataset_validation"]["eligible_sample_count"], 1)
            self.assertEqual(report["dataset_validation"]["skipped_excluded_sample_count"], 1)
            self.assertEqual(report["prediction_counts"]["total"], 1)
            self.assertFalse((fixture.root / "real" / "success-case" / "recognition.json").exists())
            self.assertFalse((skipped / "recognition.json").exists())
            self.assertFalse((invalid / "recognition.json").exists())

    def test_default_destination_atomically_writes_sample_recognition(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-replay-in-place-") as temporary:
            fixture = DatasetFixture(Path(temporary))
            sample = fixture.add_sample("success-case", color=160)

            completed, report = self.run_producer(fixture, None)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            recognition = sample / "recognition.json"
            self.assertTrue(recognition.is_file())
            self.assertEqual(
                json.loads(recognition.read_text(encoding="utf-8"))["sample_id"],
                "success-case",
            )
            self.assertEqual(list(sample.glob(".recognition.json.*.tmp")), [])
            self.assertEqual(report["results"][0]["prediction_path"], str(recognition))

            # recognition.json is outside the older validator's artifact list,
            # but this producer must recognize its own schema and remain rerunnable.
            second, second_report = self.run_producer(fixture, None)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second_report["prediction_counts"]["total"], 1)
            self.assertEqual(list(sample.glob(".recognition.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
