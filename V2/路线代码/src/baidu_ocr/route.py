"""方案 A 页面分流：正文走标准含位置版，表格/带章页走高精度含位置版。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _find_tables_count(page) -> int:
    try:
        tabs = page.find_tables()
        return len(tabs.tables) if tabs else 0
    except Exception:  # noqa: BLE001
        return 0


def _red_seal_stats(image: Image.Image, cfg: dict) -> dict:
    """在 HSV 空间检测红色连通域，用于印章页判断。"""
    route = cfg.get("route") or {}
    arr = np.asarray(image.convert("RGB"))
    # PIL HSV: H 0-255, S 0-255, V 0-255. 红色在 H 两端。
    hsv = np.asarray(image.convert("HSV"))
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # 约 0-15° 与 345-360°；PIL H 360°≈255
    red = (
        ((h <= 12) | (h >= 230))
        & (s >= 80)
        & (v >= 50)
        & (v <= 250)
    )
    ratio = float(red.mean()) if red.size else 0.0
    # 简易连通域（4 邻域 flood），只关心最大块面积
    min_px = int(route.get("seal_min_component_px") or 400)
    mask = red.astype(np.uint8)
    hgt, wdt = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    max_area = 0
    n_big = 0
    # 采样扫描，避免纯 Python 全图 flood 过慢：先用连续行游程近似
    # 用标签的粗实现：对掩膜做膨胀后算连通
    from numpy.lib.stride_tricks import as_strided  # noqa: F401

    # 迭代栈式连通域只跑红色像素；若过多则抽样
    coords = np.argwhere(mask)
    if len(coords) > 200_000:
        # 像素极多时只看比例，不再算连通
        return {
            "red_ratio": ratio,
            "max_component_px": None,
            "max_component_ratio": None,
            "n_big_components": None,
            "hit": ratio >= float(route.get("seal_red_ratio_min") or 0.0015),
            "method": "ratio_only",
        }
    # 两遍扫描的粗连通：用 scipy 没有则手写
    labels = np.zeros_like(mask, dtype=np.int32)
    label = 0
    areas: dict[int, int] = {}
    # 行扫描 union-find 会更长；这里对红色点 BFS，限制组件数
    for y, x in coords:
        if visited[y, x]:
            continue
        label += 1
        stack = [(int(y), int(x))]
        visited[y, x] = True
        area = 0
        while stack:
            cy, cx = stack.pop()
            area += 1
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < hgt and 0 <= nx < wdt and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        areas[label] = area
        if area > max_area:
            max_area = area
        if area >= min_px:
            n_big += 1
        if label >= 400:
            break
    page_area = max(hgt * wdt, 1)
    max_ratio = max_area / page_area
    hit = (ratio >= float(route.get("seal_red_ratio_min") or 0.0015)) and (
        max_ratio >= float(route.get("seal_min_component_ratio") or 0.0004) or max_area >= min_px
    )
    return {
        "red_ratio": round(ratio, 6),
        "max_component_px": int(max_area),
        "max_component_ratio": round(max_ratio, 6),
        "n_big_components": n_big,
        "hit": bool(hit),
        "method": "connected_red",
    }


def _line_density(image: Image.Image, cfg: dict) -> dict:
    route = cfg.get("route") or {}
    gray = np.asarray(image.convert("L"))
    # 文字/表格线偏暗
    binary = gray < 80
    h, w = binary.shape
    min_len_h = max(8, int(w * float(route.get("line_min_length_ratio") or 0.25)))
    min_len_v = max(8, int(h * float(route.get("line_min_length_ratio") or 0.25)))
    # 横线：每行连续 True 的最长段
    h_pixels = 0
    for row in binary:
        # 游程
        changes = np.diff(row.astype(np.int8), prepend=0, append=0)
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        for s, e in zip(starts, ends):
            length = int(e - s)
            if length >= min_len_h:
                h_pixels += length
    v_pixels = 0
    for col in binary.T:
        changes = np.diff(col.astype(np.int8), prepend=0, append=0)
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        for s, e in zip(starts, ends):
            length = int(e - s)
            if length >= min_len_v:
                v_pixels += length
    dens = (h_pixels + v_pixels) / max(h * w, 1)
    hit = dens >= float(route.get("line_density_min") or 0.012)
    return {
        "h_line_pixels": int(h_pixels),
        "v_line_pixels": int(v_pixels),
        "line_density": round(dens, 6),
        "hit": bool(hit),
    }


def route_page(
    *,
    page,
    image_path: str | Path | None,
    cfg: dict,
    text_layer_info: dict | None = None,
) -> dict[str, Any]:
    """返回分流结果。不把页面正文写入原因以外的字段。"""
    route_cfg = cfg.get("route") or {}
    reasons: list[str] = []
    details: dict[str, Any] = {}
    n_tables = 0
    if route_cfg.get("find_tables", True) and page is not None:
        n_tables = _find_tables_count(page)
        details["n_tables"] = n_tables
        if n_tables >= int(route_cfg.get("min_tables") or 1):
            reasons.append(f"find_tables:{n_tables}")

    image = None
    if image_path and Path(image_path).exists():
        image = Image.open(image_path)

    if image is not None:
        seal = _red_seal_stats(image, cfg)
        details["seal"] = {k: v for k, v in seal.items() if k != "mask"}
        if seal.get("hit"):
            reasons.append("red_seal_pixels")
        lines = _line_density(image, cfg)
        details["lines"] = lines
        if lines.get("hit"):
            reasons.append("line_density")

    # 页面类型
    page_class = "body"
    if any(r.startswith("find_tables") or r == "line_density" for r in reasons):
        page_class = "table"
    if "red_seal_pixels" in reasons:
        page_class = "seal" if page_class == "body" else "table_and_seal"

    use_text_layer = bool((cfg.get("text_layer") or {}).get("enabled", True)) and bool(
        (text_layer_info or {}).get("usable")
    )
    if use_text_layer:
        api = None
        path = "text_layer"
        reasons = ["usable_text_layer"] + reasons
    elif page_class in {"table", "seal", "table_and_seal"}:
        api = route_cfg.get("table_or_seal_api") or "accurate"
        path = "ocr_accurate" if api == "accurate" else f"ocr_{api}"
    else:
        api = route_cfg.get("body_api") or "general"
        path = "ocr_general" if api == "general" else f"ocr_{api}"
        reasons = reasons or ["default_body"]

    return {
        "page_class": page_class,
        "path": path,
        "api": api,
        "reasons": reasons,
        "details": details,
        "use_text_layer": use_text_layer,
    }
