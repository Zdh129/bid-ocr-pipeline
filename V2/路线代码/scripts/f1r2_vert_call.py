#!/usr/bin/env python3
"""f1_round2 变体 N2 的外发部分：vertical_margin.detect 找到的页边竖排文字条 → 三种转向图各自单独 → 百度 accurate。默认 dry-run。"""
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
    import numpy as np
    import opt2_scheme_a_call as A
    from baidu_ocr.client import BaiduOcrClient
    from baidu_ocr.config import load_config
    from baidu_ocr.cost import CostTracker
    from baidu_ocr.postprocess import vertical_margin as V
    ap = argparse.ArgumentParser()
    for k in ("--b-doc", "--render-dir", "--out", "--config"):
        ap.add_argument(k, required=True)
    ap.add_argument("--label", default="vertical-margin")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--allow-external", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    units = V.detect(json.loads(Path(a.b_doc).read_text(encoding="utf-8")), Path(a.render_dir))
    pages = sorted({u["page"] for u in units})
    print(json.dumps({"strips": len(units), "pages": pages, "calls_needed": "每条 2~3 张图", "api": "accurate"}, ensure_ascii=False))
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
    A.PARAMS = {**A.PARAMS, "detect_direction": "false"}   # 三种转向各自单独成图；关掉整图方向检测（拼在一起时会被整体转向）
    for p in pages:
        gray = cv2.imread(str(Path(a.render_dir) / f"page-{p:04d}.png"), cv2.IMREAD_GRAYSCALE)
        us = [u for u in units if u["page"] == p]
        for ui, u in enumerate(us):
            reads = {}
            for name, im in V.variants(gray, u["box_px"]).items():
                im = cv2.resize(im, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                canvas = np.full((im.shape[0] + 80, im.shape[1] + 80), 255, np.uint8)
                canvas[40:40 + im.shape[0], 40:40 + im.shape[1]] = im
                cp = out / "crops" / f"p{p:02d}_{u['side']}{ui}_{name}.png"
                cp.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(cp), canvas)
                resp, _ = A.call(client, cp, "accurate", p, f"vert{u['side']}{ui}{name}", out / "raw", log)
                ws = sorted((resp or {}).get("words_result") or [], key=lambda w: (w["location"]["top"] // 30, w["location"]["left"]))
                pr = [w.get("probability", {}).get("average", 0.0) for w in ws]
                reads[name] = {"text": "".join(w["words"] for w in ws), "prob": round(sum(pr) / len(pr), 4) if pr else 0.0}
            best = max(({"variant": k, **v} for k, v in reads.items() if v["text"].strip()), key=lambda r: r["prob"], default=None)
            res.append({**u, "reads": reads, "best": best})
    A.wj(out / "results.json", {"GAP": V.GAP, "PROB_T": V.PROB_T, "units": res, "call_log": log})
    A.wj(out / "cost.json", tracker.summary())
    print(json.dumps({"calls": sum(1 for x in log if x.get("ok") and not x.get("cached")), "cost_cny": tracker.summary().get("total_cny")}, ensure_ascii=False))
    for r in res:
        print(r["page"], r["side"], json.dumps(r["reads"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
