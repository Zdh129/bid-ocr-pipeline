#!/usr/bin/env python3
"""synth18 新盲测集就绪检查（只读：不改 synth18/ 里任何文件，不联网，不跑 OCR）。

检查项：
  1. 目录、扫描版 PDF、参照文本是否存在（可用 --pdf/--ref 指定，否则自动找）；manifest.json（可选）。
  2. PDF 页数（= --expect-pages，默认 18）、每页尺寸；文字层：pdffonts 无字体、pdftotext 逐页为空（扫描版必须无文字层）。
  3. 参照：UTF-8（无 BOM、无 \\r）、分页标记 =====第N页=====（与 scripts/blind20_score.parse_ref 同一正则）、
     页号从 1 连续到 N 且 N = PDF 页数、首个标记前无正文、每页非空；【印章：…】/【?】括号成对；
     列出计分器不认识的【…】标记（会被当成正文字计分）、表格行数、【?】数量。
  4. 与现有计分的兼容性：用 blind20_score.parse_ref 实际解析一遍；run_score.py 的 SHA 前缀闸门与整体文本口径（见输出 notes）。
  5. 冻结清单 output/blindset2_prep/frozen_config_v2.json 是否与当前代码一致。
输出：标准输出 + --out（默认 output/blindset2_prep/synth18_ready_check.json）。退出码：0 就绪，1 有阻断项，2 目录不存在。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MARK = re.compile(r"^=====第(\d+)页=====\s*$")  # 与 blind20_score.parse_ref 一致
KNOWN_TAG = re.compile(r"【印章[:：].*?】|【\?】")


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sh(cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def pick(d: Path, pats, avoid=()) -> Path | None:
    c = [p for pat in pats for p in sorted(d.glob(pat)) if not any(a in p.name.lower() for a in avoid)]
    return c[0] if c else None


def check_pdf(pdf: Path, expect: int, R: dict):
    info = sh(["pdfinfo", str(pdf)])
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    n = int(m.group(1)) if m else 0
    R["pdf"] = {"path": str(pdf), "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "pages": n}
    if n != expect:
        R["blockers"].append(f"PDF 页数 {n} ≠ 预期 {expect}")
    sizes = []
    for p in range(1, n + 1):
        pi = sh(["pdfinfo", "-f", str(p), "-l", str(p), str(pdf)])
        mm = re.search(r"Page\s+\d+\s+size:\s+([\d.]+) x ([\d.]+)", pi)
        sizes.append([float(mm.group(1)), float(mm.group(2))] if mm else None)
    R["pdf"]["page_sizes_pt"] = sizes
    fonts = [l for l in sh(["pdffonts", str(pdf)]).splitlines()[2:] if l.strip()]
    text_pages = [p for p in range(1, n + 1) if sh(["pdftotext", "-f", str(p), "-l", str(p), "-layout", str(pdf), "-"]).strip()]
    R["pdf"]["fonts"] = len(fonts)
    R["pdf"]["pages_with_text_layer"] = text_pages
    if fonts or text_pages:
        R["blockers"].append(f"扫描版 PDF 仍有文字层（字体 {len(fonts)} 个，有文字的页 {text_pages}）：须按 200 DPI 渲染成图片再合成无文字层 PDF")


def check_ref(ref: Path, n_pdf: int, R: dict, bs):
    raw = ref.read_bytes()
    r = {"path": str(ref), "sha256": hashlib.sha256(raw).hexdigest()}
    R["reference"] = r
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        R["blockers"].append(f"参照不是 UTF-8：{e}")
        return
    if text.startswith("\ufeff"):
        R["warnings"].append("参照带 BOM（parse_ref 能处理首行标记前的 BOM 吗？首行若是标记会因 BOM 匹配失败）——建议去掉")
        if MARK.match(text.lstrip("\ufeff").splitlines()[0].strip()) and not MARK.match(text.splitlines()[0].strip()):
            R["blockers"].append("首行分页标记前有 BOM，parse_ref 会漏掉第 1 页")
    if "\r" in text:
        R["warnings"].append("参照含 \\r（Windows 换行）；splitlines 能处理，但建议统一为 \\n")
    lines = text.splitlines()
    marks, pre, bad_marks = [], [], []
    for i, l in enumerate(lines, 1):
        m = MARK.match(l.strip())
        if m:
            marks.append(int(m.group(1)))
        elif not marks and l.strip():
            pre.append(i)
        if "=====" in l and not m:
            bad_marks.append(i)
    r["page_marks"] = marks
    if pre:
        R["blockers"].append(f"第一个分页标记前有正文（第 {pre[:5]} 行），计分时会被丢弃")
    if bad_marks:
        R["blockers"].append(f"疑似写错的分页标记（含 ===== 但不符合「=====第N页=====」），行号 {bad_marks[:10]}")
    if marks != list(range(1, len(marks) + 1)):
        dup = [k for k, v in Counter(marks).items() if v > 1]
        R["blockers"].append(f"页号不是从 1 连续递增：{marks}" + (f"；重复 {dup}" if dup else ""))
    if len(marks) != n_pdf:
        R["blockers"].append(f"参照页数 {len(marks)} ≠ PDF 页数 {n_pdf}")
    # 用现有计分器真实解析
    pages = bs.parse_ref(text)
    per = {}
    unknown = Counter()
    for p, pg in sorted(pages.items()):
        body = sum(1 for k, _ in pg["lines"] if k == "body")
        table = sum(1 for k, _ in pg["lines"] if k == "table")
        s, _ = bs.ref_chars(pg)
        per[p] = {"body_lines": body, "table_lines": table, "seals": len(pg["seals"]), "scored_chars": len(s),
                  "unclear_marks": sum(l.count("【?】") for _, l in pg["lines"])}
        if not s and not pg["seals"]:
            R["blockers"].append(f"参照第 {p} 页为空")
        for _, l in pg["lines"]:
            for tag in re.findall(r"【[^】]*】", KNOWN_TAG.sub("", l)):
                unknown[tag[:20]] += 1
    r["per_page"] = per
    r["total_scored_chars"] = sum(v["scored_chars"] for v in per.values())
    opens, closes = text.count("【"), text.count("】")
    if opens != closes:
        R["blockers"].append(f"【】不成对：【 {opens} 个，】 {closes} 个")
    bad_seal = re.findall(r"【印章(?![:：])", text)
    if bad_seal:
        R["blockers"].append(f"印章标记缺冒号（应为【印章：…】）{len(bad_seal)} 处，计分器会当成正文")
    if unknown:
        R["warnings"].append(f"计分器不认识的【…】标记会按正文字计分：{dict(unknown.most_common(10))}")
    half_pipe = [i for i, l in enumerate(lines, 1) if "|" in l and " | " not in l and not re.search(r"\|\s*\|", l) and not l.strip().startswith("|")]
    if half_pipe:
        R["warnings"].append(f"含「|」但不是「 | 」分隔的行会被算作正文（行号 {half_pipe[:10]}）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/workspace/pdf-test/synth18")
    ap.add_argument("--pdf")
    ap.add_argument("--ref")
    ap.add_argument("--expect-pages", type=int, default=18)
    ap.add_argument("--out", default=str(ROOT / "output" / "blindset2_prep" / "synth18_ready_check.json"))
    a = ap.parse_args()
    d = Path(a.dir)
    R = {"checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dir": str(d), "expect_pages": a.expect_pages,
         "blockers": [], "warnings": [], "notes": []}
    if not d.is_dir():
        R["blockers"].append(f"目录不存在：{d}")
        print(json.dumps(R, ensure_ascii=False, indent=1))
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
        return 2
    before = {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in d.rglob("*") if p.is_file()}
    pdf = Path(a.pdf) if a.pdf else pick(d, ["*scan*.pdf", "*.pdf"], avoid=("textlayer", "text_layer", "原文"))
    ref = Path(a.ref) if a.ref else pick(d, ["*REFERENCE*.txt", "*reference*.txt", "*ref*.txt", "*参照*.txt", "*.txt"])
    man = d / "manifest.json"
    R["files"] = {"pdf": str(pdf) if pdf else None, "ref": str(ref) if ref else None, "manifest": str(man) if man.exists() else None,
                  "all": sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())}
    bs = load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    n_pdf = 0
    if not pdf:
        R["blockers"].append("找不到扫描版 PDF（可用 --pdf 指定）")
    else:
        check_pdf(pdf, a.expect_pages, R)
        n_pdf = R["pdf"]["pages"]
    if not ref:
        R["blockers"].append("找不到参照文本（可用 --ref 指定）")
    else:
        check_ref(ref, n_pdf or a.expect_pages, R, bs)
    if man.exists():
        m = json.loads(man.read_text(encoding="utf-8"))
        pages = m.get("pages") or []
        miss = [k for k in ("output_page", "doc_type") if pages and k not in pages[0]]
        if len(pages) != (n_pdf or a.expect_pages):
            R["warnings"].append(f"manifest.pages 条数 {len(pages)} ≠ 页数")
        if miss:
            R["warnings"].append(f"manifest.pages 缺字段 {miss}（bestconfig 计分按 output_page/doc_type 出分类型统计）")
    else:
        R["warnings"].append("没有 manifest.json：计分可跑，但没有按文档类型的分项（需要每页 output_page、doc_type）")
    # 冻结清单
    fz = load_mod("freeze_config_v2", ROOT / "scripts" / "freeze_config_v2.py")
    if fz.OUT.exists():
        bad = fz.verify()
        R["frozen_config_v2"] = {"path": str(fz.OUT), "intact": not bad, "diffs": bad}
        if bad:
            R["blockers"].append(f"冻结清单与当前代码不一致：{bad[:5]}")
    else:
        R["blockers"].append("缺冻结清单 output/blindset2_prep/frozen_config_v2.json")
    R["notes"] += [
        "计分兼容：页内计分走 scripts/blind20_score.py（parse_ref/score_system），其 REF/MANIFEST 路径写死为 frozen20；"
        "新集需由包装脚本把 bs.REF/bs.MANIFEST 指向 synth18（不改计分规则）。",
        "run_score.py 有 SHA 前缀闸门（config.yaml frozen20.reference_sha256_prefix，默认 da93f43f），新参照会被拒，除非加 --allow-constructed-ref；"
        "且它对 .txt 参照按整份文本计分、不按「=====第N页=====」分页，也不剥离【印章：…】和「 | 」——与 blind20_score 口径不同，不应用它出新集主分。",
        "blind20 用的人工参照修正（data/blind20/manual_reference_fixes.json、output/compare/reference_corrections.json）只适用于 blind20，新集不应套用。",
    ]
    after = {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in d.rglob("*") if p.is_file()}
    R["readonly_ok"] = before == after
    R["ready"] = not R["blockers"]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: R[k] for k in ("ready", "blockers", "warnings")}, ensure_ascii=False, indent=1))
    return 0 if R["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
