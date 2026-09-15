from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ID = "sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6"
SAMPLE = ROOT / "data/dataset/augment_offers/real" / SAMPLE_ID
MANIFEST_PATH = ROOT / "data/knowledge/augment_icons/manifest.json"
OUT_DIR = ROOT / "outputs/tmp/phase2_real_icon_benchmark"
JSON_PATH = ROOT / "outputs/phase2_real_icon_benchmark.json"
MD_PATH = ROOT / "outputs/phase2_real_icon_benchmark.md"
NATIVE_EXE = (
    OUT_DIR / "native_build_vs/Release/phase2_real_icon_benchmark.exe"
)

SLOTS = ("left", "center", "right")
CALIBRATED_RECTS = {
    "left": (625, 342, 240, 240),
    "center": (1171, 342, 240, 240),
    "right": (1717, 342, 240, 240),
}
TEMPLATE_FILES = {
    "ARAM_Impassable": "4d08d0a84c4c5191d75084024d456de86c6f943b5e664e0ae98308df5170822b.png",
    "Equilibrium": "2e46f6d496815443d5797dd7496f02321515c0c872142d20a4d64c947e5807e2.png",
    "ARAM_CelestialBody": "eec0300c8c30cd3068756216c2e52a431a98014a0999ca4cf904d0c4e3f52b91.png",
}
MAXIMUM_HAMMING_DISTANCE = 10
MINIMUM_MARGIN = 0.08


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def parse_optional_float(value: str) -> float | None:
    return None if value == "-" else float(value)


def parse_native(stdout: str) -> dict[str, Any]:
    result: dict[str, Any] = {"rows": [], "summaries": {}}
    for line in stdout.splitlines():
        fields = line.split("\t")
        if fields[0] == "META":
            result["meta"] = {
                "templates_loaded": int(fields[1]),
                "kiwi_eligible_templates": int(fields[2]),
                "timed_iterations_per_card": int(fields[3]),
                "warmup_iterations_per_card": int(fields[4]),
            }
        elif fields[0] == "ROW":
            if len(fields) != 19:
                raise RuntimeError(f"unexpected native ROW: {line}")
            result["rows"].append(
                {
                    "variant": fields[1],
                    "slot": fields[2],
                    "truth": fields[3],
                    "rect_px": [int(value) for value in fields[4:8]],
                    "state": fields[8],
                    "prediction": None if fields[9] == "-" else fields[9],
                    "reason": fields[10],
                    "difference_hash": fields[11],
                    "top1_score": parse_optional_float(fields[12]),
                    "top2_score": parse_optional_float(fields[13]),
                    "margin": parse_optional_float(fields[14]),
                    "candidate_ids": [
                        value for value in fields[15:17] if value != "-"
                    ],
                    "latency_avg_ms": float(fields[17]),
                    "latency_p95_ms": float(fields[18]),
                }
            )
        elif fields[0] == "SUMMARY":
            result["summaries"][fields[1]] = {
                "latency_avg_ms": float(fields[2]),
                "latency_p95_ms": float(fields[3]),
                "timed_calls": int(fields[4]),
                "checksum": int(fields[5]),
            }
        elif line:
            raise RuntimeError(f"unexpected native output: {line}")
    if "meta" not in result or len(result["rows"]) != 6:
        raise RuntimeError("native output was incomplete")
    return result


def pixel_luma(pixel: np.ndarray) -> int:
    blue, green, red = (int(pixel[index]) for index in range(3))
    return (29 * blue + 150 * green + 77 * red) >> 8


def sample_resized_luma(crop: np.ndarray, target_x: int, target_y: int) -> int:
    height, width = crop.shape[:2]
    x_num = target_x * (width - 1)
    y_num = target_y * (height - 1)
    x0, x_rem = divmod(x_num, 8)
    y0, y_rem = divmod(y_num, 7)
    x1 = min(x0 + 1, width - 1)
    y1 = min(y0 + 1, height - 1)
    top = pixel_luma(crop[y0, x0]) * (8 - x_rem) + pixel_luma(
        crop[y0, x1]
    ) * x_rem
    bottom = pixel_luma(crop[y1, x0]) * (8 - x_rem) + pixel_luma(
        crop[y1, x1]
    ) * x_rem
    value = top * (7 - y_rem) + bottom * y_rem
    return (value + 28) // 56


def difference_hash(crop: np.ndarray) -> int:
    if crop.size == 0 or crop.shape[0] == 0 or crop.shape[1] == 0:
        raise ValueError("invalid crop")
    value = 0
    bit = 0
    for y in range(8):
        for x in range(8):
            if sample_resized_luma(crop, x, y) < sample_resized_luma(
                crop, x + 1, y
            ):
                value |= 1 << bit
            bit += 1
    return value


def rank_hash(
    crop: np.ndarray, templates: list[dict[str, Any]]
) -> dict[str, Any]:
    hash_value = difference_hash(crop)
    eligible = [
        item
        for item in templates
        if not item.get("modes") or "KIWI" in item["modes"]
    ]
    ranking = sorted(
        (
            {
                "augment_id": item["augment_id"],
                "distance": (hash_value ^ int(item["difference_hash"], 16)).bit_count(),
            }
            for item in eligible
        ),
        key=lambda item: (item["distance"], item["augment_id"]),
    )
    for item in ranking:
        item["score"] = 1.0 - item["distance"] / 64.0
    top1, top2 = ranking[:2]
    margin = top1["score"] - top2["score"]
    state = "unknown"
    prediction = None
    if top1["distance"] > MAXIMUM_HAMMING_DISTANCE:
        reason = "hash_distance_above_threshold"
    elif top1["distance"] == top2["distance"]:
        reason = "hash_top1_ambiguous"
    elif margin < MINIMUM_MARGIN:
        reason = "hash_margin_below_threshold"
    else:
        state = "matched"
        prediction = top1["augment_id"]
        reason = "hash_match"
    return {
        "hash": f"{hash_value:016x}",
        "ranking": ranking,
        "state": state,
        "prediction": prediction,
        "reason": reason,
        "candidate_ids": [top1["augment_id"], top2["augment_id"]],
        "top1_score": top1["score"],
        "top2_score": top2["score"],
        "margin": margin,
    }


def rect_crop(image: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, width, height = rect
    return image[y : y + height, x : x + width]


def verify_native_parity(
    raw: np.ndarray,
    templates: list[dict[str, Any]],
    native: dict[str, Any],
) -> None:
    for row in native["rows"]:
        reference = rank_hash(
            rect_crop(raw, tuple(row["rect_px"])), templates
        )
        checks = {
            "hash": reference["hash"] == row["difference_hash"],
            "state": reference["state"] == row["state"],
            "prediction": reference["prediction"] == row["prediction"],
            "reason": reference["reason"] == row["reason"],
            "candidates": reference["candidate_ids"] == row["candidate_ids"],
            "top1": math.isclose(
                reference["top1_score"], row["top1_score"], abs_tol=1e-6
            ),
            "top2": math.isclose(
                reference["top2_score"], row["top2_score"], abs_tol=1e-6
            ),
            "margin": math.isclose(
                reference["margin"], row["margin"], abs_tol=1e-6
            ),
        }
        if not all(checks.values()):
            raise RuntimeError(
                f"Python/native parity failed for {row['variant']}/{row['slot']}: {checks}"
            )
        truth_item = next(
            item
            for item in reference["ranking"]
            if item["augment_id"] == row["truth"]
        )
        row["top1_distance"] = reference["ranking"][0]["distance"]
        row["top2_distance"] = reference["ranking"][1]["distance"]
        row["truth_distance"] = truth_item["distance"]
        row["truth_rank"] = reference["ranking"].index(truth_item) + 1
        row["truth_candidate_position"] = (
            row["candidate_ids"].index(row["truth"]) + 1
            if row["truth"] in row["candidate_ids"]
            else None
        )
        row["decision"] = row["prediction"] or "UNKNOWN"
        row["conflict_flags"] = {
            "top1_tie": row["top1_distance"] == row["top2_distance"],
            "margin_below_threshold": row["margin"] < MINIMUM_MARGIN,
            "distance_above_threshold": row["top1_distance"]
            > MAXIMUM_HAMMING_DISTANCE,
            "accepted_wrong_id": row["state"] == "matched"
            and row["prediction"] != row["truth"],
        }


def intersection_metrics(
    actual: tuple[int, int, int, int], truth: tuple[int, int, int, int]
) -> dict[str, Any]:
    ax, ay, aw, ah = actual
    tx, ty, tw, th = truth
    width = max(0, min(ax + aw, tx + tw) - max(ax, tx))
    height = max(0, min(ay + ah, ty + th) - max(ay, ty))
    intersection = width * height
    actual_area = aw * ah
    truth_area = tw * th
    union = actual_area + truth_area - intersection
    return {
        "intersection_px2": intersection,
        "iou": intersection / union,
        "truth_area_coverage": intersection / truth_area,
        "current_crop_area_on_truth": intersection / actual_area,
        "area_ratio_current_to_calibrated": actual_area / truth_area,
        "center_offset_px": [
            (ax + aw / 2.0) - (tx + tw / 2.0),
            (ay + ah / 2.0) - (ty + th / 2.0),
        ],
    }


def mask_iou(left: np.ndarray, right: np.ndarray) -> float:
    intersection = np.count_nonzero(left & right)
    union = np.count_nonzero(left | right)
    return intersection / union if union else 0.0


def fit_icon_geometry(
    gray: np.ndarray,
    alpha: np.ndarray,
    search_x: range,
    search_y: range,
) -> list[dict[str, Any]]:
    sx1, sy1 = min(search_x), min(search_y)
    sx2, sy2 = max(search_x) + 250, max(search_y) + 250
    evidence: list[dict[str, Any]] = []
    for pixel_threshold in (45, 55, 65):
        actual = gray[sy1:sy2, sx1:sx2] > pixel_threshold
        best: tuple[float, int, int, int, int] = (0.0, 0, 0, 0, 0)
        for alpha_threshold in (1, 16, 32, 64, 96, 128):
            for size in range(232, 249):
                rendered = cv2.resize(
                    alpha, (size, size), interpolation=cv2.INTER_LINEAR
                ) > alpha_threshold
                for y in search_y:
                    for x in search_x:
                        predicted = np.zeros_like(actual)
                        predicted[
                            y - sy1 : y - sy1 + size,
                            x - sx1 : x - sx1 + size,
                        ] = rendered
                        candidate = (
                            mask_iou(actual, predicted),
                            x,
                            y,
                            size,
                            alpha_threshold,
                        )
                        if candidate > best:
                            best = candidate
        evidence.append(
            {
                "raw_luma_threshold": pixel_threshold,
                "mask_iou": best[0],
                "best_rect_px": [best[1], best[2], best[3], best[3]],
                "template_alpha_threshold": best[4],
            }
        )
    return evidence


def score_variant(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    accepted_correct = sum(
        row["state"] == "matched" and row["prediction"] == row["truth"]
        for row in rows
    )
    raw_top1_correct = sum(row["candidate_ids"][0] == row["truth"] for row in rows)
    top2_hit = sum(row["truth"] in row["candidate_ids"] for row in rows)
    return {
        "denominator_cards": count,
        "accepted_correct_cards": accepted_correct,
        "icon_card_accuracy": accepted_correct / count,
        "raw_rank_top1_correct_cards": raw_top1_correct,
        "raw_rank_top1_accuracy": raw_top1_correct / count,
        "truth_in_top2_cards": top2_hit,
        "truth_in_top2_rate": top2_hit / count,
        "unknown_cards": sum(row["state"] == "unknown" for row in rows),
        "three_card_all_correct": accepted_correct == count,
        "raw_rank_three_card_all_top1": raw_top1_correct == count,
        "three_card_truth_all_in_top2": top2_hit == count,
    }


def sensitivity(
    raw: np.ndarray,
    templates: list[dict[str, Any]],
    truth: dict[str, str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "purpose": "diagnostic only; never used to select the reported calibrated ROI",
        "grid": "per card: dx/dy=-2..2 px and square size=238..242 px (125 crops)",
        "cards": {},
    }
    for slot in SLOTS:
        bx, by, bw, _ = CALIBRATED_RECTS[slot]
        observations = []
        for size in range(238, 243):
            size_shift = int(round((size - bw) / 2.0))
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    rect = (bx + dx - size_shift, by + dy - size_shift, size, size)
                    match = rank_hash(rect_crop(raw, rect), templates)
                    observations.append(match)
        result["cards"][slot] = {
            "tested_crops": len(observations),
            "accepted_correct": sum(
                item["state"] == "matched" and item["prediction"] == truth[slot]
                for item in observations
            ),
            "raw_top1_correct": sum(
                item["candidate_ids"][0] == truth[slot] for item in observations
            ),
            "truth_in_top2": sum(
                truth[slot] in item["candidate_ids"] for item in observations
            ),
        }
    return result


def normalized(rect: tuple[int, int, int, int], width: int, height: int) -> list[float]:
    x, y, rw, rh = rect
    return [x / width, y / height, rw / width, rh / height]


def letterbox(image: np.ndarray, width: int, height: int) -> np.ndarray:
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(
        image,
        (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )
    canvas = np.full((height, width, 3), (8, 18, 18), dtype=np.uint8)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized[:, :, :3]
    return canvas


def template_on_dark(path: Path, size: int = 240) -> np.ndarray:
    icon = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    icon = cv2.resize(icon, (size, size), interpolation=cv2.INTER_LINEAR)
    alpha = icon[:, :, 3:4].astype(np.float32) / 255.0
    background = np.full((size, size, 3), (8, 18, 18), dtype=np.float32)
    return (icon[:, :, :3] * alpha + background * (1.0 - alpha)).astype(np.uint8)


def write_visuals(
    raw: np.ndarray,
    current_rects: dict[str, tuple[int, int, int, int]],
    truth: dict[str, str],
) -> None:
    annotated = raw[:, :, :3].copy()
    for slot in SLOTS:
        for rect, color, label in (
            (current_rects[slot], (0, 0, 255), f"{slot} metadata"),
            (CALIBRATED_RECTS[slot], (0, 255, 0), f"{slot} calibrated"),
        ):
            x, y, width, height = rect
            cv2.rectangle(annotated, (x, y), (x + width - 1, y + height - 1), color, 5)
            cv2.putText(
                annotated,
                label,
                (x, max(30, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                color,
                2,
                cv2.LINE_AA,
            )
    cv2.imwrite(str(OUT_DIR / "annotated_raw.png"), annotated)

    cell, header, gap = 240, 42, 16
    label_width = 210
    canvas = np.full(
        (header + 3 * (cell + gap), 3 * cell + 4 * gap + label_width, 3),
        (28, 28, 28),
        dtype=np.uint8,
    )
    for column, title in enumerate(("metadata ROI", "calibrated ROI", "source template")):
        cv2.putText(
            canvas,
            title,
            (gap + column * (cell + gap), 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (230, 230, 230),
            2,
            cv2.LINE_AA,
        )
    for row, slot in enumerate(SLOTS):
        y = header + row * (cell + gap)
        panels = (
            letterbox(rect_crop(raw, current_rects[slot]), cell, cell),
            letterbox(rect_crop(raw, CALIBRATED_RECTS[slot]), cell, cell),
            template_on_dark(
                ROOT
                / "data/knowledge/augment_icons/icons"
                / TEMPLATE_FILES[truth[slot]],
                cell,
            ),
        )
        for column, panel in enumerate(panels):
            x = gap + column * (cell + gap)
            canvas[y : y + cell, x : x + cell] = panel
        cv2.putText(
            canvas,
            f"{slot}: {truth[slot]}",
            (3 * cell + 3 * gap + 2, y + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(OUT_DIR / "crop_comparison.png"), canvas)


def pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def result_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| card | truth | top1 (score/d) | top2 (score/d) | margin | truth pos | decision | reason / conflict |",
        "|---|---|---|---|---:|---:|---|---|",
    ]
    for row in rows:
        flags = [name for name, active in row["conflict_flags"].items() if active]
        lines.append(
            "| {slot} | `{truth}` | `{top1}` ({s1:.6f}/{d1}) | `{top2}` "
            "({s2:.6f}/{d2}) | {margin:.6f} | {pos} | **{decision}** | "
            "`{reason}`; {flags} |".format(
                slot=row["slot"],
                truth=row["truth"],
                top1=row["candidate_ids"][0],
                s1=row["top1_score"],
                d1=row["top1_distance"],
                top2=row["candidate_ids"][1],
                s2=row["top2_score"],
                d2=row["top2_distance"],
                margin=row["margin"],
                pos=row["truth_candidate_position"] or "—",
                decision=row["decision"],
                reason=row["reason"],
                flags=", ".join(flags) or "none",
            )
        )
    return lines


def build_markdown(report: dict[str, Any]) -> str:
    variants = report["results"]
    current = variants["current_metadata"]
    calibrated = variants["calibrated"]
    lines = [
        "# Phase 2 real icon ROI / template benchmark",
        "",
        "## Verdict",
        "",
        "This is one independent, unobscured Windows Graphics Capture offer with three annotated cards "
        "(`n=1 offer`, `n=3 cards`). It is real RAW game data, not a template self-match. The sample is "
        "far too small for a stability/generalization claim.",
        "",
        "| ROI | accepted icon accuracy | raw top1 | truth in top2 | three-card-all-correct | UNKNOWN | native latency avg / p95 |",
        "|---|---:|---:|---:|---|---:|---:|",
    ]
    for key, label in (("current_metadata", "current metadata"), ("calibrated", "calibrated")):
        item = variants[key]
        metric = item["metrics"]
        latency = item["latency"]
        lines.append(
            f"| {label} | {metric['accepted_correct_cards']}/3 ({pct(metric['icon_card_accuracy'])}) | "
            f"{metric['raw_rank_top1_correct_cards']}/3 ({pct(metric['raw_rank_top1_accuracy'])}) | "
            f"{metric['truth_in_top2_cards']}/3 ({pct(metric['truth_in_top2_rate'])}) | "
            f"{'PASS' if metric['three_card_all_correct'] else 'FAIL (0/1 offer)'} | "
            f"{metric['unknown_cards']}/3 | {latency['latency_avg_ms']:.6f} / "
            f"{latency['latency_p95_ms']:.6f} ms |"
        )
    lines.extend(
        [
            "",
            "Primary `icon card accuracy` above means accepted production-matcher output, not raw rank. "
            "Because every card is rejected by policy, both ROI variants are 0/3 accepted and all outputs are `UNKNOWN`. "
            "The calibrated crop nevertheless improves raw retrieval from 0/3 top-2 hits to 3/3; this is diagnostic candidate recall, not accepted accuracy.",
            "",
            "## Calibrated ROI matcher detail",
            "",
            *result_table(calibrated["cards"]),
            "",
            "Left and center have exact top1/top2 distance ties (deterministic ID ordering puts truth second). "
            "Right ranks truth first, but distance 11 exceeds the configured maximum 10 and margin 0.03125 is below 0.08. "
            "Therefore ambiguity is reported as `UNKNOWN`; no candidate is promoted to a prediction.",
            "",
            "## Current metadata ROI matcher detail",
            "",
            *result_table(current["cards"]),
            "",
            "## ROI error",
            "",
            "The current metadata `icon` rectangles start at y=553 while the measured icon squares start at y=342. "
            "Their centers are 294 px too low and the crops are about 1.8x the calibrated area, pulling title/body pixels into the dHash.",
            "",
            "| card | metadata rect | calibrated rect | IoU | icon area retained | metadata area on icon | center delta (x,y) |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for item in report["roi_evaluation"]["cards"]:
        metric = item["overlap"]
        lines.append(
            f"| {item['slot']} | `{','.join(map(str, item['current_rect_px']))}` | "
            f"`{','.join(map(str, item['calibrated_rect_px']))}` | {metric['iou']:.4f} | "
            f"{pct(metric['truth_area_coverage'])} | {pct(metric['current_crop_area_on_truth'])} | "
            f"({metric['center_offset_px'][0]:.1f}, {metric['center_offset_px'][1]:.1f}) px |"
        )
    recommendation = report["recommended_normalized_icon_rect"]
    lines.extend(
        [
            "",
            "Mean metadata-vs-calibrated IoU is "
            f"{report['roi_evaluation']['mean_iou']:.4f}; mean icon-area retention is "
            f"{pct(report['roi_evaluation']['mean_truth_area_coverage'])}. This quantitatively confirms that the current metadata ROI is cropped wrong.",
            "",
            "## Recommended precise normalized rect",
            "",
            "Use these frame-normalized `(x, y, width, height)` rectangles for this exact 2560×1600 geometry:",
            "",
        ]
    )
    for slot in SLOTS:
        values = recommendation["raw_frame_normalized"][slot]
        lines.append(f"- {slot}: `({', '.join(f'{value:.9f}' for value in values)})`")
    lines.extend(
        [
            "",
            "Equivalent pixel rectangles are left `(625,342,240,240)`, center `(1171,342,240,240)`, "
            "right `(1717,342,240,240)`. Parametrically: "
            "`x = 0.244140625 + slot_index * 0.213281250`, `y = 0.213750000`, "
            "`w = 0.093750000`, `h = 0.150000000`, with slot index 0/1/2.",
            "",
            "The same rectangles normalized to the metadata offer `(256,112,2048,1280)` are also in the JSON. "
            "A card-local normalized rect is intentionally not recommended: the metadata card rectangles are themselves visibly misregistered, so normalizing against them would encode that error.",
            "",
            "## Failure causes and scope",
            "",
            "- Current ROI failure: severe geometric miscrop (mean IoU and retained icon area above); dHash sees title/body/background rather than the icon square.",
            "- Calibrated ROI failure: template/render domain gap plus coarse 64-bit dHash. Left/center tie unrelated templates at 15–16 bits; right is closest at 11 bits but still fails both distance and margin policy.",
            "- The truth templates have singleton `candidate_ids_for_source_icon`, so this is not a manifest source-icon alias conflict.",
            "- The ±2 px / 238–242 px sensitivity grid is diagnostic only and is included in JSON; it was not used to choose the ROI.",
            "- No labels or product code were changed. This benchmark establishes only what happened on one offer; it does not establish a stable rate.",
            "",
            "Sensitivity diagnostic (125 nearby crops per card): accepted-correct remains 0/125 for every card. "
            "Truth-in-top2 is left 89/125, center 17/125, right 107/125; candidate ranking is therefore not ROI-stable even in this tiny local grid.",
            "",
            "## Method / reproducibility",
            "",
            "The ROI was measured from RAW alpha silhouettes by thresholded geometry fitting (three luma thresholds) and the common 546 px card pitch, without optimizing matcher score. "
            "Primary matching and timing use the compiled production `src/vision/icon_matcher.cpp` with 245 loaded templates, 220 KIWI-eligible templates, max Hamming distance 10, and minimum margin 0.08. "
            f"Latency uses {report['benchmark']['timing']['timed_iterations_per_card']} timed calls per card after "
            f"{report['benchmark']['timing']['warmup_iterations_per_card']} warmups; repetitions measure compute latency, not additional accuracy samples. "
            "An independent Python byte-for-byte dHash/ranking implementation matched all six native outputs.",
            "",
            "Artifacts: `outputs/tmp/phase2_real_icon_benchmark/annotated_raw.png`, "
            "`outputs/tmp/phase2_real_icon_benchmark/crop_comparison.png`, and "
            "`outputs/tmp/phase2_real_icon_benchmark/native_results.tsv`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not NATIVE_EXE.is_file():
        raise RuntimeError(
            "native runner missing; configure/build outputs/tmp/phase2_real_icon_benchmark/native_project first"
        )

    metadata = load_json(SAMPLE / "metadata.json")
    annotation = load_json(SAMPLE / "annotation.json")
    manifest = load_json(MANIFEST_PATH)
    templates = manifest["templates"]
    if len(templates) != 245:
        raise RuntimeError(f"expected 245 templates, got {len(templates)}")
    if metadata["source"]["kind"] != "windows_graphics_capture":
        raise RuntimeError("sample is not Windows Graphics Capture data")
    if annotation["status"] != "complete":
        raise RuntimeError("sample annotation is incomplete")

    raw = cv2.imread(str(SAMPLE / "RAW.png"), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.shape[:2] != (1600, 2560):
        raise RuntimeError(f"unexpected RAW shape: {None if raw is None else raw.shape}")
    truth = {
        slot: annotation["cards"][slot]["augment_id"] for slot in SLOTS
    }
    current_rects = {
        item["slot"]: (
            item["icon"]["x"],
            item["icon"]["y"],
            item["icon"]["width"],
            item["icon"]["height"],
        )
        for item in metadata["rois"]["cards"]
    }

    completed = subprocess.run(
        [str(NATIVE_EXE), str(ROOT)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (OUT_DIR / "native_results.tsv").write_text(
        completed.stdout, encoding="utf-8", newline="\n"
    )
    native = parse_native(completed.stdout)
    if native["meta"]["templates_loaded"] != 245:
        raise RuntimeError("native matcher did not load 245 templates")
    for row in native["rows"]:
        expected_rect = (
            current_rects[row["slot"]]
            if row["variant"] == "current_metadata"
            else CALIBRATED_RECTS[row["slot"]]
        )
        if tuple(row["rect_px"]) != expected_rect or row["truth"] != truth[row["slot"]]:
            raise RuntimeError(
                f"native input mismatch for {row['variant']}/{row['slot']}"
            )
    verify_native_parity(raw, templates, native)

    by_variant: dict[str, dict[str, Any]] = {}
    for variant in ("current_metadata", "calibrated"):
        rows = [row for row in native["rows"] if row["variant"] == variant]
        by_variant[variant] = {
            "cards": rows,
            "metrics": score_variant(rows),
            "latency": native["summaries"][variant],
        }

    gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY)
    fit_search = {
        "left": (range(624, 635), range(338, 347)),
        "center": (range(1170, 1181), range(338, 347)),
        "right": (range(1712, 1723), range(338, 347)),
    }
    fit_evidence: dict[str, Any] = {}
    for slot in SLOTS:
        template = cv2.imread(
            str(
                ROOT
                / "data/knowledge/augment_icons/icons"
                / TEMPLATE_FILES[truth[slot]]
            ),
            cv2.IMREAD_UNCHANGED,
        )
        fit_evidence[slot] = fit_icon_geometry(
            gray, template[:, :, 3], *fit_search[slot]
        )

    roi_cards = []
    for slot in SLOTS:
        roi_cards.append(
            {
                "slot": slot,
                "current_rect_px": list(current_rects[slot]),
                "calibrated_rect_px": list(CALIBRATED_RECTS[slot]),
                "overlap": intersection_metrics(
                    current_rects[slot], CALIBRATED_RECTS[slot]
                ),
                "geometry_fit_evidence": fit_evidence[slot],
            }
        )
    mean_iou = sum(item["overlap"]["iou"] for item in roi_cards) / 3.0
    mean_coverage = sum(
        item["overlap"]["truth_area_coverage"] for item in roi_cards
    ) / 3.0
    mean_crop_on_truth = sum(
        item["overlap"]["current_crop_area_on_truth"] for item in roi_cards
    ) / 3.0

    offer = metadata["rois"]["offer"]
    offer_rect = (offer["x"], offer["y"], offer["width"], offer["height"])
    offer_normalized = {}
    for slot in SLOTS:
        x, y, width, height = CALIBRATED_RECTS[slot]
        offer_normalized[slot] = [
            (x - offer_rect[0]) / offer_rect[2],
            (y - offer_rect[1]) / offer_rect[3],
            width / offer_rect[2],
            height / offer_rect[3],
        ]

    write_visuals(raw, current_rects, truth)

    report = {
        "schema": "lol_assistant.phase2_real_icon_benchmark",
        "schema_version": 1,
        "scope": {
            "independent_offers": 1,
            "cards": 3,
            "sample_size_warning": "Extremely small: one offer/three cards; no stability or generalization claim.",
            "real_raw_not_template_self_match": True,
            "labels_unchanged": True,
            "product_code_unchanged": True,
        },
        "sample": {
            "sample_id": SAMPLE_ID,
            "sample_kind": metadata["sample_kind"],
            "capture_source": metadata["source"]["kind"],
            "raw_path": str((SAMPLE / "RAW.png").relative_to(ROOT)).replace("\\", "/"),
            "raw_sha256": sha256(SAMPLE / "RAW.png"),
            "metadata_sha256": sha256(SAMPLE / "metadata.json"),
            "annotation_sha256": sha256(SAMPLE / "annotation.json"),
            "resolution": metadata["resolution"],
            "truth": truth,
            "annotation_status": annotation["status"],
        },
        "template_set": {
            "manifest_path": str(MANIFEST_PATH.relative_to(ROOT)).replace("\\", "/"),
            "manifest_sha256": sha256(MANIFEST_PATH),
            "templates_loaded": native["meta"]["templates_loaded"],
            "kiwi_eligible_templates": native["meta"]["kiwi_eligible_templates"],
            "distinct_ids": len({item["augment_id"] for item in templates}),
            "truth_source_alias_candidates": {
                slot: next(
                    item["candidate_ids_for_source_icon"]
                    for item in templates
                    if item["augment_id"] == truth[slot]
                )
                for slot in SLOTS
            },
        },
        "matcher_policy": {
            "mode": "KIWI",
            "maximum_hamming_distance": MAXIMUM_HAMMING_DISTANCE,
            "minimum_margin": MINIMUM_MARGIN,
            "candidate_limit": 2,
            "ambiguity_output": "UNKNOWN",
        },
        "roi_evaluation": {
            "calibration_method": "RAW luma mask vs source alpha silhouette geometric fit at thresholds 45/55/65; 546 px shared slot pitch; matcher score was not an optimization objective.",
            "cards": roi_cards,
            "mean_iou": mean_iou,
            "mean_truth_area_coverage": mean_coverage,
            "mean_current_crop_area_on_truth": mean_crop_on_truth,
            "current_metadata_cropped_wrong": True,
        },
        "recommended_normalized_icon_rect": {
            "coordinate_order": ["x", "y", "width", "height"],
            "raw_frame_size": [2560, 1600],
            "raw_frame_pixels": {
                slot: list(CALIBRATED_RECTS[slot]) for slot in SLOTS
            },
            "raw_frame_normalized": {
                slot: normalized(CALIBRATED_RECTS[slot], 2560, 1600)
                for slot in SLOTS
            },
            "raw_frame_parametric": {
                "slot_index": {"left": 0, "center": 1, "right": 2},
                "x": "0.244140625 + slot_index * 0.213281250",
                "y": 0.21375,
                "width": 0.09375,
                "height": 0.15,
            },
            "metadata_offer_rect_px": list(offer_rect),
            "metadata_offer_normalized": offer_normalized,
            "card_local_not_recommended": "Current metadata card rectangles are also misregistered; card-local normalization would encode that error.",
        },
        "results": by_variant,
        "calibrated_roi_sensitivity": sensitivity(raw, templates, truth),
        "benchmark": {
            "primary_implementation": "native C++ compiled from current src/vision/icon_matcher.cpp",
            "python_reference_parity_all_six_rows": True,
            "timing": {
                **native["meta"],
                "latency_population_note": "Repeated native matcher calls on the same 3 crops; repeats are not accuracy samples.",
                "clock": "std::chrono::steady_clock",
            },
            "environment": {
                "platform": platform.platform(),
                "processor": os.environ.get("PROCESSOR_IDENTIFIER"),
                "build": "MSVC x64 Release",
                "native_exe_sha256": sha256(NATIVE_EXE),
            },
        },
        "failure_causes": [
            "Current metadata ROI is geometrically miscropped: center is 294 px too low, with very low IoU and icon-area retention.",
            "Calibrated real render and source templates have a domain gap under the coarse 64-bit dHash.",
            "Calibrated left/center have top1 distance ties and truth is deterministic rank 2.",
            "Calibrated right truth is rank 1 but distance 11 > 10 and margin 0.03125 < 0.08.",
        ],
        "artifacts": {
            "annotated_raw": "outputs/tmp/phase2_real_icon_benchmark/annotated_raw.png",
            "crop_comparison": "outputs/tmp/phase2_real_icon_benchmark/crop_comparison.png",
            "native_results": "outputs/tmp/phase2_real_icon_benchmark/native_results.tsv",
        },
    }
    JSON_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    MD_PATH.write_text(build_markdown(report), encoding="utf-8", newline="\n")
    print(f"wrote {JSON_PATH}")
    print(f"wrote {MD_PATH}")


if __name__ == "__main__":
    main()
