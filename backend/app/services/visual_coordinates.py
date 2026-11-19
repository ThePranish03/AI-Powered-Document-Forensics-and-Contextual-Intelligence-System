"""Pixel-to-PDF coordinate mapping for rendered visual-analysis artifacts.

Conventions:
- Rendered rasters use a top-left origin, x right, y down.
- DocForensIQ reports page geometry in PyMuPDF page points, which share the displayed
  (rotation-applied) top-left origin space with `page.get_pixmap()`, so a uniform
  scale plus the page-rect origin covers rotated pages as well as crop-box offsets.
- Mapping is versioned so persisted region coordinates stay interpretable.
"""
from __future__ import annotations

from dataclasses import dataclass

MAPPING_VERSION = "pixel_to_pdf_v1"


@dataclass(frozen=True)
class RenderGeometry:
    page_width_points: float
    page_height_points: float
    render_width_px: int
    render_height_px: int
    origin_x_points: float = 0.0
    origin_y_points: float = 0.0
    page_rotation: int = 0

    def __post_init__(self) -> None:
        if self.render_width_px <= 0 or self.render_height_px <= 0:
            raise ValueError("Rendered pixel dimensions must be positive")
        if self.page_width_points <= 0 or self.page_height_points <= 0:
            raise ValueError("Page point dimensions must be positive")
        if self.page_rotation not in {0, 90, 180, 270}:
            raise ValueError("Page rotation must be 0, 90, 180 or 270")

    @property
    def scale_x(self) -> float:
        return self.page_width_points / self.render_width_px

    @property
    def scale_y(self) -> float:
        return self.page_height_points / self.render_height_px

    def pixel_to_pdf_point(self, pixel_x: float, pixel_y: float) -> tuple[float, float]:
        return (self.origin_x_points + pixel_x * self.scale_x, self.origin_y_points + pixel_y * self.scale_y)

    def pdf_point_to_pixel(self, point_x: float, point_y: float) -> tuple[float, float]:
        return ((point_x - self.origin_x_points) / self.scale_x, (point_y - self.origin_y_points) / self.scale_y)

    def box_pixel_to_pdf(self, box: dict) -> dict:
        x0, y0 = self.pixel_to_pdf_point(float(box["x0"]), float(box["y0"]))
        x1, y1 = self.pixel_to_pdf_point(float(box["x1"]), float(box["y1"]))
        return {"x0": round(x0, 2), "y0": round(y0, 2), "x1": round(x1, 2), "y1": round(y1, 2)}

    def box_pdf_to_pixel(self, box: dict) -> dict:
        x0, y0 = self.pdf_point_to_pixel(float(box["x0"]), float(box["y0"]))
        x1, y1 = self.pdf_point_to_pixel(float(box["x1"]), float(box["y1"]))
        return {"x0": round(x0, 2), "y0": round(y0, 2), "x1": round(x1, 2), "y1": round(y1, 2)}

    def to_metadata(self) -> dict:
        return {
            "mapping_version": MAPPING_VERSION,
            "page_width_points": self.page_width_points,
            "page_height_points": self.page_height_points,
            "render_width_px": self.render_width_px,
            "render_height_px": self.render_height_px,
            "origin_x_points": self.origin_x_points,
            "origin_y_points": self.origin_y_points,
            "page_rotation": self.page_rotation,
        }


def geometry_from_page(page, render_width_px: int, render_height_px: int) -> RenderGeometry:
    """Build geometry from an open PyMuPDF page and its rendered pixel size.

    `page.rect` is the displayed page rectangle (rotation applied, top-left origin),
    which is exactly the space the pixmap renders into.
    """
    rect = page.rect
    return RenderGeometry(
        page_width_points=float(rect.width),
        page_height_points=float(rect.height),
        render_width_px=int(render_width_px),
        render_height_px=int(render_height_px),
        origin_x_points=float(rect.x0),
        origin_y_points=float(rect.y0),
        page_rotation=getattr(page, "rotation", 0) or 0,
    )
