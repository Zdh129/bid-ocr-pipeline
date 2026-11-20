#!/usr/bin/env python3
"""生成 output/hybrid/report_prep.md（准备阶段报告；有 VLM 结果后再跑一次即带上路线分数）。纯本地。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from baidu_ocr.hybrid.rules import RULES  # noqa: E402
from baidu_ocr.hybrid.star_rule import RULE_TEXT  # noqa: E402

OUT = ROOT / "output" / "hybrid"


def pct(x):
    return "—" if x is None else f"{x * 100:.2f}%"


def main():
    sc = json.loads((OUT / "scores_hybrid.json").read_text(encoding="utf-8"))
    est = json.loads((OUT / "estimate.json").read_text(encoding="utf-8"))
    L = [f"# blind20 混合路线（B 打底 + OpenAI VLM）准备报告", "",
         f"生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}（北京时间 CST，UTC+8；= 伦敦时间 −8 小时）。本阶段**未调用任何外部接口**（百度、OpenAI 均未调用，未读取任何 key）。", ""]
    L += ["## 1. 计分（★/☆ 规则前后；A、B 标准版为已跑好的输出）", "", f"★/☆ 规则：{RULE_TEXT}", ""]
    for vname, title in (("corrected", "修正参照（reference_corrections.json + manual_reference_fixes.json）"), ("original", "原参照（未修正）")):
        L += [f"### {title}", "", "| 系统 | 总体 读出率 | 总体 CER | 删 | 替 | 插 | 正文 读出率/CER | 表格 读出率/CER | 印章 读出率/CER | 达标(>99%且<1%) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for name, s in sc["variants"][vname].items():
            t = s["total"]
            o = t["overall"]
            ok = "是" if o["readout"] > 0.99 and o["cer"] < 0.01 else "否"
            L.append(f"| {name} | {pct(o['readout'])} | {pct(o['cer'])} | {o['deletions']} | {o['substitutions']} | {o['insertions']} | "
                     f"{pct(t['body']['readout'])} / {pct(t['body']['cer'])} | {pct(t['table']['readout'])} / {pct(t['table']['cer'])} | "
                     f"{pct(t['seal']['readout'])} / {pct(t['seal']['cer'])} | {ok} |")
        L.append("")
    L += ["（总体 = 正文 + 表格；印章单独计分，不并入总体，与此前报告口径一致。）", ""]
    L += ["## 2. 两条路线的送审量与预估费用（OpenAI 按清单裁图尺寸预估；百度按已花费计）", "",
          "| 路线 | 块数 | 涉及页 | 类型分布 | 模型分布 | 预估 token（入/出） | 预估 OpenAI 费用 | 百度已花 | 合计 |", "|---|---|---|---|---|---|---|---|---|"]
    bd = est["baidu_spent"]
    for r, s in est["routes"].items():
        baidu = bd["B_standard"]["cny"] + (bd["A_accurate"]["cny"] if r == "route2" else 0)
        L.append(f"| {r} | {s['n_blocks']} | {s['n_pages_involved']} | {s['by_kind']} | {s['by_model']} | {s['est_prompt_tokens']} / {s['est_completion_tokens']} | "
                 f"${s['est_usd']:.3f} ≈ ¥{s['est_cny']:.2f} | ¥{baidu:.2f} | ¥{baidu + s['est_cny']:.2f} |")
    d = est["both_routes_dedup"]
    L += ["", f"两条路线一起跑、相同裁图复用结果：{d['n_unique_calls']} 次调用，预估 ${d['est_usd']:.3f} ≈ ¥{d['est_cny']:.2f}。汇率 1 美元 = 7.2 元。", ""]
    L += ["## 3. 可触达错误上限（假设 VLM 在送审区域内全对、且不引入新错；修正参照）", "",
          "| 路线 | 基线 | 现在 读出率/CER | 错误数 | 落在送审区域内 | 上限 读出率/CER |", "|---|---|---|---|---|---|"]
    for r, dd in sc.get("addressable_upper_bound", {}).items():
        for k, v in dd.items():
            o = v["overall"]
            L.append(f"| {r} | {k} | {pct(o['now_readout'])} / {pct(o['now_cer'])} | {o['errors']} | {o['addressable']} | {pct(o['ceiling_readout'])} / {pct(o['ceiling_cer'])} |")
    L += ["", "## 4. 判定规则（两条路线共用，常量见 src/baidu_ocr/hybrid/rules.py: RULES）", "", "```json", json.dumps(RULES, ensure_ascii=False, indent=1), "```", ""]
    (OUT / "report_prep.md").write_text("\n".join(L), encoding="utf-8")
    print(OUT / "report_prep.md")


if __name__ == "__main__":
    main()
