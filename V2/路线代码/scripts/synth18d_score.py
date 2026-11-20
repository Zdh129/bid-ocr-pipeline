#!/usr/bin/env python3
"""synth18d 一次性计分（scripts/synth18d_run.py score 调用；不联网）。由 scripts/synth18e_score.py 改写。
顺序：校验冻结 → 建全部版本的预测（不看参照）→ 逐版本记输出 sha → 核对参照 sha → 写 SCORED 标记（独占；之后再跑拒绝）→ 计分 → 中文报告。
口径：主口径（容错，签名入参照）、剔除签名口径、严格口径；manifest.json 的 doc_type 分版面（严格口径＋主口径）。规则全部来自冻结的计分脚本。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import synth18d_run as R  # noqa: E402

REC = R.RECOMMENDED
B_NAME = "文档解析原始结果"
V2_NAME = "v2（旧冻结清单代码；6.2 未获批，无回填结果）"
F1_NAME = "方案F1（不加纠错）"
RN_NAME = "推荐组合（不含纠错）"
RL_NAME = "推荐组合（含纠错）"
AB_SEAL = "推荐组合（含纠错）去掉变体一乙"
AB_STAR = "推荐组合（含纠错）去掉变体三"
AB_LLM = "推荐组合（含纠错）去掉深度求索纠错"
E_NAME = "参考：方案E（本轮之前冻结）"


def no(k):
    return {x: y for x, y in REC.items() if x != k}


# 名称 → (开关, 是否深度求索)
ROWS = {F1_NAME: ({}, None), RN_NAME: (REC, None), RL_NAME: (REC, "deepseek"),
        AB_SEAL: (no("seal"), "deepseek"), AB_STAR: (no("starf"), "deepseek"), AB_LLM: (REC, None), E_NAME: (R.EXPECTED, None)}
LLM_VARIANTS = [REC, no("seal"), no("starf")]


def entry():
    return {"dir": R.S18D, "b": R.b_doc_path(), "root": R.WO, "a": R.WP / "output" / "scheme_a",
            "u": R.WP / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
            "reread": R.OUT / "reread" / "A_P2" / "results.json", "render": R.WO / "output" / "scheme_a" / "renders",
            "llm_caches": [], "llm_cfg": None, "llm_spent_before": 0.0, "blind": True,
            "a_orig_summary": R.WO / "output" / "scheme_a" / "call_summary.json"}


def ctx():
    import f1r2 as F
    F.OUT = R.OUT
    F.DS["synth18d"] = entry()
    for k in ("seal_leak", "vertical"):
        assert (R.OUT / "synth18d" / k / "results.json").exists(), f"缺 {k} 结果"
    return F, F.Ctx("synth18d")


def build_preds():
    from baidu_ocr.postprocess import pred_pages, run_postprocess
    from baidu_ocr.postprocess.aux import load_aux
    from baidu_ocr.postprocess.pipeline import merged_cfg
    import synth18_score as SS
    F, c = ctx()
    preds, counts, logs = {B_NAME: pred_pages(copy.deepcopy(c.doc))}, {}, {}
    fz2 = R.v2_verify()
    pp = merged_cfg(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["postprocess"])
    aux = load_aux(R.WO, c.doc)
    aux["vlm"] = {"items": [], "results": {}}
    cfg = SS.deep_update(copy.deepcopy(pp), fz2["variants"]["v2"]["postprocess_overrides"])
    d, info = run_postprocess(copy.deepcopy(c.doc), cfg, aux)
    preds[V2_NAME], counts[V2_NAME] = pred_pages(d), info["counts"]
    for n, (v, llm) in ROWS.items():
        preds[n], counts[n], lg = c.run(v, llm=llm)
        logs[n] = dict(Counter(x.get("step") for x in lg))
    return preds, counts, logs


def by_type_tol(T, bs, pr, rj, mp, mode):
    """容错口径按 doc_type 汇总：tolerant_score 逐页 overall 计数相加，再用 blind20_score.finalize 算读出率／错字率。"""
    sc = T.score_tolerant(bs, pr, rj, mp, mode)
    keys = ("ref_chars", "pred_chars", "matches", "deletions", "substitutions", "insertions")
    agg, npg = {}, Counter()
    for p, v in sc["per_page"].items():
        t = mp[int(p)]["doc_type"]
        npg[t] += 1
        a = agg.setdefault(t, {k: 0 for k in keys})
        for k in keys:
            a[k] += v["overall"][k]
    out = {}
    for t, a in agg.items():
        f = bs.finalize(a)
        out[t] = {"pages": npg[t], "readout": round(f["readout"] * 100, 2), "cer": round(f["cer"] * 100, 2),
                  "D": f["deletions"], "S": f["substitutions"], "I": f["insertions"], "ref": f["ref_chars"]}
    return out


def by_type(st):
    out = {}
    for k, v in (st.get("by_doc_type") or {}).items():
        o = v["overall"]
        out[k] = {"pages": v.get("pages"), "readout": round(o["readout"] * 100, 2), "cer": round(o["cer"] * 100, 2),
                  "D": o["deletions"], "S": o["substitutions"], "I": o["insertions"], "ref": o["ref_chars"]}
    return out


def score_all(preds, sdir):
    import synth18b_score as SB
    import tolerant_score as T
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(Path(sdir))
    res = {}
    for n, pr in preds.items():
        st = bs.score_system(pr, refs["full"], mp, None)
        cats, rows = SB.categorize_all(bc, bs, hs, refs, rng, pr, st)
        res[n] = {"strict": bc.summ(st)["overall"], "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in"))["overall"],
                  "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out"))["overall"], "strict_by_doc_type": by_type(st),
                  "tol_in_by_doc_type": by_type_tol(T, bs, pr, rj, mp, "in"),
                  "cats": cats, "rows": rows, "per_page_strict": {p: v["overall"] for p, v in st["per_page"].items()}}
    for n in res:
        for basek, tag in ((B_NAME, "new_vs_b"), (V2_NAME, "new_vs_v2"), (F1_NAME, "new_vs_f1"), (RL_NAME, "new_vs_rl"), (RN_NAME, "new_vs_rn")):
            if n != basek:
                res[n][tag] = SB.new_errors(bs, res[basek]["rows"], res[n]["rows"])
    return res


def main():
    R.verify()
    if (R.OUT / "SCORED").exists():
        sys.exit("已计过分，拒绝再跑")
    preds, counts, logs = build_preds()
    out_sha = {n: hashlib.sha256(json.dumps(pr, ensure_ascii=False, sort_keys=True).encode()).hexdigest() for n, pr in preds.items()}
    R.wj(R.OUT / "pred_pages.json", preds)
    R.wj(R.OUT / "输出sha.json", {"at": R.now(), "pred_pages.json": R.sha(R.OUT / "pred_pages.json"), "各版本": out_sha})
    print("全部版本输出已生成并记 sha：", R.sha(R.OUT / "pred_pages.json"), flush=True)
    # ---- 到这里才打开参照
    ref_sha = {n: R.sha(R.S18D / n) for n in ("blind-ref.txt", "blind-ref.json", "manifest.json")}
    if ref_sha["blind-ref.txt"] != R.REF_SHA_EXPECTED:
        sys.exit(f"参照 sha 不符：{ref_sha['blind-ref.txt']}，停止（未计分）")
    with open(R.OUT / "SCORED", "x") as f:
        f.write(R.now())
    started = R.now()
    res = score_all(preds, R.S18D)
    led = [json.loads(l) for l in (R.OUT / "synth18d" / "llm" / "ledger.jsonl").read_text().splitlines() if l.strip()]
    ok = [x for x in led if x.get("http") == 200]
    llm = {"requests_ok": len(ok), "attempts": len(led), "prompt_tokens": sum(x["prompt_tokens"] for x in ok),
           "completion_tokens": sum(x["completion_tokens"] for x in ok), "usd": round(sum(x["usd"] for x in ok), 6)}
    meta = {"scored_at": started, "frozen_sha256": R.sha(R.FROZEN), "frozen_rec_sha256": R.sha(R.FROZEN_REC), "frozen_e_sha256": R.sha(R.FROZEN_E),
            "ref_sha256": ref_sha, "output_sha": out_sha, "baidu": R.ledger(), "llm": llm, "steps": logs}
    R.wj(R.OUT / "scores.json", {**meta, "results": {n: {k: v for k, v in r.items() if k != "rows"} for n, r in res.items()}, "counts": counts})
    R.wj(R.OUT / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
    f = lambda x: f"{x['readout']:.2f}/{x['cer']:.2f}/{x['D']}/{x['S']}/{x['I']}"  # noqa: E731
    for n, r in res.items():
        print(n, "| 主", f(r["tol_in"]), "| 剔签", f(r["tol_out"]), "| 严格", f(r["strict"]),
              "| 新错 B/v2/F1/含纠错/不含纠错", *[sum(e["cost"] for e in r[t]) if t in r else "—" for t in ("new_vs_b", "new_vs_v2", "new_vs_f1", "new_vs_rl", "new_vs_rn")])
    print(json.dumps({"llm": llm, "baidu": meta["baidu"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
