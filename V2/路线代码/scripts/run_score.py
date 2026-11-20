#!/usr/bin/env python3
"""用 ChatGPT 网页参照给中间 JSON 计分。须先通过冻结参照的 SHA256 校验（除非 --allow-constructed-ref）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.config import load_config
from baidu_ocr.frozen import check_frozen20, sha256_file
from baidu_ocr.pipeline import write_json
from baidu_ocr.score import extract_pred_sections, load_reference, score_by_sections


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, help="方案输出的中间 JSON")
    ap.add_argument("--ref", required=True, help="ChatGPT 网页参照（json 或 txt）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-constructed-ref", action="store_true", help="仅用于单元/dry-run 的构造参照")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    ref_path = Path(args.ref)
    if not args.allow_constructed_ref:
        digest = sha256_file(ref_path)
        prefix = ((cfg.get("frozen20") or {}).get("reference_sha256_prefix") or "da93f43f").lower()
        if not digest.startswith(prefix):
            print(f"拒绝：参照 SHA256={digest} 不以 {prefix} 开头，可能不是冻结的 ChatGPT 参照。")
            print("不要用文字层或其他输出替代。若这是构造测试，请加 --allow-constructed-ref。")
            return 2
    pred = json.loads(Path(args.pred).read_text(encoding="utf-8"))
    ref = load_reference(ref_path)
    pred_sec = extract_pred_sections(pred)
    if "pages" in ref and ref["pages"]:
        # 若参照按页给了正文/表/章，拼接
        body, table, seal, overall = [], [], [], []
        for p in ref["pages"]:
            body.append(p.get("body") or "")
            table.append(p.get("table") or "")
            seal.append(p.get("seal") or "")
            overall.append(p.get("text") or p.get("overall") or "")
        ref_sec = {
            "body": "\n".join(body) if any(body) else ref.get("body") or "",
            "table": "\n".join(table) if any(table) else ref.get("table") or "",
            "seal": "\n".join(seal) if any(seal) else ref.get("seal") or "",
            "overall": "\n".join(overall) if any(overall) else ref.get("text") or "",
        }
    else:
        ref_sec = {
            "overall": ref.get("text") or "",
            "body": ref.get("body"),
            "table": ref.get("table"),
            "seal": ref.get("seal"),
        }
        ref_sec = {k: v for k, v in ref_sec.items() if v is not None}
        ref_sec.setdefault("overall", "")
    scores = score_by_sections(pred_sec, ref_sec)
    write_json(Path(args.out), scores)
    print(json.dumps(scores, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
