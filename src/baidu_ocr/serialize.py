"""将识别结果转为生成侧中间格式 v1.0.0。坐标单位 pt，原点在左上角。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .reading_order import PAGE_NO_RE, assign_reading_order
from .table_rebuild import rebuild_table_from_words, table_from_docparser

SCHEMA_VERSION = "1.0.0"
ENGINE_NAME = "baidu-ocr-test-pipeline"
ENGINE_VERSION = "0.1.0"

TYPE_MAP = {
    "text": "paragraph",
    "paragraph": "paragraph",
    "title": "title",
    "table": "table",
    "figure": "image",
    "image": "image",
    "seal": "seal",
    "handwriting": "handwriting",
    "formula": "formula",
    "equation": "formula",
    "header": "header",
    "footer": "footer",
    "caption": "figure_caption",
    "figure_caption": "figure_caption",
    "image_caption": "figure_caption",
    "table_caption": "table_caption",
    "page_number": "page_number",
    "heading": "heading",
    "list": "list",
    "signature": "signature",
    "chart": "image",
    "aside_text": "paragraph",
    "page_footnote": "paragraph",
    "ref_text": "paragraph",
    "image_footnote": "figure_caption",
    "algorithm": "unknown",
    "unknown": "unknown",
    "other": "unknown",
    "doc_title": "title",
    "paragraph_title": "heading",
}

LIST_RE = re.compile(
    r"^\s*(?:[（(]?\d+[）).、]|[①②③④⑤⑥⑦⑧⑨⑩]|[·•\-—]\s+|[一二三四五六七八九十]+、)"
)
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+[章节篇部]")
NUM_L2_RE = re.compile(r"^\d+\.\d+")
NUM_L3_RE = re.compile(r"^\d+\.\d+\.\d+")


def _now_iso() -> str:
    # 机器时钟为 CST；同时给出带时区的 ISO 时间。
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:  # noqa: BLE001
        tz = datetime.now().astimezone().tzinfo
    return datetime.now(tz).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()



def _crop_asset(
    page_image: Path | None,
    pixel_bbox: list[float] | None,
    dest: Path,
    asset_id: str,
    typ: str,
    dpi: float,
    source_page: int,
) -> dict | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    wrote = False
    if page_image and Path(page_image).exists() and pixel_bbox:
        from PIL import Image

        with Image.open(page_image) as im:
            w, h = im.size
            x0, y0, x1, y1 = (int(max(0, v)) for v in pixel_bbox[:4])
            x1, y1 = min(w, max(x0 + 1, x1)), min(h, max(y0 + 1, y1))
            crop = im.crop((x0, y0, x1, y1))
            if crop.size[0] and crop.size[1]:
                crop.save(dest)
                wrote = True
                ww, hh = crop.size
    if not wrote:
        from PIL import Image

        Image.new("RGB", (8, 8), (255, 255, 255)).save(dest)
        ww, hh = 8, 8
    return {
        "id": asset_id,
        "type": "seal" if typ == "seal" else "image",
        "path": f"assets/{dest.name}",
        "mime_type": "image/png",
        "hash_sha256": sha256_file(dest),
        "width_px": int(ww),
        "height_px": int(hh),
        "original_dpi": float(dpi),
        "source_page": int(source_page),
    }

def _valid_bbox(box: list[float] | None) -> list[float] | None:
    if not box or len(box) < 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in box[:4])
    if x1 <= x0:
        x1 = x0 + 0.1
    if y1 <= y0:
        y1 = y0 + 0.1
    return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]


def _bbox_to_pt(raw: dict, dpi: float, mapper=None) -> list[float] | None:
    box = raw.get("bbox") or {}
    if isinstance(box, list):
        # 已是 pt？若同时给了 unit
        unit = raw.get("bbox_unit") or "pt"
        if unit == "pt":
            return _valid_bbox(box)
        return _valid_bbox([float(v) * 72.0 / dpi for v in box[:4]])
    pdf = _valid_bbox(box.get("pdf") or [])
    pixel = box.get("pixel")
    if pixel and len(pixel) >= 4:
        if mapper is not None:
            return _valid_bbox(mapper.pixel_to_pt(pixel))
        return _valid_bbox([float(v) * 72.0 / dpi for v in pixel[:4]])
    return pdf


def map_type(raw: str, text: str = "") -> str:
    typ = TYPE_MAP.get((raw or "other").lower(), "unknown")
    if typ == "footer" and PAGE_NO_RE.match((text or "").strip()):
        return "page_number"
    if typ == "paragraph" and text and LIST_RE.match(text.strip().splitlines()[0] if text.strip() else ""):
        return "list"
    if typ == "title":
        t = (text or "").strip()
        if CHAPTER_RE.match(t) or NUM_L2_RE.match(t) or NUM_L3_RE.match(t) or re.match(r"^\d+、", t):
            return "heading"
    return typ


def infer_heading_level(text: str) -> int:
    t = (text or "").strip()
    if NUM_L3_RE.match(t):
        return 3
    if NUM_L2_RE.match(t):
        return 2
    return 1


def _source_of(raw: dict) -> str:
    src = raw.get("source")
    if src in {"rule", "heuristic"}:
        return "rule"
    if src == "text_layer":
        return "rule"
    if src == "coverage_gap":
        return "ocr"
    return "ocr"


def _verification(raw: dict) -> str:
    if raw.get("coverage_gap") or raw.get("verification_status") == "needs_review":
        return "needs_review"
    if raw.get("pii_blocked"):
        return "needs_review"
    conf = raw.get("confidence")
    if conf is not None and conf < 0.85:
        return "needs_review"
    if (raw.get("text") or "").strip() or (raw.get("table") or {}).get("cells"):
        if conf is None or conf >= 0.85:
            return "auto_passed"
    return "unverified"


def words_to_internal_blocks(words: list[dict], page: int, mapper, dpi: float) -> list[dict]:
    blocks = []
    for i, w in enumerate(words, start=1):
        loc = w.get("location") or {}
        pixel = None
        if loc:
            l, t = float(loc.get("left") or 0), float(loc.get("top") or 0)
            pixel = [l, t, l + float(loc.get("width") or 0), t + float(loc.get("height") or 0)]
        pdf = mapper.pixel_to_pdf(pixel) if mapper is not None and pixel else None
        conf = None
        prob = w.get("probability") or {}
        if isinstance(prob, dict) and prob.get("average") is not None:
            conf = float(prob["average"])
        text = w.get("words") if w.get("words") is not None else (w.get("text") or "")
        typ = "text"
        blocks.append(
            {
                "id": f"b-{page:03d}-{i:03d}",
                "block_type": typ,
                "bbox": {"pdf": pdf, "pixel": pixel},
                "text": text,
                "confidence": conf if conf is not None else 0.9,
                "source": "ocr",
                "lines": [{"text": text, "confidence": conf, "bbox": {"pdf": pdf, "pixel": pixel}}],
            }
        )
    return blocks


def build_document(
    *,
    source_file: str,
    pages_meta: list[dict],
    blocks_by_page: dict[int, list[dict]],
    dpi: float,
    source_type: str = "pdf",
    data_label: str = "constructed",
    engine_notes: str | None = None,
    warnings: list | None = None,
    source_hash: str | None = None,
    assets_dir: Path | None = None,
) -> dict:
    v_pages = []
    v_blocks = []
    v_assets = []
    all_warnings = list(warnings or [])
    asset_seq = 0
    needs_review = False
    for page in pages_meta:
        page_no = int(page["page"])
        width = float(page.get("width_pdf") or 0)
        height = float(page.get("height_pdf") or 0)
        if not width and page.get("width_px"):
            width = float(page["width_px"]) * 72.0 / dpi
            height = float(page["height_px"]) * 72.0 / dpi
        rotation = int(page.get("rotation") or 0)
        if rotation not in {0, 90, 180, 270}:
            rotation = 0
        mapper = page.get("mapper")
        rec = {
            "page_number": page_no,
            "width": round(width, 2),
            "height": round(height, 2),
            "unit": "pt",
            "render_dpi": dpi,
            "rotation": rotation,
            "width_mm": round(width * 25.4 / 72.0, 2),
            "height_mm": round(height * 25.4 / 72.0, 2),
            "deskew_applied": False,
            "skew_angle_deg": 0.0,
            "coordinate_space": {
                "unit": "pt",
                "origin": "top-left",
                "y_axis": "downward",
                "formula": "pt = px * 72 / render_dpi",
                "render_dpi": dpi,
                "render_width_px": page.get("width_px"),
                "render_height_px": page.get("height_px"),
                "recognition_scale": 1.0,
                "pdf_user_space": [0, 0, round(width, 2), round(height, 2)],
            },
        }
        v_pages.append(rec)

        raw_blocks = list(blocks_by_page.get(page_no) or [])
        prepared = []
        for raw in raw_blocks:
            typ = map_type(raw.get("block_type") or raw.get("type") or "text", raw.get("text") or "")
            bbox = _bbox_to_pt(raw, dpi, mapper)
            if not bbox:
                all_warnings.append({"code": "missing_bbox", "page": page_no, "block": raw.get("id")})
                continue
            blk: dict[str, Any] = {
                "id": raw.get("id") or f"b-{page_no:03d}-{len(prepared)+1:03d}",
                "page": page_no,
                "bbox": bbox,
                "bbox_unit": "pt",
                "type": typ,
                "text": raw.get("text") or "",
                "confidence": raw.get("confidence"),
                "source": _source_of(raw),
                "verification_status": _verification(raw),
            }
            if typ == "heading":
                blk["level"] = raw.get("level") or infer_heading_level(blk["text"])
            if typ == "table":
                table = raw.get("table")
                if not table:
                    # 尝试从 lines 重建
                    table = rebuild_table_from_words(raw.get("lines") or raw.get("cells") or [])
                    # 把单元格 bbox 转 pt
                cells = []
                for c in (table or {}).get("cells") or []:
                    cb = c.get("bbox")
                    if cb and not (isinstance(cb, list) and raw.get("table_bbox_unit") == "pt"):
                        # 像素？若数值远大于页面 pt 则按像素转换
                        if cb and max(cb) > max(width, height) * 1.2:
                            cb = [float(v) * 72.0 / dpi for v in cb]
                    cell = {
                        "row": int(c.get("row") or 0),
                        "col": int(c.get("col") or 0),
                        "text": c.get("text") or "",
                        "rowspan": int(c.get("rowspan") or 1),
                        "colspan": int(c.get("colspan") or 1),
                    }
                    if cb:
                        cell["bbox"] = _valid_bbox(cb)
                    cells.append(cell)
                blk["table"] = {
                    "n_rows": int((table or {}).get("n_rows") or 0),
                    "n_cols": int((table or {}).get("n_cols") or 0),
                    "cells": cells,
                }
                if (table or {}).get("header_row_count") is not None:
                    blk["table"]["header_row_count"] = int(table["header_row_count"])
                if (table or {}).get("caption"):
                    blk["table"]["caption"] = table["caption"]
            warns = list(raw.get("warnings") or [])
            if raw.get("coverage_gap"):
                warns.append("coverage_gap")
                blk["needs_review"] = True
            if typ in {"image", "seal"}:
                asset_seq += 1
                asset_id = f"a-{page_no:03d}-{asset_seq:03d}"
                dest_dir = Path(assets_dir) if assets_dir else Path("/tmp/baidu-ocr-assets")
                pixel = (raw.get("bbox") or {}).get("pixel")
                if not pixel and mapper is not None and bbox:
                    pixel = mapper.pdf_to_pixel(bbox)
                page_image = page.get("image_ref")
                dest = dest_dir / f"{asset_id}.png"
                asset = _crop_asset(Path(page_image) if page_image else None, pixel, dest, asset_id, typ, dpi, page_no)
                if asset:
                    # schema asset type enum may be limited
                    asset["type"] = "seal" if typ == "seal" else "image"
                    v_assets.append(asset)
                    blk["image"] = {"asset_id": asset_id}
                    if typ == "seal":
                        blk["image"]["display_width_pt"] = round(bbox[2] - bbox[0], 2)
            if warns:
                blk["warnings"] = warns
            if blk["verification_status"] == "needs_review":
                blk["needs_review"] = True
                needs_review = True
            prepared.append(blk)
        prepared = assign_reading_order(prepared, width)
        v_blocks.extend(prepared)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "data_label": data_label,
        "document": {
            "source_file": Path(source_file).name,
            "source_type": source_type,
            "page_count": len(v_pages),
            "generated_at": _now_iso(),
            "engine_name": ENGINE_NAME,
            "engine_version": ENGINE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "render_dpi_default": dpi,
        },
        "coordinate_system": {
            "origin": "top-left",
            "x_axis": "rightward",
            "y_axis": "downward",
            "default_unit": "pt",
            "description": "页面左上原点，x 向右、y 向下，默认单位 typographic pt（1 pt = 1/72 inch）。换算：pt = px × 72 / 本页 render_dpi。",
        },
        "pages": v_pages,
        "blocks": v_blocks,
        "assets": v_assets,
        "warnings": all_warnings,
        "needs_review": needs_review,
    }
    if source_hash:
        doc["document"]["source_hash_sha256"] = source_hash
    if engine_notes:
        doc["document"]["notes"] = engine_notes
    return doc


def validate_schema(document: dict, schema_path: str | Path) -> list[str]:
    import json

    try:
        import jsonschema
    except ImportError:
        return ["jsonschema 未安装"]
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(document)]


# 给方案 B 用
layout_type_map = TYPE_MAP
