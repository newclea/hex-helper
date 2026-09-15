#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
ANNOTATE_SCRIPT = REPO_ROOT / "scripts" / "phase2" / "annotate_dataset.py"
VALIDATE_SCRIPT = REPO_ROOT / "scripts" / "phase2" / "validate_dataset.py"
BENCHMARK_SCRIPT = REPO_ROOT / "scripts" / "benchmark_phase2.py"
CANONICAL_FILES = {
    "RAW.png",
    "LEFT_CARD.png",
    "CENTER_CARD.png",
    "RIGHT_CARD.png",
    "metadata.json",
}


def load_script(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


BENCHMARK = load_script("phase2_contract_benchmark", BENCHMARK_SCRIPT)


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def strict_loads(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )


class CollectionDatasetContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configured = os.environ.get("PHASE2_COLLECTOR_FIXTURE")
        cls.fixture_exe = (
            Path(configured)
            if configured
            else REPO_ROOT
            / "outputs"
            / "tmp"
            / "phase2_contract"
            / "collector_contract_fixture.exe"
        )
        if not cls.fixture_exe.is_file():
            raise unittest.SkipTest(
                "collector fixture is not built; run tests/phase2_contract/run_contract.ps1"
            )

    def run_cli(self, command: list[str], *, expected_exit: int = 0) -> subprocess.CompletedProcess[str]:
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
        return completed

    def collect(self, root: Path, provenance: str) -> tuple[str, Path]:
        bucket = root / provenance
        completed = self.run_cli([str(self.fixture_exe), str(bucket), provenance])
        self.assertEqual(completed.stderr, "")
        sample_id = completed.stdout.strip()
        sample_dir = bucket / sample_id
        self.assertRegex(sample_id, r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
        self.assertEqual({path.name for path in sample_dir.iterdir()}, CANONICAL_FILES)
        return sample_id, sample_dir

    def annotate_three_cards(self, root: Path, sample_id: str) -> dict[str, object]:
        annotation: dict[str, object] | None = None
        for side in ("left", "center", "right"):
            completed = self.run_cli(
                [
                    sys.executable,
                    "-B",
                    str(ANNOTATE_SCRIPT),
                    "--dataset-root",
                    str(root),
                    "set",
                    sample_id,
                    side,
                    "--valid",
                    "false",
                ]
            )
            self.assertEqual(completed.stderr, "")
            parsed = strict_loads(completed.stdout)
            self.assertIsInstance(parsed, dict)
            annotation = parsed  # type: ignore[assignment]
        assert annotation is not None
        self.assertEqual(annotation["status"], "complete")
        self.assertEqual(set(annotation["cards"]), {"left", "center", "right"})  # type: ignore[arg-type]
        return annotation

    def validate_cli(
        self, root: Path, knowledge: Path, *, expected_exit: int
    ) -> dict[str, object]:
        completed = self.run_cli(
            [
                sys.executable,
                "-B",
                str(VALIDATE_SCRIPT),
                "--dataset-root",
                str(root),
                "--knowledge",
                str(knowledge),
            ],
            expected_exit=expected_exit,
        )
        self.assertEqual(completed.stderr, "")
        parsed = strict_loads(completed.stdout)
        self.assertIsInstance(parsed, dict)
        return parsed  # type: ignore[return-value]

    def benchmark_record(self, sample_dir: Path) -> dict[str, object]:
        metadata = strict_loads((sample_dir / "metadata.json").read_text(encoding="utf-8"))
        annotation = strict_loads((sample_dir / "annotation.json").read_text(encoding="utf-8"))
        assert isinstance(metadata, dict)
        assert isinstance(annotation, dict)
        cards = annotation["cards"]
        assert isinstance(cards, dict)
        return {
            "sample_id": metadata["sample_id"],
            "metadata": {
                "provenance": metadata["provenance"],
                "resolution": {
                    "width": metadata["resolution"]["width"],
                    "height": metadata["resolution"]["height"],
                },
                "ui_scale": metadata["ui_scale"],
                "ocr_preprocessing_variant": {
                    "grayscale": False,
                    "contrast": None,
                    "threshold": None,
                    "scale": "1x",
                    "variant_id": "contract-no-preprocessing",
                },
            },
            "annotation": {
                "screen_present": False,
                "cards": [
                    {"slot": side.upper(), "valid": cards[side]["valid"]}
                    for side in ("left", "center", "right")
                ],
            },
            "recognition_result": {
                "screen_detected": False,
                "latency_ms": 0.0,
                "cards": [],
            },
        }

    def test_collector_annotation_validator_and_real_only_benchmark_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-contract-") as temporary:
            root = Path(temporary) / "augment_offers"
            knowledge = Path(temporary) / "augments.zh-CN.json"
            knowledge.write_text(
                json.dumps(
                    {"schema_version": 1, "locale": "zh-CN", "augments": []},
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )

            samples: dict[str, tuple[str, Path]] = {}
            for provenance in ("real", "synthetic", "unknown"):
                samples[provenance] = self.collect(root, provenance)
                sample_id, sample_dir = samples[provenance]
                metadata = strict_loads(
                    (sample_dir / "metadata.json").read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["schema_version"], 1)  # type: ignore[index]
                self.assertEqual(metadata["sample_id"], sample_id)  # type: ignore[index]
                self.assertEqual(metadata["provenance"], provenance)  # type: ignore[index]
                self.assertTrue(metadata["source"]["reference"])  # type: ignore[index]
                self.annotate_three_cards(root, sample_id)

            report = self.validate_cli(root, knowledge, expected_exit=0)
            self.assertTrue(report["valid"])
            self.assertEqual(
                report["provenance_counts"],
                {"real": 1, "synthetic": 1, "unknown": 1},
            )
            self.assertEqual(report["benchmark_eligible_sample_count"], 1)
            self.assertEqual(
                report["benchmark_excluded_counts"],
                {
                    "synthetic": 1,
                    "unknown": 1,
                    "invalid_or_unannotated_real": 0,
                },
            )

            benchmark_samples = [
                self.benchmark_record(samples[provenance][1])
                for provenance in ("real", "synthetic")
            ]
            unknown_id = samples["unknown"][0]
            self.assertNotIn(
                unknown_id, {sample["sample_id"] for sample in benchmark_samples}
            )
            benchmark_input = Path(temporary) / "benchmark_input.json"
            benchmark_input.write_text(
                json.dumps(
                    {
                        "schema_version": "phase2_benchmark_input_v1",
                        "samples": benchmark_samples,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
            loaded = BENCHMARK.load_dataset(benchmark_input)
            benchmark_report = BENCHMARK.build_report(loaded, benchmark_input)
            self.assertEqual(benchmark_report["input"]["input_sample_count"], 2)
            self.assertEqual(benchmark_report["sample_count"], 1)
            self.assertEqual(
                benchmark_report["input"]["excluded_synthetic_sample_count"], 1
            )
            self.assertEqual(benchmark_report["scope"]["provenance"], "real")

            synthetic_id, synthetic_dir = samples["synthetic"]
            for side in ("LEFT", "CENTER", "RIGHT"):
                (synthetic_dir / f"{side}_CARD.png").rename(
                    synthetic_dir / f"{side}.png"
                )
            legacy_report = self.validate_cli(root, knowledge, expected_exit=0)
            warning_codes = {
                warning["code"] for warning in legacy_report["warnings"]
            }
            self.assertIn("deprecated_artifact_names", warning_codes)
            legacy_sample = next(
                sample
                for sample in legacy_report["samples"]
                if sample["sample_id"] == synthetic_id
            )
            self.assertEqual(legacy_sample["artifact_naming"], "legacy_deprecated")

            legacy_show = self.run_cli(
                [
                    sys.executable,
                    "-B",
                    str(ANNOTATE_SCRIPT),
                    "--dataset-root",
                    str(root),
                    "show",
                    synthetic_id,
                    "--json",
                ]
            )
            legacy_payload = strict_loads(legacy_show.stdout)
            self.assertEqual(legacy_payload["artifact_naming"], "legacy_deprecated")  # type: ignore[index]
            self.assertEqual(
                {warning["code"] for warning in legacy_payload["warnings"]},  # type: ignore[index]
                {"deprecated_artifact_names"},
            )

            shutil.copyfile(
                synthetic_dir / "LEFT.png", synthetic_dir / "LEFT_CARD.png"
            )
            mixed_report = self.validate_cli(root, knowledge, expected_exit=1)
            issue_codes = {issue["code"] for issue in mixed_report["issues"]}
            self.assertIn("mixed_artifact_names", issue_codes)

            show = self.run_cli(
                [
                    sys.executable,
                    "-B",
                    str(ANNOTATE_SCRIPT),
                    "--dataset-root",
                    str(root),
                    "show",
                    synthetic_id,
                    "--json",
                ],
                expected_exit=1,
            )
            self.assertIn("canonical", show.stderr)
            self.assertIn("deprecated", show.stderr)

    def test_validator_rejects_duplicate_keys_and_nonfinite_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase2-strict-json-") as temporary:
            root = Path(temporary) / "augment_offers"
            (root / "real").mkdir(parents=True)
            (root / "synthetic").mkdir()
            knowledge = Path(temporary) / "augments.zh-CN.json"
            knowledge.write_text(
                '{"schema_version":1,"locale":"zh-CN","augments":[]}\n',
                encoding="utf-8",
            )
            sample_id, sample_dir = self.collect(root, "unknown")
            self.annotate_three_cards(root, sample_id)
            metadata_path = sample_dir / "metadata.json"
            original = metadata_path.read_text(encoding="utf-8")

            duplicate = original.replace(
                '{"schema":', '{"schema_version":1,"schema":', 1
            )
            metadata_path.write_text(duplicate, encoding="utf-8")
            duplicate_report = self.validate_cli(root, knowledge, expected_exit=1)
            duplicate_codes = {
                issue["code"] for issue in duplicate_report["issues"]
            }
            self.assertIn("metadata_unreadable", duplicate_codes)

            nonfinite = original.replace(
                '"schema_version":1', '"schema_version":NaN', 1
            )
            metadata_path.write_text(nonfinite, encoding="utf-8")
            nonfinite_report = self.validate_cli(root, knowledge, expected_exit=1)
            nonfinite_codes = {
                issue["code"] for issue in nonfinite_report["issues"]
            }
            self.assertIn("metadata_unreadable", nonfinite_codes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
