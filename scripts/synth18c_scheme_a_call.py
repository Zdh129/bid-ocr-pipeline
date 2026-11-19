#!/usr/bin/env python3
"""synth18c 版（由 synth18b 版复制；多一个 --input，只允许 output/synth18c/P2/synth18c-P2.pdf 且 sha 与 prep 记录一致）。原：synth18b 版（由 synth18_scheme_a_call.py 复制：只改输入名、配置与校验方式，不读参照/manifest）。
synth18 版方案 A 调用（由 blind20_scheme_a_call.py 逐行复制，只改输入文件名、默认配置与输出目录；分流、覆盖、补识逻辑不变）。
只发 synth18-scan.pdf 的 18 页渲染图（以及缺行补识别的裁剪拼图）。

- 分流：表格页 / 带章页 -> accurate（高精度含位置），其余 -> general（标准含位置）。每页记录原因。
- 覆盖：本地检测的每个文字行框都要有输出；缺失则把缺失区域裁剪拼图送 accurate 重试；仍缺则标出。
- 原始返回只存本机 output/scheme_a/raw/。绝不打印 Key / token。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2
import numpy as np

from baidu_ocr.blind20_local import load_bgr, route_blind20, text_segments
from baidu_ocr.client import BaiduOcrClient
from baidu_ocr.config import load_config
from baidu_ocr.cost import CostTracker
from baidu_ocr.frozen import sha256_file
from baidu_ocr.render import render_pages

PDF_NAME = "synth18c-scan.pdf"

PARAMS = {
    "language_type": "CHN_ENG",
    "detect_direction": "true",
    "probability": "true",
    "vertexes_location": "true",
    "recognize_granularity": "small",
    "paragraph": "false",
}


def wj(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def word_polys(words):
    polys = []
    for w in words:
        vl = w.get("vertexes_location")
        if vl and len(vl) == 4:
            polys.append(np.array([[p["x"], p["y"]] for p in vl], dtype=np.int32))
        else:
            loc = w.get("location") or {}
            l, t, ww, hh = (int(loc.get(k) or 0) for k in ("left", "top", "width", "height"))
            polys.append(np.array([[l, t], [l + ww, t], [l + ww, t + hh], [l, t + hh]], dtype=np.int32))
    return polys


def coverage(segs, txt_mask, words, shape, min_frac=0.5):
    mask = np.zeros(shape[:2], np.uint8)
    for poly in word_polys(words):
        cv2.fillPoly(mask, [poly], 1)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    res = []
    for s in segs:
        x0, y0, x1, y1 = s["bbox"]
        ink = txt_mask[y0:y1, x0:x1] > 0
        tot = int(ink.sum())
        cov = int((ink & (mask[y0:y1, x0:x1] > 0)).sum())
        frac = cov / tot if tot else 1.0
        res.append({**s, "covered_frac": round(frac, 3), "covered": frac >= min_frac})
    return res


def build_composite(img, boxes, pad=14, gap=40, scale=2.0):
    crops, metas = [], []
    for b in boxes:
        x0, y0, x1, y1 = b
        H, W = img.shape[:2]
        X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
        c = img[Y0:Y1, X0:X1]
        c = cv2.resize(c, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        crops.append(c)
        metas.append({"src": [X0, Y0, X1, Y1]})
    width = max(c.shape[1] for c in crops) + 2 * gap
    height = sum(c.shape[0] for c in crops) + gap * (len(crops) + 1)
    canvas = np.full((height, width, 3), 255, np.uint8)
    y = gap
    for c, m in zip(crops, metas):
        canvas[y:y + c.shape[0], gap:gap + c.shape[1]] = c
        m["dst"] = [gap, y, gap + c.shape[1], y + c.shape[0]]
        y += c.shape[0] + gap
    return canvas, metas, scale


def map_back(words, metas, scale):
    out = []
    for w in words:
        loc = w.get("location") or {}
        cx = loc.get("left", 0) + loc.get("width", 0) / 2
        cy = loc.get("top", 0) + loc.get("height", 0) / 2
        m = next((m for m in metas if m["dst"][0] - 5 <= cx <= m["dst"][2] + 5 and m["dst"][1] - 5 <= cy <= m["dst"][3] + 5), None)
        if m is None:
            continue
        dx, dy = m["dst"][0], m["dst"][1]
        sx, sy = m["src"][0], m["src"][1]

        def tr(l, t):
            return sx + (l - dx) / scale, sy + (t - dy) / scale

        nw = dict(w)
        l, t = tr(loc["left"], loc["top"])
        nw["location"] = {"left": int(round(l)), "top": int(round(t)), "width": int(round(loc["width"] / scale)), "height": int(round(loc["height"] / scale))}
        if w.get("vertexes_location"):
            nw["vertexes_location"] = [dict(zip(("x", "y"), (int(round(v)) for v in tr(p["x"], p["y"])))) for p in w["vertexes_location"]]
        chars = []
        for ch in w.get("chars") or []:
            cl = ch.get("location") or {}
            l2, t2 = tr(cl.get("left", 0), cl.get("top", 0))
            chars.append({**ch, "location": {"left": int(round(l2)), "top": int(round(t2)), "width": int(round(cl.get("width", 0) / scale)), "height": int(round(cl.get("height", 0) / scale))}})
        nw["chars"] = chars
        nw["_from_retry"] = True
        out.append(nw)
    return out


def call(client, image_path, api, page, kind, raw_dir, log):
    cached = raw_dir / f"p{page:02d}_{kind}_{api}.json"
    if cached.exists():  # 断点续跑：已成功落盘的调用不重发（synth18 版新增）
        log.append({"page": page, "kind": kind, "api": api, "attempt": 0, "ok": True, "cached": True, "elapsed_sec": 0.0,
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return json.loads(cached.read_text(encoding="utf-8")), 0.0
    for attempt in range(4):
        t0 = time.perf_counter()
        try:
            r = client.recognize_image(image_path, api, page=page, filename=f"{PDF_NAME}#{kind}", extra_params=PARAMS)
        except Exception as exc:  # 网络层异常（SSL EOF 等）按可重试处理；不记录 URL（含 token）（synth18 版新增）
            r = {"ok": False, "error_code": "network", "error_msg": type(exc).__name__}
        el = time.perf_counter() - t0
        log.append({"page": page, "kind": kind, "api": api, "attempt": attempt, "ok": r.get("ok"),
                    "error_code": r.get("error_code"), "error_msg": r.get("error_msg"), "elapsed_sec": round(el, 3),
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        if r.get("ok"):
            wj(raw_dir / f"p{page:02d}_{kind}_{api}.json", r["response"])
            return r["response"], el
        if r.get("error_code") in (18, 17, 282000, 282003, "network"):  # QPS / 内部错误 -> 退避重试
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    return None, 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config_synth18c_live.yaml"))
    ap.add_argument("--allow-external", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "output" / "synth18c" / "orig" / "work" / "output" / "scheme_a"))
    ap.add_argument("--force-api", default="", help="诊断：所有页强制用某接口（如 general）")
    ap.add_argument("--no-retry", action="store_true")
    ap.add_argument("--input", default="")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if not (cfg.get("allow_external") and args.allow_external):
        print("拒绝：需要 config_synth18c_live.yaml allow_external=true 且命令行 --allow-external")
        return 2
    # synth18b 版：不走 check_frozen20（它会读参照与 manifest）；只认配置里的扫描件路径 + sha256
    pdf = Path(cfg["synth18c"]["scan_pdf"])
    assert pdf.name == PDF_NAME and sha256_file(pdf) == cfg["synth18c"]["scan_sha256"], "synth18c 扫描件 sha 不符"
    if args.input:
        p2 = (ROOT / "output" / "synth18c" / "P2" / "synth18c-P2.pdf").resolve()
        rec = json.loads((p2.parent / "prep.json").read_text(encoding="utf-8"))
        assert Path(args.input).resolve() == p2 and sha256_file(p2) == rec["p2_pdf_sha256"], "只允许 prep 生成的 synth18c-P2.pdf"
        pdf = p2
    out = Path(args.out)
    raw_dir = out / "raw"
    crops_dir = out / "crops"
    tracker = CostTracker.from_config(cfg)
    client = BaiduOcrClient(cfg, tracker)
    t_all = time.perf_counter()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    renders = render_pages(pdf, out / "renders", 200, 200 / 72.0)
    log: list = []
    pages = []
    for rr in renders:
        p = rr["page"]
        t_page = time.perf_counter()
        img = load_bgr(rr["image_ref"])
        t0 = time.perf_counter()
        route = route_blind20(img)
        segs, txt = text_segments(img, seal_boxes=route["seal"]["boxes"])
        t_local = time.perf_counter() - t0
        api = args.force_api or route["api"]
        resp, el_main = call(client, rr["image_ref"], api, p, "main", raw_dir, log)
        words = list((resp or {}).get("words_result") or [])
        need = [s for s in segs if not s["in_seal"]]
        cov1 = coverage(need, txt, words, img.shape)
        missing = [c for c in cov1 if not c["covered"]]
        retry_info = None
        el_retry = 0.0
        if missing and not args.no_retry and resp is not None:
            canvas, metas, scale = build_composite(img, [m["bbox"] for m in missing])
            if canvas.shape[0] > 8000:  # accurate 最长边 8192
                f = 8000 / canvas.shape[0]
                canvas = cv2.resize(canvas, None, fx=f, fy=f)
                for m in metas:
                    m["dst"] = [v * f for v in m["dst"]]
                scale *= f
            cpath = crops_dir / f"p{p:02d}_retry.png"
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(cpath), canvas)
            rresp, el_retry = call(client, cpath, "accurate", p, "retry", raw_dir, log)
            rwords = map_back(list((rresp or {}).get("words_result") or []), metas, scale)
            # 只补那些落在缺失框里的新结果
            words_all = words + rwords
            cov2 = coverage(need, txt, words_all, img.shape)
            retry_info = {"n_missing_before": len(missing), "retry_api": "accurate", "n_retry_words": len(rwords),
                          "n_missing_after": sum(not c["covered"] for c in cov2), "composite": str(cpath)}
            wj(raw_dir / f"p{p:02d}_retry_mapped.json", rwords)
            final_cov = cov2
        else:
            final_cov = cov1
        seal_segs = [s for s in segs if s["in_seal"]]
        pages.append({
            "page": p,
            "image_ref": rr["image_ref"], "width_px": rr["width_px"], "height_px": rr["height_px"],
            "width_pdf": rr["width_pdf"], "height_pdf": rr["height_pdf"], "render_sec": round(rr["render_sec"], 3),
            "route": {"page_class": route["page_class"], "api": api, "reasons": route["reasons"],
                      "seal_red_px": route["seal"]["red_px"], "seal_boxes": route["seal"]["boxes"],
                      "tables": route["tables"], "forced": bool(args.force_api)},
            "main_ok": resp is not None,
            "n_words_main": len(words),
            "coverage": {"expected_line_boxes": len(need), "covered_first_pass": sum(c["covered"] for c in cov1),
                         "missing_first_pass": len(missing), "retry": retry_info,
                         "still_missing": [c for c in final_cov if not c["covered"]],
                         "seal_region_boxes_excluded": len(seal_segs)},
            "timing_sec": {"local": round(t_local, 3), "api_main": round(el_main, 3), "api_retry": round(el_retry, 3),
                           "page_total": round(time.perf_counter() - t_page + rr["render_sec"], 3)},
        })
        print(f"p{p:02d} {route['page_class']:<15} {api:<8} words={len(words):3d} lines={len(need):3d} miss1={len(missing):2d} miss2={len(pages[-1]['coverage']['still_missing']):2d} t={el_main:.2f}s cost¥{tracker.total_cny:.3f}", flush=True)
    summary = {"started_at_cst": started, "finished_at_cst": time.strftime("%Y-%m-%d %H:%M:%S"),
               "wall_sec": round(time.perf_counter() - t_all, 2), "pages": pages, "call_log": log,
               "input": {"file": pdf.name, "sha256": sha256_file(pdf)}, "params": PARAMS,
               "force_api": args.force_api or None}
    wj(out / "call_summary.json", summary)
    wj(out / "cost.json", tracker.summary())
    s = tracker.summary()
    print(json.dumps({"wall_sec": summary["wall_sec"], "total_cny": s["total_cny"], "total_usd": s["total_usd"], "n_calls": s["n_calls"],
                      "by_api": {k: {kk: vv for kk, vv in v.items()} for k, v in s["by_api"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
