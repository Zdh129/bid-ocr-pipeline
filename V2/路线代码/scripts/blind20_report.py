"""生成 output/compare/report.md（只读取已有产物，不发起任何外部调用）。"""
import json, hashlib, pathlib
R = pathlib.Path("output")
S = json.load(open(R/"compare/scores.json"))
BM = json.load(open(R/"final/scheme_a/build_meta.json"))
CS = json.load(open(R/"scheme_a/call_summary.json"))
GEN = json.load(open(R/"diag_all_general/call_summary.json"))
CORR = json.load(open(R/"compare/reference_corrections.json"))
MAN = json.load(open("data/blind20/manual_reference_fixes.json"))
cost = {k: json.load(open(R/f"{k}/cost.json")) for k in ["scheme_a", "scheme_b_standard", "scheme_b_unlimited", "diag_all_general"]}
rawb = {k: json.load(open(R/f"{k}/blind20-scan.raw.json")) for k in ["scheme_b_standard", "scheme_b_unlimited"]}
FX = 7.2
NAMES = {"A": "方案 A（杨工混合：高精度含位置版+坐标重排+补识）", "B_std": "方案 B 文档解析·标准版",
         "B_unl": "方案 B 文档解析·Unlimited-OCR 版", "A_gen": "诊断：全部走通用含位置版（不算方案）"}
def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()
def pct(x): return "—" if x is None else f"{x*100:.2f}%"
def row(name, t):
    return f"| {name} | {t['ref_chars']} | {pct(t['readout'])} | {pct(t['cer'])} | {t['deletions']} | {t['substitutions']} | {t['insertions']} |"
L = []
w = L.append
w("# blind20 百度 OCR 实测对比：方案 A vs 方案 B\n")
w("时间均为北京时间（UTC+8）。实测日期 2026-11-18。\n")
w("## 1. 结论\n")
c = S["variants"]["corrected"]["systems"]; o = S["variants"]["original"]["systems"]
w(f"- **更接近「读出率 ≥99% 且 CER ≤1%」的是方案 B 标准版**：修正参照后总体读出率 {pct(c['B_std']['total']['overall']['readout'])}、CER {pct(c['B_std']['total']['overall']['cer'])}；"
  f"方案 A 为 {pct(c['A']['total']['overall']['readout'])} / {pct(c['A']['total']['overall']['cer'])}。**两者都还没达标**（B 读出率还差约 0.3 个百分点，CER 还高约 0.7 个百分点）。")
w(f"- 按原始参照（未修正）计分，所有方案读出率只有 83–84%、CER 21–39%，离阈值很远；但核图证明**绝大部分差异是参照本身写错**（参照有多页整页臆写），不是 OCR 错。")
w("- B 标准版的剩余错误集中在：★ 读成 ☆（且「★1.」常读成「☆1。」）、发票合计行单元格顺序、水印/印章字混入、手写签名。前两类可用后处理规则修掉，修掉后有望达标，但本次**未**把规则后处理计入分数。")
w("- A 的主要短板：无框线的发票明细区多行单元格顺序错乱（p19/p20），印章文字几乎读不出（高精度版不是印章接口，印章读出率约 5%）。正文读出率与 B 持平（约 98.8%），但正文 CER 偏高（2.6% vs 1.7%）。")
w("- Unlimited-OCR 版不推荐：在 6 页二维码处生成了英文描述幻觉（“The image provided is a QR code…”，每处约 170–210 字），印章 0 输出，CER 20%。")
w("- **修正参照是用 A 与 B 标准版一致的读法+人工核图得出的，对 A 和 B 标准版有利的偏差必须说明**（见第 7 节）。\n")

w("## 2. 实测协议与输入\n")
w("- 外发文件仅 `/workspace/pdf-test/frozen20/blind20-scan.pdf`（sha256 `" + sha("/workspace/pdf-test/frozen20/blind20-scan.pdf") + "`）。`blind20-textlayer.pdf` 未用于 OCR、未外发。")
w("- 参照文件（未改动）sha256 `" + S["reference_sha256"] + "`。frozen20 目录内文件全部只读使用，运行前后哈希校验一致。")
w("- 外发开关：`config.yaml` 保持 `allow_external: false`；实测用另存的 `config_live.yaml`（`allow_external: true`）。线上配置 `/workspace/pdf-test/ocr/config.yaml` 未动。用户已批准 20 页（含证件页）发往百度 OCR，`pii.enabled=false`、`drop_pii_pages=false`。")
w("- 费用上限：软上限 $5、硬上限 $10，未变。Key/token 全程未打印。")
w("- 计分口径：" + S["protocol"].replace("\n", " ") + "\n")

w("## 3. 调用、耗时、费用\n")
w("| 方案 | 接口 | 调用次数 | 计费页 | 费用（元） | 费用（美元, 1$=¥7.2） | 总耗时 | 每页 | 运行时间（北京时间） |")
w("|---|---|---|---|---|---|---|---|---|")
ca = cost["scheme_a"]["by_api"]["accurate"]
w(f"| A | 高精度含位置版 | {ca['calls']}（主识别 20 + 补识拼图 8），全部成功 | 28 次 | {cost['scheme_a']['total_cny']:.2f} | {cost['scheme_a']['total_cny']/FX:.3f} | {CS['wall_sec']:.1f} s | {CS['wall_sec']/20:.1f} s（单次接口均值 5.1 s，范围 3.7–6.0 s） | {CS['started_at_cst']} – {CS['finished_at_cst'][11:]} |")
for k, n in [("scheme_b_standard", "B 标准版"), ("scheme_b_unlimited", "B Unlimited-OCR")]:
    r = rawb[k]
    w(f"| {n} | 文档解析（整份 PDF 异步） | 1 次提交 + 轮询 | {r['billed_pages']} 页 | {cost[k]['total_cny']:.2f} | {cost[k]['total_cny']/FX:.3f} | {r['submit_to_success_sec']:.1f} s | {r['submit_to_success_sec']/20:.1f} s（折算） | 结果落盘 {'11:45:45' if 'standard' in k else '11:49:50'} |")
w(f"| 诊断 A_gen | 通用含位置版 | 20 | 20 次 | {cost['diag_all_general']['total_cny']:.2f} | {cost['diag_all_general']['total_cny']/FX:.3f} | {GEN['wall_sec']:.1f} s | {GEN['wall_sec']/20:.1f} s | {GEN['started_at_cst']} 起 |")
tot = sum(cost[k]["total_cny"] for k in cost)
w(f"\n合计约 ¥{tot:.2f}（约 ${tot/FX:.2f}），远低于 $5 软上限。费用按百度公开按量单价估算（高精度含位置 ¥0.04/次、通用含位置 ¥0.01/次、文档解析标准版 ¥0.05/页、Unlimited ¥0.02/页），如账户有免费额度，实扣可能更低，以百度账单为准。\n")

w("## 4. 方案 A：逐页路由、理由、行框覆盖\n")
w("路由规则：检测到表格框线或红色印章 → 高精度含位置版；否则 → 通用含位置版。实际 20 页全部命中印章或表格，所以**主识别全部走高精度，通用版一次未用**。为了对照，另跑了一遍全通用（A_gen），只作诊断。\n")
w("| 页 | 类型 | 接口 | 理由 | 期望行框 | 首轮缺 | 补识后仍缺 | 仍缺分类 | 本地 s | 主识别 s | 补识 s | 合计 s |")
w("|---|---|---|---|---|---|---|---|---|---|---|---|")
E = M1 = SM = 0
for r in BM["routing"]:
    cv = r["coverage"]; t = r["timing_sec"]
    E += cv["expected_line_boxes"]; M1 += cv["missing_first_pass"]; SM += len(cv["still_missing"])
    gaps = "、".join(g["gap_class"] for g in cv["still_missing"]) or "—"
    w(f"| {r['page']} | {r['page_class']} | {r['api']} | {'; '.join(r['reasons'])} | {cv['expected_line_boxes']} | {cv['missing_first_pass']} | {len(cv['still_missing'])} | {gaps} | {t['local']:.2f} | {t['api_main']:.2f} | {t['api_retry']:.2f} | {t['page_total']:.2f} |")
w(f"\n覆盖：本地检出文字行框 {E} 个（已排除印章区域内的 106 个），首轮未被 OCR 框覆盖 {M1} 个，8 页做了补识（拼图一次调用高精度），补识后仍缺 {SM} 个，全部在 JSON 中以 `coverage_gap` 块标出。")
w("按裁图人工复核，这 20 处是：**二维码 10 处、印章残片/骑缝章/发票监制章外圈 9 处、正文 1 处（p12 被印章压住的一行，其文字其实已识别，仅因红章遮挡导致覆盖率 48.9% < 50% 阈值被标出）**。没有发现真实文字行被静默漏掉。自动分类器与人工有 4 处不同：p2 一处印章残片、p19/p20 各一处二维码碎块被标成 text_or_other，p12 那行正文被标成 seal_fragment；以人工复核为准。\n")

w("## 5. 分数\n")
for var, title in [("original", "按原始参照（ChatGPT 转写，未修改）"), ("corrected", "按修正参照（核图修正，见第 7 节）")]:
    w(f"### 5.{1 if var=='original' else 2} {title}\n")
    for sec, sn in [("overall", "总体（正文+表格）"), ("body", "正文"), ("table", "表格"), ("seal", "印章（单独计分）")]:
        w(f"**{sn}**\n\n| 系统 | 参照字数 | 读出率 | CER | 删 | 替 | 插 |\n|---|---|---|---|---|---|---|")
        for k in ["A", "B_std", "B_unl", "A_gen"]:
            w(row(NAMES[k], S["variants"][var]["systems"][k]["total"][sec]))
        w("")
w("### 5.3 按文档类型（修正参照，总体）\n")
dts = list(c["A"]["by_doc_type"].keys())
w("| 文档类型 | 页数 | A 读出率 | A CER | B 标准 读出率 | B 标准 CER | B Unlimited 读出率 | B Unlimited CER |\n|---|---|---|---|---|---|---|---|")
for d in dts:
    a, b, u = (c[k]["by_doc_type"][d] for k in ["A", "B_std", "B_unl"])
    w(f"| {d} | {a['pages']} | {pct(a['overall']['readout'])} | {pct(a['overall']['cer'])} | {pct(b['overall']['readout'])} | {pct(b['overall']['cer'])} | {pct(u['overall']['readout'])} | {pct(u['overall']['cer'])} |")
w("\n### 5.4 逐页（修正参照，总体 CER）\n")
w("| 页 | 类型 | A 读出率 | A CER | B 标准 读出率 | B 标准 CER |\n|---|---|---|---|---|---|")
for p in sorted(c["A"]["per_page"], key=int):
    a = c["A"]["per_page"][p]; b = c["B_std"]["per_page"][p]
    w(f"| {p} | {a['doc_type']} | {pct(a['overall']['readout'])} | {pct(a['overall']['cer'])} | {pct(b['overall']['readout'])} | {pct(b['overall']['cer'])} |")
w("")

w("## 6. 三处可疑参照的核实（裁图见 `output/compare/verify/`）\n")
w("- **p2「实验差」vs「实验桌」**：原图是「实验桌」，参照写错。并且参照 p2 表格大部分是臆写：原图型号为 NIRS DS 2500F、品牌为丹麦 FOSS / 优利娜，第 3 行是「实验凳」，赠品表里是「扫码枪」。")
w("- **p13「氢」vs「氧」**：原图是「氢气发生器」、型号 Intura H2 500，参照写成氧气；整页参照内容都与原图不符（臆写）。")
w("- **p19 买方**：原图买方是「贵州茅台酒股份有限公司」；参照的发票号码、开户行、货物行、单价、收款人/复核人/开票人也都与原图不符（原图如单价 846283.18584070796、收款人喻利平、复核人梁燕、开票人王良萍）。")
w("- 附带发现：参照 p9–p20 的【印章：…】大多写成「博凯」，原图实际是各厂家/供应商自己的章。\n")

w("## 7. 「原图支持我们」清单与修正参照\n")
nsk = len(CORR.get("skipped", []))
w(f"- 共识差异：A 与 B 标准版给出**相同**读法而与参照不同的片段，在已逐页核图的页（1、2、5、6、7、9–20；p3 因手写「周浪」未纳入）上共 **{len(CORR['corrections'])} 条**被原图支持（另有 {nsk} 条跳过：其中 ★/☆ 差异以原图 ★ 为准，不算参照错）。在非整页重写页上实际应用 {S['variants']['corrected']['n_corrections']} 条，其余落在下面 7 个整页重写页里，已被整页重写覆盖。")
w(f"- 整页重写（参照整页臆写，按裁图逐字转写，看不清处用【?】）：p{', p'.join(str(x) for x in sorted(int(k) for k in MAN['page_rewrites']))}，共 {len(MAN['page_rewrites'])} 页。")
w(f"- 非共识的子串修正 {len(MAN['substring_fixes'])} 条：" + "；".join(f"p{x['page']}「{x['old'][:20]}」→「{x['new'][:24]}」" for x in MAN["substring_fixes"]) + "（p2 那条已被共识修正吸收）。")
w(f"- 印章文字修正 {len(MAN['seal_fixes'])} 页：p{', p'.join(str(x) for x in sorted(int(k) for k in MAN['seal_fixes']))}。")
w(f"- 修正后参照字数 {c['A']['total']['overall']['ref_chars']}（原 {o['A']['total']['overall']['ref_chars']}）。完整清单：`output/compare/reference_corrections.json`、`data/blind20/manual_reference_fixes.json`。原参照文件未改，修正只在计分时套用。")
w("- **偏差说明**：共识修正只在 A 与 B 标准版读法一致时成立，天然有利于这两条方案（尤其两者共同的错误若恰好与原图不符却未被核图发现，会被当成正确）。整页重写和印章修正是人工逐字核图，偏差较小。Unlimited 与 A_gen 不参与共识，分数相对偏低一些。\n")

w("## 8. 主要错误类型与示例（修正参照）\n")
w("| 错误类型 | 方案 | 示例 |\n|---|---|---|")
w("| 无框线发票明细区多行单元格顺序错乱 | A | p20「(全酒酒精分析仪)+Alcolyzer3001+Xsample520」被挪到别处（34 字）；p19「2400*750*850mm」「升降、防滑五星脚」错位 |")
w("| 发票合计行单元格顺序 | B 标准 | p20「13% 232389.38 合计 ¥2858407.08」读成「¥2858407.08 13% 232389.38」 |")
w("| ★ 读成 ☆、「.」读成「。」 | A、B 都有，B 更多 | p15「★3.」→「☆3。」；p16 表格「★1.」「★5.」→「☆1。」「☆5。」 |")
w("| 印章/监制章/水印字混入正文 | A、B | p8 A 插入「贵州习酒股份有限公司」（印章）；p19/p20 A 插入「贵州省州省税务局下纛:1」；p3 插入「合同专用州茅台」（茅台 logo 水印+印章） |")
w("| 竖排小字漏读 | A、B | p19/p20「下载次数:1」 |")
w("| 手写签名 | A、B | p3/p8「周浪」「陈强」被读出，参照未收录 → 记为插入（是否应计入参照可再定） |")
w("| 页脚位置 | A | p8「第8页共16页」位置错（顺序错，算删+插） |")
w("| 印章文字读不出 | A | 印章读出率约 5%：高精度含位置版不是印章接口，红章文字基本不输出 |")
w("| 印章流水号/残字插入 | B 标准 | 印章读出率 82%，但插入 115 字（印章编码数字、残字） |")
w("| 二维码处英文幻觉、印章 0 输出 | B Unlimited | p2–p6、p19「The image provided is a QR code and does not contain any text…」 |")
w("\n## 9. 产物路径\n")
w("- 中间 JSON（生成器 schema v1.0.0，schema 校验 0 错误）：`output/final/scheme_a/blind20-scan.json`、`output/final/scheme_b_standard/blind20-scan.json`、`output/final/scheme_b_unlimited/blind20-scan.json`、`output/final/diag_all_general/blind20-scan.json`")
w("- 原始响应：`output/scheme_a/raw/`、`output/scheme_b_standard/raw/`、`output/scheme_b_unlimited/raw/`；调用日志 `output/scheme_a/call_summary.json`、`output/*/cost.json`")
w("- 分数：`output/compare/scores.json`（original / corrected 两种参照）；核图裁图：`output/compare/verify/*.png`")
open(R/"compare/report.md", "w").write("\n".join(L) + "\n")
print("ok", len(L))
