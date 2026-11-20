#!/usr/bin/env python3
"""混合路线计分（纯本地）：
- 基线：A、B 标准版（已跑好的 pred_pages），各算「不加规则 / 加 ★☆ 规则」两版；
- 路线 1 / 路线 2：若 output/hybrid/vlm/results/<route>/ 下已有 VLM 结果，则合并回中间 JSON 后计分（同样两版）；
- 参照：修正参照（reference_corrections.json + manual_reference_fixes.json）与原参照两套；
- 另做「可触达错误上限」分析：B 现有错误里落在各路线送审区域内的比例（假设 VLM 在送审区域内全对且不引入新错）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.hybrid.merge import merge_route, pred_pages_from_doc  # noqa: E402
from baidu_ocr.hybrid.rules import norm, table_rows  # noqa: E402
from baidu_ocr.hybrid.star_rule import RULE_TEXT, apply_to_document, apply_to_pred_pages  # noqa: E402
from baidu_ocr.score import levenshtein_alignment  # noqa: E402
from baidu_ocr.serialize import validate_schema  # noqa: E402

OUT = ROOT / "output" / "hybrid"
FINAL = ROOT / "output" / "final"
CORR = ROOT / "output" / "compare" / "reference_corrections.json"
MANUAL = ROOT / "data" / "blind20" / "manual_reference_fixes.json"


def load_bs():
    spec = importlib.util.spec_from_file_location("blind20_score", ROOT / "scripts" / "blind20_score.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ref_variants(bs):
    ref_text = bs.REF.read_text(encoding="utf-8")
    man = json.loads(bs.MANIFEST.read_text(encoding="utf-8"))
    mpages = {int(p["output_page"]): p for p in man["pages"]}
    manual = json.loads(MANUAL.read_text(encoding="utf-8"))
    bs.MANUAL.clear()
    bs.MANUAL.update(manual)
    rw = {int(k) for k in (manual.get("page_rewrites") or {})}
    corr = [c for c in json.loads(CORR.read_text(encoding="utf-8"))["corrections"] if c["page"] not in rw]
    refp = bs.parse_ref(ref_text)
    return {"corrected": (refp, corr), "original": (refp, None)}, mpages


def ref_page_string(bs, refp, p, corrections):
    """与 blind20_score.score_system 中的参照构造完全相同（用于可触达分析）。"""
    rp = refp[p]
    rs, rsec = bs.ref_chars(rp)
    if corrections:
        rw = (bs.MANUAL.get("page_rewrites") or {}).get(str(p))
        if rw:
            rs, rsec = bs.ref_chars(bs.parse_ref("=====第%d页=====\n" % p + rw["text"])[p])
        else:
            pc = [c for c in corrections if c["page"] == p]
            if pc:
                rs, rsec = bs.corrected_ref(rs, rsec, pc)
        for fx in bs.MANUAL.get("substring_fixes") or []:
            if fx["page"] != p:
                continue
            old, new = bs.norm(fx["old"]), bs.norm(fx["new"])
            if rs.count(old) != 1:
                continue
            i = rs.index(old)
            sec = rsec[i]
            rs = rs[:i] + new + rs[i + len(old):]
            rsec = rsec[:i] + [sec] * len(new) + rsec[i + len(old):]
    return rs, rsec


def owners(doc: dict, p: int) -> tuple[str, list]:
    """逐字归属：正文字 → ('block', id)；表格字 → ('table', id, 行下标)。"""
    s, own = [], []
    for x in sorted([b for b in doc["blocks"] if b["page"] == p], key=lambda b: b["reading_order"]):
        if x["type"] == "seal":
            continue
        if x["type"] == "table":
            for i, r in enumerate(table_rows(x)):
                t = norm(" ".join(c["text"] for c in r["cells"]))
                s.append(t)
                own += [("table", x["id"], i)] * len(t)
        else:
            t = norm(x.get("text"))
            s.append(t)
            own += [("block", x["id"])] * len(t)
    return "".join(s), own


def addressable(bs, doc, refp, corrections, manifest_items):
    sent_blocks = {i["target"]["b_block_id"] for i in manifest_items if i["kind"] == "line"}
    sent_rows = set()
    for i in manifest_items:
        if i["kind"] == "table_band":
            a, b = i["target"]["row_idx"]
            sent_rows |= {(i["target"]["b_block_id"], r) for r in range(a, b + 1)}

    def hit(o):
        if o is None:
            return False
        return (o[0] == "block" and o[1] in sent_blocks) or (o[0] == "table" and (o[1], o[2]) in sent_rows)

    tot = {s: {"ref": 0, "match": 0, "D": 0, "S": 0, "I": 0, "aD": 0, "aS": 0, "aI": 0} for s in ("body", "table")}
    for p in sorted(refp):
        rs, rsec = ref_page_string(bs, refp, p, corrections)
        ps, own = owners(doc, p)
        aln = levenshtein_alignment(ps, rs)
        pi = ri = 0
        last = rsec[0] if rsec else "body"
        for op, _, _ in aln["ops"]:
            if op == "insert":
                sec = last
                o = own[pi] if pi < len(own) else None
                tot[sec]["I"] += 1
                tot[sec]["aI"] += hit(o)
                pi += 1
                continue
            sec = rsec[ri]
            last = sec
            tot[sec]["ref"] += 1
            if op == "match":
                tot[sec]["match"] += 1
                pi += 1
            elif op == "substitute":
                tot[sec]["S"] += 1
                tot[sec]["aS"] += hit(own[pi])
                pi += 1
            else:
                o_prev = own[pi - 1] if pi > 0 else None
                o_next = own[pi] if pi < len(own) else None
                tot[sec]["D"] += 1
                tot[sec]["aD"] += hit(o_prev) or hit(o_next)
            ri += 1
    res = {}
    allc = {k: tot["body"][k] + tot["table"][k] for k in tot["body"]}
    for name, t in (("overall", allc), ("body", tot["body"]), ("table", tot["table"])):
        err = t["D"] + t["S"] + t["I"]
        addr = t["aD"] + t["aS"] + t["aI"]
        res[name] = {**t, "now_readout": round(t["match"] / t["ref"], 6), "now_cer": round(err / t["ref"], 6),
                     "errors": err, "addressable": addr, "addressable_share": round(addr / err, 4) if err else None,
                     "ceiling_readout": round((t["match"] + t["aD"] + t["aS"]) / t["ref"], 6),
                     "ceiling_cer": round((err - addr) / t["ref"], 6)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "scores_hybrid.json"))
    args = ap.parse_args()
    bs = load_bs()
    variants, mpages = ref_variants(bs)
    b_doc = json.loads((FINAL / "scheme_b_standard" / "blind20-scan.json").read_text(encoding="utf-8"))
    systems = {
        "A": json.loads((FINAL / "scheme_a" / "pred_pages.json").read_text(encoding="utf-8")),
        "B_std": json.loads((FINAL / "scheme_b_standard" / "pred_pages.json").read_text(encoding="utf-8")),
    }
    assert pred_pages_from_doc(b_doc) == systems["B_std"], "B 中间 JSON 与 pred_pages 口径不一致"
    manifests, route_info = {}, {}
    for route in ("route1", "route2"):
        mp = OUT / f"manifest_{route}.json"
        if not mp.exists():
            continue
        man = json.loads(mp.read_text(encoding="utf-8"))
        manifests[route] = man["items"]
        rdir = OUT / "vlm" / "results" / route
        results = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in rdir.glob("*.json")} if rdir.exists() else {}
        n_ok = sum(1 for r in results.values() if r.get("ok"))
        route_info[route] = {"n_items": len(man["items"]), "n_results_ok": n_ok,
                             "vlm_calls": sum(1 for r in results.values() if r.get("ok") and not r.get("cache_hit")),
                             "vlm_cache_hits": sum(1 for r in results.values() if r.get("cache_hit")),
                             "vlm_usd": round(sum(float(r.get("usd") or 0) for r in results.values() if r.get("ok") and not r.get("cache_hit")), 6),
                             "vlm_usd_standalone": round(sum(float(r.get("usd") or 0) for r in results.values() if r.get("ok")), 6),
                             "vlm_sec_sum": round(sum(float(r.get("elapsed_sec") or 0) for r in results.values()), 2),
                             "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in results.values() if r.get("ok")),
                             "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in results.values() if r.get("ok"))}
        if n_ok == 0:
            route_info[route]["status"] = "no_vlm_results_yet（未外发，仅有清单）"
            continue
        from baidu_ocr.hybrid.rules import RULES, lowconf_only_inactive
        route_info[route]["thresholds"] = {}
        for thr in RULES["lowconf_eval_thresholds"]:
            odir = OUT / route / f"thr{thr:.2f}"
            if (odir / "assets").exists():
                shutil.rmtree(odir / "assets")
            odir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(FINAL / "scheme_b_standard" / "assets", odir / "assets")
            doc, pred, log = merge_route(b_doc, man["items"], results, ROOT, odir / "assets", lowconf_threshold=thr)
            doc["document"]["notes"] = (doc["document"].get("notes") or "") + f"; hybrid {route}: B 标准版打底 + OpenAI VLM（规则 {man['rules_version']}，低置信生效阈值 {thr}）"
            errs = validate_schema(doc, ROOT / "schemas" / "intermediate.schema.json")
            (odir / "blind20-scan.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            (odir / "blind20-scan.schema_errors.json").write_text(json.dumps(errs, ensure_ascii=False, indent=2), encoding="utf-8")
            (odir / "pred_pages.json").write_text(json.dumps(pred, ensure_ascii=False, indent=2), encoding="utf-8")
            (odir / "merge_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
            sdoc, _ = apply_to_document(doc)
            (odir / "blind20-scan.star.json").write_text(json.dumps(sdoc, ensure_ascii=False, indent=2), encoding="utf-8")
            used = [i for i in man["items"] if not lowconf_only_inactive(i, thr)]
            systems[f"{route}@{thr:.2f}"] = pred
            route_info[route]["thresholds"][f"{thr:.2f}"] = {"schema_errors": len(errs), "items_used": len(used),
                                                            "items_used_by_kind": dict(__import__("collections").Counter(i["kind"] for i in used))}
        route_info[route]["status"] = "merged"
    # ★/☆ 规则
    star_hits = {}
    for name in list(systems):
        systems[name + "+star"], star_hits[name] = apply_to_pred_pages(systems[name])
    out = {"star_rule": RULE_TEXT, "star_rule_hits_by_page": star_hits, "route_info": route_info, "variants": {}}
    for vname, (refp, corr) in variants.items():
        v = {}
        for name, pred in systems.items():
            s = bs.score_system(pred, refp, mpages, corr)
            v[name] = {"total": s["total"], "per_page": {str(k): {kk: s["per_page"][k][kk] for kk in ("overall", "body", "table", "seal")} for k in s["per_page"]}}
        out["variants"][vname] = v
    # 可触达错误上限（基于 B 标准版现有输出；修正参照）
    refp, corr = variants["corrected"]
    sb_doc, _ = apply_to_document(b_doc)
    out["addressable_upper_bound"] = {}
    for route, items in manifests.items():
        out["addressable_upper_bound"][route] = {"B_raw": addressable(bs, b_doc, refp, corr, items),
                                                 "B+star": addressable(bs, sb_doc, refp, corr, items)}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for vname, v in out["variants"].items():
        for name, s in v.items():
            t = s["total"]
            print(vname, name, {k: (t[k]["readout"], t[k]["cer"], t[k]["deletions"], t[k]["substitutions"], t[k]["insertions"], t[k]["ref_chars"]) for k in t})
    for route, d in out["addressable_upper_bound"].items():
        for k, r in d.items():
            o = r["overall"]
            print("upper", route, k, o["now_readout"], o["now_cer"], "addr", o["addressable"], "/", o["errors"], "->", o["ceiling_readout"], o["ceiling_cer"])


if __name__ == "__main__":
    main()
