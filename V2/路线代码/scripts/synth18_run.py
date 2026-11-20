#!/usr/bin/env python3
"""synth18 端到端：用冻结的新最优解（output/blindset2_prep/frozen_config_v2.json）在 synth18 上实跑。

子命令：
  build    百度原始返回 → 中间 JSON（复用 blind20_build 的构建函数，只把输出根指向工作目录）（不联网）
  prepare  6.2 选区（漏读行、手写日期；不看参照）+ 裁图 + 估算（不联网）
  run      --live 才外发裁图给 gpt-6.2-astra（astra_run 同一套 Batch→标准接口逻辑）
  score    后处理各变体 → schema 校验 → 计分（blind20_score 解析/计分逻辑，参照/manifest 指向 synth18）→ 报告

工作目录 output/synth18/work 模拟项目根（output/...），里面的中间文件沿用 blind20-scan.* 文件名是因为
hybrid.inputs / postprocess.aux / blind20_build 里写死了这个名字；内容全部来自 synth18-scan.pdf。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

S18 = Path("/workspace/pdf-test/synth18")
PDF = S18 / "synth18-scan.pdf"
PDF_SHA = "13880016af849e7c1eaa5dd4470524297a8cdf6186486c3647ca3a36e15bc9d2"
OUT = ROOT / "output" / "synth18"
WR = OUT / "work"                 # 模拟的项目根
W = WR / "output"
VLM = OUT / "vlm"
FROZEN = ROOT / "output" / "blindset2_prep" / "frozen_config_v2.json"
LIVE_CFG = ROOT / "config_astra_synth18_live.yaml"
ASTRA_SPENT_BEFORE = 0.149396 + 0.002704   # astra 单测 + bestconfig_v2（= $0.1521）


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def verify_frozen():
    fz = load_mod("freeze_config_v2", ROOT / "scripts" / "freeze_config_v2.py")
    bad = fz.verify()
    if bad:
        sys.exit(f"冻结清单不一致，拒绝运行：{bad}")
    return jl(FROZEN)


# ------------------------------------------------------------------ build
def build(args):
    verify_frozen()
    assert sha(PDF) == PDF_SHA
    for ed in ("scheme_b_standard", "scheme_b_unlimited"):
        d = W / ed
        if not (d / "raw" / "synth18-scan.parse_result.json").exists():
            print(f"缺 {ed} 原始返回，跳过")
            continue
        shutil.copyfile(d / "raw" / "synth18-scan.parse_result.json", d / "raw" / "blind20-scan.parse_result.json")
        shutil.copyfile(d / "synth18-scan.raw.json", d / "blind20-scan.raw.json")
    bm = load_mod("blind20_build", ROOT / "scripts" / "blind20_build.py")
    bm.OUT = W
    bm.SRC_NAME = PDF.name
    a_sum = jl(W / "scheme_a" / "call_summary.json")
    bm.build_scheme_a(W / "scheme_a", W / "final" / "scheme_a", "routed")
    bm.build_scheme_b(W / "scheme_b_standard", W / "final" / "scheme_b_standard", "standard", a_sum)
    if (W / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json").exists():
        bm.build_scheme_b(W / "scheme_b_unlimited", W / "final" / "scheme_b_unlimited", "unlimited", a_sum)
    for d in ("scheme_a", "scheme_b_standard", "scheme_b_unlimited"):
        p = W / "final" / d / "blind20-scan.schema_errors.json"
        if p.exists():
            print(d, "schema errors:", len(jl(p)))


# ------------------------------------------------------------------ prepare (VLM)
def prepare(args):
    import cv2
    import yaml

    from baidu_ocr.hybrid import astra
    from baidu_ocr.hybrid import crops as C
    from baidu_ocr.hybrid.crops import prompt_for, prompt_hash, text_tokens
    from baidu_ocr.hybrid.inputs import PX_PER_PT, load_pages
    from baidu_ocr.hybrid.rules import _region, assign_a_lines, common_hard_regions, order_by_lines, select_route2, union
    from baidu_ocr.postprocess.vlm_backfill import RULE_TEXT, handwritten_date_blocks

    fz = verify_frozen()
    cfg = yaml.safe_load(LIVE_CFG.read_text(encoding="utf-8"))["vlm"]
    assert cfg["model"] == fz["vlm"]["model"] and cfg["detail"] == fz["vlm"]["detail"] and cfg["reasoning_effort"] == fz["vlm"]["reasoning_effort"]
    renders = C.render_pdf(PDF, W / "hybrid" / "renders", dpi=200)
    local_seals = {p: C.local_seal_boxes(path) for p, path in renders.items()}
    pages = load_pages(WR, local_seals)
    date_ids = {b["id"] for b in handwritten_date_blocks([b for pg in pages for b in pg["b_blocks"]])}
    items = []
    for pg in pages:
        p = pg["page"]
        img = cv2.imread(str(renders[p]))
        assert (img.shape[1], img.shape[0]) == (pg["w_px"], pg["h_px"]), p
        regs = [r for r in select_route2(pg, PX_PER_PT)["regions"] if r["kind"] == "missing_line"]
        for k, r in enumerate(regs, 1):
            r["id"], r["scenario"] = f"s18-p{p:02d}-miss-{k:02d}", "missing_line"
        hard = common_hard_regions(pg)
        assign = assign_a_lines(pg, hard["seal_regions"])
        for b in pg["b_blocks"]:
            if b["id"] not in date_ids:
                continue
            als = order_by_lines([al for al in pg["a_lines"] if assign[al["id"]] == ("block", b["id"])])
            bbox = union([b["bbox"]] + [a["bbox"] for a in als]) if als else b["bbox"]
            r = _region("line", p, bbox, ["handwritten_date_rule"], {"b_block_id": b["id"]},
                        {"b_text": b.get("text") or "", "a_text": "".join(a["text"] for a in als), "b_score": b.get("score"),
                         "expected_chars": len(b.get("text") or "")})
            r["id"], r["scenario"] = f"s18-p{p:02d}-date-{b['id']}", "handwritten_date"
            regs.append(r)
        for r in regs:
            r["bbox_px"] = [round(v, 1) for v in C.bbox_pt_to_px(r["bbox_pt"], PX_PER_PT)]
            dest = VLM / "crops" / f"{r['id']}.png"
            info = C.crop_region(img, r["bbox_px"], r["kind"], dest)
            info["crop_path"] = str(dest.relative_to(ROOT))
            assert max(info["crop_w"], info["crop_h"]) <= 1600
            _, prompt = prompt_for(r)
            psha = prompt_hash(prompt)
            assert psha == fz["vlm"]["line_prompt_sha12"], "提示词与冻结不一致"
            it_tok = astra.image_tokens(info["crop_w"], info["crop_h"], cfg["detail"])
            p_tok = it_tok + text_tokens(prompt) + 12
            c_tok = int(r["context"].get("expected_chars", 10) * 1.15 + 10) + 400
            items.append({**r, **info, "model": cfg["model"], "detail": cfg["detail"], "prompt_kind": "line", "prompt_sha12": psha,
                          "category": r["scenario"], "test": "synth18", "reuse": None, "est_image_tokens": it_tok, "est_prompt_tokens": p_tok,
                          "est_completion_tokens": c_tok, "est_usd_batch": astra.usd(p_tok, c_tok, 0, "batch"),
                          "est_usd_standard": astra.usd(p_tok, c_tok, 0, "standard")})
    P = sum(i["est_prompt_tokens"] for i in items)
    Cc = sum(i["est_completion_tokens"] for i in items)
    cap_left = round(5.0 - ASTRA_SPENT_BEFORE, 6)
    est = {"n_items": len(items), "by_scenario": dict(Counter(i["scenario"] for i in items)), "est_prompt_tokens": P, "est_completion_tokens": Cc,
           "est_usd_batch": astra.usd(P, Cc, 0, "batch"), "est_usd_standard": astra.usd(P, Cc, 0, "standard"),
           "worst_case_usd_batch": astra.worst_case_usd(len(items), P, cfg["max_completion_tokens"], "batch") if items else 0,
           "worst_case_usd_standard": astra.worst_case_usd(len(items), P, cfg["max_completion_tokens"], "standard") if items else 0,
           "astra_spent_before": ASTRA_SPENT_BEFORE, "cap_left_usd": cap_left, "date_rule": RULE_TEXT}
    est["go"] = est["est_usd_standard"] <= cap_left
    wj(VLM / "manifest.json", {"items": items})
    wj(VLM / "estimate.json", est)
    for i in items:
        print(i["scenario"], i["id"], i["bbox_pt"], (i["context"] or {}).get("b_text") or (i["context"] or {}).get("a_text"))
    print(json.dumps({k: v for k, v in est.items() if k != "date_rule"}, ensure_ascii=False))


def run(args):
    verify_frozen()
    ar = load_mod("astra_run_s18", ROOT / "scripts" / "astra_run.py")
    ar.OUT = VLM
    ar.run(args)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "prepare", "run", "score"])
    ap.add_argument("--config", default=str(LIVE_CFG))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--standard-only", action="store_true")
    ap.add_argument("--no-fallback", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a)
    elif a.cmd == "prepare":
        prepare(a)
    elif a.cmd == "run":
        run(a)
    else:
        import synth18_score
        synth18_score.main()
