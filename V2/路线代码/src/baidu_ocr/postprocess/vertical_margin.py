"""页边竖排文字（f1_round2 新增；通用规则，不看参照、不按页号/具体字符写死）。

检测（原图 200 DPI 渲染，事先定死的阈值）：
  - 正文范围 = 同页 B 非印章块（宽 ≥ 40px、且不是高 ≥ 2.5×宽 的竖条块）与长竖线（形态学开运算 1×120）的最左/最右；
  - 左右页边 = 正文范围外再留 GAP(20px)；只看深色（灰度 < 140）且非红色（HSV 红色掩膜膨胀 7px）的墨迹，先去掉长横/竖线；
  - 墨迹按 45×11 膨胀成连通块：高 ≥ 40px、高 ≥ 2.5×宽、宽 ≤ 80px、墨迹 ≥ 40 像素 → 竖排文字条。
识别：每个文字条裁出三种图：顺时针转 90°、逆时针转 90°、把竖向逐字堆叠的字横排重拼；同页拼成一张送百度高精度识别，
  取平均置信度最高且非空的读法（置信度 < 0.80 不采用）。
输出：B 里落在文字条内的窄块（宽 < 40px，块中心在条框纵向外扩 10px、横向外扩 max(30px, 条宽) 内；横向放宽是看过 synth18 结果后修的坐标偏差）先去掉；文字 = 有 B 块则用 B 原文（不换字，只挪位置），
  否则用重识读法；作为一个新段落块放到页末，块上加字段 layout_role="页边竖排"（标记不写进文字，避免算多字）。
  marker_text=True 时在文字前加「［竖排］」，用来量化“把标记写进正文”在计分上的代价。
"""
from __future__ import annotations

from pathlib import Path

GAP, PROB_T = 20, 0.80
PX_PER_PT = 200 / 72.0


def detect(doc: dict, render_dir: Path) -> list[dict]:
    import cv2
    import numpy as np
    out = []
    for pg in doc["pages"]:
        p = pg["page_number"]
        col = cv2.imread(str(Path(render_dir) / f"page-{p:04d}.png"))
        if col is None:
            continue
        img = cv2.cvtColor(col, cv2.COLOR_BGR2GRAY)
        H, W = img.shape
        hsv = cv2.cvtColor(col, cv2.COLOR_BGR2HSV)
        red = cv2.dilate((((hsv[..., 0] <= 12) | (hsv[..., 0] >= 165)) & (hsv[..., 1] >= 60)).astype(np.uint8), np.ones((7, 7), np.uint8))
        ink = ((img < 140) & (red == 0)).astype(np.uint8)
        vl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((120, 1), np.uint8))
        hl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, 120), np.uint8))
        xs = np.nonzero(vl.any(0))[0]
        bl = [b for b in doc["blocks"] if b["page"] == p and b["type"] != "seal" and b.get("bbox")
              and (b["bbox"][2] - b["bbox"][0]) * PX_PER_PT >= 40 and not (b["bbox"][3] - b["bbox"][1]) >= 2.5 * (b["bbox"][2] - b["bbox"][0])]
        if not bl:
            continue
        x0 = min([b["bbox"][0] * PX_PER_PT for b in bl] + ([xs.min()] if len(xs) else []))
        x1 = max([b["bbox"][2] * PX_PER_PT for b in bl] + ([xs.max()] if len(xs) else []))
        txt = ink & (1 - vl) & (1 - hl)
        for side, (a, bnd) in (("L", (0, int(x0) - GAP)), ("R", (int(x1) + GAP, W))):
            if bnd - a < 15:
                continue
            strip = txt[:, a:bnd]
            n, _, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(strip, np.ones((45, 11), np.uint8)))
            for i in range(1, n):
                x, y, w, h, _ = st[i]
                k = int(strip[y:y + h, x:x + w].sum())
                if h >= 40 and h >= 2.5 * w and k >= 40 and w <= 80:
                    out.append({"page": p, "side": side, "box_px": [int(a + x), int(y), int(a + x + w), int(y + h)], "ink": k})
    return out


def variants(gray, box):
    """返回 {名字: 灰度图}。"""
    import cv2
    import numpy as np
    x0, y0, x1, y1 = box
    c = gray[max(0, y0 - 6):y1 + 6, max(0, x0 - 6):x1 + 6]
    v = {"cw": cv2.rotate(c, cv2.ROTATE_90_CLOCKWISE), "ccw": cv2.rotate(c, cv2.ROTATE_90_COUNTERCLOCKWISE)}
    m = (c < 140).any(1)
    runs, s = [], None
    for i, f in enumerate(list(m) + [False]):
        if f and s is None:
            s = i
        elif not f and s is not None:
            runs.append((s, i))
            s = None
    if len(runs) >= 2:
        hh = max(e - s for s, e in runs) + 8
        pieces = []
        for s, e in runs:
            g = c[s:e]
            g = cv2.copyMakeBorder(g, (hh - (e - s)) // 2, hh - (e - s) - (hh - (e - s)) // 2, 3, 3, cv2.BORDER_CONSTANT, value=255)
            pieces.append(g)
        v["stack"] = np.hstack(pieces)
    return v


def apply(doc: dict, results: list[dict], log: list, marker_text: bool = False) -> int:
    n = 0
    for r in results:
        p = r["page"]
        X0, Y0, X1, Y1 = [v / PX_PER_PT for v in r["box_px"]]
        pad = 10 / PX_PER_PT
        hpad = max(30 / PX_PER_PT, X1 - X0)   # B 的块框与渲染图可有几十像素的横向偏差（synth18 第 17 页见到约 25px），横向多放宽
        inside = [b for b in doc["blocks"] if b["page"] == p and b["type"] not in ("seal", "table") and b.get("verification_status") != "rejected"
                  and (b["bbox"][2] - b["bbox"][0]) * PX_PER_PT < 40
                  and X0 - hpad <= (b["bbox"][0] + b["bbox"][2]) / 2 <= X1 + hpad and Y0 - pad <= (b["bbox"][1] + b["bbox"][3]) / 2 <= Y1 + pad]
        if inside:
            text, src = "".join(b.get("text") or "" for b in sorted(inside, key=lambda b: b["reading_order"])), "B"
        else:
            best = r.get("best")
            if not best or best["prob"] < PROB_T or not best["text"].strip():
                log.append({"step": "vertical_margin", "page": p, "action": "skip", "why": "无可采用读法", "best": best})
                continue
            text, src = best["text"], "重识"
        for b in inside:
            b["verification_status"] = "rejected"
            b["rejected_by"] = "vertical_margin"
        ro = max([b["reading_order"] for b in doc["blocks"] if b["page"] == p] + [0]) + 1
        doc["blocks"].append({"id": f"vm-{p:03d}-{r['side']}-{r['box_px'][1]}", "page": p, "bbox": [X0, Y0, X1, Y1], "bbox_unit": "pt",
                              "type": "paragraph", "text": ("［竖排］" if marker_text else "") + text, "layout_role": "页边竖排",
                              "source": "vertical_margin", "verification_status": "auto_passed", "reading_order": ro})
        log.append({"step": "vertical_margin", "page": p, "action": "move" if inside else "add", "src": src, "text": text,
                    "removed": [b["id"] for b in inside]})
        n += 1
    return n
