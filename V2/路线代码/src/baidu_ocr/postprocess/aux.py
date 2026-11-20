"""补漏/重排需要的辅助输入：A（高精度含位置）的字框、Unlimited-OCR 的版面块。全部来自已落盘的原始返回，不联网。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..hybrid.inputs import a_chars_for_page
from ..hybrid.merge import parse_table

# 与离线模拟 analysis-opt/sim.py 的 qrstrip 相同的英文长句判据，外加「QR code」字样
QR_RE = re.compile(r"[A-Za-z]{2,}( [A-Za-z,\.]+){6,}|QR\s*[Cc]ode")
TEXT_TYPES = {"text", "title", "header", "footer", "aside_text", "page_number", "contents", "caption"}


def is_qr_hallucination(text: str) -> bool:
    return bool(QR_RE.search(text or ""))


def load_u_items(u_raw: dict, page_width_pt: dict[int, float]) -> tuple[dict[int, list], int]:
    """Unlimited 版面块 → {page: [{kind, bbox(pt), text, cells}]}；返回 (结果, 剔除的二维码幻觉块数)。"""
    out, dropped = {}, 0
    for pg in u_raw.get("pages") or []:
        p = int(pg["page_num"]) + 1
        pw = float((pg.get("meta") or {}).get("page_width") or page_width_pt.get(p) or 1)
        sx = float(page_width_pt.get(p, pw)) / pw
        items = []
        for lay in pg.get("layouts") or []:
            typ = (lay.get("type") or "text").lower()
            txt = lay.get("text") or ""
            pos = lay.get("position") or [0, 0, 0, 0]
            bb = [pos[0] * sx, pos[1] * sx, (pos[0] + pos[2]) * sx, (pos[1] + pos[3]) * sx]
            if is_qr_hallucination(txt):
                dropped += 1
                continue
            if typ == "table" or "<table" in txt:
                rows = parse_table(txt)
                items.append({"kind": "table", "bbox": bb, "text": "", "cells": [c for r in rows for c in r if c and c.strip()]})
            elif typ in TEXT_TYPES:
                items.append({"kind": "text", "bbox": bb, "text": txt, "cells": []})
        out[p] = items
    return out, dropped


def load_aux(root: Path, b_doc: dict, a_dir: Path | None = None, u_raw_path: Path | None = None) -> dict:
    root = Path(root)
    a_dir = Path(a_dir or root / "output" / "scheme_a")
    u_raw_path = Path(u_raw_path or root / "output" / "scheme_b_unlimited" / "raw" / "blind20-scan.parse_result.json")
    asum = {pg["page"]: pg for pg in json.loads((a_dir / "call_summary.json").read_text(encoding="utf-8"))["pages"]}
    widths = {int(pm["page_number"]): float(pm["width"]) for pm in b_doc["pages"]}
    u_items, dropped = load_u_items(json.loads(u_raw_path.read_text(encoding="utf-8")), widths)
    pages = {}
    for p in widths:
        pages[p] = {"a_chars": a_chars_for_page(a_dir, asum[p]) if p in asum else [], "u_items": u_items.get(p, [])}
    return {"pages": pages, "u_qr_dropped": dropped, "a_dir": str(a_dir), "u_raw": str(u_raw_path)}


def chars_in(chars: list[dict], bb, pad: float = 1.5) -> list[dict]:
    x0, y0, x1, y1 = bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad
    out = []
    for c in chars:
        cx, cy = (c["bbox"][0] + c["bbox"][2]) / 2, (c["bbox"][1] + c["bbox"][3]) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(c)
    return out


def reading_sort(chars: list[dict]) -> list[dict]:
    """按行聚类（y 中心差 < 半个字高）再按 x 排。"""
    if not chars:
        return []
    hs = sorted(c["bbox"][3] - c["bbox"][1] for c in chars)
    tol = max(2.0, hs[len(hs) // 2] * 0.5)
    cs = sorted(chars, key=lambda c: (c["bbox"][1] + c["bbox"][3]) / 2)
    lines, cur, cy = [], [], None
    for c in cs:
        y = (c["bbox"][1] + c["bbox"][3]) / 2
        if cy is not None and abs(y - cy) > tol:
            lines.append(cur)
            cur = []
        cur.append(c)
        cy = sum((k["bbox"][1] + k["bbox"][3]) / 2 for k in cur) / len(cur)
    if cur:
        lines.append(cur)
    return [c for ln in lines for c in sorted(ln, key=lambda k: k["bbox"][0])]
