#!/usr/bin/env python3
"""约定容忍计分的口径消融副本（不改原文件 tolerant_score.py，不改参照，不改预测）。

在原 score_tolerant 上增加三个开关，可分别关掉主口径的三项放宽：
  allow_vertical   页边竖排：invoice_vertical 作为 kind=margin_vertical 的单元，不计位置（默认开）
  allow_header     发票表头：「购买方」之前各行作为 kind=invoice_header 的单元，不计行序（默认开）
  allow_seal_leak  印章混入：extract_seal_leaks 把正文里 4 字以上、与参照印章相同的片段挪到印章串（默认开）

三个开关默认全部为 True，行为应与原 tolerant_score.score_tolerant 完全一致。
签名口径（sign="in"/"out"）不变；本消融默认签名计入。

直接运行本脚本：用已有 pred_pages.json 对各套集做口径消融，不重新识别、不调用外部 API、不改任何已有文件。
"""
from __future__ import annotations

import copy
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from baidu_ocr.score import levenshtein_alignment  # noqa: E402

MAX_NORM_DIST = 0.5


def approx_sub(u: str, t: str):
    """u 在 t 里的最佳近似子串（Sellers）：返回 (dist, start, end)。"""
    if not u:
        return 0, 0, 0
    if not t:
        return len(u), 0, 0
    n, m = len(u), len(t)
    prev = [0] * (m + 1)
    starts = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        cst = [0] * (m + 1)
        for j in range(1, m + 1):
            c = 0 if u[i - 1] == t[j - 1] else 1
            best, st = prev[j - 1] + c, starts[j - 1]
            if prev[j] + 1 < best:
                best, st = prev[j] + 1, starts[j]
            if cur[j - 1] + 1 < best:
                best, st = cur[j - 1] + 1, cst[j - 1]
            cur[j], cst[j] = best, st
        prev, starts = cur, cst
    j = min(range(m + 1), key=lambda j: (prev[j], -j))
    return prev[j], starts[j], j


def counts(pred: str, ref: str) -> dict:
    a = levenshtein_alignment(pred, ref)
    return {"ref_chars": a["ref_chars"], "pred_chars": a["pred_chars"], "matches": a["matches"], "deletions": a["deletions"],
            "substitutions": a["substitutions"], "insertions": a["insertions"]}


def ref_pages_from_json(bs, rj: dict, sign: str, allow_vertical: bool = True, allow_header: bool = True):
    """返回 {page: {"lines": [(kind, text)], "seals": [...], "units": [unit...]}}；units 已从 lines 去掉。

    allow_vertical=False 时，不把 invoice_vertical 抽成不计位置的单元，竖排文字留在原行里按位置计分。
    allow_header=False 时，不把「购买方」之前的发票表头行抽成不计行序的单元，表头留在原行里按行序计分。
    """
    out = {}
    for pg in rj["pages"]:
        p = pg["page"]
        lines = {l["n"]: l["text"] for l in pg["ref_lines"]}
        if sign == "out":
            for b in pg["blocks"]:
                if b["type"] == "handwriting_sign":
                    t = lines[b["ref_line"]]
                    i = t.rfind(b["text"])
                    assert i >= 0
                    lines[b["ref_line"]] = t[:i] + t[i + len(b["text"]):]
        units = []
        if allow_vertical:
            for b in pg["blocks"]:
                if b["type"] == "invoice_vertical":
                    t = lines[b["ref_line"]]
                    i = t.find(b["text"])
                    assert i >= 0, (p, b)
                    lines[b["ref_line"]] = t[:i] + t[i + len(b["text"]):]
                    units.append({"kind": "margin_vertical", "text": b["text"], "ref_line": b["ref_line"]})
        is_invoice = any(b["type"] == "invoice_vertical" for b in pg["blocks"]) or "发票号码" in "".join(lines.values())
        if allow_header and is_invoice:
            buyer = next((n for n in sorted(lines) if bs.norm(lines[n]).startswith("购买方")), None)
            if buyer is not None:
                for n in sorted(lines):
                    if n >= buyer:
                        break
                    if lines[n].startswith("【") or not bs.norm(bs.SEAL_RE.sub("", lines[n])):
                        continue
                    units.append({"kind": "invoice_header", "text": bs.SEAL_RE.sub("", lines[n]), "ref_line": n})
                    lines[n] = "".join(f"【印章：{s}】" for s in bs.SEAL_RE.findall(lines[n]))
        text = f"=====第{p}页=====\n" + "".join(lines[n] + "\n" for n in sorted(lines))
        rp = bs.parse_ref(text)[p]
        rp["units"] = units
        out[p] = rp
    return out


def extract_units(bs, units, pred_lines):
    """迭代贪心：每轮在所有（未配单元 × OCR 行）里取归一化编辑距离最小的一对，抽出该子串。"""
    lines = list(pred_lines)
    res = []
    todo = [dict(u, n=bs.norm(u["text"])) for u in units if bs.norm(u["text"])]
    while todo:
        best = None
        for ui, u in enumerate(todo):
            for li, ln in enumerate(lines):
                d, s, e = approx_sub(u["n"], ln)
                nd = d / len(u["n"])
                if best is None or nd < best[0]:
                    best = (nd, ui, li, s, e)
        if best is None or best[0] > MAX_NORM_DIST:
            for u in todo:
                res.append({**u, "pred": "", **counts("", u["n"])})
            break
        nd, ui, li, s, e = best
        u = todo.pop(ui)
        sub = lines[li][s:e]
        lines[li] = lines[li][:s] + lines[li][e:]
        res.append({**u, "pred": sub, **counts(sub, u["n"])})
    return res, lines


def extract_seal_leaks(bs, seals_ref, pred_lines, body_ref):
    """正文里与参照印章文字的最长公共片段（≥4 字）：抽出后正文编辑距离下降才抽。"""
    moved = []
    lines = list(pred_lines)
    for st in seals_ref:
        sn = bs.norm(st)
        changed = True
        while changed:
            changed = False
            for li, ln in enumerate(lines):
                m = SequenceMatcher(None, ln, sn, autojunk=False).find_longest_match(0, len(ln), 0, len(sn))
                if m.size < 4:
                    continue
                before = levenshtein_alignment("".join(lines), body_ref)
                cand = lines[:li] + [ln[:m.a] + ln[m.a + m.size:]] + lines[li + 1:]
                after = levenshtein_alignment("".join(cand), body_ref)
                db = before["deletions"] + before["substitutions"] + before["insertions"]
                da = after["deletions"] + after["substitutions"] + after["insertions"]
                if da <= db - m.size * 0.5:
                    moved.append(ln[m.a:m.a + m.size])
                    lines = cand
                    changed = True
                    break
    return moved, lines


def score_tolerant(bs, pred_pages: dict, rj: dict, manifest_pages: dict, sign: str = "in",
                   allow_vertical: bool = True, allow_header: bool = True, allow_seal_leak: bool = True):
    """主口径计分。三个 allow_* 开关默认全开，与原 tolerant_score.score_tolerant 一致。"""
    refp = ref_pages_from_json(bs, rj, sign, allow_vertical=allow_vertical, allow_header=allow_header)
    new_pred, unit_log, seal_log = {}, [], []
    extra = {}
    for p, rp in refp.items():
        pp = pred_pages.get(str(p)) or {"sections": [], "seal": ""}
        lines = [bs.norm(x) for s in pp["sections"] for x in (s["text"] or "").split("\n")]
        lines = [x for x in lines if x]
        res, lines = extract_units(bs, rp["units"], lines) if rp["units"] else ([], lines)
        body_ref, _ = bs.ref_chars(rp)
        moved, lines = extract_seal_leaks(bs, rp["seals"], lines, body_ref) if (allow_seal_leak and rp["seals"]) else ([], lines)
        new_pred[str(p)] = {"sections": [{"section": "body", "text": "".join(lines)}], "seal": (pp.get("seal") or "") + "".join(moved)}
        extra[p] = res
        unit_log += [{"page": p, **{k: r[k] for k in ("kind", "text", "pred", "deletions", "substitutions", "insertions")}} for r in res]
        seal_log += [{"page": p, "moved_to_seal": m} for m in moved]
    sc = bs.score_system(new_pred, {p: {"lines": rp["lines"], "seals": rp["seals"]} for p, rp in refp.items()}, manifest_pages, None)
    # 把单元的计数并入正文与总分
    raw = {k: {kk: sc["total"][k][kk] for kk in ("ref_chars", "pred_chars", "matches", "deletions", "substitutions", "insertions")} for k in ("overall", "body", "table", "seal")}
    for p, res in extra.items():
        for r in res:
            for k in ("ref_chars", "pred_chars", "matches", "deletions", "substitutions", "insertions"):
                raw["overall"][k] += r[k]
                raw["body"][k] += r[k]
                pg = sc["per_page"][p]["overall"]
                pg[k] = pg.get(k, 0) + r[k]
    for p in sc["per_page"]:
        sc["per_page"][p]["overall"] = bs.finalize({k: sc["per_page"][p]["overall"][k] for k in ("ref_chars", "pred_chars", "matches", "deletions", "substitutions", "insertions")})
    sc["total"] = {k: bs.finalize(v) for k, v in raw.items()}
    sc["units"] = unit_log
    sc["seal_moves"] = seal_log
    return sc


def summ(sc):
    out = {}
    for k in ("overall", "body", "table", "seal"):
        t = sc["total"][k]
        out[k] = {"readout": round(t["readout"] * 100, 2), "cer": round(t["cer"] * 100, 2), "D": t["deletions"], "S": t["substitutions"],
                  "I": t["insertions"], "ref": t["ref_chars"]}
    return out


# ------------------------------------------------------------------ 自检（只用 synth18 开发集参照 + 合成样例）
def selftest():
    import bestconfig_run as bc
    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    S18 = Path("/workspace/pdf-test/synth18")
    rj = json.loads((S18 / "blind-ref.json").read_text(encoding="utf-8"))
    mp = {int(p["output_page"]): p for p in json.loads((S18 / "manifest.json").read_text(encoding="utf-8"))["pages"]}
    # A：完美输出（按参照原顺序）→ 两种口径都 100% / 0
    perfect = {}
    for pg in rj["pages"]:
        rp = bs.parse_ref(f"=====第{pg['page']}页=====\n" + "".join(l["text"] + "\n" for l in pg["ref_lines"]))[pg["page"]]
        perfect[str(pg["page"])] = {"sections": [{"section": k, "text": t} for k, t in rp["lines"]], "seal": "".join(rp["seals"])}
    t = summ(score_tolerant(bs, perfect, rj, mp, "in"))["overall"]
    assert t["cer"] == 0 and t["readout"] == 100, t
    # B：发票页把表头带顺序打乱、竖排字挪到页首、正文混入监制章文字 → 容忍版应仍 0；严格版 >0
    bad = copy.deepcopy(perfect)
    for p in ("15", "16", "17"):
        secs = bad[p]["sections"]
        pg = next(x for x in rj["pages"] if x["page"] == int(p))
        vert = next(b["text"] for b in pg["blocks"] if b["type"] == "invoice_vertical")
        for s in secs:
            s["text"] = s["text"].replace(vert, "")
        hdr = [s for s in secs[:3]]
        secs[:3] = [hdr[1], hdr[2], hdr[0]]
        secs.insert(0, {"section": "body", "text": vert})
        secs.append({"section": "body", "text": "国家税务总局云岭省税务局"})
    strict = bc.summ(bs.score_system(bad, bs.parse_ref((S18 / "blind-ref.txt").read_text(encoding="utf-8")), mp, None))["overall"]
    tol = summ(score_tolerant(bs, bad, rj, mp, "in"))["overall"]
    assert strict["cer"] > 0 and tol["cer"] == 0 and tol["readout"] == 100, (strict, tol)
    # C：真实正文里的单位名（与印章同名）不能被当成印章抽走：完美输出 + 单位名 → 删掉单位名应算漏读
    miss = copy.deepcopy(perfect)
    p4 = miss["4"]["sections"]
    p4[:] = [s for s in p4 if "云岭启明分析仪器有限公司" not in s["text"]]
    tol_m = summ(score_tolerant(bs, miss, rj, mp, "in"))["overall"]
    assert tol_m["D"] > 0, tol_m
    # D：签名剔除口径：完美输出去掉签名字 → out 口径 0，in 口径 >0
    nos = copy.deepcopy(perfect)
    for pg in rj["pages"]:
        for b in pg["blocks"]:
            if b["type"] == "handwriting_sign":
                for s in nos[str(pg["page"])]["sections"]:
                    if s["text"].endswith(b["text"]):
                        s["text"] = s["text"][: -len(b["text"])]
                        break
    a_in = summ(score_tolerant(bs, nos, rj, mp, "in"))["overall"]
    a_out = summ(score_tolerant(bs, nos, rj, mp, "out"))["overall"]
    assert a_out["cer"] == 0 and a_in["cer"] > 0, (a_in, a_out)
    print("tolerant selftest OK", {"strict_bad": strict, "tol_bad": tol, "tol_missing_company": tol_m, "nosign_in": a_in})


if __name__ == "__main__":
    selftest()
