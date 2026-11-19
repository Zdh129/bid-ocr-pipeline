"""★/☆ 过滤 + 判空心/实心（f1_round2 新增；替代 star_fill 用；通用规则，不看参照、不按页号/具体字符写死）。

1. A（accurate 含位置）识别为 ★/☆ 的每个字框：同 star_fill 量中心 30% 窗口黑像素比例 core（≥ 0.5 判 ★，否则 ☆），
   并要求字框内有墨迹（墨迹像素 ≥ 5）才算「有星形墨迹」；上下文取其后最多 6 个字。
2. 输出里的每个 ★/☆：取其后最多 6 个非空白字作上下文（比较前做 NFKC，「。」视同「.」、「，」视同「,」），
   先用前 3 字（不足 3 取全部，至少 2 字）去匹配同页 A 星号；匹配到的判定互相冲突时，逐步加长到 6 字再比。
   - 匹配到且判定一致 → 改成该判定；
   - 一个都匹配不到 → 视为 A 字框里没有对应的星形墨迹，删除这个字符；
   - 加长到 6 字仍冲突 → 不改。
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from .star_fill import CORE_T

STARS = "★☆"
CTX_MAX, CTX_MIN, CTX_START = 6, 2, 3


def _n(s: str) -> str:
    t = unicodedata.normalize("NFKC", s)
    return t.replace("。", ".").replace("，", ",")


def _ctx(chars, i, n=CTX_MAX):
    return _n("".join(c for c in chars[i + 1:] if not c.isspace()))[:n]


def page_marks(a_dir: Path, page_summary: dict) -> list[dict]:
    import cv2
    import numpy as np
    p, api = page_summary["page"], page_summary["route"]["api"]
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
                continue   # 字框里没有墨迹：不算星号
            x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
            cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * 0.15, (y1 - y0) * 0.15
            core = float(crop[int(cy - hh):int(cy + hh) + 1, int(cx - hw):int(cx + hw) + 1].mean())
            out.append({"ctx": _ctx(seq, i), "core": round(core, 3), "filled": core >= CORE_T})
    return out


def load_marks(a_dir: Path) -> dict[int, list[dict]]:
    summ = json.loads((Path(a_dir) / "call_summary.json").read_text(encoding="utf-8"))
    return {pg["page"]: page_marks(a_dir, pg) for pg in summ["pages"]}


def decide(ctx: str, marks: list[dict]):
    """返回 '★' / '☆' / None（冲突）/ 'DEL'（无匹配）。"""
    if len(ctx) < CTX_MIN:
        return None   # 上下文太短：不动
    k = min(CTX_START, len(ctx))
    while True:
        cand = [m for m in marks if len(m["ctx"]) >= min(k, len(ctx)) and m["ctx"][:k] == ctx[:k]]
        if not cand:
            return "DEL" if k == min(CTX_START, len(ctx)) else None
        kinds = {m["filled"] for m in cand}
        if len(kinds) == 1:
            return "★" if kinds.pop() else "☆"
        if k >= min(CTX_MAX, len(ctx)):
            return None
        k += 1


def apply_text(text, marks, log=None, page=None):
    if not text or not any(c in STARS for c in text):
        return text, 0
    chars = list(text)
    out, n = [], 0
    for i, ch in enumerate(chars):
        if ch not in STARS:
            out.append(ch)
            continue
        d = decide(_ctx(chars, i), marks)
        if d == "DEL":
            n += 1
            if log is not None:
                log.append({"step": "star_filter", "page": page, "action": "delete", "char": ch, "ctx": _ctx(chars, i)})
            continue
        if d in ("★", "☆") and d != ch:
            n += 1
            if log is not None:
                log.append({"step": "star_filter", "page": page, "action": "set", "from": ch, "to": d, "ctx": _ctx(chars, i)})
            out.append(d)
            continue
        out.append(ch)
    return "".join(out), n


def apply_pred(pr: dict, marks_by_page: dict, log=None) -> int:
    n = 0
    for p, pg in pr.items():
        mk = marks_by_page.get(int(p)) or []
        for s in pg["sections"]:
            s["text"], k = apply_text(s["text"], mk, log, int(p))
            n += k
    return n
