#!/usr/bin/env python3
"""synth18b 留出集一次性验证：冻结 G1 / G2（及子变体）→ 百度 → LLM 表格纠错 → 一次计分（严格版 + 约定容忍版）。

  freeze          写 output/synth18b/frozen_synth18b.json（独占创建，已存在则拒绝）：组定义 + 代码/配置/提示词 sha256 + 扫描件 sha256
  verify          校验冻结文件（之后每一步都先跑）
  prep            渲染 synth18b-scan.pdf 300 DPI → P2 去章 → 装 PDF（不联网）
  baidu [--live]  B 文档解析（原图、P2 各 18 页）+ A（accurate 原走法，原图）+ Unlimited（原图）；开跑前与每步前做免费额度检查
  build           原始返回 → 中间 JSON
  llm [--live]    表格段 → gpt-4o（原图 B、P2 B）、deepseek（原图 B）；最坏情况校验，合计 ≤ $2
  score           一次性计分（结束写标记，之后再运行拒绝）：synth18b 严格版 + 容忍版（签名入/剔除）、错误分类、LLM 新引入错误；
                  同一容忍脚本重算 synth18（开发集）上的 B 原始 / G1 / G2 / G2-ds
计分前本脚本不打开 synth18b 的 blind-ref.*、manifest.json、textlayer、_build/（只打开 synth18b-scan.pdf）。
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

S18B = Path("/workspace/pdf-test/synth18b")
PDF = S18B / "synth18b-scan.pdf"
PDF_SHA = "9520a50a431f57c3d9ae110ca553100b2d4d026908bf9864e605b28109345a40"
OUT = ROOT / "output" / "synth18b"
FROZEN = OUT / "frozen_synth18b.json"
WO = OUT / "orig" / "work"          # 模拟项目根（原图）
WP = OUT / "P2" / "work"            # P2 去章版（只有 B；A/Unlimited 用原图的）
LLMD = OUT / "llm"
BAIDU_CFG = ROOT / "config_synth18b_live.yaml"
LLM_CFG = ROOT / "config_llm_synth18b_live.yaml"
PROMPT = ROOT / "output" / "synth18_llm" / "prompt_v1.txt"
PROMPT_SHA = "86ae3d1c77ba3276cc209077ec04166ae878031a09fc285057c5b345430d231a"
LLM_CAP = 2.0
# 免费额度（本地日志推算，截至 synth18 优化第二步）：文档解析 110/200 页，accurate 47/500，general 21/1000
USED0 = {"docparser_pages": 110, "accurate_calls": 47, "general_calls": 21}
FREE = {"docparser_pages": 200, "accurate_calls": 500, "general_calls": 1000}

GROUPS = {
    "G1": {"preproc": "P2", "star": True, "seal": "off", "fill": False, "inv": False, "img": True, "llm": "none"},
    "G1+gpt-4o": {"preproc": "P2", "star": True, "seal": "off", "fill": False, "inv": False, "img": True, "llm": "gpt-4o"},
    "G2": {"preproc": "orig", "star": True, "seal": "off", "fill": True, "inv": False, "img": True, "llm": "gpt-4o"},
    "G2-deepseek": {"preproc": "orig", "star": True, "seal": "off", "fill": True, "inv": False, "img": True, "llm": "deepseek"},
}
REFERENCE_ROWS = {"B 原始（原图）": {"preproc": "orig", "raw": True}, "B 原始（P2）": {"preproc": "P2", "raw": True},
                  "G2 去掉 LLM（诊断基线，用于统计 LLM 新引入错误）": {"preproc": "orig", "star": True, "seal": "off", "fill": True, "inv": False, "img": True, "llm": "none"}}
CODE = ["scripts/synth18b_run.py", "scripts/synth18b_scheme_a_call.py", "scripts/run_scheme_b.py", "scripts/blind20_build.py",
        "scripts/synth18_combos.py", "scripts/synth18_preproc.py", "scripts/tolerant_score.py", "scripts/synth18b_score.py", "scripts/blind20_score.py",
        "scripts/synth18_score.py", "scripts/bestconfig_run.py", "scripts/hybrid_score.py", "scripts/synth18_run.py",
        "src/baidu_ocr/postprocess/__init__.py", "src/baidu_ocr/postprocess/pipeline.py", "src/baidu_ocr/postprocess/star.py",
        "src/baidu_ocr/postprocess/seal_strip.py", "src/baidu_ocr/postprocess/fill_gaps.py", "src/baidu_ocr/postprocess/invoice_reorder.py",
        "src/baidu_ocr/postprocess/aux.py", "src/baidu_ocr/postprocess/textutil.py", "src/baidu_ocr/score.py",
        "config.yaml", "config_synth18b_live.yaml", "config_llm_synth18b_live.yaml", "output/synth18_llm/prompt_v1.txt",
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


# ------------------------------------------------------------------ freeze
def freeze(a):
    OUT.mkdir(parents=True, exist_ok=True)
    assert sha(PDF) == PDF_SHA
    assert sha(PROMPT) == PROMPT_SHA
    doc = {"created_at": now(), "purpose": "synth18b 留出集一次性验证；冻结后不改组定义/代码/配置",
           "groups": GROUPS, "reference_rows": REFERENCE_ROWS,
           "group_notes": {"preproc P2": "scripts/synth18_preproc.py 的 p2()（HSV 红色掩膜内像素换成 R 通道灰度），300 DPI，JPEG q88 装回原页面尺寸 PDF",
                           "star/seal/fill/inv": "frozen 后处理步骤原样（config.yaml postprocess 节），seal=off 关 seal_strip，inv=off 关 invoice_reorder",
                           "img": "image_fill（scripts/synth18_combos.py）：B 的空 image 块用 A 字框按行拼回",
                           "llm": "只换表格段；LLM 输入 = B 原始（未后处理）该页的表格段；prompt_v1 不改；star 开时对换入文字再套 ★/☆ 规则",
                           "A/Unlimited": "G1/G2 都用原图的 A（accurate 原走法）与 Unlimited；G1 的 B 来自 P2 版",
                           "6.2": "不用（未获批）"},
           "scoring": {"strict": "blind20_score.parse_ref/score_system，参照 = synth18b/blind-ref.txt，manifest = synth18b/manifest.json，不套 blind20 修正",
                       "tolerant": "scripts/tolerant_score.py：页边竖排/印章抽出单独比、发票表头带配对、签名入/剔除两列；身份证页不动",
                       "main": "约定容忍版 + 签名入参照", "runs": "只跑一次（output/synth18b/SCORED 标记）",
                       "expected_ref_sha_prefix": "5f73e9c1（父 agent 告知；计分时读取并记录实际 sha）"},
           "input": {"scan_pdf": str(PDF), "scan_sha256": PDF_SHA},
           "prompt_sha256": PROMPT_SHA, "llm_cap_usd": LLM_CAP,
           "code_sha256": {c: sha(ROOT / c) for c in CODE}}
    with open(FROZEN, "x", encoding="utf-8") as f:   # 独占创建：已存在则抛 FileExistsError
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
        png = OUT / "P2" / "img" / f"p{i + 1:02d}.png"
        pix = pg.get_pixmap(dpi=300)
        pix.save(OUT / "P2" / "img" / f"orig_p{i + 1:02d}.png")
        img = P.p2(cv2.imread(str(OUT / "P2" / "img" / f"orig_p{i + 1:02d}.png")))
        cv2.imwrite(str(png), img)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        np_ = doc.new_page(width=pg.rect.width, height=pg.rect.height)
        np_.insert_image(np_.rect, stream=buf.tobytes())
    doc.save(OUT / "P2" / "synth18b-P2.pdf", deflate=True)
    print("P2 pdf", len(doc), "页", (OUT / "P2" / "synth18b-P2.pdf").stat().st_size // 1024, "KB")


# ------------------------------------------------------------------ baidu
def used():
    u = dict(USED0)
    for f in OUT.glob("**/cost.json"):
        for x in jl(f).get("calls") or []:
            if not x.get("success") or x.get("dry_run"):
                continue
            if x["api"] == "docparser_standard":
                u["docparser_pages"] += int(x.get("units") or 0)
            elif x["api"] == "accurate":
                u["accurate_calls"] += 1
            elif x["api"] == "general":
                u["general_calls"] += 1
    return u


def left():
    u = used()
    return {k: FREE[k] - u[k] for k in FREE}


def redact(s):
    return re.sub(r"access_token=[^&\s\"']+", "access_token=***REDACTED***", s)


def run_logged(cmd, log):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    Path(log).write_text(redact(r.stdout + "\n" + r.stderr), encoding="utf-8")
    return r.returncode


def baidu(a):
    verify()
    py = str(ROOT / ".venv" / "bin" / "python")
    steps = [
        ("B 原图", WO / "output" / "scheme_b_standard", [py, "scripts/run_scheme_b.py", "--config", str(BAIDU_CFG), "--input", str(PDF),
                                                       "--out", str(WO / "output" / "scheme_b_standard"), "--edition", "standard"], {"docparser_pages": 18}),
        ("B P2", WP / "output" / "scheme_b_standard", [py, "scripts/run_scheme_b.py", "--config", str(BAIDU_CFG), "--input", str(OUT / "P2" / "synth18b-P2.pdf"),
                                                     "--out", str(WP / "output" / "scheme_b_standard"), "--edition", "standard"], {"docparser_pages": 18}),
        ("Unlimited 原图", WO / "output" / "scheme_b_unlimited", [py, "scripts/run_scheme_b.py", "--config", str(BAIDU_CFG), "--input", str(PDF),
                                                              "--out", str(WO / "output" / "scheme_b_unlimited"), "--edition", "unlimited"], {}),
        ("A 原图", WO / "output" / "scheme_a", [py, "scripts/synth18b_scheme_a_call.py", "--config", str(BAIDU_CFG), "--out", str(WO / "output" / "scheme_a")],
         {"accurate_calls": 36}),
    ]
    lf = left()
    need = {"docparser_pages": 36, "accurate_calls": 36}
    print("免费余量", lf, "本次最多需要", need)
    if any(lf[k] < v for k, v in need.items()):
        sys.exit("可能超出免费额度，停止")
    for name, out, cmd, nd in steps:
        done = (out / "cost.json").exists() and any(x.get("success") for x in jl(out / "cost.json").get("calls") or [])
        if name.startswith("A") and (out / "call_summary.json").exists():
            done = True
        if done:
            print(name, "已完成，跳过")
            continue
        lf = left()
        if any(lf[k] < v for k, v in nd.items()):
            sys.exit(f"{name}: 余量 {lf} 不够，停止")
        if not a.live:
            print("[dry]", name, " ".join(cmd + ["--allow-external"]))
            continue
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        rc = run_logged(cmd + ["--allow-external"], OUT / f"baidu_{name.replace(' ', '_')}.log")
        print(name, "exit", rc, f"{time.time() - t0:.1f}s", "余量", left())
        if rc != 0:
            sys.exit(f"{name} 失败，停止")


# ------------------------------------------------------------------ build
def build(a):
    verify()
    import synth18_run as S
    bm = S.load_mod("blind20_build", ROOT / "scripts" / "blind20_build.py")
    W = WO / "output"
    a_sum = jl(W / "scheme_a" / "call_summary.json")
    for root, eds in ((WO, ("scheme_b_standard", "scheme_b_unlimited")), (WP, ("scheme_b_standard",))):
        Wr = root / "output"
        for ed in eds:
            d = Wr / ed
            stem = next(p.stem.replace(".parse_result", "") for p in (d / "raw").glob("*.parse_result.json") if not p.name.startswith("blind20"))
            shutil.copyfile(d / "raw" / f"{stem}.parse_result.json", d / "raw" / "blind20-scan.parse_result.json")
            shutil.copyfile(d / f"{stem}.raw.json", d / "blind20-scan.raw.json")
        bm.OUT = Wr
        bm.SRC_NAME = PDF.name
        if root is WO:
            bm.build_scheme_a(Wr / "scheme_a", Wr / "final" / "scheme_a", "routed")
            bm.build_scheme_b(Wr / "scheme_b_unlimited", Wr / "final" / "scheme_b_unlimited", "unlimited", a_sum)
        bm.build_scheme_b(Wr / "scheme_b_standard", Wr / "final" / "scheme_b_standard", "standard", a_sum)
    print("built")


def b_doc(pre):
    return jl((WO if pre == "orig" else WP) / "output" / "final" / "scheme_b_standard" / "blind20-scan.json")


def aux_for(doc):
    from baidu_ocr.postprocess.aux import load_aux
    return load_aux(WO, doc)


# ------------------------------------------------------------------ llm（只发表格段）
def llm_chunks(pre):
    from baidu_ocr.postprocess import pred_pages
    pr = pred_pages(b_doc(pre))
    out = []
    for p in sorted(pr, key=int):
        segs = [{"id": f"p{int(p):02d}-s{i + 1:02d}", "type": "table", "text": s["text"]} for i, s in enumerate(pr[p]["sections"]) if s["section"] == "table"]
        if segs:
            out.append({"page": int(p), "segments": segs})
    return out


JOBS = [("gpt-4o", "orig"), ("deepseek", "orig"), ("gpt-4o", "P2")]


def oa_tokens(t):
    import tiktoken
    return len(tiktoken.get_encoding("o200k_base").encode(t))


def ds_tokens(t):
    import unicodedata
    cjk = sum(1 for c in t if unicodedata.east_asian_width(c) in ("W", "F"))
    return math.ceil(cjk * 0.6 + (len(t) - cjk) * 0.3)


def price(mc, when):
    if "price_usd_per_1m" in mc:
        return mc["price_usd_per_1m"]
    peak = when.weekday() < 5 and any(x <= when.hour < y for x, y in mc["peak_hours_utc"])
    return mc["price_usd_per_1m_peak" if peak else "price_usd_per_1m_offpeak"]


def usd(mc, pt, ct, cached, when):
    pr = price(mc, when)
    return round(((pt - cached) * pr["input"] + cached * pr["cached_input"] + ct * pr["output"]) / 1e6, 6)


def llm(a):
    verify()
    import requests
    cfg = yaml.safe_load(LLM_CFG.read_text(encoding="utf-8"))["llm"]
    assert sha(PROMPT) == PROMPT_SHA
    sysp = PROMPT.read_text(encoding="utf-8")
    led_p = LLMD / "ledger.jsonl"
    spent = sum(float(json.loads(l).get("usd") or 0) for l in led_p.read_text().splitlines() if l.strip()) if led_p.exists() else 0.0
    if a.live:
        assert cfg["allow_external"] is True and cfg.get("approval")
    plan = []
    for m, pre in JOBS:
        mc = cfg["models"][m]
        tok = oa_tokens if mc["provider"] == "openai" else ds_tokens
        for ch in llm_chunks(pre):
            um = json.dumps({"page": ch["page"], "segments": ch["segments"]}, ensure_ascii=False)
            exp = json.dumps({"segments": [{"id": s["id"], "text": s["text"]} for s in ch["segments"]]}, ensure_ascii=False)
            pt = tok(sysp) + tok(um) + 10
            ct = math.ceil(tok(exp) * 1.1)
            mo = int(min(16384, max(1024, ct * 2 + 256)))
            pk = mc.get("price_usd_per_1m_peak") or mc["price_usd_per_1m"]
            plan.append({"model": m, "pre": pre, "page": ch["page"], "chunk": ch, "user": um, "pt": pt, "ct": ct, "mo": mo,
                         "est": (pt * pk["input"] + ct * pk["output"]) / 1e6, "worst": (pt * pk["input"] + mo * pk["output"]) / 1e6})
    est = {"n_requests": len(plan), "est_usd": round(sum(x["est"] for x in plan), 4), "worst_usd": round(sum(x["worst"] for x in plan), 4),
           "by_job": {f"{m}/{pre}": {"requests": sum(1 for x in plan if (x["model"], x["pre"]) == (m, pre)),
                                     "est_usd": round(sum(x["est"] for x in plan if (x["model"], x["pre"]) == (m, pre)), 4),
                                     "prompt_tokens": sum(x["pt"] for x in plan if (x["model"], x["pre"]) == (m, pre))} for m, pre in JOBS},
           "spent_before": round(spent, 4), "cap_usd": LLM_CAP}
    wj(LLMD / "estimate.json", est)
    print(json.dumps(est, ensure_ascii=False))
    if est["worst_usd"] + spent > LLM_CAP:
        sys.exit("最坏情况超过上限，停止")
    if not a.live:
        return
    for x in plan:
        rp = LLMD / "results" / f"{x['model']}_{x['pre']}" / f"p{x['page']:02d}.json"
        if rp.exists():
            continue
        mc = cfg["models"][x["model"]]
        if spent + x["worst"] > LLM_CAP:
            sys.exit(f"触顶：已花 ${spent:.4f} + 本次最坏 ${x['worst']:.4f} > ${LLM_CAP}")
        key = os.environ.get(mc["api_key_env"])
        assert key, f"{mc['api_key_env']} 不存在"
        body = {"model": mc["model"], "temperature": cfg["temperature"], "max_tokens": x["mo"],
                "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": x["user"]}], "response_format": {"type": "json_object"}}
        body.update(mc.get("extra_body") or {})
        for att in range(3):
            when = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            rec = {"at": when.isoformat(timespec="seconds"), "model": x["model"], "pre": x["pre"], "page": x["page"], "attempt": att + 1}
            try:
                resp = requests.post(f"{mc['base_url']}/chat/completions", json=body, timeout=180,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                rec.update({"error": type(e).__name__, "usd": 0.0, "sec": round(time.perf_counter() - t0, 2)})
                with led_p.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                time.sleep(3)
                continue
            rec.update({"http": resp.status_code, "sec": round(time.perf_counter() - t0, 2)})
            if resp.status_code != 200:
                rec.update({"error": resp.text[:200].replace(key, "***"), "usd": 0.0})
                with led_p.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                time.sleep(3)
                continue
            j = resp.json()
            u = j.get("usage") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0)
            rec.update({"prompt_tokens": pt, "completion_tokens": ct, "cached_tokens": cached, "usd": usd(mc, pt, ct, cached, when),
                        "served_model": j.get("model"), "finish": j["choices"][0].get("finish_reason")})
            txt = j["choices"][0]["message"].get("content") or ""
            try:
                o = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip()))
                segs = {s["id"]: s["text"] for s in o["segments"] if isinstance(s, dict) and isinstance(s.get("text"), str)}
                rec["parse_ok"] = True
            except Exception as e:  # noqa: BLE001
                segs, rec["parse_ok"] = {}, False
            rec["missing_ids"] = [s["id"] for s in x["chunk"]["segments"] if s["id"] not in segs]
            spent += rec["usd"]
            with led_p.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            wj(rp, {"page": x["page"], "segments": segs, "raw": txt, "draft": {s["id"]: s["text"] for s in x["chunk"]["segments"]}, "usage": u, "usd": rec["usd"]})
            print(x["model"], x["pre"], f"p{x['page']:02d}", pt, ct, f"${rec['usd']:.5f}", f"累计 ${spent:.4f}")
            break
    print(f"LLM 累计 ${spent:.4f}")


def llm_tabs(model, pre):
    d = LLMD / "results" / f"{model}_{pre}"
    out = {}
    for f in sorted(d.glob("p*.json")):
        r = jl(f)
        out[r["page"]] = [r["segments"].get(i, t) for i, t in r["draft"].items()]
    return out


# ------------------------------------------------------------------ 组 → 预测
def run_group(g, doc, aux, tabs):
    import synth18_combos as C
    from baidu_ocr.postprocess import pred_pages, run_postprocess
    from baidu_ocr.postprocess.pipeline import merged_cfg
    pp = merged_cfg(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["postprocess"])
    pp["star"]["enabled"] = g["star"]
    pp["seal_strip"]["enabled"] = g["seal"] != "off"
    pp["fill_gaps"]["enabled"] = g["fill"]
    pp["invoice_reorder"]["enabled"] = g["inv"]
    pp["vlm_backfill"]["enabled"] = False
    d = copy.deepcopy(doc)
    log = []
    if g["img"]:
        C.image_fill(d, aux, log)
    d, info = run_postprocess(d, pp, aux)
    pr = pred_pages(d)
    if g["llm"] != "none":
        pr = C.apply_llm(pr, tabs, g["star"])
    return pr, {**info["counts"], "image_fill": len(log)}, d


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["freeze", "verify", "prep", "baidu", "build", "llm", "score"])
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.cmd == "score":
        import synth18b_score
        synth18b_score.main()
    else:
        globals()[a.cmd](a)
