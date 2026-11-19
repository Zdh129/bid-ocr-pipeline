"""把 VLM 结果合并回 B 标准版的中间 JSON（schema 1.0.0），并导出计分用的逐页文本 pred_pages。

合并规则：
- line：替换对应 B 块 text（VLM 输出为空则置空——视为 B 读出的是水印/印章/噪声）；
- missing_line：在同页按 y 插入新段落块（VLM 输出为空则不插入）；
- table_band：用 VLM 的 Markdown/HTML 表格替换该表对应行段（其他行保留 B 原值）；新单元格 bbox 为段内均分的近似值；
- seal：印章区域内第一个 B 印章块写入 VLM 文字，其余 B 印章块置空；只有本地检测、无 B 印章块的区域新增一个 seal 块（裁图作为资源）。
每个被改动的块都带 bbox（pt）、vlm 溯源（区域 id、模型、裁图路径与 sha256、裁图像素框、原文本）与 warning。
"""
from __future__ import annotations

import copy
import hashlib
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.M)


def clean_text(s: str) -> str:
    s = FENCE.sub("", s or "").strip()
    return s


def flatten_line(s: str) -> str:
    """多行 → 单行：CJK 之间直接连写，西文之间补一个空格。"""
    parts = [p.strip() for p in clean_text(s).splitlines() if p.strip()]
    out = ""
    for p in parts:
        if out and re.match(r"[A-Za-z0-9]", out[-1]) and re.match(r"[A-Za-z0-9]", p[0]):
            out += " "
        out += p
    return out


class _HT(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cur, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cur = []
        elif tag in ("td", "th"):
            self.cell = ""

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cur is not None and self.cell is not None:
            self.cur.append(self.cell.strip())
            self.cell = None
        elif tag == "tr" and self.cur is not None:
            self.rows.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data


SEP_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def parse_table(text: str) -> list[list[str]]:
    t = clean_text(text)
    if "<table" in t.lower() or "<tr" in t.lower():
        p = _HT()
        p.feed(t)
        if p.rows:
            return p.rows
    rows = []
    md = [ln for ln in t.splitlines() if ln.strip().startswith("|")]
    if md:
        for ln in t.splitlines():
            if not ln.strip():
                continue
            if not ln.strip().startswith("|"):
                # 表格内被模型拆到下一行的单元格续文：接到上一行最后一个单元格
                if rows:
                    rows[-1][-1] = (rows[-1][-1] + ln.strip().rstrip("|").strip())
                continue
            if SEP_ROW.match(ln):
                continue
            s = ln.strip()
            if s.startswith("|"):
                s = s[1:]
            if s.endswith("|"):
                s = s[:-1]
            rows.append([c.strip().replace("<br>", "").replace("<br/>", "") for c in s.split("|")])
        return rows
    return [[ln.strip()] for ln in t.splitlines() if ln.strip()]


def _sim(x: str, y: str) -> float:
    import difflib

    from .rules import norm
    x, y = norm(x), norm(y)
    if not x or not y:
        return 0.0
    return difflib.SequenceMatcher(None, x, y, autojunk=False).ratio()


def drop_bleed_rows(vrows: list[list[str]], rows: list[dict], a: int, b: int) -> list[list[str]]:
    """串行/补写表头去重：VLM 输出的某行若与段外任一 B 行相似度 ≥0.8，且高于与段内所有 B 行的相似度，
    视为相邻行露出或模型自行补写的表头，丢弃（只比较文本，不看参照）。"""
    inside = [rows[i]["text"] for i in range(a, b + 1)]
    outside = [rows[i]["text"] for i in range(len(rows)) if i < a or i > b]
    out = []
    for vr in vrows:
        t = "".join(vr)
        so = max((_sim(t, o) for o in outside), default=0.0)
        si = max((_sim(t, x) for x in inside), default=0.0)
        if so >= 0.8 and so > si:
            continue
        out.append(vr)
    return out


def pred_pages_from_doc(doc: dict) -> dict:
    """与 scripts/blind20_build.py 的 pred_pages 口径一致：正文=非表格非印章块；表格=按行、格间空格；印章单独。"""
    out = {}
    for pm in doc["pages"]:
        p = int(pm["page_number"])
        bl = sorted([x for x in doc["blocks"] if x["page"] == p], key=lambda x: x["reading_order"])
        secs = []
        for x in bl:
            if x["type"] == "table":
                rows: dict[int, list] = {}
                for c in sorted(x["table"]["cells"], key=lambda c: (c["row"], c["col"])):
                    rows.setdefault(c["row"], []).append(c["text"])
                secs.append({"section": "table", "text": "\n".join(" ".join(v) for _, v in sorted(rows.items()))})
            elif x["type"] == "seal":
                continue
            else:
                secs.append({"section": "body", "text": x.get("text") or ""})
        seal = "\n".join(x["text"] for x in bl if x["type"] == "seal" and x.get("text"))
        out[str(p)] = {"sections": secs, "seal": seal}
    return out


def _prov(item: dict, res: dict, pre) -> dict:
    return {"region_id": item["id"], "route": item["route"], "kind": item["kind"], "reasons": item["reasons"], "model": res.get("model"),
            "crop": item["crop_path"], "crop_sha256": item["sha256"], "crop_box_px": item["crop_box_px"], "render_dpi": 200,
            "pre_vlm_text": pre}


def _warn(code, item, p, bid, msg) -> dict:
    return {"code": code, "message": f"{msg}（区域 {item['id']}，裁图 {item['crop_path']}）", "page": p, "block_id": bid, "severity": "提示"}


def _replace_table_rows(blk: dict, bands: list[tuple[dict, list[list[str]]]]):
    from .rules import table_rows

    rows = table_rows(blk)
    by_start = {}
    for item, vrows in bands:
        a, b = item["target"]["row_idx"]
        by_start[a] = (b, item, drop_bleed_rows(vrows, rows, a, b))
    new_cells, r_out, i = [], 0, 0
    replaced = set()
    while i < len(rows):
        if i in by_start:
            b, item, vrows = by_start[i]
            y0, y1 = rows[i]["band_y0"], rows[b]["band_y1"]
            x0, x1 = blk["bbox"][0], blk["bbox"][2]
            n = max(1, len(vrows))
            for k, vr in enumerate(vrows):
                ncol = max(1, len(vr))
                ry0 = y0 + (y1 - y0) * k / n
                ry1 = y0 + (y1 - y0) * (k + 1) / n
                for cidx, txt in enumerate(vr):
                    new_cells.append({"row": r_out, "col": cidx, "rowspan": 1, "colspan": 1, "text": txt,
                                      "bbox": [round(x0 + (x1 - x0) * cidx / ncol, 2), round(ry0, 2), round(x0 + (x1 - x0) * (cidx + 1) / ncol, 2), round(ry1, 2)]})
                r_out += 1
            replaced.update(range(i, b + 1))
            i = b + 1
            continue
        # 保留 B 原行；跨行单元格不得伸进被替换的行段
        for c in rows[i]["cells"]:
            c2 = dict(c)
            span = int(c.get("rowspan") or 1)
            k = 1
            while k < span and (i + k) < len(rows) and (i + k) not in replaced and (i + k) not in by_start:
                k += 1
            c2["row"], c2["rowspan"] = r_out, k
            new_cells.append(c2)
        r_out += 1
        i += 1
    blk["table"]["cells"] = new_cells or [{"row": 0, "col": 0, "text": ""}]
    blk["table"]["n_rows"] = max(1, max(c["row"] + int(c.get("rowspan") or 1) for c in blk["table"]["cells"]))
    blk["table"]["n_cols"] = max(1, max(c["col"] + int(c.get("colspan") or 1) for c in blk["table"]["cells"]))


def merge_route(b_doc: dict, items: list[dict], results: dict[str, dict], root: Path | None = None, assets_out: Path | None = None,
                lowconf_threshold: float | None = None) -> tuple[dict, dict, list]:
    """b_doc：B 标准版中间 JSON；items：manifest 条目；results：{item_id: 结果(ok/text/...)}。返回 (doc, pred_pages, log)。
    lowconf_threshold：离线生效阈值（见 rules.lowconf_only_inactive）；None 表示全部采用。"""
    from .rules import lowconf_only_inactive

    if lowconf_threshold is not None:
        items = [i for i in items if not lowconf_only_inactive(i, lowconf_threshold)]
    doc = copy.deepcopy(b_doc)
    doc.setdefault("warnings", [])
    by_id = {b["id"]: b for b in doc["blocks"]}
    log = []
    table_bands: dict[str, list] = {}
    inserts = []
    for item in items:
        res = results.get(item["id"])
        if not res or not res.get("ok"):
            log.append({"item_id": item["id"], "action": "kept_B", "why": (res or {}).get("error") or (res or {}).get("skipped") or "no_result"})
            continue
        txt = res.get("text") or ""
        p = item["page"]
        if item["kind"] == "line":
            blk = by_id[item["target"]["b_block_id"]]
            pre = blk.get("text")
            blk["text"] = flatten_line(txt)
            blk["source"] = "vlm"
            blk["verification_status"] = "unverified"
            blk["vlm"] = _prov(item, res, pre)
            blk.setdefault("warnings", []).append(_warn("vlm_replaced", item, p, blk["id"], "文本由视觉模型重识别替换"))
            log.append({"item_id": item["id"], "action": "replace_line", "block": blk["id"], "before": pre, "after": blk["text"]})
        elif item["kind"] == "missing_line":
            t = flatten_line(txt)
            if not t:
                log.append({"item_id": item["id"], "action": "missing_line_empty"})
                continue
            inserts.append((item, res, t))
        elif item["kind"] == "table_band":
            table_bands.setdefault(item["target"]["b_block_id"], []).append((item, parse_table(txt)))
        elif item["kind"] == "seal":
            st = flatten_line(txt)
            ids = item["target"]["b_seal_ids"]
            if ids:
                for k, sid in enumerate(ids):
                    blk = by_id[sid]
                    pre = blk.get("text")
                    blk["text"] = st if k == 0 else ""
                    blk["source"] = "vlm"
                    blk["vlm"] = _prov(item, res, pre)
                    blk.setdefault("warnings", []).append(_warn("vlm_seal", item, p, sid, "印章文字由视觉模型识别"))
                log.append({"item_id": item["id"], "action": "replace_seal", "blocks": ids, "after": st})
            elif st:
                inserts.append((item, res, st))
    for tid, bands in table_bands.items():
        blk = by_id[tid]
        pre = [dict(c) for c in blk["table"]["cells"]]
        _replace_table_rows(blk, bands)
        blk["source"] = "mixed"
        blk["verification_status"] = "unverified"
        blk["vlm"] = {"bands": [_prov(it, {"model": it["model"]}, None) for it, _ in bands], "pre_vlm_n_cells": len(pre)}
        blk.setdefault("warnings", []).append({"code": "vlm_table_rows", "message": f"表格 {len(bands)} 段行由视觉模型重识别替换；新单元格 bbox 为段内均分近似值",
                                              "page": blk["page"], "block_id": tid, "severity": "提示"})
        log.append({"item_id": [it["id"] for it, _ in bands], "action": "replace_table_rows", "block": tid})
    # 插入新块（漏行 / 仅本地检出的印章）
    for k, (item, res, t) in enumerate(inserts, 1):
        p = item["page"]
        bid = f"b-{p:03d}-v{k:02d}"
        is_seal = item["kind"] == "seal"
        nb = {"id": bid, "page": p, "bbox": item["bbox_pt"], "bbox_unit": "pt", "type": "seal" if is_seal else "paragraph",
              "text": t, "confidence": 0.8, "source": "vlm", "verification_status": "unverified" if not is_seal else "needs_review",
              "vlm": _prov(item, res, None),
              "warnings": [_warn("vlm_inserted", item, p, bid, "该行 B 未输出，由视觉模型从裁图补出" if not is_seal else "本地红章检测到、B 未给出的印章")]}
        if is_seal:
            nb["needs_review"] = True
            aid = f"a-vlm-{item['id']}"
            if root is not None and assets_out is not None:
                src = root / item["crop_path"]
                assets_out.mkdir(parents=True, exist_ok=True)
                dst = assets_out / f"{aid}.png"
                shutil.copyfile(src, dst)
                doc.setdefault("assets", []).append({"id": aid, "type": "seal", "path": f"assets/{aid}.png", "mime_type": "image/png",
                                                     "hash_sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
                                                     "width_px": int(item["crop_w"]), "height_px": int(item["crop_h"]), "original_dpi": 200.0, "source_page": p})
            nb["image"] = {"asset_id": aid}
        page_blocks = sorted([b for b in doc["blocks"] if b["page"] == p], key=lambda b: b["reading_order"])
        pos = len([b for b in page_blocks if b["type"] != "seal"])
        if not is_seal:
            cy = (nb["bbox"][1] + nb["bbox"][3]) / 2
            for j, b in enumerate(page_blocks):
                if b["type"] == "seal":
                    continue
                if (b["bbox"][1] + b["bbox"][3]) / 2 > cy and not (b["type"] == "table" and b["bbox"][1] <= cy <= b["bbox"][3]):
                    pos = j
                    break
        else:
            pos = len(page_blocks)
        page_blocks.insert(pos, nb)
        for r, b in enumerate(page_blocks):
            b["reading_order"] = r
        doc["blocks"].append(nb)
        by_id[bid] = nb
        log.append({"item_id": item["id"], "action": "insert_block", "block": bid, "text": t})
    doc["blocks"].sort(key=lambda b: (b["page"], b["reading_order"]))
    return doc, pred_pages_from_doc(doc), log
