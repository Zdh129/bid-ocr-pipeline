"""印章文字混入正文：红章区内去红后重识，B 正文里重识读不到的字视为章文去掉（f1_round2 新增，通用规则，不看参照）。

阈值（事先定死）：
  选取：B 正文类块（paragraph/title/header/list，非表格、非印章、非图片），同时满足
        a. 在原图 200 DPI 渲染上块框内红色像素比例 ≥ RED_T(0.05)（红色 = HSV：H≤12 或 ≥155，S≥50，V≥70，与 P2 同一掩膜）；
        b. 块框与同页 B 识别出的印章块框重叠 ≥ SEAL_OV(0.4)（以块面积计）——排除红色印刷的正文（如发票标题）。
  重识：块框外扩 6px 裁图，把红色像素（掩膜再膨胀 2px）置白（不动黑字），放大 2 倍，按页拼图送百度 accurate。
  采用：把块文字与重识文字逐字对齐（去空白、NFKC），块里连续 ≥ MIN_RUN(2) 个字在重识里对不上 → 视为章文从正文删除；
        重识为空且红色比例 ≥ WHOLE_T(0.3) → 整块删除（否则不动，防重识失败误删）。只删不增。
  两种模式：runs（上面的完整规则）；whole（只做整块删除）。whole 是看过 synth18/synth18c 的去红重识文字之后、计分之前加的：
  去红会连带抹掉章下黑字的笔画，runs 模式会误删真实正文。两种都计分并报告。B 整页不用 P2（上一轮 B 用 P2 变差）。
"""
from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

RED_T, SEAL_OV, WHOLE_T, MIN_RUN, PAD = 0.05, 0.4, 0.3, 2, 6
PX_PER_PT = 200 / 72.0
TYPES = ("paragraph", "title", "header", "list")


def red_mask(bgr):
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = (((h <= 12) | (h >= 155)) & (s >= 50) & (v >= 70)).astype(np.uint8) * 255
    return cv2.dilate(m, np.ones((3, 3), np.uint8), 1)


def select(doc: dict, render_dir: Path) -> list[dict]:
    import cv2
    units, cache = [], {}
    for b in doc["blocks"]:
        if b["type"] not in TYPES or b.get("verification_status") == "rejected" or not (b.get("text") or "").strip():
            continue
        p = b["page"]
        if p not in cache:
            img = cv2.imread(str(Path(render_dir) / f"page-{p:04d}.png"))
            cache[p] = (img, red_mask(img) if img is not None else None)
        img, m = cache[p]
        if img is None:
            continue
        H, W = m.shape
        x0, y0 = max(0, int(b["bbox"][0] * PX_PER_PT)), max(0, int(b["bbox"][1] * PX_PER_PT))
        x1, y1 = min(W, int(b["bbox"][2] * PX_PER_PT) + 1), min(H, int(b["bbox"][3] * PX_PER_PT) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        frac = float((m[y0:y1, x0:x1] > 0).mean())
        seals = [s["bbox"] for s in doc["blocks"] if s["page"] == p and s["type"] == "seal"]
        if frac >= RED_T and max([_ov(b["bbox"], s) for s in seals] + [0.0]) >= SEAL_OV:
            units.append({"id": b["id"], "page": p, "text": b["text"], "red_frac": round(frac, 3),
                          "box_px": [max(0, x0 - PAD), max(0, y0 - PAD), min(W, x1 + PAD), min(H, y1 + PAD)]})
    return units


def _ov(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1]) or 1.0
    return ix * iy / area


def dered(img):
    import cv2
    import numpy as np
    m = cv2.dilate(red_mask(img), np.ones((5, 5), np.uint8), 1) > 0
    out = img.copy()
    out[m] = 255
    return out


def _chars(t: str):
    out = []
    for i, ch in enumerate(t or ""):
        n = unicodedata.normalize("NFKC", ch)
        if n.strip():
            out.append((i, n[0]))
    return out


def apply(doc: dict, results: list[dict], log: list, mode: str = "runs") -> int:
    """mode="runs"：按上面的规则删未对上的连续片段；mode="whole"：只在重识为空且红色比例 ≥ WHOLE_T 时整块删除。"""
    by_id = {r["id"]: r for r in results}
    n = 0
    for b in doc["blocks"]:
        r = by_id.get(b["id"])
        if r is None or r.get("r_text") is None:
            continue
        if b.get("text") != r["text"]:
            continue   # 块文字已被前序步骤改动：不动
        bc = _chars(b["text"])
        rs = "".join(c for _, c in _chars(r["r_text"]))
        bs = "".join(c for _, c in bc)
        keep = [False] * len(bc)
        for a, _, size in SequenceMatcher(None, bs, rs, autojunk=False).get_matching_blocks():
            for k in range(a, a + size):
                keep[k] = True
        drop = set()
        k = 0
        while k < len(bc):
            if keep[k]:
                k += 1
                continue
            j = k
            while j < len(bc) and not keep[j]:
                j += 1
            if (mode == "runs" and j - k >= MIN_RUN and rs) or (not rs and r["red_frac"] >= WHOLE_T):
                drop.update(bc[q][0] for q in range(k, j))
            k = j
        if drop:
            new = "".join(ch for i, ch in enumerate(b["text"]) if i not in drop)
            log.append({"step": "seal_leak", "page": b["page"], "block_id": b["id"], "removed": "".join(b["text"][i] for i in sorted(drop)),
                        "kept": new, "red_frac": r["red_frac"], "reread": r["r_text"]})
            b["text"] = new
            n += 1
    return n
