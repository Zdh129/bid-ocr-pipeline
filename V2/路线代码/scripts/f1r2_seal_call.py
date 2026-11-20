#!/usr/bin/env python3
"""f1_round2 变体 1 的外发部分：seal_leak.select 选出的块 → 去红裁图拼页 → 百度 accurate → 结果映回块。默认 dry-run。"""
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
    from baidu_ocr.postprocess import seal_leak as S
    ap = argparse.ArgumentParser()
    ap.add_argument("--b-doc", required=True)
    ap.add_argument("--render-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--label", default="seal-leak")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--allow-external", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    units = S.select(json.loads(Path(a.b_doc).read_text(encoding="utf-8")), Path(a.render_dir))
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
        img = S.dered(cv2.imread(str(Path(a.render_dir) / f"page-{p:04d}.png")))
        canvas, metas, scale = A.build_composite(img, [u["box_px"] for u in us], pad=0)
        cp = out / "crops" / f"p{p:02d}_dered.png"
        cp.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(cp), canvas)
        resp, _ = A.call(client, cp, "accurate", p, "sealleak", out / "raw", log)
        words = A.map_back(list((resp or {}).get("words_result") or []), metas, scale)
        for u, m in zip(us, metas):
            X0, Y0, X1, Y1 = m["src"]
            mine = sorted([w for w in words if X0 - 2 <= w["location"]["left"] + w["location"]["width"] / 2 <= X1 + 2
                           and Y0 - 2 <= w["location"]["top"] + w["location"]["height"] / 2 <= Y1 + 2],
                          key=lambda w: (w["location"]["top"], w["location"]["left"]))
            res.append({**u, "r_text": "".join(w["words"] for w in mine) if resp is not None else None})
    A.wj(out / "results.json", {"thresholds": {"RED_T": S.RED_T, "SEAL_OV": S.SEAL_OV, "WHOLE_T": S.WHOLE_T, "MIN_RUN": S.MIN_RUN}, "units": res, "call_log": log})
    A.wj(out / "cost.json", tracker.summary())
    print(json.dumps({"calls": len(pages), "cost_cny": tracker.summary().get("total_cny")}, ensure_ascii=False))
    for r in res:
        print(r["page"], repr(r["text"][:30]), "->", repr((r["r_text"] or "")[:30]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
