"""方案 A/B 与 dry-run 编排。默认不外发。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pymupdf

from .client import BaiduOcrClient
from .config import allow_external, load_config, resolve_path
from .cost import CostTracker
from .coverage import (
    check_coverage,
    coverage_blocks_from_gaps,
    projection_line_boxes,
    text_layer_line_boxes,
)
from .docparser import DocParserClient
from .pii_gate import blocked_record, load_known_pages, pii_reasons, sha256_file
from .reading_order import sort_lines_reading_order
from .render import render_pages
from .route import route_page
from .serialize import build_document, validate_schema, words_to_internal_blocks
from .table_rebuild import rebuild_table_from_words, table_from_docparser
from .text_layer import assess_text_layer, extract_blocks

LAYOUT_TYPE = {
    "title": "title",
    "text": "text",
    "table": "table",
    "image": "figure",
    "image_caption": "figure_caption",
    "image_footnote": "figure_caption",
    "equation": "formula",
    "header": "header",
    "footer": "footer",
    "page_number": "page_number",
    "aside_text": "text",
    "seal": "seal",
    "chart": "figure",
    "algorithm": "text",
    "page_footnote": "text",
    "ref_text": "text",
}


def _open_page(doc, page_no: int):
    return doc[page_no - 1]


def inspect_page(
    *,
    pdf_path: Path,
    page_no: int,
    rendered: dict,
    cfg: dict,
    file_sha256: str | None,
    label: str = "",
) -> dict:
    """渲染后的本地检查：文字层、分流、PII。不把正文写入对外记录（PII 页尤其如此）。"""
    doc = pymupdf.open(pdf_path)
    try:
        page = _open_page(doc, page_no)
        tl = assess_text_layer(page, cfg)
        # PII 用文字层文本 + 文件名；扫描页可能漏检，见风险说明
        reasons = pii_reasons(
            tl.get("text") or "",
            filename=pdf_path.name,
            file_sha256=file_sha256,
            page=page_no,
            cfg=cfg,
        )
        routing = route_page(
            page=page,
            image_path=rendered.get("image_ref"),
            cfg=cfg,
            text_layer_info=tl,
        )
        blocks = extract_blocks(page, page_no) if routing.get("use_text_layer") else []
        rec = {
            "filename": pdf_path.name,
            "path": str(pdf_path),
            "page": page_no,
            "label": label,
            "render": {
                "image_ref": rendered.get("image_ref"),
                "width_px": rendered.get("width_px"),
                "height_px": rendered.get("height_px"),
                "width_pdf": rendered.get("width_pdf"),
                "height_pdf": rendered.get("height_pdf"),
                "rotation": rendered.get("rotation"),
                "render_sec": rendered.get("render_sec"),
            },
            "text_layer": {
                "usable": tl.get("usable"),
                "reasons": tl.get("reasons"),
                "chars": tl.get("chars"),
                "garbled_ratio": tl.get("garbled_ratio"),
                "script_ratio": tl.get("script_ratio"),
                "image_coverage": tl.get("image_coverage"),
                "median_font_pt": tl.get("median_font_pt"),
            },
            "route": routing,
            "pii": reasons,
            # pii.enabled=false（2026-11-18 donghao 本人批准）时只记录命中原因，不拦截
            "pii_blocked": bool(reasons) and bool((cfg.get("pii") or {}).get("enabled", True)),
            "external_allowed_for_page": (not (bool(reasons) and bool((cfg.get("pii") or {}).get("enabled", True)))) and allow_external(cfg),
        }
        # 仅非 PII 且走文字层时，把块留在内部供序列化；PII 页不把正文写入 JSON 摘要
        rec["_internal"] = {
            "text_layer_text": tl.get("text") if not reasons else None,
            "text_layer_blocks": blocks if routing.get("use_text_layer") and not reasons else blocks if routing.get("use_text_layer") else [],
            "mapper": rendered.get("mapper"),
            "rendered": rendered,
            "tl_full": tl,
        }
        if reasons:
            rec["blocked"] = blocked_record(pdf_path.name, page_no, reasons)
        return rec
    finally:
        doc.close()


def run_local_inspect(cfg: dict, page_specs: list[dict], out_dir: Path) -> list[dict]:
    load_known_pages(cfg)
    dpi = float(cfg["render"]["dpi"])
    zoom = float(cfg["render"]["zoom"])
    results = []
    for spec in page_specs:
        pdf_path = Path(spec["pdf"])
        pages = [int(p) for p in spec.get("pages") or []]
        label = spec.get("label") or ""
        if not pdf_path.exists():
            results.append({"filename": pdf_path.name, "error": "file_not_found", "path": str(pdf_path), "label": label})
            continue
        file_sha = sha256_file(pdf_path)
        dest = out_dir / "renders" / pdf_path.stem
        rendered_pages = render_pages(pdf_path, dest, dpi, zoom, pages=pages or None)
        by_no = {r["page"]: r for r in rendered_pages}
        targets = pages or [r["page"] for r in rendered_pages]
        for page_no in targets:
            if page_no not in by_no:
                results.append({"filename": pdf_path.name, "page": page_no, "error": "page_not_found", "label": label})
                continue
            rec = inspect_page(
                pdf_path=pdf_path,
                page_no=page_no,
                rendered=by_no[page_no],
                cfg=cfg,
                file_sha256=file_sha,
                label=label,
            )
            results.append(rec)
    return results


def _public_inspect(rec: dict) -> dict:
    """去掉内部正文，供写盘。"""
    return {k: v for k, v in rec.items() if k != "_internal"}


def apply_scheme_a_to_page(
    rec: dict,
    cfg: dict,
    client: BaiduOcrClient,
    mock: dict | None = None,
    mock_retry: dict | None = None,
) -> dict:
    """对单页执行方案 A（文字层或 OCR）。PII 或关闭外发时不请求。"""
    page_no = rec["page"]
    mapper = (rec.get("_internal") or {}).get("mapper")
    dpi = float(cfg["render"]["dpi"])
    routing = rec.get("route") or {}
    if rec.get("pii_blocked"):
        return {
            "page": page_no,
            "skipped": True,
            "reason": "pii_blocked",
            "blocks": [],
            "route": routing,
        }
    if routing.get("use_text_layer"):
        blocks = (rec.get("_internal") or {}).get("text_layer_blocks") or []
        for b in blocks:
            b["source"] = "text_layer"
        return {
            "page": page_no,
            "skipped": False,
            "path": "text_layer",
            "blocks": blocks,
            "route": routing,
        }

    api = routing.get("api") or "general"
    image_path = rec.get("render", {}).get("image_ref")
    expected = []
    if (cfg.get("coverage") or {}).get("use_projection_lines", True) and image_path:
        expected = projection_line_boxes(image_path, cfg)
    iou_thr = float((cfg.get("coverage") or {}).get("iou_threshold") or 0.3)

    def cov_fn(words):
        return check_coverage(expected, words, iou_threshold=iou_thr)

    mocks = [m for m in (mock, mock_retry) if m is not None]
    packed = client.recognize_with_retry(
        image_path,
        api,
        coverage_check=cov_fn,
        page=page_no,
        filename=rec.get("filename"),
        mock_responses=mocks or None,
    )
    result = packed.get("result") or {}
    words = ((result.get("response") or {}).get("words_result")) or []
    # 按坐标重排
    width_px = rec.get("render", {}).get("width_px") or 0
    words = sort_lines_reading_order(words, float(width_px or 1))
    blocks = words_to_internal_blocks(words, page_no, mapper, dpi)
    # 表格页：把全部文字聚成一张表，同时保留行块？方案 A 要求按单元格坐标重建表格
    if (routing.get("page_class") or "") in {"table", "table_and_seal"} and words:
        table = rebuild_table_from_words(words)
        # 用所有行的外接框
        pixels = [b["bbox"]["pixel"] for b in blocks if b.get("bbox", {}).get("pixel")]
        if pixels:
            box_px = [
                min(p[0] for p in pixels),
                min(p[1] for p in pixels),
                max(p[2] for p in pixels),
                max(p[3] for p in pixels),
            ]
            box_pdf = mapper.pixel_to_pdf(box_px) if mapper is not None else None
        else:
            box_px, box_pdf = None, None
        table_block = {
            "id": f"b-{page_no:03d}-tbl",
            "block_type": "table",
            "bbox": {"pdf": box_pdf, "pixel": box_px},
            "text": "",
            "confidence": 0.9,
            "source": "ocr",
            "table": table,
        }
        blocks = [table_block]
    gaps = packed.get("still_missing") or []
    if gaps:
        blocks.extend(coverage_blocks_from_gaps(gaps, page_no, len(blocks), mapper))
    return {
        "page": page_no,
        "skipped": False,
        "path": routing.get("path"),
        "api": api,
        "dry_run": result.get("dry_run"),
        "coverage": packed.get("coverage"),
        "retried": packed.get("retried"),
        "still_missing": gaps,
        "blocks": blocks,
        "route": routing,
    }


def scheme_b_blocks_from_parse(parse_result: dict, pages_meta: list[dict], dpi: float) -> dict[int, list[dict]]:
    """把文档解析 JSON 转为内部块。page_num 官方从 0 起。"""
    out: dict[int, list[dict]] = {}
    meta_by = {int(p["page"]): p for p in pages_meta}
    for page in (parse_result or {}).get("pages") or []:
        page_no = int(page.get("page_num") or 0) + 1
        mapper = (meta_by.get(page_no) or {}).get("mapper")
        width_pt = float((meta_by.get(page_no) or {}).get("width_pdf") or 0)
        height_pt = float((meta_by.get(page_no) or {}).get("height_pdf") or 0)
        pw = float(((page.get("meta") or {}).get("page_width")) or 0)
        ph = float(((page.get("meta") or {}).get("page_height")) or 0)
        # 判断返回坐标是 pt 还是 px
        unit_pt = False
        if pw and width_pt and abs(pw - width_pt) / max(width_pt, 1) < 0.15:
            unit_pt = True
        blocks = []
        idx = 0
        tables = {id(t): t for t in (page.get("tables") or [])}
        # layouts 优先
        for layout in page.get("layouts") or []:
            idx += 1
            typ = LAYOUT_TYPE.get((layout.get("type") or "text").lower(), "text")
            pos = layout.get("position") or []
            if len(pos) == 4:
                x, y, w, h = (float(v) for v in pos)
                raw_box = [x, y, x + w, y + h]
            elif layout.get("polygon"):
                xs = [p[0] for p in layout["polygon"]]
                ys = [p[1] for p in layout["polygon"]]
                raw_box = [min(xs), min(ys), max(xs), max(ys)]
            else:
                raw_box = [0, 0, 1, 1]
            if unit_pt:
                pdf, pixel = raw_box, (mapper.pdf_to_pixel(raw_box) if mapper else None)
            else:
                pixel = raw_box
                pdf = mapper.pixel_to_pdf(raw_box) if mapper else [v * 72.0 / dpi for v in raw_box]
            blk = {
                "id": f"b-{page_no:03d}-{idx:03d}",
                "block_type": typ,
                "bbox": {"pdf": pdf, "pixel": pixel},
                "text": layout.get("text") or "",
                "confidence": 0.9,
                "source": "ocr",
            }
            if typ == "table":
                # 找同页 tables 中位置接近的一项
                chosen = None
                for t in page.get("tables") or []:
                    chosen = t
                    break
                if chosen:
                    blk["table"] = table_from_docparser(chosen)
                    blk["table_bbox_unit"] = "pt" if unit_pt else "px"
            blocks.append(blk)
        # 若 layouts 里没有 table/seal，但 tables 有
        if not any(b["block_type"] == "table" for b in blocks):
            for t in page.get("tables") or []:
                idx += 1
                blocks.append(
                    {
                        "id": f"b-{page_no:03d}-{idx:03d}",
                        "block_type": "table",
                        "bbox": {"pdf": [0, 0, width_pt or 1, height_pt or 1], "pixel": None},
                        "text": "",
                        "confidence": 0.9,
                        "source": "ocr",
                        "table": table_from_docparser(t),
                    }
                )
        out[page_no] = blocks
    return out


def public_summary(pages: list[dict]) -> list[dict]:
    return [_public_inspect(p) if "_internal" in p else p for p in pages]


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
