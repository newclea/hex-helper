# Phase2 OCR/Icon Stage Machine Evidence Final Fix

## Verdict

`PASS`

The unchanged final executable was replayed against the real Phase2 dataset. The benchmark-eligible original-WGC n5 sample now reports product SQLite component evidence as OCR `3/3`, icon `0/3`, and final augment recognition `3/3`.

## Machine-readable attestation

```json
{
  "schema_version": "phase2_ocr_stage_export_fix_report_v1",
  "status": "PASS",
  "execution": {
    "mode": "KIWI",
    "headless": true,
    "dry_run": true,
    "dataset_mutated": false,
    "executable_modified": false,
    "executable": "F:\\Realworld\\lol\\outputs\\tmp\\build_phase2_final_verify\\bin\\lol_augment_assistant.exe",
    "executable_sha256": "B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075"
  },
  "prediction_source": "session_start.database:recognition_results.raw_json",
  "real_benchmark": {
    "sample_id": "sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6",
    "session_id": "phase1-fa55379851eb5bea67e7f6c503588910",
    "database_sha256": "07C0A7EF9BA592F8ED889BC4A54D37F6B5BDA63344F0628CC30CC389972471E9",
    "recognition_row_count": 3,
    "ocr_accuracy": {"numerator": 3, "denominator": 3, "value": 1.0},
    "icon_accuracy": {"numerator": 0, "denominator": 3, "value": 0.0},
    "final_accuracy": {"numerator": 3, "denominator": 3, "value": 1.0},
    "three_cards_all_correct": {"numerator": 1, "denominator": 1, "value": 1.0},
    "prediction_failures": 0,
    "cards": [
      {
        "slot": "LEFT",
        "raw_ocr_text": "不 动 如 山",
        "ocr_backend": "windows_media_ocr:zh-CN;title_preprocess=gray_1x;strategy=single_default",
        "ocr_state": "RECOGNIZED",
        "final_augment_id": "ARAM_Impassable",
        "icon_state": "UNKNOWN",
        "icon_id": null,
        "icon_confidence": 0.75
      },
      {
        "slot": "CENTER",
        "raw_ocr_text": "我 们 的 治 疗",
        "ocr_backend": "windows_media_ocr:zh-CN;title_preprocess=gray_1x;strategy=single_default",
        "ocr_state": "RECOGNIZED",
        "final_augment_id": "Equilibrium",
        "icon_state": "UNKNOWN",
        "icon_id": null,
        "icon_confidence": 0.765625
      },
      {
        "slot": "RIGHT",
        "raw_ocr_text": "星 界 躯 体",
        "ocr_backend": "windows_media_ocr:zh-CN;title_preprocess=gray_1x;strategy=single_default",
        "ocr_state": "RECOGNIZED",
        "final_augment_id": "ARAM_CelestialBody",
        "icon_state": "UNKNOWN",
        "icon_id": null,
        "icon_confidence": 0.828125
      }
    ]
  },
  "ocr_normalization": {
    "unicode_form": "NFKC",
    "casefold": true,
    "remove_unicode_whitespace": true,
    "remove_unicode_punctuation_categories": "P*",
    "fuzzy_matching": false
  },
  "stage_denominators": {
    "UNAVAILABLE": "excluded from that independent stage denominator",
    "UNKNOWN": "included and incorrect"
  },
  "tests": {
    "producer_and_benchmark": {"passed": 18, "failed": 0},
    "fake_sqlite_coverage": [
      "ocr_and_icon_3_of_3",
      "icon_UNKNOWN",
      "malformed_raw_json",
      "database_path_escape",
      "database_stdout_inconsistency"
    ]
  }
}
```

## Fix boundary

Only the replay producer, benchmark, their two test modules, and this report were changed. The producer opens the database reported by `session_start.database` in SQLite read-only mode, requires it to resolve inside the per-sample temporary workspace, parses the three slot rows from `recognition_results.raw_json`, and rejects malformed or inconsistent database/stdout evidence as a prediction failure. OCR text is never synthesized from annotation, display name, final augment ID, or truth data.

The benchmark compares product `raw_text` against the annotation title after the declared normalization only. No fuzzy matching or augment-ID-derived OCR is used. Icon `UNKNOWN` remains independently evaluable and therefore scored `0/3`; only explicit stage `UNAVAILABLE` is omitted from that stage denominator.

## Verification commands

```text
python -m unittest tests.phase2_benchmark.test_benchmark_phase2 tests.phase2_replay_benchmark.test_dataset_replay_benchmark -v
Result: 18 tests passed

python -B scripts/phase2/run_dataset_replay.py --exe outputs/tmp/build_phase2_final_verify/bin/lol_augment_assistant.exe --dataset-root data/dataset/augment_offers --knowledge data/knowledge/augments.zh-CN.json --dry-run --timeout-seconds 30 --minimum-real-samples 1
Result: exit 0; producer status ok; benchmark status ok; OCR 3/3; icon 0/3; final 3/3
```
