# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Text layout shared by the renderer and hit testing (Pango, no GTK)."""

from __future__ import annotations

from functools import lru_cache

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

FONT = "Sans Bold"

_ctx = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))


def layout(ctx: cairo.Context, text: str, size: float, wrap_width: float | None = None):
    lay = PangoCairo.create_layout(ctx)
    desc = Pango.FontDescription.from_string(FONT)
    desc.set_absolute_size(max(1.0, size) * Pango.SCALE)
    lay.set_font_description(desc)
    if wrap_width:
        lay.set_width(int(wrap_width * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
    lay.set_text(text, -1)
    return lay


@lru_cache(maxsize=512)
def measure(text: str, size: float, wrap_width: float | None = None) -> tuple[float, float]:
    """(width, height) of ``text`` in pixels."""
    _w, logical = layout(_ctx, text, size, wrap_width).get_pixel_extents()
    return float(logical.width), float(logical.height)
