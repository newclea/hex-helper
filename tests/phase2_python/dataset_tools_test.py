#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock
import zlib


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_script(module_name: str, relative_path: str) -> ModuleType:
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ANNOTATE = load_script("phase2_annotate_dataset", "scripts/phase2/annotate_dataset.py")
VALIDATE = load_script("phase2_validate_dataset", "scripts/phase2/validate_dataset.py")

KNOWN_ID = "KnownAugment"
KNOWN_NAME = "已知强化符文"
FIXED_TIME = "2026-08-25T12:00:00Z"


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def png_bytes(width: int = 2, height: int = 2, channel: int = 32) -> bytes:
    pixel = bytes((channel, 64, 96))
    scanlines = b"".join(b"\x00" + pixel * width for _row in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        VALIDATE.PNG_SIGNATURE
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(scanlines))
        + png_chunk(b"IEND", b"")
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase2-dataset-")
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "augment_offers"
        (self.root / "real").mkdir(parents=True)
        (self.root / "synthetic").mkdir()
        self.knowledge = self.workspace / "augments.zh-CN.json"
        write_json(
            self.knowledge,
            {
                "schema_version": 1,
                "locale": "zh-CN",
                "augments": [
                    {
                        "id": KNOWN_ID,
                        "display_name": KNOWN_NAME,
                        "default_name": "Known Augment",
                    }
                ],
            },
        )

    def cleanup(self) -> None:
        self.temporary.cleanup()

    def create_sample(
        self,
        sample_id: str = "sample-001",
        provenance: str = "real",
        *,
        annotation: bool = True,
        channel: int = 32,
        source_kind: str | None = None,
        derived_from_preview: bool = False,
        capture_boundary: str | None = None,
        benchmark_use: str | None = None,
        typed_boundary: bool = True,
    ) -> Path:
        sample = self.root / provenance / sample_id
        sample.mkdir()
        image = png_bytes(channel=channel)
        for filename in (
            "RAW.png",
            "LEFT_CARD.png",
            "CENTER_CARD.png",
            "RIGHT_CARD.png",
        ):
            (sample / filename).write_bytes(image)
        effective_source_kind = source_kind or (
            "windows_graphics_capture" if provenance == "real" else "synthetic"
        )
        metadata = {
            "schema_version": 1,
            "sample_id": sample_id,
            "provenance": provenance,
            "source": {
                "kind": effective_source_kind,
                "reference": "temporary-test-fixture",
            },
            "captured_at_utc": FIXED_TIME,
        }
        if typed_boundary:
            metadata.update(
                {
                    "derived_from_preview": derived_from_preview,
                    "capture_boundary": capture_boundary
                    or (
                        "original_wgc"
                        if provenance == "real"
                        else "synthetic"
                    ),
                    "benchmark_use": benchmark_use
                    or (
                        "original_wgc_metrics"
                        if provenance == "real"
                        else "excluded"
                    ),
                }
            )
        write_json(sample / "metadata.json", metadata)
        if annotation:
            write_json(
                sample / "annotation.json",
                {
                    "schema_version": 1,
                    "sample_id": sample_id,
                    "status": "complete",
                    "cards": {
                        side: {
                            "augment_id": KNOWN_ID,
                            "augment_name": KNOWN_NAME,
                            "valid": True,
                        }
                        for side in ANNOTATE.SIDES
                    },
                    "skip_reason": None,
                    "updated_at_utc": FIXED_TIME,
                },
            )
        return sample

    def write_skipped_annotation(self, sample: Path, reason: object) -> None:
        write_json(
            sample / "annotation.json",
            {
                "schema_version": 1,
                "sample_id": sample.name,
                "status": "skipped",
                "cards": {},
                "skip_reason": reason,
                "updated_at_utc": FIXED_TIME,
            },
        )


class DatasetToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def validate(self) -> dict[str, object]:
        return VALIDATE.validate_dataset(self.fixture.root, self.fixture.knowledge)

    def issue_codes(self, report: dict[str, object]) -> set[str]:
        issues = report["issues"]
        assert isinstance(issues, list)
        return {str(issue["code"]) for issue in issues}

    def warning_codes(self, report: dict[str, object]) -> list[str]:
        warnings = report["warnings"]
        assert isinstance(warnings, list)
        return [str(warning["code"]) for warning in warnings]

    def test_legal_dataset(self) -> None:
        sample = self.fixture.create_sample()
        annotation = json.loads((sample / "annotation.json").read_text(encoding="utf-8"))
        annotation["cards"]["center"] = {
            "augment_id": None,
            "augment_name": None,
            "valid": False,
        }
        write_json(sample / "annotation.json", annotation)

        report = self.validate()

        self.assertTrue(report["valid"], json.dumps(report, ensure_ascii=False, indent=2))
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["real_sample_count"], 1)
        self.assertEqual(report["synthetic_sample_count"], 0)
        self.assertEqual(report["unknown_sample_count"], 0)
        self.assertEqual(report["benchmark_eligible_sample_count"], 1)
        self.assertEqual(report["annotated_sample_count"], 1)
        self.assertEqual(report["unannotated_sample_count"], 0)
        self.assertEqual(report["valid_card_count"], 2)
        self.assertEqual(report["invalid_card_count"], 1)
        self.assertEqual(report["resolution_distribution"]["RAW"], {"2x2": 1})

    def test_unlabeled_sample_is_reported_honestly(self) -> None:
        self.fixture.create_sample(annotation=False)

        report = self.validate()

        self.assertFalse(report["valid"])
        self.assertIn("annotation_missing", self.issue_codes(report))
        self.assertEqual(report["annotated_sample_count"], 0)
        self.assertEqual(report["unannotated_sample_count"], 1)

    def test_real_directory_shape_with_skipped_samples_is_valid_and_excluded(
        self,
    ) -> None:
        complete_ids = (
            "real-preview-1787670257978-f010",
            "real-preview-1787670257978-f020",
            "real-preview-1787670572085-f0025",
            "sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6",
        )
        skipped_ids = (
            "sample_1787673873611452_f2_p30896_n0_0309c4711657f988",
            "sample_1787673874882312_f69_p30896_n1_77c5f72fbff68bea",
            "sample_1787673876034130_f134_p30896_n2_7d20cfaed875701d",
            "sample_1787673877200290_f199_p30896_n3_47d9c66595f20b78",
            "sample_1787673880393715_f382_p30896_n4_d56184b2891fa1ac",
        )
        skip_reason = "modal_disconnect_or_afk_occlusion"
        for index, sample_id in enumerate((*complete_ids, *skipped_ids)):
            preview_derived = sample_id.startswith("real-preview-")
            sample = self.fixture.create_sample(
                sample_id,
                channel=32 + index,
                source_kind=("manual_capture" if preview_derived else None),
                derived_from_preview=preview_derived,
                capture_boundary=("preview_derived" if preview_derived else None),
                benchmark_use=(
                    "real_scenario_calibration" if preview_derived else None
                ),
            )
            if sample_id in skipped_ids:
                self.fixture.write_skipped_annotation(sample, skip_reason)

        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(REPO_ROOT / "scripts/phase2/validate_dataset.py"),
                "--dataset-root",
                str(self.fixture.root),
                "--knowledge",
                str(self.fixture.knowledge),
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        report = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertTrue(report["valid"])
        self.assertEqual(report["sample_count"], 9)
        self.assertEqual(report["benchmark_eligible_sample_count"], 1)
        self.assertEqual(
            report["original_wgc_benchmark_eligible_sample_count"], 1
        )
        self.assertEqual(report["preview_derived_sample_count"], 3)
        self.assertEqual(
            report["preview_derived_benchmark_excluded_sample_count"], 3
        )
        self.assertEqual(
            report["capture_boundary_counts"],
            {
                "original_wgc": 6,
                "preview_derived": 3,
                "other_real_capture": 0,
                "synthetic": 0,
                "unknown": 0,
            },
        )
        self.assertEqual(report["skipped_excluded_sample_count"], 5)
        self.assertEqual(
            report["benchmark_excluded_counts"]["invalid_or_unannotated_real"], 8
        )
        self.assertEqual(report["annotated_sample_count"], 4)
        self.assertEqual(report["unannotated_sample_count"], 5)
        self.assertEqual(self.warning_codes(report).count("annotation_skipped"), 5)
        self.assertEqual(
            self.warning_codes(report).count(
                "preview_derived_excluded_from_original_wgc_benchmark"
            ),
            3,
        )
        self.assertEqual(report["issues"], [])

        preview_samples = {
            sample["sample_id"]: sample
            for sample in report["samples"]
            if sample["capture_boundary"] == "preview_derived"
        }
        self.assertEqual(set(preview_samples), set(complete_ids[:3]))
        for sample in preview_samples.values():
            self.assertTrue(sample["valid"])
            self.assertTrue(sample["annotated"])
            self.assertEqual(sample["provenance"], "real")
            self.assertEqual(sample["benchmark_use"], "real_scenario_calibration")
            self.assertFalse(sample["benchmark_eligible"])

        skipped_samples = {
            sample["sample_id"]: sample
            for sample in report["samples"]
            if sample["annotation_status"] == "skipped"
        }
        self.assertEqual(set(skipped_samples), set(skipped_ids))
        for sample in skipped_samples.values():
            self.assertTrue(sample["valid"])
            self.assertFalse(sample["annotated"])
            self.assertFalse(sample["benchmark_eligible"])
            self.assertEqual(sample["skip_reason"], skip_reason)
            self.assertEqual(
                [warning["code"] for warning in sample["warnings"]],
                ["annotation_skipped"],
            )
            self.assertIn(skip_reason, sample["warnings"][0]["message"])

    def test_each_preview_signal_forces_calibration_only_exclusion(self) -> None:
        cases = (
            (
                "derived-flag",
                {
                    "source_kind": "windows_graphics_capture",
                    "derived_from_preview": True,
                    "capture_boundary": "original_wgc",
                    "benchmark_use": "original_wgc_metrics",
                },
            ),
            (
                "manual-source",
                {
                    "source_kind": "manual_capture",
                    "capture_boundary": "original_wgc",
                    "benchmark_use": "original_wgc_metrics",
                },
            ),
            (
                "debug-source",
                {
                    "source_kind": "debug_preview",
                    "capture_boundary": "original_wgc",
                    "benchmark_use": "original_wgc_metrics",
                },
            ),
            (
                "typed-boundary",
                {
                    "source_kind": "windows_graphics_capture",
                    "capture_boundary": "preview_derived",
                    "benchmark_use": "original_wgc_metrics",
                },
            ),
        )
        for index, (sample_id, options) in enumerate(cases):
            self.fixture.create_sample(sample_id, channel=70 + index, **options)

        report = self.validate()

        self.assertTrue(report["valid"])
        self.assertEqual(report["preview_derived_sample_count"], len(cases))
        self.assertEqual(report["benchmark_eligible_sample_count"], 0)
        self.assertEqual(
            self.warning_codes(report).count(
                "preview_derived_excluded_from_original_wgc_benchmark"
            ),
            len(cases),
        )
        for sample in report["samples"]:
            self.assertEqual(sample["capture_boundary"], "preview_derived")
            self.assertEqual(sample["benchmark_use"], "real_scenario_calibration")
            self.assertFalse(sample["benchmark_eligible"])

    def test_legacy_wgc_metadata_is_inferred_with_warning(self) -> None:
        self.fixture.create_sample("legacy-wgc", typed_boundary=False)

        report = self.validate()

        self.assertTrue(report["valid"])
        self.assertEqual(report["benchmark_eligible_sample_count"], 1)
        self.assertIn("legacy_benchmark_boundary_inferred", self.warning_codes(report))
        sample = report["samples"][0]
        self.assertEqual(sample["capture_boundary"], "original_wgc")
        self.assertEqual(sample["benchmark_use"], "original_wgc_metrics")

    def test_typed_boundary_fields_reject_unknown_values(self) -> None:
        sample = self.fixture.create_sample()
        metadata = json.loads((sample / "metadata.json").read_text(encoding="utf-8"))
        metadata["capture_boundary"] = "preview-ish"
        metadata["benchmark_use"] = True
        metadata["derived_from_preview"] = "yes"
        write_json(sample / "metadata.json", metadata)

        report = self.validate()

        self.assertFalse(report["valid"])
        self.assertTrue(
            {
                "metadata_capture_boundary",
                "metadata_benchmark_use",
                "metadata_derived_from_preview",
            }.issubset(self.issue_codes(report))
        )

    def test_malformed_skip_and_in_progress_annotation_still_fail(self) -> None:
        skipped = self.fixture.create_sample("bad-skip", channel=90)
        self.fixture.write_skipped_annotation(skipped, "  ")
        in_progress = self.fixture.create_sample("unfinished", channel=91)
        annotation = json.loads(
            (in_progress / "annotation.json").read_text(encoding="utf-8")
        )
        annotation["status"] = "in_progress"
        del annotation["cards"]["right"]
        write_json(in_progress / "annotation.json", annotation)

        report = self.validate()

        self.assertFalse(report["valid"])
        self.assertIn("annotation_skip_reason", self.issue_codes(report))
        self.assertIn("annotation_incomplete", self.issue_codes(report))
        self.assertNotIn("annotation_skipped", self.warning_codes(report))
        self.assertEqual(report["benchmark_eligible_sample_count"], 0)
        self.assertEqual(report["skipped_excluded_sample_count"], 0)

    def test_unknown_augment_id(self) -> None:
        sample = self.fixture.create_sample()
        annotation = json.loads((sample / "annotation.json").read_text(encoding="utf-8"))
        annotation["cards"]["left"]["augment_id"] = "NotInCatalog"
        write_json(sample / "annotation.json", annotation)

        report = self.validate()

        self.assertFalse(report["valid"])
        self.assertIn("unknown_augment_id", self.issue_codes(report))

    def test_missing_card_file_and_card_count(self) -> None:
        sample = self.fixture.create_sample()
        (sample / "RIGHT_CARD.png").unlink()

        report = self.validate()

        codes = self.issue_codes(report)
        self.assertFalse(report["valid"])
        self.assertIn("missing_artifact", codes)
        self.assertIn("card_count", codes)

    def test_invalid_card_annotation_and_corrupt_card_image(self) -> None:
        sample = self.fixture.create_sample()
        annotation = json.loads((sample / "annotation.json").read_text(encoding="utf-8"))
        annotation["cards"]["center"]["valid"] = "yes"
        write_json(sample / "annotation.json", annotation)
        (sample / "LEFT_CARD.png").write_bytes(b"not-a-png")

        report = self.validate()

        codes = self.issue_codes(report)
        self.assertFalse(report["valid"])
        self.assertIn("invalid_card", codes)
        self.assertIn("invalid_image", codes)

    def test_duplicate_sample_id_and_content_hash(self) -> None:
        self.fixture.create_sample(
            sample_id="duplicate", provenance="real", channel=10
        )
        self.fixture.create_sample(
            sample_id="duplicate", provenance="synthetic", channel=11
        )
        self.fixture.create_sample(
            sample_id="same-images-real", provenance="real", channel=12
        )
        self.fixture.create_sample(
            sample_id="same-images-synthetic", provenance="synthetic", channel=12
        )

        report = self.validate()

        self.assertFalse(report["valid"])
        self.assertEqual(len(report["duplicate_sample_ids"]), 1)
        self.assertEqual(report["duplicate_sample_ids"][0]["sample_id"], "duplicate")
        self.assertEqual(len(report["duplicate_sample_hashes"]), 1)
        codes = self.issue_codes(report)
        self.assertIn("duplicate_sample_id", codes)
        self.assertIn("duplicate_sample_hash", codes)

    def test_metadata_provenance_mismatch(self) -> None:
        sample = self.fixture.create_sample(provenance="real")
        metadata = json.loads((sample / "metadata.json").read_text(encoding="utf-8"))
        metadata["provenance"] = "synthetic"
        write_json(sample / "metadata.json", metadata)

        report = self.validate()

        self.assertFalse(report["valid"])
        self.assertIn("metadata_provenance_mismatch", self.issue_codes(report))

    def test_atomic_write_preserves_original_on_replace_failure(self) -> None:
        sample = self.fixture.create_sample(annotation=False)
        target = sample / "annotation.json"
        original = {"message": "原始标签"}
        ANNOTATE.atomic_write_json(target, original, self.fixture.root)
        original_bytes = target.read_bytes()

        with mock.patch.object(ANNOTATE.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                ANNOTATE.atomic_write_json(
                    target, {"message": "不应覆盖"}, self.fixture.root
                )

        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), original)
        self.assertEqual(list(sample.glob(".annotation.json.*.tmp")), [])

    def test_noninteractive_set_clear_skip_and_path_boundary(self) -> None:
        sample = self.fixture.create_sample(annotation=False)
        for side in ANNOTATE.SIDES:
            ANNOTATE.set_card_annotation(
                self.fixture.root,
                sample,
                "sample-001",
                side,
                valid=True,
                augment_id=KNOWN_ID,
                augment_name=KNOWN_NAME,
            )
        annotation = json.loads((sample / "annotation.json").read_text(encoding="utf-8"))
        self.assertEqual(annotation["status"], "complete")

        annotation = ANNOTATE.clear_annotation(
            self.fixture.root, sample, "sample-001", "left"
        )
        self.assertEqual(annotation["status"], "in_progress")
        self.assertNotIn("left", annotation["cards"])
        annotation = ANNOTATE.skip_annotation(
            self.fixture.root, sample, "sample-001", "待人工复核"
        )
        self.assertEqual(annotation["status"], "skipped")
        self.assertEqual(annotation["skip_reason"], "待人工复核")

        outside = self.fixture.root.parent / "annotation.json"
        with self.assertRaises(ANNOTATE.DatasetError):
            ANNOTATE.atomic_write_json(outside, {}, self.fixture.root)
        self.assertFalse(outside.exists())

    def test_validator_cli_stdout_is_strict_json(self) -> None:
        sample = self.fixture.create_sample()
        command = [
            sys.executable,
            "-B",
            str(REPO_ROOT / "scripts/phase2/validate_dataset.py"),
            "--dataset-root",
            str(self.fixture.root),
            "--knowledge",
            str(self.fixture.knowledge),
        ]
        for expected_exit, expected_valid in ((0, True), (1, False)):
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                check=False,
                text=True,
                encoding="utf-8",
                capture_output=True,
            )
            self.assertEqual(
                completed.returncode,
                expected_exit,
                completed.stdout + completed.stderr,
            )
            self.assertEqual(completed.stderr, "")
            self.assertEqual(json.loads(completed.stdout)["valid"], expected_valid)
            if expected_valid:
                (sample / "annotation.json").unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
