#!/usr/bin/env python3
"""跑方案 B（文档解析）。默认 dry-run。edition=standard 或 unlimited。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.config import load_config, resolve_path
from baidu_ocr.cost import CostTracker
from baidu_ocr.docparser import DocParserClient
from baidu_ocr.frozen import check_frozen20
from baidu_ocr.pipeline import public_summary, run_local_inspect, scheme_b_blocks_from_parse, write_json
from baidu_ocr.pii_gate import sha256_file
from baidu_ocr.serialize import build_document, validate_schema


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--input", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--edition", choices=["standard", "unlimited"], default="")
    ap.add_argument("--allow-external", action="store_true")
    ap.add_argument("--require-frozen", action="store_true")
    ap.add_argument("--mock", default="", help="dry-run 时使用的模拟 parse_result JSON")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.edition:
        cfg.setdefault("docparser", {})["edition"] = args.edition
    if args.allow_external and not cfg.get("allow_external"):
        print("拒绝：命令行 --allow-external 不能单独打开外发。")
        return 2
    if cfg.get("allow_external") and not args.allow_external:
        cfg["allow_external"] = False

    if args.require_frozen:
        st = check_frozen20(cfg)
        if not st["ready"]:
            print("冻结集未就绪：", st["errors"])
            return 1
        input_dir = Path(st["scan_pdf"]) if st.get("scan_pdf") else Path(st["dir"])
        if st.get("scan_pdf"):
            assert input_dir.name == (cfg.get("frozen20") or {}).get("ocr_input"), "只允许外发 ocr_input"
    else:
        input_dir = Path(args.input) if args.input else Path((cfg.get("frozen20") or {}).get("dir") or "/workspace/pdf-test/frozen20")

    pdfs = []
    if input_dir.is_file() and input_dir.suffix.lower() == ".pdf":
        pdfs = [input_dir]
    elif input_dir.is_dir():
        pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        print(f"未找到 PDF：{input_dir}")
        return 1

    out_dir = Path(args.out) if args.out else resolve_path(cfg, "output", "scheme_b")
    out_dir.mkdir(parents=True, exist_ok=True)
    mock = None
    if args.mock:
        mock = json.loads(Path(args.mock).read_text(encoding="utf-8"))

    # 先本地检查 PII，避免整份带证件页的 PDF 被提交
    specs = [{"pdf": str(p), "pages": None, "label": "input"} for p in pdfs]
    inspects = run_local_inspect(cfg, specs, out_dir)
    write_json(out_dir / "inspect.json", public_summary(inspects))

    tracker = CostTracker.from_config(cfg)
    client = DocParserClient(cfg, tracker)
    dpi = float(cfg["render"]["dpi"])
    schema_path = resolve_path(cfg, cfg.get("paths", {}).get("schema_path") or "schemas/intermediate.schema.json")

    by_file: dict[str, list] = {}
    for rec in inspects:
        if rec.get("error"):
            continue
        by_file.setdefault(rec["path"], []).append(rec)

    for path, recs in by_file.items():
        pii_pages = [r["page"] for r in recs if r.get("pii_blocked")]
        # 官方 page_filter 从 0 开始
        keep = [r["page"] - 1 for r in recs if not r.get("pii_blocked")]
        pages_meta = []
        for rec in recs:
            meta = dict((rec.get("_internal") or {}).get("rendered") or {})
            meta["page"] = rec["page"]
            meta["mapper"] = (rec.get("_internal") or {}).get("mapper")
            pages_meta.append(meta)
        if pii_pages and not keep:
            write_json(out_dir / f"{Path(path).stem}.skipped.json", {"reason": "all_pages_pii", "pages": pii_pages})
            continue
        page_filter = keep if pii_pages else None
        parsed = client.parse_pdf(path, page_filter=page_filter, mock_result=mock, billed_pages=len(keep) if keep else None)
        write_json(out_dir / f"{Path(path).stem}.raw.json", {k: v for k, v in parsed.items() if k not in ("parse_result", "markdown")} | {"has_parse_result": bool(parsed.get("parse_result"))})
        if parsed.get("parse_result") is not None:
            write_json(out_dir / "raw" / f"{Path(path).stem}.parse_result.json", parsed["parse_result"])
        if parsed.get("markdown"):
            (out_dir / "raw").mkdir(parents=True, exist_ok=True)
            (out_dir / "raw" / f"{Path(path).stem}.md").write_text(parsed["markdown"], encoding="utf-8")
        blocks = scheme_b_blocks_from_parse(parsed.get("parse_result") or {}, pages_meta, dpi)
        doc = build_document(
            source_file=path,
            pages_meta=pages_meta,
            blocks_by_page=blocks,
            dpi=dpi,
            source_type="mixed",
            data_label="production" if cfg.get("allow_external") else "constructed",
            assets_dir=out_dir / "assets",
            engine_notes=f"scheme=B edition={client.edition} allow_external={cfg.get('allow_external')} dropped_pii_pages={pii_pages}",
        )
        write_json(out_dir / f"{Path(path).stem}.json", doc)
        write_json(out_dir / f"{Path(path).stem}.schema_errors.json", validate_schema(doc, schema_path))
    write_json(out_dir / "cost.json", tracker.summary())
    print(json.dumps({"out": str(out_dir), "edition": client.edition, "allow_external": cfg.get("allow_external"), "cost": tracker.summary()}, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
