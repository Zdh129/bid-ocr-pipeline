#!/usr/bin/env python3
"""gpt-6.2-astra 单测计分（逐区域，不合并进底稿——组合测试本次不跑）。不联网。

做法：每个送检区域，把「最优解底稿在该区域的文字」(B+后处理) 与「astra 输出」分别用半全局对齐在修正参照的同页字符串里找窗口，
取两个窗口的并集作为该区域的参照窗口，两者都对这同一窗口算 D/S/I。两边都先套 ★/☆ 规则、NFKC、去空白（与 blind20 计分口径一致）。
区域选择不看参照；参照只用于计分。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from baidu_ocr.hybrid.merge import flatten_line, parse_table  # noqa: E402
from baidu_ocr.hybrid.inputs import a_chars_for_page, b_raw_layout_index  # noqa: E402
from baidu_ocr.postprocess.aux import chars_in, reading_sort  # noqa: E402
from baidu_ocr.hybrid.rules import table_rows  # noqa: E402
from baidu_ocr.postprocess.star import apply_text  # noqa: E402
from baidu_ocr.postprocess.textutil import insertions  # noqa: E402
from baidu_ocr.score import levenshtein_alignment  # noqa: E402

OUT = ROOT / "output" / "astra"
LON = timezone.utc  # 用户在伦敦（当前 UTC+0）


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bs = load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
hs = load_mod("hybrid_score", ROOT / "scripts" / "hybrid_score.py")
hs.load_bs = lambda: bs


def N(s):
    return bs.norm(apply_text(s or "")[0])


def window(text, ref):
    """半全局对齐找 text 在参照页里的窗口；对不上（代价 > 一半字数）返回 None。"""
    if not text:
        return None
    _, lead, tail = insertions(text, ref, free_ends=True)
    s, e = len(lead), len(ref) - len(tail)
    a = levenshtein_alignment(text, ref[s:e])
    if a["deletions"] + a["substitutions"] + a["insertions"] > 0.5 * len(text):
        return None
    return s, e


def disambiguate(w, ref, chars, bbox):
    """参照页里若有多处与锚窗口只差 ≤1 字的同长片段（如两处“日期:2024年7月X0日”），
    按区域在页内阅读顺序的相对位置挑最近的一处。返回 (窗口, 候选数)。"""
    L = w[1] - w[0]
    seg = ref[w[0]:w[1]]
    base = levenshtein_alignment(seg, seg)
    cands = []
    for i in range(0, len(ref) - L + 1):
        a = levenshtein_alignment(seg, ref[i:i + L])
        if a["deletions"] + a["substitutions"] + a["insertions"] <= 1:
            cands.append(i)
    # 去掉彼此重叠的近邻起点（同一处的 ±1 偏移）
    uniq = []
    for i in cands:
        if not uniq or i - uniq[-1] >= L // 2 + 1:
            uniq.append(i)
    if len(uniq) <= 1 or L < 8:
        return w, 1
    # 用同一栏（x 有重叠）、区域上方 200pt 内的 A 字做上下文，和各候选前 60 字的参照比，取最像的一处
    x0, y0, x1, y1 = bbox
    above = [c for c in chars if c["bbox"][3] <= y0 + 2 and c["bbox"][1] >= y0 - 200
             and min(c["bbox"][2], x1) > max(c["bbox"][0], x0)]
    ctx = N("".join(c["ch"] for c in reading_sort(above)))[-40:]
    if not ctx:
        return w, len(uniq)
    def cost(i):
        left = ref[max(0, i - 80):i]
        _, lead, tail = insertions(ctx, left, free_ends=True)
        a = levenshtein_alignment(ctx, left[len(lead):len(left) - len(tail)])
        return a["deletions"] + a["substitutions"] + a["insertions"]
    best = min(uniq, key=cost)
    return (best, best + L), len(uniq)


def overlap(w1, w2):
    return w1 and w2 and min(w1[1], w2[1]) > max(w1[0], w2[0])


def dsi(pred, ref):
    a = levenshtein_alignment(pred, ref)
    return {"D": a["deletions"], "S": a["substitutions"], "I": a["insertions"], "ref": len(ref), "match": a["matches"]}


def region_b_text(item, by_id):
    t = item["target"]
    if item["kind"] == "line":
        b = by_id.get(t["b_block_id"])
        return "" if b is None or b.get("verification_status") == "rejected" else (b.get("text") or "")
    if item["kind"] == "missing_line":
        return ""
    blk = by_id[t["b_block_id"]]
    rows = table_rows(blk)
    y0, y1 = item["bbox_pt"][1], item["bbox_pt"][3]
    sel = [r for r in rows if r["y0"] is not None and y0 <= (r["y0"] + r["y1"]) / 2 <= y1]
    return "\n".join(" ".join(c.get("text") or "" for c in r["cells"]) for r in sel)


def astra_text(item, res):
    if item["kind"] == "table_band":
        return "\n".join(" ".join(r) for r in parse_table(res.get("text") or ""))
    return flatten_line(res.get("text") or "")


def main():
    best = jl(ROOT / "output/bestconfig/blind20-scan.json")
    by_id = {b["id"]: b for b in best["blocks"]}
    items = jl(OUT / "manifest.json")["items"]
    res_dir = OUT / "vlm" / "results"
    results = {p.stem: jl(p) for p in res_dir.glob("*.json")} if res_dir.exists() else {}
    refp, mpages, refs = hs_ref()
    asum = {pg["page"]: pg for pg in jl(ROOT / "output/scheme_a/call_summary.json")["pages"]}
    achars = {p: a_chars_for_page(ROOT / "output/scheme_a", asum[p]) for p in asum}
    corr = refs["corrected"]
    page_ref = {p: hs.ref_page_string(bs, refp, p, corr)[0] for p in refp}
    rows, agg = [], defaultdict(lambda: {"B": {"D": 0, "S": 0, "I": 0, "ref": 0, "match": 0}, "X": {"D": 0, "S": 0, "I": 0, "ref": 0, "match": 0}, "n": 0, "n_ok": 0})
    for it in [i for i in items if i["test"] == "main"]:
        r = results.get(it["id"])
        ok = bool(r and r.get("ok"))
        bt = N(region_b_text(it, by_id))
        xt = N(astra_text(it, r)) if ok else bt
        ref = page_ref[it["page"]]
        # 定窗：以 A（高精度含位置）在该区域框内的字为锚（与两个被比较的系统都独立），
        # 再并入与锚重叠的底稿/astra 窗口；A 为空或对不上时，退回底稿与 astra 窗口的并集（两窗不重叠时取靠前者并记歧义）。
        at = N("".join(c["ch"] for c in reading_sort(chars_in(achars[it["page"]], it["bbox_pt"]))))
        wa, wb, wx = window(at, ref), window(bt, ref), window(xt, ref)
        if wa:
            wa, n_cand = disambiguate(wa, ref, achars[it["page"]], it["bbox_pt"])
            if n_cand > 1:
                wb = wb if overlap(wb, wa) else None
                wx = wx if overlap(wx, wa) else None
        amb = False
        if wa:
            ws = [wa] + [w for w in (wb, wx) if overlap(w, wa)]
        else:
            ws = [w for w in (wb, wx) if w]
            if len(ws) == 2 and not overlap(*ws):
                amb = True
        if ws:
            s0, e0 = min(w[0] for w in ws), max(w[1] for w in ws)
            rw = ref[s0:e0]
        else:
            rw = ""
        db, dx = dsi(bt, rw), dsi(xt, rw)
        cat = it["category"]
        for k, d in (("B", db), ("X", dx)):
            for kk in d:
                agg[cat][k][kk] += d[kk]
                agg["all"][k][kk] += d[kk]
        agg[cat]["n"] += 1
        agg["all"]["n"] += 1
        agg[cat]["n_ok"] += ok
        agg["all"]["n_ok"] += ok
        rows.append({"id": it["id"], "page": it["page"], "category": cat, "reasons": it["reasons"], "bbox_pt": [round(v, 1) for v in it["bbox_pt"]],
                     "ok": ok, "error": (r or {}).get("error") if r else "no_result", "ref_window": rw, "B": bt, "astra": xt if ok else None,
                     "B_err": db["D"] + db["S"] + db["I"], "astra_err": dx["D"] + dx["S"] + dx["I"], "B_dsi": db, "astra_dsi": dx,
                     "b_score": (it.get("context") or {}).get("b_score"), "anchor": "A" if wa else ("B∪astra" if ws else "none"), "ambiguous": amb, "A": at})
    # 印章小测
    seals = []
    sf = bs.MANUAL.get("seal_fixes") or {}
    for it in [i for i in items if i["test"] == "seal_small"]:
        r = results.get(it["id"]) or {}
        p = it["page"]
        ref_seals = (sf.get(str(p)) or {}).get("seals") or refp[p]["seals"]
        bseal = "".join(by_id[s].get("text") or "" for s in it["target"].get("b_seal_ids") or [] if s in by_id)
        xt = bs.norm(flatten_line(r.get("text") or ""))
        bn = bs.norm(bseal)
        tgt = max(ref_seals, key=lambda s: max(SequenceMatcher(None, xt, bs.norm(s)).ratio(), SequenceMatcher(None, bn, bs.norm(s)).ratio()), default="")
        tn = bs.norm(tgt)
        allref = set(bs.norm("".join(ref_seals)))
        invented = [c for c in xt if c not in allref]
        seals.append({"id": it["id"], "page": p, "bbox_pt": [round(v, 1) for v in it["bbox_pt"]], "crop": it["crop_path"], "ok": r.get("ok"),
                      "astra": xt, "B": bn, "ref_best_match": tgt, "ref_all_on_page": ref_seals,
                      "astra_dsi": dsi(xt, tn), "B_dsi": dsi(bn, tn), "astra_chars_not_in_any_ref_seal": "".join(invented),
                      "B_chars_not_in_any_ref_seal": "".join(c for c in bn if c not in allref)})
    # 手写
    hand = handwriting(best, rows, items, results, page_ref)
    json.dump({"rows": rows, "agg": agg, "seals": seals, "hand": hand}, open(OUT / "scores_unit.json", "w"), ensure_ascii=False, indent=1)
    report(rows, agg, seals, hand, results)


def hs_ref():
    ref_text = bs.REF.read_text(encoding="utf-8")
    man = jl(bs.MANIFEST)
    mpages = {int(p["output_page"]): p for p in man["pages"]}
    manual = jl(ROOT / "data/blind20/manual_reference_fixes.json")
    bs.MANUAL.clear()
    bs.MANUAL.update(manual)
    rw = {int(k) for k in (manual.get("page_rewrites") or {})}
    corr = [c for c in jl(ROOT / "output/compare/reference_corrections.json")["corrections"] if c["page"] not in rw]
    return bs.parse_ref(ref_text), mpages, {"corrected": corr, "original": None}


def handwriting(best, rows, items, results, page_ref):
    """B 版面类型 hand_sign 的块（手写签名）与其所在签署区；以及送检区域里含手写日期的行。"""
    raw = jl(ROOT / "output/scheme_b_standard/raw/blind20-scan.parse_result.json")
    idx = b_raw_layout_index(raw)
    a = jl(ROOT / "output/final/scheme_a/blind20-scan.json")
    sig = []
    for b in best["blocks"]:
        meta = idx.get(b["id"]) or {}
        if meta.get("raw_type") != "hand_sign":
            continue
        ab = [x for x in a["blocks"] if x["page"] == b["page"] and x.get("bbox") and _iou(x["bbox"], b["bbox"]) > 0.5]
        sent = [i["id"] for i in items if i["page"] == b["page"] and _iou(i["bbox_pt"], b["bbox"]) > 0.3]
        sig.append({"page": b["page"], "block_id": b["id"], "bbox_pt": [round(v, 1) for v in b["bbox"]], "text": b.get("text"),
                    "b_raw_type": "hand_sign", "b_layout_score": meta.get("score"),
                    "a_text": [x.get("text") for x in ab], "a_confidence": [x.get("confidence") for x in ab], "in_astra_crops": sent})
    dates = [r for r in rows if "日期" in (r["B"] or "") or "日期" in (r["astra"] or "")]
    return {"signature_blocks": sig, "date_rows": dates}


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0


def report(rows, agg, seals, hand, results):
    est = jl(OUT / "estimate.json")
    led = [json.loads(x) for x in (OUT / "vlm/ledger.jsonl").read_text(encoding="utf-8").splitlines()] if (OUT / "vlm/ledger.jsonl").exists() else []
    bst = jl(OUT / "vlm/batch_state.json") if (OUT / "vlm/batch_state.json").exists() else {}
    sst = jl(OUT / "vlm/standard_state.json") if (OUT / "vlm/standard_state.json").exists() else {}
    res = list(results.values())
    spent = sum(float(x.get("usd") or 0) for x in led)
    by_mode = defaultdict(lambda: {"n": 0, "ok": 0, "usd": 0.0, "pt": 0, "ct": 0, "rt": 0})
    for r in res:
        m = by_mode[r["mode"]]
        m["n"] += 1
        m["ok"] += bool(r.get("ok"))
        m["usd"] += float(r.get("usd") or 0)
        m["pt"] += int(r.get("prompt_tokens") or 0)
        m["ct"] += int(r.get("completion_tokens") or 0)
        m["rt"] += int(r.get("reasoning_tokens") or 0)
    attempts = len(led)
    fin = (bst.get("final") or {})

    def ts(e):
        return datetime.fromtimestamp(e, LON).strftime("%H:%M:%S") if e else "—"

    L = ["# blind20 视觉复核：gpt-6.2-astra（单测）", "",
         f"生成：{datetime.now(LON).strftime('%Y-%m-%d %H:%M')}（伦敦时间 UTC+0）。脚本 `scripts/astra_run.py`（prepare/run）、`scripts/astra_score.py`（计分）。",
         "", "> 本次按父 agent 调整只做**单测**：逐区域比较「最优解底稿在该区域的文字」与「astra 的读法」，**没有把 astra 结果合并进底稿（组合测试未跑）**，"
         "所以没有整份文档的读出率/CER，也没有合并后的中间 JSON。", "",
         "> 最优解的四条后处理规则是在这 20 页上调出来的，不是盲测；送检区域按路线 2 规则选，不看参照，参照只用于计分。", ""]
    # 0 模型与价格
    L += ["## 1. 模型可用性与价格", "",
          "- `GET /v1/models`（只读，未发图）：返回 98 个模型，**含 `gpt-6.2-astra`**（另有 `gpt-6-astra`）。",
          "- 价格已在 OpenAI 官方页核实（platform.openai.com/docs/pricing 与 /docs/models/gpt-6.2-astra）：Standard 输入 $8、输出 $40、缓存输入 $0.40 /百万 token（短上下文）；Batch 五折 $4 / $20。与转述一致。",
          "- 图像 token：官方 vision 文档只列了 `gpt-6-astra`（32px 补丁、detail=high 预算 2,500 补丁、×1.2），`gpt-6.2-astra` 未单列，估算按同族规则（推定）。实际计费以返回的 usage 为准。",
          "- 调用参数：Chat Completions，`detail=high`（裁图长边 ≤1600，均在补丁预算内，不缩放），`reasoning_effort=low`，`max_completion_tokens=2000`（含推理 token，用于把最坏情况压在 $5 内）。", ""]
    # 2 估算
    L += ["## 2. 开跑前估算", "",
          f"| 项 | 值 |", "|---|---|",
          f"| 请求数 | {est['n_requests']}（主测 {est['n_requests'] - est['by_category'].get('seal', 0)}：低置信行 {est['by_category'].get('lowconf')}、不一致行 {est['by_category'].get('mismatch')}、表格段 {est['by_category'].get('table')}、漏读行 {est['by_category'].get('missing')}；印章小测 {est['by_category'].get('seal')}） |",
          f"| 估算输入 token | {est['est_prompt_tokens']:,}（其中图像 {est['est_image_tokens']:,}） |",
          f"| 估算输出 token | {est['est_completion_tokens']:,}（每块按推理 400 token 保守估） |",
          f"| 估算费用 | Batch ${est['est_usd_batch']:.2f}；标准接口 ${est['est_usd_standard']:.2f} |",
          f"| 最坏情况（每块输出顶满 2000） | Batch ${est['worst_case_usd_batch']:.2f}；标准 ${est['worst_case_usd_standard']:.2f}（标准接口靠逐块预留额度控制在 $5 内） |",
          f"| 结论 | 估算 < $5，开跑 |", ""]
    # 3 执行
    L += ["## 3. 执行过程、调用次数、token、费用、耗时", ""]
    if bst:
        rc = fin.get("request_counts") or {}
        L += [f"- Batch：{__import__('datetime').datetime.fromisoformat(bst['submitted_at']).astimezone(__import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}（伦敦时间 UTC+0）提交 {bst.get('n')} 个请求（JSONL {bst.get('bytes', 0) / 1e6:.1f} MB，含 base64 裁图）。"
              f"最终状态 `{fin.get('status')}`，完成 {rc.get('completed')}/{rc.get('total')}，失败 {rc.get('failed')}；墙钟 {bst.get('wall_sec')} 秒。"
              + (" 按调整后的规则（超约 10 分钟未完成即取消），已请求取消，未完成的部分改走标准接口；Batch 里已完成的请求直接采用、不再重发，避免重复计费。" if bst.get("cancel_requested_at") else "")]
    if sst:
        L += [f"- 标准接口（并发 8）：补跑 {sst.get('n')} 块，墙钟 {sst.get('wall_sec')} 秒。"
              " 原因：Batch 已在约 2.6 分钟内全部完成（没触发 10 分钟超时、没有取消），但 r2-p05-line-02（二维码区域）的 Batch 返回为空串（3 个输出 token），"
              "按规则判失败，只对这 1 块走标准接口重跑；两次都已计费并记入账本（Batch $0.0009 + 标准 $0.0018），其余块没有重复调用。" + (f" 停止原因：{sst['stop']}" if sst.get("stop") else "")]
    L += ["", "| 通道 | 结果数 | 成功 | 输入 token | 输出 token（其中推理） | 费用 |", "|---|---|---|---|---|---|"]
    for k, m in by_mode.items():
        L.append(f"| {k} | {m['n']} | {m['ok']} | {m['pt']:,} | {m['ct']:,}（{m['rt']:,}） | ${m['usd']:.4f} |")
    L += [f"| **合计** | {sum(m['n'] for m in by_mode.values())} | {sum(m['ok'] for m in by_mode.values())} | {sum(m['pt'] for m in by_mode.values()):,} | "
          f"{sum(m['ct'] for m in by_mode.values()):,}（{sum(m['rt'] for m in by_mode.values()):,}） | **${spent:.4f}**（≈¥{spent * 7.2:.2f}） |", "",
          f"账本 `output/astra/vlm/ledger.jsonl` 共 {attempts} 条尝试记录（含失败/跳过），累计 ${spent:.4f}，远低于 $5 硬上限。失败或截断的块：" +
          (", ".join(f"{r['item_id']}（{r.get('error')}）" for r in res if not r.get("ok")) or "无") + "。百度调用 0 次。", ""]
    # 4 单测结果
    L += ["## 4. 单测：逐区域「最优解底稿」vs「astra」（修正参照）", "",
          "计分：先用 A（高精度含位置）落在该区域框内的字，在修正参照同页里做半全局对齐定出区域窗口（A 与两个被比较对象都独立），再并入与之重叠的底稿/astra 窗口；"
          "A 为空时用底稿与 astra 窗口的并集；对不上参照（代价超过一半字数）的读法不定窗，只计为多读。两者对同一窗口算 D/S/I。"
          "这是区域级近似：只统计送检区域内的字，结果不能直接换算成整份的读出率/CER。", "",
          "| 区域类别 | 块数（成功） | 区域参照字数 | 底稿 错字 D/S/I | astra 错字 D/S/I | 底稿 区域 CER | astra 区域 CER | 提升（错字数减少） |", "|---|---|---|---|---|---|---|---|"]
    names = {"lowconf": "低置信行", "mismatch": "A/B 不一致行", "missing": "B 漏读行", "table": "表格不一致段", "all": "合计"}
    for k in ("lowconf", "mismatch", "missing", "table", "all"):
        a = agg.get(k)
        if not a:
            continue
        eb = a["B"]["D"] + a["B"]["S"] + a["B"]["I"]
        ex = a["X"]["D"] + a["X"]["S"] + a["X"]["I"]
        refn = max(a["B"]["ref"], 1)
        L.append(f"| {names[k]} | {a['n']}（{a['n_ok']}） | {a['B']['ref']} | {eb}（{a['B']['D']}/{a['B']['S']}/{a['B']['I']}） | {ex}（{a['X']['D']}/{a['X']['S']}/{a['X']['I']}） | "
                 f"{eb / refn * 100:.2f}% | {ex / max(a['X']['ref'], 1) * 100:.2f}% | {eb - ex:+d} |")
    L += ["", "注：底稿在「B 漏读行」上是空串，所以该类底稿错字=整行漏字；astra 在这类上的错字含它多读的内容。", ""]
    better = [r for r in rows if r["astra_err"] < r["B_err"]]
    worse = [r for r in rows if r["astra_err"] > r["B_err"]]
    L += [f"逐块：astra 更好 {len(better)} 块，更差 {len(worse)} 块，持平 {len(rows) - len(better) - len(worse)} 块。", "",
          "### 4.1 astra 比底稿差的块（全部）", "", "| 块 | 页 | 类别 | 底稿错 | astra错 | 参照窗口 | 底稿 | astra |", "|---|---|---|---|---|---|---|---|"]
    esc = lambda s: (s or "").replace("|", "\\|").replace("\n", "/")[:60]  # noqa: E731
    for r in sorted(worse, key=lambda r: r["B_err"] - r["astra_err"]):
        L.append(f"| {r['id']} | {r['page']} | {names[r['category']]} | {r['B_err']} | {r['astra_err']} | {esc(r['ref_window'])} | {esc(r['B'])} | {esc(r['astra'])} |")
    L += ["", "### 4.2 astra 比底稿好的块（全部）", "", "| 块 | 页 | 类别 | 底稿错 | astra错 | 参照窗口 | 底稿 | astra |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(better, key=lambda r: r["astra_err"] - r["B_err"]):
        L.append(f"| {r['id']} | {r['page']} | {names[r['category']]} | {r['B_err']} | {r['astra_err']} | {esc(r['ref_window'])} | {esc(r['B'])} | {esc(r['astra'])} |")
    L.append("")
    # 5 手写
    L += ["## 5. 手写内容", "", "### 5.1 送检区域里的手写日期行", "",
          "| 块 | 页 | bbox(pt) | B 置信 | 参照窗口 | 底稿 | astra | 底稿错 | astra错 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in hand["date_rows"]:
        L.append(f"| {r['id']} | {r['page']} | {r['bbox_pt']} | {r['b_score']} | {esc(r['ref_window'])} | {esc(r['B'])} | {esc(r['astra'])} | {r['B_err']} | {r['astra_err']} |")
    L += ["", "### 5.2 手写签名区域（最优解剩下的 7 个签名类错字）", "",
          "这些块在 B 原始返回里版面类型是 `hand_sign`；路线 2 的低置信规则只看 text/title/head_tail 类型，所以**签名块本身都没进本次送检**，astra 没有读过签名裁图。", "",
          "| 页 | B 块 | bbox(pt) | B 文字 | B 版面置信度 | A 同位置文字 | A 行置信度 | 是否在本次送检裁图中 |", "|---|---|---|---|---|---|---|---|"]
    for s in hand["signature_blocks"]:
        L.append(f"| {s['page']} | {s['block_id']} | {s['bbox_pt']} | {s['text']} | {s['b_layout_score']} | {', '.join(map(str, s['a_text']))} | "
                 f"{', '.join(f'{c:.3f}' for c in s['a_confidence'] if c is not None)} | {', '.join(s['in_astra_crops']) or '否'} |")
    L += ["", "7 个错字与区域对应：p3 `b-003-009`「周浪」(参照该处为「:」+「周」，计 2+1=3 字)；p8 `b-008-010`「陈强」2 字；p8 `b-008-014`「周浪」2 字。p6 `b-006-009`「:周锦」的冒号是补漏误补（计入标点，不在 7 字内）。", ""]
    # 6 印章
    L += ["## 6. 印章小测（4 块，单独报，不计入主测）", "",
          "选块：路线 2 的 30 个印章区按 id 排序取第 1、8、15、22 个（不看参照）。参照用修正后的印章参照，取同页与输出最相近的那一枚。", "",
          "| 块 | 页 | 参照（最近的一枚） | B | astra | B D/S/I | astra D/S/I | astra 编出的字（不在本页任何参照印章里） | B 同口径 |", "|---|---|---|---|---|---|---|---|---|"]
    for s in seals:
        a, b = s["astra_dsi"], s["B_dsi"]
        L.append(f"| {s['id']} | {s['page']} | {s['ref_best_match']} | {s['B']} | {s['astra']} | {b['D']}/{b['S']}/{b['I']} | {a['D']}/{a['S']}/{a['I']} | {s['astra_chars_not_in_any_ref_seal'] or '无'} | {s['B_chars_not_in_any_ref_seal'] or '无'} |")
    L.append("")
    # 7 对比
    L += ["### 6.1 小结：手写与编字", "",
          "- **手写日期**：送检的 4 行手写日期 astra 全对（4/4）；底稿错 1 处（p8 r2-p08-line-10 甲方日期，手写「20」底稿读成「30」，astra 读对）。裁图目测也是「20」。",
          "- **手写签名**：签名块（B 版面类型 hand_sign）不在路线 2 的选块规则内，本次**没有送检签名裁图**，所以无法回答 astra 读签名准不准；最优解剩下的 7 个签名类错字对应的区域见 5.2，下一步组合测试可把这 4 个框（含 p6）单独裁图送检。",
          "  注意：这 7 个错字主要不是「读错名字」——p3 是多读/位置错（参照为「:」+「周」），p8 两处是参照里没有对应字（签名字被计为多读）——所以即使 astra 读得准，也要先定好签名字是否计入参照，否则送检无收益。",
          "- **编字（印章小测）**：p1 的骑缝章残片（裁图可见「…茅台酒股份有…」的半枚章，修正参照里这一页没有印章文字）astra 输出「威海台基睿酞股份有」，9 字里约 5 字是编的（威海、基睿酞）；"
          "其余 3 枚完整印章 astra 与 B 读法相同或更干净（p14 没带出印章编号 1101090129516）。结论：完整印章基本可信，残缺/半枚印章会编出看起来合理的字，组合时不应采用。",
          "- **其他编字/误读**：二维码区域 astra 输出「扫一扫」、p2 两处空白区输出「は」「一」、p19 一处输出「回」，都是多读；p3「贵州」误为「地州」1 处。",
          "- **表格**：astra 在表格段明显更差（-45 字），主要是按行带裁图时漏掉带边缘的整行（p20 合计行、p14 两段首/尾行），不是认错字。",
          ""]
    L += ["## 7. 对比（修正参照）", "",
          "| 方案 | 读出率 | CER | 说明 |", "|---|---|---|---|",
          "| B 原始基线 | 98.69% | 1.67% | 文档解析标准版，不加后处理 |",
          "| 最优解 | 99.57% | 0.61% | B + ★/☆ + 剔除印章字 + A/U 补漏 + 发票重排（规则在本 20 页调出） |",
          "| 路线 2（gpt-4o/gpt-5-mini，阈值 0.75，+★） | 98.98% | 1.77% | 之前的混合路线，B 原始为底稿；比 B+★ 差 |",
          "| 最优解 + astra（组合） | 未跑 | 未跑 | 本次按调整只做单测 |", "",
          "参考（不直接比分）：第二轮 PP-StructureV3 + VLM 共 229 次（gpt-5-mini 182 次、gpt-4o 47 次），费用 $0.6379，VLM 耗时合计 1132.8 秒；"
          "那一轮是另一套页（A 组文字层页 + B 组扫描页）、另一种参照（文字层金标准 / 本地与 VLM 一致的银标准：145 块中一致 20、分歧 125），"
          "且 A 组准确率在报告中为空（0 页计分），见 `/workspace/pdf-test/ocr/output/round2/report.md`。", ""]
    L += ["## 8. 文件", "", "- 报告 `output/astra/report.md`；送检清单 `output/astra/manifest.json`；估算 `output/astra/estimate.json`",
          "- 每块原始输出 `output/astra/vlm/results/*.json`（含 usage、费用、模式）；账本 `output/astra/vlm/ledger.jsonl`；Batch 状态 `output/astra/vlm/batch_state.json`",
          "- 单测明细 `output/astra/scores_unit.json`", "- 配置 `config_astra_live.yaml`（仅此文件 allow_external=true）；`config.yaml` 仍为 false；frozen20 与 ocr/config.yaml 未动", ""]
    (OUT / "report.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:5]))


if __name__ == "__main__":
    main()
