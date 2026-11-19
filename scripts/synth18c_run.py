#!/usr/bin/env python3
"""synth18c 留出集一次性验证：冻结 F1 / F2（及 LLM 子变体）→ 百度 → 二次识别 → LLM 表格纠错 → 一次计分（严格 + 容忍签名入/剔除）。

  freeze          写 output/synth18c/frozen_synth18c.json（独占创建，已存在则拒绝；不覆盖任何旧冻结文件）
  verify          校验冻结文件（之后每一步都先跑）
  prep            渲染 synth18c-scan.pdf 300 DPI → P2 去章 → 装 PDF（不联网），记录 P2 PDF sha
  baidu [--live]  B 文档解析（原图）+ A 原图（原走法）+ A P2（强制 accurate）+ Unlimited（原图、P2）
  reread [--live] 按置信度二次识别（A 原图、A P2 各自的低置信度行 → accurate）
  build           原始返回 → 中间 JSON
  llm [--live]    F1/F2 后处理后的表格段 → gpt-4o / deepseek；最坏情况校验，synth18c 单独 ≤ $2
  score           一次性计分（先核对参照 sha，再写 SCORED 标记；之后再运行拒绝）
计分前本脚本只打开 synth18c-scan.pdf；不打开 blind-ref.*、manifest.json、textlayer、_build/。不用 6.2。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

S18C = Path("/workspace/pdf-test/synth18c")
PDF = S18C / "synth18c-scan.pdf"
PDF_SHA = "e55be294eaf914f97371b948a3f0f21a918d3c6b2de52a4567d44153c2e1be50"
REF_SHA_EXPECTED = "2bcbc3a774987684dfda4df7535ff77d050cf567ab38191de6a27277afdf794f"
OUT = ROOT / "output" / "synth18c"
FROZEN = OUT / "frozen_synth18c.json"
WO = OUT / "orig" / "work"
WP = OUT / "P2" / "work"
LLMD = OUT / "llm"
BAIDU_CFG = ROOT / "config_synth18c_live.yaml"
LLM_CFG = ROOT / "config_llm_synth18c_live.yaml"
PROMPT = ROOT / "output" / "synth18_llm" / "prompt_v1.txt"
PROMPT_SHA = "86ae3d1c77ba3276cc209077ec04166ae878031a09fc285057c5b345430d231a"
LLM_CAP = 2.0
BAIDU_PAID_CAP_CNY = 100.0
# 免费余量（本地日志推算，截至 opt2 结束）：文档解析 54 页、accurate 407、general 977
LEFT0 = {"docparser_pages": 54, "accurate_calls": 407, "general_calls": 977}
PAID_BEFORE_CNY = 0.36 + 0.36   # synth18b Unlimited ¥0.36 + opt2 Unlimited(P2) ¥0.36（本轮之前的累计百度付费）

_COMMON = {"B": "orig", "star": True, "starmode": "fill", "seal": "off", "fill": True, "inv": "v2", "img": True, "reread": True, "vlm": "off"}
GROUPS = {
    "F1": {**_COMMON, "A": "P2", "U": "P2", "llm": "none"},
    "F1+deepseek": {**_COMMON, "A": "P2", "U": "P2", "llm": "deepseek"},
    "F1+gpt-4o": {**_COMMON, "A": "P2", "U": "P2", "llm": "gpt-4o"},
    "F2": {**_COMMON, "A": "orig", "U": "orig", "llm": "none"},
    "F2+deepseek": {**_COMMON, "A": "orig", "U": "orig", "llm": "deepseek"},
}
REFERENCE_ROWS = {
    "B 原始（原图）": {"raw": True},
    "旧 G2 去掉 LLM（诊断：inv off、星号旧规则、无二次识别）": {**_COMMON, "A": "orig", "U": "orig", "inv": "off", "starmode": "r1", "reread": False, "llm": "none"},
}
CODE = ["scripts/synth18c_run.py", "scripts/synth18c_score.py", "scripts/synth18c_scheme_a_call.py", "scripts/opt2_reread.py", "scripts/opt2_scheme_a_call.py", "scripts/synth18_opt2.py",
        "scripts/run_scheme_b.py", "scripts/blind20_build.py", "scripts/synth18_combos.py", "scripts/synth18_preproc.py",
        "scripts/tolerant_score.py", "scripts/synth18b_score.py", "scripts/synth18b_run.py", "scripts/blind20_score.py", "scripts/synth18_score.py",
        "scripts/bestconfig_run.py", "scripts/hybrid_score.py", "scripts/synth18_run.py",
        "src/baidu_ocr/postprocess/__init__.py", "src/baidu_ocr/postprocess/pipeline.py", "src/baidu_ocr/postprocess/star.py",
        "src/baidu_ocr/postprocess/star_fill.py", "src/baidu_ocr/postprocess/reread.py", "src/baidu_ocr/postprocess/invoice_reorder_v2.py",
        "src/baidu_ocr/postprocess/seal_strip.py", "src/baidu_ocr/postprocess/fill_gaps.py", "src/baidu_ocr/postprocess/invoice_reorder.py",
        "src/baidu_ocr/postprocess/vlm_backfill.py", "src/baidu_ocr/postprocess/aux.py", "src/baidu_ocr/postprocess/textutil.py", "src/baidu_ocr/score.py",
        "config.yaml", "config_synth18c_live.yaml", "config_llm_synth18c_live.yaml", "output/synth18_llm/prompt_v1.txt",
        "output/blindset2_prep/frozen_config_v2.json"]


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
    return re.sub(r"access_token=[^&\s\"']+", "access_token=***REDACTED***", s)


# ------------------------------------------------------------------ freeze / verify
def freeze(a):
    OUT.mkdir(parents=True, exist_ok=True)
    assert sha(PDF) == PDF_SHA, "synth18c 扫描件 sha 不符"
    assert sha(PROMPT) == PROMPT_SHA
    doc = {"created_at": now(), "purpose": "synth18c 留出集一次性验证；冻结后不改组定义/代码/配置。新清单，不覆盖 frozen_config_v2.json / frozen_synth18b.json",
           "groups": GROUPS, "reference_rows": REFERENCE_ROWS,
           "group_notes": {
               "流水线": "image_fill → star(R1-R3) → seal_strip(off) → fill_gaps(A+U) → invoice_reorder_v2 → reread → [LLM 表格段] → star_fill",
               "B": "文档解析标准版，原图",
               "A": "F1：P2 去章图，强制 accurate（含缺行补识）；F2：原图，原走法（分流 accurate/general）",
               "U": "Unlimited，F1 用 P2 图、F2 用原图（只作补漏）",
               "P2": "scripts/synth18_preproc.py 的 p2()（HSV 红色掩膜内像素换成 R 通道灰度），300 DPI，JPEG q88 装回原页面尺寸 PDF",
               "invoice_reorder_v2": "src/baidu_ocr/postprocess/invoice_reorder_v2.py（表头并格时用明细行作列模板；按 A 字框 y 拆开合计行；空输入保护）",
               "star_fill": "src/baidu_ocr/postprocess/star_fill.py：A 星号字框中心 30% 窗口黑像素比例 ≥ 0.5 判 ★，否则 ☆；按后 3 字上下文落到输出",
               "reread": "src/baidu_ocr/postprocess/reread.py：A 行 probability.average < 0.90 或 min < 0.60 且不在表格内 → 裁图放大 2 倍拼图送 accurate；采用规则 R-a/R-b 见模块说明",
               "llm": "只换表格段；LLM 输入 = 本组后处理后（LLM 之前）的表格段；prompt_v1 不改；之后再套 R1 与 star_fill",
               "6.2": "不用（synth18c 未获批）"},
           "scoring": {"strict": "blind20_score.parse_ref/score_system，参照 = synth18c/blind-ref.txt，manifest = synth18c/manifest.json",
                       "tolerant": "scripts/tolerant_score.py（与 synth18b 时冻结的同一份，sha 见 code_sha256）",
                       "main": "约定容忍版 + 签名入参照", "runs": "只跑一次（output/synth18c/SCORED 标记）",
                       "expected_ref_sha256": REF_SHA_EXPECTED},
           "input": {"scan_pdf": str(PDF), "scan_sha256": PDF_SHA},
           "prompt_sha256": PROMPT_SHA, "llm_cap_usd": LLM_CAP, "baidu_paid_cap_cny_cumulative": BAIDU_PAID_CAP_CNY,
           "dev_results_synth18": "output/synth18_opt2/batch4.md",
           "code_sha256": {c: sha(ROOT / c) for c in CODE}}
    with open(FROZEN, "x", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1))
    os.chmod(FROZEN, 0o444)
    print("frozen", sha(FROZEN))


def verify(a=None):
    fz = jl(FROZEN)
    bad = {c: s for c, s in fz["code_sha256"].items() if sha(ROOT / c) != s}
    if bad or fz["groups"] != GROUPS:
        sys.exit(f"冻结校验失败：{list(bad) or '组定义不同'}")
    assert sha(PDF) == PDF_SHA
    return fz


# ------------------------------------------------------------------ prep
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
    out = OUT / "P2" / "synth18c-P2.pdf"
    doc.save(out, deflate=True)
    wj(OUT / "P2" / "prep.json", {"p2_pdf_sha256": sha(out), "pages": len(doc), "at": now()})
    print("P2 pdf", len(doc), "页", out.stat().st_size // 1024, "KB")


# ------------------------------------------------------------------ 百度账本（免费 / 付费分记）
def baidu_ledger():
    """按 cost.json / cost_note.json 统计本留出集的百度调用：免费额度内的按次（文档解析按页）记，超出部分记付费。"""
    calls = {"docparser_standard_pages": 0, "accurate": 0, "general": 0, "unlimited_pages": 0}
    cny_list = {"docparser_standard": 0.0, "accurate": 0.0, "general": 0.0, "docparser_unlimited": 0.0}
    for f in sorted(OUT.glob("**/cost.json")):
        for x in jl(f).get("calls") or []:
            if not x.get("success") or x.get("dry_run"):
                continue
            api = x["api"]
            if api == "docparser_standard":
                calls["docparser_standard_pages"] += int(x.get("units") or 0)
            elif api == "docparser_unlimited":
                calls["unlimited_pages"] += int(x.get("units") or 0)
            elif api in ("accurate", "general"):
                calls[api] += 1
            cny_list[api] = round(cny_list.get(api, 0) + float(x.get("cny") or 0), 4)
    for f in sorted(OUT.glob("**/cost_note.json")):
        n = jl(f)
        calls["unlimited_pages"] += int(n.get("billed_pages") or 0)
        cny_list["docparser_unlimited"] = round(cny_list["docparser_unlimited"] + float(n.get("cny_list_estimate") or 0), 4)
    free = {"docparser_pages": min(calls["docparser_standard_pages"], LEFT0["docparser_pages"]),
            "accurate_calls": min(calls["accurate"], LEFT0["accurate_calls"]), "general_calls": min(calls["general"], LEFT0["general_calls"])}
    over = {"docparser_pages": calls["docparser_standard_pages"] - free["docparser_pages"],
            "accurate_calls": calls["accurate"] - free["accurate_calls"], "general_calls": calls["general"] - free["general_calls"]}
    per = {"docparser_pages": 0.05, "accurate_calls": 0.04, "general_calls": 0.01}   # 列表价（与 cost.json 一致的按量单价）
    paid = round(sum(over[k] * per[k] for k in over) + cny_list["docparser_unlimited"], 4)
    left = {k: LEFT0[k] - free[k] for k in LEFT0}
    return {"calls": calls, "free_used": free, "paid_calls": {**over, "unlimited_pages": calls["unlimited_pages"]}, "paid_cny": paid,
            "list_cny": cny_list, "free_left": left, "paid_cumulative_cny": round(PAID_BEFORE_CNY + paid, 4)}


def run_logged(cmd, log):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    Path(log).write_text(redact(r.stdout + "\n" + r.stderr), encoding="utf-8")
    return r.returncode


def baidu(a):
    verify()
    py = str(ROOT / ".venv" / "bin" / "python")
    p2 = OUT / "P2" / "synth18c-P2.pdf"
    steps = [
        ("B 原图", WO / "output" / "scheme_b_standard", [py, "scripts/run_scheme_b.py", "--config", str(BAIDU_CFG), "--input", str(PDF),
                                                       "--out", str(WO / "output" / "scheme_b_standard"), "--edition", "standard"], 0.9),
        ("Unlimited 原图", WO / "output" / "scheme_b_unlimited", [py, "scripts/run_scheme_b.py", "--config", str(BAIDU_CFG), "--input", str(PDF),
                                                              "--out", str(WO / "output" / "scheme_b_unlimited"), "--edition", "unlimited"], 0.36),
        ("Unlimited P2", WP / "output" / "scheme_b_unlimited", [py, "scripts/run_scheme_b.py", "--config", str(BAIDU_CFG), "--input", str(p2),
                                                              "--out", str(WP / "output" / "scheme_b_unlimited"), "--edition", "unlimited"], 0.36),
        ("A 原图", WO / "output" / "scheme_a", [py, "scripts/synth18c_scheme_a_call.py", "--config", str(BAIDU_CFG), "--out", str(WO / "output" / "scheme_a")], 1.44),
        ("A P2", WP / "output" / "scheme_a", [py, "scripts/synth18c_scheme_a_call.py", "--config", str(BAIDU_CFG), "--out", str(WP / "output" / "scheme_a"),
                                             "--input", str(p2), "--force-api", "accurate"], 1.44),
    ]
    for name, out, cmd, worst_cny in steps:
        done = (out / "call_summary.json").exists() if name.startswith("A") else any((out / "raw").glob("*.parse_result.json")) if (out / "raw").exists() else False
        if done:
            print(name, "已完成，跳过")
            continue
        led = baidu_ledger()
        if led["paid_cumulative_cny"] + worst_cny > BAIDU_PAID_CAP_CNY:
            sys.exit(f"{name}: 累计付费 ¥{led['paid_cumulative_cny']} + 本步最坏 ¥{worst_cny} > ¥{BAIDU_PAID_CAP_CNY}，停止")
        if not a.live:
            print("[dry]", name, " ".join(cmd + ["--allow-external"]), "| 账本", json.dumps(led["free_left"], ensure_ascii=False))
            continue
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        rc = run_logged(cmd + ["--allow-external"], OUT / f"baidu_{name.replace(' ', '_')}.log")
        if rc != 0 and name.startswith("Unlimited") and any((out / "raw").glob("*.parse_result.json")):
            raw = next(p for p in out.glob("*.raw.json"))
            wj(out / "cost_note.json", {"note": "调用成功、原始返回已落盘；本地构建中间 JSON 失败，未写 cost.json。按 billed_pages 记账，不重发。",
                                        "api": "docparser_unlimited", "billed_pages": jl(raw).get("billed_pages"),
                                        "cny_list_estimate": round(0.02 * int(jl(raw).get("billed_pages") or 0), 4)})
            rc = 0
        print(name, "exit", rc, f"{time.time() - t0:.1f}s", json.dumps(baidu_ledger(), ensure_ascii=False))
        if rc != 0:
            sys.exit(f"{name} 失败，停止")


def reread_calls(a):
    verify()
    py = str(ROOT / ".venv" / "bin" / "python")
    bdoc = WO / "output" / "final" / "scheme_b_standard" / "blind20-scan.json"
    for tag, adir in (("orig", WO / "output" / "scheme_a"), ("P2", WP / "output" / "scheme_a")):
        out = OUT / "reread" / f"A_{tag}"
        if (out / "results.json").exists():
            print(tag, "已完成，跳过")
            continue
        led = baidu_ledger()
        if led["paid_cumulative_cny"] + 0.04 * 18 > BAIDU_PAID_CAP_CNY:
            sys.exit("付费上限风险，停止")
        cmd = [py, "scripts/opt2_reread.py", "--a-dir", str(adir), "--b-doc", str(bdoc), "--out", str(out), "--config", str(BAIDU_CFG), "--label", f"synth18c-reread-{tag}"]
        if a.live:
            cmd += ["--live", "--allow-external"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        txt = redact(r.stdout + "\n" + r.stderr)
        (OUT / f"reread_{tag}.log").parent.mkdir(parents=True, exist_ok=True)
        (OUT / f"reread_{tag}.log").write_text(txt, encoding="utf-8")
        print(tag, "exit", r.returncode, txt.strip().splitlines()[0] if txt.strip() else "")
        if r.returncode != 0:
            sys.exit("二次识别失败，停止")


# ------------------------------------------------------------------ build
def build(a):
    verify()
    import synth18_run as S
    bm = S.load_mod("blind20_build", ROOT / "scripts" / "blind20_build.py")
    Wr = WO / "output"
    a_sum = jl(Wr / "scheme_a" / "call_summary.json")
    for root, eds in ((WO, ("scheme_b_standard", "scheme_b_unlimited")), (WP, ("scheme_b_unlimited",))):
        for ed in eds:
            d = root / "output" / ed
            stem = next(p.name.replace(".parse_result.json", "") for p in (d / "raw").glob("*.parse_result.json") if not p.name.startswith("blind20"))
            shutil.copyfile(d / "raw" / f"{stem}.parse_result.json", d / "raw" / "blind20-scan.parse_result.json")
            if (d / f"{stem}.raw.json").exists():
                shutil.copyfile(d / f"{stem}.raw.json", d / "blind20-scan.raw.json")
    bm.OUT = Wr
    bm.SRC_NAME = PDF.name
    bm.build_scheme_b(Wr / "scheme_b_standard", Wr / "final" / "scheme_b_standard", "standard", a_sum)
    print("built")


# ------------------------------------------------------------------ 组 → 预测
A_DIRS = {"orig": WO / "output" / "scheme_a", "P2": WP / "output" / "scheme_a"}
U_RAW = {"orig": WO / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
         "P2": WP / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json"}


def b_doc():
    return jl(WO / "output" / "final" / "scheme_b_standard" / "blind20-scan.json")


def setup_O():
    import synth18_opt2 as O
    from baidu_ocr.postprocess import star_fill
    O.G["reread"] = {k: jl(OUT / "reread" / f"A_{k}" / "results.json")["units"] for k in A_DIRS}
    O.G["marks"] = {k: star_fill.load_marks(v) for k, v in A_DIRS.items()}
    return O


def base_doc_pr(O, g, doc):
    from baidu_ocr.postprocess import pred_pages
    from baidu_ocr.postprocess.aux import load_aux
    aux = load_aux(WO, doc, a_dir=A_DIRS[g["A"]], u_raw_path=U_RAW[g["U"]])
    d, counts = O.run_pipeline(g, doc, aux, None)
    return d, pred_pages(d), counts


def finish_pr(O, g, pr, tabs):
    from baidu_ocr.postprocess import star_fill
    if g["llm"] != "none":
        pr = O.apply_llm_tabs(pr, tabs, g["star"])
    n = 0
    if g.get("starmode") == "fill":
        n = star_fill.apply_pred(pr, O.G["marks"][g["A"]])
    return pr, n


# ------------------------------------------------------------------ llm
def oa_tokens(t):
    import tiktoken
    return len(tiktoken.get_encoding("o200k_base").encode(t))


def ds_tokens(t):
    import unicodedata
    cjk = sum(1 for ch in t if unicodedata.east_asian_width(ch) in ("W", "F"))
    return math.ceil(cjk * 0.6 + (len(t) - cjk) * 0.3)


def price(mc, when):
    if "price_usd_per_1m" in mc:
        return mc["price_usd_per_1m"]
    peak = when.weekday() < 5 and any(x <= when.hour < y for x, y in mc["peak_hours_utc"])
    return mc["price_usd_per_1m_peak" if peak else "price_usd_per_1m_offpeak"]


def chunk_key(model, ch):
    return hashlib.sha256((model + json.dumps(ch, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:20]


def llm_jobs(O):
    doc = b_doc()
    jobs = []
    for name, g in GROUPS.items():
        if g["llm"] == "none":
            continue
        _, pr, _ = base_doc_pr(O, g, doc)
        for ch in O.tables_of(pr):
            jobs.append((g["llm"], ch))
    return jobs


def llm(a):
    verify()
    import requests
    O = setup_O()
    cfg = yaml.safe_load(LLM_CFG.read_text(encoding="utf-8"))["llm"]
    assert sha(PROMPT) == PROMPT_SHA
    sysp = PROMPT.read_text(encoding="utf-8")
    led_p = LLMD / "ledger.jsonl"
    spent = sum(float(json.loads(l).get("usd") or 0) for l in led_p.read_text().splitlines() if l.strip()) if led_p.exists() else 0.0
    if a.live:
        assert cfg["allow_external"] is True and cfg.get("approval")
    plan, seen = [], set()
    for m, ch in llm_jobs(O):
        k = chunk_key(m, ch)
        if k in seen:
            continue
        seen.add(k)
        mc = cfg["models"][m]
        tok = oa_tokens if mc["provider"] == "openai" else ds_tokens
        um = json.dumps({"page": ch["page"], "segments": ch["segments"]}, ensure_ascii=False)
        exp = json.dumps({"segments": [{"id": s["id"], "text": s["text"]} for s in ch["segments"]]}, ensure_ascii=False)
        pt = tok(sysp) + tok(um) + 10
        ct = math.ceil(tok(exp) * 1.1)
        mo = int(min(16384, max(1024, ct * 2 + 256)))
        pk = mc.get("price_usd_per_1m_peak") or mc["price_usd_per_1m"]
        plan.append({"model": m, "key": k, "chunk": ch, "user": um, "mo": mo, "cached": (LLMD / "cache" / f"{k}.json").exists(),
                     "est": (pt * pk["input"] + ct * pk["output"]) / 1e6, "worst": (pt * pk["input"] + mo * pk["output"]) / 1e6})
    todo = [x for x in plan if not x["cached"]]
    est = {"unique_requests": len(plan), "to_call": len(todo), "est_usd": round(sum(x["est"] for x in todo), 4),
           "worst_usd": round(sum(x["worst"] for x in todo), 4), "spent_before": round(spent, 4), "cap_usd": LLM_CAP}
    wj(LLMD / "estimate.json", est)
    print(json.dumps(est, ensure_ascii=False))
    if est["worst_usd"] + spent > LLM_CAP:
        sys.exit("最坏情况超过上限，停止")
    if not a.live:
        return
    for x in todo:
        mc = cfg["models"][x["model"]]
        if spent + x["worst"] > LLM_CAP:
            sys.exit("触顶，停止")
        key = os.environ.get(mc["api_key_env"])
        assert key, f"{mc['api_key_env']} 不存在"
        body = {"model": mc["model"], "temperature": cfg["temperature"], "max_tokens": x["mo"],
                "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": x["user"]}], "response_format": {"type": "json_object"}}
        body.update(mc.get("extra_body") or {})
        for att in range(3):
            when = datetime.now(timezone.utc)
            rec = {"at": when.isoformat(timespec="seconds"), "model": x["model"], "key": x["key"], "page": x["chunk"]["page"], "attempt": att + 1}
            try:
                resp = requests.post(f"{mc['base_url']}/chat/completions", json=body, timeout=180,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                rec.update({"error": type(e).__name__, "usd": 0.0})
                with led_p.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                time.sleep(3)
                continue
            if resp.status_code != 200:
                rec.update({"http": resp.status_code, "error": resp.text[:200].replace(key, "***"), "usd": 0.0})
                with led_p.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                time.sleep(3)
                continue
            j = resp.json()
            u = j.get("usage") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0)
            pr_ = price(mc, when)
            usd = round(((pt - cached) * pr_["input"] + cached * pr_["cached_input"] + ct * pr_["output"]) / 1e6, 6)
            txt = j["choices"][0]["message"].get("content") or ""
            try:
                o = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip()))
                segs = {s["id"]: s["text"] for s in o["segments"] if isinstance(s, dict) and isinstance(s.get("text"), str)}
                ok = True
            except Exception:  # noqa: BLE001
                segs, ok = {}, False
            rec.update({"http": 200, "prompt_tokens": pt, "completion_tokens": ct, "cached_tokens": cached, "usd": usd, "served_model": j.get("model"),
                        "parse_ok": ok, "missing_ids": [s["id"] for s in x["chunk"]["segments"] if s["id"] not in segs]})
            spent += usd
            with led_p.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            wj(LLMD / "cache" / f"{x['key']}.json", {"model": x["model"], "page": x["chunk"]["page"], "segments": segs, "raw": txt,
                                                     "draft": {s["id"]: s["text"] for s in x["chunk"]["segments"]}, "usage": u, "usd": usd})
            print(x["model"], f"p{x['chunk']['page']:02d}", pt, ct, f"${usd:.5f}", f"累计 ${spent:.4f}", flush=True)
            break
    print(f"LLM 累计 ${spent:.4f}")


def llm_tabs_for(O, model, pr):
    out = {}
    for ch in O.tables_of(pr):
        f = LLMD / "cache" / f"{chunk_key(model, ch)}.json"
        if not f.exists():
            return None
        r = jl(f)
        out[ch["page"]] = [r["segments"].get(s["id"], s["text"]) for s in ch["segments"]]
    return out


def build_preds():
    from baidu_ocr.postprocess import pred_pages
    O = setup_O()
    doc = b_doc()
    preds, counts = {"B 原始（原图）": pred_pages(doc)}, {}
    allg = {**GROUPS, **{k: v for k, v in REFERENCE_ROWS.items() if not v.get("raw")}}
    for name, g in allg.items():
        _, pr, cnt = base_doc_pr(O, g, doc)
        tabs = llm_tabs_for(O, g["llm"], pr) if g["llm"] != "none" else None
        if g["llm"] != "none" and tabs is None:
            sys.exit(f"{name}: 缺 LLM 结果")
        pr, nsf = finish_pr(O, g, pr, tabs)
        preds[name], counts[name] = pr, {**cnt, "star_fill": nsf}
    return preds, counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["freeze", "verify", "prep", "baidu", "reread", "build", "llm", "score", "ledger"])
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.cmd == "score":
        import synth18c_score
        synth18c_score.main()
    elif a.cmd == "reread":
        reread_calls(a)
    elif a.cmd == "ledger":
        print(json.dumps(baidu_ledger(), ensure_ascii=False, indent=1))
    else:
        globals()[a.cmd](a)
