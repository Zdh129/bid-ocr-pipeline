#!/usr/bin/env python3
"""synth18b 一次性计分（由 synth18b_run.py score 调用）。开头独占创建 output/synth18b/SCORED，已存在则拒绝。"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import synth18b_run as R  # noqa: E402

BUCKETS = ["星号", "竖排", "顺序", "标点", "签名", "真错字", "印章混入", "漏读和杂字"]


def is_punct(ch):
    return ch in set("~～-—–·•|") or unicodedata.category(ch).startswith(("P", "Z")) or ch.isspace()


def digits(s):
    return re.findall(r"\d+", unicodedata.normalize("NFKC", s))


def load_bs(sdir: Path):
    import bestconfig_run as bc
    import synth18_run as S
    import synth18_score as SS
    S.S18 = sdir
    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    bs.REF, bs.MANIFEST = sdir / "blind-ref.txt", sdir / "manifest.json"
    bs.MANUAL.clear()
    full, nosign, rj, _ = SS.ref_texts()
    refs = {"full": bs.parse_ref(full), "nosign": bs.parse_ref(nosign)}
    mp = {int(p["output_page"]): p for p in json.loads((sdir / "manifest.json").read_text(encoding="utf-8"))["pages"]}
    rng = SS.special_ranges(bs, refs["full"], rj)
    hs = bc.load_mod("hybrid_score", ROOT / "scripts" / "hybrid_score.py")
    hs.load_bs = lambda: bs
    bc.SIGN = ()
    return bc, bs, hs, refs, rj, mp, rng


def categorize_all(bc, bs, hs, refs, rng, pr, sc):
    tot, rows = Counter(), []
    spans = sorted(sc["error_spans"], key=lambda s: (s["page"], s["ref_start"]))
    for i, sp in enumerate(spans):
        p = sp["page"]
        rs, _ = hs.ref_page_string(bs, refs["full"], p, None)
        ps = bs.norm("".join(s["text"] for s in pr[str(p)]["sections"]))
        seal_chars = set(bs.norm(pr[str(p)].get("seal") or "")) | set(bs.norm("".join(refs["full"][p]["seals"])))
        same = [o for j, o in enumerate(spans) if o["page"] == p and j != i]
        c, why = bc.categorize(sp, ps, rs, seal_chars, set(), "".join(o["ref"] for o in same), "".join(o["pred"] for o in same))
        a, b = sp["ref_start"], sp["ref_end"]
        sk = next((k for (x0, x1, k) in rng.get(p, []) if (a < x1 and b > x0) or (a == b and x0 <= a <= x1)), None)
        if c == "星号":
            bk = "星号"
        elif sk == "签名":
            bk = "签名"
        elif sk == "竖排" or why == "发票竖排小字漏读":
            bk = "竖排"
        elif c in ("顺序", "标点", "真错字", "印章混入"):
            bk = c
        else:
            bk = "漏读和杂字"
        tot[bk] += sp["cost"]
        rows.append({**{k: sp[k] for k in ("page", "section", "ref", "pred", "cost", "context", "ref_start", "ref_end")}, "category": bk, "detail": why})
    return dict(tot), rows


def new_errors(bs, base_rows, rows):
    bad, ins_at = defaultdict(set), defaultdict(set)
    for r in base_rows:
        bad[r["page"]].update(range(r["ref_start"], r["ref_end"]))
        if r["ref_start"] == r["ref_end"]:
            ins_at[r["page"]].add(r["ref_start"])
    out = []
    for r in rows:
        p, a, b = r["page"], r["ref_start"], r["ref_end"]
        if not ((a == b and a not in ins_at[p] and a not in bad[p]) or (b > a and any(x not in bad[p] for x in range(a, b)))):
            continue
        rp, pp = bs.norm(r["ref"]), bs.norm(r["pred"])
        if rp and not pp:
            k = "删除"
        elif pp and not rp and any(not is_punct(c) for c in pp):
            k = "编造/插入"
        elif digits(rp) != digits(pp) and re.search(r"\d", rp + pp):
            k = "改数字/金额/日期"
        elif all(is_punct(c) for c in rp + pp):
            k = "标点改错"
        else:
            k = "改错字/替换"
        out.append({**{x: r[x] for x in ("page", "ref", "pred", "cost", "category")}, "kind": k})
    return out


def draft_checks(model, pre):
    out = []
    d = R.LLMD / "results" / f"{model}_{pre}"
    for f in sorted(d.glob("p*.json")):
        r = R.jl(f)
        for i, a in r["draft"].items():
            b = r["segments"].get(i)
            if b is None:
                out.append({"id": i, "missing": True})
                continue
            ca, cb = Counter(c for c in a if not is_punct(c)), Counter(c for c in b if not is_punct(c))
            da = sorted("".join(re.findall(r"\d", unicodedata.normalize("NFKC", a))))
            db = sorted("".join(re.findall(r"\d", unicodedata.normalize("NFKC", b))))
            lost, new = "".join(c * n for c, n in (ca - cb).items()), "".join(c * n for c, n in (cb - ca).items())
            if lost or new or da != db:
                out.append({"id": i, "digit_multiset_changed": da != db, "lost": lost, "added": new})
    return out


def fmt(x):
    return f"{x['readout']:.2f}% | {x['cer']:.2f}% | {x['D']}/{x['S']}/{x['I']}"


def main():
    import tolerant_score as T
    fz = R.verify()
    with open(R.OUT / "SCORED", "x") as f:   # 只跑一次
        f.write(R.now())
    started = R.now()
    # ---------- synth18b 预测（计分前构建，不看参照）
    docs = {"orig": R.b_doc("orig"), "P2": R.b_doc("P2")}
    aux = {k: R.aux_for(v) for k, v in docs.items()}
    tabs = {(m, pre): R.llm_tabs(m, pre) for m, pre in R.JOBS}
    from baidu_ocr.postprocess import pred_pages
    preds, counts = {}, {}
    preds["B 原始（原图）"] = pred_pages(docs["orig"])
    preds["B 原始（P2）"] = pred_pages(docs["P2"])
    allg = {**R.GROUPS, "G2 去掉 LLM（诊断基线，用于统计 LLM 新引入错误）": R.REFERENCE_ROWS["G2 去掉 LLM（诊断基线，用于统计 LLM 新引入错误）"]}
    for name, g in allg.items():
        pre = g["preproc"]
        preds[name], counts[name], _ = R.run_group(g, docs[pre], aux[pre], tabs.get((g["llm"], pre), {}))
    R.wj(R.OUT / "pred_pages.json", preds)
    # ---------- 现在才打开 synth18b 参照
    ref_sha = {n: R.sha(R.S18B / n) for n in ("blind-ref.txt", "blind-ref.json", "manifest.json")}
    bc, bs, hs, refs, rj, mp, rng = load_bs(R.S18B)
    res = {}
    for name, pr in preds.items():
        st = bs.score_system(pr, refs["full"], mp, None)
        cats, rows = categorize_all(bc, bs, hs, refs, rng, pr, st)
        res[name] = {"strict": bc.summ(st), "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in")), "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out")),
                     "cats": cats, "rows": rows, "per_page_strict": {p: v["overall"] for p, v in st["per_page"].items()}}
    tol_detail = T.score_tolerant(bs, preds["G2"], rj, mp, "in")
    newerr = {"G1+gpt-4o（相对 G1）": new_errors(bs, res["G1"]["rows"], res["G1+gpt-4o"]["rows"]),
              "G2（gpt-4o，相对 G2 去掉 LLM）": new_errors(bs, res["G2 去掉 LLM（诊断基线，用于统计 LLM 新引入错误）"]["rows"], res["G2"]["rows"]),
              "G2-deepseek（相对 G2 去掉 LLM）": new_errors(bs, res["G2 去掉 LLM（诊断基线，用于统计 LLM 新引入错误）"]["rows"], res["G2-deepseek"]["rows"])}
    dchk = {f"{m}/{pre}": draft_checks(m, pre) for m, pre in R.JOBS}
    # ---------- synth18（开发集）同一容忍脚本重算
    import synth18_combos as C
    import synth18_opt_phase2 as P2
    from baidu_ocr.postprocess.aux import load_aux
    bc8, bs8, hs8, refs8, rj8, mp8, rng8 = load_bs(Path("/workspace/pdf-test/synth18"))
    d_orig = R.jl(C.S18W / "output" / "final" / "scheme_b_standard" / "blind20-scan.json")
    d_p2 = R.jl(P2.wdir("P2") / "final" / "scheme_b_standard" / "blind20-scan.json")
    a_orig, a_p2 = load_aux(C.S18W, d_orig), load_aux(C.S18W, d_p2)
    t8 = {m: C.llm_tables(m) for m in ("gpt-4o", "deepseek")}
    p8 = {"B 原始（原图）": pred_pages(d_orig), "B 原始（P2）": pred_pages(d_p2)}
    for name in ("G1", "G2", "G2-deepseek"):
        g = R.GROUPS[name]
        d, a = (d_p2, a_p2) if g["preproc"] == "P2" else (d_orig, a_orig)
        p8[name] = R.run_group(g, d, a, t8.get(g["llm"], {}))[0]
    res8 = {}
    for name, pr in p8.items():
        res8[name] = {"strict": bc8.summ(bs8.score_system(pr, refs8["full"], mp8, None)), "tol_in": T.summ(T.score_tolerant(bs8, pr, rj8, mp8, "in")),
                      "tol_out": T.summ(T.score_tolerant(bs8, pr, rj8, mp8, "out"))}
    # ---------- 费用
    led = [json.loads(l) for l in (R.LLMD / "ledger.jsonl").read_text().splitlines() if l.strip()] if (R.LLMD / "ledger.jsonl").exists() else []
    baidu_costs = {}
    for f in sorted(R.OUT.glob("**/cost.json")):
        c = R.jl(f)
        baidu_costs[str(f.relative_to(R.OUT))] = {k: {"calls": v.get("calls"), "success": v.get("success"), "cny": v.get("cny"), "sec": v.get("elapsed_sec")} for k, v in (c.get("by_api") or {}).items()}
    out = {"started": started, "ref_sha256": ref_sha, "frozen_sha256": R.sha(R.FROZEN), "results": {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in res.items()},
           "new_errors": newerr, "draft_checks": dchk, "synth18_rescore": res8, "llm_ledger": led, "baidu_costs": baidu_costs, "quota_left": R.left(),
           "counts": counts, "tolerant_units_G2": tol_detail["units"], "tolerant_seal_moves_G2": tol_detail["seal_moves"]}
    R.wj(R.OUT / "scores.json", out)
    R.wj(R.OUT / "diff_vs_ref.json", {k: v["rows"] for k, v in res.items()})
    # ---------- 报告
    L = ["# synth18b 留出集一次性验证（G1 / G2）", "", f"计分时间 {started}（UTC = 伦敦时间）。冻结文件 `output/synth18b/frozen_synth18b.json` sha256 `{out['frozen_sha256']}`。",
         "参照 sha256：" + "；".join(f"{k} `{v}`" for k, v in ref_sha.items()) + "（父 agent 告知的参照 sha 前缀 5f73e9c1）。", "",
         "## 1. synth18b 结果（主结论 = 约定容忍版 + 签名入参照）", "",
         "| 系统 | 容忍·签名入 读出 | CER | D/S/I | 容忍·剔除签名 读出 | CER | D/S/I | 严格版 读出 | CER | D/S/I |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in res.items():
        L.append(f"| {k} | {fmt(v['tol_in']['overall'])} | {fmt(v['tol_out']['overall'])} | {fmt(v['strict']['overall'])} |")
    L += ["", "达标线：读出率 ≥ 99%、CER < 1%。", "", "## 2. 错误分类（严格版、签名入参照，按编辑代价计字）", "", "| 类别 | " + " | ".join(res) + " |", "|---|" + "---|" * len(res)]
    for b in BUCKETS:
        L.append(f"| {b} | " + " | ".join(str(v["cats"].get(b, 0)) for v in res.values()) + " |")
    L += ["", "## 3. LLM 新引入的错误（对参照：基线读对、LLM 后改错的位置）", ""]
    for k, v in newerr.items():
        c = Counter(r["kind"] for r in v)
        L.append(f"- {k}：{len(v)} 处 / {sum(r['cost'] for r in v)} 字；" + "，".join(f"{kk} {n}" for kk, n in c.items()))
        for r in v[:30]:
            L.append(f"  - p{r['page']} {r['kind']}：`{r['ref'][:40] or '∅'}` → `{r['pred'][:40] or '∅'}`（{r['cost']} 字）")
    L += ["", "对草稿（不看参照）：" + "；".join(f"{k} 标记 {len(v)} 段（数字多重集变化 {sum(1 for r in v if r.get('digit_multiset_changed'))}，删字段 {sum(1 for r in v if r.get('lost'))}，增字段 {sum(1 for r in v if r.get('added'))}，缺 id {sum(1 for r in v if r.get('missing'))}）" for k, v in dchk.items()), "",
          "## 4. 同一容忍脚本重算 synth18（开发集）", "", "| 系统 | 容忍·签名入 | 容忍·剔除签名 | 严格版 |", "|---|---|---|---|"]
    for k, v in res8.items():
        L.append(f"| {k} | {fmt(v['tol_in']['overall'])} | {fmt(v['tol_out']['overall'])} | {fmt(v['strict']['overall'])} |")
    L += ["", "（synth18 上没有 G1+gpt-4o：P2 版表格未送 LLM。）", "", "## 5. 调用与费用", ""]
    for k, v in baidu_costs.items():
        L.append(f"- 百度 {k}：{v}")
    L.append(f"- 免费余量（本地日志推算）：{out['quota_left']}")
    by = defaultdict(lambda: [0, 0, 0, 0.0, 0])
    for r in led:
        x = by[f"{r['model']}/{r['pre']}"]
        x[0] += 1 if r.get("http") == 200 else 0
        x[1] += r.get("prompt_tokens") or 0
        x[2] += r.get("completion_tokens") or 0
        x[3] += r.get("usd") or 0
        x[4] += 0 if r.get("http") == 200 else 1
    for k, x in by.items():
        L.append(f"- {k}：成功 {x[0]} 次（失败尝试 {x[4]}），输入 {x[1]} / 输出 {x[2]} tokens，${x[3]:.4f}")
    L.append(f"- OpenAI+DeepSeek 本轮合计 ${sum(r.get('usd') or 0 for r in led):.4f}（上限 $2）")
    (R.OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
