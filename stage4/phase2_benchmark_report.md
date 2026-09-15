# Phase 2 Real Dataset Benchmark

- Status: `insufficient_real_data`
- Input source: `outputs\phase2_emergency_real`
- Input state: `no_benchmark_records`
- Evaluation scope: `provenance=real`; synthetic samples are excluded.
- Raw assets observed: 152
- Schema-valid input samples: 0
- Excluded synthetic samples: 0

## Counts

- Real samples: 0
- Valid annotated cards: 0
- Real samples with all three card annotations valid: 0

## Metrics

| Metric | Value | Numerator | Denominator |
|---|---:|---:|---:|
| `screen_detection_accuracy` | N/A | 0 | 0 |
| `ocr_accuracy` | N/A | 0 | 0 |
| `icon_accuracy` | N/A | 0 | 0 |
| `card_recognition_accuracy` | N/A | 0 | 0 |
| `three_cards_all_correct_rate` | N/A | 0 | 0 |
| `unknown_rate` | N/A | 0 | 0 |
| `false_match_rate` | N/A | 0 | 0 |
| `average_latency_ms` | N/A | — | 0 samples |
| `p95_latency_ms` | N/A | — | 0 samples |

## Resolution Distribution

No selected real samples.

## UI Scale Distribution

No selected real samples.

## Stratified by Resolution

No selected real samples.

## Stratified by UI Scale

No selected real samples.

## Stratified by OCR Preprocessing Variant

No selected real samples.

## Typical Failures

No model failures in the evaluated real samples, or no evaluable real samples.

## Denominator Semantics

- `sample_count`: All schema-valid input samples with provenance=real; synthetic samples are excluded.
- `card_count`: All valid=true card annotations on selected real samples; invalid cards are excluded.
- `screen_detection_accuracy`: Correct screen_present versus screen_detected decisions / all selected real samples.
- `ocr_accuracy`: Exact OCR text matches / valid card annotations. UNKNOWN is incorrect.
- `icon_accuracy`: Exact icon_id matches / valid card annotations. UNKNOWN is incorrect.
- `card_recognition_accuracy`: Exact augment_id matches / valid card annotations. UNKNOWN is incorrect.
- `three_cards_all_correct_rate`: Samples with all three augment_id predictions correct / samples where LEFT, CENTER, and RIGHT annotations are all valid.
- `unknown_rate`: Missing or explicit UNKNOWN card predictions / valid card annotations; UNKNOWN is not correct.
- `false_match_rate`: Incorrect non-UNKNOWN augment_id predictions / valid card annotations.
- `average_latency_ms`: Arithmetic mean over selected real sample latency_ms values.
- `p95_latency_ms`: Nearest-rank P95 over selected real sample latency_ms values: sorted[ceil(0.95*n)-1].

## Interpretation Boundary

- Rates with a zero denominator are `N/A` in Markdown and `null` in JSON; zero is never fabricated.
- P95 uses nearest rank: `sorted[ceil(0.95*n)-1]`.
- OCR preprocessing strata are descriptive only. The benchmark does not select grayscale, contrast, threshold, 2x, or 3x parameters for the caller.

## Status Reasons

- `three_card_real_sample_count_below_minimum:0<1`
- `no_real_sample_has_three_valid_card_annotations`
- `input_source_state:no_benchmark_records`
