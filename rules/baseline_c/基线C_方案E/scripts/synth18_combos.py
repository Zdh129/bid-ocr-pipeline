#!/usr/bin/env python3
"""synth18 本地穷举：后处理 × 补漏 × (可选) 6.2 结果 × (可选) LLM 表格纠错。全部复用已落盘结果，不联网。

开关（全部是通用规则，不看参照；image_fill 是本轮新增候选，见下）：
  star          ★/☆ 规则（frozen 原样）  on/off
  seal          剔除印章文字：off / full（frozen 原样）/ text（只用文字判据，不用位置判据）
  fill          A+Unlimited 补漏（frozen 原样）  on/off
  inv           发票按坐标重排（frozen 原样）  on/off
  img           image_fill（新增候选）：B 把整块判为 image 且无文字时，用 A（高精度含位置）落在该块框内的字按行拼回  on/off
  vlm           6.2 结果：off / v2nr（v2_no_posthoc_rules）/ v2（仅原始 synth18 有 6.2 结果）
  llm           只把表格段换成 LLM 纠错输出：none / gpt-4o / deepseek（仅原始 synth18；按页内表格段顺序对齐，段数不符的页不换）
计分：blind20_score 的 parse_ref/score_system，参照/manifest 指向 synth18，不套 blind20 修正。
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from baidu_ocr.postprocess import pred_pages, run_postprocess  # noqa: E402
from baidu_ocr.postprocess.aux import chars_in, load_aux  # noqa: E402
from baidu_ocr.postprocess.pipeline import merged_cfg  # noqa: E402

S18W = ROOT / "output" / "synth18" / "work"
OPT = ROOT / "output" / "synth18_opt"
FROZEN = ROOT / "output" / "blindset2_prep" / "frozen_config_v2.json"
LLM = ROOT / "output" / "synth18_llm"
G = {}


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def deep_update(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            deep_update(a[k], v)
        else:
            a[k] = v
    return a


# ------------------------------------------------------------------ 新增候选：image_fill
def image_fill(doc, aux, log):
    n = 0
    for b in doc["blocks"]:
        if b["type"] != "image" or (b.get("text") or "").strip():
            continue
        cs = chars_in(aux["pages"][b["page"]]["a_chars"], b["bbox"])
        if len(cs) < 2:
            continue
        hs = sorted(c["bbox"][3] - c["bbox"][1] for c in cs)
        tol = max(2.0, hs[len(hs) // 2] * 0.5)
        lines, cur, cy = [], [], None
        for c in sorted(cs, key=lambda c: (c["bbox"][1] + c["bbox"][3]) / 2):
            y = (c["bbox"][1] + c["bbox"][3]) / 2
            if cy is not None and abs(y - cy) > tol:
                lines.append(cur)
                cur = []
            cur.append(c)
            cy = sum((k["bbox"][1] + k["bbox"][3]) / 2 for k in cur) / len(cur)
        if cur:
            lines.append(cur)
        b["text"] = "\n".join("".join(k["ch"] for k in sorted(ln, key=lambda k: k["bbox"][0])) for ln in lines)
        log.append({"step": "image_fill", "block_id": b["id"], "page": b["page"], "chars": len(cs)})
        n += 1
    return n


# ------------------------------------------------------------------ LLM 表格段
def llm_tables(model):
    d = LLM / "results" / model
    chunks = {c["page"]: c for c in jl(LLM / "chunks.json")["chunks"]}
    out = {}
    for p, ch in chunks.items():
        f = d / f"p{p:02d}.json"
        if not f.exists():
            continue
        r = jl(f)["segments"]
        out[p] = [r.get(s["id"], s["text"]) for s in ch["segments"] if s["type"] == "table"]
    return out


def apply_llm(pr, tabs, star_on=False):
    """表格段换成 LLM 输出；若 star 开着，对换进来的文字同样套 ★/☆ 规则（保持「star 作用于最终表格文字」的流水线语义）。"""
    from baidu_ocr.postprocess.star import apply_text
    pr = copy.deepcopy(pr)
    for p, ts in tabs.items():
        secs = [s for s in pr[str(p)]["sections"] if s["section"] == "table"]
        if len(secs) != len(ts):
            continue
        for s, t in zip(secs, ts):
            s["text"] = apply_text(t)[0] if star_on else t
    return pr


# ------------------------------------------------------------------ 评估
def setup(b_doc, aux, vlm, has_llm):
    import bestconfig_run as bc
    import synth18_score as SS
    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    bs.REF, bs.MANIFEST = SS.S.S18 / "blind-ref.txt", SS.S.S18 / "manifest.json"
    bs.MANUAL.clear()
    full, nosign, rj, _ = SS.ref_texts()
    G.update(bs=bs, bc=bc, refs={"full": bs.parse_ref(full), "nosign": bs.parse_ref(nosign)},
             mpages={int(p["output_page"]): p for p in jl(SS.S.S18 / "manifest.json")["pages"]},
             b_doc=b_doc, aux=aux, vlm=vlm,
             pp=merged_cfg(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["postprocess"]),
             fz=jl(FROZEN), llm={m: llm_tables(m) for m in ("gpt-4o", "deepseek")} if has_llm else {})


def run_combo(c):
    pp = copy.deepcopy(G["pp"])
    pp["star"]["enabled"] = c["star"]
    pp["seal_strip"]["enabled"] = c["seal"] != "off"
    if c["seal"] == "text":
        pp["seal_strip"]["use_position"] = False
    pp["fill_gaps"]["enabled"] = c["fill"]
    pp["invoice_reorder"]["enabled"] = c["inv"]
    aux = dict(G["aux"])
    if c["vlm"] != "off":
        deep_update(pp, G["fz"]["variants"]["v2_no_posthoc_rules" if c["vlm"] == "v2nr" else "v2"]["postprocess_overrides"])
        aux["vlm"] = G["vlm"]
    else:
        pp["vlm_backfill"]["enabled"] = False
    doc = copy.deepcopy(G["b_doc"])
    log = []
    if c["img"]:
        image_fill(doc, aux, log)
    err = None
    try:
        doc, info = run_postprocess(doc, pp, aux)
    except ValueError as e:   # frozen invoice_reorder 在个别变体上 min() 空序列报错：不改 frozen 代码，本组合记为该步失败、去掉该步重跑
        if not pp["invoice_reorder"]["enabled"]:
            raise
        err = f"invoice_reorder 报错：{e}"
        pp["invoice_reorder"]["enabled"] = False
        doc, info = run_postprocess(doc, pp, aux)
    pr = pred_pages(doc)
    if c["llm"] != "none":
        pr = apply_llm(pr, G["llm"][c["llm"]], c["star"])
    bc, bs = G["bc"], G["bs"]
    res = {rk: bc.summ(bs.score_system(pr, G["refs"][rk], G["mpages"], None)) for rk in (("full", "nosign") if c.get("_nosign") else ("full",))}
    return {**c, "name": name(c), **{f"{rk}": v for rk, v in res.items()}, "counts": {**info["counts"], "image_fill": len(log)}, "error": err}


def name(c):
    parts = [f"star{'+' if c['star'] else '-'}", f"seal:{c['seal']}", f"fill{'+' if c['fill'] else '-'}", f"inv{'+' if c['inv'] else '-'}",
             f"img{'+' if c['img'] else '-'}"]
    if c["vlm"] != "off":
        parts.append(f"vlm:{c['vlm']}")
    if c["llm"] != "none":
        parts.append(f"llm:{c['llm']}")
    if c.get("_err"):
        parts.append("(inv 报错未生效)")
    return " ".join(parts)


def grid(vlm_opts, llm_opts):
    for star, seal, fill, inv, img, vlm, llm in itertools.product([False, True], ["off", "full", "text"], [False, True], [False, True],
                                                                    [False, True], vlm_opts, llm_opts):
        yield {"star": star, "seal": seal, "fill": fill, "inv": inv, "img": img, "vlm": vlm, "llm": llm}


def evaluate(b_doc, aux, vlm, has_llm, vlm_opts, llm_opts, procs=7):
    setup(b_doc, aux, vlm, has_llm)
    combos = list(grid(vlm_opts, llm_opts))
    with Pool(procs) as pool:
        rows = pool.map(run_combo, combos, chunksize=4)
    rows.sort(key=lambda r: (r["full"]["overall"]["cer"], -r["full"]["overall"]["readout"]))
    # 前 15 名补剔除签名口径
    top = [run_combo({**{k: r[k] for k in ("star", "seal", "fill", "inv", "img", "vlm", "llm")}, "_nosign": True}) for r in rows[:15]]
    for r, t in zip(rows[:15], top):
        r["nosign"] = t["nosign"]
    return rows


def table_md(rows, title, baidu_calls, cost, refs=None, n=20):
    def f(r, rk="full", s="overall"):
        x = r[rk][s]
        return f"{x['readout']:.2f}% | {x['cer']:.2f}% | {x['D']}/{x['S']}/{x['I']}"
    L = [f"### {title}", "", "| # | 组合 | 读出率 | CER | D/S/I | 剔除签名 读出/CER | 百度调用 | 费用 |", "|---|---|---|---|---|---|---|---|"]
    show = rows[:n] + [r for r in (refs or []) if r not in rows[:n]]
    for i, r in enumerate(show):
        ns = r.get("nosign", {}).get("overall")
        L.append(f"| {rows.index(r) + 1 if r in rows else '-'} | {r['name']} | {f(r)} | " + (f"{ns['readout']:.2f}% / {ns['cer']:.2f}%" if ns else "") + f" | {baidu_calls} | {cost} |")
    return L


def batch1(a):
    b_doc = jl(S18W / "output" / "final" / "scheme_b_standard" / "blind20-scan.json")
    aux = load_aux(S18W, b_doc)
    man = jl(ROOT / "output" / "synth18" / "vlm" / "manifest.json")["items"]
    res = {}
    for it in man:
        rp = ROOT / "output" / "synth18" / "vlm" / "vlm" / "results" / f"{it['id']}.json"
        if rp.exists():
            res[it["id"]] = jl(rp)
    rows = evaluate(b_doc, aux, {"items": man, "results": res}, True, ["off", "v2nr", "v2"], ["none", "gpt-4o", "deepseek"])
    OPT.mkdir(parents=True, exist_ok=True)
    (OPT / "batch1_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(rows))
    for r in rows[:15]:
        print(r["name"], r["full"]["overall"], r.get("nosign", {}).get("overall"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["batch1"])
    a = ap.parse_args()
    globals()[a.cmd](a)
