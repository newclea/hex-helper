# Phase 2 Real Dataset Benchmark

- Status: `ok`
- Input source: `F:\Realworld\lol\outputs\tmp\phase2_final_replay_benchmark2\benchmark_input`
- Input state: `split_jsonl_loaded`
- Evaluation scope: `provenance=real`, `capture_boundary=original_wgc`, `benchmark_use=original_wgc_metrics`.
- Preview-derived and other non-original-WGC real samples are excluded from every metric.
- Raw assets observed: 3
- Schema-valid input samples: 1
- Excluded synthetic samples: 0
- Excluded preview-derived real samples: 0
- Excluded other real samples: 0
- Boundary warnings: 0

## Counts

- Benchmark-eligible original-WGC samples: 1
- Valid annotated cards: 3
- Real samples with all three card annotations valid: 1

## Metrics

| Metric | Value | Numerator | Denominator |
|---|---:|---:|---:|
| `screen_detection_accuracy` | 1.000000 (100.00%) | 1 | 1 |
| `ocr_accuracy` | 1.000000 (100.00%) | 3 | 3 |
| `icon_accuracy` | 0.000000 (0.00%) | 0 | 3 |
| `card_recognition_accuracy` | 1.000000 (100.00%) | 3 | 3 |
| `three_cards_all_correct_rate` | 1.000000 (100.00%) | 1 | 1 |
| `unknown_rate` | 0.000000 (0.00%) | 0 | 3 |
| `false_match_rate` | 0.000000 (0.00%) | 0 | 3 |
| `average_latency_ms` | 934.812 | — | 1 samples |
| `p95_latency_ms` | 934.812 | — | 1 samples |

## Resolution Distribution

| Value | Samples |
|---|---:|
| `2560x1600` | 1 |

## UI Scale Distribution

| Value | Samples |
|---|---:|
| `unknown` | 1 |

## Stratified by Resolution

| Stratum | Samples | Cards | Screen | OCR | Icon | Card | All 3 | Unknown | False match | Avg ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `2560x1600` | 1 | 3 | 1.000000 (100.00%) | 1.000000 (100.00%) | 0.000000 (0.00%) | 1.000000 (100.00%) | 1.000000 (100.00%) | 0.000000 (0.00%) | 0.000000 (0.00%) | 934.812 | 934.812 |

## Stratified by UI Scale

| Stratum | Samples | Cards | Screen | OCR | Icon | Card | All 3 | Unknown | False match | Avg ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `unknown` | 1 | 3 | 1.000000 (100.00%) | 1.000000 (100.00%) | 0.000000 (0.00%) | 1.000000 (100.00%) | 1.000000 (100.00%) | 0.000000 (0.00%) | 0.000000 (0.00%) | 934.812 | 934.812 |

## Stratified by OCR Preprocessing Variant

| Stratum | Samples | Cards | Screen | OCR | Icon | Card | All 3 | Unknown | False match | Avg ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `grayscale=true;contrast=none;threshold=none;scale=1x;variant_id=product-default-gray-1x` | 1 | 3 | 1.000000 (100.00%) | 1.000000 (100.00%) | 0.000000 (0.00%) | 1.000000 (100.00%) | 1.000000 (100.00%) | 0.000000 (0.00%) | 0.000000 (0.00%) | 934.812 | 934.812 |

## Typical Failures

- `sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6`: {"code": "icon_mismatch", "expected_icon_id": "ARAM_Impassable", "predicted_icon_id": null, "predicted_icon_state": "UNKNOWN", "slot": "LEFT"}; {"code": "icon_mismatch", "expected_icon_id": "Equilibrium", "predicted_icon_id": null, "predicted_icon_state": "UNKNOWN", "slot": "CENTER"}; {"code": "icon_mismatch", "expected_icon_id": "ARAM_CelestialBody", "predicted_icon_id": null, "predicted_icon_state": "UNKNOWN", "slot": "RIGHT"}

## Denominator Semantics

- `sample_count`: All schema-valid original-WGC benchmark-eligible real samples; preview-derived, other-real, and synthetic samples are excluded.
- `card_count`: All valid=true card annotations on selected original-WGC samples; invalid cards are excluded.
- `screen_detection_accuracy`: Correct screen_present versus screen_detected decisions / all selected original-WGC samples.
- `ocr_accuracy`: NFKC+casefold OCR title matches after removing Unicode whitespace and punctuation / valid annotations whose OCR stage is not UNAVAILABLE. OCR UNKNOWN is incorrect; OCR UNAVAILABLE is excluded.
- `icon_accuracy`: Exact MATCHED icon_id matches / valid annotations whose icon stage is not UNAVAILABLE. Icon UNKNOWN is incorrect; icon UNAVAILABLE is excluded.
- `card_recognition_accuracy`: Exact augment_id matches / valid card annotations. UNKNOWN is incorrect.
- `three_cards_all_correct_rate`: Samples with all three augment_id predictions correct / samples where LEFT, CENTER, and RIGHT annotations are all valid.
- `unknown_rate`: Missing or explicit UNKNOWN card predictions / valid card annotations; UNKNOWN is not correct.
- `false_match_rate`: Incorrect non-UNKNOWN augment_id predictions / valid card annotations.
- `average_latency_ms`: Arithmetic mean over selected original-WGC sample latency_ms values.
- `p95_latency_ms`: Nearest-rank P95 over selected original-WGC sample latency_ms values: sorted[ceil(0.95*n)-1].

## Capture Boundary Warnings

No capture-boundary warnings.

## Interpretation Boundary

- Rates with a zero denominator are `N/A` in Markdown and `null` in JSON; zero is never fabricated.
- P95 uses nearest rank: `sorted[ceil(0.95*n)-1]`.
- OCR preprocessing strata are descriptive only. The benchmark does not select grayscale, contrast, threshold, 2x, or 3x parameters for the caller.
