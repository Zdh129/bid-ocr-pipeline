#!/usr/bin/env python3
"""synth18 预处理变体（纯本地，不外发）。

render   synth18-scan.pdf → 300 DPI PNG（output/synth18_opt/preproc/orig/pNN.png）
variants 生成 P1/P2/P3 及组合：
  P1  自适应二值化（高斯，block 41, C 15）+ 2×2 开运算去噪点（在反相图上开运算，去掉孤立小黑点）
  P2  HSV 红色分离去印章：H∈[0,12]∪[155,180]、S≥50、V≥70 的像素 → 白（掩膜膨胀 1 次）；黑字（V 低）不在掩膜里
  P3  表格线：反相二值图上用长横核（≥ 宽/30）和长竖核（≥ 高/40）开运算提线，膨胀 1 次后置白
  组合按 P2 → P3 → P1 顺序（先在彩色上去章，再去线，最后二值化）
stats    每个变体的图像统计：去章误伤（非红区深色像素被抹掉的比例）、红色残留、去线像素量等
thumbs   对比缩略图（每页一张：orig | 各变体）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PDF = Path("/workspace/pdf-test/synth18/synth18-scan.pdf")
OUT = ROOT / "output" / "synth18_opt" / "preproc"
DPI = 300
VARIANTS = ["P1", "P2", "P3", "P2+P3", "P1+P2", "P1+P3", "P1+P2+P3"]


def red_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = (((h <= 12) | (h >= 155)) & (s >= 50) & (v >= 70)).astype(np.uint8) * 255
    return cv2.dilate(m, np.ones((3, 3), np.uint8), 1)


def p2(bgr):
    """红章像素换成其 R 通道灰度：红墨在 R 通道接近纸色，被章压住的黑字在 R 通道仍是深色（不误伤）。"""
    out = bgr.copy()
    m = red_mask(bgr) > 0
    r = bgr[..., 2]
    out[m] = np.repeat(r[m][:, None], 3, axis=1)
    return out


def line_mask(gray):
    inv = 255 - cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 15)
    h, w = gray.shape
    hk = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(60, w // 30), 1)))
    vk = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(60, h // 40))))
    return cv2.dilate(hk | vk, np.ones((3, 3), np.uint8), 1)


def p3(bgr):
    out = bgr.copy()
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    paper = np.median(bgr.reshape(-1, 3), axis=0).astype(np.uint8)
    out[line_mask(g) > 0] = paper  # 用纸色填，不用纯白（避免白边）
    return out


def p1(bgr):
    g = cv2.medianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 3)
    b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 20)
    inv = cv2.morphologyEx(255 - b, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, 8)
    small = np.where(st[:, cv2.CC_STAT_AREA] < 12)[0]
    inv[np.isin(lab, small[small > 0])] = 0
    return cv2.cvtColor(255 - inv, cv2.COLOR_GRAY2BGR)


def apply(name, bgr):
    x = bgr
    if "P2" in name:
        x = p2(x)
    if "P3" in name:
        x = p3(x)
    if "P1" in name:
        x = p1(x)
    return x


def render(a):
    import fitz
    d = fitz.open(PDF)
    (OUT / "orig").mkdir(parents=True, exist_ok=True)
    for i, pg in enumerate(d):
        pix = pg.get_pixmap(dpi=DPI)
        pix.save(OUT / "orig" / f"p{i + 1:02d}.png")
    print("rendered", len(d))


def vdir(name):
    return OUT / name.replace("+", "_")


def variants(a):
    for f in sorted((OUT / "orig").glob("p*.png")):
        bgr = cv2.imread(str(f))
        for v in VARIANTS:
            vdir(v).mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(vdir(v) / f.name), apply(v, bgr), [cv2.IMWRITE_PNG_COMPRESSION, 6])
    print("variants done")


def stats(a):
    res = {}
    for f in sorted((OUT / "orig").glob("p*.png")):
        bgr = cv2.imread(str(f))
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        rm = red_mask(bgr) > 0
        lm = line_mask(g) > 0
        hsv0 = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        dark = (hsv0[..., 2] < 110) & (hsv0[..., 1] < 90)  # 黑字像素（暗且不饱和；排除红章本身）
        # 红区附近（膨胀 15px）以外的深色像素：P2 不应改动；红区内的深色像素：被章压住的黑字，看 P2 是否保留
        near = cv2.dilate(rm.astype(np.uint8), np.ones((31, 31), np.uint8)) > 0
        p2img = cv2.cvtColor(p2(bgr), cv2.COLOR_BGR2GRAY)
        # 「被抹掉」= 原来是黑字、P2 后亮度 > 160
        under = dark & near
        r = {"red_px": int(rm.sum()), "line_px": int(lm.sum()), "dark_px": int(dark.sum()),
             "p2_dark_removed_outside_seal": int((dark & ~near & (p2img > 160)).sum()),
             "p2_dark_under_seal": int(under.sum()), "p2_dark_under_seal_removed": int((under & (p2img > 160)).sum()),
             "p3_dark_removed": int((dark & lm).sum())}
        hsv = cv2.cvtColor(p2(bgr), cv2.COLOR_BGR2HSV)
        r["p2_red_left"] = int((((hsv[..., 0] <= 12) | (hsv[..., 0] >= 155)) & (hsv[..., 1] >= 50) & (hsv[..., 2] >= 70)).sum())
        res[f.stem] = r
    (OUT / "stats.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    for k, r in res.items():
        print(k, r)


def thumbs(a):
    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)
    for f in sorted((OUT / "orig").glob("p*.png")):
        ims = [cv2.imread(str(f))] + [cv2.imread(str(vdir(v) / f.name)) for v in VARIANTS]
        ims = [cv2.resize(i, (420, int(i.shape[0] * 420 / i.shape[1])), interpolation=cv2.INTER_AREA) for i in ims]
        for im, name in zip(ims, ["orig"] + VARIANTS):
            cv2.putText(im, name, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 0, 0), 2)
        cv2.imwrite(str(OUT / "thumbs" / f"{f.stem}.jpg"), np.hstack(ims), [cv2.IMWRITE_JPEG_QUALITY, 80])
    print("thumbs done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["render", "variants", "stats", "thumbs", "all"])
    a = ap.parse_args()
    for c in (["render", "variants", "stats", "thumbs"] if a.cmd == "all" else [a.cmd]):
        globals()[c](a)
