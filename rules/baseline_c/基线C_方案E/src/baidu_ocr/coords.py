"""PDF point 坐标（左上原点）与渲染像素坐标互转。pt = px × 72 / render_dpi。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CoordMapper:
    page_x0: float
    page_y0: float
    page_x1: float
    page_y1: float
    zoom: float
    width_px: int
    height_px: int
    dpi: float

    @property
    def width_pdf(self) -> float:
        return self.page_x1 - self.page_x0

    @property
    def height_pdf(self) -> float:
        return self.page_y1 - self.page_y0

    def pdf_to_pixel(self, bbox: list[float] | tuple[float, ...]) -> list[float]:
        x0, y0, x1, y1 = bbox
        return [
            (x0 - self.page_x0) * self.zoom,
            (y0 - self.page_y0) * self.zoom,
            (x1 - self.page_x0) * self.zoom,
            (y1 - self.page_y0) * self.zoom,
        ]

    def pixel_to_pdf(self, bbox: list[float] | tuple[float, ...]) -> list[float]:
        x0, y0, x1, y1 = bbox
        return [
            x0 / self.zoom + self.page_x0,
            y0 / self.zoom + self.page_y0,
            x1 / self.zoom + self.page_x0,
            y1 / self.zoom + self.page_y0,
        ]

    def pixel_to_pt(self, bbox: list[float] | tuple[float, ...]) -> list[float]:
        k = 72.0 / self.dpi
        return [round(float(v) * k, 2) for v in bbox[:4]]

    def pack(self, pdf: list[float], pixel: list[float] | None = None) -> dict:
        if pixel is None:
            pixel = self.pdf_to_pixel(pdf)
        return {
            "pdf": [round(v, 4) for v in pdf],
            "pixel": [round(v, 2) for v in pixel],
            "origin": "top-left",
        }
