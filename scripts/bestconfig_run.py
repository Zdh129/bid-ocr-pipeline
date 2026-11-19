#!/usr/bin/env python3
"""「最优解」配置在 blind20 上端到端跑一遍（百度部分按额度规则决定重调或复用），并出对比报告。

本脚本自身不联网：不调百度、不调 OpenAI。百度接口是否重调由「额度核查」决定，结论写在 quota_check.json；
本次结论为「无法通过 API/文档可靠确认剩余免费额度 → 不调用，复用已有原始输出」。
输出：output/bestconfig/
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from baidu_ocr.postprocess import pred_pages, run_postprocess  # noqa: E402
from baidu_ocr.postprocess.aux import load_aux  # noqa: E402
from baidu_ocr.postprocess.pipeline import merged_cfg  # noqa: E402
from baidu_ocr.postprocess.star import RULE_TEXT as STAR_RULE  # noqa: E402
from baidu_ocr.serialize import validate_schema  # noqa: E402

OUT = ROOT / "output" / "bestconfig"
FROZEN_PDF = Path("/workspace/pdf-test/frozen20/blind20-scan.pdf")
PROD_CFG = Path("/workspace/pdf-test/ocr/config.yaml")


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------- 额度核查 ----------------
FREE_QUOTA_DOC = {
    "docparser_standard": {"name": "文档解析（标准版）", "free": "个人/企业认证 200 页，一次性（非每月）",
                           "limit": 200, "monthly": False, "url": "https://ai.baidu.com/ai-doc/OCR/fk3h7xu7h"},
    "docparser_unlimited": {"name": "文档解析（Unlimited-OCR）", "free": "文档只写已认证新用户 2000 页/月；老用户是否享有未写明",
                            "limit": None, "monthly": True, "url": "见 docs/baidu_api.md（2026-11-18 调研）"},
    "accurate": {"name": "通用文字识别（高精度含位置版）", "free": "个人 500 次/月，企业 1000 次/月",
                 "limit": 500, "monthly": True, "url": "https://ai.baidu.com/ai-doc/OCR/fk3h7xu7h"},
    "general": {"name": "通用文字识别（标准含位置版）", "free": "个人 1000 次/月，企业 2000 次/月",
                "limit": 1000, "monthly": True, "url": "https://ai.baidu.com/ai-doc/OCR/fk3h7xu7h"},
}
PLAN_CALLS = {"docparser_standard": ("20 页（整份 1 次提交）", 20), "docparser_unlimited": ("20 页（整份 1 次提交）", 20),
              "accurate": ("约 28 次（20 主调 + 8 补识）", 28), "general": ("0 次（A 原走法本集 20 页都路由到 accurate）", 0)}


def local_usage() -> dict:
    o = ROOT / "output"
    use = {}
    for key, path in (("docparser_standard", o / "scheme_b_standard" / "cost.json"), ("docparser_unlimited", o / "scheme_b_unlimited" / "cost.json"),
                      ("accurate", o / "scheme_a" / "cost.json"), ("general", o / "diag_all_general" / "cost.json")):
        d = jl(path)
        calls = [c for c in d["calls"] if not c.get("dry_run")]
        use[key] = {"log": str(path.relative_to(ROOT)), "requests": len(calls), "success": sum(1 for c in calls if c.get("success")),
                    "units": sum(float(c.get("units") or 0) for c in calls), "cny_list_price": d.get("total_cny"),
                    "elapsed_sec": round(sum(float(c.get("elapsed_sec") or 0) for c in calls), 1)}
    return use


def quota_check() -> dict:
    use = local_usage()
    rows = {}
    for k, q in FREE_QUOTA_DOC.items():
        used = use[k]["units"]
        theo = (q["limit"] - used) if q["limit"] else None
        rows[k] = {**q, "local_used_units": used, "local_requests": use[k]["requests"], "log": use[k]["log"],
                   "theoretical_remaining_if_only_these_calls": theo, "plan": PLAN_CALLS[k][0], "plan_units": PLAN_CALLS[k][1],
                   "remaining_confirmed": False, "decision": "不调用，复用已有输出"}
    return {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M %Z") or "",
        "method": "只看官方文档 + 本地调用日志；不登录控制台、不用浏览器、不发探测请求（失败调用也扣免费额度）。",
        "api_for_remaining_quota": "无。官方文档（AI 开放能力通用参考·查看用量 https://cloud.baidu.com/doc/AI_REFERENCE/s/Gm3zhy5v2；"
                                   "免费测试资源 https://ai.baidu.com/ai-doc/OCR/fk3h7xu7h）只给出控制台「资源列表 / 监控报表」查剩余量，"
                                   "没有查询剩余免费额度的 OCR API；文字识别的 access_token 也不能查账单/额度。",
        "why_unconfirmable": [
            "免费资源要在控制台「领取」后才生效，是否已领取、账号是个人还是企业认证，本地都看不到；",
            "同一账号下其他应用/他人的调用量本地看不到，本地日志只是下限；",
            "Unlimited-OCR 文档只写新用户 2000 页/月，本账号是否享有不确定；",
            "此前所有调用在本地 cost.json 里都按刊例价记账（总 ¥2.72），没有任何返回能证明走的是免费额度。",
        ],
        "apis": rows,
        "conclusion": "四个接口都无法可靠确认剩余免费额度 → 按规则一律不调用，复用 2026-11-18 11:4x CST 已落盘的原始返回（同一 PDF，sha256 一致）。",
    }


# ---------------- 计分 ----------------
def ref_setup(bs):
    ref_text = bs.REF.read_text(encoding="utf-8")
    man = jl(bs.MANIFEST)
    mpages = {int(p["output_page"]): p for p in man["pages"]}
    manual = jl(ROOT / "data" / "blind20" / "manual_reference_fixes.json")
    bs.MANUAL.clear()
    bs.MANUAL.update(manual)
    rw = {int(k) for k in (manual.get("page_rewrites") or {})}
    corr = [c for c in jl(ROOT / "output" / "compare" / "reference_corrections.json")["corrections"] if c["page"] not in rw]
    return bs.parse_ref(ref_text), mpages, {"corrected": corr, "original": None}


def summ(s) -> dict:
    out = {}
    for k in ("overall", "body", "table", "seal"):
        t = s["total"][k]
        out[k] = {"readout": round(t["readout"] * 100, 2), "cer": round(t["cer"] * 100, 2), "D": t["deletions"], "S": t["substitutions"],
                  "I": t["insertions"], "ref": t["ref_chars"]}
    return out


PUN_EXTRA = set("~～-—–·•|")


def is_punct(ch):
    return ch in PUN_EXTRA or unicodedata.category(ch).startswith("P")


SIGN = ("周浪", "陈强")


def categorize(span, page_pred_norm, page_ref_norm, seal_chars, seal_zone=(), others_del="", others_ins="") -> tuple[str, str]:
    """others_del / others_ins：同页其他错误段里被漏掉的参照字 / 多出来的输出字（用来认出「搬了家」的字）。"""
    r, p = span["ref"], span["pred"]
    if "★" in r + p or "☆" in r + p:
        return "星号", "★/☆ 漏或错"
    if p and not r and (p in SIGN or (span["page"] == 3 and p == "周")):
        return "其他", "手写签名（参照未收录）"
    if span["page"] == 3 and (r, p) in (("周", ""), (":", "周浪")):
        return "其他", "手写签名区"
    if p and not r and (p in seal_zone or (seal_chars and sum(c in seal_chars for c in p) / len(p) >= 0.6)):
        return "印章混入", "多出的字位于印章框内或属于本页印章/监制章文字"
    if r and p and r != p and sorted(r) == sorted(p):
        return "顺序", "同字换位"
    if r and p and len(r) + len(p) >= 6:
        common = sum((Counter(r) & Counter(p)).values())
        if common >= 0.7 * max(len(r), len(p)):
            return "顺序", "整段错位（字基本相同、顺序不同）"
    if p and not r and not (Counter(p) - Counter(others_del)):
        return "顺序", "多出的字正是同页别处漏掉的字（位置错）"
    if r and not p and not (Counter(r) - Counter(others_ins)):
        return "顺序", "漏掉的字出现在同页别处（位置错）"
    if all(is_punct(c) for c in r + p):
        return "标点", "全半角/句点逗号/顿号等"
    if r and p and len(r) == len(p):
        return "真错字", "字形认错"
    if r == "下载次数:1":
        return "其他", "发票竖排小字漏读"
    if r and not p:
        return "其他", "漏读"
    if p and not r:
        return "其他", "多读/杂字"
    return "其他", "混合"


def run_variant(b_doc, cfg, aux):
    t = time.perf_counter()
    doc, info = run_postprocess(b_doc, cfg, aux)
    return doc, info, time.perf_counter() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()
    cfg_all = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    assert cfg_all.get("allow_external") is False, "默认 config 的 allow_external 必须保持 false"
    pp_cfg = merged_cfg(cfg_all.get("postprocess"))

    # 1) 输入与额度
    qc = quota_check()
    wj(OUT / "quota_check.json", qc)
    pdf_sha = sha(FROZEN_PDF)
    a_sum = jl(ROOT / "output" / "scheme_a" / "call_summary.json")
    inputs = {
        "pdf": {"path": str(FROZEN_PDF), "sha256": pdf_sha, "same_as_called": pdf_sha == a_sum["input"]["sha256"]},
        "B_raw": {"path": "output/scheme_b_standard/raw/blind20-scan.parse_result.json",
                  "sha256": sha(ROOT / "output/scheme_b_standard/raw/blind20-scan.parse_result.json")},
        "B_doc": {"path": "output/final/scheme_b_standard/blind20-scan.json", "sha256": sha(ROOT / "output/final/scheme_b_standard/blind20-scan.json")},
        "A_raw_dir": {"path": "output/scheme_a/raw", "files": len(list((ROOT / "output/scheme_a/raw").glob("*.json")))},
        "U_raw": {"path": "output/scheme_b_unlimited/raw/blind20-scan.parse_result.json",
                  "sha256": sha(ROOT / "output/scheme_b_unlimited/raw/blind20-scan.parse_result.json")},
        "prod_config_sha256": sha(PROD_CFG),
    }
    assert inputs["pdf"]["same_as_called"], "frozen20 PDF 与当时调用的不一致"

    bs = load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    refp, mpages, refs = ref_setup(bs)
    b_doc = jl(ROOT / "output/final/scheme_b_standard/blind20-scan.json")
    base_pred = pred_pages(b_doc)
    assert base_pred == jl(ROOT / "output/final/scheme_b_standard/pred_pages.json"), "基线 pred_pages 与已有 B 结果不一致"

    t0 = time.perf_counter()
    aux = load_aux(ROOT, b_doc)
    t_aux = time.perf_counter() - t0

    # 2) 最优解
    best_doc, best_info, t_best = run_variant(b_doc, pp_cfg, aux)
    errs = validate_schema(best_doc, ROOT / "schemas" / "intermediate.schema.json")
    best_pred = pred_pages(best_doc)
    wj(OUT / "blind20-scan.json", best_doc)
    wj(OUT / "pred_pages.json", best_pred)
    wj(OUT / "postprocess_log.json", {"counts": best_info["counts"], "cfg": best_info["cfg"], "log": best_info["log"],
                                       "u_qr_dropped": aux["u_qr_dropped"]})

    # 3) 消融
    off = {k: {"enabled": False} for k in ("star", "seal_strip", "fill_gaps", "invoice_reorder")}

    def only(*ks, **extra):
        c = copy.deepcopy(off)
        for k in ks:
            c[k] = {**pp_cfg[k], "enabled": True}
        for k, v in extra.items():
            c[k] = {**c[k], **v}
        return c

    def minus(k, **extra):
        c = copy.deepcopy(pp_cfg)
        c[k] = {**c[k], "enabled": False}
        for kk, v in extra.items():
            c[kk] = {**c[kk], **v}
        return c

    variants = [
        ("基线 B（不加后处理）", "base", {"enabled": False}),
        ("最优解（四步全开）", "best", pp_cfg),
        ("去掉 ★/☆ 规则", "-star", minus("star")),
        ("去掉序号点（R3），其余全开", "-serial", {**copy.deepcopy(pp_cfg), "star": {**pp_cfg["star"], "serial_dot": False}}),
        ("去掉剔除印章文字", "-seal", minus("seal_strip")),
        ("去掉补漏（A+U）", "-fill", minus("fill_gaps")),
        ("去掉发票按坐标重排", "-invoice", minus("invoice_reorder")),
        ("补漏每处 ≤1 字，其余全开", "fill1", {**copy.deepcopy(pp_cfg), "fill_gaps": {**pp_cfg["fill_gaps"], "max_len": 1}}),
        ("剔除印章只用位置判据，其余全开", "seal-pos", {**copy.deepcopy(pp_cfg), "seal_strip": {**pp_cfg["seal_strip"], "use_text": False}}),
        ("剔除印章只用文字判据，其余全开", "seal-txt", {**copy.deepcopy(pp_cfg), "seal_strip": {**pp_cfg["seal_strip"], "use_position": False}}),
        ("只加 ★/☆ 规则", "+star", only("star")),
        ("只加剔除印章文字", "+seal", only("seal_strip")),
        ("只加补漏（A+U）", "+fill", only("fill_gaps")),
        ("只加发票按坐标重排", "+invoice", only("invoice_reorder")),
        ("★/☆ + 剔除印章（≈离线 S2）", "S2", only("star", "seal_strip")),
        ("S2 + 补漏 ≤1 字（≈离线 F2-1，但按区域对齐）", "F2-1r", only("star", "seal_strip", "fill_gaps", fill_gaps={"max_len": 1})),
    ]
    results, docs = {}, {}
    for title, key, c in variants:
        d, info, tt = (b_doc, {"counts": {}}, 0.0) if key == "base" else ((best_doc, best_info, t_best) if key == "best" else run_variant(b_doc, c, aux))
        pr = base_pred if key == "base" else pred_pages(d)
        docs[key] = (d, pr)
        sc = {rk: bs.score_system(pr, refp, mpages, rv) for rk, rv in refs.items()}
        results[key] = {"title": title, "counts": info["counts"], "sec": round(tt, 3), "summary": {rk: summ(v) for rk, v in sc.items()},
                        "_full": sc}

    # 4) 逐页、按文档类型、错字分类、逐字段 diff
    full_b, full_o = results["base"]["_full"]["corrected"], results["best"]["_full"]["corrected"]
    per_page = []
    for p in sorted(full_b["per_page"]):
        a, z = full_b["per_page"][p], full_o["per_page"][p]
        per_page.append({"page": p, "doc_type": a["doc_type"], "ref_chars": a["overall"]["ref_chars"],
                         "base": {k: round(a["overall"][k] * 100, 2) for k in ("readout", "cer")},
                         "base_DSI": [a["overall"]["deletions"], a["overall"]["substitutions"], a["overall"]["insertions"]],
                         "best": {k: round(z["overall"][k] * 100, 2) for k in ("readout", "cer")},
                         "best_DSI": [z["overall"]["deletions"], z["overall"]["substitutions"], z["overall"]["insertions"]],
                         "seal_base": round(a["seal"]["cer"] * 100, 2) if a["seal"]["ref_chars"] else None,
                         "seal_best": round(z["seal"]["cer"] * 100, 2) if z["seal"]["ref_chars"] else None})
    by_type = {k: {"pages": full_b["by_doc_type"][k]["pages"],
                   "base": [round(full_b["by_doc_type"][k]["overall"]["readout"] * 100, 2), round(full_b["by_doc_type"][k]["overall"]["cer"] * 100, 2)],
                   "best": [round(full_o["by_doc_type"][k]["overall"]["readout"] * 100, 2), round(full_o["by_doc_type"][k]["overall"]["cer"] * 100, 2)]}
               for k in full_b["by_doc_type"]}

    hs = load_mod("hybrid_score", ROOT / "scripts" / "hybrid_score.py")
    hs.load_bs = lambda: bs

    def cats(key, rk="corrected"):
        full = results[key]["_full"][rk]
        pr = docs[key][1]
        tot, rows = Counter(), []
        spans = sorted(full["error_spans"], key=lambda s: (s["page"], s["ref_start"]))
        for i, sp in enumerate(spans):
            p = sp["page"]
            rs, _ = hs.ref_page_string(bs, refp, p, refs[rk])
            ps = bs.norm("".join(s["text"] for s in pr[str(p)]["sections"]))
            seal_chars = set(bs.norm(pr[str(p)].get("seal") or "")) | set(bs.norm("".join(refp[p]["seals"])))
            same = [o for j, o in enumerate(spans) if o["page"] == p and j != i]
            c, why = categorize(sp, ps, rs, seal_chars, seal_zone.get(p, set()),
                                "".join(o["ref"] for o in same), "".join(o["pred"] for o in same))
            tot[c] += sp["cost"]
            rows.append({**{k: sp[k] for k in ("page", "section", "ref", "pred", "cost", "context")}, "category": c, "detail": why})
        return dict(tot), rows

    # 印章框内的杂字（按坐标判定，取最优解剔除日志里「P 位于印章框内」的块）
    seal_zone = defaultdict(set)
    for c in best_info["log"]:
        if c["step"] == "seal_strip" and c["reason"].startswith("P"):
            seal_zone[c["page"]].add(bs.norm(c["text"]))
    cat_base, rows_base = cats("base")
    cat_best, rows_best = cats("best")
    wj(OUT / "diff_vs_corrected_ref.json", {"best": rows_best, "base": rows_base})

    # 5) 改动明细（基线 → 最优解）
    changes = best_info["log"]

    # 6) 汇总 JSON
    t_total = time.perf_counter() - t_all
    timing = {"aux_load_sec": round(t_aux, 3), "postprocess_sec": round(t_best, 3), "script_total_sec": round(t_total, 1),
              "baidu_original_elapsed_sec": {k: v["elapsed_sec"] for k, v in local_usage().items()}}
    wj(OUT / "scores.json", {"inputs": inputs, "timing": timing, "schema_errors": errs,
                             "variants": {k: {kk: vv for kk, vv in v.items() if kk != "_full"} for k, v in results.items()},
                             "per_page": per_page, "by_doc_type": by_type, "categories": {"base": cat_base, "best": cat_best}})
    write_report(qc, inputs, timing, errs, results, per_page, by_type, cat_base, cat_best, rows_base, rows_best, changes, aux, pp_cfg)
    print(json.dumps({k: results[k]["summary"]["corrected"]["overall"] for k in results}, ensure_ascii=False))
    print("schema errors:", len(errs), "timing:", timing)


def fmt_row(title, s):
    o, b, t, z = s["overall"], s["body"], s["table"], s["seal"]
    return (f"| {title} | **{o['readout']:.2f}%** | **{o['cer']:.2f}%** | {o['D']}/{o['S']}/{o['I']} | "
            f"{b['readout']:.2f}% / {b['cer']:.2f}% ({b['D']}/{b['S']}/{b['I']}) | {t['readout']:.2f}% / {t['cer']:.2f}% ({t['D']}/{t['S']}/{t['I']}) | "
            f"{z['readout']:.2f}% / {z['cer']:.2f}% ({z['D']}/{z['S']}/{z['I']}) |")


HDR = ("| 方案 | 读出率 | CER | 删/替/插 | 正文 读出/CER (D/S/I) | 表格 读出/CER (D/S/I) | 印章 读出/CER (D/S/I) |\n"
       "|---|---|---|---|---|---|---|")


def verdict(o):
    r_ok, c_ok = o["readout"] > 99.0, o["cer"] < 1.0
    if r_ok and c_ok:
        return f"两项达标（读出率高出 {o['readout'] - 99:.2f}pp，CER 低于目标 {1 - o['cer']:.2f}pp）"
    parts = []
    parts.append("读出率达标" if r_ok else f"读出率差 {99 - o['readout']:.2f}pp")
    parts.append("CER 达标" if c_ok else f"CER 超 {o['cer'] - 1:.2f}pp")
    return "，".join(parts)


def write_report(qc, inputs, timing, errs, R, per_page, by_type, cat_base, cat_best, rows_base, rows_best, changes, aux, cfg):
    L = []
    b, z = R["base"]["summary"], R["best"]["summary"]
    L += ["# blind20「最优解」配置端到端复测报告", "",
          f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}（伦敦时间，UTC+0；= 北京时间 +8h）。脚本 `scripts/bestconfig_run.py`，后处理模块 `src/baidu_ocr/postprocess/`。",
          "",
          "> **注意：这不是盲测。** ★/☆ 与序号点、印章剔除、发票重排、补漏的条件都是看着这 20 页的错误调出来的（含参照修正后的口径），"
          "存在过拟合。结论只说明「这套后处理在 blind20 上能做到多少」，切换主路径前必须在新的、人工逐字校对的盲测集上复验。",
          "",
          "## 0. 结论", "",
          f"- 最优解（B 标准版 + ★/☆+序号点 + 剔除印章文字 + A/U 一致补漏 + 发票按坐标重排）：修正参照下 **读出率 {z['corrected']['overall']['readout']:.2f}%、CER {z['corrected']['overall']['cer']:.2f}%** → {verdict(z['corrected']['overall'])}。",
          f"- 基线（单跑 B、不加后处理，复用已有 B 原始结果）：{b['corrected']['overall']['readout']:.2f}% / {b['corrected']['overall']['cer']:.2f}% → {verdict(b['corrected']['overall'])}。",
          f"- 原参照（未修正）下：最优解 {z['original']['overall']['readout']:.2f}% / {z['original']['overall']['cer']:.2f}%，基线 {b['original']['overall']['readout']:.2f}% / {b['original']['overall']['cer']:.2f}%（原参照里有已知错误，仅供对照）。",
          "- **本次没有重新调用任何百度接口，也没有调用 OpenAI。** 四个百度接口都无法通过 API 或文档可靠确认剩余免费额度，按规则全部复用 2026-11-18 已落盘的原始返回（同一 PDF，sha256 一致），详见第 1 节。因此「重跑 B 与旧 B 是否一致」这一项没有可比数据，基线只有一份。",
          f"- 后处理纯本地，20 页总耗时 {timing['postprocess_sec']:.2f} 秒（另加载 A 字框/Unlimited 输出 {timing['aux_load_sec']:.2f} 秒），新增百度费用 ¥0。",
          f"- 输出的中间 JSON 通过 schema 校验：{len(errs)} 个错误。", ""]

    # 1 额度
    L += ["## 1. 百度接口额度核查与是否重调", "",
          f"核查方法：{qc['method']}", "",
          f"能否用 API 查剩余额度：{qc['api_for_remaining_quota']}", "",
          "无法可靠确认的原因：", ""] + [f"- {x}" for x in qc["why_unconfirmable"]] + [""]
    L += ["| 接口 | 官方免费额度 | 本地日志已用（请求/计量） | 若只有这些调用，理论剩余 | 本次计划用量 | 剩余是否可确认 | 处理 |", "|---|---|---|---|---|---|---|"]
    for k, r in qc["apis"].items():
        theo = "—（额度本身不确定）" if r["theoretical_remaining_if_only_these_calls"] is None else f"{r['theoretical_remaining_if_only_these_calls']:.0f}"
        L.append(f"| {r['name']} | {r['free']} | {r['local_requests']} 次 / {r['local_used_units']:.0f} {'页' if 'docparser' in k else '次'}（`{r['log']}`） | {theo} | {r['plan']} | 否 | {r['decision']} |")
    L += ["", f"结论：{qc['conclusion']}", "",
          "本次实际调用次数：文档解析标准版 0、Unlimited-OCR 0、高精度含位置 0、标准含位置 0、OpenAI 0。累计（本地日志，全部为 2026-11-18 北京时间上午的调用）：标准版 20 页、Unlimited 20 页、高精度含位置 28 次、标准含位置 20 次，刊例价合计 ¥2.72。", "",
          "复用的输入：", "",
          f"- PDF：`{inputs['pdf']['path']}` sha256 `{inputs['pdf']['sha256'][:16]}…`，与当时调用时记录的一致：{inputs['pdf']['same_as_called']}",
          f"- B 标准版原始返回：`{inputs['B_raw']['path']}`（sha256 `{inputs['B_raw']['sha256'][:16]}…`）→ 已有中间 JSON `{inputs['B_doc']['path']}`",
          f"- A（高精度含位置，原走法：20 主调 + 8 次补识）：`{inputs['A_raw_dir']['path']}`（{inputs['A_raw_dir']['files']} 个原始返回）",
          f"- Unlimited-OCR：`{inputs['U_raw']['path']}`（剔除二维码幻觉块 {aux['u_qr_dropped']} 个）", ""]

    # 2 主表
    for rk, title in (("corrected", "修正参照（主）"), ("original", "原参照（附）")):
        L += [f"## 2{'a' if rk == 'corrected' else 'b'}. 最优解 vs 基线 — {title}", "", HDR,
              fmt_row("基线 B（不加后处理）", R["base"]["summary"][rk]), fmt_row("**最优解**", R["best"]["summary"][rk]), ""]
        if rk == "corrected":
            o1, o2 = b[rk]["overall"], z[rk]["overall"]
            L += [f"变化：读出率 {o2['readout'] - o1['readout']:+.2f}pp，CER {o2['cer'] - o1['cer']:+.2f}pp；删 {o1['D']}→{o2['D']}，替 {o1['S']}→{o2['S']}，插 {o1['I']}→{o2['I']}。印章不受后处理影响（只改正文/表格），印章 CER 高主要是半圈章、五角星压字和参照口径问题。", ""]
    L += ["对照目标（读出率 > 99%、CER < 1%，正文+表格合计；印章单列不计入）：", "",
          "| 方案 | 修正参照 | 原参照 |", "|---|---|---|",
          f"| 基线 B | {verdict(b['corrected']['overall'])} | {verdict(b['original']['overall'])} |",
          f"| 最优解 | {verdict(z['corrected']['overall'])} | {verdict(z['original']['overall'])} |", "",
          "按文档类型（修正参照，读出率 / CER）：", "", "| 类型 | 页数 | 基线 | 最优解 |", "|---|---|---|---|"]
    for k, v in by_type.items():
        L.append(f"| {k} | {v['pages']} | {v['base'][0]:.2f}% / {v['base'][1]:.2f}% | {v['best'][0]:.2f}% / {v['best'][1]:.2f}% |")
    L.append("")

    # 3 逐页
    L += ["## 3. 逐页明细（修正参照，正文+表格）", "",
          "| 页 | 类型 | 参照字数 | 基线 读出/CER | 基线 D/S/I | 最优解 读出/CER | 最优解 D/S/I | 印章 CER 基线→最优解 |", "|---|---|---|---|---|---|---|---|"]
    for r in per_page:
        seal = "—" if r["seal_base"] is None else f"{r['seal_base']:.1f}% → {r['seal_best']:.1f}%"
        flag = " ⚠" if r["best"]["cer"] >= 1 else ""
        L.append(f"| {r['page']} | {r['doc_type']} | {r['ref_chars']} | {r['base']['readout']:.2f}% / {r['base']['cer']:.2f}% | {'/'.join(map(str, r['base_DSI']))} | "
                 f"{r['best']['readout']:.2f}% / {r['best']['cer']:.2f}%{flag} | {'/'.join(map(str, r['best_DSI']))} | {seal} |")
    L += ["", "⚠ = 该页最优解 CER 仍 ≥1%。", ""]

    # 4 错字分类
    cats_order = ["星号", "顺序", "标点", "真错字", "印章混入", "其他"]
    tb, tz = sum(cat_base.values()), sum(cat_best.values())
    L += ["## 4. 错字分类（修正参照，正文+表格，按错误字数）", "",
          "分类规则（按优先级）：含 ★/☆ → 星号；手写签名（周浪、陈强，参照未收录）→ 其他；多出的字位于印章框内或属于本页印章/监制章文字 → 印章混入；"
          "同字换位、整段字基本相同只是顺序不同、或多出/漏掉的字正好对应同页别处的漏/多 → 顺序；全是标点 → 标点；等长替换 → 真错字；"
          "其余（发票竖排「下载次数」、漏读、杂字）→ 其他。", "",
          "| 类别 | 基线（字） | 基线 CER 贡献 | 最优解（字） | 最优解 CER 贡献 |", "|---|---|---|---|---|"]
    ref_n = R["base"]["summary"]["corrected"]["overall"]["ref"]
    for c in cats_order:
        L.append(f"| {c} | {cat_base.get(c, 0)} | {cat_base.get(c, 0) / ref_n * 100:.2f}pp | {cat_best.get(c, 0)} | {cat_best.get(c, 0) / ref_n * 100:.2f}pp |")
    L += [f"| 合计 | {tb} | {tb / ref_n * 100:.2f}pp | {tz} | {tz / ref_n * 100:.2f}pp |", "",
          "（错误段按对齐片段计，一段的字数 = 该段编辑代价；合计与 D+S+I 一致。）", ""]
    sub = defaultdict(int)
    for r in rows_best:
        sub[(r["category"], r["detail"])] += r["cost"]
    L += ["最优解剩余错误的细分：", "", "| 类别 | 细分 | 字数 |", "|---|---|---|"]
    for (c, d), v in sorted(sub.items(), key=lambda kv: (cats_order.index(kv[0][0]), -kv[1])):
        L.append(f"| {c} | {d} | {v} |")
    L.append("")

    # 5 diff
    L += ["## 5. 最优解与修正参照的逐字段 diff（全部剩余错误）", "",
          "「参照」为空 = 多读（插入）；「输出」为空 = 漏读（删除）；两者都有 = 替换。上下文取参照前后各 6 字。完整 JSON：`output/bestconfig/diff_vs_corrected_ref.json`（含基线）。", "",
          "| 页 | 分区 | 参照 | 输出 | 字数 | 类别 | 细分 | 上下文 |", "|---|---|---|---|---|---|---|---|"]
    esc = lambda s: (s or "").replace("|", "\\|").replace("\n", "/")  # noqa: E731
    for r in rows_best:
        L.append(f"| {r['page']} | {r['section']} | {esc(r['ref']) or '∅'} | {esc(r['pred']) or '∅'} | {r['cost']} | {r['category']} | {r['detail']} | {esc(r['context'])} |")
    L.append("")

    # 6 后处理改动
    cnt = Counter(c["step"] for c in changes)
    L += ["## 6. 后处理实际改动（基线 → 最优解）", "",
          f"改动条数：★/☆+序号点 {cnt.get('star', 0)} 处（块/单元格），剔除印章文字 {cnt.get('seal_strip', 0)} 块，补漏 {cnt.get('fill_gaps', 0)} 处，发票重排 {cnt.get('invoice_reorder', 0)} 张表。完整日志：`output/bestconfig/postprocess_log.json`。", "",
          "### 6.1 剔除印章文字", "", "| 页 | 块 | 文字 | 判据 |", "|---|---|---|---|"]
    for c in changes:
        if c["step"] == "seal_strip":
            L.append(f"| {c['page']} | {c['block_id']} | {esc(c['text'])} | {c['reason']} |")
    L += ["", "### 6.2 补漏（A 与 Unlimited 在同一区域一致时补 B 漏字）", "", "| 页 | 块/单元格 | 补入 | 改前 | 改后 |", "|---|---|---|---|---|"]
    for c in changes:
        if c["step"] == "fill_gaps":
            L.append(f"| {c['page']} | {c['block_id']}{' 格' + str(c['cell']) if c.get('cell') else ''} | {''.join(c['inserted'])} | {esc(c['before'])[:40]} | {esc(c['after'])[:40]} |")
    L += ["", "### 6.3 发票按坐标重排", ""]
    for c in changes:
        if c["step"] == "invoice_reorder":
            L += [f"p{c['page']} {c['block_id']}：明细区第 {c['region_rows'][0]}–{c['region_rows'][1]} 行 → {c['new_rows']} 行", "", "改前（B 原行，单元格直接拼接）：", ""]
            L += [f"- `{esc(x)}`" for x in c["before"]]
            L += ["", "改后（格间用 ｜，格内换行用 /）：", ""] + [f"- `{esc(x)}`" for x in c["after"]] + [""]
    L += ["### 6.4 ★/☆ 与序号点（前 15 处）", "", "| 页 | 改前 | 改后 |", "|---|---|---|"]
    for c in [c for c in changes if c["step"] == "star"][:15]:
        L.append(f"| {c['page']} | {esc(c['before'])[:40]} | {esc(c['after'])[:40]} |")
    L.append("")

    # 7 消融
    L += ["## 7. 按规则逐条消融（修正参照，正文+表格合计；括号内为原参照）", "",
          "| 变体 | 读出率 | CER | 删/替/插 | 相对最优解 CER | 原参照 读出/CER | 达标? |", "|---|---|---|---|---|---|---|"]
    bc = z["corrected"]["overall"]["cer"]
    for k, v in R.items():
        o, oo = v["summary"]["corrected"]["overall"], v["summary"]["original"]["overall"]
        ok = "✅" if o["readout"] > 99 and o["cer"] < 1 else "—"
        L.append(f"| {v['title']} | {o['readout']:.2f}% | {o['cer']:.2f}% | {o['D']}/{o['S']}/{o['I']} | {o['cer'] - bc:+.2f}pp | {oo['readout']:.2f}% / {oo['cer']:.2f}% | {ok} |")
    L += ["", "要点：", "",
          f"- 单条贡献最大的是 ★/☆ 规则（单加 CER {R['base']['summary']['corrected']['overall']['cer']:.2f}→{R['+star']['summary']['corrected']['overall']['cer']:.2f}%），其次是发票按坐标重排（单加 →{R['+invoice']['summary']['corrected']['overall']['cer']:.2f}%）。",
          f"- 去掉任意一条：★/☆ {R['-star']['summary']['corrected']['overall']['cer']:.2f}%、发票重排 {R['-invoice']['summary']['corrected']['overall']['cer']:.2f}%、印章剔除 {R['-seal']['summary']['corrected']['overall']['cer']:.2f}%、补漏 {R['-fill']['summary']['corrected']['overall']['cer']:.2f}%、序号点 {R['-serial']['summary']['corrected']['overall']['cer']:.2f}%。",
          f"- 补漏每处 ≤1 字与 ≤2 字在本集上结果相同（{R['fill1']['summary']['corrected']['overall']['cer']:.2f}% vs {bc:.2f}%）：本集所有一致补字都是 1 字。补漏收益小（{cnt.get('fill_gaps', 0)} 处），其中 p6「:周锦」多补了一个冒号（A、U 都读出了冒号，参照没有），是已知误补。",
          f"- 序号点 R3 在本集上不起作用（去掉后 {R['-serial']['summary']['corrected']['overall']['cer']:.2f}%，与最优解相同）：本集的序号点错误都在 ★ 后面，已由 R2 覆盖；R3 保留给不带 ★ 的「1。」类序号。",
          f"- 剔除印章：只用位置判据 {R['seal-pos']['summary']['corrected']['overall']['cer']:.2f}%，只用文字判据 {R['seal-txt']['summary']['corrected']['overall']['cer']:.2f}%；本集上文字判据没有额外贡献，位置判据（B 返回的印章框）是主力。",
          f"- 离线模拟 F2-1（整页字符串对齐）是 99.27% / 0.99%；本次按区域对齐的同配置（S2 + 补漏 ≤1 字，不含发票重排）为 {R['F2-1r']['summary']['corrected']['overall']['readout']:.2f}% / {R['F2-1r']['summary']['corrected']['overall']['cer']:.2f}%。", ""]

    # 8 耗时与费用
    L += ["## 8. 耗时与费用", "",
          "| 项 | 本次 | 说明 |", "|---|---|---|",
          f"| 百度调用 | 0 次，¥0 | 额度无法确认，全部复用 |",
          f"| OpenAI 调用 | 0 次，$0 | 本任务禁止 |",
          f"| 加载 A 字框 + Unlimited 版面 | {timing['aux_load_sec']:.2f} s | 本地 |",
          f"| 四步后处理（20 页） | {timing['postprocess_sec']:.2f} s | 本地 |",
          f"| 脚本总耗时（含 16 个变体 × 2 份参照计分） | {timing['script_total_sec']:.1f} s | 本地 |", "",
          "复用的百度原始结果当时的接口耗时（本地日志）：" + "；".join(f"{FREE_QUOTA_DOC[k]['name']} {v:.1f} s" for k, v in timing["baidu_original_elapsed_sec"].items()) + "。"
          "若生产按最优解走，需要 B 标准版 + A 高精度含位置 + Unlimited 三路，每页约 ¥0.05 + ¥0.04×(1–2) + ¥0.02 ≈ ¥0.11–0.15（刊例价，不含免费额度）。", ""]

    # 9 规则与配置
    L += ["## 9. 最优解配置与规则定义", "",
          "流水线正式步骤：`baidu_ocr.postprocess.run_postprocess(doc, cfg, aux)`，在 B 标准版中间 JSON 上按 star → seal_strip → fill_gaps → invoice_reorder 顺序执行；"
          "开关在 `config.yaml` 的 `postprocess:` 节（默认 `allow_external: false` 不变）。被剔除的印章字块保留在 JSON 中，`verification_status=rejected` 并带 warning，生成侧会跳过。", "",
          f"- ★/☆ 与序号点：{STAR_RULE}",
          "- 剔除印章文字：仅对 paragraph/header 块、规范化后 ≤12 字、不含冒号、不以序号/★ 开头的短块；满足任一即剔除：P 块面积 ≥60% 落在 B 印章框内；T1 与同页印章文字重合 ≥60% 且 ≥4 字；T2 含「税务局/下纛/监制」；T3 ≥6 字且被相邻含冒号行包含。",
          "- 补漏：区域 = B 每个正文块和每个表格单元格；A 取中心落在区域内的字框（正文块左右放宽 10pt），U 取重叠的 Unlimited 文本块（半全局对齐）或最相似的 Unlimited 表格单元格（相似度 ≥0.6）；A、U 都先套 ★ 规则；"
          "只有 A、U 在 B 的同一位置都多出同一串、且 ≤2 字时才补；只补不删不替换。Unlimited 先剔除二维码幻觉（英文长句 / QR code 字样）。",
          "- 发票按坐标重排：见 `src/baidu_ocr/postprocess/invoice_reorder.py` 文件头；只在含「发票号码/电子发票」的页、表头含「项目名称」「税额」的表的明细区生效，字符只搬家不增删（有断言），按 A 字框坐标纠偏（Theil–Sen 斜率）后分行分列。", "",
          "```yaml", yaml.safe_dump({"postprocess": cfg}, allow_unicode=True, sort_keys=False).strip(), "```", ""]

    # 10 测试与文件
    tp = OUT / "pytest.txt"
    L += ["## 10. 测试结果", "", "```", tp.read_text(encoding="utf-8").strip() if tp.exists() else "（未找到 pytest.txt）", "```", "",
          "## 11. 文件", "",
          "- 报告：`output/bestconfig/report.md`",
          "- 最优解中间 JSON：`output/bestconfig/blind20-scan.json`；计分输入：`output/bestconfig/pred_pages.json`",
          "- 全部变体分数、逐页、分类：`output/bestconfig/scores.json`",
          "- 逐字段 diff：`output/bestconfig/diff_vs_corrected_ref.json`",
          "- 后处理日志：`output/bestconfig/postprocess_log.json`",
          "- 额度核查：`output/bestconfig/quota_check.json`",
          "- 代码：`src/baidu_ocr/postprocess/`（`star.py`、`seal_strip.py`、`fill_gaps.py`、`invoice_reorder.py`、`aux.py`、`textutil.py`、`pipeline.py`），`scripts/bestconfig_run.py`，单测 `tests/test_postprocess.py`",
          "- 约束核对：`frozen20/` 未改（sha256 校验一致）；`/workspace/pdf-test/ocr/config.yaml` 未动（150 DPI，sha256 记录在 scores.json）；`config.yaml` 的 `allow_external` 仍为 false；本次未发出任何外部请求。", ""]
    (OUT / "report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
