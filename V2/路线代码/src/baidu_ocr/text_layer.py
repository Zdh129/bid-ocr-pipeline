"""文字层质量检测与直接抽取。质量差则交给扫描识别路径。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MIN_CHARS = 30
MAX_GARBLED_RATIO = 0.05
MIN_SCRIPT_RATIO = 0.5
HIDDEN_IMAGE_COVER = 0.5
HIDDEN_MAX_FONT_PT = 3.0


def _plain(page) -> str:
    return page.get_text("text") or ""


def _char_stats(text: str) -> dict:
    chars = re.sub(r"\s+", "", text or "")
    n = len(chars)
    if n == 0:
        return {"n": 0, "garbled": 0, "script": 0, "cjk": 0}
    garbled = 0
    script = 0
    cjk = 0
    for ch in chars:
        cat = unicodedata.category(ch)
        if cat in {"Cc", "Co", "Cn"} or ch in "�□￼":
            garbled += 1
        if "\u4e00" <= ch <= "\u9fff":
            cjk += 1
            script += 1
        elif ch.isalnum() or ch in ".,;:()（）%、+-":
            script += 1
    return {"n": n, "garbled": garbled / n, "script": script / n, "cjk": cjk}


def assess_text_layer(page, cfg: dict | None = None) -> dict[str, Any]:
    tl = ((cfg or {}).get("text_layer") or {})
    min_chars = int(tl.get("min_chars") or MIN_CHARS)
    max_garbled = float(tl.get("max_garbled_ratio") or MAX_GARBLED_RATIO)
    min_script = float(tl.get("min_script_ratio") or MIN_SCRIPT_RATIO)
    hidden_cover = float(tl.get("hidden_image_cover") or HIDDEN_IMAGE_COVER)
    hidden_font = float(tl.get("hidden_max_font_pt") or HIDDEN_MAX_FONT_PT)

    text = _plain(page)
    st = _char_stats(text)
    reasons = []
    ok = True
    if st["n"] < min_chars:
        ok = False
        reasons.append("too_few_chars")
    if st["n"] and st["garbled"] > max_garbled:
        ok = False
        reasons.append("garbled")
    if st["n"] and st["script"] < min_script:
        ok = False
        reasons.append("low_script_ratio")

    area = max(float(page.rect.width * page.rect.height), 1.0)
    image_area = 0.0
    try:
        for info in page.get_image_info() or []:
            bbox = info.get("bbox")
            if bbox:
                image_area += max(0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    except Exception:  # noqa: BLE001
        image_area = 0.0
    cover = min(1.0, image_area / area)
    sizes = []
    try:
        d = page.get_text("dict") or {}
        for b in d.get("blocks") or []:
            for line in b.get("lines") or []:
                for span in line.get("spans") or []:
                    sz = float(span.get("size") or 0)
                    if sz > 0:
                        sizes.append(sz)
    except Exception:  # noqa: BLE001
        sizes = []
    median = sorted(sizes)[len(sizes) // 2] if sizes else None
    if cover >= hidden_cover and median is not None and median <= hidden_font and st["n"] >= min_chars:
        ok = False
        reasons.append("hidden_ocr_tiny_font")

    return {
        "usable": ok,
        "reasons": reasons,
        "chars": st["n"],
        "garbled_ratio": round(st["garbled"], 4) if st["n"] else 0,
        "script_ratio": round(st["script"], 4) if st["n"] else 0,
        "cjk_chars": st["cjk"],
        "image_coverage": round(cover, 4),
        "median_font_pt": median,
        "text": text,
    }


def extract_blocks(page, page_number: int) -> list[dict]:
    """从 PyMuPDF dict 抽出段落块（已是 pt、左上原点）。"""
    d = page.get_text("dict") or {}
    blocks = []
    idx = 0
    height = float(page.rect.height)
    for b in d.get("blocks") or []:
        bbox = list(b.get("bbox") or [])
        if len(bbox) < 4:
            continue
        if b.get("type") == 1:
            idx += 1
            blocks.append(
                {
                    "id": f"b-{page_number:03d}-{idx:03d}",
                    "block_type": "figure",
                    "bbox": {"pdf": bbox, "pixel": None},
                    "text": "",
                    "confidence": 1.0,
                    "source": "rule",
                    "lines": [],
                }
            )
            continue
        parts = []
        sizes = []
        lines = []
        for line in b.get("lines") or []:
            segs = []
            for span in line.get("spans") or []:
                t = span.get("text") or ""
                if t:
                    segs.append(t)
                    sizes.append(float(span.get("size") or 0))
            lt = "".join(segs).strip()
            if lt:
                parts.append(lt)
                lb = list(line.get("bbox") or bbox)
                lines.append({"text": lt, "confidence": 1.0, "bbox": {"pdf": lb}})
        text = "\n".join(parts).strip()
        if not text:
            continue
        idx += 1
        typ = "text"
        max_sz = max(sizes) if sizes else 0
        y0, y1 = bbox[1], bbox[3]
        if y1 < 0.08 * height and len(text) < 40:
            typ = "header"
        elif y0 > 0.92 * height and len(text) < 40:
            typ = "footer"
        elif max_sz >= 16 and len(text) < 80:
            typ = "title"
        blocks.append(
            {
                "id": f"b-{page_number:03d}-{idx:03d}",
                "block_type": typ,
                "bbox": {"pdf": bbox, "pixel": None},
                "text": text,
                "confidence": 1.0,
                "source": "rule",
                "lines": lines,
                "extras": {"median_size": sorted(sizes)[len(sizes) // 2] if sizes else None},
            }
        )
    return blocks
