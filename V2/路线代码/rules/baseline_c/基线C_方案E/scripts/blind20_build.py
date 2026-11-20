#!/usr/bin/env python3
"""离线构建：把方案 A / B 的原始返回转成生成侧中间 JSON（schema 1.0.0），并导出逐页预测文本（按阅读顺序，分正文/表格/印章）。

不发任何网络请求。
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2
import numpy as np

from baidu_ocr.blind20_local import line_masks, ink_mask, red_mask
from baidu_ocr.coords import CoordMapper
from baidu_ocr.serialize import build_document, validate_schema

OUT = ROOT / "output"
SCHEMA = ROOT / "schemas" / "intermediate.schema.json"
DPI = 200.0
SRC_NAME = "blind20-scan.pdf"


def wj(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def mapper_for(pg):
    w_pdf, h_pdf = float(pg["width_pdf"]), float(pg["height_pdf"])
    return CoordMapper(page_x0=0.0, page_y0=0.0, page_x1=w_pdf, page_y1=h_pdf, zoom=DPI / 72.0,
                       width_px=int(pg["width_px"]), height_px=int(pg["height_px"]), dpi=DPI)


# ---------------------------------------------------------------- 方案 A
def loc_box(loc):
    return [loc["left"], loc["top"], loc["left"] + loc["width"], loc["top"] + loc["height"]]


def table_cells(img, region):
    """表格区域内由线围成的连通域即单元格（容忍倾斜与合并单元格）。返回 (label_map, cells)。"""
    x0, y0, x1, y1 = region
    ink = ink_mask(img, thr=185)
    hm, vm = line_masks(ink)
    lines = ((hm | vm) > 0).astype(np.uint8)[y0:y1, x0:x1].copy()
    lines = cv2.dilate(lines, np.ones((5, 5), np.uint8))
    cv2.rectangle(lines, (0, 0), (lines.shape[1] - 1, lines.shape[0] - 1), 1, 3)
    free = (1 - lines).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(free, 4)
    cells = []
    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        if a < 400 or w < 15 or h < 15:
            continue
        keep[i] = True
        cells.append({"label": i, "px": [x + x0, y + y0, x + w + x0, y + h + y0]})
    if not cells:
        return None, []

    def cluster(vals, tol=18):
        vals = sorted(vals)
        groups = [[vals[0]]]
        for v in vals[1:]:
            if v - groups[-1][-1] <= tol:
                groups[-1].append(v)
            else:
                groups.append([v])
        return [float(np.mean(g)) for g in groups]

    ys = cluster([c["px"][1] for c in cells] + [c["px"][3] for c in cells])
    xs = cluster([c["px"][0] for c in cells] + [c["px"][2] for c in cells])

    def idx(v, arr):
        return int(np.argmin([abs(v - a) for a in arr]))

    for c in cells:
        r0, r1 = idx(c["px"][1], ys), idx(c["px"][3], ys)
        c0, c1 = idx(c["px"][0], xs), idx(c["px"][2], xs)
        c["row"], c["col"] = r0, c0
        c["rowspan"], c["colspan"] = max(1, r1 - r0), max(1, c1 - c0)
    lab_full = np.zeros(img.shape[:2], np.int32)
    lab_sub = np.where(keep[lab], lab, 0)
    lab_full[y0:y1, x0:x1] = lab_sub
    # 行列号压缩成连续编号
    rows = sorted({c["row"] for c in cells})
    cols = sorted({c["col"] for c in cells})
    for c in cells:
        c["row"] = rows.index(c["row"])
        c["col"] = cols.index(c["col"])
    return lab_full, cells


def line_sort(items, key_box):
    """按行聚类后从左到右。表格永远单独成行（不与文字行合并）。"""
    return [it for row in group_rows(items, key_box) for it in row]


def group_rows(items, key_box):
    items = sorted(items, key=lambda it: (key_box(it)[1] + key_box(it)[3]) / 2)
    lines = []
    for it in items:
        b = key_box(it)
        cy, h = (b[1] + b[3]) / 2, max(1, b[3] - b[1])
        is_tab = it.get("kind") == "table"
        if lines and not is_tab and not lines[-1]["table"]:
            lb = lines[-1]
            if abs(cy - lb["cy"]) <= 0.5 * min(h, lb["h"]):
                lb["items"].append(it)
                lb["cy"] = (lb["cy"] * (len(lb["items"]) - 1) + cy) / len(lb["items"])
                continue
        lines.append({"cy": cy, "h": h, "items": [it], "table": is_tab})
    return [sorted(ln["items"], key=lambda it: key_box(it)[0]) for ln in lines]


def reading_order(items, page_w):
    """单栏为主。检测「左右并排两栏」的连续行区段（例如签署页甲方/乙方并排），区段内先左栏后右栏。"""
    def box(it):
        return it["px"]
    if not items:
        return []
    rows = group_rows(items, box)
    best = None
    for m in np.linspace(0.3 * page_w, 0.7 * page_w, 41):
        # 找最长的连续兼容行段（无项目跨越 m，且无表格）
        i = 0
        while i < len(rows):
            j = i
            both = 0
            while j < len(rows):
                r = rows[j]
                if any(it.get("kind") == "table" for it in r) or any(box(it)[0] < m < box(it)[2] for it in r):
                    break
                if any(box(it)[2] <= m for it in r) and any(box(it)[0] >= m for it in r):
                    both += 1
                j += 1
            # 两栏都要像「正文」：平均字数 >= 4（避免把「名称 …… 数量」这种键值清单当成两栏）
            seg_items = [it for r in rows[i:j] for it in r]
            L = [it for it in seg_items if box(it)[2] <= m]
            R = [it for it in seg_items if box(it)[0] >= m]
            avg = lambda xs: (sum(len(it.get("text") or "") for it in xs) / len(xs)) if xs else 0
            if both >= 3 and avg(L) >= 4 and avg(R) >= 4 and (best is None or both > best[0]):
                best = (both, i, j, m)
            i = max(j, i + 1)
    if best is None:
        return [it for r in rows for it in r]
    _, i, j, m = best
    out = [it for r in rows[:i] for it in r]
    seg = rows[i:j]
    out += [it for r in seg for it in r if box(it)[2] <= m or (box(it)[0] + box(it)[2]) / 2 < m]
    out += [it for r in seg for it in r if not (box(it)[2] <= m or (box(it)[0] + box(it)[2]) / 2 < m)]
    out += [it for r in rows[j:] for it in r]
    return out


def build_scheme_a(src_dir: Path, out_dir: Path, label: str):
    summ = json.loads((src_dir / "call_summary.json").read_text(encoding="utf-8"))
    pages_meta, blocks_by_page, pred_pages, routing = [], {}, {}, []
    for pg in summ["pages"]:
        p = pg["page"]
        img = cv2.imread(pg["image_ref"])
        H, W = img.shape[:2]
        mp = mapper_for(pg)
        api = pg["route"]["api"]
        raw = json.loads((src_dir / "raw" / f"p{p:02d}_main_{api}.json").read_text(encoding="utf-8"))
        words = list(raw.get("words_result") or [])
        rpath = src_dir / "raw" / f"p{p:02d}_retry_mapped.json"
        n_retry_dup = 0
        if rpath.exists():
            # 去重：重试补回的结果若与首轮已有结果框重叠 >40%，视为重复，不再加入
            main_mask = np.zeros((int(pg["height_px"]), int(pg["width_px"])), np.uint8)
            for w0 in words:
                l0 = w0["location"]
                main_mask[max(0, l0["top"]):l0["top"] + l0["height"], max(0, l0["left"]):l0["left"] + l0["width"]] = 1
            for rw in json.loads(rpath.read_text(encoding="utf-8")):
                l1 = rw["location"]
                sub = main_mask[max(0, l1["top"]):l1["top"] + l1["height"], max(0, l1["left"]):l1["left"] + l1["width"]]
                if sub.size and sub.mean() > 0.4:
                    n_retry_dup += 1
                    continue
                words.append(rw)
        rm = red_mask(img) > 0
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        seal_boxes = pg["route"]["seal_boxes"]
        # 表格
        tables = []
        for t in pg["route"]["tables"]:
            lab, cells = table_cells(img, t["bbox"])
            if cells:
                tables.append({"region": t["bbox"], "lab": lab, "cells": cells, "chars": {c["label"]: [] for c in cells}})
        items = []
        seal_words = []
        for wi, w in enumerate(words):
            box = loc_box(w["location"])
            x0, y0, x1, y1 = (max(0, int(v)) for v in box)
            sub = (slice(y0, max(y0 + 1, y1)), slice(x0, max(x0 + 1, x1)))
            dark = (gray[sub] < 170) | rm[sub]
            red = rm[sub] & dark
            fr = red.sum() / dark.sum() if dark.sum() else 0
            sat = float(np.median(hsv[sub][red][:, 1])) if red.sum() else 0
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            in_seal = any(sb[0] <= cx <= sb[2] and sb[1] <= cy <= sb[3] for sb in seal_boxes)
            edge = cx < 0.07 * W or cx > 0.93 * W
            if fr >= 0.6 and sat >= 130 and (in_seal or edge):
                seal_words.append({"text": w["words"], "px": box, "conf": (w.get("probability") or {}).get("average")})
                continue
            chars = w.get("chars") or [{"char": w["words"], "location": w["location"]}]
            outside = []
            for ch in chars:
                cb = loc_box(ch["location"])
                ccx, ccy = int((cb[0] + cb[2]) / 2), int((cb[1] + cb[3]) / 2)
                hit = None
                for ti, t in enumerate(tables):
                    rx0, ry0, rx1, ry1 = t["region"]
                    if rx0 <= ccx < rx1 and ry0 <= ccy < ry1:
                        l = int(t["lab"][min(H - 1, ccy), min(W - 1, ccx)])
                        if l == 0:
                            win = t["lab"][max(0, ccy - 9):ccy + 10, max(0, ccx - 9):ccx + 10]
                            vals = win[win > 0]
                            if len(vals):
                                l = int(np.bincount(vals).argmax())
                        if l and l in t["chars"]:
                            hit = (ti, l)
                        break
                if hit:
                    tables[hit[0]]["chars"][hit[1]].append({"ch": ch["char"], "px": cb, "w": wi})
                else:
                    outside.append({"ch": ch["char"], "px": cb})
            # 表外字符按原行连续段还原
            if outside:
                if len(outside) == len(chars):
                    items.append({"kind": "line", "text": w["words"], "px": box,
                                  "conf": (w.get("probability") or {}).get("average"), "retry": bool(w.get("_from_retry"))})
                else:
                    xs0 = min(c["px"][0] for c in outside); ys0 = min(c["px"][1] for c in outside)
                    xs1 = max(c["px"][2] for c in outside); ys1 = max(c["px"][3] for c in outside)
                    items.append({"kind": "line", "text": "".join(c["ch"] for c in outside), "px": [xs0, ys0, xs1, ys1],
                                  "conf": (w.get("probability") or {}).get("average"), "retry": bool(w.get("_from_retry"))})
        for ti, t in enumerate(tables):
            cells_out = []
            for c in t["cells"]:
                chs = t["chars"][c["label"]]
                # 单元格内：按字所在行（字框中心 y）聚类，再按 x
                ordered = line_sort(chs, lambda it: it["px"])
                txt = "".join(it["ch"] for it in ordered)
                cells_out.append({"row": c["row"], "col": c["col"], "rowspan": c["rowspan"], "colspan": c["colspan"],
                                  "text": txt, "bbox": mp.pixel_to_pdf(c["px"]), "px": c["px"]})
            cells_out.sort(key=lambda c: (c["row"], c["col"]))
            n_rows = max(c["row"] + c["rowspan"] for c in cells_out)
            n_cols = max(c["col"] + c["colspan"] for c in cells_out)
            items.append({"kind": "table", "px": t["region"], "table": {"n_rows": n_rows, "n_cols": n_cols,
                          "cells": [{k: v for k, v in c.items() if k != "px"} for c in cells_out]},
                          "text": "\n".join(" | ".join(c["text"] for c in cells_out if c["row"] == r) for r in range(n_rows))})
        ordered = reading_order(items, W)
        blocks = []
        sections = []
        for i, it in enumerate(ordered, 1):
            if it["kind"] == "table":
                blocks.append({"id": f"b-{p:03d}-{i:03d}", "block_type": "table", "bbox": {"pixel": it["px"]},
                               "text": "", "confidence": 0.95, "source": "ocr", "table": it["table"], "table_bbox_unit": "pt"})
                rows = {}
                for c in it["table"]["cells"]:
                    rows.setdefault(c["row"], []).append(c["text"])
                sections.append({"section": "table", "text": "\n".join(" ".join(v) for _, v in sorted(rows.items()))})
            else:
                blk = {"id": f"b-{p:03d}-{i:03d}", "block_type": "text", "bbox": {"pixel": it["px"]},
                       "text": it["text"], "confidence": it.get("conf") or 0.9, "source": "ocr"}
                if it.get("retry"):
                    blk["warnings"] = ["recovered_by_crop_retry"]
                blocks.append(blk)
                sections.append({"section": "body", "text": it["text"]})
        # 印章块（红色字，位于印章框内或骑缝处）
        for k, sb in enumerate(seal_boxes, 1):
            txt = "".join(s["text"] for s in seal_words if sb[0] <= (s["px"][0] + s["px"][2]) / 2 <= sb[2] and sb[1] <= (s["px"][1] + s["px"][3]) / 2 <= sb[3])
            blocks.append({"id": f"b-{p:03d}-s{k:02d}", "block_type": "seal", "bbox": {"pixel": sb}, "text": txt,
                           "confidence": 0.5 if txt else 0.0, "source": "ocr", "verification_status": "needs_review"})
        stray = [s for s in seal_words if not any(sb[0] <= (s["px"][0] + s["px"][2]) / 2 <= sb[2] and sb[1] <= (s["px"][1] + s["px"][3]) / 2 <= sb[3] for sb in seal_boxes)]
        for k, s in enumerate(stray, 1):
            blocks.append({"id": f"b-{p:03d}-se{k:02d}", "block_type": "seal", "bbox": {"pixel": s["px"]}, "text": s["text"],
                           "confidence": 0.5, "source": "ocr", "verification_status": "needs_review", "warnings": ["riding_seal_fragment"]})
        seal_text = "\n".join(b["text"] for b in blocks if b["block_type"] == "seal" and b["text"])
        # 覆盖缺口：显式标出
        for k, g in enumerate(pg["coverage"]["still_missing"], 1):
            x0, y0, x1, y1 = g["bbox"]
            crop = img[y0:y1, x0:x1]
            red_fr = float((red_mask(crop) > 0).mean()) if crop.size else 0
            dark_fr = float((cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) < 120).mean()) if crop.size else 0
            w, h = x1 - x0, y1 - y0
            if red_fr > 0.03 and dark_fr < 0.2:
                gcls = "seal_fragment"
            elif dark_fr > 0.25 and 0.6 < w / max(1, h) < 2.6 and min(w, h) > 30:
                gcls = "qr_code"
            else:
                gcls = "text_or_other"
            g["gap_class"] = gcls
            blocks.append({"id": f"b-{p:03d}-g{k:02d}", "block_type": "text", "bbox": {"pixel": g["bbox"]}, "text": "",
                           "confidence": 0.0, "source": "coverage_gap", "coverage_gap": True, "verification_status": "needs_review",
                           "gap_reason": f"no_ocr_output_after_retry:{gcls}", "warnings": [f"coverage_gap:{gcls}"]})
        blocks_by_page[p] = blocks
        pages_meta.append({"page": p, "width_pdf": pg["width_pdf"], "height_pdf": pg["height_pdf"], "width_px": pg["width_px"],
                           "height_px": pg["height_px"], "rotation": 0, "mapper": mp, "image_ref": pg["image_ref"]})
        pred_pages[p] = {"sections": sections, "seal": seal_text}
        routing.append({"page": p, **{k: pg["route"][k] for k in ("page_class", "api", "reasons")},
                        "coverage": {"expected_line_boxes": pg["coverage"]["expected_line_boxes"],
                                     "covered_first_pass": pg["coverage"]["covered_first_pass"],
                                     "missing_first_pass": pg["coverage"]["missing_first_pass"],
                                     "retry": pg["coverage"]["retry"],
                                     "still_missing": [{"bbox": g["bbox"], "gap_class": g.get("gap_class"), "covered_frac": g["covered_frac"]} for g in pg["coverage"]["still_missing"]],
                                     "seal_region_boxes_excluded": pg["coverage"]["seal_region_boxes_excluded"],
                                     "retry_words_dropped_as_duplicate": n_retry_dup},
                        "timing_sec": pg["timing_sec"]})
    doc = build_document(source_file=SRC_NAME, pages_meta=pages_meta, blocks_by_page=blocks_by_page, dpi=DPI,
                         source_type="pdf", data_label="production", assets_dir=out_dir / "assets",
                         engine_notes=f"scheme=A({label}) baidu general/accurate 含位置版 recognize_granularity=small; 表格按线框连通域重建单元格; 覆盖缺口显式标出")
    fix_order(doc, blocks_by_page)
    finish(doc, out_dir, pred_pages, {"routing": routing})


def fix_order(doc, blocks_by_page):
    """build_document 会按自己的规则重排；这里恢复我们计算的阅读顺序（印章与缺口块排在最后）。"""
    order = {}
    for p, blocks in blocks_by_page.items():
        for i, b in enumerate(blocks):
            order[b["id"]] = i
    for b in doc["blocks"]:
        if b["id"] in order:
            b["reading_order"] = order[b["id"]]
    doc["blocks"].sort(key=lambda b: (b["page"], b.get("reading_order", 0)))


MSG = {
    "coverage_gap": "本地检测到的行框在百度返回里没有对应输出（重试后仍缺），需人工复核",
    "riding_seal_fragment": "骑缝章残片，红色文字，归入印章",
    "recovered_by_crop_retry": "首轮缺失，经裁剪拼图送高精度版重试后补回",
}


def finish(doc, out_dir, pred_pages, extra):
    for b in doc["blocks"]:
        ws = []
        seen = set()
        for w in b.get("warnings") or []:
            if isinstance(w, dict):
                ws.append(w)
                continue
            if w in seen:
                continue
            seen.add(w)
            code = w.split(":")[0]
            if code == "coverage_gap" and ":" not in w and any(isinstance(x, str) and x.startswith("coverage_gap:") for x in b["warnings"]):
                continue
            ws.append({"code": code, "message": MSG.get(code, w) + (f"（{w.split(':', 1)[1]}）" if ":" in w else ""),
                       "page": b["page"], "block_id": b["id"], "severity": "重要" if code == "coverage_gap" else "提示"})
        if ws:
            b["warnings"] = ws
    errs = validate_schema(doc, SCHEMA)
    wj(out_dir / "blind20-scan.json", doc)
    wj(out_dir / "blind20-scan.schema_errors.json", errs)
    wj(out_dir / "pred_pages.json", {str(k): v for k, v in sorted(pred_pages.items())})
    wj(out_dir / "build_meta.json", extra)
    print(out_dir.name, "blocks", len(doc["blocks"]), "schema_errors", len(errs), (errs[:3] if errs else ""))


# ---------------------------------------------------------------- 方案 B
def unesc(t: str) -> str:
    """百度文档解析的单元格文字里有字面量反斜杠 n（转义残留），按换行处理。"""
    return t.replace("\\n", "\n")


class _HT(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cur, self.cell, self.in_cell = [], None, None, False
        self.span = (1, 1)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self.cur = []
        elif tag in ("td", "th"):
            self.in_cell, self.cell = True, []
            self.span = (int(a.get("rowspan") or 1), int(a.get("colspan") or 1))
        elif tag == "br" and self.in_cell:
            self.cell.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cur is not None:
            self.cur.append({"text": "".join(self.cell).strip(), "rowspan": self.span[0], "colspan": self.span[1]})
            self.in_cell = False
        elif tag == "tr" and self.cur is not None:
            self.rows.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


def html_table_cells(html):
    pr = _HT()
    pr.feed(html)
    occ = {}
    cells = []
    for r, row in enumerate(pr.rows):
        c = 0
        for cell in row:
            while (r, c) in occ:
                c += 1
            cells.append({"row": r, "col": c, **cell})
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    occ[(r + dr, c + dc)] = True
            c += cell["colspan"]
    n_rows = max((c["row"] + c["rowspan"] for c in cells), default=0)
    n_cols = max((c["col"] + c["colspan"] for c in cells), default=0)
    return {"n_rows": n_rows, "n_cols": n_cols, "cells": cells}


def matrix_table_cells(t):
    raw = t.get("cells") or []
    mat = t.get("matrix") or []
    spans = {}
    for r, row in enumerate(mat):
        for c, idx in enumerate(row):
            s = spans.setdefault(int(idx), [r, c, r, c])
            s[0], s[1] = min(s[0], r), min(s[1], c)
            s[2], s[3] = max(s[2], r), max(s[3], c)
    cells = []
    for idx, (r0, c0, r1, c1) in sorted(spans.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        src = raw[idx] if idx < len(raw) else {}
        pos = src.get("position")
        bbox = [pos[0], pos[1], pos[0] + pos[2], pos[1] + pos[3]] if pos and len(pos) == 4 else None
        cells.append({"row": r0, "col": c0, "rowspan": r1 - r0 + 1, "colspan": c1 - c0 + 1, "text": unesc(src.get("text") or ""), "bbox": bbox})
    return {"n_rows": len(mat), "n_cols": max((len(r) for r in mat), default=0), "cells": cells}


def build_scheme_b(src_dir: Path, out_dir: Path, edition: str, a_summary: dict):
    d = json.loads((src_dir / "raw" / "blind20-scan.parse_result.json").read_text(encoding="utf-8"))
    rmeta = json.loads((src_dir / "blind20-scan.raw.json").read_text(encoding="utf-8"))
    pages_meta, blocks_by_page, pred_pages = [], {}, {}
    pg_by = {pg["page"]: pg for pg in a_summary["pages"]}
    TYPE = {"head_tail": "header", "header": "header", "footer": "footer", "page_number": "page_number", "title": "title",
            "text": "text", "table": "table", "seal": "seal", "image": "figure", "aside_text": "text", "figure": "figure",
            "contents": "text", "caption": "caption", "image_caption": "caption", "table_caption": "caption", "formula": "formula"}
    for page in d["pages"]:
        p = int(page["page_num"]) + 1
        pg = pg_by[p]
        mp = mapper_for(pg)
        meta = page.get("meta") or {}
        pw = float(meta.get("page_width") or pg["width_pdf"])
        sx = float(pg["width_pdf"]) / pw if pw else 1.0
        blocks, sections = [], []
        tables_by = {t.get("layout_id"): t for t in page.get("tables") or []}
        seals = list(page.get("seals") or [])
        seal_texts = []
        for i, lay in enumerate(page.get("layouts") or [], 1):
            typ = TYPE.get((lay.get("type") or "text").lower(), "text")
            pos = lay.get("position") or [0, 0, 1, 1]
            bb = [pos[0] * sx, pos[1] * sx, (pos[0] + pos[2]) * sx, (pos[1] + pos[3]) * sx]
            blk = {"id": f"b-{p:03d}-{i:03d}", "block_type": typ, "bbox": {"pdf": bb, "pixel": mp.pdf_to_pixel(bb)},
                   "text": unesc(lay.get("text") or ""), "confidence": 0.9, "source": "ocr"}
            if typ == "table":
                t = tables_by.get(lay.get("layout_id"))
                if t is not None and t.get("matrix"):
                    tab = matrix_table_cells(t)
                    for c in tab["cells"]:
                        if c.get("bbox"):
                            c["bbox"] = [v * sx for v in c["bbox"]]
                elif "<table" in (lay.get("text") or ""):
                    tab = html_table_cells(lay["text"])
                elif t is not None and t.get("table_html"):
                    tab = html_table_cells(t["table_html"])
                else:
                    tab = {"n_rows": 0, "n_cols": 0, "cells": []}
                blk["table"] = tab
                blk["table_bbox_unit"] = "pt"
                blk["text"] = ""
                rows = {}
                for c in tab["cells"]:
                    rows.setdefault(c["row"], []).append(c["text"])
                sections.append({"section": "table", "text": "\n".join(" ".join(v) for _, v in sorted(rows.items()))})
            elif typ == "seal":
                # 标准版：seals[] 里按位置找
                best = None
                for s in seals:
                    sp = s.get("position") or [0, 0, 0, 0]
                    if abs(sp[0] - pos[0]) < 3 and abs(sp[1] - pos[1]) < 3:
                        best = s
                if best is None and seals:
                    best = seals[0]
                if best is not None:
                    seals.remove(best)
                    txt = "".join([best.get("major_str") or ""] + list(best.get("minor_strs") or []))
                    blk["text"] = txt
                    seal_texts.append(txt)
                blk["verification_status"] = "needs_review"
            else:
                sections.append({"section": "body", "text": blk["text"]})
            blocks.append(blk)
        for k, s in enumerate(seals, 1):  # 未挂到 layout 的印章
            sp = s.get("position") or [0, 0, 1, 1]
            bb = [sp[0] * sx, sp[1] * sx, (sp[0] + sp[2]) * sx, (sp[1] + sp[3]) * sx]
            txt = "".join([s.get("major_str") or ""] + list(s.get("minor_strs") or []))
            seal_texts.append(txt)
            blocks.append({"id": f"b-{p:03d}-s{k:02d}", "block_type": "seal", "bbox": {"pdf": bb, "pixel": mp.pdf_to_pixel(bb)},
                           "text": txt, "confidence": 0.5, "source": "ocr", "verification_status": "needs_review"})
        blocks_by_page[p] = blocks
        pages_meta.append({"page": p, "width_pdf": pg["width_pdf"], "height_pdf": pg["height_pdf"], "width_px": pg["width_px"],
                           "height_px": pg["height_px"], "rotation": 0, "mapper": mp, "image_ref": pg["image_ref"]})
        pred_pages[p] = {"sections": sections, "seal": "\n".join(t for t in seal_texts if t)}
    doc = build_document(source_file=SRC_NAME, pages_meta=pages_meta, blocks_by_page=blocks_by_page, dpi=DPI,
                         source_type="pdf", data_label="production", assets_dir=out_dir / "assets",
                         engine_notes=f"scheme=B edition={edition} 百度文档解析整份提交; 阅读顺序取 layouts 原顺序")
    fix_order(doc, blocks_by_page)
    finish(doc, out_dir, pred_pages, {"edition": edition, "raw_meta": rmeta})


def main():
    a_sum = json.loads((OUT / "scheme_a" / "call_summary.json").read_text(encoding="utf-8"))
    build_scheme_a(OUT / "scheme_a", OUT / "final" / "scheme_a", "routed")
    build_scheme_a(OUT / "diag_all_general", OUT / "final" / "diag_all_general", "all_general_diag")
    build_scheme_b(OUT / "scheme_b_standard", OUT / "final" / "scheme_b_standard", "standard", a_sum)
    build_scheme_b(OUT / "scheme_b_unlimited", OUT / "final" / "scheme_b_unlimited", "unlimited", a_sum)


if __name__ == "__main__":
    main()
