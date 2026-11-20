#!/usr/bin/env python3
"""blind20 上复测冻结的「推荐组合」（冻结清单 output/f1_round2/frozen_推荐组合.json）。产出放 output/blind20_frozen/。

  verify            核对冻结清单（每一步都先跑）：组合代码、配置、提示词 sha，扫描件 sha，生产配置 sha，config.yaml 不外发
  prep              blind20-scan.pdf 300 DPI 渲染 → 颜色去章图（P2）→ 装 PDF（不联网）
  baidu [--live]    无限量识别（颜色去章图）＋高精度识别（颜色去章图，强制高精度）；文档解析原图结果复用以前已落盘的
  reread [--live]   二次识别（高精度识别颜色去章图的低置信度行 → 高精度识别）
  seal [--live]     变体一乙要用的「印章混入·去红重识」
  vert [--live]     新变体二要用的「页边竖排」三种转向识别
  build             无限量识别原始返回改名成流水线认的文件名（不联网）
  eval              冻结组合去掉深度求索纠错 → 三种口径计分，与以前的文档解析原始结果、v1、v2 对比，列新引入错误（不联网）
  llm_prep          只在本地准备要发给深度求索的表格文字：记页数、字数、估算费用，然后停下（不联网、不外发）
外发只有百度（含由这 20 页导出的处理图、裁图、旋转图）；深度求索、gpt-4o、6.2 都不用。日志不写任何密钥或访问令牌。
本脚本只调用冻结代码，不含识别规则。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

PDF = Path("/workspace/pdf-test/frozen20/blind20-scan.pdf")
PDF_SHA = "40fdaa2e4a49481365627d00f1105d25c3c4d47dfe2da147dbf45072fa34c9b5"
PROD_SHA = "a34c9d238a22214c1495ac115be2515db09f84bd13982380c446a63a4c91eaf3"
OUT = ROOT / "output" / "blind20_frozen"
FROZEN = ROOT / "output" / "f1_round2" / "frozen_推荐组合.json"
CFG = ROOT / "config_blind20_frozen_live.yaml"
WP = OUT / "P2" / "work"
B_DOC = ROOT / "output" / "final" / "scheme_b_standard" / "blind20-scan.json"
A_ORIG = ROOT / "output" / "scheme_a"
PAID_BEFORE = 1.44
CAP = 100.0
LEFT0 = {"docparser_pages": 36, "accurate_calls": 317, "general_calls": 975}   # 本轮之前的免费余量（本地账本推算）
RECOMMENDED = {"seal": "whole", "inv": "v3", "starf": True, "hdr": "fields", "vert": True, "check": True}
NAME = "冻结推荐组合（去掉深度求索纠错）"


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(s):
    s = re.sub(r"access_token=[^&\s\"']+", "access_token=***", s)
    return re.sub(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+", r"\1***", s)


def verify(a=None):
    fz = jl(FROZEN)
    bad = [c for c, s in fz["code_sha256"].items() if sha(ROOT / c) != s]
    if bad:
        sys.exit(f"冻结校验失败：{bad}")
    if fz["variant"] != RECOMMENDED:
        sys.exit("冻结校验失败：组合定义不同")
    assert sha(PDF) == PDF_SHA, "blind20 扫描件 sha 不符"
    assert sha(ROOT.parent / "ocr" / "config.yaml") == PROD_SHA, "生产配置被改过"
    assert yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["allow_external"] is False
    return fz


def ledger():
    calls = {"docparser_standard_pages": 0, "accurate": 0, "general": 0, "unlimited_pages": 0}
    unl_cny = 0.0
    for f in sorted(OUT.glob("**/cost.json")):
        for x in jl(f).get("calls") or []:
            if not x.get("success") or x.get("dry_run"):
                continue
            api = x["api"]
            if api == "docparser_standard":
                calls["docparser_standard_pages"] += int(x.get("units") or 0)
            elif api == "docparser_unlimited":
                calls["unlimited_pages"] += int(x.get("units") or 0)
                unl_cny += float(x.get("cny") or 0)
            elif api in ("accurate", "general"):
                calls[api] += 1
    for f in sorted(OUT.glob("**/cost_note.json")):
        n = jl(f)
        calls["unlimited_pages"] += int(n.get("billed_pages") or 0)
        unl_cny += float(n.get("cny_list_estimate") or 0)
    used = {"docparser_pages": calls["docparser_standard_pages"], "accurate_calls": calls["accurate"], "general_calls": calls["general"]}
    over = {k: max(0, used[k] - LEFT0[k]) for k in used}
    per = {"docparser_pages": 0.05, "accurate_calls": 0.04, "general_calls": 0.01}
    paid = round(sum(over[k] * per[k] for k in over) + unl_cny, 4)
    return {"calls": calls, "free_left": {k: LEFT0[k] - min(used[k], LEFT0[k]) for k in LEFT0}, "paid_cny_this_round": paid,
            "paid_cumulative_cny": round(PAID_BEFORE + paid, 4), "cap_cny": CAP}


def run_logged(cmd, log):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    Path(log).write_text(redact(r.stdout + "\n" + r.stderr), encoding="utf-8")
    return r.returncode, redact(r.stdout)


def check_cap(worst):
    led = ledger()
    if led["paid_cumulative_cny"] + worst > CAP:
        sys.exit(f"累计付费 ¥{led['paid_cumulative_cny']} + 本步最坏 ¥{worst} > ¥{CAP}，停止")
    return led


def prep(a):
    verify()
    import cv2
    import fitz
    import synth18_preproc as P
    src = fitz.open(PDF)
    (OUT / "P2" / "img").mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i, pg in enumerate(src):
        pix = pg.get_pixmap(dpi=300)
        op = OUT / "P2" / "img" / f"orig_p{i + 1:02d}.png"
        pix.save(op)
        img = P.p2(cv2.imread(str(op)))
        cv2.imwrite(str(OUT / "P2" / "img" / f"p{i + 1:02d}.png"), img)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        np_ = doc.new_page(width=pg.rect.width, height=pg.rect.height)
        np_.insert_image(np_.rect, stream=buf.tobytes())
    out = OUT / "P2" / "blind20-P2.pdf"
    doc.save(out, deflate=True)
    wj(OUT / "P2" / "prep.json", {"p2_pdf_sha256": sha(out), "pages": len(doc), "at": now()})
    print("颜色去章图 PDF", len(doc), "页", out.stat().st_size // 1024, "KB")


def baidu(a):
    verify()
    py = str(ROOT / ".venv" / "bin" / "python")
    p2 = OUT / "P2" / "blind20-P2.pdf"
    assert sha(p2) == jl(OUT / "P2" / "prep.json")["p2_pdf_sha256"]
    steps = [("无限量识别_颜色去章图", WP / "output" / "scheme_b_unlimited",
              [py, "scripts/run_scheme_b.py", "--config", str(CFG), "--input", str(p2), "--out", str(WP / "output" / "scheme_b_unlimited"), "--edition", "unlimited"], 0.02 * 20),
             ("高精度识别_颜色去章图", WP / "output" / "scheme_a",
              [py, "scripts/blind20_frozen_scheme_a_call.py", "--config", str(CFG), "--out", str(WP / "output" / "scheme_a"), "--input", str(p2), "--force-api", "accurate"], 0.0)]
    for name, out, cmd, worst in steps:
        done = (out / "call_summary.json").exists() if "高精度" in name else ((out / "raw").exists() and any((out / "raw").glob("*.parse_result.json")))
        if done:
            print(name, "已完成，跳过")
            continue
        led = check_cap(worst)
        if not a.live:
            print("[dry]", name, "最坏付费 ¥%.2f" % worst, "| 账本", json.dumps(led, ensure_ascii=False))
            continue
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        rc, _ = run_logged(cmd + ["--allow-external"], OUT / "日志" / f"百度_{name}.log")
        if rc != 0 and "无限量" in name and (out / "raw").exists() and any((out / "raw").glob("*.parse_result.json")):
            raw = next(p for p in out.glob("*.raw.json"))
            wj(out / "cost_note.json", {"note": "调用成功、原始返回已落盘；本地构建中间 JSON 失败，未写 cost.json。按 billed_pages 记账，不重发。",
                                        "api": "docparser_unlimited", "billed_pages": jl(raw).get("billed_pages"),
                                        "cny_list_estimate": round(0.02 * int(jl(raw).get("billed_pages") or 0), 4)})
            rc = 0
        print(name, "exit", rc, f"{time.time() - t0:.1f}s", json.dumps(ledger(), ensure_ascii=False))
        if rc != 0:
            sys.exit(f"{name} 失败，停止")


def build(a):
    verify()
    import shutil
    d = WP / "output" / "scheme_b_unlimited"
    stem = next(p.name.replace(".parse_result.json", "") for p in (d / "raw").glob("*.parse_result.json") if not p.name.startswith("blind20-scan"))
    shutil.copyfile(d / "raw" / f"{stem}.parse_result.json", d / "raw" / "blind20-scan.parse_result.json")
    print("built", stem)


def _sub(a, name, cmd, worst, done_file):
    verify()
    if Path(done_file).exists():
        print(name, "已完成，跳过")
        return
    led = check_cap(worst)
    if a.live:
        cmd = cmd + ["--live", "--allow-external"]
    rc, so = run_logged(cmd, OUT / "日志" / f"{name}.log")
    print(name, "exit", rc, so.strip().splitlines()[0] if so.strip() else "", "| 账本", json.dumps(ledger() if a.live else led, ensure_ascii=False))
    if rc != 0:
        sys.exit(f"{name} 失败，停止")


def reread_calls(a):
    py = str(ROOT / ".venv" / "bin" / "python")
    out = OUT / "reread" / "A_P2"
    _sub(a, "二次识别", [py, "scripts/opt2_reread.py", "--a-dir", str(WP / "output" / "scheme_a"), "--b-doc", str(B_DOC), "--out", str(out),
                      "--config", str(CFG), "--label", "blind20-frozen-reread"], 0.0, out / "results.json")


def seal(a):
    py = str(ROOT / ".venv" / "bin" / "python")
    out = OUT / "blind20" / "seal_leak"
    _sub(a, "印章混入去红重识", [py, "scripts/f1r2_seal_call.py", "--b-doc", str(B_DOC), "--render-dir", str(A_ORIG / "renders"), "--out", str(out),
                         "--config", str(CFG), "--label", "blind20-frozen-sealleak"], 0.0, out / "results.json")


def vert(a):
    py = str(ROOT / ".venv" / "bin" / "python")
    out = OUT / "blind20" / "vertical"
    _sub(a, "页边竖排识别", [py, "scripts/f1r2_vert_call.py", "--b-doc", str(B_DOC), "--render-dir", str(A_ORIG / "renders"), "--out", str(out),
                       "--config", str(CFG), "--label", "blind20-frozen-vertical"], 0.0, out / "results.json")


# ------------------------------------------------------------------ 计分
def ctx():
    import f1r2 as F
    F.OUT = OUT
    F.DS["blind20"] = {"dir": OUT / "计分参照", "b": B_DOC, "root": ROOT, "a": WP / "output" / "scheme_a",
                       "u": WP / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
                       "reread": OUT / "reread" / "A_P2" / "results.json", "render": A_ORIG / "renders",
                       "llm_caches": [], "llm_cfg": None, "llm_spent_before": 0.0, "blind": True, "a_orig_summary": A_ORIG / "call_summary.json"}
    for k in ("seal_leak", "vertical"):
        assert (OUT / "blind20" / k / "results.json").exists(), f"缺 {k} 结果"
    return F, F.Ctx("blind20")


def scorer():
    import bestconfig_run as bc
    import synth18_score as SS
    import tolerant_score as T
    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    d = OUT / "计分参照"
    bs.REF, bs.MANIFEST = d / "blind-ref.txt", d / "manifest.json"
    bs.MANUAL.clear()
    full = bs.REF.read_text(encoding="utf-8")
    rj = jl(d / "blind-ref.json")
    refs = {"full": bs.parse_ref(full), "nosign": bs.parse_ref(full)}
    mp = {int(p["output_page"]): p for p in jl(d / "manifest.json")["pages"]}
    rng = SS.special_ranges(bs, refs["full"], rj)
    hs = bc.load_mod("hybrid_score", ROOT / "scripts" / "hybrid_score.py")
    hs.load_bs = lambda: bs
    return bc, bs, hs, T, refs, rj, mp, rng


def evaluate(a):
    fz = verify()
    import synth18b_score as SB
    from baidu_ocr.postprocess import pred_pages
    F, c = ctx()
    preds = {"文档解析原始结果": pred_pages(c.doc), "以前的 v1（当前最优解，四步）": jl(ROOT / "output" / "bestconfig" / "pred_pages.json"),
             "以前的 v2（v1＋6.2 回填）": jl(ROOT / "output" / "bestconfig_v2" / "pred_pages.json")}
    counts, logs = {}, {}
    preds["方案F1（不加纠错，参考）"], counts["方案F1（不加纠错，参考）"], _ = c.run({}, llm=None)
    preds[NAME], counts[NAME], lg = c.run(RECOMMENDED, llm=None)
    logs[NAME] = [x for x in lg if x.get("step") in ("seal_leak", "invoice_reorder_v3", "star_filter", "invoice_header", "vertical_margin", "invoice_check", "reread")]
    bc, bs, hs, T, refs, rj, mp, rng = scorer()
    res = {}
    for n, pr in preds.items():
        st = bs.score_system(pr, refs["full"], mp, None)
        cats, rows = SB.categorize_all(bc, bs, hs, refs, rng, pr, st)
        res[n] = {"strict": bc.summ(st)["overall"], "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in"))["overall"],
                  "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out"))["overall"], "cats": cats, "rows": rows,
                  "per_section_strict": {k: v for k, v in bc.summ(st).items() if k != "overall"}}
    for n in res:
        for basek, tag in (("文档解析原始结果", "new_vs_b"), ("以前的 v1（当前最优解，四步）", "new_vs_v1"), ("以前的 v2（v1＋6.2 回填）", "new_vs_v2")):
            if n != basek:
                res[n][tag] = SB.new_errors(bs, res[basek]["rows"], res[n]["rows"])
    wj(OUT / "pred_pages.json", {k: v for k, v in preds.items() if not k.startswith("以前")})
    wj(OUT / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
    wj(OUT / "scores.json", {"scored_at": now(), "frozen_sha256": sha(FROZEN), "ref_struct_sha256": {f: sha(OUT / "计分参照" / f) for f in ("blind-ref.txt", "blind-ref.json")},
                             "results": {n: {k: v for k, v in r.items() if k != "rows"} for n, r in res.items()}, "counts": counts, "logs": logs,
                             "baidu": ledger()})
    for n, r in res.items():
        f = lambda x: f"{x['readout']:.2f}%/{x['cer']:.2f}%/{x['D']}/{x['S']}/{x['I']}"  # noqa: E731
        print(n, "| 主", f(r["tol_in"]), "| 剔签", f(r["tol_out"]), "| 严格", f(r["strict"]),
              "| 新错 vs B/v1/v2", *[sum(e["cost"] for e in r.get(t, [])) if t in r else "—" for t in ("new_vs_b", "new_vs_v1", "new_vs_v2")])


def ds_tokens(t):
    import unicodedata
    cjk = sum(1 for ch in t if unicodedata.east_asian_width(ch) in ("W", "F"))
    return math.ceil(cjk * 0.6 + (len(t) - cjk) * 0.3)


def llm_prep(a):
    """只在本地准备；不联网、不外发。"""
    fz = verify()
    F, c = ctx()
    pr, _, _ = c.base(RECOMMENDED)
    chunks = F.tables_of(pr)
    prompt = (ROOT / "output" / "synth18_llm" / "prompt_v1.txt").read_text(encoding="utf-8")
    assert sha(ROOT / "output" / "synth18_llm" / "prompt_v1.txt") == fz["prompt_sha256"]
    mc = yaml.safe_load((ROOT / "config_llm_synth18c_live.yaml").read_text(encoding="utf-8"))["llm"]["models"]["deepseek"]
    pk, op = mc["price_usd_per_1m_peak"], mc["price_usd_per_1m_offpeak"]
    rows, tot = [], {"pages": len(chunks), "segments": 0, "chars": 0, "in_tok": 0, "out_tok_est": 0, "out_tok_max": 0}
    for ch in chunks:
        um = json.dumps({"page": ch["page"], "segments": ch["segments"]}, ensure_ascii=False)
        exp = json.dumps({"segments": [{"id": s["id"], "text": s["text"]} for s in ch["segments"]]}, ensure_ascii=False)
        it = ds_tokens(prompt) + ds_tokens(um) + 10
        ot = math.ceil(ds_tokens(exp) * 1.1)
        mo = int(min(16384, max(1024, ot * 2 + 256)))
        n = sum(len(s["text"]) for s in ch["segments"])
        rows.append({"page": ch["page"], "segments": len(ch["segments"]), "chars": n, "in_tok": it, "out_tok_est": ot, "max_tokens": mo})
        tot["segments"] += len(ch["segments"])
        tot["chars"] += n
        tot["in_tok"] += it
        tot["out_tok_est"] += ot
        tot["out_tok_max"] += mo
    est = lambda p, o: round((tot["in_tok"] * p["input"] + o * p["output"]) / 1e6, 5)  # noqa: E731
    summ = {"note": "只在本地准备，未外发。深度求索从未获批接收 blind20 的任何文字（含真实个人信息）；需用户明确批准后才可发送。",
            "at": now(), "model": mc["model"], "prompt": "prompt_v1（sha 见冻结清单，未改）", "totals": tot,
            "est_usd": {"预计（错峰价）": est(op, tot["out_tok_est"]), "预计（高峰价）": est(pk, tot["out_tok_est"]),
                        "最坏（高峰价、输出顶满 max_tokens）": est(pk, tot["out_tok_max"])}, "per_page": rows}
    wj(OUT / "深度求索待批" / "规模与预估费用.json", summ)
    wj(OUT / "深度求索待批" / "待发表格文字_仅本地_勿外发.json", {"chunks": chunks, "warning": "含 blind20 原文（可能含个人信息），仅本地保存，未获批前不得外发"})
    print(json.dumps({k: summ[k] for k in ("totals", "est_usd")}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "prep", "baidu", "reread", "seal", "vert", "build", "eval", "llm_prep"])
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    {"verify": lambda a: print("冻结清单校验通过", sha(FROZEN)) if verify() else None, "prep": prep, "baidu": baidu, "reread": reread_calls, "seal": seal,
     "vert": vert, "build": build, "eval": evaluate, "llm_prep": llm_prep}[a.cmd](a)
