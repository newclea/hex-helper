#!/usr/bin/env python3.11
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "benchmark_phase2.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

SPEC = importlib.util.spec_from_file_location("benchmark_phase2", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class Phase2BenchmarkTest(unittest.TestCase):
    def mixed_report(self) -> dict[str, object]:
        dataset = FIXTURES / "mixed_dataset.json"
        return BENCHMARK.build_report(BENCHMARK.load_dataset(dataset), dataset)

    def load_single_sample(
        self, metadata_updates: dict[str, object]
    ) -> dict[str, object]:
        document = json.loads(
            (FIXTURES / "mixed_dataset.json").read_text(encoding="utf-8")
        )
        sample = copy.deepcopy(document["samples"][0])
        sample["sample_id"] = "capture-boundary-case"
        sample["metadata"].update(metadata_updates)
        return {
            "samples": [BENCHMARK._validate_sample(sample, "test.samples[0]")],
            "source_state": "test_fixture",
            "raw_asset_count": 1,
        }

    def test_core_metrics_filter_synthetic_and_exclude_invalid_card(self) -> None:
        report = self.mixed_report()

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["input"]["input_sample_count"], 5)
        self.assertEqual(report["input"]["excluded_synthetic_sample_count"], 1)
        self.assertEqual(report["input"]["excluded_preview_derived_sample_count"], 0)
        self.assertEqual(
            report["input"]["benchmark_eligible_original_wgc_sample_count"], 4
        )
        self.assertFalse(report["scope"]["synthetic_included"])
        self.assertFalse(report["scope"]["preview_derived_included"])
        self.assertEqual(report["sample_count"], 4)
        self.assertEqual(report["card_count"], 11)
        self.assertEqual(report["three_card_sample_count"], 3)

        self.assertEqual(
            report["metric_counts"]["screen_detection_accuracy"],
            {"numerator": 3, "denominator": 4},
        )
        self.assertAlmostEqual(report["screen_detection_accuracy"], 3 / 4)
        for metric in ("ocr_accuracy", "icon_accuracy"):
            self.assertEqual(
                report["metric_counts"][metric],
                {"numerator": 9, "denominator": 10},
            )
            self.assertAlmostEqual(report[metric], 9 / 10)
        for metric in ("card_recognition_accuracy",):
            self.assertEqual(
                report["metric_counts"][metric],
                {"numerator": 9, "denominator": 11},
            )
            self.assertAlmostEqual(report[metric], 9 / 11)
        self.assertEqual(
            report["metric_counts"]["three_cards_all_correct_rate"],
            {"numerator": 1, "denominator": 3},
        )
        self.assertAlmostEqual(report["three_cards_all_correct_rate"], 1 / 3)
        self.assertAlmostEqual(report["unknown_rate"], 1 / 11)
        self.assertAlmostEqual(report["false_match_rate"], 1 / 11)

        # The invalid CENTER card contributes to neither per-card nor all-three
        # denominators. Its deliberately wrong prediction must not be a false match.
        self.assertEqual(
            report["metric_counts"]["false_match_rate"],
            {"numerator": 1, "denominator": 11},
        )

    def test_ocr_title_normalization_and_independent_stage_denominators(self) -> None:
        document = json.loads(
            (FIXTURES / "mixed_dataset.json").read_text(encoding="utf-8")
        )
        sample = copy.deepcopy(document["samples"][0])
        sample["sample_id"] = "normalized-independent-stages"
        cards = sample["recognition_result"]["cards"]
        cards[0]["ocr_text"] = "Ａ l，p.h-a！"
        cards[0]["ocr_state"] = "RECOGNIZED"
        cards[0]["ocr_backend"] = "fixture-ocr"
        cards[1]["ocr_text"] = None
        cards[1]["ocr_state"] = "UNAVAILABLE"
        cards[1]["icon_id"] = None
        cards[1]["icon_state"] = "UNKNOWN"
        cards[1]["icon_confidence"] = 0.75
        loaded = {
            "samples": [BENCHMARK._validate_sample(sample, "test.samples[0]")],
            "source_state": "test_fixture",
            "raw_asset_count": 1,
        }

        report = BENCHMARK.build_report(loaded, "test-fixture")

        self.assertEqual(
            report["metric_counts"]["ocr_accuracy"],
            {"numerator": 2, "denominator": 2},
        )
        self.assertEqual(
            report["metric_counts"]["icon_accuracy"],
            {"numerator": 2, "denominator": 3},
        )
        self.assertFalse(report["ocr_normalization"]["fuzzy_matching"])

    def test_preview_derived_fixture_is_excluded_from_all_metrics(self) -> None:
        fixture_path = FIXTURES / "mixed_dataset.json"
        document = json.loads(fixture_path.read_text(encoding="utf-8"))
        preview_sample = copy.deepcopy(document["samples"][0])
        preview_sample["sample_id"] = "preview-all-correct-must-not-count"
        preview_sample["metadata"].update(
            {
                "source": {"kind": "manual_capture"},
                "derived_from_preview": True,
                "capture_boundary": "preview_derived",
                "benchmark_use": "original_wgc_metrics",
            }
        )
        document["samples"].append(preview_sample)

        with tempfile.TemporaryDirectory(prefix="phase2-preview-benchmark-") as temporary:
            dataset = Path(temporary) / "dataset.json"
            dataset.write_text(
                json.dumps(document, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
            )
            report = BENCHMARK.build_report(BENCHMARK.load_dataset(dataset), dataset)

        baseline = self.mixed_report()
        self.assertEqual(report["input"]["input_sample_count"], 6)
        self.assertEqual(report["sample_count"], baseline["sample_count"])
        self.assertEqual(report["card_count"], baseline["card_count"])
        self.assertEqual(
            report["metric_counts"], baseline["metric_counts"]
        )
        self.assertEqual(report["input"]["excluded_preview_derived_sample_count"], 1)
        self.assertEqual(report["input"]["excluded_other_real_sample_count"], 0)
        preview_warnings = [
            warning
            for warning in report["boundary_warnings"]
            if warning["sample_id"] == preview_sample["sample_id"]
        ]
        self.assertEqual(len(preview_warnings), 1)
        self.assertEqual(
            preview_warnings[0]["code"],
            "preview_derived_excluded_from_original_wgc_benchmark",
        )

    def test_each_preview_signal_overrides_real_provenance(self) -> None:
        cases = (
            {"derived_from_preview": True},
            {"source": {"kind": "manual_capture"}},
            {"source": {"kind": "debug_preview"}},
            {"capture_boundary": "preview_derived"},
        )
        for metadata_updates in cases:
            with self.subTest(metadata_updates=metadata_updates):
                loaded = self.load_single_sample(metadata_updates)
                report = BENCHMARK.build_report(loaded, "test-fixture")
                self.assertEqual(report["input"]["input_sample_count"], 1)
                self.assertEqual(
                    report["input"]["excluded_preview_derived_sample_count"], 1
                )
                self.assertEqual(report["sample_count"], 0)
                self.assertIsNone(report["card_recognition_accuracy"])
                self.assertEqual(
                    report["boundary_warnings"][0]["code"],
                    "preview_derived_excluded_from_original_wgc_benchmark",
                )

    def test_clear_direct_wgc_sample_is_eligible(self) -> None:
        loaded = self.load_single_sample(
            {
                "source": {"kind": "windows_graphics_capture"},
                "derived_from_preview": False,
                "capture_boundary": "original_wgc",
                "benchmark_use": "original_wgc_metrics",
            }
        )

        report = BENCHMARK.build_report(loaded, "test-fixture")

        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["input"]["excluded_preview_derived_sample_count"], 0)
        self.assertEqual(report["input"]["excluded_other_real_sample_count"], 0)
        self.assertEqual(report["boundary_warnings"], [])

    def test_p95_distributions_failures_and_strata(self) -> None:
        report = self.mixed_report()

        self.assertEqual(report["average_latency_ms"], 25.0)
        self.assertEqual(report["p95_latency_ms"], 40.0)
        self.assertEqual(report["latency_percentile_method"], "nearest_rank")
        self.assertEqual(
            report["resolution_distribution"],
            {"1920x1080": 2, "2560x1440": 2},
        )
        self.assertEqual(report["ui_scale_distribution"], {"100%": 2, "125%": 2})
        self.assertEqual(len(report["stratified"]["resolution"]), 2)
        self.assertEqual(len(report["stratified"]["ui_scale"]), 2)
        self.assertEqual(len(report["stratified"]["ocr_preprocessing_variant"]), 3)

        variant_keys = {
            row["key"]
            for row in report["stratified"]["ocr_preprocessing_variant"]
        }
        self.assertTrue(any("grayscale=false" in key and "scale=2x" in key for key in variant_keys))
        self.assertTrue(any("contrast=1.25" in key and "scale=2x" in key for key in variant_keys))
        self.assertTrue(any("threshold=128" in key and "scale=3x" in key for key in variant_keys))

        failures = {failure["sample_id"]: failure for failure in report["typical_failures"]}
        self.assertEqual(set(failures), {"one-wrong", "unknown-card", "invalid-card"})
        self.assertIn(
            "false_match",
            {reason["code"] for reason in failures["one-wrong"]["reasons"]},
        )
        self.assertIn(
            "unknown",
            {reason["code"] for reason in failures["unknown-card"]["reasons"]},
        )
        self.assertIn(
            "screen_detection_mismatch",
            {reason["code"] for reason in failures["invalid-card"]["reasons"]},
        )

    def test_empty_input_succeeds_without_fabricated_rates(self) -> None:
        dataset = FIXTURES / "empty_dataset.json"
        report = BENCHMARK.build_report(BENCHMARK.load_dataset(dataset), dataset)

        self.assertEqual(report["status"], "insufficient_real_data")
        self.assertEqual(report["sample_count"], 0)
        self.assertEqual(report["card_count"], 0)
        for metric in (*BENCHMARK.RATE_METRICS, *BENCHMARK.LATENCY_METRICS):
            self.assertIsNone(report[metric], metric)

        strict_json = BENCHMARK._strict_json_text(report)
        self.assertNotIn("NaN", strict_json)
        self.assertNotIn("Infinity", strict_json)
        parsed = json.loads(strict_json)
        self.assertIsNone(parsed["three_cards_all_correct_rate"])

    def test_minimum_sample_status_uses_fully_annotated_real_samples(self) -> None:
        dataset = FIXTURES / "mixed_dataset.json"
        loaded = BENCHMARK.load_dataset(dataset)
        report = BENCHMARK.build_report(
            loaded, dataset, minimum_real_samples=4
        )

        self.assertEqual(report["sample_count"], 4)
        self.assertEqual(report["three_card_sample_count"], 3)
        self.assertEqual(report["status"], "insufficient_real_data")
        self.assertIn(
            "three_card_real_sample_count_below_minimum:3<4",
            report["status_reasons"],
        )

    def test_split_jsonl_schema_joins_metadata_annotation_and_result(self) -> None:
        dataset = FIXTURES / "joined"
        loaded = BENCHMARK.load_dataset(dataset)
        report = BENCHMARK.build_report(loaded, dataset)

        self.assertEqual(loaded["source_state"], "split_jsonl_loaded")
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["card_count"], 3)
        self.assertEqual(report["three_cards_all_correct_rate"], 1.0)
        self.assertEqual(report["average_latency_ms"], 12.5)

    def test_cli_writes_parseable_json_and_readable_markdown(self) -> None:
        dataset = FIXTURES / "mixed_dataset.json"
        with tempfile.TemporaryDirectory(prefix="phase2-benchmark-test-") as temporary:
            json_output = Path(temporary) / "report.json"
            markdown_output = Path(temporary) / "report.md"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "--dataset",
                    str(dataset),
                    "--json-output",
                    str(json_output),
                    "--markdown-output",
                    str(markdown_output),
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            parsed = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertEqual(parsed["schema_version"], "phase2_benchmark_report_v1")
            markdown = markdown_output.read_text(encoding="utf-8")
            self.assertIn("## Denominator Semantics", markdown)
            self.assertIn("UNKNOWN is incorrect", markdown)
            self.assertIn("OCR Preprocessing Variant", markdown)
            self.assertNotIn("synthetic-all-correct", markdown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
