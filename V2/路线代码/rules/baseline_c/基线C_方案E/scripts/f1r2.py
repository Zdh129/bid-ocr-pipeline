#!/usr/bin/env python3
"""f1_round2：以 F1+DeepSeek 为底座的三个变体 + 合并版，在 synth18 与 synth18c（非盲，已计过分）上本地评估。
冻结文件与冻结代码都不改；新步骤在 src/baidu_ocr/postprocess/{seal_leak,invoice_reorder_v3,star_filter}.py。

  llm [--live]   变体改了表格段时（v3），对新表格文字调 DeepSeek（prompt_v1，sha 校验；缓存按内容复用）
  eval           全部变体 × 两个数据集，三套口径 + 新引入错误 → output/f1_round2/rows.json、report.md
流水线：image_fill → [seal_leak] → star(R1-R3) → fill_gaps → invoice_reorder_v2|v3 → reread → LLM 表格段(deepseek) → star_fill|star_filter
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from baidu_ocr.postprocess import (fill_gaps, invoice_check, invoice_header, invoice_reorder_v2, invoice_reorder_v3,  # noqa: E402
                                   pred_pages, reread, seal_fill_guard, seal_leak, star, star_fill, star_filter, vertical_margin)
from baidu_ocr.postprocess.aux import load_aux  # noqa: E402
from baidu_ocr.postprocess.pipeline import merged_cfg  # noqa: E402

OUT = ROOT / "output" / "f1_round2"
PROMPT = ROOT / "output" / "synth18_llm" / "prompt_v1.txt"
PROMPT_SHA = "86ae3d1c77ba3276cc209077ec04166ae878031a09fc285057c5b345430d231a"
O18 = ROOT / "output" / "synth18"
O18W = O18 / "work" / "output"
OP2 = ROOT / "output" / "synth18_opt2"
O18C = ROOT / "output" / "synth18c"
DS = {
    "synth18": {"dir": Path("/workspace/pdf-test/synth18"), "b": O18W / "final" / "scheme_b_standard" / "blind20-scan.json", "root": O18 / "work",
                "a": OP2 / "P2" / "work" / "output" / "scheme_a", "u": OP2 / "P2" / "work" / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
                "reread": OP2 / "reread" / "A_P2" / "results.json", "render": O18W / "scheme_a" / "renders",
                "llm_caches": [OP2 / "llm" / "cache"], "llm_cfg": ROOT / "config_llm_synth18opt2_live.yaml",
                "llm_spent_before": 0.1574 + 0.0840, "blind": True, "a_orig_summary": O18W / "scheme_a" / "call_summary.json"},
    "synth18c": {"dir": Path("/workspace/pdf-test/synth18c"), "b": O18C / "orig" / "work" / "output" / "final" / "scheme_b_standard" / "blind20-scan.json",
                 "root": O18C / "orig" / "work", "a": O18C / "P2" / "work" / "output" / "scheme_a",
                 "u": O18C / "P2" / "work" / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
                 "reread": O18C / "reread" / "A_P2" / "results.json", "render": O18C / "orig" / "work" / "output" / "scheme_a" / "renders",
                 "llm_caches": [O18C / "llm" / "cache"], "llm_cfg": ROOT / "config_llm_synth18c_live.yaml",
                 "llm_spent_before": 0.0407, "blind": False, "a_orig_summary": O18C / "orig" / "work" / "output" / "scheme_a" / "call_summary.json"},
}
BASE = "底座：方案F1＋深度求索纠错"
S1B, S2, S3 = {"seal": "whole"}, {"inv": "v3"}, {"starf": True}
VARIANTS = {
    BASE: {},
    "变体一甲：印章混入·去红重识（按连续字段删）": {"seal": "runs"},
    "变体一乙：印章混入·去红重识（只删整块）": dict(S1B),
    "变体二：发票明细与合计行顺序（第三版重排）": dict(S2),
    "变体三：星号过滤": dict(S3),
    "合并一：一乙＋二＋三": {**S1B, **S2, **S3},
    "合并二：一甲＋二＋三": {"seal": "runs", **S2, **S3},
    "新变体一甲：发票表头·固定字段顺序": {"hdr": "fields"},
    "新变体一乙：发票表头·按坐标分栏": {"hdr": "columns"},
    "新变体二：页边竖排·旋转识别放页末": {"vert": True},
    "新变体二（对照：标记写进正文）": {"vert": True, "vmark": True},
    "新变体三：补漏防章": {"guard": True},
    "新变体四：发票金额校验": {"check": True},
    "全部叠加甲：一乙＋二＋三＋新一甲＋新二＋新三＋新四": {**S1B, **S2, **S3, "hdr": "fields", "vert": True, "guard": True, "check": True},
    "全部叠加乙：一乙＋二＋三＋新一乙＋新二＋新三＋新四": {**S1B, **S2, **S3, "hdr": "columns", "vert": True, "guard": True, "check": True},
    "推荐组合：一乙＋二＋三＋新一甲＋新二＋新四（不含新三）": {**S1B, **S2, **S3, "hdr": "fields", "vert": True, "check": True},
}


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tables_of(pr):
    out = []
    for p in sorted(pr, key=int):
        segs = [{"id": f"p{int(p):02d}-s{i + 1:02d}", "type": "table", "text": s["text"]} for i, s in enumerate(pr[p]["sections"]) if s["section"] == "table"]
        if segs:
            out.append({"page": int(p), "segments": segs})
    return out


def chunk_key(model, ch):
    return hashlib.sha256((model + json.dumps(ch, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:20]


class Ctx:
    def __init__(self, name):
        d = DS[name]
        self.name, self.d = name, d
        self.doc = jl(d["b"])
        self.aux = load_aux(d["root"], self.doc, a_dir=d["a"], u_raw_path=d["u"])
        self.reread = jl(d["reread"])["units"]
        sl = OUT / name / "seal_leak" / "results.json"
        self.seal = jl(sl)["units"] if sl.exists() else []
        vr = OUT / name / "vertical" / "results.json"
        self.vert = jl(vr)["units"] if vr.exists() else []
        self.seal_boxes = seal_fill_guard.seal_boxes(self.doc, d["a_orig_summary"])
        self.marks = star_fill.load_marks(d["a"])
        self.marks_f = star_filter.load_marks(d["a"])
        self.pp = merged_cfg(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["postprocess"])
        self.caches = d["llm_caches"] + [OUT / name / "llm" / "cache"]

    def base(self, v):
        import synth18_combos as C
        doc, log, cnt = copy.deepcopy(self.doc), [], {}
        fill_aux = seal_fill_guard.filter_aux(self.aux, self.seal_boxes, log) if v.get("guard") else self.aux
        cnt["image_fill"] = C.image_fill(doc, fill_aux, log)
        if v.get("seal"):
            cnt["seal_leak"] = seal_leak.apply(doc, self.seal, log, mode=v["seal"])
        cnt["star"] = star.apply(doc, self.pp["star"], log)
        cnt["fill_gaps"] = fill_gaps.apply(doc, self.pp["fill_gaps"], log, fill_aux)
        inv = invoice_reorder_v3 if v.get("inv") == "v3" else invoice_reorder_v2
        cnt["invoice_reorder"] = inv.apply(doc, {}, log, self.aux)
        cnt["reread"] = reread.apply(doc, self.reread, log)
        if v.get("hdr"):
            cnt["invoice_header"] = invoice_header.apply(doc, log, self.aux, v["hdr"])
        if v.get("vert"):
            cnt["vertical_margin"] = vertical_margin.apply(doc, self.vert, log, marker_text=bool(v.get("vmark")))
        return pred_pages(doc), cnt, log

    def tabs(self, pr, model="deepseek"):
        out = {}
        for ch in tables_of(pr):
            f = next((c / f"{chunk_key(model, ch)}.json" for c in self.caches if (c / f"{chunk_key(model, ch)}.json").exists()), None)
            if f is None:
                return None, ch
            r = jl(f)
            out[ch["page"]] = [r["segments"].get(s["id"], s["text"]) for s in ch["segments"]]
        return out, None

    def run(self, v, llm="deepseek"):
        import synth18_combos as C
        pr, cnt, log = self.base(v)
        if llm:
            t, miss = self.tabs(pr, llm)
            if t is None:
                raise RuntimeError(f"缺 LLM 结果 p{miss['page']}")
            pr = C.apply_llm(pr, t, True)
        if v.get("check"):
            cnt["invoice_check"] = invoice_check.apply_pred(pr, log)
        if v.get("starf"):
            cnt["star_filter"] = star_filter.apply_pred(pr, self.marks_f, log)
        else:
            cnt["star_fill"] = star_fill.apply_pred(pr, self.marks, log)
        return pr, cnt, log


# ------------------------------------------------------------------ LLM（只对缓存里没有的新表格文字）
def price(mc, when):
    if "price_usd_per_1m" in mc:
        return mc["price_usd_per_1m"]
    peak = when.weekday() < 5 and any(x <= when.hour < y for x, y in mc["peak_hours_utc"])
    return mc["price_usd_per_1m_peak" if peak else "price_usd_per_1m_offpeak"]


def ds_tokens(t):
    import unicodedata
    cjk = sum(1 for ch in t if unicodedata.east_asian_width(ch) in ("W", "F"))
    return math.ceil(cjk * 0.6 + (len(t) - cjk) * 0.3)


def llm(a):
    import requests
    assert sha(PROMPT) == PROMPT_SHA
    sysp = PROMPT.read_text(encoding="utf-8")
    model = "deepseek"
    for name in DS:
        c = Ctx(name)
        cfg = yaml.safe_load(c.d["llm_cfg"].read_text(encoding="utf-8"))["llm"]
        mc = cfg["models"][model]
        ldir = OUT / name / "llm"
        led_p = ldir / "ledger.jsonl"
        spent_round = sum(float(json.loads(l).get("usd") or 0) for l in led_p.read_text().splitlines() if l.strip()) if led_p.exists() else 0.0
        todo, seen = [], set()
        for vn, v in VARIANTS.items():
            pr, _, _ = c.base(v)
            for ch in tables_of(pr):
                k = chunk_key(model, ch)
                if k in seen or any((cc / f"{k}.json").exists() for cc in c.caches):
                    continue
                seen.add(k)
                um = json.dumps({"page": ch["page"], "segments": ch["segments"]}, ensure_ascii=False)
                exp = json.dumps({"segments": [{"id": s["id"], "text": s["text"]} for s in ch["segments"]]}, ensure_ascii=False)
                pt, ct = ds_tokens(sysp) + ds_tokens(um) + 10, math.ceil(ds_tokens(exp) * 1.1)
                mo = int(min(16384, max(1024, ct * 2 + 256)))
                pk = mc["price_usd_per_1m_peak"]
                todo.append({"key": k, "chunk": ch, "user": um, "mo": mo, "worst": (pt * pk["input"] + mo * pk["output"]) / 1e6})
        worst = sum(x["worst"] for x in todo)
        print(name, json.dumps({"to_call": len(todo), "worst_usd": round(worst, 4), "spent_before": c.d["llm_spent_before"] + spent_round, "cap": 2.0}))
        if c.d["llm_spent_before"] + spent_round + worst > 2.0:
            sys.exit("最坏情况超过上限，停止")
        if not a.live:
            continue
        assert cfg["allow_external"] is True and cfg.get("approval")
        key = os.environ.get(mc["api_key_env"])
        assert key
        for x in todo:
            body = {"model": mc["model"], "temperature": cfg["temperature"], "max_tokens": x["mo"],
                    "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": x["user"]}], "response_format": {"type": "json_object"}}
            body.update(mc.get("extra_body") or {})
            for att in range(3):
                when = datetime.now(timezone.utc)
                rec = {"at": when.isoformat(timespec="seconds"), "model": model, "key": x["key"], "page": x["chunk"]["page"], "attempt": att + 1}
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
                    led_p.parent.mkdir(parents=True, exist_ok=True)
                    with led_p.open("a") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    time.sleep(3)
                    continue
                j = resp.json()
                u = j.get("usage") or {}
                pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
                cached = int(u.get("prompt_cache_hit_tokens") or 0)
                p_ = price(mc, when)
                usd = round(((pt - cached) * p_["input"] + cached * p_["cached_input"] + ct * p_["output"]) / 1e6, 6)
                txt = j["choices"][0]["message"].get("content") or ""
                try:
                    o = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip()))
                    segs = {s["id"]: s["text"] for s in o["segments"] if isinstance(s, dict) and isinstance(s.get("text"), str)}
                    ok = True
                except Exception:  # noqa: BLE001
                    segs, ok = {}, False
                rec.update({"http": 200, "prompt_tokens": pt, "completion_tokens": ct, "usd": usd, "parse_ok": ok,
                            "missing_ids": [s["id"] for s in x["chunk"]["segments"] if s["id"] not in segs]})
                led_p.parent.mkdir(parents=True, exist_ok=True)
                with led_p.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                wj(ldir / "cache" / f"{x['key']}.json", {"model": model, "page": x["chunk"]["page"], "segments": segs, "raw": txt,
                                                         "draft": {s["id"]: s["text"] for s in x["chunk"]["segments"]}, "usage": u, "usd": usd})
                print(name, f"p{x['chunk']['page']:02d}", pt, ct, f"${usd:.5f}", flush=True)
                break


# ------------------------------------------------------------------ 评估
def fmt(x):
    return f"{x['readout']:.2f}% / {x['cer']:.2f}% / {x['D']}/{x['S']}/{x['I']}"


def summarize(lst):
    k = Counter()
    for e in lst:
        k[e["kind"]] += e["cost"]
    return {"n": len(lst), "chars": sum(e["cost"] for e in lst), "by_kind": dict(k)}


def evaluate(a):
    import synth18b_score as SB
    import tolerant_score as T
    allres = {}
    for name in DS:
        c = Ctx(name)
        preds, counts, logs = {"文档解析原始结果": pred_pages(c.doc)}, {}, {}
        for vn, v in VARIANTS.items():
            preds[vn], counts[vn], lg = c.run(v)
            logs[vn] = [x for x in lg if x.get("step") in ("seal_leak", "invoice_reorder_v3", "star_filter", "invoice_header", "vertical_margin", "seal_fill_guard", "invoice_check")]
        preds["方案F1（不加纠错）"], counts["方案F1（不加纠错）"], _ = c.run({}, llm=None)
        bc, bs, hs, refs, rj, mp, rng = SB.load_bs(c.d["dir"])
        res = {}
        for n, pr in preds.items():
            st = bs.score_system(pr, refs["full"], mp, None)
            cats, rows = SB.categorize_all(bc, bs, hs, refs, rng, pr, st)
            res[n] = {"strict": bc.summ(st)["overall"], "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in"))["overall"],
                      "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out"))["overall"], "cats": cats, "rows": rows}
        base = res[BASE]["rows"]
        for n, r in res.items():
            if n == "文档解析原始结果":
                continue
            r["new_vs_base"] = SB.new_errors(bs, base, r["rows"])
            r["new_vs_b"] = SB.new_errors(bs, res["文档解析原始结果"]["rows"], r["rows"])
        allres[name] = {"res": res, "counts": counts, "logs": logs}
        wj(OUT / name / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
        wj(OUT / name / "pred_pages.json", preds)
    out = {n: {"blind": DS[n]["blind"], "res": {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in x["res"].items()},
               "counts": x["counts"], "logs": x["logs"]} for n, x in allres.items()}
    wj(OUT / "rows.json", out)
    for n, x in out.items():
        print("##", n, "（非盲）" if not x["blind"] else "")
        for k, r in x["res"].items():
            print(f"  {k}: in {fmt(r['tol_in'])} | out {fmt(r['tol_out'])} | strict {fmt(r['strict'])}"
                  + (f" | 新错 vs 底座 {summarize(r['new_vs_base'])['chars']} vs B {summarize(r['new_vs_b'])['chars']}" if "new_vs_base" in r else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["llm", "eval"])
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    {"llm": llm, "eval": evaluate}[a.cmd](a)
