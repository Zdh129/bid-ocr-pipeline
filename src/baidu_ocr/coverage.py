"""行框覆盖检查：每个检测到的文字行框都必须有对应输出，禁止静默跳过。"""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image


def iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def baidu_location_to_bbox(loc: dict) -> list[float]:
    left = float(loc.get("left") or 0)
    top = float(loc.get("top") or 0)
    width = float(loc.get("width") or 0)
    height = float(loc.get("height") or 0)
    return [left, top, left + width, top + height]


def words_to_boxes(words_result: list[dict]) -> list[dict]:
    out = []
    for i, w in enumerate(words_result or []):
        loc = w.get("location") or {}
        bbox = baidu_location_to_bbox(loc) if loc else None
        text = w.get("words")
        if text is None:
            text = w.get("text") or ""
        conf = None
        prob = w.get("probability") or {}
        if isinstance(prob, dict) and prob.get("average") is not None:
            conf = float(prob["average"])
        out.append({"index": i, "bbox": bbox, "text": text, "confidence": conf, "raw": w})
    return out


def projection_line_boxes(image_path: str, cfg: dict) -> list[dict]:
    """扫描页的水平投影行框（像素，左上原点）。"""
    cov = cfg.get("coverage") or {}
    im = Image.open(image_path).convert("L")
    arr = np.asarray(im)
    h, w = arr.shape
    ink = arr < 200
    density = ink.mean(axis=1)
    # 自适应：超过中位数与均值之间的阈值视为有字
    thr = max(0.02, float(np.median(density) + 0.3 * (density.mean() if density.mean() else 0)))
    min_h = int(cov.get("projection_min_height_px") or 8)
    min_w = int(w * float(cov.get("projection_min_width_ratio") or 0.08))
    active = density >= thr
    boxes = []
    i = 0
    idx = 0
    while i < h:
        if not active[i]:
            i += 1
            continue
        j = i + 1
        while j < h and active[j]:
            j += 1
        if (j - i) >= min_h:
            band = ink[i:j]
            cols = np.where(band.any(axis=0))[0]
            if len(cols) and int(cols[-1] - cols[0]) >= min_w:
                idx += 1
                boxes.append(
                    {
                        "id": f"proj-{idx:03d}",
                        "bbox": [float(cols[0]), float(i), float(cols[-1] + 1), float(j)],
                        "source": "projection",
                    }
                )
        i = j
    return boxes


def text_layer_line_boxes(blocks: list[dict], mapper) -> list[dict]:
    boxes = []
    n = 0
    for b in blocks or []:
        for line in b.get("lines") or []:
            pdf = (line.get("bbox") or {}).get("pdf")
            if not pdf or mapper is None:
                continue
            n += 1
            boxes.append(
                {
                    "id": f"tl-{n:03d}",
                    "bbox": [float(v) for v in mapper.pdf_to_pixel(pdf)],
                    "source": "text_layer",
                    "text": line.get("text") or "",
                }
            )
    return boxes


def check_coverage(
    expected: list[dict],
    words_result: list[dict],
    *,
    iou_threshold: float = 0.3,
) -> dict[str, Any]:
    """每个 expected 行框必须匹配到一条非空识别结果。未覆盖的明确列出。"""
    preds = words_to_boxes(words_result)
    used: set[int] = set()
    matched = []
    missing = []
    empty_matched = []
    for exp in expected or []:
        eb = exp.get("bbox")
        if not eb:
            missing.append({**{k: v for k, v in exp.items() if k != "text"}, "reason": "expected_box_missing"})
            continue
        best_i, best_iou = -1, 0.0
        for p in preds:
            if p["index"] in used or not p["bbox"]:
                continue
            score = iou(eb, p["bbox"])
            if score > best_iou:
                best_i, best_iou = p["index"], score
        if best_i < 0 or best_iou < iou_threshold:
            missing.append(
                {
                    "id": exp.get("id"),
                    "bbox": eb,
                    "source": exp.get("source"),
                    "reason": "no_matching_output",
                    "best_iou": round(best_iou, 4),
                }
            )
            continue
        used.add(best_i)
        pred = next(p for p in preds if p["index"] == best_i)
        rec = {
            "id": exp.get("id"),
            "bbox": eb,
            "pred_index": best_i,
            "iou": round(best_iou, 4),
            "empty": not bool((pred["text"] or "").strip()),
        }
        if rec["empty"]:
            rec["reason"] = "matched_but_empty_text"
            empty_matched.append(rec)
            missing.append(rec)
        else:
            matched.append(rec)
    unmatched_preds = [p for p in preds if p["index"] not in used]
    return {
        "expected": len(expected or []),
        "predicted": len(preds),
        "matched": len(matched),
        "missing": missing,
        "empty_matched": empty_matched,
        "unmatched_pred_count": len(unmatched_preds),
        "complete": len(missing) == 0,
        "gaps_marked": True,
    }


def coverage_blocks_from_gaps(gaps: list[dict], page: int, start_idx: int, mapper=None) -> list[dict]:
    """把仍缺的行框写成显式结果块，禁止静默跳过。"""
    blocks = []
    for i, g in enumerate(gaps, start=1):
        bbox_px = g.get("bbox")
        pdf = mapper.pixel_to_pdf(bbox_px) if mapper is not None and bbox_px else None
        blocks.append(
            {
                "id": f"b-{page:03d}-{start_idx + i:03d}",
                "block_type": "text",
                "bbox": {"pdf": pdf, "pixel": bbox_px},
                "text": "",
                "confidence": 0.0,
                "source": "coverage_gap",
                "verification_status": "needs_review",
                "coverage_gap": True,
                "gap_reason": g.get("reason") or "no_matching_output",
                "warnings": [f"coverage_gap:{g.get('reason') or 'missing'}"],
            }
        )
    return blocks
