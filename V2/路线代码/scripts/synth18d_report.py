#!/usr/bin/env python3
"""synth18d 中文报告：只读 output/synth18d/scores.json 等计分产物生成 output/synth18d/报告_中文.md（计分之后写的，不改任何分数）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
O = ROOT / "output" / "synth18d"
d = json.loads((O / "scores.json").read_text(encoding="utf-8"))
r = d["results"]
names = list(r)
F = lambda x: f"{x['readout']:.2f}% / {x['cer']:.2f}%（{x['D']}/{x['S']}/{x['I']}）"  # noqa: E731
RL, RN = "推荐组合（含纠错）", "推荐组合（不含纠错）"
fz = json.loads((O / "frozen_synth18d.json").read_text(encoding="utf-8"))
L = ["# synth18d 留出集一次性验证报告", "",
     f"计分时间：{d['scored_at'][:19].replace('T', ' ')}（伦敦时间）。计分只算一次（`output/synth18d/SCORED`）。", "",
     "## 一、冻结与流程", "",
     f"- 运行前冻结清单：`output/synth18d/frozen_synth18d.json`，sha256 `{d['frozen_sha256']}`，冻结时间 {fz['冻结时间_伦敦']}（伦敦时间），共 {len(fz['code_sha256'])} 个文件。",
     f"- 组合代码来自 `output/f1_round2/frozen_推荐组合.json`（sha256 `{d['frozen_rec_sha256']}`）和 `output/synth18e_prep/frozen_方案E.json`（sha256 `{d['frozen_e_sha256']}`），逐文件核对一致，一行未改。",
     "- 本轮新写的只有运行／计分／深度求索调用脚手架和两份 live 配置（`config_synth18d_live.yaml`、`config_llm_synth18d_live.yaml`，写入了批准原话），都在清单里。",
     "- 就绪检查：`scripts/ready_check_推荐组合.py --dir /workspace/pdf-test/synth18d` 判为就绪，阻断 0、警告 0；扫描件与参照 sha 与给定值一致。就绪检查本身会做参照格式检查（父 agent 指定先跑）。",
     f"- 全部版本输出先生成并记 sha（`output/synth18d/输出sha.json`，pred_pages.json sha256 见该文件），之后才打开参照与 manifest。参照 sha256：" + "；".join(f"`{k}` `{v}`" for k, v in d["ref_sha256"].items()) + "（blind-ref.txt 与给定值一致）。",
     "- v2：用旧冻结清单 `frozen_config_v2.json` 的代码和 v2 覆盖项；6.2 未获批，所以没有回填结果（与 v1 基本相同）。",
     "- 三个去掉单项的版本都以推荐组合（含纠错）为基准，只关开关，未调参。「去掉深度求索纠错」与「推荐组合（不含纠错）」是同一配置，分数相同。", "",
     "## 二、总分（读出率 / 错字率（漏字/错字/多字））", "", "| 版本 | 主口径 | 剔除签名口径 | 严格口径 |", "|---|---|---|---|"]
for n in names:
    L.append(f"| {n} | {F(r[n]['tol_in'])} | {F(r[n]['tol_out'])} | {F(r[n]['strict'])} |")
L += ["", "## 三、新引入错误总量（严格口径下的代价字数）", "", "| 版本 | 比文档解析 | 比 v2 | 比方案F1 | 比推荐组合（含纠错） | 比推荐组合（不含纠错） |", "|---|---|---|---|---|---|"]
for n in names:
    c = lambda t: str(sum(e["cost"] for e in r[n][t])) if t in r[n] else "—"  # noqa: E731
    L.append(f"| {n} | {c('new_vs_b')} | {c('new_vs_v2')} | {c('new_vs_f1')} | {c('new_vs_rl')} | {c('new_vs_rn')} |")
types = list(r[RL]["strict_by_doc_type"])
for key, title, no in (("strict_by_doc_type", "严格口径", "四"), ("tol_in_by_doc_type", "主口径", "五")):
    L += ["", f"## {no}、按版面类型（manifest 的 doc_type）· {title}", "", "| 版面（页数） | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for t in types:
        pg = r[RL][key][t]["pages"]
        L.append(f"| {t}（{pg if isinstance(pg, int) else len(pg)}） | " + " | ".join(F(r[n][key][t]) if t in r[n][key] else "—" for n in names) + " |")
L += ["", "## 六、新引入的错误逐条（严格口径）", ""]
def lst(n, t, lab):
    es = r[n].get(t, [])
    L.append(f"### {n} {lab}：{len(es)} 处 / {sum(e['cost'] for e in es)} 字")
    L.append("")
    for e in es:
        L.append(f"- 第 {e['page']} 页［{e['category']}］「{e['ref']}」→「{e['pred']}」（{e['kind']}，{e['cost']} 字）")
    L.append("")
lst(RL, "new_vs_b", "比文档解析原始结果")
lst(RL, "new_vs_v2", "比 v2")
lst(RL, "new_vs_f1", "比方案F1")
lst(RL, "new_vs_rn", "比推荐组合（不含纠错）＝深度求索纠错新引入")
for n in ("推荐组合（含纠错）去掉变体一乙", "推荐组合（含纠错）去掉变体三"):
    lst(n, "new_vs_rl", "比推荐组合（含纠错）")
lst(RN, "new_vs_b", "比文档解析原始结果")
lst("参考：方案E（本轮之前冻结）", "new_vs_b", "比文档解析原始结果")
b, l = d["baidu"], d["llm"]
L += ["## 七、调用次数和费用", "",
      f"- 百度（synth18d 全部 18 页及派生图）：文档解析 {b['calls']['docparser_standard_pages']} 页（免费额度）、无限量识别 {b['calls']['unlimited_pages']} 页（原图 18＋颜色去章图 18，付费）、"
      f"高精度识别 {b['calls']['accurate']} 次、通用识别 {b['calls']['general']} 次（均在免费额度内）。",
      f"- 百度本轮付费 ¥{b['paid_cny_this_round']}，累计付费 ¥{b['paid_cumulative_cny']}（上限 ¥{b['cap_cny']:.0f}）。剩余免费额度：文档解析 {b['free_left']['docparser_pages']} 页、高精度识别 {b['free_left']['accurate_calls']} 次、通用识别 {b['free_left']['general_calls']} 次。",
      f"- 深度求索：只发表格文字，prompt_v1 未改；{l['requests_ok']} 次调用全部成功（尝试 {l['attempts']} 次），输入 {l['prompt_tokens']}、输出 {l['completion_tokens']} 个计费单位，实际 ${l['usd']}（上限 $1）。"
      "发送前规模：11 段去重后的表格块，11 页、12 段、4014 字，最坏预估 $0.01555。",
      "- 未使用 gpt-4o（OpenAI 模型）和 6.2。", ""]
(O / "报告_中文.md").write_text("\n".join(L), encoding="utf-8")
print("ok", len(L))
