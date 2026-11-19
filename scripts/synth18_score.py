#!/usr/bin/env python3
"""synth18 计分与报告（不联网）。由 `scripts/synth18_run.py score` 调用。

计分：直接用 scripts/blind20_score.py 的 parse_ref / score_system（规则不改），只把参照与 manifest 换成 synth18 的；
不套用 blind20 的参照修正（MANUAL 清空、corrections=None）。另做一版「剔除签名」参照：按 blind-ref.json 里
handwriting_sign 块，从其所在参照行末尾去掉签名字（与 blind20 口径——签名不入参照——对比用）。
"""
from __future__ import annotations

import copy
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from baidu_ocr.postprocess import pred_pages, run_postprocess  # noqa: E402
from baidu_ocr.postprocess.aux import load_aux  # noqa: E402
from baidu_ocr.postprocess.pipeline import merged_cfg  # noqa: E402
from baidu_ocr.serialize import validate_schema  # noqa: E402
import synth18_run as S  # noqa: E402

BLIND20 = {"B": (98.69, 1.67), "v1": (99.57, 0.61), "v2_no_posthoc_rules": (99.58, 0.85), "v2": (99.75, 0.43)}
VARS = [("B 原始（不加后处理）", "base"), ("v1（不加 6.2）", "v1"), ("v2_no_posthoc_rules（不加两条护栏，泛化结论用）", "v2_no_posthoc_rules"),
        ("v2 全开", "v2"), ("参考：v2 只用 (a)", "v2_a_only"), ("参考：v2 只用 (b)", "v2_b_only")]
BUCKETS = ["星号", "竖排", "顺序", "标点", "签名", "真错字", "印章混入", "漏读和杂字"]


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def deep_update(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            deep_update(a[k], v)
        else:
            a[k] = v
    return a


def ref_texts():
    rj = jl(S.S18 / "blind-ref.json")
    full = (S.S18 / "blind-ref.txt").read_text(encoding="utf-8")
    rec = "".join(f"=====第{p['page']}页=====\n" + "".join(l["text"] + "\n" for l in p["ref_lines"]) for p in rj["pages"])
    assert rec == full, "blind-ref.json 的 ref_lines 与 blind-ref.txt 不一致"
    out = []
    removed = []
    for p in rj["pages"]:
        lines = {l["n"]: l["text"] for l in p["ref_lines"]}
        for b in p["blocks"]:
            if b["type"] == "handwriting_sign":
                t = lines[b["ref_line"]]
                i = t.rfind(b["text"])
                assert i >= 0
                lines[b["ref_line"]] = t[:i] + t[i + len(b["text"]):]
                removed.append({"page": p["page"], "text": b["text"], "line_after": lines[b["ref_line"]]})
        out.append(f"=====第{p['page']}页=====\n" + "".join(lines[n] + "\n" for n in sorted(lines)))
    return full, "".join(out), rj, removed


def special_ranges(bs, refp_full, rj):
    """各页规范化参照串里 签名 / 手写日期 / 页边竖排 的位置区间（只对 full 参照）。"""
    rng = defaultdict(list)
    for p in rj["pages"]:
        pg = p["page"]
        rs, _ = bs.ref_chars(refp_full[pg])
        for b in p["blocks"]:
            kind = {"handwriting_sign": "签名", "handwriting_date": "手写日期", "invoice_vertical": "竖排"}.get(b["type"])
            if not kind:
                continue
            t = bs.norm(b["text"])
            line = bs.norm([l["text"] for l in p["ref_lines"] if l["n"] == b["ref_line"]][0])
            j = rs.find(line)
            k = line.rfind(t)
            if j >= 0 and k >= 0:
                rng[pg].append((j + k, j + k + len(t), kind))
    return rng


def main():
    import bestconfig_run as bc

    t_all = time.perf_counter()
    fz = S.verify_frozen()
    cfg_all = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert cfg_all["allow_external"] is False and cfg_all["postprocess"]["vlm_backfill"]["enabled"] is False
    pp = merged_cfg(cfg_all["postprocess"])
    b_doc = jl(S.W / "final" / "scheme_b_standard" / "blind20-scan.json")
    u_ok = (S.W / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json").exists()
    assert u_ok, "缺 Unlimited 原始返回"
    aux = load_aux(S.WR, b_doc)
    man = jl(S.VLM / "manifest.json")["items"]
    results = {}
    for it in man:
        rp = S.VLM / "vlm" / "results" / f"{it['id']}.json"
        if rp.exists():
            r = jl(rp)
            assert r["crop_sha256"] == it["sha256"] and r["prompt_sha12"] == it["prompt_sha12"]
            results[it["id"]] = r
    aux["vlm"] = {"items": man, "results": results}

    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    bs.REF, bs.MANIFEST = S.S18 / "blind-ref.txt", S.S18 / "manifest.json"
    bs.MANUAL.clear()
    full, nosign, rj, removed = ref_texts()
    refs = {"full": bs.parse_ref(full), "nosign": bs.parse_ref(nosign)}
    (S.OUT / "blind-ref.nosign.txt").write_text(nosign, encoding="utf-8")
    mpages = {int(p["output_page"]): p for p in jl(S.S18 / "manifest.json")["pages"]}
    rng = special_ranges(bs, refs["full"], rj)

    R, docs = {}, {}
    for title, key in VARS:
        t0 = time.perf_counter()
        if key == "base":
            d, info = copy.deepcopy(b_doc), {"counts": {}, "log": [], "cfg": {}}
            pr = pred_pages(d)
        else:
            c = deep_update(copy.deepcopy(pp), fz["variants"][key]["postprocess_overrides"])
            d, info = run_postprocess(b_doc, c, aux)
            pr = pred_pages(d)
        tt = time.perf_counter() - t0
        sc = {rk: bs.score_system(pr, rv, mpages, None) for rk, rv in refs.items()}
        R[key] = {"title": title, "counts": info["counts"], "sec": round(tt, 3), "summary": {rk: bc.summ(v) for rk, v in sc.items()}, "_full": sc}
        docs[key] = (d, pr, info)
    # 诊断（不是调参）：从 v1 逐步去掉一步，看每步在 synth18 上的贡献
    diag = {}
    for step in ("star", "seal_strip", "fill_gaps", "invoice_reorder"):
        c = deep_update(copy.deepcopy(pp), fz["variants"]["v1"]["postprocess_overrides"])
        c[step] = {**c[step], "enabled": False}
        d, _ = run_postprocess(b_doc, c, aux)
        diag[step] = bc.summ(bs.score_system(pred_pages(d), refs["full"], mpages, None))
    errs = {k: validate_schema(docs[k][0], ROOT / "schemas" / "intermediate.schema.json") for k in ("v1", "v2_no_posthoc_rules", "v2")}
    S.wj(S.OUT / "blind20-scan.v2.json", docs["v2"][0])  # 名字沿用不便；另存一份规范名
    (S.OUT / "blind20-scan.v2.json").rename(S.OUT / "synth18-scan.v2.json")
    S.wj(S.OUT / "synth18-scan.v2_no_posthoc_rules.json", docs["v2_no_posthoc_rules"][0])
    S.wj(S.OUT / "synth18-scan.v1.json", docs["v1"][0])
    S.wj(S.OUT / "pred_pages.json", {k: docs[k][1] for k in docs})
    S.wj(S.OUT / "postprocess_log.json", {k: {"counts": docs[k][2]["counts"], "log": docs[k][2]["log"]} for k in docs if k != "base"})

    # 错字分类（full 参照）
    hs = bc.load_mod("hybrid_score", ROOT / "scripts" / "hybrid_score.py")
    hs.load_bs = lambda: bs
    bc.SIGN = ()  # blind20 专用的签名名单不适用

    def cats(key, rk="full"):
        full_ = R[key]["_full"][rk]
        pr = docs[key][1]
        seal_zone = defaultdict(set)
        for c in docs[key][2].get("log") or []:
            if c.get("step") == "seal_strip" and str(c.get("reason", "")).startswith("P"):
                seal_zone[c["page"]].add(bs.norm(c["text"]))
        tot, rows = Counter(), []
        spans = sorted(full_["error_spans"], key=lambda s: (s["page"], s["ref_start"]))
        for i, sp in enumerate(spans):
            p = sp["page"]
            rs, _ = hs.ref_page_string(bs, refs[rk], p, None)
            ps = bs.norm("".join(s["text"] for s in pr[str(p)]["sections"]))
            seal_chars = set(bs.norm(pr[str(p)].get("seal") or "")) | set(bs.norm("".join(refs[rk][p]["seals"])))
            same = [o for j, o in enumerate(spans) if o["page"] == p and j != i]
            c, why = bc.categorize(sp, ps, rs, seal_chars, seal_zone.get(p, set()), "".join(o["ref"] for o in same), "".join(o["pred"] for o in same))
            a, b = sp["ref_start"], sp["ref_end"]
            sk = None
            if rk == "full":
                for (x0, x1, kind) in rng.get(p, []):
                    if (a < x1 and b > x0) or (a == b and x0 <= a <= x1):
                        sk = kind
                        break
            if c == "星号":
                bk = "星号"
            elif sk == "签名":
                bk, why = "签名", "手写签名区"
            elif sk == "竖排" or why == "发票竖排小字漏读":
                bk = "竖排"
            elif c in ("顺序", "标点", "真错字", "印章混入"):
                bk = c
            else:
                bk = "漏读和杂字"
            if sk == "手写日期":
                why += "（手写日期区）"
            tot[bk] += sp["cost"]
            rows.append({**{k: sp[k] for k in ("page", "section", "ref", "pred", "cost", "context")}, "category": bk, "detail": why})
        return dict(tot), rows
    C = {k: cats(k) for _, k in VARS}
    S.wj(S.OUT / "diff_vs_ref.json", {k: C[k][1] for k in ("base", "v1", "v2_no_posthoc_rules", "v2")})

    # ☆ 检查：参照里 4 个 ☆ 在各变体输出里变成了什么
    star_rows = []
    for p in sorted(refs["full"]):
        rs, _ = bs.ref_chars(refs["full"][p])
        for pos, ch in enumerate(rs):
            if ch != "☆":
                continue
            row = {"page": p, "context": rs[max(0, pos - 2):pos + 12]}
            for _, key in VARS[:4]:
                sp = next((s for s in R[key]["_full"]["full"]["error_spans"] if s["page"] == p and s["ref_start"] <= pos < max(s["ref_end"], s["ref_start"] + 1)), None)
                row[key] = "☆（正确）" if sp is None else f"{sp['ref'] or '∅'}→{sp['pred'] or '∅'}"
            star_rows.append(row)
    star_log = [e for e in docs["v1"][2]["log"] if e.get("step") == "star"]

    # 费用
    def cost(path):
        return jl(path) if Path(path).exists() else {}
    c_b, c_u, c_a = cost(S.W / "scheme_b_standard" / "cost.json"), cost(S.W / "scheme_b_unlimited" / "cost.json"), cost(S.W / "scheme_a" / "cost.json")
    a_sum = jl(S.W / "scheme_a" / "call_summary.json")
    led = [json.loads(l) for l in (S.VLM / "vlm" / "ledger.jsonl").read_text().splitlines() if l.strip()] if (S.VLM / "vlm" / "ledger.jsonl").exists() else []
    bst = cost(S.VLM / "vlm" / "batch_state.json")
    sst = cost(S.VLM / "vlm" / "standard_state.json")
    timing = {"script_sec": round(time.perf_counter() - t_all, 1)}
    S.wj(S.OUT / "scores.json", {"variants": {k: {kk: vv for kk, vv in v.items() if kk != "_full"} for k, v in R.items()},
                                 "per_page": {k: {p: {s: {m: v[s][m] for m in ("readout", "cer", "deletions", "substitutions", "insertions", "ref_chars")}
                                                       for s in ("overall", "body", "table", "seal")} for p, v in R[k]["_full"]["full"]["per_page"].items()} for k in R},
                                 "categories": {k: C[k][0] for k in C}, "stars": star_rows, "schema_errors": {k: len(v) for k, v in errs.items()},
                                 "removed_signatures": removed, "timing": timing})
    S.wj(S.OUT / "diag_steps.json", diag)
    write_report(bc, R, C, diag, star_rows, star_log, errs, removed, (c_b, c_u, c_a), a_sum, led, bst, sst, man, results, docs, mpages, fz)
    print(json.dumps({k: (R[k]["summary"]["full"]["overall"], R[k]["summary"]["nosign"]["overall"]) for k in R}, ensure_ascii=False))
    print("diag", {k: v["overall"] for k, v in diag.items()})
    print("cats", {k: C[k][0] for k in C})
    print("stars", star_rows)
    print("schema", {k: len(v) for k, v in errs.items()})


def lon(iso):
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%H:%M:%S") if iso else ""


def write_report(bc, R, C, diag, star_rows, star_log, errs, removed, costs, a_sum, led, bst, sst, man, results, docs, mpages, fz):
    c_b, c_u, c_a = costs
    s = {k: R[k]["summary"] for k in R}
    o = lambda k, rk="full": s[k][rk]["overall"]  # noqa: E731
    keys4 = ["base", "v1", "v2_no_posthoc_rules", "v2"]
    L = ["# synth18 端到端实跑：冻结的新最优解（bestconfig_v2）泛化检验", "",
         f"生成：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}（伦敦时间 UTC+0）。脚本 `scripts/synth18_run.py`（build/prepare/run/score）、`scripts/synth18_score.py`、"
         "`scripts/synth18_scheme_a_call.py`（A 调用，blind20 版逐行复制只改输入名，另加断点续跑与网络异常重试）。", "",
         "> 规则冻结：运行前 `freeze_config_v2.py --verify` 一致（54 个文件 sha256），本次没有改任何规则、护栏或提示词。"
         "新增的只是 live 配置（`config_synth18_live.yaml`：百度外发、关 PII 拦截；`config_astra_synth18_live.yaml`：6.2 外发、上限 = $5 − 已花），不在冻结清单内，冻结校验不受影响。", "",
         "> synth18 是另一方合成的新集：参照 = 排版原文（未经真人校对），手写为手写体字体模拟，扫描为算法模拟。v2 的两条护栏是看着 blind20 参照定的，所以泛化结论以 **v2_no_posthoc_rules** 为准。", "",
         "## 0. 结论", ""]
    L.append(f"- synth18（签名入参照，主口径）：B 原始 {o('base')['readout']:.2f}%/{o('base')['cer']:.2f}%，v1 {o('v1')['readout']:.2f}%/{o('v1')['cer']:.2f}%，"
             f"**v2_no_posthoc_rules {o('v2_no_posthoc_rules')['readout']:.2f}%/{o('v2_no_posthoc_rules')['cer']:.2f}%**，v2 {o('v2')['readout']:.2f}%/{o('v2')['cer']:.2f}%。")
    L.append(f"- 剔除签名（blind20 口径）：B {o('base','nosign')['readout']:.2f}%/{o('base','nosign')['cer']:.2f}%，v1 {o('v1','nosign')['readout']:.2f}%/{o('v1','nosign')['cer']:.2f}%，"
             f"v2_no_posthoc_rules {o('v2_no_posthoc_rules','nosign')['readout']:.2f}%/{o('v2_no_posthoc_rules','nosign')['cer']:.2f}%，v2 {o('v2','nosign')['readout']:.2f}%/{o('v2','nosign')['cer']:.2f}%。")
    L.append(f"- 目标（读出率 >99%、CER <1%）：v2_no_posthoc_rules {bc.verdict(o('v2_no_posthoc_rules'))}；v2 {bc.verdict(o('v2'))}。")
    L.append(f"- 中间 JSON schema 校验：" + "，".join(f"{k} {len(v)} 错" for k, v in errs.items()) + "。")
    L.append("")
    for rk, t in (("full", "签名入参照（主）"), ("nosign", "剔除签名（blind20 口径）")):
        L += [f"## 1{'a' if rk == 'full' else 'b'}. 总表 — {t}", "", bc.HDR]
        for k in keys4 + ["v2_a_only", "v2_b_only"]:
            L.append(bc.fmt_row(R[k]["title"], s[k][rk]))
        L.append("")
    L += ["## 2. 与 blind20 对照（读出率 / CER，正文+表格）", "", "| 方案 | blind20（修正参照） | synth18 签名入参照 | synth18 剔除签名 |", "|---|---|---|---|"]
    for k, nm in (("base", "B"), ("v1", "v1"), ("v2_no_posthoc_rules", "v2_no_posthoc_rules"), ("v2", "v2")):
        b20 = BLIND20["B" if k == "base" else k]
        L.append(f"| {R[k]['title']} | {b20[0]:.2f}% / {b20[1]:.2f}% | {o(k)['readout']:.2f}% / {o(k)['cer']:.2f}% | {o(k,'nosign')['readout']:.2f}% / {o(k,'nosign')['cer']:.2f}% |")
    L.append("")
    # ☆
    L += ["## 3. 4 个真实 ☆ 是否被 ★/☆ 规则误改（如实报告，规则不改）", "",
          "★/☆ 规则 R1：「☆ 后紧跟数字 → ★」（blind20 参照里 ☆ 0 次）。synth18 的 4 个 ☆ 全部紧跟条款号，正好落在 R1 的作用范围内。", "",
          "| 页 | 参照上下文 | B 原始 | v1 | v2_no_posthoc_rules | v2 |", "|---|---|---|---|---|---|"]
    for r in star_rows:
        L.append(f"| {r['page']} | {r['context']} | {r['base']} | {r['v1']} | {r['v2_no_posthoc_rules']} | {r['v2']} |")
    star_changes = [e for e in star_log if "☆" in str(e.get("before", "")) and "★" in str(e.get("after", ""))]
    L += ["", f"v1 后处理日志里 ☆→★ 的改动 {len(star_changes)} 处：", ""]
    for e in star_changes[:10]:
        L.append(f"- p{e.get('page')} `{e.get('before')}` → `{e.get('after')}`")
    L.append("")
    # categories
    L += ["## 4. 错字分类（签名入参照，按编辑代价计字）", "", "| 类别 | B 原始 | v1 | v2_no_posthoc_rules | v2 |", "|---|---|---|---|---|"]
    for b in BUCKETS:
        L.append(f"| {b} | " + " | ".join(str(C[k][0].get(b, 0)) for k in keys4) + " |")
    L.append("| **合计** | " + " | ".join(str(sum(C[k][0].values())) for k in keys4) + " |")
    L += ["", "分类与 blind20 同一函数（bestconfig_run.categorize），只把签名名单换成 blind-ref.json 的 handwriting_sign 区、竖排换成 invoice_vertical 区；"
          "「混合/多读/漏读」并入漏读和杂字；手写日期区的错在明细里加注。", "", "v2_no_posthoc_rules 剩余错字明细：", "",
          "| 页 | 区 | 参照 | 输出 | 字数 | 类别 | 说明 |", "|---|---|---|---|---|---|---|"]
    for r in C["v2_no_posthoc_rules"][1]:
        L.append(f"| {r['page']} | {r['section']} | {r['ref'] or '∅'} | {r['pred'] or '∅'} | {r['cost']} | {r['category']} | {r['detail']} |")
    L.append("")
    # 诊断
    L += ["### 4.1 主要失分与各步贡献（诊断，不据此改规则）", "",
          "从 v1 逐步关掉一步（签名入参照）：", "", "| v1 去掉 | 读出率 | CER | 删/替/插 | 相对 v1 CER |", "|---|---|---|---|---|"]
    for st, nm in (("star", "★/☆ 规则"), ("seal_strip", "剔除印章文字"), ("fill_gaps", "A+U 补漏"), ("invoice_reorder", "发票按坐标重排")):
        x = diag[st]["overall"]
        L.append(f"| {nm} | {x['readout']:.2f}% | {x['cer']:.2f}% | {x['D']}/{x['S']}/{x['I']} | {x['cer'] - o('v1')['cer']:+.2f}pp |")
    top = sorted(C["v2_no_posthoc_rules"][1], key=lambda r: -r["cost"])[:12]
    L += ["", "v2_no_posthoc_rules 最大的 12 段错误：", "", "| 页 | 参照 | 输出 | 字数 | 类别 |", "|---|---|---|---|---|"]
    for r in top:
        L.append(f"| {r['page']} | {r['ref'][:40] or '∅'} | {r['pred'][:40] or '∅'} | {r['cost']} | {r['category']} |")
    dates = {k: sum(r["cost"] for r in C[k][1] if "手写日期区" in r["detail"]) for k in keys4}
    L += ["", "手写日期区（参照 5 处）错字：" + "，".join(f"{R[k]['title'].split('（')[0]} {dates[k]}" for k in keys4) + "。", ""]
    # per page
    L += ["## 5. 逐页（签名入参照；读出率/CER，D/S/I）", "", "| 页 | 类型 | 参照字数 | B 原始 | v1 | v2_no_posthoc_rules | v2 | 印章 CER（v2） |", "|---|---|---|---|---|---|---|---|"]
    for p in sorted(R["base"]["_full"]["full"]["per_page"]):
        cells = []
        for k in keys4:
            x = R[k]["_full"]["full"]["per_page"][p]["overall"]
            cells.append(f"{x['readout'] * 100:.2f}/{x['cer'] * 100:.2f} ({x['deletions']}/{x['substitutions']}/{x['insertions']})")
        x = R["v2"]["_full"]["full"]["per_page"][p]
        seal = f"{x['seal']['cer'] * 100:.1f}%" if x["seal"]["ref_chars"] else "—"
        L.append(f"| {p} | {mpages[p]['doc_type']} | {x['overall']['ref_chars']} | " + " | ".join(cells) + f" | {seal} |")
    L.append("")
    # 6.2
    log = [e for e in docs["v2"][2]["log"] if e.get("step") == "vlm_backfill"]
    log0 = {e["item_id"]: e for e in docs["v2_no_posthoc_rules"][2]["log"] if e.get("step") == "vlm_backfill"}
    byid = {i["id"]: i for i in man}
    L += ["## 6. 用到 6.2 的位置", "", f"选区（不看参照）：漏读行 {sum(1 for i in man if i['scenario'] == 'missing_line')} 块、手写日期 {sum(1 for i in man if i['scenario'] == 'handwritten_date')} 块。", "",
          "| 场景 | 区域 | 页 | bbox(pt) | 底稿 | 6.2 | v2 动作 | v2_no_posthoc_rules 动作 |", "|---|---|---|---|---|---|---|---|"]
    for e in sorted(log, key=lambda e: (e["page"], e["item_id"])):
        it = byid[e["item_id"]]
        t62 = (results.get(e["item_id"]) or {}).get("text", "")
        before = e.get("before") if e["action"] in ("replace", "confirm") else (it["context"] or {}).get("b_text", "")
        a0 = log0.get(e["item_id"], {})
        L.append(f"| {'(a) 漏读行' if e['scenario'] == 'missing_line' else '(b) 手写日期'} | {e['item_id']} | {e['page']} | {it['bbox_pt']} | {before or '∅'} | {t62 or '∅'} | "
                 f"{e['action']}{'（' + e['why'] + '）' if e.get('why') else ''}{'，' + e['placed'] if e.get('placed') else ''} | {a0.get('action', '')}{'，' + a0['placed'] if a0.get('placed') else ''} |")
    L.append("")
    # costs
    def calls(c, api):
        x = (c.get("by_api") or {}).get(api) or {}
        return x.get("calls", 0), x.get("success", 0), x.get("cny", 0.0), x.get("elapsed_sec", 0.0)
    nb = calls(c_b, "docparser_standard")
    nu = calls(c_u, "docparser_unlimited")
    na = calls(c_a, "accurate")
    acc_total = na[1] + 4
    gen_total = 1
    L += ["## 7. 费用与调用次数", "", "### 百度（刊例价；按本地日志推算免费额度）", "",
          "| 接口 | 本次调用 | 计量 | 刊例价 | 耗时 | 免费额度（官方） | 本地日志已用（含本次） | 推算剩余 |", "|---|---|---|---|---|---|---|---|",
          f"| 文档解析标准版 | {nb[0]} 次提交（成功 {nb[1]}） | 18 页 | ¥{nb[2]:.2f} | {nb[3]:.1f} s | 200 页一次性 | 20 + 18 = 38 页 | 162 页 |",
          f"| 高精度含位置（accurate） | {acc_total} 次成功（第 1 次运行 p1–p5 中 4 次 + 续跑 {na[1]} 次：主调 13 + 补识 2）；另有 1 次网络断开未计费 | {acc_total} 次 | ¥{0.04 * acc_total:.2f} | 约 {na[3] + 26:.0f} s | 500 次/月（个人） | 28 + {acc_total} = {28 + acc_total} 次 | {500 - 28 - acc_total} 次 |",
          f"| 标准含位置（general，A 原走法 p3 正文页） | 1 次 | 1 次 | ¥0.01 | 6.3 s | 1000 次/月 | 20 + 1 = 21 次 | 979 次 |",
          f"| Unlimited-OCR | {nu[0]} 次提交（成功 {nu[1]}）；另有首次并发提交时连接被对端断开 1 次（未拿到 task_id，按未计费处理，无法 100% 确认） | 18 页 | ¥{nu[2]:.2f} | {nu[3]:.1f} s | 不确定（按付费） | — | — |", "",
          "注：免费额度剩余只按本机日志推算；同账号其他调用、是否已领取免费资源本地看不到。三条并发运行时 A 和 Unlimited 各遇到一次网络断开，A 改为断点续跑+网络异常重试后顺序重跑，Unlimited 顺序重跑。", "",
          "### OpenAI gpt-6.2-astra", ""]
    usd = sum(float(r.get("usd") or 0) for r in led)
    L += [f"- 调用：{len(led)} 次尝试（成功 {sum(1 for r in led if r.get('ok'))}）；输入 {sum(int(r.get('prompt_tokens') or 0) for r in led)} / 输出 {sum(int(r.get('completion_tokens') or 0) for r in led)} token（推理 {sum(int(r.get('reasoning_tokens') or 0) for r in led)}）；**${usd:.4f}**。",
          f"- 通道：Batch（{lon(bst.get('submitted_at'))} 伦敦时间提交，{bst.get('status')}，墙钟 {bst.get('wall_sec')} 秒）" + (f"；标准接口补跑 {sst.get('n')} 块，{sst.get('wall_sec')} 秒" if sst else "；未走标准接口") + "。",
          f"- astra 累计：${S.ASTRA_SPENT_BEFORE:.4f} + ${usd:.4f} = **${S.ASTRA_SPENT_BEFORE + usd:.4f}**（上限 $5）；项目 OpenAI 累计（含更早 gpt-4o/gpt-5-mini $0.4993）${S.ASTRA_SPENT_BEFORE + usd + 0.4993:.4f}。", "",
          "## 8. 文件与约束", "",
          "- 报告 `output/synth18/report.md`；中间 JSON `output/synth18/synth18-scan.{v1,v2_no_posthoc_rules,v2}.json`；分数 `scores.json`；逐字段 diff `diff_vs_ref.json`；后处理日志 `postprocess_log.json`；剔除签名参照 `blind-ref.nosign.txt`；"
          "6.2 清单/裁图/结果 `vlm/`；百度原始返回与中间 JSON `work/output/`（文件名沿用 blind20-scan.* 是因为加载代码写死了名字，内容来自 synth18-scan.pdf）。",
          "- 外发：百度只收到 synth18-scan.pdf（sha 13880016af84…）及其渲染页/补识拼图；OpenAI 只收到 vlm/manifest.json 列出的裁图（长边 ≤1600）。synth18-textlayer.pdf 未送任何 OCR。",
          "- 未改 frozen20、synth18/（只读）、`/workspace/pdf-test/ocr/config.yaml`；`config.yaml` 的 allow_external 仍为 false；未打印 key。"
          "（注：A 与 Unlimited 首次失败时 requests 的异常栈把带 access_token 的 URL 写进了本地日志，已就地替换为 ***REDACTED***；access_token 是 30 天临时令牌，不是 API Key。）", ""]
    (S.OUT / "report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
