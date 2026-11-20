#!/usr/bin/env python3
"""synth18c 一次性计分（scripts/synth18c_run.py score 调用）。
顺序：校验冻结 → 构建全部预测（不看参照）→ 核对参照 sha（不符则停，不计分）→ 写 SCORED 标记 → 计分。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import synth18c_run as R  # noqa: E402


def fmt(x):
    return f"{x['readout']:.2f}% | {x['cer']:.2f}% | {x['D']}/{x['S']}/{x['I']}"


def main():
    import synth18b_score as SB
    import tolerant_score as T
    fz = R.verify()
    if (R.OUT / "SCORED").exists():
        sys.exit("已计过分，拒绝再跑")
    preds, counts = R.build_preds()
    R.wj(R.OUT / "pred_pages.json", preds)
    ref_sha = {n: R.sha(R.S18C / n) for n in ("blind-ref.txt", "blind-ref.json", "manifest.json")}
    if ref_sha["blind-ref.txt"] != R.REF_SHA_EXPECTED:
        sys.exit(f"参照 sha 不符：{ref_sha['blind-ref.txt']} ≠ {R.REF_SHA_EXPECTED}，停止（未计分）")
    with open(R.OUT / "SCORED", "x") as f:
        f.write(R.now())
    started = R.now()
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(R.S18C)
    res = {}
    for name, pr in preds.items():
        st = bs.score_system(pr, refs["full"], mp, None)
        cats, rows = SB.categorize_all(bc, bs, hs, refs, rng, pr, st)
        res[name] = {"strict": bc.summ(st)["overall"], "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in"))["overall"],
                     "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out"))["overall"], "cats": cats, "rows": rows,
                     "per_page_strict": {p: v["overall"] for p, v in st["per_page"].items()}}
    braw = res["B 原始（原图）"]["rows"]
    newerr = {n: SB.new_errors(bs, braw, r["rows"]) for n, r in res.items() if n != "B 原始（原图）"}
    llm_new = {"F1+deepseek（相对 F1）": SB.new_errors(bs, res["F1"]["rows"], res["F1+deepseek"]["rows"]),
               "F1+gpt-4o（相对 F1）": SB.new_errors(bs, res["F1"]["rows"], res["F1+gpt-4o"]["rows"]),
               "F2+deepseek（相对 F2）": SB.new_errors(bs, res["F2"]["rows"], res["F2+deepseek"]["rows"])}
    led = [json.loads(l) for l in (R.LLMD / "ledger.jsonl").read_text().splitlines() if l.strip()] if (R.LLMD / "ledger.jsonl").exists() else []
    llm_cost = {}
    for m in ("gpt-4o", "deepseek"):
        xs = [x for x in led if x["model"] == m and x.get("http") == 200]
        llm_cost[m] = {"requests": len(xs), "failed_attempts": sum(1 for x in led if x["model"] == m and x.get("error")),
                       "prompt_tokens": sum(x["prompt_tokens"] for x in xs), "completion_tokens": sum(x["completion_tokens"] for x in xs),
                       "usd": round(sum(x["usd"] for x in xs), 4)}
    ledger = R.baidu_ledger()
    rr = {k: R.jl(R.OUT / "reread" / f"A_{k}" / "results.json")["units"] for k in ("orig", "P2")}
    out = {"scored_at": started, "frozen_sha256": R.sha(R.FROZEN), "ref_sha256": ref_sha,
           "results": {n: {k: v for k, v in r.items() if k != "rows"} for n, r in res.items()},
           "new_errors_vs_braw": newerr, "llm_new_errors": llm_new, "counts": counts, "llm_cost": llm_cost, "baidu": ledger,
           "reread_units": {k: len(v) for k, v in rr.items()}}
    R.wj(R.OUT / "scores.json", out)
    R.wj(R.OUT / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
    # ---------- report
    L = ["# synth18c 留出集一次性验证（F1 / F2）", "",
         f"计分时间 {started}（UTC = 伦敦时间）。冻结文件 `output/synth18c/frozen_synth18c.json` sha256 `{out['frozen_sha256']}`。",
         f"参照 sha256：blind-ref.txt `{ref_sha['blind-ref.txt']}`（应为 2bcbc3a7…，一致）；blind-ref.json `{ref_sha['blind-ref.json']}`；manifest.json `{ref_sha['manifest.json']}`。", "",
         "## 1. synth18c 结果（主结论 = 约定容忍版 + 签名入参照）", "",
         "| 系统 | 容忍·签名入 读出 | CER | D/S/I | 剔除签名 读出 | CER | D/S/I | 严格 读出 | CER | D/S/I | 新引入错误（相对 B 原始） |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in res.items():
        ne = newerr.get(n, [])
        L.append(f"| {n} | {fmt(r['tol_in'])} | {fmt(r['tol_out'])} | {fmt(r['strict'])} | " + (f"{len(ne)} 处 / {sum(e['cost'] for e in ne)} 字" if n in newerr else "—") + " |")
    L += ["", "达标线（主口径）：读出率 ≥ 99%、CER < 1%。", "", "## 2. 错误分类（严格口径、签名入，按编辑代价计字）", ""]
    cats = sorted({k for r in res.values() for k in r["cats"]})
    L += ["| 类别 | " + " | ".join(res) + " |", "|---|" + "---|" * len(res)]
    for k in cats:
        L.append(f"| {k} | " + " | ".join(str(r["cats"].get(k, 0)) for r in res.values()) + " |")
    L += ["", "## 3. 新引入的错误", ""]
    for n, lst in newerr.items():
        if n in ("F1", "F2"):
            kk = Counter()
            for e in lst:
                kk[e["kind"]] += e["cost"]
            L.append(f"- {n}（相对 B 原始）：{len(lst)} 处 / {sum(e['cost'] for e in lst)} 字；{dict(kk)}")
            for e in lst[:15]:
                L.append(f"  - p{e['page']} [{e['category']}] `{e['ref']}` → `{e['pred']}`（{e['kind']}，{e['cost']} 字）")
    for n, lst in llm_new.items():
        L.append(f"- LLM 新引入 · {n}：{len(lst)} 处 / {sum(e['cost'] for e in lst)} 字")
        for e in lst:
            L.append(f"  - p{e['page']} `{e['ref']}` → `{e['pred']}`（{e['kind']}）")
    L += ["", "## 4. 调用与费用", "",
          f"- 百度（synth18c）：调用 {json.dumps(ledger['calls'], ensure_ascii=False)}；免费部分 {json.dumps(ledger['free_used'], ensure_ascii=False)}；"
          f"付费部分 {json.dumps(ledger['paid_calls'], ensure_ascii=False)}，付费 ¥{ledger['paid_cny']}；列表价 {json.dumps(ledger['list_cny'], ensure_ascii=False)}",
          f"- 免费余量（本地日志推算）：{json.dumps(ledger['free_left'], ensure_ascii=False)}；百度累计付费（含此前 Unlimited）¥{ledger['paid_cumulative_cny']} / ¥{R.BAIDU_PAID_CAP_CNY}",
          f"- 二次识别选中行数：{json.dumps(out['reread_units'], ensure_ascii=False)}；各组后处理计数：{json.dumps(counts, ensure_ascii=False)}"]
    for m, c in llm_cost.items():
        L.append(f"- {m}：成功 {c['requests']} 次（失败尝试 {c['failed_attempts']}），输入 {c['prompt_tokens']} / 输出 {c['completion_tokens']} tokens，${c['usd']}")
    L.append(f"- synth18c OpenAI+DeepSeek 合计 ${round(sum(c['usd'] for c in llm_cost.values()), 4)}（单独上限 $2）；6.2 未使用")
    (R.OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
