#!/usr/bin/env python3
"""本地裁图核实：从 blind20-scan.pdf 按 200DPI 像素框（或比例框）裁剪放大，存 PNG。不外发。"""
import sys
import pymupdf
from pathlib import Path
PDF = "/workspace/pdf-test/frozen20/blind20-scan.pdf"
OUT = Path("/workspace/pdf-test/baidu/output/compare/verify")


def crop(page, x0, y0, x1, y1, name, zoom=300 / 72):
    """x/y 为 200DPI 渲染像素坐标。"""
    doc = pymupdf.open(PDF)
    pg = doc[page - 1]
    f = 72 / 200
    clip = pymupdf.Rect(x0 * f, y0 * f, x1 * f, y1 * f)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.png"
    pix.save(str(p))
    return p


if __name__ == "__main__":
    a = sys.argv[1:]
    print(crop(int(a[0]), *map(float, a[1:5]), a[5]))
