"""按坐标重排阅读顺序：分栏 + 先上后下、先左后右；页眉页脚不插入正文中间。"""

from __future__ import annotations

import re

PAGE_NO_RE = re.compile(
    r"^\s*(?:[-—–]?\s*\d+\s*[-—–]?|第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?)\s*$"
)


def _bbox(b: dict) -> list[float]:
    box = b.get("bbox")
    if isinstance(box, dict):
        return list(box.get("pdf") or box.get("pixel") or [0, 0, 0, 0])
    return list(box or [0, 0, 0, 0])


def assign_reading_order(blocks: list[dict], page_width: float) -> list[dict]:
    body, heads, foots = [], [], []
    for b in blocks:
        typ = b.get("type") or b.get("block_type")
        text = (b.get("text") or "").strip()
        if typ == "header":
            heads.append(b)
        elif typ in {"footer", "page_number"} or (typ == "footer") or PAGE_NO_RE.match(text):
            foots.append(b)
        else:
            body.append(b)

    def yx(b):
        box = _bbox(b)
        return (box[1], box[0])

    heads.sort(key=yx)
    foots.sort(key=yx)

    def _gutter(items: list[dict]) -> float | None:
        narrow = [b for b in items if (_bbox(b)[2] - _bbox(b)[0]) < 0.55 * page_width]
        if len(narrow) < 4:
            return None
        mids = sorted((_bbox(b)[0] + _bbox(b)[2]) / 2 for b in narrow)
        gaps = [(mids[i + 1] - mids[i], (mids[i] + mids[i + 1]) / 2) for i in range(len(mids) - 1)]
        if not gaps:
            return None
        best_gap, split = max(gaps, key=lambda g: g[0])
        if best_gap < page_width * 0.08:
            return None
        if not (0.28 * page_width <= split <= 0.72 * page_width):
            return None
        left = [b for b in narrow if (_bbox(b)[0] + _bbox(b)[2]) / 2 < split and _bbox(b)[2] <= split + page_width * 0.03]
        right = [b for b in narrow if (_bbox(b)[0] + _bbox(b)[2]) / 2 >= split and _bbox(b)[0] >= split - page_width * 0.03]
        if len(left) < 2 or len(right) < 2:
            return None
        return split

    def _order_body(items: list[dict]) -> list[dict]:
        split = _gutter(items)
        if split is None:
            return sorted(items, key=yx)
        full, left, right = [], [], []
        for b in items:
            box = _bbox(b)
            mid = (box[0] + box[2]) / 2
            width = box[2] - box[0]
            if width >= 0.55 * page_width or (box[0] < split < box[2] and width >= 0.4 * page_width):
                full.append(b)
            elif mid < split:
                left.append(b)
            else:
                right.append(b)
        # 通栏块按 y 切断分栏
        full.sort(key=yx)
        left.sort(key=yx)
        right.sort(key=yx)
        if not full:
            return left + right
        ordered = []
        bands = []
        curs = []
        y_cuts = [(_bbox(b)[1], "full", b) for b in full]
        # 用通栏 y 把左右栏分段
        cuts = [(_bbox(b)[1] + _bbox(b)[3]) / 2 for b in full]
        cuts = [ -1e9 ] + cuts + [ 1e9 ]
        # 更简单：按「通栏前的左右 + 通栏」交替
        fi = 0
        li = 0
        ri = 0
        while True:
            next_full_y = _bbox(full[fi])[1] if fi < len(full) else None
            # 先输出 y 小于下一通栏的左栏再右栏
            def take_side(side, idx):
                out = []
                while idx < len(side) and (next_full_y is None or _bbox(side[idx])[1] < next_full_y):
                    out.append(side[idx])
                    idx += 1
                return out, idx
            lpart, li = take_side(left, li)
            rpart, ri = take_side(right, ri)
            ordered.extend(lpart)
            ordered.extend(rpart)
            if fi < len(full):
                ordered.append(full[fi])
                fi += 1
            else:
                break
        return ordered

    ordered = heads + _order_body(body) + foots
    # 页眉从 0 起，正文接着编，页脚用较大序号避免插到中间语义上仍在末尾
    for i, b in enumerate(ordered):
        b["reading_order"] = i
    return ordered


def sort_lines_reading_order(lines: list[dict], page_width: float) -> list[dict]:
    """对百度 words_result 行做分栏重排。bbox 为 [l,t,r,b] 像素或 pt，单位一致即可。"""
    items = []
    for i, ln in enumerate(lines):
        loc = ln.get("location") or {}
        if loc:
            bbox = [
                float(loc.get("left") or 0),
                float(loc.get("top") or 0),
                float(loc.get("left") or 0) + float(loc.get("width") or 0),
                float(loc.get("top") or 0) + float(loc.get("height") or 0),
            ]
        else:
            bbox = ln.get("bbox") or [0, 0, 0, 0]
            if isinstance(bbox, dict):
                bbox = list(bbox.get("pixel") or bbox.get("pdf") or [0, 0, 0, 0])
        items.append({**ln, "_bbox": bbox, "_i": i})

    def yx(it):
        b = it["_bbox"]
        return (b[1], b[0])

    # 复用分栏：构造临时块
    tmp = [{"bbox": it["_bbox"], "type": "paragraph", "text": it.get("words") or it.get("text") or ""} for it in items]
    # 建立 bbox 到原行的映射（可能重复，用顺序消耗）
    assign_reading_order(tmp, page_width)
    # assign 就地写 reading_order；按它排序原 items
    # tmp 与 items 是不同对象。重新按相同算法排 items。
    wrapped = [{"bbox": it["_bbox"], "type": "paragraph", "orig": it} for it in items]
    assign_reading_order(wrapped, page_width)
    wrapped.sort(key=lambda x: x.get("reading_order", 0))
    return [w["orig"] for w in wrapped]
