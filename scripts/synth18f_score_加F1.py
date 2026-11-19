#!/usr/bin/env python3
"""synth18f 一次性计分的包装（配置甲冻结之后、计分之前新写；不改任何冻结文件）。
父 agent 转述的要求把「方案F1」列为对照，但冻结的 scripts/synth18f_score.py 只带文档解析原始结果、方案E、推荐组合去掉变体三。
本脚本只在内存里给 jia_eval.build 多加一行「方案F1（不加纠错）」= Ctx.run({}, llm=None)，然后原样调用冻结的 synth18f_score.main()；
计分规则、顺序（先生成全部输出记 sha、再开参照）、一次性标记都不变。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import jia_eval as J  # noqa: E402

F1_NAME = "方案F1（不加纠错）"
_orig = J.build


def build(c, llm_ok=True):
    preds, counts, logs, notes = _orig(c, llm_ok)
    out = {J.B_NAME: preds.pop(J.B_NAME)}
    out[F1_NAME], counts[F1_NAME], lg = c.run({}, llm=None)
    logs[F1_NAME] = {"steps": {}, "seal_leak": []}
    out.update(preds)
    return out, counts, logs, notes


J.build = build

if __name__ == "__main__":
    import synth18f_score
    synth18f_score.main()
