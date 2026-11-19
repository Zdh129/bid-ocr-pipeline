"""剔除印章区域混进正文的文字。被剔除的块保留在 JSON 里，verification_status=rejected 并加 warning，不进正文输出。

判定（满足任一，且均要求：正文块类型为 paragraph/header、规范化后 ≤max_chars 字、不含冒号、不以序号/★ 开头）：
P  位置：块面积落在某个印章框（B 的 seal 块框；可选本地红章框）内的比例 ≥ overlap_min。
T1 文字：块文字与同页印章文字重合 ≥60%，且 ≥4 字（与离线模拟 sim.py sealstrip 相同）。
T2 关键词：税务局 / 下纛 / 监制（发票监制章字样，与离线模拟相同）。
T3 与相邻行重复：本行 ≥6 字且被相邻含冒号的行包含（如印章名被单独读成一行，与离线模拟相同）。
"""
from __future__ import annotations

import re

from .textutil import norm

KW = re.compile(r"税务局|下纛|监制")
NUM_START = re.compile(r"^[\s(（]*[★☆]?\s*\d")


def _overlap(bb, sb) -> float:
    ix = max(0.0, min(bb[2], sb[2]) - max(bb[0], sb[0]))
    iy = max(0.0, min(bb[3], sb[3]) - max(bb[1], sb[1]))
    a = (bb[2] - bb[0]) * (bb[3] - bb[1])
    return ix * iy / a if a > 0 else 0.0


def decide(blk: dict, seal_boxes: list, seal_text: str, neighbours: list[str], cfg: dict) -> str | None:
    t = blk.get("text") or ""
    n = norm(t)
    if blk.get("type") not in set(cfg.get("block_types", ["paragraph", "header"])):
        return None
    if not (0 < len(n) <= int(cfg.get("max_chars", 12))) or re.search(r"[:：]", t) or NUM_START.match(t):
        return None
    if cfg.get("use_position", True):
        ov = max((_overlap(blk["bbox"], sb) for sb in seal_boxes), default=0.0)
        if ov >= float(cfg.get("overlap_min", 0.6)):
            return f"P 位于印章框内（重叠 {ov:.2f}）"
    if cfg.get("use_text", True):
        st = norm(seal_text)
        if st and len(n) >= 4 and sum(c in st for c in n) / len(n) >= 0.6:
            return "T1 与印章文字重合≥60%"
        if KW.search(n):
            return "T2 发票监制章关键词"
        for nb in neighbours:
            m = norm(nb)
            if len(n) >= 6 and n != m and n in m and re.search(r"[:：]", nb):
                return "T3 被相邻含冒号行包含"
    return None


def apply(doc: dict, cfg: dict, log: list, extra_seal_boxes: dict | None = None) -> int:
    n = 0
    pages = sorted({b["page"] for b in doc["blocks"]})
    for p in pages:
        bl = sorted([b for b in doc["blocks"] if b["page"] == p], key=lambda b: b.get("reading_order", 0))
        seals = [b for b in bl if b["type"] == "seal"]
        boxes = [b["bbox"] for b in seals] + list((extra_seal_boxes or {}).get(p, []))
        stext = "".join(b.get("text") or "" for b in seals)
        body = [b for b in bl if b["type"] not in ("seal", "table")]
        for i, b in enumerate(body):
            if b.get("verification_status") == "rejected":
                continue
            nbs = [body[j].get("text") or "" for j in (i - 1, i + 1) if 0 <= j < len(body)]
            why = decide(b, boxes, stext, nbs, cfg)
            if why:
                log.append({"step": "seal_strip", "page": p, "block_id": b["id"], "text": b.get("text"), "reason": why})
                b["verification_status"] = "rejected"
                b.setdefault("warnings", []).append({"code": "seal_text_stripped", "message": f"疑似印章字混入正文，已剔除：{why}", "severity": "提示"})
                n += 1
    return n
