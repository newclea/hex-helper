# Phase 2 real icon ROI / template benchmark

## Verdict

This is one independent, unobscured Windows Graphics Capture offer with three annotated cards (`n=1 offer`, `n=3 cards`). It is real RAW game data, not a template self-match. The sample is far too small for a stability/generalization claim.

| ROI | accepted icon accuracy | raw top1 | truth in top2 | three-card-all-correct | UNKNOWN | native latency avg / p95 |
|---|---:|---:|---:|---|---:|---:|
| current metadata | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) | FAIL (0/1 offer) | 3/3 | 0.078452 / 0.113100 ms |
| calibrated | 0/3 (0.0%) | 1/3 (33.3%) | 3/3 (100.0%) | FAIL (0/1 offer) | 3/3 | 0.077422 / 0.114100 ms |

Primary `icon card accuracy` above means accepted production-matcher output, not raw rank. Because every card is rejected by policy, both ROI variants are 0/3 accepted and all outputs are `UNKNOWN`. The calibrated crop nevertheless improves raw retrieval from 0/3 top-2 hits to 3/3; this is diagnostic candidate recall, not accepted accuracy.

## Calibrated ROI matcher detail

| card | truth | top1 (score/d) | top2 (score/d) | margin | truth pos | decision | reason / conflict |
|---|---|---|---|---:|---:|---|---|
| left | `ARAM_Impassable` | `ARAM_CourageoftheColossus` (0.750000/16) | `ARAM_Impassable` (0.750000/16) | 0.000000 | 2 | **UNKNOWN** | `hash_distance_above_threshold`; top1_tie, margin_below_threshold, distance_above_threshold |
| center | `Equilibrium` | `ARAM_OceanSoul` (0.765625/15) | `Equilibrium` (0.765625/15) | 0.000000 | 2 | **UNKNOWN** | `hash_distance_above_threshold`; top1_tie, margin_below_threshold, distance_above_threshold |
| right | `ARAM_CelestialBody` | `ARAM_CelestialBody` (0.828125/11) | `ARAM_BacktoBasics` (0.796875/13) | 0.031250 | 1 | **UNKNOWN** | `hash_distance_above_threshold`; margin_below_threshold, distance_above_threshold |

Left and center have exact top1/top2 distance ties (deterministic ID ordering puts truth second). Right ranks truth first, but distance 11 exceeds the configured maximum 10 and margin 0.03125 is below 0.08. Therefore ambiguity is reported as `UNKNOWN`; no candidate is promoted to a prediction.

## Current metadata ROI matcher detail

| card | truth | top1 (score/d) | top2 (score/d) | margin | truth pos | decision | reason / conflict |
|---|---|---|---|---:|---:|---|---|
| left | `ARAM_Impassable` | `ARAM_Stats` (0.671875/21) | `ARAM_StatsOnStats` (0.671875/21) | 0.000000 | — | **UNKNOWN** | `hash_distance_above_threshold`; top1_tie, margin_below_threshold, distance_above_threshold |
| center | `Equilibrium` | `ARAM_FeyMagic` (0.718750/18) | `ARAM_PandorasBox` (0.718750/18) | 0.000000 | — | **UNKNOWN** | `hash_distance_above_threshold`; top1_tie, margin_below_threshold, distance_above_threshold |
| right | `ARAM_CelestialBody` | `ARAM_HighRoller` (0.609375/25) | `ARAM_Quest_VoidImmolation` (0.609375/25) | 0.000000 | — | **UNKNOWN** | `hash_distance_above_threshold`; top1_tie, margin_below_threshold, distance_above_threshold |

## ROI error

The current metadata `icon` rectangles start at y=553 while the measured icon squares start at y=342. Their centers are 294 px too low and the crops are about 1.8x the calibrated area, pulling title/body pixels into the dHash.

| card | metadata rect | calibrated rect | IoU | icon area retained | metadata area on icon | center delta (x,y) |
|---|---|---|---:|---:|---:|---:|
| left | `512,553,256,406` | `625,342,240,240` | 0.0263 | 7.2% | 4.0% | (-105.0, 294.0) px |
| center | `1152,553,257,406` | `1171,342,240,240` | 0.0445 | 12.0% | 6.6% | (-10.5, 294.0) px |
| right | `1792,553,256,406` | `1717,342,240,240` | 0.0305 | 8.3% | 4.6% | (83.0, 294.0) px |

Mean metadata-vs-calibrated IoU is 0.0338; mean icon-area retention is 9.2%. This quantitatively confirms that the current metadata ROI is cropped wrong.

## Recommended precise normalized rect

Use these frame-normalized `(x, y, width, height)` rectangles for this exact 2560×1600 geometry:

- left: `(0.244140625, 0.213750000, 0.093750000, 0.150000000)`
- center: `(0.457421875, 0.213750000, 0.093750000, 0.150000000)`
- right: `(0.670703125, 0.213750000, 0.093750000, 0.150000000)`

Equivalent pixel rectangles are left `(625,342,240,240)`, center `(1171,342,240,240)`, right `(1717,342,240,240)`. Parametrically: `x = 0.244140625 + slot_index * 0.213281250`, `y = 0.213750000`, `w = 0.093750000`, `h = 0.150000000`, with slot index 0/1/2.

The same rectangles normalized to the metadata offer `(256,112,2048,1280)` are also in the JSON. A card-local normalized rect is intentionally not recommended: the metadata card rectangles are themselves visibly misregistered, so normalizing against them would encode that error.

## Failure causes and scope

- Current ROI failure: severe geometric miscrop (mean IoU and retained icon area above); dHash sees title/body/background rather than the icon square.
- Calibrated ROI failure: template/render domain gap plus coarse 64-bit dHash. Left/center tie unrelated templates at 15–16 bits; right is closest at 11 bits but still fails both distance and margin policy.
- The truth templates have singleton `candidate_ids_for_source_icon`, so this is not a manifest source-icon alias conflict.
- The ±2 px / 238–242 px sensitivity grid is diagnostic only and is included in JSON; it was not used to choose the ROI.
- No labels or product code were changed. This benchmark establishes only what happened on one offer; it does not establish a stable rate.

Sensitivity diagnostic (125 nearby crops per card): accepted-correct remains 0/125 for every card. Truth-in-top2 is left 89/125, center 17/125, right 107/125; candidate ranking is therefore not ROI-stable even in this tiny local grid.

## Method / reproducibility

The ROI was measured from RAW alpha silhouettes by thresholded geometry fitting (three luma thresholds) and the common 546 px card pitch, without optimizing matcher score. Primary matching and timing use the compiled production `src/vision/icon_matcher.cpp` with 245 loaded templates, 220 KIWI-eligible templates, max Hamming distance 10, and minimum margin 0.08. Latency uses 2000 timed calls per card after 100 warmups; repetitions measure compute latency, not additional accuracy samples. An independent Python byte-for-byte dHash/ranking implementation matched all six native outputs.

Artifacts: `outputs/tmp/phase2_real_icon_benchmark/annotated_raw.png`, `outputs/tmp/phase2_real_icon_benchmark/crop_comparison.png`, and `outputs/tmp/phase2_real_icon_benchmark/native_results.tsv`.
