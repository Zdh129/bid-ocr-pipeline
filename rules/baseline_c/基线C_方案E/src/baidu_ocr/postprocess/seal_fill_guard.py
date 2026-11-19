"""补漏防章（f1_round2 变体 N3；通用规则，不看参照、不按页号/具体字符写死）。

补漏步骤（image_fill 图片区补字、fill_gaps 补漏字）从 A/U 取字之前，先把落在印章区域里的 A 字框和 U 行去掉，
这样补进来的行/字里不会带印章内的字。印章区域 = 同页 B 识别出的印章块框 ∪ 原图 A 路由阶段按红色像素找出的印章框
（call_summary.json 的 route.seal_boxes，200 DPI 像素，换算成 pt）。判定：字框/行框中心落在任一印章框内。
只影响补漏用的辅助数据；B 原有文字、发票重排、二次识别都用原辅助数据。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

PX_PER_PT = 200 / 72.0


def seal_boxes(doc: dict, a_orig_summary: Path | None) -> dict[int, list]:
    out: dict[int, list] = {}
    for b in doc["blocks"]:
        if b["type"] == "seal" and b.get("bbox"):
            out.setdefault(b["page"], []).append(list(b["bbox"]))
    if a_orig_summary and Path(a_orig_summary).exists():
        for pg in json.loads(Path(a_orig_summary).read_text(encoding="utf-8"))["pages"]:
            for bx in pg["route"].get("seal_boxes") or []:
                out.setdefault(pg["page"], []).append([v / PX_PER_PT for v in bx])
    return out


def _in(bb, boxes):
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in boxes)


def filter_aux(aux: dict, boxes: dict[int, list], log: list) -> dict:
    a2 = copy.copy(aux)
    a2["pages"] = {}
    for p, pg in aux["pages"].items():
        bx = boxes.get(p) or []
        if not bx:
            a2["pages"][p] = pg
            continue
        ac = [c for c in pg["a_chars"] if not _in(c["bbox"], bx)]
        ui = [u for u in pg["u_items"] if u.get("kind") != "text" or not _in(u["bbox"], bx)]
        if len(ac) != len(pg["a_chars"]) or len(ui) != len(pg["u_items"]):
            log.append({"step": "seal_fill_guard", "page": p, "a_chars_dropped": len(pg["a_chars"]) - len(ac),
                        "u_lines_dropped": len(pg["u_items"]) - len(ui),
                        "dropped_text": "".join(c["ch"] for c in pg["a_chars"] if _in(c["bbox"], bx))[:80]})
        a2["pages"][p] = {**pg, "a_chars": ac, "u_items": ui}
    return a2
