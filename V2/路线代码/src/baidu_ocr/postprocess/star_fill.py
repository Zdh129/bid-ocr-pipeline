"""★/☆ 按字框填充率判空心/实心（opt2 新增，通用规则，不看参照）。

判据（事先定死，不拟合）：对 A（accurate 含字位置）识别为 ★/☆ 的字框，取页面渲染图（A 的 200 DPI 渲染）
在字框内的墨迹外接框，量中心 30%×30% 窗口的黑像素比例 core。实心星中心是墨，空心星中心是纸 →
core ≥ CORE_T(0.5，几何中点) 判 ★，否则判 ☆。
落到输出：对预测文字里每个 ★/☆，取其后 3 个非空白字作上下文，到同页 A 星号里找上下文相同（前缀 ≥2 字一致）的那个，
按其判定改写；找不到则保持原样（即保留 R1 的结果）。只改 ★/☆ 本身，不增删字。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CORE_T = 0.5
STARS = "★☆"


def _ctx(chars: list[str], i: int, n: int = 3) -> str:
    return "".join(c for c in chars[i + 1:] if not c.isspace())[:n]


def page_marks(a_dir: Path, page_summary: dict) -> list[dict]:
    import cv2
    import numpy as np
    p = page_summary["page"]
    api = page_summary["route"]["api"]
    raw = json.loads((Path(a_dir) / "raw" / f"p{p:02d}_main_{api}.json").read_text(encoding="utf-8"))
    img_p = Path(a_dir) / "renders" / f"page-{p:04d}.png"
    if not img_p.exists():
        return []
    img = cv2.imread(str(img_p), cv2.IMREAD_GRAYSCALE)
    out = []
    for w in raw.get("words_result") or []:
        chs = w.get("chars") or []
        seq = [c["char"] for c in chs]
        for i, ch in enumerate(chs):
            if ch["char"] not in STARS:
                continue
            l = ch["location"]
            crop = img[max(0, l["top"]):l["top"] + l["height"], max(0, l["left"]):l["left"] + l["width"]] < 128
            ys, xs = np.nonzero(crop)
            if len(xs) < 5:
                continue
            x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
            cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * 0.15, (y1 - y0) * 0.15
            core = float(crop[int(cy - hh):int(cy + hh) + 1, int(cx - hw):int(cx + hw) + 1].mean())
            out.append({"ctx": _ctx(seq, i), "core": round(core, 3), "filled": core >= CORE_T, "a_char": ch["char"]})
    return out


def load_marks(a_dir: Path) -> dict[int, list[dict]]:
    summ = json.loads((Path(a_dir) / "call_summary.json").read_text(encoding="utf-8"))
    return {pg["page"]: page_marks(a_dir, pg) for pg in summ["pages"]}


def apply_text(text: str, marks: list[dict], log: list | None = None, page: int | None = None) -> tuple[str, int]:
    if not text or not marks or not re.search(f"[{STARS}]", text):
        return text, 0
    chars = list(text)
    n = 0
    for i, ch in enumerate(chars):
        if ch not in STARS:
            continue
        ctx = _ctx(chars, i)
        cand = [m for m in marks if len(ctx) >= 2 and m["ctx"][:len(ctx)][:max(2, min(len(ctx), len(m["ctx"])))] == ctx[:max(2, min(len(ctx), len(m["ctx"])))]]
        if not cand:
            continue
        kinds = {m["filled"] for m in cand}
        if len(kinds) != 1:
            continue   # 同上下文判定冲突：不改
        new = "★" if kinds.pop() else "☆"
        if new != ch:
            chars[i] = new
            n += 1
            if log is not None:
                log.append({"step": "star_fill", "page": page, "ctx": ctx, "from": ch, "to": new, "core": [m["core"] for m in cand]})
    return "".join(chars), n


def apply_pred(pr: dict, marks_by_page: dict, log: list | None = None) -> int:
    n = 0
    for p, pg in pr.items():
        mk = marks_by_page.get(int(p)) or []
        for s in pg["sections"]:
            s["text"], k = apply_text(s["text"], mk, log, int(p))
            n += k
    return n
