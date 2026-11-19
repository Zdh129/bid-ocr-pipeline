#!/usr/bin/env python3
"""blind20 计分：以 ChatGPT 网页版逐页转写为满分参照（银标准）。

口径：NFKC、忽略空白、保留标点、参照里【?】排除；正文/表格计分前剔除参照的【印章：…】标记与表格「 | 」分隔符；
印章文字单独计分。逐页对齐（Levenshtein，带回溯），删除=漏字，替换，插入；读出率=匹配字数/参照字数，CER=(删+替+插)/参照字数。
正文/表格的拆分：按参照字符所属分区归属对齐操作（插入归到前一个参照字符的分区）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from baidu_ocr.score import levenshtein_alignment  # noqa: E402

REF = Path("/workspace/pdf-test/frozen20/CHATGPT-REFERENCE-BLIND20-NEW.txt")
MANIFEST = Path("/workspace/pdf-test/frozen20/manifest.json")
SEAL_RE = re.compile(r"【印章[:：](.*?)】")
UNC = "【?】"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace(unicodedata.normalize("NFKC", UNC), "").replace(UNC, "")
    return "".join(ch for ch in s if not ch.isspace())


def parse_ref(text: str) -> dict[int, dict]:
    pages: dict[int, dict] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^=====第(\d+)页=====\s*$", line.strip())
        if m:
            cur = int(m.group(1))
            pages[cur] = {"lines": [], "seals": []}
            continue
        if cur is None:
            continue
        seals = SEAL_RE.findall(line)
        pages[cur]["seals"].extend(seals)
        rest = SEAL_RE.sub("", line)
        if not rest.strip():
            continue
        if " | " in rest or rest.strip().startswith("|") or re.search(r"\|\s*\|", rest):
            pages[cur]["lines"].append(("table", rest.replace("|", " ")))
        else:
            pages[cur]["lines"].append(("body", rest))
    return pages


def ref_chars(page: dict) -> tuple[str, list[str]]:
    s, sec = [], []
    for kind, line in page["lines"]:
        n = norm(line)
        s.append(n)
        sec.extend([kind] * len(n))
    return "".join(s), sec


def zero():
    return {"ref_chars": 0, "pred_chars": 0, "matches": 0, "deletions": 0, "substitutions": 0, "insertions": 0}


def add(a, b):
    for k in a:
        a[k] += b.get(k, 0)


def finalize(c):
    out = dict(c)
    r = c["ref_chars"]
    out["readout"] = round(c["matches"] / r, 6) if r else None
    out["cer"] = round((c["deletions"] + c["substitutions"] + c["insertions"]) / r, 6) if r else None
    return out


def error_spans(ops, page, section_of=None, ctx=6):
    spans = []
    i = 0
    n = len(ops)
    # ops: (op, pred, ref)
    refpos = 0
    ref_idx = []
    for op in ops:
        ref_idx.append(refpos)
        if op[0] != "insert":
            refpos += 1
    while i < n:
        if ops[i][0] == "match":
            i += 1
            continue
        j = i
        while j < n and ops[j][0] != "match":
            j += 1
        # 合并间隔 <=1 个匹配的相邻错误
        while j + 2 < n and ops[j][0] == "match" and ops[j + 1][0] != "match":
            j += 1
            while j < n and ops[j][0] != "match":
                j += 1
        seg = ops[i:j]
        ref_s = "".join(o[2] or "" for o in seg if o[0] != "insert")
        pred_s = "".join(o[1] or "" for o in seg if o[0] != "delete")
        left = "".join((o[2] or o[1] or "") for o in ops[max(0, i - ctx):i] if o[0] == "match")
        right = "".join((o[2] or o[1] or "") for o in ops[j:j + ctx] if o[0] == "match")
        kinds = {o[0] for o in seg if o[0] != "match"}
        sec = section_of[ref_idx[i]] if section_of and ref_idx[i] < len(section_of) else None
        r0 = ref_idx[i]
        r1 = r0 + len(ref_s)
        spans.append({"page": page, "section": sec, "ref_start": r0, "ref_end": r1, "ref": ref_s, "pred": pred_s, "context": f"{left}［…］{right}",
                      "cost": sum(1 for o in seg if o[0] != "match"), "kinds": sorted(kinds)})
        i = j
    return spans


def corrected_ref(rs: str, rsec: list, corrs: list) -> tuple[str, list]:
    """在规范化参照串上按 (ref_start, ref_end) 替换；倒序应用。新字符继承所在位置的分区。"""
    rs_l = list(rs)
    sec_l = list(rsec)
    for c in sorted(corrs, key=lambda c: -c["ref_start"]):
        a, b = c["ref_start"], c["ref_end"]
        assert "".join(rs_l[a:b]) == norm(c["ref"]), (c, "".join(rs_l[a:b]))
        sec = sec_l[a] if a < len(sec_l) else (sec_l[a - 1] if a > 0 else "body")
        new = list(norm(c["new"]))
        rs_l[a:b] = new
        sec_l[a:b] = [sec] * len(new)
    return "".join(rs_l), sec_l


MANUAL: dict = {}
FIXLOG: dict = {}


def score_system(pred_pages: dict, ref_pages: dict, manifest_pages: dict, corrections: list | None = None):
    per_page = {}
    tot = {"overall": zero(), "body": zero(), "table": zero(), "seal": zero()}
    by_type: dict[str, dict] = {}
    spans_all = []
    for p in sorted(ref_pages):
        rp = ref_pages[p]
        rs, rsec = ref_chars(rp)
        seals_ref = list(rp["seals"])
        if corrections:
            rw = (MANUAL.get("page_rewrites") or {}).get(str(p))
            if rw:
                rp2 = parse_ref("=====第%d页=====\n" % p + rw["text"])[p]
                rs, rsec = ref_chars(rp2)
            else:
                pc = [c for c in corrections if c["page"] == p]
                if pc:
                    rs, rsec = corrected_ref(rs, rsec, pc)
            for fx in MANUAL.get("substring_fixes") or []:
                if fx["page"] != p:
                    continue
                old, new = norm(fx["old"]), norm(fx["new"])
                cnt = rs.count(old)
                FIXLOG[(p, fx["old"])] = cnt
                if cnt != 1:
                    continue
                i = rs.index(old)
                sec = rsec[i]
                rs = rs[:i] + new + rs[i + len(old):]
                rsec = rsec[:i] + [sec] * len(new) + rsec[i + len(old):]
            sf = (MANUAL.get("seal_fixes") or {}).get(str(p))
            if sf:
                seals_ref = sf["seals"]
        rp = {**rp, "seals": seals_ref}
        pp = pred_pages.get(str(p)) or {"sections": [], "seal": ""}
        ps = norm("".join(s["text"] for s in pp["sections"]))
        aln = levenshtein_alignment(ps, rs)
        sec_c = {"body": zero(), "table": zero()}
        ri = 0
        last_sec = rsec[0] if rsec else "body"
        for op, pc, rc in aln["ops"]:
            if op == "insert":
                sec = last_sec if ri > 0 else (rsec[0] if rsec else "body")
                sec_c[sec]["insertions"] += 1
                sec_c[sec]["pred_chars"] += 1
                continue
            sec = rsec[ri]
            last_sec = sec
            sec_c[sec]["ref_chars"] += 1
            if op == "match":
                sec_c[sec]["matches"] += 1
                sec_c[sec]["pred_chars"] += 1
            elif op == "substitute":
                sec_c[sec]["substitutions"] += 1
                sec_c[sec]["pred_chars"] += 1
            else:
                sec_c[sec]["deletions"] += 1
            ri += 1
        overall = zero()
        add(overall, sec_c["body"])
        add(overall, sec_c["table"])
        # 印章
        ref_seal = norm("".join(rp["seals"]))
        pred_seal = norm(pp.get("seal") or "")
        sa = levenshtein_alignment(pred_seal, ref_seal)
        seal_c = {"ref_chars": sa["ref_chars"], "pred_chars": sa["pred_chars"], "matches": sa["matches"],
                  "deletions": sa["deletions"], "substitutions": sa["substitutions"], "insertions": sa["insertions"]}
        dtype = manifest_pages[p]["doc_type"]
        per_page[p] = {"doc_type": dtype, "overall": finalize(overall), "body": finalize(sec_c["body"]),
                       "table": finalize(sec_c["table"]), "seal": finalize(seal_c),
                       "seal_ref": "；".join(rp["seals"]), "seal_pred": pp.get("seal") or ""}
        add(tot["overall"], overall)
        add(tot["body"], sec_c["body"])
        add(tot["table"], sec_c["table"])
        add(tot["seal"], seal_c)
        bt = by_type.setdefault(dtype, {"overall": zero(), "body": zero(), "table": zero(), "seal": zero(), "pages": 0})
        bt["pages"] += 1
        add(bt["overall"], overall)
        add(bt["body"], sec_c["body"])
        add(bt["table"], sec_c["table"])
        add(bt["seal"], seal_c)
        spans_all.extend(error_spans(aln["ops"], p, rsec))
    return {
        "total": {k: finalize(v) for k, v in tot.items()},
        "by_doc_type": {k: {"pages": v["pages"], **{s: finalize(v[s]) for s in ("overall", "body", "table", "seal")}} for k, v in by_type.items()},
        "per_page": per_page,
        "error_spans": sorted(spans_all, key=lambda s: -s["cost"]),
    }


def apply_corrections(ref_text: str, corrections: list[dict]) -> tuple[str, list[dict]]:
    pages = re.split(r"(=====第\d+页=====)", ref_text)
    # pages: ['', '=====第1页=====', body1, '=====第2页=====', body2, ...]
    applied = []
    idx = {}
    for k in range(1, len(pages), 2):
        idx[int(re.search(r"\d+", pages[k]).group())] = k + 1
    for c in corrections:
        k = idx[c["page"]]
        cnt = pages[k].count(c["old"])
        if cnt == 0:
            applied.append({**c, "applied": 0, "error": "old_not_found"})
            continue
        n = c.get("count", 1)
        pages[k] = pages[k].replace(c["old"], c["new"], n if n > 0 else cnt)
        applied.append({**c, "applied": min(cnt, n) if n > 0 else cnt})
    return "".join(pages), applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", nargs="+", required=True, help="name=path/to/pred_pages.json")
    ap.add_argument("--corrections", default="")
    ap.add_argument("--manual", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    ref_text = REF.read_text(encoding="utf-8")
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mpages = {int(p["output_page"]): p for p in man["pages"]}
    variants = {"original": (ref_text, [])}
    if args.corrections:
        corr = json.loads(Path(args.corrections).read_text(encoding="utf-8"))
        rewrite_pages = set()
        if args.manual:
            MANUAL.update(json.loads(Path(args.manual).read_text(encoding="utf-8")))
            rewrite_pages = {int(k) for k in (MANUAL.get("page_rewrites") or {})}
        variants["corrected"] = (ref_text, [c for c in corr["corrections"] if c["page"] not in rewrite_pages])
    out = {"protocol": __doc__.strip(), "reference_sha256": "8946a6d2a36f7de8166f74d9542e370760e0734b03b316a6a2f416105df0bcda", "variants": {}}
    for vname, (txt, applied) in variants.items():
        refp = parse_ref(txt)
        v = {"n_corrections": len(applied), "systems": {}}
        FIXLOG.clear()
        for spec in args.systems:
            name, path = spec.split("=", 1)
            pred = json.loads(Path(path).read_text(encoding="utf-8"))
            v["systems"][name] = score_system(pred, refp, mpages, applied or None)
        v["substring_fix_hits"] = [{"page": k[0], "old": k[1], "occurrences": c} for k, c in FIXLOG.items()]
        out["variants"][vname] = v
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for vname, v in out["variants"].items():
        for name, s in v["systems"].items():
            t = s["total"]
            print(vname, name, {k: (t[k]["readout"], t[k]["cer"], t[k]["deletions"], t[k]["substitutions"], t[k]["insertions"], t[k]["ref_chars"]) for k in t})


if __name__ == "__main__":
    main()
