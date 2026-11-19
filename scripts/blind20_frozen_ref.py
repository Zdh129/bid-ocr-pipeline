#!/usr/bin/env python3
"""blind20 计分用参照结构（只用于计分；不改参照文件本身，不进识别规则）。

把以前 blind20「修正参照（主）」的修正方式（manual_reference_fixes.json 的整页重写、规范化串上的唯一替换、印章替换，
以及 output/compare/reference_corrections.json 的按位置修正）原样应用到 frozen20/CHATGPT-REFERENCE-BLIND20-NEW.txt 上，
并保留每个字所在的参照行，输出成 tolerant_score.py 需要的格式：
  output/blind20_frozen/计分参照/blind-ref.txt（=====第N页===== 分页、逐行；印章以【印章：…】单独一行；表格行行尾加「 | 」以保持表格分区）
  output/blind20_frozen/计分参照/blind-ref.json（pages[].ref_lines / blocks；blocks 只标 invoice_vertical，规则见下）
  output/blind20_frozen/计分参照/manifest.json（frozen20/manifest.json 的 pages 原样复制）
blocks：blind20 参照没有收录手写签名（手写处写成【?】、不计分），所以不标 handwriting_sign（主口径与剔除签名口径在参照侧相同）；
发票页上参照里单独成行的「下载次数」类页边竖排注记标为 invoice_vertical（按行文字匹配 ^下载次数[:：]?\\d+$）。
自检：在这份结构上用 blind20_score.score_system 严格计分，应与以前 bestconfig / bestconfig_v2 的「修正参照」结果逐项一致。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
OUTD = ROOT / "output" / "blind20_frozen" / "计分参照"
REF = Path("/workspace/pdf-test/frozen20/CHATGPT-REFERENCE-BLIND20-NEW.txt")
MAN = Path("/workspace/pdf-test/frozen20/manifest.json")
VERT = re.compile(r"^下载次数[:：]?\d+$")


def main():
    import bestconfig_run as bc
    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    text = REF.read_text(encoding="utf-8")
    manual = json.loads((ROOT / "data" / "blind20" / "manual_reference_fixes.json").read_text(encoding="utf-8"))
    rw = {int(k): v for k, v in (manual.get("page_rewrites") or {}).items()}
    corr = [c for c in json.loads((ROOT / "output" / "compare" / "reference_corrections.json").read_text(encoding="utf-8"))["corrections"] if c["page"] not in rw]
    refp = bs.parse_ref(text)
    out_txt, rj = [], {"pages": []}
    for p in sorted(refp):
        rp = bs.parse_ref("=====第%d页=====\n" % p + rw[p]["text"])[p] if p in rw else refp[p]
        chars, lid = [], []
        kinds = []
        for i, (kind, line) in enumerate(rp["lines"]):
            n = bs.norm(line)
            chars += list(n)
            lid += [i] * len(n)
            kinds.append(kind)
        if p not in rw:
            for c in sorted([c for c in corr if c["page"] == p], key=lambda c: -c["ref_start"]):
                a, b = c["ref_start"], c["ref_end"]
                assert "".join(chars[a:b]) == bs.norm(c["ref"]), (p, c)
                li = lid[a] if a < len(lid) else (lid[a - 1] if a > 0 else 0)
                new = list(bs.norm(c["new"]))
                chars[a:b] = new
                lid[a:b] = [li] * len(new)
        for fx in manual.get("substring_fixes") or []:
            if fx["page"] != p:
                continue
            s = "".join(chars)
            old, new = bs.norm(fx["old"]), bs.norm(fx["new"])
            if s.count(old) != 1:
                continue
            i = s.index(old)
            li = lid[i]
            chars[i:i + len(old)] = list(new)
            lid[i:i + len(old)] = [li] * len(new)
        seals = (manual.get("seal_fixes") or {}).get(str(p), {}).get("seals") if str(p) in (manual.get("seal_fixes") or {}) else rp["seals"]
        lines = []
        for i, kind in enumerate(kinds):
            t = "".join(ch for ch, l in zip(chars, lid) if l == i)
            if t:
                lines.append(t + (" | " if kind == "table" else ""))
        if seals:
            lines.append("".join(f"【印章：{s}】" for s in seals))
        ref_lines = [{"n": k + 1, "text": t} for k, t in enumerate(lines)]
        blocks = [{"type": "invoice_vertical", "text": l["text"], "ref_line": l["n"]} for l in ref_lines if VERT.match(l["text"].strip())]
        rj["pages"].append({"page": p, "ref_lines": ref_lines, "blocks": blocks})
        out_txt.append(f"=====第{p}页=====\n" + "".join(l["text"] + "\n" for l in ref_lines))
    OUTD.mkdir(parents=True, exist_ok=True)
    (OUTD / "blind-ref.txt").write_text("".join(out_txt), encoding="utf-8")
    (OUTD / "blind-ref.json").write_text(json.dumps(rj, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUTD / "manifest.json").write_text(json.dumps({"pages": json.loads(MAN.read_text(encoding="utf-8"))["pages"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("pages", len(rj["pages"]), "vertical blocks", sum(len(p["blocks"]) for p in rj["pages"]))


if __name__ == "__main__":
    main()
