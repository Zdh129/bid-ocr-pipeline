#!/usr/bin/env python3
"""冻结「配置甲」→ output/配置甲/frozen_配置甲.json（独占创建；不覆盖任何旧清单；不联网，不碰 synth18f）。
组合代码逐文件核对与旧清单（frozen_推荐组合／frozen_方案E／synth18d／synth18b／synth18c／frozen_config_v2）里的 sha 一致；
组合代码里出现旧清单都没冻结过的文件就拒绝（说明不能只靠已冻结代码拼出配置甲）。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "配置甲" / "frozen_配置甲.json"
OLD = {"frozen_推荐组合": "output/f1_round2/frozen_推荐组合.json", "frozen_方案E": "output/synth18e_prep/frozen_方案E.json",
       "frozen_synth18d": "output/synth18d/frozen_synth18d.json", "synth18b": "output/synth18b/frozen_synth18b.json",
       "synth18c": "output/synth18c/frozen_synth18c.json", "frozen_config_v2": "output/blindset2_prep/frozen_config_v2.json"}
GROUPS = {
    "组合代码": sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("src/baidu_ocr/**/*.py") if "__pycache__" not in p.parts) + [
        "scripts/f1r2.py", "scripts/synth18_combos.py", "scripts/synth18_preproc.py"],
    "组合配置": ["config_配置甲.yaml", "config_方案E.yaml", "config.yaml"],
    "百度调用与构建": ["scripts/run_scheme_b.py", "scripts/opt2_scheme_a_call.py", "scripts/synth18f_scheme_a_call.py", "scripts/opt2_reread.py",
                 "scripts/f1r2_vert_call.py", "scripts/f1r2_seal_call.py", "scripts/blind20_build.py", "scripts/synth18_run.py"],
    "计分": ["scripts/jia_eval.py", "scripts/blind20_score.py", "scripts/tolerant_score.py", "scripts/synth18b_score.py", "scripts/synth18b_run.py",
           "scripts/synth18_score.py", "scripts/bestconfig_run.py", "scripts/hybrid_score.py", "scripts/synth18_opt_phase2.py",
           "output/blindset2_prep/frozen_config_v2.json"],
    "就绪检查": ["scripts/synth18_ready_check.py", "scripts/ready_check_配置甲.py"],
    "synth18f 运行与计分": ["scripts/synth18f_run.py", "scripts/synth18f_score.py"],
    "开发成绩与本脚本": ["scripts/配置甲_开发成绩.py", "scripts/配置甲_freeze.py"],
    "参考行依赖的冻结清单": ["output/f1_round2/frozen_推荐组合.json", "output/synth18e_prep/frozen_方案E.json"],
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    if OUT.exists():
        sys.exit(f"{OUT} 已存在：不覆盖")
    olds = {}
    for k, p in OLD.items():
        j = json.loads((ROOT / p).read_text(encoding="utf-8"))
        olds[k] = j.get("code_sha256") or j.get("files_sha256") or {}
    files, origin = {}, {}
    for lst in GROUPS.values():
        for rel in lst:
            h = sha(ROOT / rel)
            files[rel] = h
            origin[rel] = [k for k, d in olds.items() if d.get(rel) == h]
    combo_new = [r for r in GROUPS["组合代码"] if not origin[r]]
    if combo_new:
        sys.exit(f"组合代码里有旧清单都没冻结过的文件（或 sha 不同），不能只靠已冻结代码拼出配置甲：{combo_new}")
    v = yaml.safe_load((ROOT / "config_配置甲.yaml").read_text(encoding="utf-8"))["配置甲"]["variant"]
    now = datetime.now(timezone.utc)
    doc = {"冻结时间_伦敦": now.strftime("%Y-%m-%d %H:%M:%S"), "created_at": now.isoformat(timespec="seconds"), "名称": "配置甲",
           "用途": "配置甲开发成绩（synth18／18c／18d／18e，非盲）与 synth18f 留出集一次性验证（待用户批准外发后再跑）；冻结后不改代码、配置、脚本",
           "组合": "方案F1（不加纠错）＋变体一乙（只删整块）＋变体二（发票重排第三版）＋N2（页边竖排）＋N4（金额核对）；关闭变体三、N1 甲／乙、N3、任何大模型纠错",
           "variant": v, "llm": "none",
           "流水线": "image_fill → seal_leak(whole) → star(R1-R3) → fill_gaps(A+U) → invoice_reorder_v3 → reread → vertical_margin → invoice_check → star_fill",
           "计分": {"口径": ["主口径", "剔除签名口径", "严格口径"], "分版面": "manifest 的 doc_type（严格口径＋主口径）",
                  "对照": ["文档解析原始结果", "方案E", "推荐组合去掉变体三（不含纠错；有深度求索缓存的套另报含纠错版）"], "诊断": "配置甲去掉变体一乙（定位误删）"},
           "分组": GROUPS, "来源核对": origin, "旧清单里没有的文件": [r for r, o in origin.items() if not o],
           "旧清单 sha256": {k: sha(ROOT / p) for k, p in OLD.items()}, "code_sha256": files}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "x", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1))
    os.chmod(OUT, 0o444)
    print("frozen", sha(OUT), len(files), "files；旧清单里没有的：", doc["旧清单里没有的文件"])


if __name__ == "__main__":
    main()
