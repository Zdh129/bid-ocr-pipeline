"""blind20 复测的本地图像分析：印章（红色像素）、表格线（形态学，容忍 ±1° 倾斜）、文字行框（用于覆盖检查）。

全部在本机运行，不外发。坐标为渲染图像素，左上原点。
"""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


def load_bgr(path: str) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return img


def red_mask(img: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = ((h <= 10) | (h >= 160)) & (s >= 70) & (v >= 60)
    return m.astype(np.uint8)


def seal_stats(img: np.ndarray, min_red_px: int = 1500, min_comp_px: int = 600) -> dict:
    m = red_mask(img)
    H0, W0 = m.shape
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    areas = sorted((int(a) for a in stats[1:, cv2.CC_STAT_AREA]), reverse=True)
    red_px = int(m.sum())
    # 外接框：把大块红色合并（印章可能被切成几段，如骑缝章）
    big = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 60]
    boxes = []
    if big:
        mm = np.isin(lab, big).astype(np.uint8)
        mm = cv2.dilate(mm, np.ones((25, 25), np.uint8))
        n2, _, st2, _ = cv2.connectedComponentsWithStats(mm, 8)
        for i in range(1, n2):
            x, y, w, h, a = (int(v) for v in st2[i])
            # 印章：块状、不超过页面 35%（排除发票红色边框/表格线），最短边 >= 50px
            if a >= 2500 and min(w, h) >= 50 and w <= 0.35 * W0 and h <= 0.35 * H0:
                boxes.append([x, y, x + w, y + h])
    hit = red_px >= min_red_px or (areas[0] if areas else 0) >= min_comp_px
    H, W = m.shape
    return {
        "red_px": red_px,
        "red_ratio": round(red_px / float(H * W), 6),
        "largest_component_px": areas[0] if areas else 0,
        "n_components_ge60": len(big),
        "boxes": boxes,
        "hit": bool(hit),
    }


def ink_mask(img: np.ndarray, thr: int = 150, drop_red: bool = False) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = (gray < thr).astype(np.uint8)
    if drop_red:
        ink[red_mask(img) > 0] = 0
    return ink


def line_masks(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    H, W = ink.shape
    # 先轻微纵向闭合，容忍倾斜造成的断线
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, W // 30), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, H // 45)))
    hm = cv2.morphologyEx(cv2.dilate(ink, np.ones((3, 1), np.uint8)), cv2.MORPH_OPEN, hk)
    vm = cv2.morphologyEx(cv2.dilate(ink, np.ones((1, 3), np.uint8)), cv2.MORPH_OPEN, vk)
    return hm, vm


def estimate_skew(hm: np.ndarray) -> float:
    """用长横线估计倾斜角（度，正值=顺时针下倾 y 随 x 增大）。没有长横线返回 0。"""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(hm, 8)
    angles, weights = [], []
    H, W = hm.shape
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if w < W * 0.2 or h > 60:
            continue
        ys, xs = np.where(lab[y:y + h, x:x + w] == i)
        if len(xs) < 50:
            continue
        k, b = np.polyfit(xs.astype(float), ys.astype(float), 1)
        angles.append(math.degrees(math.atan(k)))
        weights.append(w)
    if not angles:
        return 0.0
    return float(np.average(angles, weights=weights))


def table_regions(img: np.ndarray) -> list[dict]:
    ink = ink_mask(img, thr=185)
    hm, vm = line_masks(ink)
    H, W = ink.shape
    grid = cv2.dilate(hm | vm, np.ones((9, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(grid, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        if w < W * 0.4 or h < 60:
            continue
        sub_h = hm[y:y + h, x:x + w]
        sub_v = vm[y:y + h, x:x + w]
        # 横线条数：按行投影聚类
        rows = np.where(sub_h.sum(axis=1) > w * 0.15)[0]
        n_h = 0 if len(rows) == 0 else 1 + int((np.diff(rows) > 6).sum())
        cols = np.where(sub_v.sum(axis=0) > h * 0.15)[0]
        n_v = 0 if len(cols) == 0 else 1 + int((np.diff(cols) > 6).sum())
        if n_h >= 2 and n_v >= 2:
            out.append({"bbox": [x, y, x + w, y + h], "n_hlines": n_h, "n_vlines": n_v})
    out.sort(key=lambda r: r["bbox"][1])
    return out


def text_segments(img: np.ndarray, *, gap_px: int = 22, seal_boxes: list | None = None) -> list[dict]:
    """本地文字行框（行内连续墨迹段）。去掉表格线，水平膨胀后取连通域；落在印章框内的段标为 seal。"""
    ink = ink_mask(img, thr=150)
    hm, vm = line_masks(ink_mask(img, thr=185))
    lines = cv2.dilate(hm | vm, np.ones((3, 3), np.uint8))
    txt = ink.copy()
    txt[lines > 0] = 0
    txt = cv2.morphologyEx(txt, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))  # 去噪点
    dil = cv2.dilate(txt, cv2.getStructuringElement(cv2.MORPH_RECT, (gap_px, 1)))
    dil = cv2.dilate(dil, np.ones((3, 1), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dil, 8)
    H, W = ink.shape
    segs = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        if h < 10 or w < 12 or h > 160:
            continue
        inkpx = int(txt[y:y + h, x:x + w][lab[y:y + h, x:x + w] == i].sum())
        if inkpx < 60:
            continue
        seg = {"bbox": [x, y, x + w, y + h], "ink_px": inkpx, "in_seal": False}
        for sb in seal_boxes or []:
            ix = max(0, min(x + w, sb[2]) - max(x, sb[0]))
            iy = max(0, min(y + h, sb[3]) - max(y, sb[1]))
            if ix * iy >= 0.5 * w * h:
                seg["in_seal"] = True
        segs.append(seg)
    segs.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
    for k, s in enumerate(segs, 1):
        s["id"] = f"seg-{k:03d}"
    return segs, txt


def route_blind20(img: np.ndarray) -> dict[str, Any]:
    seal = seal_stats(img)
    tables = table_regions(img)
    reasons = []
    if tables:
        reasons.append(f"table_lines:{len(tables)}")
    if seal["hit"]:
        reasons.append(f"red_seal:{seal['red_px']}px")
    if tables and seal["hit"]:
        cls = "table_and_seal"
    elif tables:
        cls = "table"
    elif seal["hit"]:
        cls = "seal"
    else:
        cls = "body"
    api = "accurate" if cls != "body" else "general"
    return {"page_class": cls, "api": api, "reasons": reasons or ["no_table_no_seal->body"],
            "seal": seal, "tables": tables}
