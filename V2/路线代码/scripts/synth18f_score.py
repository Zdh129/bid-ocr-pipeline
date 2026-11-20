#!/usr/bin/env python3
"""synth18f 一次性计分（配置甲；scripts/synth18f_run.py score 调用；不联网）。
顺序：校验冻结 → 建全部版本预测（不看参照）→ 逐版本记输出 sha（写 输出sha_计分前.json）→ 打开参照与 manifest、核对参照 sha
→ 写 SCORED（独占；之后再跑拒绝）→ 计分 → 中文报告 output/synth18f/报告_中文.md。
版本：文档解析原始结果、方案E、推荐组合去掉变体三（不含纠错）、配置甲；诊断行：配置甲去掉变体一乙。三种口径、doc_type 分版面、新错逐条、变体一乙删字逐条。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import jia_eval as J  # noqa: E402
import synth18f_run as R  # noqa: E402


def main():
    R.verify()
    if (R.OUT / "SCORED").exists():
        sys.exit("已计过分，拒绝再跑")
    _, s = R.live_cfg()
    import f1r2 as F
    F.OUT = R.OUT
    F.DS["synth18f"] = {"dir": R.S18F, "b": R.b_doc_path(), "root": R.WO, "a": R.WP / "output" / "scheme_a",
                        "u": R.WP / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
                        "reread": R.OUT / "reread" / "A_P2" / "results.json", "render": R.WO / "output" / "scheme_a" / "renders",
                        "llm_caches": [], "llm_cfg": None, "llm_spent_before": 0.0, "blind": True,
                        "a_orig_summary": R.WO / "output" / "scheme_a" / "call_summary.json"}
    for k in ("seal_leak", "vertical"):
        assert (R.OUT / "synth18f" / k / "results.json").exists(), f"缺 {k} 结果（先跑 seal / vert）"
    c = F.Ctx("synth18f")
    preds, counts, logs, notes = J.build(c, llm_ok=False)
    R.wj(R.OUT / "pred_pages.json", preds)
    R.wj(R.OUT / "输出sha_计分前.json", {"at": R.now(), "pred_pages.json": R.sha(R.OUT / "pred_pages.json"),
         "各版本": {n: hashlib.sha256(json.dumps(p, ensure_ascii=False, sort_keys=True).encode()).hexdigest() for n, p in preds.items()}})
    print("全部版本输出已生成并记 sha", R.sha(R.OUT / "pred_pages.json"), flush=True)
    # ---- 到这里才打开参照
    ref_sha = {n: R.sha(R.S18F / n) for n in ("blind-ref.txt", "blind-ref.json", "manifest.json")}
    exp = s.get("reference_sha256_expected")
    if exp and ref_sha["blind-ref.txt"] != exp:
        sys.exit(f"参照 sha 不符：{ref_sha['blind-ref.txt']} ≠ {exp}，停止（未计分）")
    with open(R.OUT / "SCORED", "x") as f:
        f.write(R.now())
    res, scorer = J.score(preds, R.S18F)
    diag = J.seal_diag(c, logs, scorer)
    led = R.ledger()
    R.wj(R.OUT / "scores.json", {"scored_at": R.now(), "frozen_sha256": R.sha(R.FROZEN), "ref_sha256": ref_sha, "baidu": led, "notes": notes,
                                 "results": {n: {k: v for k, v in r.items() if k != "rows"} for n, r in res.items()}, "counts": counts,
                                 "steps": {n: l["steps"] for n, l in logs.items()}, "seal_diag": diag})
    R.wj(R.OUT / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
    names = [n for n in res if n != J.NOSEAL]
    L = ["# synth18f 留出集一次性验证（配置甲）", "", f"计分时间 {R.now()[:19].replace('T', ' ')}（伦敦时间）。冻结清单 `output/配置甲/frozen_配置甲.json` sha256 `{R.sha(R.FROZEN)}`。",
         "参照 sha256：" + "；".join(f"`{k}` `{v}`" for k, v in ref_sha.items()), "", "## 一、总分（读出率 / 错字率（漏字/错字/多字））", "",
         "| 版本 | 主口径 | 剔除签名口径 | 严格口径 | 新错比文档解析 | 新错比方案E | 新错比推荐去三 |", "|---|---|---|---|---|---|---|"]
    for n in names:
        r = res[n]
        cc = lambda t: str(sum(e["cost"] for e in r[t])) if t in r else "—"  # noqa: E731
        L.append(f"| {n} | {J.fmt(r['tol_in'])} | {J.fmt(r['tol_out'])} | {J.fmt(r['strict'])} | {cc('new_vs_b')} | {cc('new_vs_e')} | {cc('new_vs_r3n')} |")
    for key, title in (("strict_by_doc_type", "严格口径"), ("tol_in_by_doc_type", "主口径")):
        types = sorted({t for n in names for t in res[n][key]})
        L += ["", f"## 按 doc_type 分版面·{title}", "", "| 版面（页数） | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for t in types:
            pg = next(res[n][key][t]["pages"] for n in names if t in res[n][key])
            L.append(f"| {t}（{pg}） | " + " | ".join(J.fmt(res[n][key][t]) if t in res[n][key] else "—" for n in names) + " |")
    r = res[J.JIA_NAME]
    for tag, lab in (("new_vs_b", "比文档解析原始结果"), ("new_vs_e", "比方案E"), ("new_vs_r3n", "比推荐组合去掉变体三"), ("new_vs_noseal", "变体一乙新引入")):
        es = r.get(tag, [])
        L += ["", f"## 配置甲新引入的错误·{lab}：{len(es)} 处 / {sum(e['cost'] for e in es)} 字", ""]
        L += [f"- 第 {e['page']} 页［{e['category']}］「{e['ref']}」→「{e['pred']}」（{e['kind']}，{e['cost']} 字）" for e in es]
    L += ["", "## 变体一乙删掉的块", "", "| 页 | 版面 | 删掉的字 | 判定 | 依据 |", "|---|---|---|---|---|"]
    for d in diag:
        v = "**误删真字**" if d["真字（在参照正文或表格里）"] else ("章文（删对）" if d["在参照印章里"] else "参照里没有")
        L.append(f"| {d['page']} | {d['doc_type']} | 「{d['removed']}」 | {v} | {J.why(d)} |")
    L += ["", "## 调用与费用", "", f"- 百度：调用 {led['calls']}；本轮付费 ¥{led['paid_cny_this_round']}，累计 ¥{led['paid_cumulative_cny']}（上限 ¥{led['cap_cny']}）；剩余免费额度 {led['free_left']}。",
          "- 深度求索、gpt-4o、6.2：未使用。"] + [f"- 说明：{x}" for x in notes]
    (R.OUT / "报告_中文.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
