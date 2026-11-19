#!/usr/bin/env python3
"""synth18e 一次性计分（scripts/synth18e_run.py score 调用；不联网）。
顺序：校验冻结 → 建全部预测（不看参照）→ 打开参照、记 sha → 写 SCORED 标记（独占；之后再跑拒绝）→ 计分 → 中文报告。
行：文档解析原始结果、v2（6.2 未获批时改为「v2 去掉 6.2」即 v1，并标明）、方案F1（不加纠错，参考）、方案E。
口径：主口径（容错，签名入参照）、剔除签名口径（容错，签名剔除）、严格口径（blind20_score.score_system）；
另按 manifest.json 的 doc_type 分版面报严格口径。新引入错误：相对文档解析原始结果、相对 v2、方案E 相对方案F1。
计分规则全部来自冻结的 blind20_score / tolerant_score / synth18b_score / bestconfig_run，本脚本不改规则。
"""
from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import synth18e_run as R  # noqa: E402

B_NAME, F1_NAME, E_NAME = "文档解析原始结果", "方案F1（不加纠错，参考）", "方案E"
V2_NAME, V1_NAME = "以前的 v2（v1＋6.2 回填）", "以前的 v2 去掉 6.2（即 v1；synth18e 的 6.2 未获批）"


def build_preds(ds_name, ds_entry, f_out, v2_root, vlm_dir, variant):
    """不看参照。ds_entry 是 f1r2.DS 的一项；v2_root 是原图工作根（含 output/scheme_a、scheme_b_unlimited）；vlm_dir 为 6.2 目录或 None。"""
    import f1r2 as F
    from baidu_ocr.postprocess import pred_pages, run_postprocess
    from baidu_ocr.postprocess.aux import load_aux
    from baidu_ocr.postprocess.pipeline import merged_cfg
    import synth18_score as SS
    F.OUT = f_out
    F.DS[ds_name] = ds_entry
    c = F.Ctx(ds_name)
    preds, counts, logs = {B_NAME: pred_pages(copy.deepcopy(c.doc))}, {}, {}
    # v2 对照：frozen_config_v2.json 的覆盖项 + 冻结代码 run_postprocess（与 synth18_score 同一做法）
    fz2 = R.v2_verify()
    pp = merged_cfg(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["postprocess"])
    aux = load_aux(v2_root, c.doc)
    results, man = {}, []
    if vlm_dir and (Path(vlm_dir) / "manifest.json").exists():
        man = R.jl(Path(vlm_dir) / "manifest.json")["items"]
        for it in man:
            rp = Path(vlm_dir) / "vlm" / "results" / f"{it['id']}.json"
            if rp.exists():
                r = R.jl(rp)
                assert r["crop_sha256"] == it["sha256"] and r["prompt_sha12"] == it["prompt_sha12"]
                results[it["id"]] = r
    if results:
        aux["vlm"] = {"items": man, "results": results}
        v2name, key = V2_NAME, "v2"
    else:
        v2name, key = V1_NAME, "v1"
    cfg = SS.deep_update(copy.deepcopy(pp), fz2["variants"][key]["postprocess_overrides"])
    d, info = run_postprocess(copy.deepcopy(c.doc), cfg, aux)
    preds[v2name], counts[v2name] = pred_pages(d), info["counts"]
    preds[F1_NAME], counts[F1_NAME], _ = c.run({}, llm=None)
    preds[E_NAME], counts[E_NAME], lg = c.run(variant, llm=None)
    steps = Counter(x.get("step") for x in lg)
    logs[E_NAME] = {"steps": dict(steps), "detail": [x for x in lg if x.get("step") in ("invoice_reorder_v3", "invoice_header", "vertical_margin", "invoice_check", "reread")]}
    return preds, counts, logs, v2name, {"vlm_items": len(man), "vlm_results": len(results)}


def by_type(st):
    out = {}
    for k, v in (st.get("by_doc_type") or {}).items():
        o = v["overall"]
        out[k] = {"pages": v.get("pages"), "readout": round(o["readout"] * 100, 2), "cer": round(o["cer"] * 100, 2),
                  "D": o["deletions"], "S": o["substitutions"], "I": o["insertions"], "ref": o["ref_chars"]}
    return out


def score_all(preds, sdir, v2name):
    import synth18b_score as SB
    import tolerant_score as T
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(Path(sdir))
    res = {}
    for n, pr in preds.items():
        st = bs.score_system(pr, refs["full"], mp, None)
        cats, rows = SB.categorize_all(bc, bs, hs, refs, rng, pr, st)
        res[n] = {"strict": bc.summ(st)["overall"], "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in"))["overall"],
                  "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out"))["overall"], "strict_by_doc_type": by_type(st),
                  "cats": cats, "rows": rows, "per_page_strict": {p: v["overall"] for p, v in st["per_page"].items()}}
    for n in res:
        for basek, tag in ((B_NAME, "new_vs_b"), (v2name, "new_vs_v2")):
            if n != basek:
                res[n][tag] = SB.new_errors(bs, res[basek]["rows"], res[n]["rows"])
    res[E_NAME]["new_vs_f1"] = SB.new_errors(bs, res[F1_NAME]["rows"], res[E_NAME]["rows"])
    return res


def fmt(x):
    return f"{x['readout']:.2f}% / {x['cer']:.2f}%（{x['D']}/{x['S']}/{x['I']}）"


def report(res, v2name, meta):
    L = ["# synth18e 计分报告（方案E）", "", f"计分时间 {meta['scored_at']}（伦敦时间）。冻结清单 `{meta['frozen']}`，sha256 `{meta['frozen_sha256']}`。",
         f"参照 sha256：" + "；".join(f"`{k}` `{v}`" for k, v in meta["ref_sha256"].items()), "",
         "## 一、总分（读出率 / 错字率（漏字/错字/多字））", "", "| 方案 | 主口径 | 剔除签名口径 | 严格口径 | 新错（比文档解析） | 新错（比 v2 对照） |", "|---|---|---|---|---|---|"]
    for n, r in res.items():
        ne = lambda t: f"{sum(e['cost'] for e in r[t])} 字" if t in r else "—"  # noqa: E731
        L.append(f"| {n} | {fmt(r['tol_in'])} | {fmt(r['tol_out'])} | {fmt(r['strict'])} | {ne('new_vs_b')} | {ne('new_vs_v2')} |")
    if v2name == V1_NAME:
        L += ["", "注：synth18e 的 6.2 外发未获批，v2 对照行没有 6.2 回填，等同 v1。"]
    types = sorted({t for r in res.values() for t in r["strict_by_doc_type"]})
    L += ["", "## 二、按版面类型（manifest.json 的 doc_type）的严格口径", "", "| 版面类型 | 页数 | " + " | ".join(res) + " |", "|---|---|" + "---|" * len(res)]
    for t in types:
        pg = next((r["strict_by_doc_type"][t]["pages"] for r in res.values() if t in r["strict_by_doc_type"]), "")
        L.append(f"| {t} | {len(pg) if isinstance(pg, list) else pg} | " + " | ".join(fmt(r["strict_by_doc_type"][t]) if t in r["strict_by_doc_type"] else "—" for r in res.values()) + " |")
    L += ["", "## 三、方案E 新引入的错误", ""]
    for tag, lab in (("new_vs_b", "比文档解析原始结果"), ("new_vs_v2", "比 v2 对照"), ("new_vs_f1", "比方案F1")):
        lst = res[E_NAME].get(tag, [])
        L.append(f"- {lab}：{len(lst)} 处 / {sum(e['cost'] for e in lst)} 字")
        for e in lst[:30]:
            L.append(f"  - 第 {e['page']} 页［{e['category']}］`{e['ref']}` → `{e['pred']}`（{e['kind']}，{e['cost']} 字）")
    L += ["", "## 四、错误分类（严格口径，按代价计字）", "", "| 类别 | " + " | ".join(res) + " |", "|---|" + "---|" * len(res)]
    for k in sorted({k for r in res.values() for k in r["cats"]}):
        L.append(f"| {k} | " + " | ".join(str(r["cats"].get(k, 0)) for r in res.values()) + " |")
    L += ["", "## 五、调用与费用", "", f"- 百度：{json.dumps(meta.get('baidu'), ensure_ascii=False)}", f"- 6.2（v2 对照）：{json.dumps(meta.get('v2_info'), ensure_ascii=False)}",
          "- 深度求索、gpt-4o：未使用。", f"- 方案E 实际执行的步骤：{json.dumps(meta.get('steps'), ensure_ascii=False)}"]
    return "\n".join(L) + "\n"


def main():
    fz = R.verify()
    if (R.OUT / "SCORED").exists():
        sys.exit("已计过分，拒绝再跑")
    entry = {"dir": R.S18E, "b": R.b_doc_path(), "root": R.WO, "a": R.WP / "output" / "scheme_a",
             "u": R.WP / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
             "reread": R.OUT / "reread" / "A_P2" / "results.json", "render": R.WO / "output" / "scheme_a" / "renders",
             "llm_caches": [], "llm_cfg": None, "llm_spent_before": 0.0, "blind": True,
             "a_orig_summary": R.WO / "output" / "scheme_a" / "call_summary.json"}
    assert (R.OUT / "synth18e" / "vertical" / "results.json").exists(), "缺页边竖排识别结果（先跑 vert）"
    preds, counts, logs, v2name, v2info = build_preds("synth18e", entry, R.OUT, R.WO, R.VLM, R.variant())
    R.wj(R.OUT / "pred_pages.json", preds)
    # ---- 到这里才打开参照
    ref_sha = {n: R.sha(R.S18E / n) for n in ("blind-ref.txt", "blind-ref.json", "manifest.json")}
    with open(R.OUT / "SCORED", "x") as f:
        f.write(R.now())
    res = score_all(preds, R.S18E, v2name)
    meta = {"scored_at": R.now(), "frozen": str(R.FROZEN.relative_to(ROOT)), "frozen_sha256": R.sha(R.FROZEN), "ref_sha256": ref_sha,
            "baidu": R.ledger(), "v2_info": v2info, "steps": logs[E_NAME]["steps"]}
    R.wj(R.OUT / "scores.json", {**meta, "results": {n: {k: v for k, v in r.items() if k != "rows"} for n, r in res.items()}, "counts": counts, "logs": logs})
    R.wj(R.OUT / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
    txt = report(res, v2name, meta)
    (R.OUT / "计分报告_中文.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
