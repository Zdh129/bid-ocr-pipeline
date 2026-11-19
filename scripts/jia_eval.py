#!/usr/bin/env python3
"""配置甲 公共评估函数（开发成绩脚本与 synth18f 计分脚本共用；不联网，只调用冻结代码与冻结计分脚本）。

版本：文档解析原始结果、方案E、推荐组合去掉变体三（不含纠错）、推荐组合去掉变体三（含深度求索纠错，有缓存才出）、配置甲，
以及诊断行「配置甲去掉变体一乙」（只用来定位变体一乙删掉的字，不算对照版本）。
口径：主口径（容错，签名入参照）、剔除签名口径、严格口径；manifest 的 doc_type 分版面（严格＋主口径）。
变体一乙删字诊断：逐条列出配置甲里 seal_leak 整块删除的块，判断删掉的字是不是参照正文／表格里的真字，并给出被判为章文的依据
（块框内红色比例、与同页文档解析印章块的重叠、去红重识是否为空）。
"""
from __future__ import annotations

import copy
from collections import Counter

JIA = {"seal": "whole", "inv": "v3", "vert": True, "check": True}
E = {"inv": "v3", "hdr": "fields", "vert": True, "check": True}
REC_NO3 = {"seal": "whole", "inv": "v3", "hdr": "fields", "vert": True, "check": True}
B_NAME = "文档解析原始结果"
E_NAME = "方案E"
R3N = "推荐组合去掉变体三（不含纠错）"
R3L = "推荐组合去掉变体三（含深度求索纠错）"
JIA_NAME = "配置甲"
NOSEAL = "诊断：配置甲去掉变体一乙"


def build(c, llm_ok=True):
    """c = f1r2.Ctx。返回 preds, counts, logs, notes。"""
    from baidu_ocr.postprocess import pred_pages
    preds, counts, logs, notes = {B_NAME: pred_pages(copy.deepcopy(c.doc))}, {}, {}, []
    rows = [(E_NAME, E, None), (R3N, REC_NO3, None)]
    if llm_ok:
        rows.append((R3L, REC_NO3, "deepseek"))
    rows += [(JIA_NAME, JIA, None), (NOSEAL, {k: v for k, v in JIA.items() if k != "seal"}, None)]
    for n, v, llm in rows:
        try:
            preds[n], counts[n], lg = c.run(v, llm=llm)
        except RuntimeError as e:   # 缺深度求索缓存：不外发，只记下
            notes.append(f"{n}：{e}（没有缓存，不外发，此行不出）")
            continue
        logs[n] = {"steps": dict(Counter(x.get("step") for x in lg)), "seal_leak": [x for x in lg if x.get("step") == "seal_leak"]}
    return preds, counts, logs, notes


def by_type(st):
    out = {}
    for k, v in (st.get("by_doc_type") or {}).items():
        o = v["overall"]
        out[k] = {"pages": len(v["pages"]) if isinstance(v.get("pages"), list) else v.get("pages"), "readout": round(o["readout"] * 100, 2),
                  "cer": round(o["cer"] * 100, 2), "D": o["deletions"], "S": o["substitutions"], "I": o["insertions"], "ref": o["ref_chars"]}
    return out


def by_type_tol(T, bs, pr, rj, mp, mode):
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


def score(preds, sdir):
    import synth18b_score as SB
    import tolerant_score as T
    from pathlib import Path
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(Path(sdir))
    res = {}
    for n, pr in preds.items():
        st = bs.score_system(pr, refs["full"], mp, None)
        cats, rows = SB.categorize_all(bc, bs, hs, refs, rng, pr, st)
        res[n] = {"strict": bc.summ(st)["overall"], "tol_in": T.summ(T.score_tolerant(bs, pr, rj, mp, "in"))["overall"],
                  "tol_out": T.summ(T.score_tolerant(bs, pr, rj, mp, "out"))["overall"], "strict_by_doc_type": by_type(st),
                  "tol_in_by_doc_type": by_type_tol(T, bs, pr, rj, mp, "in"), "cats": cats, "rows": rows}
    for n in res:
        for basek, tag in ((B_NAME, "new_vs_b"), (E_NAME, "new_vs_e"), (R3N, "new_vs_r3n"), (R3L, "new_vs_r3l"), (NOSEAL, "new_vs_noseal")):
            if n != basek and basek in res:
                res[n][tag] = SB.new_errors(bs, res[basek]["rows"], res[n]["rows"])
    return res, (bs, refs, mp)


def seal_diag(c, logs, scorer):
    """配置甲里变体一乙删掉的每一块：是不是真字、为什么被当成章文。"""
    from baidu_ocr.postprocess import seal_leak as S
    bs, refs, mp = scorer
    units = {u["id"]: u for u in c.seal}
    out = []
    for x in logs.get(JIA_NAME, {}).get("seal_leak", []):
        p = x["page"]
        b = next((b for b in c.doc["blocks"] if b["id"] == x["block_id"]), None)
        seals = [s for s in c.doc["blocks"] if s["page"] == p and s["type"] == "seal"]
        ov = max([S._ov(b["bbox"], s["bbox"]) for s in seals] + [0.0]) if b else None
        seal_txt = max(seals, key=lambda s: S._ov(b["bbox"], s["bbox"]))["text"] if (b and seals) else ""
        rs, _ = bs.ref_chars(refs["full"][p]) if p in refs["full"] else ("", None)
        ref_seals = bs.norm("".join(refs["full"][p]["seals"])) if p in refs["full"] else ""
        rem = bs.norm(x["removed"])
        out.append({"page": p, "doc_type": mp.get(p, {}).get("doc_type"), "block_id": x["block_id"], "removed": x["removed"],
                    "真字（在参照正文或表格里）": bool(rem) and rem in rs, "在参照印章里": bool(rem) and rem in ref_seals,
                    "red_frac": x["red_frac"], "seal_overlap": round(ov, 3) if ov is not None else None, "overlap_seal_text": seal_txt,
                    "reread": x["reread"], "unit_text": (units.get(x["block_id"]) or {}).get("text")})
    return out


def why(d):
    return (f"块框内红色像素比例 {d['red_frac']:.3f}（≥0.05 才进候选，≥0.30 才允许整块删）；与同页文档解析印章块「{d['overlap_seal_text']}」重叠 {d['seal_overlap']:.2f}（≥0.40 才进候选）；"
            f"去红后重识结果{'为空' if not d['reread'] else '为「' + d['reread'] + '」'}——按只删整块规则：重识为空且红色比例 ≥0.30 → 整块删除")


def fmt(x):
    return f"{x['readout']:.2f}% / {x['cer']:.2f}%（{x['D']}/{x['S']}/{x['I']}）"
