#!/usr/bin/env python3
"""生成 output/hybrid/report.md（实测后最终报告）。纯本地，读 scores_hybrid.json / estimate.json / 账本。"""
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
V2_FROM = "2026-11-18T13:56"


def pct(x):
    return "—" if x is None else f"{x * 100:.2f}%"


def gap(o):
    r, c = o["readout"], o["cer"]
    if r > 0.99 and c < 0.01:
        return "达标"
    parts = []
    if r <= 0.99:
        parts.append(f"读出率差 {(0.99 - r) * 100:.2f} 个百分点")
    if c >= 0.01:
        parts.append(f"CER 高 {(c - 0.01) * 100:.2f} 个百分点")
    return "未达标：" + "，".join(parts)


def main():
    sc = json.loads((OUT / "scores_hybrid.json").read_text(encoding="utf-8"))
    est = json.loads((OUT / "estimate.json").read_text(encoding="utf-8"))
    L = [json.loads(x) for x in (OUT / "vlm" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    v1 = [x for x in L if x["created_at"] < V2_FROM]
    v2 = [x for x in L if x["created_at"] >= V2_FROM]
    usd = lambda xs: sum(x["usd"] for x in xs if not x.get("cache_hit"))
    rs = {}
    for r in ("route1", "route2"):
        xs = [x for x in v2 if x["route"] == r]
        ts = sorted(datetime.fromisoformat(x["created_at"]) for x in xs)
        rs[r] = {"n": len(xs), "new": sum(1 for x in xs if not x.get("cache_hit")), "cache": sum(1 for x in xs if x.get("cache_hit")),
                 "n4o": sum(1 for x in xs if x["model"] == "gpt-4o"), "n5m": sum(1 for x in xs if x["model"] == "gpt-5-mini"),
                 "usd_alone": sum(x["usd"] for x in xs), "ptok": sum(x["prompt_tokens"] for x in xs), "ctok": sum(x["completion_tokens"] for x in xs),
                 "wall": (ts[-1] - ts[0]).seconds if ts else 0, "t0": ts[0].strftime("%H:%M:%S"), "t1": ts[-1].strftime("%H:%M:%S")}
    B_CNY, A_CNY, B_SEC, A_SEC = 1.00, 1.12, 47.8, 135.5
    out = ["# blind20 混合路线对比：B 标准版打底 + OpenAI VLM（路线 1 两层 / 路线 2 三层）", "",
           f"报告生成：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} 北京时间（CST，UTC+8；伦敦时间 = 北京时间 − 8 小时）。",
           "OpenAI 外发依据：当时已获项目负责人书面批准（历史测试记录，本仓库不收录外发凭证）。"
           "只发了 output/hybrid/crops/ 下的裁图（长边 ≤1600），没有发整页，也没有重新调百度。key 只从环境变量读取，没有打印。", ""]
    # 结论
    cor = sc["variants"]["corrected"]
    best = max(((k, v["total"]["overall"]) for k, v in cor.items()), key=lambda kv: (kv[1]["readout"] - kv[1]["cer"]))
    out += ["## 1. 结论", "",
            f"- **两条混合路线都没有达标**（目标：读出率 >99% 且 CER <1%），而且都比单用 B 标准版差。修正参照下最好的混合结果是路线 2、低置信生效阈值 0.75、加 ★/☆ 规则：读出率 {pct(cor['route2@0.75+star']['total']['overall']['readout'])}，CER {pct(cor['route2@0.75+star']['total']['overall']['cer'])}（{gap(cor['route2@0.75+star']['total']['overall'])}）。路线 1 最好也只有 {pct(cor['route1@0.75+star']['total']['overall']['readout'])} / {pct(cor['route1@0.75+star']['total']['overall']['cer'])}。",
            f"- **全场最好的是「B 标准版 + ★/☆ 规则」，不用 VLM**：读出率 {pct(cor['B_std+star']['total']['overall']['readout'])}（已过 99%），CER {pct(cor['B_std+star']['total']['overall']['cer'])}（{gap(cor['B_std+star']['total']['overall'])}）。不加规则时 B 是 {pct(cor['B_std']['total']['overall']['readout'])} / {pct(cor['B_std']['total']['overall']['cer'])}。",
            "- VLM 为什么拉低分数（按区域类型拆开看，见第 5 节）：",
            "  - 印章：gpt-5-mini 读环形印章明显不如百度印章识别。印章读出率从 82.25% 掉到 51.81%，插入 261 字：英文外圈被读成长串英文，还编出了与图不符的单位名，例如 p11 把「哈希水质分析仪器（上海）」读成「武汉市东西湖区水务监测」。",
            "  - 表格：路线 1 整表重识别，损失很大。p7 漏了表格最后一行长备注（约 110 字），p16、p19、p20 行和单元格的顺序乱了，表格 CER 从 1.71% 升到 9.91%。路线 2 只送 A/B 不一致的行，表格读出率从 98.49% 升到 98.93%，但插入变多，表格 CER 从 1.71% 变成 1.84%，基本持平。",
            "  - 文字行：B 的 layout score 普遍低于 0.95（文字类最高 0.897），所以按 0.95 阈值几乎每行都送了一遍。VLM 会改标点（全角与半角、「；」与「，」）、漏手写日期、偶尔认错字（测试样本A→恒凯、氢气→氦气），正文 CER 从 1.65% 升到约 2.2%。",
            "- 因此建议**不切换主路径**，B 标准版仍作主路径。值得做的是把 ★/☆ 规则落到 B 后处理；它离 CER 目标还差 0.17 个百分点。剩下的错误主要是发票合计行单元格顺序、印章/水印字混入正文和手写签名。",
            "- 说明：修正参照是用 A 与 B 一致的读法再加人工核图得到的，对 A 和 B 有利，对 VLM 的独立读法不利；这个偏差必须写明。原参照下所有方案都在 82–85% / 20–24%，主要差异来自参照本身的错误。", ""]
    # 分数表
    def table(vname, title):
        v = sc["variants"][vname]
        rows = ["### " + title, "", "| 系统 | ★/☆ | 总体 读出率 | 总体 CER | 删(漏字) | 替 | 插 | 正文 读出率 / CER (D/S/I) | 表格 读出率 / CER (D/S/I) | 印章 读出率 / CER (D/S/I) | 对照目标 |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        order = ["A", "B_std"] + [f"{r}@{t:.2f}" for r in ("route1", "route2") for t in RULES["lowconf_eval_thresholds"]]
        for base in order:
            for star in ("", "+star"):
                k = base + star
                if k not in v:
                    continue
                t = v[k]["total"]
                o = t["overall"]
                f = lambda s: f"{pct(t[s]['readout'])} / {pct(t[s]['cer'])} ({t[s]['deletions']}/{t[s]['substitutions']}/{t[s]['insertions']})"
                rows.append(f"| {base} | {'加' if star else '不加'} | {pct(o['readout'])} | {pct(o['cer'])} | {o['deletions']} | {o['substitutions']} | {o['insertions']} | {f('body')} | {f('table')} | {f('seal')} | {gap(o)} |")
        return rows + ["", f"参照字数：总体 {v['A']['total']['overall']['ref_chars']}（正文 {v['A']['total']['body']['ref_chars']}，表格 {v['A']['total']['table']['ref_chars']}），印章 {v['A']['total']['seal']['ref_chars']}。总体 = 正文 + 表格，印章单独计分。", ""]
    out += ["## 2. 完整对比表", "", "系统名里 route1@0.75 的意思是：路线 1，低置信行的生效阈值取 0.75。送审时 score < 0.95 的行一次全送，计分时离线切换：score ≥ 生效阈值、并且只因低置信被送审的行，保留 B 原文；无分数（score = 0）的行任何阈值下都采用 VLM。", ""]
    out += table("corrected", "2.1 修正参照（reference_corrections.json，以及 manual_reference_fixes.json 的整页重写和印章修正）")
    out += table("original", "2.2 原参照（CHATGPT-REFERENCE-BLIND20-NEW.txt，未修正）")
    # 成本
    out += ["## 3. VLM 调用次数、耗时、费用（时间均为北京时间）", "",
            "| 路线 | 百度部分（已花，未重调） | VLM 调用（块） | 其中 gpt-4o / gpt-5-mini | 实际新调用 / 复用 | token 入 / 出 | OpenAI 费用（本路线单独计） | 合计费用 | VLM 墙钟（6 并发） | 端到端耗时（百度 + VLM，串行计） |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r, x in rs.items():
        bcny = B_CNY + (A_CNY if r == "route2" else 0)
        bsec = B_SEC + (A_SEC if r == "route2" else 0)
        out.append(f"| {r} | ¥{bcny:.2f}（{'B 标准版 ¥1.00' if r == 'route1' else 'B 标准版 ¥1.00 + A 高精度 28 次 ¥1.12'}），{bsec:.1f} s | {x['n']} | {x['n4o']} / {x['n5m']} | {x['new']} / {x['cache']} | {x['ptok']} / {x['ctok']} | "
                   f"${x['usd_alone']:.4f} ≈ ¥{x['usd_alone'] * 7.2:.2f} | ¥{bcny + x['usd_alone'] * 7.2:.2f} | {x['wall']} s（{x['t0']}–{x['t1']}） | ≈ {bsec + x['wall']:.0f} s |")
    out += ["", f"- OpenAI 实际总花费 **${usd(L):.4f} ≈ ¥{usd(L) * 7.2:.2f}**，未到 $2 硬上限。其中第 1 轮 ${usd(v1):.4f}（{sum(1 for x in v1 if not x.get('cache_hit'))} 次调用，作废，原因见第 6 节），第 2 轮（计分用）${usd(v2):.4f}。费用按每次返回的实际 token 和单价计（gpt-4o 入 $2.50、出 $10.00；gpt-5-mini 入 $0.25、出 $2.00，均为每百万 token）。",
            "- 「复用」：两条路线里裁图和提示词都相同的块（主要是 30 个印章）只调用一次。每条路线的费用按「本路线单独跑」计，所以两条相加会大于实际花费。",
            "- 调用全部成功：0 失败、0 重试，finish_reason 全为 stop。", ""]
    # 规则
    out += ["## 4. 判定规则（两条路线共用，可复现；常量在 src/baidu_ocr/hybrid/rules.py 的 RULES）", "",
            "- **D1 表格**：B 标准版 layout.type = table 的块。裁图先把长边压到 ≤1600；如果压完短边仍 >768（gpt-4o 的 high 模式会再缩），就按 B 的行边界切段。裁图上下沿取本段单元格的实际边界，外扩 4 px。",
            "- **D2 印章**：B 的 seal 块，并上本地红色像素检测框（blind20_local.seal_stats，纯像素检测，不用 A 的识别结果）。两框 IoU ≥0.3、或一方中心落在另一方框内，就合并为一个印章区域。共 30 个区域：B+本地 22 个，仅 B 5 个，仅本地 3 个。",
            "- **D3 低置信行**：B 文字类 layout（text、title、head_tail）且 layout.score < 0.95；score = 0 的视为无分数，也算。计分时离线切换生效阈值 0.75、0.85、0.95。手写签名（hand_sign，共 4 处）两条路线都不送。",
            "- **D4 发票无线框区**：页面任一 B 文字含「发票号码」或「电子发票」，即为发票页（p19、p20）。B 表格里从含「项目名称」的行起，到第一个含「合计」但不含「价税合计」的行止，为明细区。路线 1 里这一段强制单独裁一段，用发票专用提示词。",
            "- **路线 1（两层）**：D1 整表（按段）、D2、D3、D4 全部直接送 VLM，不看 A。",
            "- **路线 2（三层）**：只送以下几类。",
            "  - D2 印章（与路线 1 完全相同）。",
            "  - D3 低置信行（与路线 1 完全相同）。",
            "  - A/B 不一致的文字行：A 行框中心落在 B 块（外扩 3pt）内即归给该块，比较口径与计分相同（NFKC、去空白）。",
            "  - A/B 不一致的表格行：B 每个单元格与落在格内的 A 单字逐格比较；不一致的行连成段送 gpt-4o。",
            "  - 漏行：A 有、B 没有任何块覆盖的行，加上 A 覆盖缺口里 text_or_other 类且 B 也没覆盖的区域。",
            "- **模型**：表格段用 gpt-4o（detail=high）；其余用 gpt-5-mini（detail=auto，reasoning_effort=low，与第二轮一致）。",
            f"- **★/☆ 规则**：{RULE_TEXT}", "",
            "### 送审块数与类型分布", "", "| 路线 | 块数 | 涉及页 | 类型 | 送审原因（一块可有多个原因） | 预估费用 | 实际费用（单独计） |", "|---|---|---|---|---|---|---|"]
    for r, s in est["routes"].items():
        out.append(f"| {r} | {s['n_blocks']} | {s['n_pages_involved']} | {s['by_kind']} | {s['by_reason']} | ${s['est_usd']:.3f} | ${rs[r]['usd_alone']:.3f} |")
    cc = est["routes"]["route2"]["cross_check"]["total"]
    out += ["", f"路线 2 交叉核对：比较文字块 {cc['lines_compared']} 个，A/B 不一致 {cc['lines_mismatch']} 个；低置信但 A/B 一致的 {cc['lowconf_and_ab_agree']} 个（按要求也送了）。比较表格行 {cc['table_rows_compared']} 行，不一致 {cc['table_rows_mismatch']} 行，合成 12 段。另有 A 有、B 无的行 {cc['a_lines_unassigned']} 行，A、B 都没覆盖的缺口 {cc['a_gaps_sent']} 处。", ""]
    out += ["## 5. 按区域类型拆开看（修正参照，生效阈值 0.95，不加 ★/☆；每行只把这一类 VLM 结果合并进 B）", "", "```", (OUT / "ablation_by_kind.txt").read_text(encoding="utf-8").strip(), "```",
            "（格式：总体 / 正文 / 表格 / 印章 各为（读出率, CER, 删, 替, 插）。B 标准版基线：总体 0.986884 / 0.016743 / 25 / 69 / 26。）", ""]
    out += ["## 6. 过程说明（含作废的第 1 轮）", "",
            "- 第 1 轮（北京时间 13:50–13:53，花费 $0.2588）有两处是我自己实现的缺陷，结果作废，原始结果留在 output/hybrid/vlm/results_v1/：",
            "  - 文字行提示词写成「只转写中间这一行」，B 的多行段落被截成一行，例如 p2 漏了 58 字；还写了「忽略手写签名」，模型连手写日期也一起略掉了。",
            "  - 路线 2 的表格段裁图上下沿外扩到相邻行中线，相邻行露进裁图，被重复转写。",
            "- 第 2 轮的修正：",
            "  - 文字行提示词改为「完整转写裁图内所有完整行；手写人名忽略，手写日期和数字照常转写」。",
            "  - 表格段只取本段单元格的实际边界。",
            "  - 合并时丢掉与段外 B 行更像（相似度 ≥0.8）的 VLM 行，即相邻行串入或模型自己补写的表头；只比较文本，不看参照。",
            "  - 判定规则和阈值没有改。印章裁图和提示词也没变，第 1 轮的印章结果直接复用，没有重复计费。",
            "- 预算闸门：第 2 轮前发现账本累计统计有 bug（ledger 里的 kind 字段被区域类型覆盖），重启后累计费用会从 0 算起。已修复并加了单测。实际累计 $0.50，从未接近 $2。", ""]
    out += ["## 7. 产物路径", "",
            "- 报告：output/hybrid/report.md（本文件）；准备阶段报告 output/hybrid/report_prep.md",
            "- 分数：output/hybrid/scores_hybrid.json（两套参照 × 各系统 × 加/不加 ★/☆，含逐页分数和可触达上限分析）",
            "- 合并后的中间 JSON（schema 1.0.0，校验 0 错误，带 bbox 和 vlm 溯源：区域 id、模型、裁图路径与 sha256、裁图像素框、原文本）：output/hybrid/route{1,2}/thr{0.75,0.85,0.95}/blind20-scan.json；加 ★/☆ 的版本为 blind20-scan.star.json；同目录下有 pred_pages.json 和 merge_log.json",
            "- 待发清单：output/hybrid/manifest_route1.json、manifest_route2.json；裁图：output/hybrid/crops/route1|route2/；200 DPI 渲染：output/hybrid/renders/",
            "- VLM 结果与账本：output/hybrid/vlm/results/route{1,2}/*.json、output/hybrid/vlm/ledger.jsonl（逐块：模型、token、费用、耗时、尝试次数，不含 key 和图片内容）",
            "- 代码：src/baidu_ocr/hybrid/（rules.py、inputs.py、crops.py、vlm.py、merge.py、star_rule.py），scripts/hybrid_prepare.py、hybrid_run_vlm.py、hybrid_score.py、hybrid_final_report.py；单测 tests/test_hybrid.py",
            "- 配置：config_hybrid.yaml（allow_external: false）；config_hybrid_live.yaml（allow_external: true，硬上限 $2）",
            "- 没有改动：frozen20/（哈希校验一致）、/workspace/pdf-test/ocr/config.yaml（线上 150 DPI）、output/final/、output/compare/", ""]
    (OUT / "report.md").write_text("\n".join(out), encoding="utf-8")
    print(OUT / "report.md")


if __name__ == "__main__":
    main()
