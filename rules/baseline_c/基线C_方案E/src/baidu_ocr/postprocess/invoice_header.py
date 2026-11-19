"""发票表头排序（f1_round2 变体 N1；通用规则，不看参照、不按页号/具体字符写死）。

发票页 = 同页有块文字含「发票号码」或「开票日期」且有表格块。表头带 = 第一个表格块上沿以上的正文类块（非印章、非图片）。
两种做法（二选一，分别计分）：
  fields  固定字段顺序：发票标题（含「发票」、不含冒号和数字的块，取字最高的那个）→ 含「发票号码」的块 → 含「开票日期」的块
          → 表头带里其它块按原顺序。
  columns 按坐标分栏再从上到下：每个块的横向范围用块内 A 字框的范围（没有 A 字时用 B 块框）；横向范围有重叠的块并成一栏
          （传递闭包），栏按左边界从左到右，栏内按上沿从上到下。
只改表头带内块的先后（reading_order 在这些块原有的序号里重新分配），不改文字。
"""
from __future__ import annotations

import re

TYPES = ("paragraph", "title", "header", "list")


def _a_extent(b, aux):
    pa = (aux or {}).get("pages", {}).get(b["page"]) or {}
    x0, y0, x1, y1 = b["bbox"]
    cs = [c["bbox"] for c in pa.get("a_chars", []) if x0 - 1 <= (c["bbox"][0] + c["bbox"][2]) / 2 <= x1 + 1 and y0 - 1 <= (c["bbox"][1] + c["bbox"][3]) / 2 <= y1 + 1]
    if not cs:
        return x0, x1
    return min(c[0] for c in cs), max(c[2] for c in cs)


def header_band(doc, p):
    bl = [b for b in doc["blocks"] if b["page"] == p and b.get("verification_status") != "rejected"]
    tabs = [b for b in bl if b["type"] == "table"]
    if not tabs or not any(re.search("发票号码|开票日期", b.get("text") or "") for b in bl if b["type"] in TYPES):
        return []
    top = min(t["bbox"][1] for t in tabs)
    return [b for b in bl if b["type"] in TYPES and b["bbox"][3] <= top + 2 and (b.get("text") or "").strip()]


def order_fields(band):
    def is_title(b):
        t = b["text"]
        return "发票" in t and not re.search(r"[:：]|\d", t)
    titles = sorted([b for b in band if is_title(b)], key=lambda b: -(b["bbox"][3] - b["bbox"][1]))
    first = titles[:1]
    no = [b for b in band if b not in first and "发票号码" in b["text"]]
    dt = [b for b in band if b not in first + no and "开票日期" in b["text"]]
    rest = sorted([b for b in band if b not in first + no + dt], key=lambda b: b["reading_order"])
    return first + no + dt + rest


def order_columns(band, aux):
    ext = {b["id"]: _a_extent(b, aux) for b in band}
    cols = []
    for b in sorted(band, key=lambda b: ext[b["id"]][0]):
        x0, x1 = ext[b["id"]]
        hit = [c for c in cols if x0 < c["x1"] and c["x0"] < x1]
        if hit:
            c = hit[0]
            for o in hit[1:]:
                c["bl"] += o["bl"]
                c["x0"], c["x1"] = min(c["x0"], o["x0"]), max(c["x1"], o["x1"])
                cols.remove(o)
            c["bl"].append(b)
            c["x0"], c["x1"] = min(c["x0"], x0), max(c["x1"], x1)
        else:
            cols.append({"x0": x0, "x1": x1, "bl": [b]})
    out = []
    for c in sorted(cols, key=lambda c: c["x0"]):
        out += sorted(c["bl"], key=lambda b: b["bbox"][1])
    return out


def apply(doc, log, aux=None, mode="columns"):
    n = 0
    for pg in doc["pages"]:
        p = pg["page_number"]
        band = header_band(doc, p)
        if len(band) < 2:
            continue
        new = order_fields(band) if mode == "fields" else order_columns(band, aux)
        old = sorted(band, key=lambda b: b["reading_order"])
        if [b["id"] for b in new] == [b["id"] for b in old]:
            continue
        ros = [b["reading_order"] for b in old]
        for b, ro in zip(new, ros):
            b["reading_order"] = ro
        log.append({"step": "invoice_header", "mode": mode, "page": p, "before": [b["text"][:16] for b in old], "after": [b["text"][:16] for b in new]})
        n += 1
    return n
