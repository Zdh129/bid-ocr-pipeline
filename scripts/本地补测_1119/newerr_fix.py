"""任务1：修正「新引入错误」统计口径（不读参照：只用已缓存的错误行 diff_vs_ref.json 与输出 pred_pages.json）。

旧口径（scripts/synth18b_score.py:new_errors）：只看位置——新版本的错误行若落在对照版本本来就错的参照位置上，就不算新错。
问题：对照版本在同一位置「已经错了」，但错法不同（例：对照把标题挪到别处、原位置被章文占了），
新版本把这段真字彻底删掉了，位置口径看不出来。
修正口径 = 旧口径 ∪ 内容口径：
  内容·真字丢失：错误行的参照串（≥2 字）在对照版本该页输出里原样出现的次数 > 新版本，且按字计数（不计标点）新版本该页确实少了 ≥2 个这些字；
  内容·多出：错误行的输出串（≥2 个非标点字）在新版本该页原样出现次数 > 对照版本，且按字计数新版本该页确实多了 ≥2 个这些字。
  按字计数用「每页一份预算」：同一批少掉/多出的字只能被一行认领，避免重复计。补出的行代价 = 认领到的字数（不按整行代价）。
  原样串变了但按字计数没少（顺序调换）→ 只列为参考，不计入。
"""
from __future__ import annotations

import json
from pathlib import Path

from common import ROOT

import synth18b_score as SB  # noqa: E402

_bs = None


def bs():
    global _bs
    if _bs is None:
        import bestconfig_run as bc
        _bs = bc.load_mod("blind20_score_norm", ROOT / "scripts" / "blind20_score.py")
    return _bs


def page_text(pr, p):
    pg = pr.get(str(p)) or {}
    return bs().norm("".join(s["text"] for s in pg.get("sections") or []))


def _take(seg, budget):
    """从本页预算里扣出 seg 里的字（非标点），返回扣到的字；预算按页共享，避免同一批字被多行重复计。"""
    got = []
    for c in seg:
        if not SB.is_punct(c) and budget.get(c, 0) > 0:
            budget[c] -= 1
            got.append(c)
    return "".join(got)


def content_new(base_pred, var_pred, rows):
    from collections import Counter
    out, budgets = [], {}
    for r in sorted(rows, key=lambda r: (r["page"], r["ref_start"])):
        p = r["page"]
        bt, vt = page_text(base_pred, p), page_text(var_pred, p)
        if p not in budgets:
            cb, cv = Counter(bt), Counter(vt)
            budgets[p] = ({c: n - cv[c] for c, n in cb.items() if n > cv[c]}, {c: n - cb[c] for c, n in cv.items() if n > cb[c]})
        lost_b, extra_b = budgets[p]
        rp, pp = bs().norm(r["ref"]), bs().norm(r["pred"])
        base = {**{x: r[x] for x in ("page", "ref", "pred", "category")}, "row_cost": r["cost"]}
        if len(rp) >= 2 and rp not in pp and bt.count(rp) > vt.count(rp):
            lost = _take(rp, lost_b)
            if len(lost) >= 2:
                out.append({**base, "cost": len(lost), "kind": "内容·真字丢失", "chars": lost})
            else:
                out.append({**base, "cost": 0, "kind": "参考·原样串变了但字还在（多为顺序变化，不计入）", "chars": ""})
            continue
        if len([c for c in pp if not SB.is_punct(c)]) >= 2 and pp not in rp and vt.count(pp) > bt.count(pp):
            extra = _take(pp, extra_b)
            if len(extra) >= 2:
                out.append({**base, "cost": len(extra), "kind": "内容·多出", "chars": extra})
    return out


def fixed(base_rows, rows, base_pred, var_pred):
    old = SB.new_errors(bs(), base_rows, rows)
    key = lambda x: (x["page"], x["ref"], x["pred"])  # noqa: E731
    seen = {key(x) for x in old}
    allc = [x for x in content_new(base_pred, var_pred, rows) if key(x) not in seen]
    return old, [x for x in allc if x["cost"] > 0], [x for x in allc if x["cost"] == 0]


def cost(xs):
    return sum(x["cost"] for x in xs)


def pairs_for(d: Path, bases, variants=None):
    diff = json.loads((d / "diff_vs_ref.json").read_text(encoding="utf-8"))
    pred = json.loads((d / "pred_pages.json").read_text(encoding="utf-8"))
    res = []
    for b in bases:
        if b not in diff or b not in pred:
            continue
        for v in (variants or list(diff)):
            if v == b or v not in diff or v not in pred:
                continue
            old, add, info = fixed(diff[b], diff[v], pred[b], pred[v])
            res.append({"base": b, "var": v, "old_n": len(old), "old_cost": cost(old), "add_n": len(add), "add_cost": cost(add), "add": add, "old": old, "order_info": info})
    return res
