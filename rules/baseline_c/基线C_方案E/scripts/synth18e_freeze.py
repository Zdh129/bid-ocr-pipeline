#!/usr/bin/env python3
"""冻结「方案E」→ output/synth18e_prep/frozen_方案E.json（独占创建，已存在则拒绝；不覆盖任何旧清单；不联网，不打开 synth18d/synth18e）。
每个文件记 sha256，并注明它与哪份旧冻结清单里的 sha 一致（frozen_推荐组合 / synth18b / synth18c / frozen_config_v2）；
旧清单里都没有的只能是本次新写的脚手架（运行、计分、就绪检查、本脚本）和组合配置 config_方案E.yaml。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "synth18e_prep" / "frozen_方案E.json"
OLD = {"frozen_推荐组合": ROOT / "output" / "f1_round2" / "frozen_推荐组合.json", "synth18b": ROOT / "output" / "synth18b" / "frozen_synth18b.json",
       "synth18c": ROOT / "output" / "synth18c" / "frozen_synth18c.json", "frozen_config_v2": ROOT / "output" / "blindset2_prep" / "frozen_config_v2.json"}
GROUPS = {
    "组合代码": sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("src/baidu_ocr/**/*.py") if "__pycache__" not in p.parts) + [
        "scripts/f1r2.py", "scripts/synth18_combos.py", "scripts/synth18_preproc.py"],
    "组合配置": ["config_方案E.yaml", "config.yaml"],
    "百度调用与构建": ["scripts/run_scheme_b.py", "scripts/opt2_scheme_a_call.py", "scripts/synth18e_scheme_a_call.py", "scripts/opt2_reread.py",
                 "scripts/f1r2_vert_call.py", "scripts/blind20_build.py"],
    "v2 对照": ["output/blindset2_prep/frozen_config_v2.json", "scripts/synth18_run.py", "scripts/astra_run.py", "config_astra_v2_live.yaml"],
    "计分": ["scripts/blind20_score.py", "scripts/tolerant_score.py", "scripts/synth18b_score.py", "scripts/synth18b_run.py", "scripts/synth18_score.py",
           "scripts/bestconfig_run.py", "scripts/hybrid_score.py", "scripts/synth18_opt_phase2.py"],
    "synth18e 运行与计分脚手架": ["scripts/synth18e_run.py", "scripts/synth18e_score.py", "scripts/synth18e_freeze.py",
                           "scripts/synth18_ready_check.py", "scripts/ready_check_方案E.py"],
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    if OUT.exists():
        sys.exit(f"{OUT} 已存在：不覆盖")
    olds = {}
    for k, p in OLD.items():
        j = json.loads(p.read_text(encoding="utf-8"))
        olds[k] = j.get("code_sha256") or j.get("files_sha256") or {}
    files, origin, new = {}, {}, []
    for g, lst in GROUPS.items():
        for rel in lst:
            h = sha(ROOT / rel)
            files[rel] = h
            src = [k for k, d in olds.items() if d.get(rel) == h]
            origin[rel] = src
            if not src:
                new.append(rel)
    combo_new = [r for r in new if r in GROUPS["组合代码"]]
    if combo_new:
        sys.exit(f"组合代码里有旧清单都没冻结过的文件（或 sha 不同），不能只靠已冻结代码拼出方案E：{combo_new}")
    v = yaml.safe_load((ROOT / "config_方案E.yaml").read_text(encoding="utf-8"))["方案E"]["variant"]
    now = datetime.now(timezone.utc)
    doc = {"冻结时间_伦敦": now.strftime("%Y-%m-%d %H:%M:%S") + "（伦敦时间，当前 UTC+0）", "created_at": now.isoformat(timespec="seconds"),
           "名称": "方案E", "用途": "synth18e 留出集一次性验证（待用户批准外发后再跑）；冻结后不改代码、配置和脚本。新清单，不覆盖任何旧清单。",
           "组合": "底座方案F1（不加深度求索纠错）＋变体二（发票重排第三版）＋N1 甲（发票表头固定字段）＋N2（页边竖排）＋N4（发票金额核对）；去掉变体一乙、变体三",
           "variant": v, "llm": "none", "6.2": "方案E 不用；只在 v2 对照行里用，且须用户单独批准",
           "流水线": "image_fill → star(R1-R3) → fill_gaps(A+U) → invoice_reorder_v3 → reread → invoice_header(fields) → vertical_margin → invoice_check → star_fill",
           "输入": {"B": "文档解析标准版，原图", "A": "高精度识别，颜色去章图，强制高精度（另跑原图原走法，供页边竖排的渲染图、v2 对照）",
                  "U": "无限量识别，颜色去章图（v2 对照用原图）", "reread": "颜色去章图高精度识别的低置信度行", "vertical": "原图 200 DPI 渲染"},
           "计分": {"口径": ["主口径（容错，签名入参照）", "剔除签名口径", "严格口径"], "分版面": "manifest.json 的 doc_type，严格口径",
                  "对照": ["文档解析原始结果", "v2（6.2 未获批时报「v2 去掉 6.2」）", "方案F1（参考）"], "一次性": "output/synth18e/SCORED"},
           "分组": GROUPS, "来源核对": origin, "旧清单里没有的文件": new, "旧清单 sha256": {k: sha(p) for k, p in OLD.items()},
           "code_sha256": files}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "x", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1))
    os.chmod(OUT, 0o444)
    print("frozen", sha(OUT), len(files), "files；旧清单里没有的：", new)


if __name__ == "__main__":
    main()
