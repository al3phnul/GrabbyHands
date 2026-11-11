# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Render an annotation document over its screenshot.

Two layers: the base (the screenshot, never modified) and the *paint layer*, a transparent
surface every item draws into. Effects (blur, pixelate, highlighter, spotlight) paint with
pre-filtered copies of the base, so they live in the paint layer too, and the eraser, which
clears the paint layer along its path, can rub out any of them without touching the
screenshot.
"""

from __future__ import annotations

import logging
import math
from collections import OrderedDict
from pathlib import Path

import cairo
from PIL import Image, ImageChops, ImageFilter

from grabbyhands.colors import parse_hex, readable_text_on
from grabbyhands.editor import textmetrics
from grabbyhands.editor.document import (
    MAX_BLUR_RADIUS,
    MAX_PIXEL_BLOCK,
    MAX_STRENGTH,
    ArrowItem,
    CalloutItem,
    Document,
    EllipseItem,
    EraserItem,
    HighlighterItem,
    Item,
    LineItem,
    MagnifyItem,
    PenItem,
    RectItem,
    RedactItem,
    SpotlightItem,
    StepItem,
    StickerItem,
    TextItem,
)
from grabbyhands.imaging import pil_to_surface, surface_to_pil

log = logging.getLogger(__name__)

# 100 % is a MAX_PIXEL_BLOCK px block or a MAX_BLUR_RADIUS px blur. Only redactions from
# version-1 documents go beyond that (up to MAX_STRENGTH); the Strength slider ends at 100.
CACHE_SIZE = 256  # effect sources kept, at most
CACHE_BYTES = 256 * 1024 * 1024  # ...and their pixels (more for very large screenshots)


def pixel_block(percent: float) -> int:
    """Pixelate block size for a strength in percent: 1 px (no effect) at 0, and
    MAX_PIXEL_BLOCK at 100."""
    p = max(0.0, min(MAX_STRENGTH, float(percent))) / 100
    return max(1, round(1 + p * (MAX_PIXEL_BLOCK - 1)))


def blur_radius(percent: float) -> float:
    """Gaussian blur radius for a strength in percent (MAX_BLUR_RADIUS at 100)."""
    return max(0.0, min(MAX_STRENGTH, float(percent))) / 100 * MAX_BLUR_RADIUS


def _surface_bytes(value: object) -> int:
    surface = value[0] if isinstance(value, tuple) else value
    return surface.get_stride() * surface.get_height()


class EffectSources:
    """The base image plus cached filtered copies used as paint for effects."""

    def __init__(self, base: Image.Image) -> None:
        self.image = base.convert("RGB")
        self.width, self.height = self.image.size
        self.surface = pil_to_surface(self.image)
        self._cache: OrderedDict[tuple, object] = OrderedDict()
        self._cache_bytes = 0
        # Room for a few whole-image copies however big the screenshot is, but never an
        # open-ended pile of them (resizing one large blur box makes a new copy per step).
        self._budget = max(CACHE_BYTES, 3 * self.width * self.height * 4)

    def _cached(self, key: tuple, make):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        value = make()
        self._cache[key] = value
        self._cache_bytes += _surface_bytes(value)
        # the newest entry always stays: it is about to be drawn
        while len(self._cache) > 1 and (
            len(self._cache) > CACHE_SIZE or self._cache_bytes > self._budget
        ):
            _key, dropped = self._cache.popitem(last=False)
            self._cache_bytes -= _surface_bytes(dropped)
        return value

    def get(self, kind: str, param: float | str) -> cairo.ImageSurface:
        """A whole-image effect source (used by the highlighter)."""
        if kind != "highlight":
            raise ValueError(kind)

        def make() -> cairo.ImageSurface:
            r, g, b, _a = parse_hex(str(param))
            color = (round(r * 255), round(g * 255), round(b * 255))
            tint = Image.new("RGB", self.image.size, color)
            return pil_to_surface(ImageChops.multiply(self.image, tint))

        return self._cached((kind, param), make)

    def region(
        self, kind: str, percent: float, x: int, y: int, w: int, h: int
    ) -> tuple[cairo.ImageSurface, int, int]:
        """Pixelated or blurred copy of just one box: (surface, left, top)."""

        def make():
            if kind == "pixelate":
                box = (x, y, x + w, y + h)
                crop = self.image.crop(box)
                block = pixel_block(percent)
                if block > 1:
                    small = crop.resize(
                        (max(1, math.ceil(w / block)), max(1, math.ceil(h / block))),
                        Image.Resampling.BOX,
                    )
                    crop = small.resize(
                        (small.width * block, small.height * block), Image.Resampling.NEAREST
                    ).crop((0, 0, w, h))
                return pil_to_surface(crop), x, y
            if kind == "blur":
                radius = blur_radius(percent)
                margin = math.ceil(radius * 3)
                left, top = max(0, x - margin), max(0, y - margin)
                right, bottom = min(self.width, x + w + margin), min(self.height, y + h + margin)
                crop = self.image.crop((left, top, right, bottom))
                if radius > 0:
                    crop = crop.filter(ImageFilter.GaussianBlur(radius))
                return pil_to_surface(crop), left, top
            raise ValueError(kind)

        return self._cached((kind, round(float(percent), 1), x, y, w, h), make)


# -- primitives ------------------------------------------------------------------------
def _set_color(ctx: cairo.Context, color: str, opacity: float = 1.0) -> None:
    r, g, b, a = parse_hex(color)
    ctx.set_source_rgba(r, g, b, a * opacity)


def _rounded(ctx: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def _ellipse_path(ctx: cairo.Context, x: float, y: float, w: float, h: float) -> None:
    if w <= 0 or h <= 0:
        return
    ctx.save()
    ctx.translate(x + w / 2, y + h / 2)
    ctx.scale(w / 2, h / 2)
    ctx.arc(0, 0, 1, 0, 2 * math.pi)
    ctx.restore()


def _polyline(ctx: cairo.Context, points: list[list[float]]) -> None:
    ctx.move_to(*points[0])
    if len(points) == 1:
        ctx.line_to(points[0][0] + 0.01, points[0][1])
    for p in points[1:]:
        ctx.line_to(*p)


def _stroke_style(ctx: cairo.Context, width: float) -> None:
    ctx.set_line_width(width)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)


def _text_with_outline(ctx, lay, x: float, y: float, color: str, size: float) -> None:
    """Text in ``color`` with a thin contrasting outline so it reads on any background."""
    from gi.repository import PangoCairo

    r, g, b, _a = parse_hex(color)
    outline = readable_text_on((round(r * 255), round(g * 255), round(b * 255)))
    ctx.move_to(x, y)
    PangoCairo.layout_path(ctx, lay)
    ctx.set_source_rgba(*outline, 0.85)
    ctx.set_line_width(max(1.5, size / 9))
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.stroke_preserve()
    _set_color(ctx, color)
    ctx.fill()


# -- item drawers ---------------------------------------------------------------------
def _draw_line(ctx: cairo.Context, item: LineItem, _src: EffectSources) -> None:
    _set_color(ctx, item.color, item.opacity)
    _stroke_style(ctx, item.width)
    x0, y0, x1, y1 = item.x0, item.y0, item.x1, item.y1
    if isinstance(item, ArrowItem) and item.length > 0:
        head = max(12.0, item.width * 4)
        angle = math.atan2(y1 - y0, x1 - x0)
        head = min(head, item.length)
        # stop the shaft inside the head so the round cap doesn't poke through the tip
        sx, sy = x1 - math.cos(angle) * head * 0.6, y1 - math.sin(angle) * head * 0.6
        ctx.move_to(x0, y0)
        ctx.line_to(sx, sy)
        ctx.stroke()
        spread = math.radians(24)
        ctx.move_to(x1, y1)
        ctx.line_to(x1 - head * math.cos(angle - spread), y1 - head * math.sin(angle - spread))
        ctx.line_to(x1 - head * math.cos(angle + spread), y1 - head * math.sin(angle + spread))
        ctx.close_path()
        ctx.fill()
        return
    ctx.move_to(x0, y0)
    ctx.line_to(x1, y1)
    ctx.stroke()


def _draw_shape(ctx: cairo.Context, item: RectItem | EllipseItem, _src: EffectSources) -> None:
    x, y, w, h = item.rect()
    is_ellipse = isinstance(item, EllipseItem)

    def make_path() -> None:
        if is_ellipse:
            _ellipse_path(ctx, x, y, w, h)
        else:
            ctx.rectangle(x, y, w, h)

    if item.fill in ("translucent", "solid"):
        make_path()
        _set_color(ctx, item.color, item.opacity * (0.28 if item.fill == "translucent" else 1))
        ctx.fill()
    make_path()
    _set_color(ctx, item.color, item.opacity)
    _stroke_style(ctx, item.width)
    ctx.set_line_join(cairo.LINE_JOIN_MITER)
    ctx.stroke()


def _draw_pen(ctx: cairo.Context, item: PenItem, _src: EffectSources) -> None:
    if not item.points:
        return
    _polyline(ctx, item.points)
    _set_color(ctx, item.color, item.opacity)
    _stroke_style(ctx, item.width)
    ctx.stroke()


def _draw_highlighter(ctx: cairo.Context, item: HighlighterItem, src: EffectSources) -> None:
    if not item.points:
        return
    _polyline(ctx, item.points)
    ctx.set_source_surface(src.get("highlight", item.color), 0, 0)
    _stroke_style(ctx, item.width)
    ctx.set_line_cap(cairo.LINE_CAP_SQUARE)
    ctx.stroke()


def _draw_eraser(ctx: cairo.Context, item: EraserItem, _src: EffectSources) -> None:
    if not item.points:
        return
    ctx.set_operator(cairo.OPERATOR_CLEAR)
    _polyline(ctx, item.points)
    _stroke_style(ctx, item.width)
    ctx.stroke()


def _draw_redact(ctx: cairo.Context, item: RedactItem, src: EffectSources) -> None:
    x, y, w, h = item.rect()
    if w <= 0 or h <= 0:
        return
    # Whole pixels, clamped to the image: the cached region matches what's drawn, and no
    # pixel at the edge of a box is left half covered.
    x0, y0 = max(0, math.floor(x)), max(0, math.floor(y))
    x1, y1 = min(src.width, math.ceil(x + w)), min(src.height, math.ceil(y + h))
    if x1 <= x0 or y1 <= y0:
        return
    ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
    if item.mode in ("pixelate", "blur"):
        surface, left, top = src.region(item.mode, item.strength, x0, y0, x1 - x0, y1 - y0)
        ctx.set_source_surface(surface, left, top)
    else:  # "solid", and any mode this version doesn't know: hide it completely
        ctx.set_source_rgb(0, 0, 0)
    ctx.fill()


def _draw_spotlights(ctx: cairo.Context, spots: list[SpotlightItem], w: int, h: int) -> None:
    """One dim pass for all spotlights (their union stays bright, nothing double-dims)."""
    dim = max(s.dim for s in spots)
    ctx.push_group()
    ctx.set_source_rgba(0, 0, 0, dim)
    ctx.paint()
    ctx.set_operator(cairo.OPERATOR_CLEAR)
    for spot in spots:
        x, y, sw, sh = spot.rect()
        if spot.shape == "ellipse":
            _ellipse_path(ctx, x, y, sw, sh)
        else:
            ctx.rectangle(x, y, sw, sh)
        ctx.fill()
    ctx.pop_group_to_source()
    ctx.paint()


def _draw_magnify(ctx: cairo.Context, item: MagnifyItem, src: EffectSources) -> None:
    x, y, w, h = item.rect()
    if w <= 0 or h <= 0:
        return
    cx, cy = x + w / 2, y + h / 2
    tx, ty, r = item.target_x, item.target_y, item.radius()
    _set_color(ctx, item.color, item.opacity)
    ctx.set_line_width(max(1.5, item.width * 0.6))
    ctx.rectangle(x, y, w, h)
    ctx.stroke()
    dist = math.hypot(tx - cx, ty - cy)
    if dist > r:
        ex, ey = tx - (tx - cx) * r / dist, ty - (ty - cy) * r / dist
        ctx.move_to(cx, cy)
        ctx.line_to(ex, ey)
        ctx.stroke()
    ctx.save()
    ctx.arc(tx, ty, r, 0, 2 * math.pi)
    ctx.clip()
    ctx.translate(tx, ty)
    ctx.scale(item.zoom, item.zoom)
    ctx.translate(-cx, -cy)
    ctx.set_source_surface(src.surface, 0, 0)
    ctx.get_source().set_filter(cairo.FILTER_GOOD)
    ctx.paint()
    ctx.restore()
    ctx.arc(tx, ty, r, 0, 2 * math.pi)
    _set_color(ctx, item.color, item.opacity)
    ctx.set_line_width(item.width)
    ctx.stroke()


def _draw_text(ctx: cairo.Context, item: TextItem, _src: EffectSources) -> None:
    if not item.text:
        return
    lay = textmetrics.layout(ctx, item.text, item.size)
    _text_with_outline(ctx, lay, item.x, item.y, item.color, item.size)


def _draw_callout(ctx: cairo.Context, item: CalloutItem, _src: EffectSources) -> None:
    x, y, w, h = item.rect()
    if w <= 0 or h <= 0:
        return
    radius = min(16.0, h / 3)
    cx, cy = x + w / 2, y + h / 2
    # tail: a triangle from the bubble's centre region to the tail point
    angle = math.atan2(item.tail_y - cy, item.tail_x - cx)
    base = min(w, h) * 0.22
    px, py = -math.sin(angle) * base, math.cos(angle) * base
    _rounded(ctx, x, y, w, h, radius)
    ctx.move_to(cx + px, cy + py)
    ctx.line_to(item.tail_x, item.tail_y)
    ctx.line_to(cx - px, cy - py)
    ctx.close_path()
    _set_color(ctx, item.color, item.opacity)
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    ctx.fill()
    if item.text:
        r, g, b, _a = parse_hex(item.color)
        fg = readable_text_on((round(r * 255), round(g * 255), round(b * 255)))
        pad = max(8.0, item.size * 0.5)
        lay = textmetrics.layout(ctx, item.text, item.size, max(10.0, w - 2 * pad))
        _tw, th = textmetrics.measure(item.text, item.size, max(10.0, w - 2 * pad))
        ctx.save()
        ctx.rectangle(x, y, w, h)
        ctx.clip()
        from gi.repository import PangoCairo

        ctx.move_to(x + pad, y + max(pad, (h - th) / 2))
        ctx.set_source_rgb(*fg)
        PangoCairo.show_layout(ctx, lay)
        ctx.restore()


def _draw_step(ctx: cairo.Context, item: StepItem, _src: EffectSources) -> None:
    from gi.repository import PangoCairo

    r = item.radius
    ctx.arc(item.x, item.y, r, 0, 2 * math.pi)
    _set_color(ctx, item.color, item.opacity)
    ctx.fill_preserve()
    ctx.set_source_rgba(1, 1, 1, 0.9)
    ctx.set_line_width(max(1.5, r * 0.12))
    ctx.stroke()
    text = str(item.number)
    pr, pg, pb, _a = parse_hex(item.color)
    ctx.set_source_rgb(*readable_text_on((round(pr * 255), round(pg * 255), round(pb * 255))))
    lay = textmetrics.layout(ctx, text, item.size)
    tw, th = textmetrics.measure(text, item.size)
    ctx.move_to(item.x - tw / 2, item.y - th / 2)
    PangoCairo.show_layout(ctx, lay)


def _draw_sticker(ctx: cairo.Context, item: StickerItem, _src: EffectSources) -> None:
    from gi.repository import PangoCairo

    x, y, w, h = item.rect()
    if w <= 1 or h <= 1 or not item.text:
        return
    size = h * 0.8
    tw, th = textmetrics.measure(item.text, size)
    if tw > w and tw > 0:
        size *= w / tw
        tw, th = textmetrics.measure(item.text, size)
    lay = textmetrics.layout(ctx, item.text, size)
    ctx.move_to(x + (w - tw) / 2, y + (h - th) / 2)
    _set_color(ctx, item.color, item.opacity)  # stamps use it; colour emoji ignore it
    PangoCairo.show_layout(ctx, lay)


DRAWERS = {
    "line": _draw_line,
    "arrow": _draw_line,
    "rect": _draw_shape,
    "ellipse": _draw_shape,
    "pen": _draw_pen,
    "highlighter": _draw_highlighter,
    "eraser": _draw_eraser,
    "redact": _draw_redact,
    "magnify": _draw_magnify,
    "text": _draw_text,
    "callout": _draw_callout,
    "step": _draw_step,
    "sticker": _draw_sticker,
}


# -- layers ---------------------------------------------------------------------------
def render_layer(
    doc: Document,
    sources: EffectSources,
    extra: Item | None = None,
    hidden: str | None = None,
) -> cairo.ImageSurface:
    """The paint layer: every item (plus an in-progress ``extra``), minus ``hidden``."""
    w, h = sources.width, sources.height
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surface)
    items = [i for i in doc.items if i.id != hidden]
    if extra is not None:
        items.append(extra)
    spots = [i for i in items if isinstance(i, SpotlightItem)]
    spots_drawn = False
    for item in items:
        # Every item starts with an empty path. (save/restore doesn't cover the path: text
        # leaves a current point behind, and the next circle would start with a line from it.)
        ctx.new_path()
        if isinstance(item, SpotlightItem):
            if not spots_drawn:
                _draw_spotlights(ctx, spots, w, h)
                spots_drawn = True
            continue
        drawer = DRAWERS.get(item.type)
        if drawer is None:
            continue
        ctx.save()
        try:
            drawer(ctx, item, sources)
        except Exception as err:  # noqa: BLE001 - one bad item must not undo the others
            # Never the item's content (text, positions) in the log: only what kind it was.
            log.warning("could not draw a %s item: %s", item.type, type(err).__name__)
            ctx.restore()
            ctx.save()
            ctx.new_path()
            if isinstance(item, RedactItem):
                _black_out(ctx, item)  # a redaction that can't be drawn still hides
        ctx.restore()
    surface.flush()
    return surface


def _black_out(ctx: cairo.Context, item: RedactItem) -> None:
    x, y, w, h = item.rect()
    ctx.set_operator(cairo.OPERATOR_OVER)
    ctx.rectangle(math.floor(x), math.floor(y), math.ceil(w) + 1, math.ceil(h) + 1)
    ctx.set_source_rgb(0, 0, 0)
    ctx.fill()


def compose(sources: EffectSources, layer: cairo.ImageSurface) -> Image.Image:
    """Base + paint layer, flattened (RGB)."""
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, sources.width, sources.height)
    ctx = cairo.Context(out)
    ctx.set_source_surface(sources.surface, 0, 0)
    ctx.paint()
    ctx.set_source_surface(layer, 0, 0)
    ctx.paint()
    return surface_to_pil(out).convert("RGB")


def render_document(
    doc: Document, original: Image.Image, sources: EffectSources | None = None
) -> Image.Image:
    """The finished image: annotations flattened and cropped."""
    sources = sources or EffectSources(original)
    image = compose(sources, render_layer(doc, sources))
    if doc.crop:
        x, y, w, h = doc.crop
        box = (max(0, x), max(0, y), min(image.width, x + w), min(image.height, y + h))
        if box[2] > box[0] and box[3] > box[1]:
            image = image.crop(box)
    return image


def render_document_file(path: Path, original: Image.Image) -> Image.Image:
    return render_document(Document.load(path), original)
