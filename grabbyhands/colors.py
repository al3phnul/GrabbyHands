# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Color formatting for the color picker."""

from __future__ import annotations

import colorsys


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def to_cmyk(rgb: tuple[int, int, int]) -> tuple[int, int, int, int]:
    """Naive (uncalibrated) CMYK percentages, the way design tools show them."""
    r, g, b = (c / 255 for c in rgb)
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0, 0, 0, 100
    c, m, y = ((1 - v - k) / (1 - k) for v in (r, g, b))
    return round(c * 100), round(m * 100), round(y * 100), round(k * 100)


FORMATS = ("hex", "rgb", "cmyk", "hsl")


def format_color(rgb: tuple[int, int, int], fmt: str = "hex") -> str:
    r, g, b = rgb
    if fmt == "cmyk":
        return "cmyk({}%, {}%, {}%, {}%)".format(*to_cmyk(rgb))
    if fmt == "rgb":
        return f"rgb({r}, {g}, {b})"
    if fmt == "hsl":
        h, lightness, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        return f"hsl({round(h * 360) % 360}, {round(s * 100)}%, {round(lightness * 100)}%)"
    return to_hex(rgb)


def parse_hex(value: str) -> tuple[float, float, float, float]:
    """'#rrggbb' or '#rrggbbaa' -> (r, g, b, a) floats in 0..1."""
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) == 6:
        v += "ff"
    if len(v) != 8:
        raise ValueError(f"not a hex color: {value!r}")
    r, g, b, a = (int(v[i : i + 2], 16) / 255 for i in (0, 2, 4, 6))
    return r, g, b, a


def readable_text_on(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """Black or white, whichever reads better on ``rgb``."""
    r, g, b = (c / 255 for c in rgb)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0.0, 0.0, 0.0) if luminance > 0.55 else (1.0, 1.0, 1.0)
