#!/usr/bin/env python3
"""synth18 优化第二步：预处理变体送百度文档解析 → 构建 → 与后处理组合本地穷举（batch2）。

  pdfs    --variants P2,P1_P2,...   把 300 DPI 变体图按原页面尺寸（pt）装成 PDF（不联网）
  quota                             按本地日志推算百度免费余量，并给 synth18b 预留（不联网）
  send    --variants ... [--live]   默认 dry-run；--live 才调 run_scheme_b.py（文档解析标准版；免费额度），逐个变体串行；
                                    开跑前与每个变体前都做额度检查（余量 − 本次 18 页 ≥ synth18b 预留），否则停
  build   --variants ...            原始返回 → 中间 JSON（blind20_build.build_scheme_b，坐标系与原扫描一致，A/Unlimited 复用原扫描的）
  batch2  --variants ...            每个变体 × 后处理组合穷举（vlm/LLM 结果只对应原 B，不适用于变体 → off/none）→ batch2.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
OPT = ROOT / "output" / "synth18_opt"
PRE = OPT / "preproc"
VAR_ROOT = OPT / "pre"
S18 = Path("/workspace/pdf-test/synth18")
PDF = S18 / "synth18-scan.pdf"
S18W = ROOT / "output" / "synth18" / "work"
LIVE_CFG = ROOT / "config_synth18opt_live.yaml"
# 本地日志推算的已用量（截至 synth18 那一轮结束；见 output/synth18/report.md §7）
USED = {"docparser_pages": 38, "accurate_calls": 47, "general_calls": 21}
FREE = {"docparser_pages": 200, "accurate_calls": 500, "general_calls": 1000}
RESERVE_S18B = {"docparser_pages": 36, "accurate_calls": 40}   # synth18b：文档解析 18 页 + 一次重跑余量；A 原走法约 19 次 + 重试余量


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def vlist(a):
    return [v.strip() for v in a.variants.split(",") if v.strip()]


def pdfs(a):
    import fitz
    src = fitz.open(PDF)
    for v in vlist(a):
        out = VAR_ROOT / v / f"synth18-{v}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        for i, pg in enumerate(src):
            img = PRE / v / f"p{i + 1:02d}.png"
            np_ = doc.new_page(width=pg.rect.width, height=pg.rect.height)
            if "P1" in v:
                np_.insert_image(np_.rect, filename=str(img))           # 二值图 PNG 压得很小
            else:
                import cv2
                ok, buf = cv2.imencode(".jpg", cv2.imread(str(img)), [cv2.IMWRITE_JPEG_QUALITY, 88])
                np_.insert_image(np_.rect, stream=buf.tobytes())
        doc.save(out, deflate=True)
        print(v, out.stat().st_size // 1024, "KB", len(doc), "页", sha(out)[:12])


def used_now():
    u = dict(USED)
    for d in sorted(VAR_ROOT.glob("*/work/output/scheme_b_standard/cost.json")):
        c = jl(d)
        u["docparser_pages"] += sum(int(x.get("units") or 0) for x in c.get("calls") or [] if str(x.get("api", "")).startswith("docparser") and x.get("success") and not x.get("dry_run"))
    for f in sorted((OPT / "s18b_used.json",)):
        if f.exists():
            for k, v in jl(f).items():
                u[k] = u.get(k, 0) + v
    return u


def quota(a=None):
    u = used_now()
    left = {k: FREE[k] - u[k] for k in FREE}
    print(json.dumps({"used": u, "left": left, "reserve_synth18b": RESERVE_S18B}, ensure_ascii=False))
    return u, left


def wdir(v):
    return VAR_ROOT / v / "work" / "output"


def send(a):
    vs = vlist(a)
    assert len(vs) <= 5
    _, left = quota()
    need = 18 * len([v for v in vs if not (wdir(v) / "scheme_b_standard" / "raw" / f"synth18-{v}.parse_result.json").exists()])
    if left["docparser_pages"] - need < RESERVE_S18B["docparser_pages"]:
        sys.exit(f"额度不足：文档解析余 {left['docparser_pages']}，本次 {need}，需给 synth18b 留 {RESERVE_S18B['docparser_pages']}，停止")
    for v in vs:
        pdf = VAR_ROOT / v / f"synth18-{v}.pdf"
        out = wdir(v) / "scheme_b_standard"
        if (out / "raw" / f"synth18-{v}.parse_result.json").exists():
            print(v, "已有结果，跳过")
            continue
        _, left = quota()
        if left["docparser_pages"] - 18 < RESERVE_S18B["docparser_pages"]:
            sys.exit(f"{v}: 余量不够给 synth18b 预留，停止")
        cmd = [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "run_scheme_b.py"), "--config", str(LIVE_CFG),
               "--input", str(pdf), "--out", str(out), "--edition", "standard"]
        if not a.live:
            print("[dry]", v, " ".join(cmd + ["--allow-external"]), f"(18 页，{pdf.stat().st_size // 1024} KB)")
            continue
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(cmd + ["--allow-external"], capture_output=True, text=True, cwd=ROOT)
        log = re.sub(r"access_token=[^&\s\"']+", "access_token=***REDACTED***", r.stdout + "\n" + r.stderr)
        (VAR_ROOT / v / "send.log").write_text(log, encoding="utf-8")
        print(v, "exit", r.returncode, log[-400:])
        if r.returncode != 0:
            sys.exit(f"{v} 失败，停止")


def build(a):
    import synth18_run as S
    bm = S.load_mod("blind20_build", ROOT / "scripts" / "blind20_build.py")
    a_sum = jl(S18W / "output" / "scheme_a" / "call_summary.json")
    for v in vlist(a):
        W = wdir(v)
        d = W / "scheme_b_standard"
        shutil.copyfile(d / "raw" / f"synth18-{v}.parse_result.json", d / "raw" / "blind20-scan.parse_result.json")
        shutil.copyfile(d / f"synth18-{v}.raw.json", d / "blind20-scan.raw.json")
        bm.OUT = W
        bm.SRC_NAME = f"synth18-{v}.pdf"
        bm.build_scheme_b(d, W / "final" / "scheme_b_standard", "standard", a_sum)
        print(v, "built")


def batch2(a):
    import synth18_combos as C
    from baidu_ocr.postprocess.aux import load_aux
    allrows = {}
    for v in vlist(a):
        b_doc = jl(wdir(v) / "final" / "scheme_b_standard" / "blind20-scan.json")
        aux = load_aux(S18W, b_doc)   # A 字框与 Unlimited 都来自原扫描（同一页面坐标系）
        rows = C.evaluate(b_doc, aux, None, False, ["off"], ["none"])
        for r in rows:
            r["variant"] = v
        allrows[v] = rows
    (OPT / "batch2_rows.json").write_text(json.dumps(allrows, ensure_ascii=False, indent=0), encoding="utf-8")
    for v, rows in allrows.items():
        print(v, rows[0]["name"], rows[0]["full"]["overall"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pdfs", "quota", "send", "build", "batch2"])
    ap.add_argument("--variants", default="")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    globals()[a.cmd](a)
