#!/usr/bin/env python3
"""blind20「新最优解」（bestconfig_v2）：B 标准版 + ★/☆ + 剔除印章 + A/U 补漏 + 发票重排 + gpt-6.2-astra 回填（漏读行、手写日期）。

子命令：
  prepare  选区（不看参照）→ 复用 output/astra 同裁图结果，未覆盖的才裁新图 → output/bestconfig_v2/manifest.json, estimate.json（不联网）
  run      --live 才外发，只发 manifest 里没有复用结果的新裁图；优先 Batch，超 10 分钟改标准接口；累计（含 astra 已花）≤ $5
  build    组装 vlm 结果 → 后处理流水线（vlm_backfill 开）→ schema 校验 → 计分、消融、报告（不联网）
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "output" / "bestconfig_v2"
ASTRA = ROOT / "output" / "astra"
RENDERS = ROOT / "output" / "hybrid" / "renders"
LIVE_CFG = ROOT / "config_astra_v2_live.yaml"


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def astra_spent() -> float:
    return sum(float(json.loads(l).get("usd") or 0) for l in (ASTRA / "vlm" / "ledger.jsonl").read_text().splitlines() if l.strip())


# ------------------------------------------------------------------ prepare
def prepare(args):
    import cv2

    from baidu_ocr.hybrid import astra
    from baidu_ocr.hybrid import crops as C
    from baidu_ocr.hybrid.crops import prompt_for, prompt_hash, text_tokens
    from baidu_ocr.hybrid.inputs import PX_PER_PT, load_pages
    from baidu_ocr.hybrid.rules import assign_a_lines, common_hard_regions, order_by_lines, select_route2, union, _region
    from baidu_ocr.postprocess.vlm_backfill import RULE_TEXT, handwritten_date_blocks
    import yaml

    cfg = yaml.safe_load(LIVE_CFG.read_text(encoding="utf-8"))["vlm"]
    renders = {int(p.stem.split("-")[1]): p for p in sorted(RENDERS.glob("page-*.png"))}
    local_seals = {p: C.local_seal_boxes(path) for p, path in renders.items()}
    pages = load_pages(ROOT, local_seals)
    amani = {i["id"]: i for i in jl(ASTRA / "manifest.json")["items"]}
    r2 = {i["id"]: i for i in jl(ROOT / "output/hybrid/manifest_route2.json")["items"]}
    tmp = OUT / "crops_check"
    items = []
    all_blocks = [b for pg in pages for b in pg["b_blocks"]]
    date_ids = {b["id"] for b in handwritten_date_blocks(all_blocks)}
    for pg in pages:
        p = pg["page"]
        img = cv2.imread(str(renders[p]))
        sel = select_route2(pg, PX_PER_PT)
        regs = [r for r in sel["regions"] if r["kind"] == "missing_line"]
        for k, r in enumerate(regs, 1):
            r["id"] = f"r2-p{p:02d}-missing_line-{k:02d}"
            r["scenario"] = "missing_line"
        # 手写日期：与路线 2 行区域同口径定框（B 块框 ∪ 分到该块的 A 行框）
        hard = common_hard_regions(pg)
        assign = assign_a_lines(pg, hard["seal_regions"])
        for b in pg["b_blocks"]:
            if b["id"] not in date_ids:
                continue
            als = order_by_lines([al for al in pg["a_lines"] if assign[al["id"]] == ("block", b["id"])])
            a_text = "".join(a["text"] for a in als)
            bbox = union([b["bbox"]] + [a["bbox"] for a in als]) if als else b["bbox"]
            r = _region("line", p, bbox, ["handwritten_date_rule"], {"b_block_id": b["id"]},
                        {"b_text": b.get("text") or "", "a_text": a_text, "b_score": b.get("score"), "expected_chars": len(b.get("text") or "")})
            r["scenario"] = "handwritten_date"
            regs.append(r)
        for r in regs:
            r["bbox_px"] = [round(v, 1) for v in C.bbox_pt_to_px(r["bbox_pt"], PX_PER_PT)]
            info = C.crop_region(img, r["bbox_px"], r["kind"], tmp / "x.png")
            # 找 astra 单测里同一裁图（同模型、同提示词、同 sha）的结果
            _, prompt = prompt_for(r)
            psha = prompt_hash(prompt)
            hit = next((a for a in amani.values() if a["page"] == p and a["kind"] == r["kind"] and a["sha256"] == info["sha256"]
                        and a["prompt_sha12"] == psha and a["model"] == cfg["model"]), None)
            if hit and (ASTRA / "vlm" / "results" / f"{hit['id']}.json").exists():
                res = jl(ASTRA / "vlm" / "results" / f"{hit['id']}.json")
                if res.get("ok") and res.get("crop_sha256") == info["sha256"] and res.get("prompt_sha12") == psha and res.get("model") == cfg["model"]:
                    items.append({**r, "id": hit["id"], "crop_path": hit["crop_path"], "crop_box_px": hit["crop_box_px"], "crop_w": hit["crop_w"],
                                  "crop_h": hit["crop_h"], "scale": hit["scale"], "sha256": hit["sha256"], "bytes": hit["bytes"],
                                  "model": cfg["model"], "detail": cfg["detail"], "prompt_sha12": psha, "reuse": f"output/astra/vlm/results/{hit['id']}.json",
                                  "category": r["scenario"], "test": "v2"})
                    continue
            nid = f"v2-p{p:02d}-{'date' if r['scenario'] == 'handwritten_date' else 'miss'}-{r['target'].get('b_block_id') or r['id'][-2:]}"
            dest = OUT / "crops" / f"{nid}.png"
            info = C.crop_region(img, r["bbox_px"], r["kind"], dest)
            info["crop_path"] = str(dest.relative_to(ROOT))
            w, h = info["crop_w"], info["crop_h"]
            assert max(w, h) <= 1600
            imgt = astra.image_tokens(w, h, cfg["detail"])
            p_tok = imgt + text_tokens(prompt) + 12
            c_tok = int(r["context"].get("expected_chars", 10) * 1.15 + 10) + 400
            items.append({**r, **info, "id": nid, "model": cfg["model"], "detail": cfg["detail"], "prompt_kind": "line", "prompt_sha12": psha,
                          "category": r["scenario"], "test": "v2", "reuse": None, "est_image_tokens": imgt, "est_prompt_tokens": p_tok,
                          "est_completion_tokens": c_tok, "est_usd_batch": astra.usd(p_tok, c_tok, 0, "batch"),
                          "est_usd_standard": astra.usd(p_tok, c_tok, 0, "standard")})
    shutil.rmtree(tmp, ignore_errors=True)
    new = [i for i in items if not i["reuse"]]
    P = sum(i["est_prompt_tokens"] for i in new)
    Cc = sum(i["est_completion_tokens"] for i in new)
    spent = astra_spent()
    cap_left = round(5.0 - spent, 6)
    est = {"n_items": len(items), "n_reused": len(items) - len(new), "n_new": len(new),
           "by_scenario": {s: sum(1 for i in items if i["scenario"] == s) for s in ("missing_line", "handwritten_date")},
           "est_prompt_tokens": P, "est_completion_tokens": Cc,
           "est_usd_batch": astra.usd(P, Cc, 0, "batch"), "est_usd_standard": astra.usd(P, Cc, 0, "standard"),
           "worst_case_usd_batch": astra.worst_case_usd(len(new), P, cfg["max_completion_tokens"], "batch") if new else 0.0,
           "worst_case_usd_standard": astra.worst_case_usd(len(new), P, cfg["max_completion_tokens"], "standard") if new else 0.0,
           "astra_spent_before": round(spent, 6), "cap_total_usd": 5.0, "cap_left_usd": cap_left,
           "date_rule": RULE_TEXT, "missing_rule": "路线 2 规则（hybrid/rules.select_route2）：A 有行而 B 无块覆盖的 A 行；A/B 都未覆盖、且不在印章区内的空白缺口。不看参照。"}
    est["go"] = est["est_usd_standard"] <= cap_left
    wj(OUT / "manifest.json", {"items": items})
    wj(OUT / "estimate.json", est)
    for i in items:
        print(i["scenario"], i["id"], i["page"], i["bbox_pt"], "复用" if i["reuse"] else "新调", (i["context"] or {}).get("b_text") or (i["context"] or {}).get("a_text"))
    print(json.dumps({k: v for k, v in est.items() if "rule" not in k}, ensure_ascii=False))


# ------------------------------------------------------------------ run
def run(args):
    ar = load_mod("astra_run_v2", ROOT / "scripts" / "astra_run.py")
    ar.OUT = OUT
    man = jl(OUT / "manifest.json")["items"]
    new = [i for i in man if not i["reuse"]]
    if not new:
        print("没有需要新调的裁图")
        return
    # astra_run.run 读 OUT/manifest.json 的全部条目；这里只给它新条目
    sub = OUT / "_run"
    ar.OUT = sub
    wj(sub / "manifest.json", {"items": new})
    est = jl(OUT / "estimate.json")
    wj(sub / "estimate.json", {**est, "go": est["go"]})
    ar.run(args)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "run", "build"])
    ap.add_argument("--config", default=str(LIVE_CFG))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--standard-only", action="store_true")
    ap.add_argument("--no-fallback", action="store_true")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a)
    elif a.cmd == "run":
        run(a)
    else:
        import bestconfig_v2_build
        bestconfig_v2_build.main()
