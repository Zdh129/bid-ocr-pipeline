#!/usr/bin/env python3
"""从「方案 A 与方案 B 标准版一致、但与参照不同」的差异段生成参照修正清单。
只收录已对照原图（本地裁图、用 Read 目视）核实过的页；手写签名等不确定处不收。不改参照文件本身。"""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
cons = json.loads((ROOT / "output/compare/consensus_spans.json").read_text(encoding="utf-8"))
VERIFIED = {
    1: "p01.png", 2: "p02_tables.png + 整页缩略图", 5: "p05.png", 6: "p06.png", 7: "p07a.png/p07b.png", 9: "p9a.png/p9b.png",
    10: "p10.png", 11: "p11a.png/p11b.png", 12: "p12.png", 13: "p13_top.png", 14: "p14a.png/p14b.png", 15: "p15.png/p15b.png",
    16: "p16.png", 17: "p17.png", 18: "整页缩略图 p18", 19: "p19_buyer.png/p19b.png + 整页缩略图", 20: "p20.png",
}
EXCLUDE = []  # (page, predicate)


def excluded(s):
    if s["page"] == 3:
        return "第3页手写签名「周浪」位置/识别不确定，不修正"
    if s["page"] not in VERIFIED:
        return "该页未逐段核对"
    return None


KEY = {(2, "差全自动热值仪"): "可疑点①：原图为「实验桌」（参照「实验差」）",
       (2, "差"): "可疑点①：原图为「实验凳」（参照「实验差」）",
       (13, "氧"): "可疑点②：原图为「氢气发生器」/「氢气」（参照写成氧）",
       (19, "习"): "可疑点③：原图买方为「贵州茅台酒股份有限公司」（参照写成习酒）"}
out, skipped = [], []
for s in sorted(cons, key=lambda s: (s["page"], s["ref_start"])):
    why = excluded(s)
    if why:
        skipped.append({**s, "skip_reason": why})
        continue
    new = s["pred"]
    star_note = ""
    if "☆" in new:
        # 原图是实心 ★，百度两条方案都读成空心 ☆：这是识别错误，不算参照错误，修正时保留 ★
        new = new.replace("☆", "★")
        star_note = "（☆ 按原图保留为 ★）"
    if new == s["ref"]:
        skipped.append({**s, "skip_reason": "仅 ★/☆ 差异，原图为 ★，参照正确"})
        continue
    out.append({"page": s["page"], "section": s["section"], "ref_start": s["ref_start"], "ref_end": s["ref_end"],
                "ref": s["ref"], "new": new, "context": s["context"], "cost": s["cost"],
                "evidence": f"本地裁图 output/compare/verify/{VERIFIED[s['page']]}，目视核对原图与方案A/B一致",
                "note": KEY.get((s["page"], s["ref"]), "") + star_note})
res = {"method": "A 与 B 标准版逐页对齐到参照后，两者给出相同替换的差异段；仅收录已对照原图核实的页。按规范化（NFKC、去空白）参照串的字符区间替换。参照文件本身未改动。",
       "bias_note": "修正项来自 A 与 B 标准版的一致结果，修正后分数对这两条天然有利；B Unlimited 只在与它们一致处受益。",
       "corrections": out, "skipped": skipped,
       "n": len(out), "chars_removed": sum(len(c["ref"]) for c in out), "chars_added": sum(len(c["new"]) for c in out)}
(ROOT / "output/compare/reference_corrections.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(res["n"], "corrections;", len(skipped), "skipped;", res["chars_removed"], "->", res["chars_added"])
