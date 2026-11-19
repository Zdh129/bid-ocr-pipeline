#!/usr/bin/env python3
"""synth18d 运行前冻结 → output/synth18d/frozen_synth18d.json（独占创建；任何外发之前；不覆盖旧清单）。
内容 = 推荐组合清单与方案E 清单里的全部文件（逐个核对与旧清单 sha 一致）＋本轮新写的 synth18d 脚手架与两份 live 配置＋prompt_v1。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "synth18d" / "frozen_synth18d.json"
OLD = {"frozen_推荐组合": ROOT / "output" / "f1_round2" / "frozen_推荐组合.json", "frozen_方案E": ROOT / "output" / "synth18e_prep" / "frozen_方案E.json"}
NEW = ["scripts/synth18d_run.py", "scripts/synth18d_score.py", "scripts/synth18d_llm.py", "scripts/synth18d_scheme_a_call.py", "scripts/synth18d_freeze.py",
       "scripts/f1r2_seal_call.py", "scripts/ready_check_推荐组合.py", "config_synth18d_live.yaml", "config_llm_synth18d_live.yaml", "output/synth18_llm/prompt_v1.txt"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    if OUT.exists():
        sys.exit("已存在，不覆盖")
    files, origin, bad = {}, {}, []
    for k, p in OLD.items():
        for rel, h in json.loads(p.read_text(encoding="utf-8"))["code_sha256"].items():
            if sha(ROOT / rel) != h:
                bad.append(rel)
            files[rel] = h
            origin.setdefault(rel, []).append(k)
    if bad:
        sys.exit(f"旧清单里的文件已改动：{bad}")
    for rel in NEW:
        files[rel] = sha(ROOT / rel)
        origin.setdefault(rel, []).append("本轮新写／未在上两份清单" if rel not in origin else "")
    now = datetime.now(timezone.utc)
    doc = {"冻结时间_伦敦": now.strftime("%Y-%m-%d %H:%M:%S"), "created_at": now.isoformat(timespec="seconds"),
           "用途": "synth18d 留出集一次性验证；冻结后不改代码、配置、提示词、脚本",
           "批准原话": "批准 synth18d 全部页（含合成证件页）发百度，表格文字发深度求索（DeepSeek）纠错，深度求索上限 $1",
           "扫描件 sha256": "4a96be6b1184e3445d6761b7d021eddc49a5116c99e1b1c90e60afb56fd30cfd",
           "参照 sha256（预期）": "264cab10519367d18d11f0f12b079770d1d0843051b485e67e32079a4891d580",
           "版本": ["文档解析原始结果", "v2（旧冻结清单代码；6.2 未获批，无回填结果）", "方案F1（不加纠错）", "推荐组合（不含纠错）", "推荐组合（含纠错）",
                  "推荐组合（含纠错）去掉变体一乙", "推荐组合（含纠错）去掉变体三", "推荐组合（含纠错）去掉深度求索纠错", "参考：方案E"],
           "旧清单 sha256": {k: sha(p) for k, p in OLD.items()}, "来源核对": origin, "code_sha256": files}
    with open(OUT, "x", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1))
    os.chmod(OUT, 0o444)
    print("frozen", sha(OUT), len(files), "files")


if __name__ == "__main__":
    main()
