# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Hand-drawn chrome: the Cairo counterparts of the stylesheet's surfaces.

The capture screen, the editor canvas and the word picker draw their own frames, handles
and labels. They all come from here, so a handle or a label looks the same wherever it
appears and matches the dark "glass" of the capture bars (see style.py). Sizes are logical
pixels; hairlines sit on pixel boundaries at scale 1 and stay crisp at scale 2.
"""

from __future__ import annotations

import math

import cairo
from gi.repository import Gtk, Pango, PangoCairo

from grabbyhands import theme

Color = tuple[float, float, float]


def rounded_rect(ctx: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        ctx.rectangle(x, y, w, h)
        return
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


# -- text ------------------------------------------------------------------------------
def text_layout(ctx: cairo.Context, text: str, size: float = 10, bold: bool = True) -> Pango.Layout:
    """A layout in the desktop's interface font (the one the widgets use), at the user's
    text size. Digits are tabular, so a size label doesn't jitter while it changes.

    Color and transparency are set on the Cairo context by the caller. (Pango's own alpha
    attribute is not used on purpose: its Cairo renderer is shared with GTK's text and
    kept the transparency for everything drawn after it.)
    """
    layout = PangoCairo.create_layout(ctx)
    settings = Gtk.Settings.get_default()
    name = settings.get_property("gtk-font-name") if settings is not None else None
    desc = Pango.FontDescription.from_string(name or "Sans")
    desc.set_size(round(size * Pango.SCALE))
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    layout.set_font_description(desc)
    dpi = settings.get_property("gtk-xft-dpi") / 1024 if settings is not None else 0
    if dpi > 0:
        PangoCairo.context_set_resolution(layout.get_context(), dpi)
    # Grayscale smoothing, like GTK's own text: no colored fringes next to the widgets'.
    options = cairo.FontOptions()
    options.set_antialias(cairo.ANTIALIAS_GRAY)
    PangoCairo.context_set_font_options(layout.get_context(), options)
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_font_features_new("tnum=1"))
    layout.set_attributes(attrs)
    layout.set_text(text, -1)
    return layout


def text_size(layout: Pango.Layout) -> tuple[float, float]:
    width, height = layout.get_size()
    return width / Pango.SCALE, height / Pango.SCALE


# -- surfaces --------------------------------------------------------------------------
def soft_shadow(
    ctx: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
    depth: int = 6,
    strength: float = 0.3,
) -> None:
    """A soft drop shadow under the box x, y, w, h: stacked, growing shapes that add up to
    ``strength`` at the box's edge and fade out over ``depth`` pixels."""
    ctx.save()
    ctx.set_source_rgba(0, 0, 0, strength / depth)
    for i in range(1, depth + 1):
        rounded_rect(ctx, x - i, y - i + depth / 3, w + 2 * i, h + 2 * i, r + i)
        ctx.fill()
    ctx.restore()


def panel(
    ctx: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    radius: float = theme.RADIUS_S,
    shadow: bool = True,
) -> None:
    """A small floating dark surface: the same recipe as the capture bars in the
    stylesheet (dark hairline outside, tinted fill, a sheen on top, accent edge)."""
    p = theme.current()
    x, y, w, h = round(x), round(y), round(w), round(h)
    if shadow:
        soft_shadow(ctx, x, y, w, h, radius, depth=4)
    ctx.set_line_width(1)
    rounded_rect(ctx, x - 0.5, y - 0.5, w + 1, h + 1, radius + 0.5)
    ctx.set_source_rgba(0, 0, 0, 0.42)
    ctx.stroke()
    rounded_rect(ctx, x, y, w, h, radius)
    ctx.set_source_rgba(*p.rgb("chrome"), theme.GLASS_ALPHA)
    ctx.fill_preserve()
    sheen = cairo.LinearGradient(0, y, 0, y + h)
    sheen.add_color_stop_rgba(0, 1, 1, 1, 0.075)
    sheen.add_color_stop_rgba(0.55, 1, 1, 1, 0)
    ctx.set_source(sheen)
    ctx.fill()
    rounded_rect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, max(0.0, radius - 0.5))
    ctx.set_source_rgba(*p.rgb("line"), 0.42)
    ctx.stroke()


def chip(
    ctx: cairo.Context,
    x: float,
    y: float,
    text: str,
    detail: str = "",
    size: float = 10,
    center: bool = False,
) -> tuple[float, float]:
    """A label on a small dark panel at x, y, or a pill centered on that point. ``detail``
    follows the text in a quieter voice. Returns the panel's size."""
    fg = theme.current().rgb("chrome_fg")
    parts = [(part, alpha) for part, alpha in ((text, 1.0), (detail, 0.72)) if part]
    layouts = [(text_layout(ctx, part, size, bold=alpha == 1.0), alpha) for part, alpha in parts]
    sizes = [text_size(layout) for layout, _alpha in layouts]
    gap = 10
    tw = sum(w for w, _h in sizes) + gap * (len(sizes) - 1)
    th = max(h for _w, h in sizes)
    pad_x, pad_y = (14, 7) if center else (8, 4)
    w, h = math.ceil(tw) + 2 * pad_x, math.ceil(th) + 2 * pad_y
    if center:
        x, y = x - w / 2, y - h / 2
    x, y = round(x), round(y)
    panel(ctx, x, y, w, h, h / 2 if center else theme.RADIUS_S)
    tx = x + pad_x
    for (layout, alpha), (part_w, _part_h) in zip(layouts, sizes, strict=True):
        ctx.move_to(tx, y + pad_y)
        ctx.set_source_rgba(*fg, alpha)
        PangoCairo.show_layout(ctx, layout)
        tx += part_w + gap
    return w, h


# -- lines and handles -----------------------------------------------------------------
def frame(
    ctx: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    color: Color,
    radius: float = 0,
) -> None:
    """An outline just outside the box x, y, w, h: an accent stroke with a dark hairline
    around it, so it shows on a white page as well as on a dark or accent-colored one."""
    s = theme.STROKE
    x, y, w, h = round(x), round(y), round(w), round(h)
    ctx.save()
    ctx.set_dash([])
    ctx.set_line_width(1)
    ctx.set_source_rgba(*theme.HALO)
    rounded_rect(ctx, x - s - 0.5, y - s - 0.5, w + 2 * s + 1, h + 2 * s + 1, radius + s + 0.5)
    ctx.stroke()
    ctx.set_line_width(s)
    ctx.set_source_rgb(*color)
    rounded_rect(ctx, x - s / 2, y - s / 2, w + s, h + s, radius + s / 2 if radius else 0)
    ctx.stroke()
    ctx.restore()


def handle(ctx: cairo.Context, x: float, y: float, color: Color) -> None:
    """A round grab handle: white, ringed in the accent, with a little shadow under it."""
    r = theme.HANDLE_RADIUS
    ctx.save()
    ctx.set_dash([])
    ctx.new_sub_path()
    ctx.arc(x, y + 1, r + 2, 0, 2 * math.pi)
    ctx.set_source_rgba(0, 0, 0, 0.26)
    ctx.fill()
    ctx.arc(x, y, r, 0, 2 * math.pi)
    ctx.set_source_rgb(1, 1, 1)
    ctx.fill_preserve()
    ctx.set_source_rgb(*color)
    ctx.set_line_width(theme.STROKE)
    ctx.stroke()
    ctx.restore()
