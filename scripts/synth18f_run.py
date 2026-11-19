#!/usr/bin/env python3
"""synth18f 留出集一次性验证（配置甲；由 scripts/synth18d_run.py 复制改写；产出放 output/synth18f/）。只调用冻结代码，不含识别规则。

配置甲 = 方案F1（不加纠错）＋变体一乙（只删整块）＋变体二（发票重排第三版）＋N2（页边竖排）＋N4（发票金额核对）；
关闭变体三、N1 甲／乙、N3、任何大模型纠错。开关只来自 config_配置甲.yaml。
同一次计分对照：文档解析原始结果、方案E、推荐组合去掉变体三（不含纠错；synth18f 若另获批深度求索并有缓存，再加含纠错版）。

  verify            核对配置甲清单＋推荐组合清单＋方案E 清单、组合开关、生产配置 sha、config.yaml 不外发、输入记录
  prep              （须先有用户批准后建的 config_synth18f_live.yaml）核对扫描件 sha → 输入记录 → 颜色去章图 PDF（不联网）
  baidu [--live]    文档解析（原图）＋无限量识别（原图、颜色去章图）＋高精度识别（原图原走法；颜色去章图强制高精度）
  build             原始返回 → 中间 JSON（不联网）
  reread [--live]   二次识别
  seal [--live]     变体一乙的印章混入去红重识
  vert [--live]     N2 页边竖排识别
  score             一次性计分（scripts/synth18f_score.py）
外发只有百度（全部页及派生图），且须用户本人批准；不用深度求索、gpt-4o、6.2。日志不写任何密钥或访问令牌。
计分之前只打开扫描件；全部版本输出生成并记 sha 之后才打开参照与 manifest。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

S18F = Path("/workspace/pdf-test/synth18f")
OUT = ROOT / "output" / "synth18f"
FROZEN = ROOT / "output" / "配置甲" / "frozen_配置甲.json"
FROZEN_REC = ROOT / "output" / "f1_round2" / "frozen_推荐组合.json"
FROZEN_E = ROOT / "output" / "synth18e_prep" / "frozen_方案E.json"
COMBO_CFG = ROOT / "config_方案E.yaml"
JIA_CFG = ROOT / "config_配置甲.yaml"
JIA = {"seal": "whole", "inv": "v3", "vert": True, "check": True}
BAIDU_CFG = ROOT / "config_synth18f_live.yaml"          # 用户批准后才建（含批准原话、扫描件路径与 sha、付费基数）
REF_SHA_EXPECTED = None   # 批准时由用户给出，写进 config_synth18f_live.yaml 的 synth18f.reference_sha256_expected
SCAN_SHA = None           # 同上：synth18f.scan_sha256
V2_FROZEN = ROOT / "output" / "blindset2_prep" / "frozen_config_v2.json"
PROD_SHA = "a34c9d238a22214c1495ac115be2515db09f84bd13982380c446a63a4c91eaf3"
WO = OUT / "orig" / "work"
WP = OUT / "P2" / "work"
VLM = None   # 6.2 未获批
INPUT_REC = OUT / "输入记录.json"
EXPECTED = {"inv": "v3", "hdr": "fields", "vert": True, "check": True}   # 方案E（参考行）
RECOMMENDED = {"seal": "whole", "inv": "v3", "starf": True, "hdr": "fields", "vert": True, "check": True}
KNOWN_KEYS = {"seal", "inv", "starf", "hdr", "vert", "vmark", "guard", "check"}


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(s):
    s = re.sub(r"access_token=[^&\s\"']+", "access_token=***", s)
    s = re.sub(r"(authorization=)[^\"&) \\]+", r"\1***", s, flags=re.I)
    return re.sub(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+", r"\1***", s)


def variant():
    c = yaml.safe_load(COMBO_CFG.read_text(encoding="utf-8"))
    assert c["allow_external"] is False
    v = c["方案E"]["variant"]
    assert set(v) <= KNOWN_KEYS, f"未知开关 {set(v) - KNOWN_KEYS}"
    assert c["方案E"]["llm"] == "none"
    return v


def variant_jia():
    c = yaml.safe_load(JIA_CFG.read_text(encoding="utf-8"))
    assert c["allow_external"] is False and c["配置甲"]["llm"] == "none"
    v = c["配置甲"]["variant"]
    assert set(v) <= KNOWN_KEYS, f"未知开关 {set(v) - KNOWN_KEYS}"
    return v


def verify(a=None):
    fz = jl(FROZEN)
    bad = [c for c, h in fz["code_sha256"].items() if not (ROOT / c).exists() or sha(ROOT / c) != h]
    for lst in (FROZEN_REC, FROZEN_E):
        bad += [c for c, h in jl(lst)["code_sha256"].items() if not (ROOT / c).exists() or sha(ROOT / c) != h]
    if bad:
        sys.exit(f"冻结校验失败：{sorted(set(bad))}")
    if variant_jia() != JIA or fz["variant"] != JIA or jl(FROZEN_REC)["variant"] != RECOMMENDED or variant() != EXPECTED:
        sys.exit("冻结校验失败：组合开关与冻结不同")
    assert sha(ROOT.parent / "ocr" / "config.yaml") == PROD_SHA, "生产配置被改过"
    assert yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["allow_external"] is False
    if INPUT_REC.exists():
        rec = jl(INPUT_REC)
        assert sha(rec["scan_pdf"]) == rec["scan_sha256"]
    return fz


def live_cfg():
    if not BAIDU_CFG.exists():
        sys.exit(f"缺 {BAIDU_CFG.name}：synth18f 外发需用户本人批准后再建（写入批准原话、扫描件路径与 sha、付费基数），现在不跑")
    c = yaml.safe_load(BAIDU_CFG.read_text(encoding="utf-8"))
    s = c.get("synth18f") or {}
    assert c.get("allow_external") is True and s.get("approval"), "live 配置须 allow_external: true 且写明批准原话"
    assert Path(s["scan_pdf"]).parent == S18F
    return c, s


# ------------------------------------------------------------------ prep
def prep(a):
    verify()
    _, s = live_cfg()
    pdf = Path(s["scan_pdf"])
    assert sha(pdf) == s["scan_sha256"], "扫描件 sha 与 live 配置不符"
    if INPUT_REC.exists():
        assert jl(INPUT_REC)["scan_sha256"] == s["scan_sha256"]
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        with open(INPUT_REC, "x", encoding="utf-8") as f:
            f.write(json.dumps({"scan_pdf": str(pdf), "scan_sha256": s["scan_sha256"], "recorded_at": now(),
                                "baidu_cfg_sha256": sha(BAIDU_CFG)}, ensure_ascii=False, indent=1))
    import cv2
    import fitz
    import synth18_preproc as P
    src = fitz.open(pdf)
    (OUT / "P2" / "img").mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i, pg in enumerate(src):
        pix = pg.get_pixmap(dpi=300)
        op = OUT / "P2" / "img" / f"orig_p{i + 1:02d}.png"
        pix.save(op)
        img = P.p2(cv2.imread(str(op)))
        cv2.imwrite(str(OUT / "P2" / "img" / f"p{i + 1:02d}.png"), img)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        np_ = doc.new_page(width=pg.rect.width, height=pg.rect.height)
        np_.insert_image(np_.rect, stream=buf.tobytes())
    out = OUT / "P2" / "synth18f-P2.pdf"
    doc.save(out, deflate=True)
    wj(OUT / "P2" / "prep.json", {"p2_pdf_sha256": sha(out), "pages": len(doc), "at": now()})
    print("颜色去章图 PDF", len(doc), "页", out.stat().st_size // 1024, "KB")


# ------------------------------------------------------------------ 百度账本
def ledger():
    _, s = live_cfg()
    left0 = s["free_left_before"]
    calls = {"docparser_standard_pages": 0, "accurate": 0, "general": 0, "unlimited_pages": 0}
    unl = 0.0
    for f in sorted(OUT.glob("**/cost.json")):
        for x in jl(f).get("calls") or []:
            if not x.get("success") or x.get("dry_run"):
                continue
            api = x["api"]
            if api == "docparser_standard":
                calls["docparser_standard_pages"] += int(x.get("units") or 0)
            elif api == "docparser_unlimited":
                calls["unlimited_pages"] += int(x.get("units") or 0)
                unl += float(x.get("cny") or 0)
            elif api in ("accurate", "general"):
                calls[api] += 1
    for f in sorted(OUT.glob("**/cost_note.json")):
        n = jl(f)
        calls["unlimited_pages"] += int(n.get("billed_pages") or 0)
        unl += float(n.get("cny_list_estimate") or 0)
    used = {"docparser_pages": calls["docparser_standard_pages"], "accurate_calls": calls["accurate"], "general_calls": calls["general"]}
    over = {k: max(0, used[k] - left0[k]) for k in used}
    per = {"docparser_pages": 0.05, "accurate_calls": 0.04, "general_calls": 0.01}
    paid = round(sum(over[k] * per[k] for k in over) + unl, 4)
    return {"calls": calls, "free_left": {k: left0[k] - min(used[k], left0[k]) for k in left0}, "paid_cny_this_round": paid,
            "paid_cumulative_cny": round(float(s["paid_before_cny"]) + paid, 4), "cap_cny": float(s["paid_cap_cny_cumulative"])}


def check_cap(worst):
    led = ledger()
    if led["paid_cumulative_cny"] + worst > led["cap_cny"]:
        sys.exit(f"累计付费 ¥{led['paid_cumulative_cny']} + 本步最坏 ¥{worst} > ¥{led['cap_cny']}，停止")
    return led


def run_logged(cmd, log):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    Path(log).write_text(redact(r.stdout + "\n" + r.stderr), encoding="utf-8")
    return r.returncode, redact(r.stdout)


def pages_n():
    return int(jl(OUT / "P2" / "prep.json")["pages"])


def baidu(a):
    verify()
    _, s = live_cfg()
    assert INPUT_REC.exists(), "先跑 prep"
    pdf = Path(jl(INPUT_REC)["scan_pdf"])
    py = str(ROOT / ".venv" / "bin" / "python")
    p2 = OUT / "P2" / "synth18f-P2.pdf"
    assert sha(p2) == jl(OUT / "P2" / "prep.json")["p2_pdf_sha256"]
    n = pages_n()
    cfg = str(BAIDU_CFG)
    steps = [
        ("文档解析_原图", WO / "output" / "scheme_b_standard",
         [py, "scripts/run_scheme_b.py", "--config", cfg, "--input", str(pdf), "--out", str(WO / "output" / "scheme_b_standard"), "--edition", "standard"], 0.05 * n),
        ("无限量识别_原图", WO / "output" / "scheme_b_unlimited",
         [py, "scripts/run_scheme_b.py", "--config", cfg, "--input", str(pdf), "--out", str(WO / "output" / "scheme_b_unlimited"), "--edition", "unlimited"], 0.02 * n),
        ("无限量识别_颜色去章图", WP / "output" / "scheme_b_unlimited",
         [py, "scripts/run_scheme_b.py", "--config", cfg, "--input", str(p2), "--out", str(WP / "output" / "scheme_b_unlimited"), "--edition", "unlimited"], 0.02 * n),
        ("高精度识别_原图原走法", WO / "output" / "scheme_a",
         [py, "scripts/synth18f_scheme_a_call.py", "--config", cfg, "--out", str(WO / "output" / "scheme_a")], 0.08 * n),
        ("高精度识别_颜色去章图", WP / "output" / "scheme_a",
         [py, "scripts/synth18f_scheme_a_call.py", "--config", cfg, "--out", str(WP / "output" / "scheme_a"), "--input", str(p2), "--force-api", "accurate"], 0.08 * n),
    ]
    for name, out, cmd, worst in steps:
        done = (out / "call_summary.json").exists() if "高精度" in name else ((out / "raw").exists() and any((out / "raw").glob("*.parse_result.json")))
        if done:
            print(name, "已完成，跳过")
            continue
        led = check_cap(worst)
        if not a.live:
            print("[预演]", name, "最坏付费 ¥%.2f" % worst, "| 账本", json.dumps(led, ensure_ascii=False))
            continue
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        rc, _ = run_logged(cmd + ["--allow-external"], OUT / "日志" / f"百度_{name}.log")
        if rc != 0 and "无限量" in name and (out / "raw").exists() and any((out / "raw").glob("*.parse_result.json")):
            raw = next(p for p in out.glob("*.raw.json"))
            wj(out / "cost_note.json", {"note": "调用成功、原始返回已落盘；本地构建中间 JSON 失败，未写 cost.json。按 billed_pages 记账，不重发。",
                                        "api": "docparser_unlimited", "billed_pages": jl(raw).get("billed_pages"),
                                        "cny_list_estimate": round(0.02 * int(jl(raw).get("billed_pages") or 0), 4)})
            rc = 0
        print(name, "exit", rc, f"{time.time() - t0:.1f}s", json.dumps(ledger(), ensure_ascii=False))
        if rc != 0:
            sys.exit(f"{name} 失败，停止")


def build(a):
    verify()
    import synth18_run as S
    bm = S.load_mod("blind20_build", ROOT / "scripts" / "blind20_build.py")
    for root, eds in ((WO, ("scheme_b_standard", "scheme_b_unlimited")), (WP, ("scheme_b_unlimited",))):
        for ed in eds:
            d = root / "output" / ed
            stem = next(p.name.replace(".parse_result.json", "") for p in (d / "raw").glob("*.parse_result.json") if not p.name.startswith("blind20"))
            shutil.copyfile(d / "raw" / f"{stem}.parse_result.json", d / "raw" / "blind20-scan.parse_result.json")
            if (d / f"{stem}.raw.json").exists():
                shutil.copyfile(d / f"{stem}.raw.json", d / "blind20-scan.raw.json")
            for f in (d / "raw").glob("*"):   # 百度临时签名链接不落盘
                if f.suffix in (".json", ".md"):
                    f.write_text(redact(f.read_text(encoding="utf-8")), encoding="utf-8")
    W = WO / "output"
    bm.OUT = W
    bm.SRC_NAME = Path(jl(INPUT_REC)["scan_pdf"]).name
    a_sum = jl(W / "scheme_a" / "call_summary.json")
    bm.build_scheme_a(W / "scheme_a", W / "final" / "scheme_a", "routed")
    bm.build_scheme_b(W / "scheme_b_standard", W / "final" / "scheme_b_standard", "standard", a_sum)
    bm.build_scheme_b(W / "scheme_b_unlimited", W / "final" / "scheme_b_unlimited", "unlimited", a_sum)
    print("built")


def b_doc_path():
    return WO / "output" / "final" / "scheme_b_standard" / "blind20-scan.json"


def _sub(a, name, cmd, worst, done_file):
    verify()
    if Path(done_file).exists():
        print(name, "已完成，跳过")
        return
    led = check_cap(worst)
    if a.live:
        cmd = cmd + ["--live", "--allow-external"]
    rc, so = run_logged(cmd, OUT / "日志" / f"{name}.log")
    print(name, "exit", rc, so.strip().splitlines()[0] if so.strip() else "", "| 账本", json.dumps(ledger() if a.live else led, ensure_ascii=False))
    if rc != 0:
        sys.exit(f"{name} 失败，停止")


def reread_calls(a):
    live_cfg()
    py = str(ROOT / ".venv" / "bin" / "python")
    out = OUT / "reread" / "A_P2"
    _sub(a, "二次识别", [py, "scripts/opt2_reread.py", "--a-dir", str(WP / "output" / "scheme_a"), "--b-doc", str(b_doc_path()), "--out", str(out),
                      "--config", str(BAIDU_CFG), "--label", "synth18f-reread"], 0.04 * pages_n(), out / "results.json")


def vert(a):
    live_cfg()
    py = str(ROOT / ".venv" / "bin" / "python")
    out = OUT / "synth18f" / "vertical"
    _sub(a, "页边竖排识别", [py, "scripts/f1r2_vert_call.py", "--b-doc", str(b_doc_path()), "--render-dir", str(WO / "output" / "scheme_a" / "renders"),
                       "--out", str(out), "--config", str(BAIDU_CFG), "--label", "synth18f-vertical"], 0.12 * pages_n(), out / "results.json")


# ------------------------------------------------------------------ v2 对照（6.2）
def v2_verify():
    """v2 用的是 frozen_config_v2.json 冻结的那套代码：逐文件核对其中每个 sha（新组合加的新文件不影响 v2，单独列出）。"""
    fz2 = jl(V2_FROZEN)
    bad = [r for r, h in fz2["files_sha256"].items() if not (ROOT / r).exists() or sha(ROOT / r) != h]
    if bad:
        sys.exit(f"v2 冻结代码不一致：{bad}")
    return fz2


def seal(a):
    live_cfg()
    py = str(ROOT / ".venv" / "bin" / "python")
    out = OUT / "synth18f" / "seal_leak"
    _sub(a, "印章混入去红重识", [py, "scripts/f1r2_seal_call.py", "--b-doc", str(b_doc_path()), "--render-dir", str(WO / "output" / "scheme_a" / "renders"),
                         "--out", str(out), "--config", str(BAIDU_CFG), "--label", "synth18f-sealleak"], 0.04 * pages_n(), out / "results.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "prep", "baidu", "build", "reread", "seal", "vert", "score"])
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.cmd == "score":
        import synth18f_score
        synth18f_score.main()
    else:
        {"verify": lambda a: print("冻结清单校验通过", sha(FROZEN)) if verify() else None, "prep": prep, "baidu": baidu, "build": build,
         "reread": reread_calls, "vert": vert, "seal": seal}[a.cmd](a)
