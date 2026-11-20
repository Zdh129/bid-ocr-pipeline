#!/usr/bin/env python3
"""按置信度二次识别的外发部分：选取低置信度行（reread.select_units，阈值见该模块）→ 按页拼图 → 百度 accurate → 结果映回行。
默认 dry-run；--live 才调用（需配置 allow_external=true 且命令行 --allow-external）。日志不写 token。

  --a-dir   A（accurate 含位置）输出目录（含 call_summary.json、raw/、renders/）
  --b-doc   B 中间 JSON（用于排除表格区）
  --out     结果目录（results.json、raw/、crops/、cost.json）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import cv2
    import opt2_scheme_a_call as A
    from baidu_ocr.client import BaiduOcrClient
    from baidu_ocr.config import load_config
    from baidu_ocr.cost import CostTracker
    from baidu_ocr.postprocess import reread as R
    ap = argparse.ArgumentParser()
    ap.add_argument("--a-dir", required=True)
    ap.add_argument("--b-doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=str(ROOT / "config_synth18opt2_live.yaml"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--allow-external", action="store_true")
    ap.add_argument("--label", default="reread")
    a = ap.parse_args()
    a_dir, out = Path(a.a_dir), Path(a.out)
    units = R.select_units(a_dir, json.loads(Path(a.b_doc).read_text(encoding="utf-8")))
    pages = sorted({u["page"] for u in units})
    print(json.dumps({"units": len(units), "pages": pages, "calls_needed": len(pages), "api": "accurate"}, ensure_ascii=False))
    if not a.live:
        return 0
    cfg = load_config(a.config)
    if not (cfg.get("allow_external") and a.allow_external):
        print("拒绝：需要配置 allow_external=true 且 --allow-external")
        return 2
    tracker = CostTracker.from_config(cfg)
    client = BaiduOcrClient(cfg, tracker)
    A.PDF_NAME = a.label
    log, res = [], []
    for p in pages:
        us = [u for u in units if u["page"] == p]
        img = cv2.imread(str(a_dir / "renders" / f"page-{p:04d}.png"))
        canvas, metas, scale = A.build_composite(img, [u["box_px"] for u in us])
        cp = out / "crops" / f"p{p:02d}_reread.png"
        cp.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(cp), canvas)
        resp, _ = A.call(client, cp, "accurate", p, "reread", out / "raw", log)
        words = A.map_back(list((resp or {}).get("words_result") or []), metas, scale)
        for u, m in zip(us, metas):
            X0, Y0, X1, Y1 = m["src"]
            mine = [w for w in words if X0 - 2 <= w["location"]["left"] + w["location"]["width"] / 2 <= X1 + 2
                    and Y0 - 2 <= w["location"]["top"] + w["location"]["height"] / 2 <= Y1 + 2]
            mine.sort(key=lambda w: (w["location"]["top"], w["location"]["left"]))
            txt = "".join(w["words"] for w in mine)
            prs = [(w.get("probability") or {}).get("average") for w in mine if (w.get("probability") or {}).get("average") is not None]
            res.append({**u, "r_text": txt, "pr": (sum(prs) / len(prs)) if prs else None, "n_words": len(mine)})
    A.wj(out / "results.json", {"thresholds": {"AVG_T": R.AVG_T, "MIN_T": R.MIN_T, "SIM_T": R.SIM_T}, "units": res, "call_log": log})
    A.wj(out / "cost.json", tracker.summary())
    print(json.dumps({"calls": len(pages), "cost": tracker.summary().get("total_cny")}, ensure_ascii=False))
    for r in res:
        print(r["id"], repr(r["a_text"][:30]), "->", repr(r["r_text"][:30]), r["pa"], r["pr"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
