"""根据单元格或文字坐标重建表格结构（cells + rowspan/colspan，尽量不重叠）。"""

from __future__ import annotations

from collections import defaultdict


def _bbox_of(item: dict) -> list[float] | None:
    loc = item.get("location")
    if loc:
        l, t = float(loc.get("left") or 0), float(loc.get("top") or 0)
        return [l, t, l + float(loc.get("width") or 0), t + float(loc.get("height") or 0)]
    box = item.get("bbox")
    if isinstance(box, dict):
        box = box.get("pixel") or box.get("pdf")
    if box and len(box) >= 4:
        return [float(v) for v in box[:4]]
    return None


def _cluster(values: list[float], gap: float) -> list[list[int]]:
    """把中心坐标聚成行或列。返回每簇的原始下标列表。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    clusters: list[list[int]] = []
    for i in order:
        if not clusters or values[i] - values[clusters[-1][-1]] > gap:
            # 与簇均值比
            if clusters:
                mean = sum(values[j] for j in clusters[-1]) / len(clusters[-1])
                if values[i] - mean <= gap:
                    clusters[-1].append(i)
                    continue
            clusters.append([i])
        else:
            mean = sum(values[j] for j in clusters[-1]) / len(clusters[-1])
            if values[i] - mean <= gap:
                clusters[-1].append(i)
            else:
                clusters.append([i])
    return clusters


def rebuild_table_from_words(words: list[dict], *, y_gap: float | None = None, x_gap: float | None = None) -> dict:
    """把带坐标的文字行聚成规则网格。"""
    items = []
    for w in words or []:
        bbox = _bbox_of(w)
        if not bbox:
            continue
        text = w.get("words") if w.get("words") is not None else (w.get("text") or "")
        items.append({"text": text, "bbox": bbox})
    if not items:
        return {"n_rows": 0, "n_cols": 0, "cells": []}

    heights = [it["bbox"][3] - it["bbox"][1] for it in items]
    widths = [it["bbox"][2] - it["bbox"][0] for it in items]
    med_h = sorted(heights)[len(heights) // 2]
    med_w = sorted(widths)[len(widths) // 2]
    y_gap = y_gap if y_gap is not None else max(6.0, med_h * 0.6)
    x_gap = x_gap if x_gap is not None else max(8.0, med_w * 0.5)

    y_centers = [(it["bbox"][1] + it["bbox"][3]) / 2 for it in items]
    x_centers = [(it["bbox"][0] + it["bbox"][2]) / 2 for it in items]
    rows = _cluster(y_centers, y_gap)
    # 列用所有 x 中心
    cols = _cluster(x_centers, x_gap)
    # 为每个 item 分配 row/col
    row_of = {}
    for r, idxs in enumerate(rows):
        for i in idxs:
            row_of[i] = r
    col_of = {}
    for c, idxs in enumerate(cols):
        for i in idxs:
            col_of[i] = c

    bucket: dict[tuple[int, int], list] = defaultdict(list)
    for i, it in enumerate(items):
        bucket[(row_of[i], col_of[i])].append(it)

    n_rows, n_cols = len(rows), len(cols)
    cells = []
    for r in range(n_rows):
        for c in range(n_cols):
            parts = bucket.get((r, c) or []) or bucket.get((r, c), [])
            parts = sorted(parts, key=lambda it: it["bbox"][0])
            text = " ".join((it["text"] or "").strip() for it in parts if (it["text"] or "").strip())
            if parts:
                xs0 = min(it["bbox"][0] for it in parts)
                ys0 = min(it["bbox"][1] for it in parts)
                xs1 = max(it["bbox"][2] for it in parts)
                ys1 = max(it["bbox"][3] for it in parts)
                bbox = [xs0, ys0, xs1, ys1]
            else:
                bbox = None
            cells.append(
                {
                    "row": r,
                    "col": c,
                    "rowspan": 1,
                    "colspan": 1,
                    "text": text,
                    "bbox": bbox,
                }
            )
    return {"n_rows": n_rows, "n_cols": n_cols, "header_row_count": 1 if n_rows else 0, "cells": cells}


def table_from_docparser(table: dict) -> dict:
    """把文档解析返回的 cells + matrix 转成中间格式 table。"""
    raw_cells = table.get("cells") or []
    matrix = table.get("matrix")
    if matrix:
        n_rows = len(matrix)
        n_cols = max((len(row) for row in matrix), default=0)
        # 统计每个 cell 索引的占位
        spans: dict[int, dict] = {}
        for r, row in enumerate(matrix):
            for c, idx in enumerate(row):
                if idx is None or idx < 0:
                    continue
                slot = spans.setdefault(int(idx), {"row": r, "col": c, "rowspan": 1, "colspan": 1, "max_r": r, "max_c": c})
                slot["row"] = min(slot["row"], r)
                slot["col"] = min(slot["col"], c)
                slot["max_r"] = max(slot["max_r"], r)
                slot["max_c"] = max(slot["max_c"], c)
        cells = []
        used = set()
        for r, row in enumerate(matrix):
            for c, idx in enumerate(row):
                if idx is None or int(idx) in used:
                    continue
                idx = int(idx)
                used.add(idx)
                sp = spans[idx]
                src = raw_cells[idx] if idx < len(raw_cells) else {}
                cells.append(
                    {
                        "row": sp["row"],
                        "col": sp["col"],
                        "rowspan": sp["max_r"] - sp["row"] + 1,
                        "colspan": sp["max_c"] - sp["col"] + 1,
                        "text": src.get("text") or "",
                        "bbox": _bbox_of(src),
                    }
                )
        return {"n_rows": n_rows, "n_cols": n_cols, "cells": cells}

    # 已经是 row/col 结构
    if raw_cells and "row" in (raw_cells[0] or {}):
        n_rows = max((int(c.get("row") or 0) + int(c.get("rowspan") or 1) for c in raw_cells), default=0)
        n_cols = max((int(c.get("col") or 0) + int(c.get("colspan") or 1) for c in raw_cells), default=0)
        cells = []
        for c in raw_cells:
            cells.append(
                {
                    "row": int(c.get("row") or 0),
                    "col": int(c.get("col") or 0),
                    "rowspan": int(c.get("rowspan") or 1),
                    "colspan": int(c.get("colspan") or 1),
                    "text": c.get("text") or "",
                    "bbox": _bbox_of(c),
                }
            )
        return {"n_rows": n_rows, "n_cols": n_cols, "cells": cells}

    return rebuild_table_from_words(raw_cells)
