#!/usr/bin/env python3
"""synth18 优化第二轮（opt2）：围绕 G2 在 synth18（开发集）上组合优化。不碰 synth18b / synth18c。

  prep                 P2 的 Unlimited 原始返回改名到 load_aux 认的位置（不联网）
  quota                本地日志推算百度免费余量
  batch3               第 1–2 项：来源（B/A/Unlimited 各自原图或 P2）× 后处理开关（含 invoice_reorder_v2）本地穷举，三套口径
  llm [--live]         对 batch3 入围底座的「后处理后表格段」做 LLM 表格纠错（gpt-4o / deepseek，prompt_v1 sha 校验，最坏情况校验，≤ 上限）
  batch3llm            入围底座 × LLM 计分，含 LLM 新引入错误
后处理流水线（opt2 版，冻结代码不改）：image_fill → star → seal_strip → fill_gaps → invoice_reorder_v2 → vlm_backfill
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
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from baidu_ocr.postprocess import pred_pages  # noqa: E402
from baidu_ocr.postprocess import fill_gaps, invoice_reorder, invoice_reorder_v2, reread, seal_strip, star, star_fill, vlm_backfill  # noqa: E402
from baidu_ocr.postprocess.aux import load_aux  # noqa: E402
from baidu_ocr.postprocess.pipeline import merged_cfg  # noqa: E402

S18 = Path("/workspace/pdf-test/synth18")
S18W = ROOT / "output" / "synth18" / "work"
OPT = ROOT / "output" / "synth18_opt"
OPT2 = ROOT / "output" / "synth18_opt2"
P2W = OPT2 / "P2" / "work"
B_DOCS = {"orig": S18W / "output" / "final" / "scheme_b_standard" / "blind20-scan.json",
          "P2": OPT / "pre" / "P2" / "work" / "output" / "final" / "scheme_b_standard" / "blind20-scan.json"}
A_DIRS = {"orig": S18W / "output" / "scheme_a", "P2": P2W / "output" / "scheme_a"}
U_RAW = {"orig": S18W / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
         "P2": P2W / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json"}
FROZEN_V2 = ROOT / "output" / "blindset2_prep" / "frozen_config_v2.json"
PROMPT = ROOT / "output" / "synth18_llm" / "prompt_v1.txt"
PROMPT_SHA = "86ae3d1c77ba3276cc209077ec04166ae878031a09fc285057c5b345430d231a"
LLM_CFG = ROOT / "config_llm_synth18opt2_live.yaml"
LLMD = OPT2 / "llm"
LLM_CAP = 2.0
# 免费额度：截至 synth18b 结束（本地日志推算）
LEFT0 = {"docparser_pages": 54, "accurate_calls": 435, "general_calls": 977}
G = {}


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def prep(a=None):
    d = P2W / "output" / "scheme_b_unlimited"
    src = next(p for p in (d / "raw").glob("*.parse_result.json") if not p.name.startswith("blind20"))
    shutil.copyfile(src, d / "raw" / "blind20-scan.parse_result.json")
    print("ok", src.name)


def opt2_costs():
    out = {}
    for f in sorted(OPT2.glob("**/cost.json")):
        c = jl(f)
        for api, v in (c.get("by_api") or {}).items():
            k = out.setdefault(api, {"calls": 0, "success": 0, "cny": 0.0, "units": 0})
            k["calls"] += v.get("calls", 0)
            k["success"] += v.get("success", 0)
            k["cny"] = round(k["cny"] + v.get("cny", 0), 4)
        for x in c.get("calls") or []:
            if x.get("success") and not x.get("dry_run"):
                out[x["api"]]["units"] = out[x["api"]].get("units", 0) + int(x.get("units") or 0)
    return out


def quota(a=None):
    c = opt2_costs()
    left = dict(LEFT0)
    left["docparser_pages"] -= sum(v["units"] for k, v in c.items() if k.startswith("docparser_standard"))
    left["accurate_calls"] -= c.get("accurate", {}).get("success", 0)
    left["general_calls"] -= c.get("general", {}).get("success", 0)
    if a is not None:
        print(json.dumps({"opt2_costs": c, "left": left}, ensure_ascii=False))
    return c, left


# ------------------------------------------------------------------ 流水线
def base_pp():
    return merged_cfg(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["postprocess"])


def run_pipeline(c, doc, aux, vlm=None):
    """c: star/seal/fill/inv('off'|'v1'|'v2')/img/vlm('off'|'v2')。返回 (doc, counts)。"""
    import synth18_combos as C
    pp = base_pp()
    if c.get("vlm", "off") != "off":
        C.deep_update(pp, jl(FROZEN_V2)["variants"]["v2"]["postprocess_overrides"])
    doc = copy.deepcopy(doc)
    log, counts = [], {}
    if c["img"]:
        counts["image_fill"] = C.image_fill(doc, aux, log)
    if c["star"]:
        counts["star"] = star.apply(doc, pp["star"], log)
    if c["seal"] != "off":
        sc = dict(pp["seal_strip"])
        if c["seal"] == "text":
            sc["use_position"] = False
        counts["seal_strip"] = seal_strip.apply(doc, sc, log)
    if c["fill"]:
        counts["fill_gaps"] = fill_gaps.apply(doc, pp["fill_gaps"], log, aux)
    if c["inv"] == "v2":
        counts["invoice_reorder_v2"] = invoice_reorder_v2.apply(doc, {}, log, aux)
    elif c["inv"] == "v1":
        counts["invoice_reorder"] = invoice_reorder.apply(doc, {}, log, aux)
    if c.get("reread"):
        counts["reread"] = reread.apply(doc, G["reread"][c["A"]], log)
    if c.get("vlm", "off") != "off":
        a2 = dict(aux)
        a2["vlm"] = vlm
        counts["vlm_backfill"] = vlm_backfill.apply(doc, pp["vlm_backfill"], log, a2)
    return doc, counts


def src_name(s):
    return f"B:{s['B']} A:{s['A']} U:{s['U']}"


def cname(c):
    s = src_name(c) + f" | star{'+' if c['star'] else '-'} seal:{c['seal']} fill{'+' if c['fill'] else '-'} inv:{c['inv']} img{'+' if c['img'] else '-'}"
    if c.get("vlm", "off") != "off":
        s += " vlm:v2"
    if c.get("starmode", "r1") != "r1":
        s += f" starmode:{c['starmode']}"
    if c.get("reread"):
        s += " reread+"
    if c.get("llm", "none") != "none":
        s += f" llm:{c['llm']}"
    return s


# ------------------------------------------------------------------ 计分
def setup_scoring():
    import synth18b_score as SB
    import tolerant_score as T
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(S18)
    G.update(SB=SB, T=T, bc=bc, bs=bs, hs=hs, refs=refs, rj=rj, mp=mp, rng=rng)


def score_pr(pr, want_rows=True):
    bc, bs, T = G["bc"], G["bs"], G["T"]
    st = bs.score_system(pr, G["refs"]["full"], G["mp"], None)
    out = {"strict": bc.summ(st)["overall"],
           "tol_in": T.summ(T.score_tolerant(bs, pr, G["rj"], G["mp"], "in"))["overall"],
           "tol_out": T.summ(T.score_tolerant(bs, pr, G["rj"], G["mp"], "out"))["overall"]}
    if want_rows:
        cats, rows = G["SB"].categorize_all(bc, bs, G["hs"], G["refs"], G["rng"], pr, st)
        out["cats"], out["rows"] = cats, rows
    return out


def ov(x):
    return x["overall"] if isinstance(x, dict) and "overall" in x else x


def load_vlm():
    man = jl(ROOT / "output" / "synth18" / "vlm" / "manifest.json")["items"]
    res = {}
    for it in man:
        rp = ROOT / "output" / "synth18" / "vlm" / "vlm" / "results" / f"{it['id']}.json"
        if rp.exists():
            res[it["id"]] = jl(rp)
    return {"items": man, "results": res}


def aux_for(s, docs):
    key = (s["B"], s["A"], s["U"])
    if key not in G.setdefault("aux", {}):
        G["aux"][key] = load_aux(S18W, docs[s["B"]], a_dir=A_DIRS[s["A"]], u_raw_path=U_RAW[s["U"]])
    return G["aux"][key]


def eval_one(c):
    doc = G["docs"][c["B"]]
    aux = aux_for(c, G["docs"])
    err = None
    try:
        d, counts = run_pipeline(c, doc, aux, G.get("vlm"))
    except Exception as e:  # noqa: BLE001  （冻结版 v1 在个别底座上会崩，记下）
        return {**c, "name": cname(c), "error": f"{type(e).__name__}: {e}"}
    pr = pred_pages(d)
    if c.get("llm", "none") != "none":
        pr = apply_llm_tabs(pr, G["tabs"][(base_key(c), c["llm"])], c["star"])
    if c.get("starmode", "r1") == "fill":
        counts["star_fill"] = star_fill.apply_pred(pr, G["marks"][c["A"]])
    sc = score_pr(pr)
    sc["new_vs_braw"] = summarize_new(G["SB"].new_errors(G["bs"], G["braw_rows"], sc["rows"]))
    if c.get("llm", "none") != "none" and base_key(c) in G.get("base_rows", {}):
        sc["llm_new"] = G["SB"].new_errors(G["bs"], G["base_rows"][base_key(c)], sc["rows"])
    sc.pop("rows")
    return {**c, "name": cname(c), "counts": counts, **sc, "error": err}


def summarize_new(lst):
    k = Counter()
    for r in lst:
        k[r["kind"]] += r["cost"]
    return {"n": len(lst), "chars": sum(r["cost"] for r in lst), "by_kind": dict(k)}


def base_key(c):
    return json.dumps({**{k: c[k] for k in ("B", "A", "U", "star", "seal", "fill", "inv", "img", "vlm")},
                       "starmode": c.get("starmode", "r1"), "reread": bool(c.get("reread"))}, sort_keys=True)


def grid3():
    srcs = [{"B": b, "A": a_, "U": u} for b in ("orig", "P2") for a_ in ("orig", "P2") for u in ("orig", "P2")
            if A_DIRS[a_].joinpath("call_summary.json").exists() and U_RAW[u].exists()]
    for s in srcs:
        for seal in ("off", "text"):
            for fill in (False, True):
                for inv in ("off", "v1", "v2"):
                    for vlm in (("off", "v2") if s["B"] == "orig" else ("off",)):
                        yield {**s, "star": True, "seal": seal, "fill": fill, "inv": inv, "img": True, "vlm": vlm, "llm": "none"}


def init_eval(with_vlm=True):
    setup_scoring()
    G["docs"] = {k: jl(v) for k, v in B_DOCS.items()}
    if with_vlm:
        G["vlm"] = load_vlm()
    braw = pred_pages(G["docs"]["orig"])
    G["braw_pr"] = braw
    G["braw"] = score_pr(braw)
    G["braw_rows"] = G["braw"]["rows"]
    G["aux"] = {}
    G["marks"] = {k: star_fill.load_marks(v) for k, v in A_DIRS.items()}
    G["reread"] = {k: jl(OPT2 / "reread" / f"A_{k}" / "results.json")["units"] for k in A_DIRS if (OPT2 / "reread" / f"A_{k}" / "results.json").exists()}
    for b in ("orig", "P2"):
        for a_ in ("orig", "P2"):
            for u in ("orig", "P2"):
                if A_DIRS[a_].joinpath("call_summary.json").exists() and U_RAW[u].exists():
                    aux_for({"B": b, "A": a_, "U": u}, G["docs"])


def batch3(a):
    init_eval()
    combos = list(grid3())
    refs = [{"B": "orig", "A": "orig", "U": "orig", "star": True, "seal": "off", "fill": True, "inv": "off", "img": True, "vlm": "off", "llm": "none", "_label": "旧 G2 去掉 LLM（对照）"},
            {"B": "P2", "A": "orig", "U": "orig", "star": True, "seal": "off", "fill": False, "inv": "off", "img": True, "vlm": "off", "llm": "none", "_label": "旧 G1（对照）"}]
    t0 = time.time()
    with Pool(7) as pool:
        rows = pool.map(eval_one, combos, chunksize=2)
    ok = [r for r in rows if not r.get("error")]
    bad = [r for r in rows if r.get("error")]
    ok.sort(key=lambda r: (r["tol_in"]["cer"], -r["tol_in"]["readout"]))
    p2b = score_pr(pred_pages(G["docs"]["P2"]))
    res = {"rows": ok, "errors": [{"name": r["name"], "error": r["error"]} for r in bad],
           "braw": {k: v for k, v in G["braw"].items() if k != "rows"}, "braw_p2": {k: v for k, v in p2b.items() if k != "rows"},
           "refs": [r["_label"] for r in refs], "sec": round(time.time() - t0, 1), "quota": quota()}
    wj(OPT2 / "batch3_rows.json", res)
    print(len(ok), "ok", len(bad), "errors", res["sec"], "s")
    for r in ok[:12]:
        print(r["name"], f"{r['tol_in']['readout']:.2f}/{r['tol_in']['cer']:.2f}", f"strict {r['strict']['readout']:.2f}/{r['strict']['cer']:.2f}")


# ------------------------------------------------------------------ LLM 表格纠错（新 B 文本 → 重新调用）
def tables_of(pr):
    out = []
    for p in sorted(pr, key=int):
        segs = [{"id": f"p{int(p):02d}-s{i + 1:02d}", "type": "table", "text": s["text"]} for i, s in enumerate(pr[p]["sections"]) if s["section"] == "table"]
        if segs:
            out.append({"page": int(p), "segments": segs})
    return out


def apply_llm_tabs(pr, tabs, star_on):
    import synth18_combos as C
    return C.apply_llm(pr, tabs, star_on)


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


FINALISTS = OPT2 / "llm_finalists.json"


def finalists():
    return jl(FINALISTS)["bases"]


def grid4():
    for a_ in ("orig", "P2"):
        for u in ("orig", "P2"):
            for sm in ("r1", "fill"):
                for rr in (False, True):
                    for fill in (False, True):
                        for vlm in ("off", "v2"):
                            yield {"B": "orig", "A": a_, "U": u, "star": True, "seal": "off", "fill": fill, "inv": "v2", "img": True,
                                   "vlm": vlm, "llm": "none", "starmode": sm, "reread": rr}
    for a_ in ("orig", "P2"):
        for sm in ("r1", "fill"):
            for rr in (False, True):
                yield {"B": "P2", "A": a_, "U": "orig", "star": True, "seal": "off", "fill": False, "inv": "v2", "img": True,
                       "vlm": "off", "llm": "none", "starmode": sm, "reread": rr}


def batch4(a):
    init_eval()
    t0 = time.time()
    with Pool(7) as pool:
        rows = pool.map(eval_one, list(grid4()), chunksize=2)
    ok = sorted([r for r in rows if not r.get("error")], key=lambda r: (r["tol_in"]["cer"], -r["tol_in"]["readout"]))
    wj(OPT2 / "batch4_rows.json", {"rows": ok, "errors": [r for r in rows if r.get("error")], "sec": round(time.time() - t0, 1)})
    print(len(ok), "ok", round(time.time() - t0), "s")
    for r in ok[:12]:
        print(r["name"], f"{r['tol_in']['readout']:.2f}/{r['tol_in']['cer']:.2f}", f"strict {r['strict']['readout']:.2f}/{r['strict']['cer']:.2f}", r["counts"])


def chunk_key(model, ch):
    return hashlib.sha256((model + json.dumps(ch, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:20]


def llm(a):
    import requests
    cfg = yaml.safe_load(LLM_CFG.read_text(encoding="utf-8"))["llm"]
    assert sha(PROMPT) == PROMPT_SHA, "prompt_v1 sha 不符"
    sysp = PROMPT.read_text(encoding="utf-8")
    led_p = LLMD / "ledger.jsonl"
    spent = sum(float(json.loads(l).get("usd") or 0) for l in led_p.read_text().splitlines() if l.strip()) if led_p.exists() else 0.0
    if a.live:
        assert cfg["allow_external"] is True and cfg.get("approval")
    G["docs"] = {k: jl(v) for k, v in B_DOCS.items()}
    vlm = load_vlm()
    G["reread"] = {k: jl(OPT2 / "reread" / f"A_{k}" / "results.json")["units"] for k in A_DIRS if (OPT2 / "reread" / f"A_{k}" / "results.json").exists()}
    plan, seen = [], set()
    for c in finalists():
        d, _ = run_pipeline(c, G["docs"][c["B"]], aux_for(c, G["docs"]), vlm)
        for m in a.models.split(","):
            mc = cfg["models"][m]
            tok = oa_tokens if mc["provider"] == "openai" else ds_tokens
            for ch in tables_of(pred_pages(d)):
                k = chunk_key(m, ch)
                if k in seen:
                    continue
                seen.add(k)
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
           "worst_usd": round(sum(x["worst"] for x in todo), 4), "spent_before": round(spent, 4), "cap_usd": LLM_CAP,
           "by_model": {m: sum(1 for x in todo if x["model"] == m) for m in a.models.split(",")}}
    wj(LLMD / "estimate.json", est)
    print(json.dumps(est, ensure_ascii=False))
    if est["worst_usd"] + spent > LLM_CAP:
        sys.exit("最坏情况超过上限，停止")
    if not a.live:
        return
    for x in todo:
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
            pr_ = price(mc, when)
            usd = round(((pt - cached) * pr_["input"] + cached * pr_["cached_input"] + ct * pr_["output"]) / 1e6, 6)
            txt = j["choices"][0]["message"].get("content") or ""
            try:
                o = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip()))
                segs = {s["id"]: s["text"] for s in o["segments"] if isinstance(s, dict) and isinstance(s.get("text"), str)}
                ok = True
            except Exception:  # noqa: BLE001
                segs, ok = {}, False
            rec.update({"prompt_tokens": pt, "completion_tokens": ct, "cached_tokens": cached, "usd": usd, "served_model": j.get("model"),
                        "finish": j["choices"][0].get("finish_reason"), "parse_ok": ok,
                        "missing_ids": [s["id"] for s in x["chunk"]["segments"] if s["id"] not in segs]})
            spent += usd
            with led_p.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            wj(LLMD / "cache" / f"{x['key']}.json", {"model": x["model"], "page": x["chunk"]["page"], "segments": segs, "raw": txt,
                                                     "draft": {s["id"]: s["text"] for s in x["chunk"]["segments"]}, "usage": u, "usd": usd})
            print(x["model"], f"p{x['chunk']['page']:02d}", pt, ct, f"${usd:.5f}", f"累计 ${spent:.4f}", flush=True)
            break
    print(f"LLM 累计 ${spent:.4f}")


def llm_tabs_for(c, model, pr):
    out = {}
    for ch in tables_of(pr):
        f = LLMD / "cache" / f"{chunk_key(model, ch)}.json"
        if not f.exists():
            return None
        r = jl(f)
        out[ch["page"]] = [r["segments"].get(s["id"], s["text"]) for s in ch["segments"]]
    return out


def batch3llm(a):
    init_eval()
    G["base_rows"], G["tabs"] = {}, {}
    combos = []
    for c in finalists():
        c = {**c, "llm": "none"}
        d, _ = run_pipeline(c, G["docs"][c["B"]], aux_for(c, G["docs"]), G["vlm"])
        pr = pred_pages(d)
        G["base_rows"][base_key(c)] = score_pr(pr)["rows"]
        for m in a.models.split(","):
            t = llm_tabs_for(c, m, pr)
            if t is None:
                print("缺 LLM 结果，跳过", cname(c), m)
                continue
            G["tabs"][(base_key(c), m)] = t
            combos.append({**c, "llm": m})
    with Pool(7) as pool:
        rows = pool.map(eval_one, combos, chunksize=1)
    rows.sort(key=lambda r: (r["tol_in"]["cer"], -r["tol_in"]["readout"]))
    wj(OPT2 / a.out_name, {"rows": rows})
    for r in rows:
        print(r["name"], f"{r['tol_in']['readout']:.2f}/{r['tol_in']['cer']:.2f}", "LLM新错", len(r.get("llm_new", [])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "quota", "batch3", "llm", "batch3llm", "batch4"])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--models", default="gpt-4o,deepseek")
    ap.add_argument("--finalists", default="")
    ap.add_argument("--out-name", default="batch3llm_rows.json")
    a = ap.parse_args()
    if a.finalists:
        FINALISTS = Path(a.finalists)
    globals()[a.cmd](a)
