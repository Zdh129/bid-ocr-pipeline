#!/usr/bin/env python3
"""端到端 dry-run：渲染、分流、PII；再用模拟百度 JSON 测解析/重排/覆盖/序列化/计分。

不调用任何外部接口。难页样本没有 ChatGPT 参照，不报告这些页的准确率。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.client import BaiduOcrClient
from baidu_ocr.config import load_config, resolve_path
from baidu_ocr.cost import CostTracker
from baidu_ocr.docparser import DocParserClient
from baidu_ocr.pipeline import (
    apply_scheme_a_to_page,
    public_summary,
    run_local_inspect,
    scheme_b_blocks_from_parse,
    write_json,
)
from baidu_ocr.report import build_report, write_report
from baidu_ocr.score import extract_pred_sections, score_by_sections, score_text
from baidu_ocr.serialize import build_document, validate_schema
from baidu_ocr.frozen import check_frozen20


def main() -> int:
    t0 = time.perf_counter()
    cfg = load_config()
    if cfg.get("allow_external"):
        print("拒绝：dry-run 要求 allow_external=false")
        return 2
    out_dir = resolve_path(cfg, "output", "dry-run")
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures = ROOT / "data" / "fixtures"
    mock_general = json.loads((fixtures / "mock_general.json").read_text(encoding="utf-8"))
    mock_general_gap = json.loads((fixtures / "mock_general_missing_line.json").read_text(encoding="utf-8"))
    mock_accurate = json.loads((fixtures / "mock_accurate_table.json").read_text(encoding="utf-8"))
    mock_doc = json.loads((fixtures / "mock_docparser_standard.json").read_text(encoding="utf-8"))
    ref = json.loads((fixtures / "mock_chatgpt_reference.json").read_text(encoding="utf-8"))

    specs = list((cfg.get("dry_run") or {}).get("pages") or [])
    inspects = run_local_inspect(cfg, specs, out_dir)
    inspect_public = public_summary(inspects)
    write_json(out_dir / "inspect.json", inspect_public)

    # ---- 用构造样本 + 模拟返回走方案 A/B 的解析链路 ----

    # 另造一页无文字层的图 PDF，专门验证方案 A 的覆盖检查与重试（仍不发请求）
    from PIL import Image
    import pymupdf as fitz
    constructed_recs = [r for r in inspects if r.get("label") == "constructed" and not r.get("error")]
    if constructed_recs:
        src_img = constructed_recs[0]["render"]["image_ref"]
        img_pdf = out_dir / "image_only_constructed.pdf"
        im = Image.open(src_img)
        # 用光栅页写入 PDF，去掉文字层
        tmp_png = out_dir / "image_only_constructed.png"
        im.save(tmp_png)
        doc = fitz.open()
        page = doc.new_page(width=im.width * 72 / float(cfg["render"]["dpi"]), height=im.height * 72 / float(cfg["render"]["dpi"]))
        page.insert_image(page.rect, filename=str(tmp_png))
        doc.save(img_pdf)
        doc.close()
        extra = run_local_inspect(cfg, [{"pdf": str(img_pdf), "pages": [1], "label": "constructed_image_only"}], out_dir)
        inspects.extend(extra)
        inspect_public = public_summary(inspects)
        write_json(out_dir / "inspect.json", inspect_public)

    constructed = [r for r in inspects if r.get("label") == "constructed" and not r.get("error")]
    tracker_a = CostTracker.from_config(cfg)
    client = BaiduOcrClient(cfg, tracker_a)
    blocks_a: dict[int, list] = {}
    pages_meta = []
    scheme_a_pages = []
    for rec in constructed:
        mocks = None
        api = (rec.get("route") or {}).get("api")
        # 对正文页注入「缺一行再补全」的模拟，以验证覆盖检查与重试
        if api == "general" or (rec.get("route") or {}).get("page_class") == "body":
            page_result = apply_scheme_a_to_page(rec, cfg, client, mock=mock_general_gap, mock_retry=mock_general)
        elif rec.get("route", {}).get("use_text_layer"):
            page_result = apply_scheme_a_to_page(rec, cfg, client)
        else:
            page_result = apply_scheme_a_to_page(rec, cfg, client, mock=mock_accurate, mock_retry=mock_accurate)
        scheme_a_pages.append({k: v for k, v in page_result.items() if k != "blocks"})
        blocks_a[rec["page"]] = page_result.get("blocks") or []
        meta = dict((rec.get("_internal") or {}).get("rendered") or {})
        meta["page"] = rec["page"]
        meta["mapper"] = (rec.get("_internal") or {}).get("mapper")
        pages_meta.append(meta)

    dpi = float(cfg["render"]["dpi"])
    src = cfg.get("paths", {}).get("constructed_sample") or "/workspace/pdf-test/ocr/data/sample/sample_tender.pdf"
    # 文字层页：用文字层块，不要用模拟 OCR 覆盖
    for rec in constructed:
        if rec.get("route", {}).get("use_text_layer"):
            blocks_a[rec["page"]] = (rec.get("_internal") or {}).get("text_layer_blocks") or blocks_a.get(rec["page"]) or []

    # 覆盖检查演示：对无文字层页先用缺行模拟，再重试完整模拟
    coverage_demo = []
    for rec in inspects:
        if rec.get("label") != "constructed_image_only" or rec.get("error"):
            continue
        demo = apply_scheme_a_to_page(rec, cfg, client, mock=mock_general_gap, mock_retry=mock_general)
        coverage_demo.append({k: v for k, v in demo.items() if k != "blocks"} | {"n_blocks": len(demo.get("blocks") or []), "n_gaps": len(demo.get("still_missing") or [])})
    write_json(out_dir / "coverage_demo.json", coverage_demo)

    notes_a = "dry-run；构造样本 + 模拟百度返回。allow_external=false。"

    doc_a = build_document(
        source_file=src,
        pages_meta=pages_meta,
        blocks_by_page=blocks_a,
        dpi=dpi,
        source_type="pdf",
        data_label="constructed",
        engine_notes=notes_a + " scheme=A",
        assets_dir=out_dir / "assets_a",
    )
    schema_path = resolve_path(cfg, cfg.get("paths", {}).get("schema_path") or "schemas/intermediate.schema.json")
    errs_a = validate_schema(doc_a, schema_path)
    write_json(out_dir / "scheme_a.json", doc_a)

    tracker_b = CostTracker.from_config(cfg)
    dclient = DocParserClient(cfg, tracker_b, edition="standard")
    parsed = dclient.parse_pdf(src, mock_result=mock_doc, billed_pages=len(constructed))
    blocks_b = scheme_b_blocks_from_parse(parsed.get("parse_result") or mock_doc, pages_meta, dpi)
    doc_b = build_document(
        source_file=src,
        pages_meta=pages_meta,
        blocks_by_page=blocks_b,
        dpi=dpi,
        source_type="pdf",
        data_label="constructed",
        engine_notes=notes_a + " scheme=B edition=standard",
        assets_dir=out_dir / "assets_b",
    )
    errs_b = validate_schema(doc_b, schema_path)
    write_json(out_dir / "scheme_b.json", doc_b)

    # 计分只用构造参照，明确标注这不是冻结 20 页验收
    pred_a = extract_pred_sections(doc_a)
    pred_b = extract_pred_sections(doc_b)
    ref_sections = {
        "overall": ref.get("text") or "",
        "body": ref.get("body") or "",
        "table": ref.get("table") or "",
        "seal": ref.get("seal") or "",
    }
    scores_a = score_by_sections(pred_a, ref_sections)
    scores_b = score_by_sections(pred_b, ref_sections)

    frozen = check_frozen20(cfg)
    report = build_report(
        scheme_a={
            "scores": scores_a,
            "cost": tracker_a.summary(),
            "elapsed_sec": round(sum(p.get("render", {}).get("render_sec") or 0 for p in inspect_public if p.get("label")=="constructed"), 4),
            "apis": "text_layer / general / accurate",
            "schema_errors": errs_a,
            "pages": scheme_a_pages,
        },
        scheme_b={
            "scores": scores_b,
            "cost": tracker_b.summary(),
            "elapsed_sec": parsed.get("elapsed_sec"),
            "edition": parsed.get("edition_name") or "文档解析（标准版）",
            "schema_errors": errs_b,
        },
        readout_thr=float((cfg.get("score") or {}).get("readout_threshold") or 0.99),
        cer_thr=float((cfg.get("score") or {}).get("cer_threshold") or 0.01),
        frozen20_ready=frozen["ready"],
        notes=[
            "本次为 dry-run，未调用百度接口，费用记 0。",
            "计分只用构造样本的模拟参照，用来验证计分器，不能当作验收成绩。",
            "难页样本已做渲染/分流/PII，因没有 ChatGPT 网页参照，不报告准确率。",
            f"schema A 校验问题数: {len(errs_a)}；schema B 校验问题数: {len(errs_b)}。",
        ],
    )
    write_report(report, out_dir)
    write_json(out_dir / "frozen20_status.json", frozen)

    n = len(inspect_public)
    n_err = sum(1 for p in inspect_public if p.get("error"))
    n_pii = sum(1 for p in inspect_public if p.get("pii_blocked"))
    n_tl = sum(1 for p in inspect_public if (p.get("route") or {}).get("use_text_layer"))
    by_class = {}
    for p in inspect_public:
        if p.get("error"):
            continue
        cls = (p.get("route") or {}).get("page_class") or "unknown"
        by_class[cls] = by_class.get(cls, 0) + 1
    elapsed = time.perf_counter() - t0
    summary = {
        "elapsed_sec": round(elapsed, 3),
        "pages_inspected": n,
        "errors": n_err,
        "pii_blocked": n_pii,
        "text_layer_pages": n_tl,
        "page_class_counts": by_class,
        "schema_errors_a": errs_a,
        "schema_errors_b": errs_b,
        "allow_external": False,
        "frozen20_ready": frozen["ready"],
        "output_dir": str(out_dir),
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
