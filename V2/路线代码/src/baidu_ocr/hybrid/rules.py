"""两条混合路线共用的「难区域 / 低置信」判定规则，以及各路线的送审选择。

所有坐标为 pt（页面左上原点，y 向下），裁图时再按 200 DPI 换成像素（px = pt × 200 / 72）。
本模块是纯函数，不读文件、不联网，便于单测与复现。
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable

# ----------------------------------------------------------------------------- 规则常量（两条路线共用，改动须同步写进报告）
RULES: dict = {
    "version": "hybrid-rules-v1 (2026-11-18)",
    "render_dpi": 200,
    # D1 表格：B 标准版 layout.type == table 的块，整表为难区域。
    "table_raw_types": ["table"],
    # D2 印章：B 标准版的 seal 块（layouts 与 seals[]）∪ 本地红色像素检测框（blind20_local.seal_stats，纯像素，不依赖 A 的识别）。
    #     两者 IoU ≥ 0.3 或一方中心落在另一方框内即合并为一个印章区域。
    "seal_merge_iou": 0.3,
    # D3 低置信行：B 标准版文字类 layout（raw type ∈ text/title/head_tail）且 layout.score < 0.95（送审阈值）；
    #     score == 0 视为「无分数」，也算低置信。送审时按 0.95 一次送齐；计分时离线切换生效阈值 0.75 / 0.85 / 0.95
    #     （score ≥ 生效阈值、且没有其他送审原因的行，保留 B 原文）。两条路线同一套阈值。
    "lowconf_raw_types": ["text", "title", "head_tail"],
    "lowconf_score_lt": 0.95,
    "lowconf_eval_thresholds": [0.75, 0.85, 0.95],
    "unscored_is_lowconf": True,
    # 手写签名（raw type hand_sign）两条路线都不送 VLM（参照不收录手写签名），原样保留。
    "skip_raw_types": ["hand_sign"],
    # D4 发票无线框区：页面任一 B 文字含「发票号码」或「电子发票」即为发票页；
    #     发票页上 B 表格中，从含「项目名称/货物或应税劳务」的行起，到第一个含「合计」但不含「价税合计」的行止（含），为无线框明细区。
    #     路线 1 中该区强制单独成一段裁图（gpt-4o）。
    "invoice_page_keywords": ["发票号码", "电子发票"],
    "invoice_noframe_start": ["项目名称", "货物或应税劳务"],
    "invoice_noframe_end": "合计",
    "invoice_noframe_end_exclude": "价税合计",
    # 表格切段：裁图长边先压到 ≤1600；若压后短边仍 > 768（gpt-4o detail=high 会再把短边缩到 768），按 B 的行边界切成多段。
    "max_side_px": 1600,
    "short_side_limit_px": 768,
    # 裁图外扩（px，200 DPI）
    "pad_px": {"line_x": 10, "line_y": 5, "table": 4, "seal": 12},
    # 路线 2：A 行归属 B 块——A 行框中心落在 B 块框（外扩 3pt）内；多个候选取交叠面积最大者。
    "assign_expand_pt": 3.0,
    # 路线 2：一致性比较口径与计分一致（NFKC、去空白、去【?】），完全相同才算一致。
    # 路线 2：A 的覆盖缺口块（本地行框无输出）只取 gap_class=text_or_other，二维码与印章残片不送。
    "gap_classes_sent": ["text_or_other"],
    # 模型：表格类（table_band）→ gpt-4o detail=high；其他 → gpt-5-mini（detail=auto，与第二轮一致）。
    "model_table": ["gpt-4o", "high"],
    "model_other": ["gpt-5-mini", "auto"],
}

UNC = "【?】"


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace(unicodedata.normalize("NFKC", UNC), "").replace(UNC, "")
    return "".join(ch for ch in s if not ch.isspace())


# ----------------------------------------------------------------------------- 几何
def area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b) -> float:
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def iou(a, b) -> float:
    i = inter(a, b)
    u = area(a) + area(b) - i
    return i / u if u > 0 else 0.0


def center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def contains_pt(box, pt, expand: float = 0.0) -> bool:
    return box[0] - expand <= pt[0] <= box[2] + expand and box[1] - expand <= pt[1] <= box[3] + expand


def union(boxes: Iterable) -> list[float]:
    bs = list(boxes)
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


# ----------------------------------------------------------------------------- D1–D4 判定
def model_for(kind: str) -> tuple[str, str]:
    return tuple(RULES["model_table"]) if kind == "table_band" else tuple(RULES["model_other"])  # type: ignore[return-value]


def is_text_like(b: dict) -> bool:
    return b.get("type") not in ("table", "seal")


def is_skipped(b: dict) -> bool:
    return (b.get("raw_type") or "") in RULES["skip_raw_types"]


def is_lowconf(b: dict) -> bool:
    """D3。b 需带 raw_type（B 原始 layout type）与 score（B 原始 layout.score）。"""
    if not is_text_like(b) or is_skipped(b):
        return False
    if (b.get("raw_type") or "") not in RULES["lowconf_raw_types"]:
        return False
    sc = b.get("score")
    if sc is None or float(sc) == 0.0:
        return bool(RULES["unscored_is_lowconf"])
    return float(sc) < RULES["lowconf_score_lt"]


def is_invoice_page(blocks: list[dict]) -> bool:
    txt = "".join(b.get("text") or "" for b in blocks)
    for b in blocks:
        for c in ((b.get("table") or {}).get("cells") or []):
            txt += c.get("text") or ""
    return any(k in txt for k in RULES["invoice_page_keywords"])


def table_rows(tblock: dict) -> list[dict]:
    """B 表格按行聚合：行框 = 该行起始单元格的 bbox 并集（x 取整表范围）。"""
    cells = (tblock.get("table") or {}).get("cells") or []
    tb = tblock["bbox"]
    rows: dict[int, list] = {}
    for c in cells:
        rows.setdefault(int(c["row"]), []).append(c)
    out = []
    for r in sorted(rows):
        cs = sorted(rows[r], key=lambda c: int(c["col"]))
        single = [c for c in cs if c.get("bbox") and int(c.get("rowspan") or 1) == 1]
        withbb = single or [c for c in cs if c.get("bbox")]
        if withbb:
            y0 = min(c["bbox"][1] for c in withbb)
            y1 = max(c["bbox"][3] for c in withbb)
        else:
            y0 = y1 = None
        out.append({"row": r, "cells": cs, "y0": y0, "y1": y1, "x0": tb[0], "x1": tb[2],
                    "text": " ".join(c.get("text") or "" for c in cs)})
    # 无 bbox 的行：按相邻行插值，最后兜底整表均分
    n = len(out)
    for i, r in enumerate(out):
        if r["y0"] is None:
            prev_y = out[i - 1]["y1"] if i > 0 and out[i - 1]["y1"] is not None else tb[1] + (tb[3] - tb[1]) * i / max(1, n)
            nxt = next((q["y0"] for q in out[i + 1:] if q["y0"] is not None), None)
            r["y0"] = prev_y
            r["y1"] = nxt if nxt is not None and nxt > prev_y else min(tb[3], prev_y + (tb[3] - tb[1]) / max(1, n))
    # 行框之间不留缝：上下扩到相邻行中线，首末行扩到表框
    for i, r in enumerate(out):
        r["band_y0"] = tb[1] if i == 0 else (out[i - 1]["y1"] + r["y0"]) / 2.0
        r["band_y1"] = tb[3] if i == n - 1 else (r["y1"] + out[i + 1]["y0"]) / 2.0
        if r["band_y1"] <= r["band_y0"]:
            r["band_y1"] = r["band_y0"] + 1.0
    return out


def invoice_noframe_rows(rows: list[dict]) -> list[int]:
    start = end = None
    for i, r in enumerate(rows):
        t = norm(r["text"])
        if start is None and any(k in t for k in RULES["invoice_noframe_start"]):
            start = i
            continue
        if start is not None and RULES["invoice_noframe_end"] in t and RULES["invoice_noframe_end_exclude"] not in t:
            end = i
            break
    if start is None:
        return []
    if end is None:
        end = len(rows) - 1
    return list(range(start, end + 1))


def _scaled_dims(w_px: float, h_px: float) -> tuple[float, float]:
    s = min(1.0, RULES["max_side_px"] / max(w_px, h_px, 1.0))
    return w_px * s, h_px * s


def band_ok(x0, x1, y0, y1, px_per_pt: float) -> bool:
    pad = RULES["pad_px"]["table"] * 2
    w, h = _scaled_dims((x1 - x0) * px_per_pt + pad, (y1 - y0) * px_per_pt + pad)
    return min(w, h) <= RULES["short_side_limit_px"]


def split_bands(rows: list[dict], idx: list[int], px_per_pt: float, forced_breaks: set[int] | None = None) -> list[list[int]]:
    """把行下标 idx（升序）切成若干连续段：不连续处必断；forced_breaks 中的行必须作为段首；
    其余按「压缩后短边 ≤ 768」贪心合并。单行超限也单独成段。"""
    forced_breaks = forced_breaks or set()
    bands: list[list[int]] = []
    cur: list[int] = []
    for i in idx:
        if cur and (i != cur[-1] + 1 or i in forced_breaks):
            bands.append(cur)
            cur = []
        if cur:
            r0, r1 = rows[cur[0]], rows[i]
            if not band_ok(r0["x0"], r0["x1"], r0["band_y0"], r1["band_y1"], px_per_pt):
                bands.append(cur)
                cur = []
        cur.append(i)
    if cur:
        bands.append(cur)
    return bands


def merge_seal_regions(b_seals: list[dict], local_boxes: list[list[float]]) -> list[dict]:
    """D2：B 印章块 ∪ 本地红章框。返回 [{bbox, b_ids, local_boxes}]，按 (y, x) 排序。"""
    regs = [{"bbox": list(b["bbox"]), "b_ids": [b["id"]], "local_boxes": []} for b in b_seals]
    for lb in local_boxes:
        hit = None
        for r in regs:
            if iou(r["bbox"], lb) >= RULES["seal_merge_iou"] or contains_pt(r["bbox"], center(lb)) or contains_pt(lb, center(r["bbox"])):
                hit = r
                break
        if hit is None:
            regs.append({"bbox": list(lb), "b_ids": [], "local_boxes": [list(lb)]})
        else:
            hit["local_boxes"].append(list(lb))
            hit["bbox"] = union([hit["bbox"], lb])
    # 合并后可能互相重叠，再迭代一次
    changed = True
    while changed:
        changed = False
        for i in range(len(regs)):
            for j in range(i + 1, len(regs)):
                a, b = regs[i], regs[j]
                if iou(a["bbox"], b["bbox"]) >= RULES["seal_merge_iou"]:
                    a["bbox"] = union([a["bbox"], b["bbox"]])
                    a["b_ids"] += b["b_ids"]
                    a["local_boxes"] += b["local_boxes"]
                    regs.pop(j)
                    changed = True
                    break
            if changed:
                break
    return sorted(regs, key=lambda r: (round(r["bbox"][1]), r["bbox"][0]))


# ----------------------------------------------------------------------------- A/B 交叉核对（路线 2 专用）
def order_by_lines(items: list[dict], key=lambda it: it["bbox"]) -> list[dict]:
    """按行（中心 y 聚类，容差 = 0.5×中位高度）再按 x 排序。"""
    if not items:
        return []
    hs = sorted(max(0.1, key(it)[3] - key(it)[1]) for it in items)
    tol = 0.5 * hs[len(hs) // 2]
    its = sorted(items, key=lambda it: center(key(it))[1])
    lines: list[list] = [[its[0]]]
    for it in its[1:]:
        cy = center(key(it))[1]
        ly = sum(center(key(q))[1] for q in lines[-1]) / len(lines[-1])
        if abs(cy - ly) <= tol:
            lines[-1].append(it)
        else:
            lines.append([it])
    out = []
    for ln in lines:
        out.extend(sorted(ln, key=lambda it: key(it)[0]))
    return out


def assign_a_lines(page: dict, seal_regions: list[dict]) -> dict:
    """返回 {a_line_id: ("block", b_id) | ("table", b_id) | ("seal", k) | ("none", None)}。"""
    ex = RULES["assign_expand_pt"]
    res = {}
    cand = [b for b in page["b_blocks"] if b.get("type") != "seal"]
    for al in page["a_lines"]:
        c = center(al["bbox"])
        hits = [b for b in cand if contains_pt(b["bbox"], c, ex)]
        if hits:
            best = max(hits, key=lambda b: (inter(b["bbox"], al["bbox"]), -area(b["bbox"])))
            res[al["id"]] = ("table" if best.get("type") == "table" else "block", best["id"])
            continue
        sk = next((k for k, r in enumerate(seal_regions) if contains_pt(r["bbox"], c)), None)
        res[al["id"]] = ("seal", sk) if sk is not None else ("none", None)
    return res


def cell_a_text(cell_bbox, a_chars: list[dict]) -> str:
    inside = [ch for ch in a_chars if contains_pt(cell_bbox, center(ch["bbox"]))]
    return "".join(ch["ch"] for ch in order_by_lines(inside))


def table_row_mismatches(tblock: dict, rows: list[dict], a_chars: list[dict]) -> dict[int, list[dict]]:
    """逐单元格比较 B 文本与落在该格内的 A 字（A 原始 small 粒度字框）。返回 {行下标: [不一致单元格明细]}。
    跨行单元格不一致时，其覆盖的所有行都记为不一致。无 bbox 的单元格无法核对，按不一致处理。"""
    out: dict[int, list[dict]] = {}
    row_of = {r["row"]: i for i, r in enumerate(rows)}
    for i, r in enumerate(rows):
        for c in r["cells"]:
            if not c.get("bbox"):
                diff = {"row": r["row"], "col": c["col"], "b": c.get("text") or "", "a": None, "why": "no_cell_bbox"}
            else:
                at = cell_a_text(c["bbox"], a_chars)
                if norm(at) == norm(c.get("text")):
                    continue
                diff = {"row": r["row"], "col": c["col"], "b": c.get("text") or "", "a": at, "why": "text_differs"}
            span = int(c.get("rowspan") or 1)
            for rr in range(r["row"], r["row"] + span):
                if rr in row_of:
                    out.setdefault(row_of[rr], []).append(diff)
    return out


# ----------------------------------------------------------------------------- 路线选择
def _region(kind, page, bbox, reasons, target, ctx) -> dict:
    model, detail = model_for(kind)
    return {"kind": kind, "page": page, "bbox_pt": [round(v, 2) for v in bbox], "reasons": reasons,
            "model": model, "detail": detail, "target": target, "context": ctx}


def common_hard_regions(page: dict) -> dict:
    """D1–D4（两条路线共用的判定结果）。"""
    blocks = page["b_blocks"]
    seals_b = [b for b in blocks if b.get("type") == "seal"]
    seal_regions = merge_seal_regions(seals_b, page.get("local_seals") or [])
    invoice = is_invoice_page(blocks)
    tables = []
    for b in blocks:
        if b.get("type") == "table":
            rows = table_rows(b)
            nf = invoice_noframe_rows(rows) if invoice else []
            tables.append({"block": b, "rows": rows, "invoice_noframe_rows": nf})
    lowconf = [b for b in blocks if is_lowconf(b)]
    skipped = [b for b in blocks if is_skipped(b)]
    return {"seal_regions": seal_regions, "invoice_page": invoice, "tables": tables, "lowconf": lowconf, "skipped": skipped}


def _seal_regions_out(page, hard) -> list[dict]:
    out = []
    for k, r in enumerate(hard["seal_regions"]):
        src = ("B+local" if r["b_ids"] and r["local_boxes"] else ("B" if r["b_ids"] else "local"))
        btxt = "".join(b.get("text") or "" for b in page["b_blocks"] if b["id"] in r["b_ids"])
        out.append(_region("seal", page["page"], r["bbox"], [f"D2_seal:{src}"],
                           {"seal_index": k, "b_seal_ids": r["b_ids"], "local_boxes_pt": r["local_boxes"]},
                           {"b_text": btxt, "expected_chars": max(12, len(norm(btxt)))}))
    return out


def _band_region(page, t, band, reasons) -> dict:
    rows = t["rows"]
    r0, r1 = rows[band[0]], rows[band[-1]]
    # 裁图只取本段单元格的实际上下沿（不外扩到相邻行中线），减少相邻行露出
    bbox = [r0["x0"], r0["y0"], r0["x1"], r1["y1"]]
    btxt = "\n".join(rows[i]["text"] for i in band)
    nf = set(t["invoice_noframe_rows"])
    rs = list(reasons)
    if nf and any(i in nf for i in band):
        rs.append("D4_invoice_noframe")
    return _region("table_band", page["page"], bbox, rs,
                   {"b_block_id": t["block"]["id"], "row_idx": [band[0], band[-1]],
                    "rows": [rows[band[0]]["row"], rows[band[-1]]["row"]], "n_rows_table": len(rows),
                    "is_full_table": band[0] == 0 and band[-1] == len(rows) - 1},
                   {"b_text": btxt, "expected_chars": len(norm(btxt)), "n_cells": sum(len(rows[i]["cells"]) for i in band)})


def select_route1(page: dict, px_per_pt: float) -> dict:
    """路线 1（两层）：B 打底；D1 表格（整表，按行切段）、D2 印章、D3 低置信行、D4 发票无线框区 全部直接送 VLM。"""
    hard = common_hard_regions(page)
    regs = []
    for t in hard["tables"]:
        idx = list(range(len(t["rows"])))
        nf = t["invoice_noframe_rows"]
        forced = set()
        if nf:
            forced = {nf[0]}
            if nf[-1] + 1 < len(idx):
                forced.add(nf[-1] + 1)
        for band in split_bands(t["rows"], idx, px_per_pt, forced):
            regs.append(_band_region(page, t, band, ["D1_table"]))
    regs += _seal_regions_out(page, hard)
    for b in hard["lowconf"]:
        regs.append(_region("line", page["page"], b["bbox"], [f"D3_lowconf:score={b.get('score')}"],
                            {"b_block_id": b["id"]}, {"b_text": b.get("text") or "", "b_score": b.get("score"),
                                                      "expected_chars": len(norm(b.get("text")))}))
    return {"regions": regs, "hard": hard}


def select_route2(page: dict, px_per_pt: float) -> dict:
    """路线 2（三层）：B 打底 + A 行框交叉核对。只送：A/B 不一致的行（含表格行）、漏行、D3 低置信行、D2 印章。"""
    hard = common_hard_regions(page)
    regs = []
    stats = {"lines_compared": 0, "lines_mismatch": 0, "lowconf_and_ab_agree": 0, "table_rows_compared": 0,
             "table_rows_mismatch": 0, "a_lines_unassigned": 0, "a_gaps_sent": 0}
    seal_regions = hard["seal_regions"]
    a_chars = [ch for ch in page.get("a_chars") or [] if not any(contains_pt(r["bbox"], center(ch["bbox"])) for r in seal_regions)]
    # 表格：逐格核对，不一致行按段送 gpt-4o
    for t in hard["tables"]:
        mism = table_row_mismatches(t["block"], t["rows"], a_chars)
        stats["table_rows_compared"] += len(t["rows"])
        stats["table_rows_mismatch"] += len(mism)
        for band in split_bands(t["rows"], sorted(mism), px_per_pt):
            reg = _band_region(page, t, band, ["ab_mismatch_table_row"])
            reg["context"]["cell_diffs"] = [d for i in band for d in mism.get(i, [])]
            regs.append(reg)
    regs += _seal_regions_out(page, hard)
    # 文字行
    assign = assign_a_lines(page, seal_regions)
    by_block: dict[str, list] = {}
    for al in page["a_lines"]:
        k, bid = assign[al["id"]]
        if k == "block":
            by_block.setdefault(bid, []).append(al)
    low_ids = {b["id"] for b in hard["lowconf"]}
    for b in page["b_blocks"]:
        if not is_text_like(b) or is_skipped(b):
            continue
        als = order_by_lines(by_block.get(b["id"], []))
        a_text = "".join(a["text"] for a in als)
        stats["lines_compared"] += 1
        mismatch = norm(a_text) != norm(b.get("text"))
        reasons = []
        if mismatch:
            stats["lines_mismatch"] += 1
            reasons.append("ab_mismatch" + (":a_empty" if not als else ""))
        if b["id"] in low_ids:
            reasons.append(f"D3_lowconf:score={b.get('score')}")
            if not mismatch:
                stats["lowconf_and_ab_agree"] += 1
        if reasons:
            bbox = union([b["bbox"]] + [a["bbox"] for a in als]) if als else b["bbox"]
            regs.append(_region("line", page["page"], bbox, reasons, {"b_block_id": b["id"]},
                                {"b_text": b.get("text") or "", "a_text": a_text, "b_score": b.get("score"),
                                 "expected_chars": max(len(norm(b.get("text"))), len(norm(a_text)))}))
    # 漏行：A 有、B 没有任何块覆盖的行
    for al in page["a_lines"]:
        k, _ = assign[al["id"]]
        if k != "none" or not norm(al.get("text")):
            continue
        stats["a_lines_unassigned"] += 1
        regs.append(_region("missing_line", page["page"], al["bbox"], ["missing_in_B:a_line"],
                            {"insert": True, "a_line_id": al["id"]},
                            {"a_text": al["text"], "expected_chars": len(norm(al["text"]))}))
    for g in page.get("a_gaps") or []:
        if g.get("gap_class") not in RULES["gap_classes_sent"]:
            continue
        c = center(g["bbox"])
        if any(contains_pt(b["bbox"], c) for b in page["b_blocks"]) or any(contains_pt(r["bbox"], c) for r in seal_regions):
            continue
        stats["a_gaps_sent"] += 1
        regs.append(_region("missing_line", page["page"], g["bbox"], ["missing_in_A_and_B:coverage_gap"],
                            {"insert": True, "a_gap_id": g["id"]}, {"a_text": "", "expected_chars": 10}))
    return {"regions": regs, "hard": hard, "stats": stats}


def lowconf_only_inactive(item: dict, threshold: float) -> bool:
    """离线切换生效阈值：item 只因 D3 低置信被送审，且 B score ≥ threshold（非 0）时，计分版本不采用 VLM 结果。"""
    if item.get("kind") != "line":
        return False
    if any(not r.startswith("D3_lowconf") for r in item.get("reasons") or []):
        return False
    sc = (item.get("context") or {}).get("b_score")
    if sc is None or float(sc) == 0.0:
        return False
    return float(sc) >= threshold
