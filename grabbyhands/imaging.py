# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Screenshot pixels: Cairo surfaces for drawing, Pillow images for processing."""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass

import cairo
from PIL import Image


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def clamp(self, width: int, height: int) -> Rect:
        x0 = max(0, min(self.x, width))
        y0 = max(0, min(self.y, height))
        x1 = max(0, min(self.x + self.w, width))
        y1 = max(0, min(self.y + self.h, height))
        return Rect(x0, y0, x1 - x0, y1 - y0)

    @staticmethod
    def from_points(x0: float, y0: float, x1: float, y1: float) -> Rect:
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        return Rect(round(left), round(top), round(right - left), round(bottom - top))


def surface_to_pil(surface: cairo.ImageSurface) -> Image.Image:
    """Convert a Cairo ARGB32/RGB24 surface to an RGBA/RGB Pillow image."""
    surface.flush()
    w, h, stride = surface.get_width(), surface.get_height(), surface.get_stride()
    data = bytes(surface.get_data())
    little = sys.byteorder == "little"
    if surface.get_format() == cairo.FORMAT_RGB24:
        raw = "BGRX" if little else "XRGB"
        return Image.frombuffer("RGB", (w, h), data, "raw", raw, stride, 1).copy()
    raw = "BGRa" if little else "aRGB"
    return Image.frombuffer("RGBA", (w, h), data, "raw", raw, stride, 1).copy()


def pil_to_surface(image: Image.Image) -> cairo.ImageSurface:
    """Convert a Pillow image to a Cairo ARGB32 surface (premultiplied)."""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    little = sys.byteorder == "little"
    raw = rgba.tobytes("raw", "BGRa" if little else "aRGB")
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    if stride != w * 4:
        rows = [raw[i * w * 4 : (i + 1) * w * 4].ljust(stride, b"\0") for i in range(h)]
        raw = b"".join(rows)
    buf = bytearray(raw)
    return cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_ARGB32, w, h, stride)


def _to_argb32(surface: cairo.ImageSurface) -> cairo.ImageSurface:
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, surface.get_width(), surface.get_height())
    ctx = cairo.Context(out)
    ctx.set_operator(cairo.OPERATOR_SOURCE)
    ctx.set_source_surface(surface, 0, 0)
    ctx.paint()
    out.flush()
    return out


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class ScreenImage:
    """A frozen screenshot of all monitors, in physical pixels."""

    def __init__(self, surface: cairo.ImageSurface) -> None:
        if surface.get_format() not in (cairo.FORMAT_ARGB32, cairo.FORMAT_RGB24):
            # A 16-bit PNG (what an HDR desktop may hand out) loads as a floating-point
            # surface. Everything here reads 8-bit pixels, so convert once.
            surface = _to_argb32(surface)
        self.surface = surface
        self.width = surface.get_width()
        self.height = surface.get_height()
        self._data = surface.get_data()
        self._stride = surface.get_stride()

    @classmethod
    def from_file(cls, path: str) -> ScreenImage:
        return cls(cairo.ImageSurface.create_from_png(path))

    @classmethod
    def from_pil(cls, image: Image.Image) -> ScreenImage:
        return cls(pil_to_surface(image))

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        x = max(0, min(self.width - 1, int(x)))
        y = max(0, min(self.height - 1, int(y)))
        off = y * self._stride + x * 4
        value = int.from_bytes(bytes(self._data[off : off + 4]), sys.byteorder)
        return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF

    def crop(self, rect: Rect) -> Image.Image:
        rect = rect.clamp(self.width, self.height)
        sub = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(rect.w, 1), max(rect.h, 1))
        ctx = cairo.Context(sub)
        ctx.set_source_surface(self.surface, -rect.x, -rect.y)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.paint()
        return surface_to_pil(sub).convert("RGB")

    def full(self) -> Image.Image:
        return self.crop(Rect(0, 0, self.width, self.height))
