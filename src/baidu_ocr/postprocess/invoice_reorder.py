"""发票明细区按坐标重排。

问题：发票明细区没有横框线，文档解析常把相邻两行并进一个单元格（如「1787610.62\\n¥2858407.08」、
「*分析仪器*近红外分析仪DS3F合计」、「张个」），按单元格输出后行序错乱。
做法（只用坐标，不换 B 的字）：
1. 识别发票页（页内文字含「发票号码」或「电子发票」），在表格里找表头行（同时含「项目名称」「税额」），
   明细区 = 表头下一行起，到第一个含「合计」的行为止（含该行）。
2. 把明细区每个单元格的 B 文本按换行拆段，再用 A 的字框给每个字定位（先在单元格框内匹配，
   没对上的 ≥2 字连续片段再到同一行带内找）；相邻字 y 跳变 > 0.6 字高或 x 间隔过大处切成「片段」。
   对不上 A 的字沿用相邻字的位置（不单独成片段）。
3. 逻辑行起点 = 每个 B 行「项目名称」列最靠上的片段（该列为空时取全行最靠上的片段），加上「项目名称」列里以「*」或「合计」开头的片段；
   每个片段（纠偏后的 y）归到 y 不晚于它的最近起点；完全对不上 A 的片段留在原 B 行。
4. 每个逻辑行按表头各列的 x 范围分列，列内按 (y, x) 拼接，重建为新的表格行。
字符只搬家不增删（有断言）。
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

from .aux import chars_in
import re

from .textutil import norm, norm_with_map

DEBUG = False
NEW_ROW = re.compile(r"^\s*(\*|合)")


def is_invoice_page(blocks: list[dict]) -> bool:
    t = "".join((b.get("text") or "") for b in blocks)
    t += "".join(c.get("text") or "" for b in blocks for c in ((b.get("table") or {}).get("cells") or []))
    return "发票号码" in t or "电子发票" in t


def _rows(tab: dict) -> dict[int, list[dict]]:
    rows: dict[int, list[dict]] = {}
    for c in tab["cells"]:
        rows.setdefault(c["row"], []).append(c)
    return {k: sorted(v, key=lambda c: c["col"]) for k, v in sorted(rows.items())}


def find_region(tab: dict) -> tuple[int, int] | None:
    rows = _rows(tab)
    hdr = next((r for r, cs in rows.items() if "项目名称" in "".join(c["text"] for c in cs) and "税额" in "".join(c["text"] for c in cs)), None)
    if hdr is None:
        return None
    end = next((r for r in rows if r > hdr and "合计" in norm("".join(c["text"] for c in rows[r]))
                and "价税合计" not in norm("".join(c["text"] for c in rows[r]))), None)
    if end is None:
        return None
    return hdr, end


def _sorted(cs):
    return sorted(cs, key=lambda c: (round((c["bbox"][1] + c["bbox"][3]) / 2 / 4), c["bbox"][0]))


def _find_runs(sn: str, loc: list, pool: list[dict], min_run: int, used: set):
    """把 loc 里仍未定位、长度≥min_run 的连续片段，到 pool（已排序字框）里找精确子串（跳过已用字框）。"""
    pstr = "".join(norm(c["ch"])[:1] or "?" for c in pool)
    i = 0
    while i < len(sn):
        if loc[i] is not None:
            i += 1
            continue
        j = i
        while j < len(sn) and loc[j] is None:
            j += 1
        if j - i >= min_run:
            at = pstr.find(sn[i:j])
            while at >= 0 and any(id(pool[at + k]) in used for k in range(j - i)):
                at = pstr.find(sn[i:j], at + 1)
            if at >= 0:
                for k in range(j - i):
                    loc[i + k] = pool[at + k]
                    used.add(id(pool[at + k]))
        i = j


def _locate(seg: str, cell_bb, chars: list[dict], band_chars: list[dict], used: set | None = None) -> list[dict | None]:
    sn, _ = norm_with_map(seg)
    used = used if used is not None else set()
    cand = _sorted([c for c in chars_in(chars, cell_bb, pad=2.0) if id(c) not in used])
    astr = "".join(norm(c["ch"])[:1] or "?" for c in cand)
    loc: list[dict | None] = [None] * len(sn)
    for a, b, size in SequenceMatcher(None, sn, astr, autojunk=False).get_matching_blocks():
        for k in range(size):
            loc[a + k] = cand[b + k]
            used.add(id(cand[b + k]))
    # 没对上的 ≥2 字连续片段：到行带内找精确子串
    _find_runs(sn, loc, band_chars, 2, used)
    # 仍没对上的（含单字）：到本列 x 范围、向下多看约 1.5 行的窗口里找（如「张个」里的「个」实际在下一行）
    hs = sorted(c["bbox"][3] - c["bbox"][1] for c in cand) or [10.0]
    h = hs[len(hs) // 2]
    win = _sorted([c for c in chars if cell_bb[0] - 2 <= (c["bbox"][0] + c["bbox"][2]) / 2 <= cell_bb[2] + 2
                   and cell_bb[1] - 0.5 * h <= (c["bbox"][1] + c["bbox"][3]) / 2 <= cell_bb[3] + 1.5 * h])
    _find_runs(sn, loc, win, 1, used)
    return loc


def _pieces(cell: dict, chars, band_chars, used: set | None = None) -> list[dict]:
    out = []
    bb = cell["bbox"]
    segs = (cell.get("text") or "").split("\n")
    for li, seg in enumerate(segs):
        sn, smap = norm_with_map(seg)
        if not sn:
            continue
        loc = _locate(seg, bb, chars, band_chars, used)
        synthetic = all(v is None for v in loc)
        known = [k for k, v in enumerate(loc) if v is not None]
        synth = {"bbox": [bb[0] + 1, bb[1] + (bb[3] - bb[1]) * li / len(segs),
                          bb[0] + 2, bb[1] + (bb[3] - bb[1]) * (li + 1) / len(segs)]}
        for k in range(len(loc)):
            if loc[k] is None:
                prev = max((q for q in known if q < k), default=None)
                nxt = min((q for q in known if q > k), default=None)
                loc[k] = loc[prev] if prev is not None else (loc[nxt] if nxt is not None else synth)
        hs = [c["bbox"][3] - c["bbox"][1] for c in loc]
        h = sorted(hs)[len(hs) // 2] or 8.0
        starts = [0]
        for k in range(1, len(loc)):
            p, c = loc[k - 1]["bbox"], loc[k]["bbox"]
            if c is p:
                continue
            dy = abs((c[1] + c[3]) / 2 - (p[1] + p[3]) / 2)
            dx = c[0] - p[2]
            if dy > 0.6 * h or dx > max(2.5 * h, 12.0) or dx < -2.5 * h:
                starts.append(k)
        starts.append(len(loc))
        for s, e in zip(starts, starts[1:]):
            raw = seg[smap[s]: (smap[e] if e < len(smap) else len(seg))]
            boxes = [c["bbox"] for c in loc[s:e]]
            ys = sorted((b[1] + b[3]) / 2 for b in boxes)
            out.append({"text": raw.strip(), "x0": min(b[0] for b in boxes), "x1": max(b[2] for b in boxes),
                        "y": ys[len(ys) // 2], "y0": min(b[1] for b in boxes), "y1": max(b[3] for b in boxes), "h": h,
                        "src": (cell["row"], cell["col"]), "line": li, "synthetic": synthetic})
    return out


def _line_sort(ps: list[dict], tol: float) -> list[dict]:
    lines, cur = [], []
    for p in ps:
        if cur and p["y"] - cur[0]["y"] > tol:
            lines.append(cur)
            cur = []
        cur.append(p)
    if cur:
        lines.append(cur)
    return [p for ln in lines for p in sorted(ln, key=lambda p: p["x0"])]


def reorder_table(tab: dict, a_chars: list[dict]) -> tuple[dict, dict] | None:
    reg = find_region(tab)
    if reg is None:
        return None
    hdr, end = reg
    rows = _rows(tab)
    head = [c for c in rows[hdr] if c.get("bbox")]
    if len(head) < 3:
        return None
    col0 = min(head, key=lambda c: c["bbox"][0])
    used: set = set()
    pieces, by_row = [], {}
    for r in range(hdr + 1, end + 1):
        cs = rows.get(r) or []
        if not cs:
            continue
        ry0 = min(c["bbox"][1] for c in cs if c.get("bbox"))
        ry1 = max(c["bbox"][3] for c in cs if c.get("bbox"))
        band = _sorted([c for c in a_chars if ry0 - 2 <= (c["bbox"][1] + c["bbox"][3]) / 2 <= ry1 + 2])
        rp = []
        for c in cs:
            if c.get("bbox"):
                rp += _pieces(c, a_chars, band, used)
        for p in rp:
            p["brow"] = r
        by_row[r] = rp
        pieces += rp
    if not pieces:
        return None
    # 纠偏：每个 B 行取各单元格最靠上的片段，两两求斜率（|dx|>50pt），全部取中位数（Theil–Sen，抗离群）
    slopes = []
    for rp in by_row.values():
        first = {}
        for p in sorted([p for p in rp if not p["synthetic"]], key=lambda p: p["y"]):
            first.setdefault(p["src"], p)
        pts = [((p["x0"] + p["x1"]) / 2, p["y"]) for p in first.values()]
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx = pts[j][0] - pts[i][0]
                if abs(dx) > 50:
                    slopes.append((pts[j][1] - pts[i][1]) / dx)
    slope = sorted(slopes)[len(slopes) // 2] if slopes else 0.0
    if abs(slope) > 0.03:
        slope = 0.0
    x_ref = col0["bbox"][0]
    for p in pieces:
        p["y"] = p["y"] - slope * ((p["x0"] + p["x1"]) / 2 - x_ref)
    h = sorted(p["h"] for p in pieces)[len(pieces) // 2]
    tol = 0.6 * h
    if DEBUG: print("slope", slope, "tol", tol)

    def in_col0(p):
        cx = (p["x0"] + p["x1"]) / 2
        return col0["bbox"][0] - 2 <= cx <= col0["bbox"][2] + 2

    # 逻辑行起点：每个 B 行的首行（优先取项目名称列首片段）；另加项目名称列里以「*」或「合」开头、明显低于本行首行的片段
    anchors, row_anchor = [], {}
    for r, rp in by_row.items():
        real = [p for p in rp if not p["synthetic"]] or rp
        c0 = [p for p in real if in_col0(p)]
        y = min(p["y"] for p in c0) if c0 else min(p["y"] for p in real)
        row_anchor[r] = y
        anchors.append(y)
        for p in c0:
            if p["y"] > y + tol and NEW_ROW.match(p["text"]):
                anchors.append(p["y"])
    merged = []
    for y in sorted(anchors):
        if merged and y - merged[-1] <= tol:
            continue
        merged.append(y)
    if DEBUG:
        print("anchors", [round(a, 1) for a in merged], {r: round(y, 1) for r, y in row_anchor.items()})
        for p in pieces:
            print("   ", p["brow"], repr(p["text"]), round(p["y"], 1))
    lrows: dict[int, list] = {}
    for p in pieces:
        if p["synthetic"]:
            p["y"] = row_anchor[p["brow"]]
        # 按坐标归到 y 不晚于它的最近起点（可上可下：如 p20「Xsample52」+下一格顶端的「0」实为同一型号）
        k = max([i for i, a in enumerate(merged) if a <= p["y"] + tol], default=0)
        lrows.setdefault(k, []).append(p)

    def col_of(p):
        cx = (p["x0"] + p["x1"]) / 2
        inside = [c for c in head if c["bbox"][0] - 1 <= cx <= c["bbox"][2] + 1]
        if inside:
            return inside[0]
        return min(head, key=lambda c: min(abs(cx - c["bbox"][0]), abs(cx - c["bbox"][2])))

    new_rows = []
    for k in sorted(lrows):
        ps = lrows[k]
        cells = []
        y0 = min(p["y0"] for p in ps)
        y1 = max(p["y1"] for p in ps)
        for hc in sorted(head, key=lambda c: c["col"]):
            mine = sorted([p for p in ps if col_of(p) is hc], key=lambda p: (p["y"], p["x0"]))
            # 同一视觉行（y 差 ≤ tol）内按 x 排
            mine = _line_sort(mine, tol)
            txt = ""
            for i, p in enumerate(mine):
                if i and abs(p["y"] - mine[i - 1]["y"]) > tol:
                    txt += "\n"
                txt += p["text"]
            cells.append({"col": hc["col"], "colspan": hc.get("colspan", 1), "text": txt,
                          "bbox": [hc["bbox"][0], y0, hc["bbox"][2], y1]})
        new_rows.append(cells)
    old_txt = "".join(c["text"] for r in range(hdr + 1, end + 1) for c in rows.get(r) or [])
    new_txt = "".join(c["text"] for r in new_rows for c in r)
    assert Counter(norm(old_txt)) == Counter(norm(new_txt)), "发票重排不应增删字"
    # 拼新表：表头及以上不变；明细区换成 new_rows；其后各行顺延
    out_cells = []
    for r, cs in rows.items():
        if r <= hdr:
            out_cells += [dict(c) for c in cs]
    r_out = hdr + 1
    for cells in new_rows:
        for c in cells:
            out_cells.append({"row": r_out, "col": c["col"], "rowspan": 1, "colspan": c["colspan"], "text": c["text"], "bbox": c["bbox"]})
        r_out += 1
    shift = r_out - (end + 1)
    for r, cs in rows.items():
        if r > end:
            for c in cs:
                out_cells.append({**c, "row": c["row"] + shift})
    new_tab = {**tab, "cells": out_cells, "n_rows": tab["n_rows"] + shift}
    info = {"header_row": hdr, "region_rows": [hdr + 1, end], "new_rows": len(new_rows),
            "before": ["".join(c["text"] for c in rows.get(r) or []) for r in range(hdr + 1, end + 1)],
            "after": [" | ".join(c["text"].replace("\n", "/") for c in r) for r in new_rows]}
    return new_tab, info


def apply(doc: dict, cfg: dict, log: list, aux: dict) -> int:
    n = 0
    for p in sorted({b["page"] for b in doc["blocks"]}):
        bl = [b for b in doc["blocks"] if b["page"] == p]
        if not is_invoice_page(bl):
            continue
        a_chars = (aux["pages"].get(p) or {}).get("a_chars") or []
        for b in bl:
            if b["type"] != "table" or b.get("verification_status") == "rejected":
                continue
            res = reorder_table(b["table"], a_chars)
            if res is None:
                continue
            new_tab, info = res
            if [c["text"] for c in sorted(new_tab["cells"], key=lambda c: (c["row"], c["col"]))] == \
               [c["text"] for c in sorted(b["table"]["cells"], key=lambda c: (c["row"], c["col"]))]:
                continue
            b["table"] = new_tab
            log.append({"step": "invoice_reorder", "page": p, "block_id": b["id"], **info})
            n += 1
    return n
