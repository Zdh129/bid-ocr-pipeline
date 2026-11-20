"""用 PyMuPDF 按测试 DPI（默认 200）把指定页渲染成 PNG。"""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf

from .coords import CoordMapper


def render_pages(
    pdf_path: str | Path,
    out_dir: str | Path,
    dpi: float,
    zoom: float,
    pages: list[int] | None = None,
) -> list[dict]:
    """pages 为 1-based 页码；None 表示全部页。只写指定页的 PNG。"""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf_path)
    try:
        matrix = pymupdf.Matrix(zoom, zoom)
        wanted = set(int(p) for p in pages) if pages is not None else None
        result = []
        for i, page in enumerate(doc):
            page_no = i + 1
            if wanted is not None and page_no not in wanted:
                continue
            t0 = time.perf_counter()
            pix = page.get_pixmap(matrix=matrix, alpha=False, annots=True)
            image_path = out_dir / f"page-{page_no:04d}.png"
            pix.save(str(image_path))
            elapsed = time.perf_counter() - t0
            rect = page.rect
            mapper = CoordMapper(
                page_x0=float(rect.x0),
                page_y0=float(rect.y0),
                page_x1=float(rect.x1),
                page_y1=float(rect.y1),
                zoom=zoom,
                width_px=int(pix.width),
                height_px=int(pix.height),
                dpi=float(dpi),
            )
            result.append(
                {
                    "page": page_no,
                    "image_ref": str(image_path),
                    "width_px": int(pix.width),
                    "height_px": int(pix.height),
                    "width_pdf": mapper.width_pdf,
                    "height_pdf": mapper.height_pdf,
                    "rotation": int(page.rotation),
                    "render_sec": elapsed,
                    "mapper": mapper,
                    "page_count": int(doc.page_count),
                }
            )
        return result
    finally:
        doc.close()
