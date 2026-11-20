#!/usr/bin/env python3
"""跑方案 A。默认 dry-run。只有 config.allow_external=true 且传入 --allow-external 才会发请求。

真实外发还需要用户本人书面批准，本脚本不会替用户打开开关。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.client import BaiduOcrClient
from baidu_ocr.config import load_config, resolve_path
from baidu_ocr.cost import CostTracker
from baidu_ocr.frozen import check_frozen20
from baidu_ocr.pipeline import apply_scheme_a_to_page, public_summary, run_local_inspect, write_json
from baidu_ocr.serialize import build_document, validate_schema


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--input", default="", help="PDF 文件或目录（优先 frozen20）")
    ap.add_argument("--out", default="")
    ap.add_argument("--allow-external", action="store_true", help="必须与配置中 allow_external=true 同时成立")
    ap.add_argument("--require-frozen", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.allow_external and not cfg.get("allow_external"):
        print("拒绝：命令行 --allow-external 不能单独打开外发。请先在专用配置里把 allow_external 设为 true，且须有用户本人批准。")
        return 2
    if cfg.get("allow_external") and not args.allow_external:
        print("配置允许外发，但未带 --allow-external，按 dry-run 执行。")
        cfg["allow_external"] = False
    if args.require_frozen:
        st = check_frozen20(cfg)
        if not st["ready"]:
            print("冻结集未就绪：", st["errors"])
            return 1
        input_dir = Path(st["scan_pdf"]) if st.get("scan_pdf") else Path(st["dir"])
    else:
        input_dir = Path(args.input) if args.input else Path((cfg.get("frozen20") or {}).get("dir") or "/workspace/pdf-test/frozen20")

    specs = []
    if input_dir.is_file() and input_dir.suffix.lower() == ".pdf":
        specs = [{"pdf": str(input_dir), "pages": None, "label": "input"}]
    elif input_dir.is_dir():
        for p in sorted(input_dir.glob("*.pdf")):
            specs.append({"pdf": str(p), "pages": None, "label": "input"})
    if not specs:
        print(f"未找到 PDF：{input_dir}")
        return 1

    out_dir = Path(args.out) if args.out else resolve_path(cfg, "output", "scheme_a")
    out_dir.mkdir(parents=True, exist_ok=True)
    inspects = run_local_inspect(cfg, specs, out_dir)
    write_json(out_dir / "inspect.json", public_summary(inspects))

    tracker = CostTracker.from_config(cfg)
    client = BaiduOcrClient(cfg, tracker)
    blocks_by: dict[tuple[str, int], list] = {}
    # 按文件序列化
    by_file: dict[str, list] = {}
    for rec in inspects:
        if rec.get("error"):
            continue
        by_file.setdefault(rec["path"], []).append(rec)
    schema_path = resolve_path(cfg, cfg.get("paths", {}).get("schema_path") or "schemas/intermediate.schema.json")
    dpi = float(cfg["render"]["dpi"])
    for path, recs in by_file.items():
        pages_meta = []
        blocks_by_page = {}
        for rec in recs:
            result = apply_scheme_a_to_page(rec, cfg, client)
            blocks_by_page[rec["page"]] = result.get("blocks") or []
            meta = dict((rec.get("_internal") or {}).get("rendered") or {})
            meta["page"] = rec["page"]
            meta["mapper"] = (rec.get("_internal") or {}).get("mapper")
            pages_meta.append(meta)
        doc = build_document(
            source_file=path,
            pages_meta=pages_meta,
            blocks_by_page=blocks_by_page,
            dpi=dpi,
            source_type="mixed",
            data_label="production" if cfg.get("allow_external") else "constructed",
            assets_dir=out_dir / "assets",
            engine_notes=f"scheme=A allow_external={cfg.get('allow_external')}",
        )
        stem = Path(path).stem
        write_json(out_dir / f"{stem}.json", doc)
        errs = validate_schema(doc, schema_path)
        write_json(out_dir / f"{stem}.schema_errors.json", errs)
    write_json(out_dir / "cost.json", tracker.summary())
    print(json.dumps({"out": str(out_dir), "allow_external": cfg.get("allow_external"), "cost": tracker.summary()}, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
