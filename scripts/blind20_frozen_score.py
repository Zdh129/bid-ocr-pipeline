#!/usr/bin/env python3
"""blind20 冻结推荐组合 完整计分（含深度求索纠错版；冻结后新写，只调用冻结代码与冻结计分脚本）。
行：文档解析原始结果、以前的 v1、以前的 v2、方案F1（不加纠错，参考）、冻结推荐组合（去掉深度求索纠错）、冻结推荐组合（含深度求索纠错）。
三种口径：主口径＝容错口径（tolerant_score，签名入参照）、剔除签名口径（tolerant_score sign=out）、严格口径（blind20_score.score_system，修正参照）。
产出：output/blind20_frozen/完整计分/{scores.json, diff_vs_ref.json, pred_pages.json}；不覆盖 output/blind20_frozen/scores.json（不含纠错版的计分）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import blind20_frozen_run as R  # noqa: E402

NAME_LLM = "冻结推荐组合（含深度求索纠错）"


def main():
    R.verify()
    import synth18b_score as SB
    from baidu_ocr.postprocess import pred_pages
    F, c = R.ctx()
    preds = {"文档解析原始结果": pred_pages(c.doc), "以前的 v1（当前最优解，四步）": R.jl(ROOT / "output" / "bestconfig" / "pred_pages.json"),
             "以前的 v2（v1＋6.2 回填）": R.jl(ROOT / "output" / "bestconfig_v2" / "pred_pages.json")}
    counts, logs = {}, {}
    preds["方案F1（不加纠错，参考）"], counts["方案F1（不加纠错，参考）"], _ = c.run({}, llm=None)
    preds[R.NAME], counts[R.NAME], _ = c.run(R.RECOMMENDED, llm=None)
    preds[NAME_LLM], counts[NAME_LLM], lg = c.run(R.RECOMMENDED, llm="deepseek")
    logs[NAME_LLM] = [x for x in lg if x.get("step") in ("seal_leak", "invoice_reorder_v3", "star_filter", "invoice_header", "vertical_margin", "invoice_check", "reread")]
    bc, bs, hs, T, refs, rj, mp, rng = R.scorer()
    res = {}
    for n, pr in preds.items():
        st = bs.score_system(pr, refs["full"], mp, None)
        cats, rows = SB.categorize_all(bc, bs, hs, refs, rng, pr, st)
        res[n] = {"strict": bc.summ(st)["overall"], "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in"))["overall"],
                  "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out"))["overall"], "cats": cats, "rows": rows,
                  "per_section_strict": {k: v for k, v in bc.summ(st).items() if k != "overall"}}
    bases = {"new_vs_b": "文档解析原始结果", "new_vs_v1": "以前的 v1（当前最优解，四步）", "new_vs_v2": "以前的 v2（v1＋6.2 回填）",
             "new_vs_nollm": R.NAME}
    for n in res:
        for tag, basek in bases.items():
            if n != basek:
                res[n][tag] = SB.new_errors(bs, res[basek]["rows"], res[n]["rows"])
    out = R.OUT / "完整计分"
    R.wj(out / "pred_pages.json", {k: v for k, v in preds.items() if not k.startswith("以前")})
    R.wj(out / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
    R.wj(out / "scores.json", {"scored_at": R.now(), "frozen_sha256": R.sha(R.FROZEN),
                               "ref_struct_sha256": {f: R.sha(R.OUT / "计分参照" / f) for f in ("blind-ref.txt", "blind-ref.json")},
                               "results": {n: {k: v for k, v in r.items() if k != "rows"} for n, r in res.items()}, "counts": counts, "logs": logs,
                               "baidu": R.ledger()})
    f = lambda x: f"{x['readout']:.2f}%/{x['cer']:.2f}%/{x['D']}/{x['S']}/{x['I']}"  # noqa: E731
    for n, r in res.items():
        print(n, "| 主", f(r["tol_in"]), "| 剔签", f(r["tol_out"]), "| 严格", f(r["strict"]),
              "| 新错 vs B/v1/v2/无纠错", *[sum(e["cost"] for e in r[t]) if t in r else "—" for t in bases])


if __name__ == "__main__":
    main()
