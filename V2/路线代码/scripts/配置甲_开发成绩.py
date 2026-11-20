#!/usr/bin/env python3
"""配置甲 开发成绩（非盲）：synth18、synth18c、synth18d、synth18e 四套都已计过分，这里只用已缓存的百度结果重跑后处理，不联网。
某一步没有缓存就停在那一步（不外发），写清楚哪套、哪个接口、多少次、预估费用；其余照常跑。
产出：output/配置甲/开发成绩/<套>/{scores.json, diff_vs_ref.json, pred_pages.json}，报告 output/配置甲/开发成绩_中文.md。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import jia_eval as J  # noqa: E402
import synth18f_run as R  # noqa: E402

OUTD = ROOT / "output" / "配置甲"


def entry(out, sname):
    return {"dir": Path(f"/workspace/pdf-test/{sname}"), "b": out / "orig" / "work" / "output" / "final" / "scheme_b_standard" / "blind20-scan.json",
            "root": out / "orig" / "work", "a": out / "P2" / "work" / "output" / "scheme_a",
            "u": out / "P2" / "work" / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json",
            "reread": out / "reread" / "A_P2" / "results.json", "render": out / "orig" / "work" / "output" / "scheme_a" / "renders",
            "llm_caches": [], "llm_cfg": None, "llm_spent_before": 0.0, "blind": False,
            "a_orig_summary": out / "orig" / "work" / "output" / "scheme_a" / "call_summary.json"}


SETS = {"synth18": (ROOT / "output" / "f1_round2", None, True), "synth18c": (ROOT / "output" / "f1_round2", None, True),
        "synth18d": (ROOT / "output" / "synth18d", entry(ROOT / "output" / "synth18d", "synth18d"), True),
        "synth18e": (ROOT / "output" / "synth18e", entry(ROOT / "output" / "synth18e", "synth18e"), False)}   # synth18e 未获批深度求索


def one(name):
    import f1r2 as F
    from baidu_ocr.postprocess import seal_leak as S
    fout, ent, llm_ok = SETS[name]
    F.OUT = fout
    if ent:
        F.DS[name] = ent
    d = F.DS[name]
    seal_res = fout / name / "seal_leak" / "results.json"
    vert_res = fout / name / "vertical" / "results.json"
    blocked = []
    if not seal_res.exists():
        units = S.select(json.loads(Path(d["b"]).read_text(encoding="utf-8")), Path(d["render"]))
        pages = sorted({u["page"] for u in units})
        blocked.append({"套": name, "步骤": "变体一乙 印章混入去红重识（scripts/f1r2_seal_call.py）", "接口": "百度 高精度识别（accurate，含位置）",
                        "次数": len(pages), "候选块": len(units), "页": pages,
                        "预估费用": f"免费额度内 ¥0（当前剩余高精度识别 137 次）；若按列表价 ¥0.04/次 = ¥{0.04 * len(pages):.2f}",
                        "影响": "配置甲、推荐组合去掉变体三两行都要这一步，未外发、未出分"})
    if not vert_res.exists():
        blocked.append({"套": name, "步骤": "N2 页边竖排识别", "接口": "百度 高精度识别", "影响": "缺缓存"})
    c = F.Ctx(name)
    if blocked:
        from baidu_ocr.postprocess import pred_pages
        import copy
        preds = {J.B_NAME: pred_pages(copy.deepcopy(c.doc))}
        preds[J.E_NAME], counts_e, lg = c.run(J.E, llm=None)
        counts, logs, notes = {J.E_NAME: counts_e}, {J.E_NAME: {"steps": {}}}, []
    else:
        preds, counts, logs, notes = J.build(c, llm_ok=llm_ok)
    if not llm_ok:
        notes.append(f"{J.R3L}：{name} 未获批深度求索，不外发，此行不出")
    res, scorer = J.score(preds, d["dir"])
    diag = J.seal_diag(c, logs, scorer) if not blocked else []
    od = OUTD / "开发成绩" / name
    R.wj(od / "pred_pages.json", preds)
    R.wj(od / "diff_vs_ref.json", {n: r["rows"] for n, r in res.items()})
    R.wj(od / "scores.json", {"at": R.now(), "results": {n: {k: v for k, v in r.items() if k != "rows"} for n, r in res.items()},
                              "counts": counts, "steps": {n: l.get("steps") for n, l in logs.items()}, "seal_diag": diag, "blocked": blocked, "notes": notes})
    return res, diag, blocked, notes, logs


def report(all_):
    fz = R.jl(R.FROZEN)
    L = ["# 配置甲 · 开发成绩（非盲）", "", "> **开发成绩（非盲）**：synth18、synth18c、synth18d、synth18e 四套之前都已计过分，参照已被看过；本报告只用已缓存的百度结果重跑后处理，**不能当作泛化成绩**。", "",
         f"冻结清单 `output/配置甲/frozen_配置甲.json`，sha256 `{R.sha(R.FROZEN)}`，冻结时间 {fz['冻结时间_伦敦']}（伦敦时间）。生成时间 {R.now()[:19].replace('T', ' ')}（伦敦时间）。", "",
         "配置甲 = 方案F1（不加纠错）＋变体一乙（只删整块）＋变体二（发票重排第三版）＋N2（页边竖排）＋N4（金额核对）；关闭变体三、N1 甲／乙、N3、任何大模型纠错。", "",
         "对照：文档解析原始结果、方案E、推荐组合去掉变体三（不含纠错；有深度求索缓存的套另报含纠错版）。「诊断：配置甲去掉变体一乙」只用来定位变体一乙删掉的字。", "",
         "每格：读出率 / 错字率（漏字/错字/多字）。新错 = 严格口径下的新引入错误代价字数。", ""]
    blocked_all = []
    for name, (res, diag, blocked, notes, logs) in all_.items():
        L += [f"## {name}（开发成绩·非盲）", ""]
        if blocked:
            blocked_all += blocked
            L += ["**停在变体一乙这一步：没有缓存，必须外发百度，未执行。** 配置甲和推荐组合去掉变体三两行未出分。", ""]
            for b in blocked:
                L.append(f"- 套：{b['套']}；步骤：{b['步骤']}；接口：{b['接口']}；次数：{b.get('次数')}（候选块 {b.get('候选块')} 个，页 {b.get('页')}）；预估费用：{b.get('预估费用')}")
            L.append("")
        for nt in notes:
            L.append(f"- 说明：{nt}")
        if notes:
            L.append("")
        names = [n for n in res if n != J.NOSEAL]
        L += ["| 版本 | 主口径 | 剔除签名口径 | 严格口径 | 新错比文档解析 | 新错比方案E | 新错比推荐去三（不含纠错） |", "|---|---|---|---|---|---|---|"]
        for n in names:
            r = res[n]
            c = lambda t: str(sum(e["cost"] for e in r[t])) if t in r else "—"  # noqa: E731
            L.append(f"| {n} | {J.fmt(r['tol_in'])} | {J.fmt(r['tol_out'])} | {J.fmt(r['strict'])} | {c('new_vs_b')} | {c('new_vs_e')} | {c('new_vs_r3n')} |")
        types = sorted({t for n in names for t in res[n]["strict_by_doc_type"]})
        L += ["", f"按 manifest 的 doc_type 分版面（严格口径）：", "", "| 版面（页数） | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for t in types:
            pg = next(res[n]["strict_by_doc_type"][t]["pages"] for n in names if t in res[n]["strict_by_doc_type"])
            L.append(f"| {t}（{pg}） | " + " | ".join(J.fmt(res[n]["strict_by_doc_type"][t]) if t in res[n]["strict_by_doc_type"] else "—" for n in names) + " |")
        if J.JIA_NAME in res:
            r = res[J.JIA_NAME]
            for tag, lab in (("new_vs_b", "比文档解析原始结果"), ("new_vs_e", "比方案E"), ("new_vs_r3n", "比推荐组合去掉变体三（不含纠错）"), ("new_vs_noseal", "比配置甲去掉变体一乙（＝变体一乙新引入）")):
                es = r.get(tag, [])
                L += ["", f"配置甲新引入的错误·{lab}：{len(es)} 处 / {sum(e['cost'] for e in es)} 字", ""]
                for e in es:
                    L.append(f"- 第 {e['page']} 页［{e['category']}］「{e['ref']}」→「{e['pred']}」（{e['kind']}，{e['cost']} 字）")
            inv = [d for d in diag if d["doc_type"] == "发票"]
            bad = [d for d in diag if d["真字（在参照正文或表格里）"]]
            L += ["", f"变体一乙删掉的块：共 {len(diag)} 块，其中发票页 {len(inv)} 块；删掉的是参照正文／表格里真字的 {len(bad)} 块"
                  f"（发票页 {sum(1 for d in bad if d['doc_type'] == '发票')} 块）。", ""]
            L += ["| 页 | 版面 | 删掉的字 | 判定 | 被当成章文的依据 |", "|---|---|---|---|---|"]
            for d in diag:
                verdict = "**误删真字**" if d["真字（在参照正文或表格里）"] else ("章文（删对）" if d["在参照印章里"] else "参照里没有（删对或无关）")
                L.append(f"| {d['page']} | {d['doc_type']} | 「{d['removed']}」 | {verdict} | {J.why(d)} |")
        L.append("")
    L += ["## 汇总：需要外发才能补齐的步骤（均未执行）", ""]
    if blocked_all:
        for b in blocked_all:
            L.append(f"- {b['套']}：{b['步骤']}，{b['接口']}，{b.get('次数')} 次，预估费用 {b.get('预估费用')}。")
    else:
        L.append("- 无。")
    L += ["", "## 约束", "", "- 本次没有调用百度、深度求索或任何外部接口；只读已缓存结果。生产配置、数据目录、参照只读。日志不写密钥。synth18f 未触碰。", ""]
    (OUTD / "开发成绩_中文.md").write_text("\n".join(L), encoding="utf-8")


def main():
    R.verify()
    all_ = {}
    for name in SETS:
        all_[name] = one(name)
        r = all_[name][0]
        print(name, {n: (J.fmt(x["tol_in"]), J.fmt(x["strict"])) for n, x in r.items()}, "blocked" if all_[name][2] else "", flush=True)
    report(all_)
    print("report written")


if __name__ == "__main__":
    main()
