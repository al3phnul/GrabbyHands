# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Color schemes: one accent (plus a gradient partner) that tints the whole app.

Also the app's design tokens: the few radii, strokes and alphas that the generated
stylesheet (style.py) and the hand-drawn Cairo surfaces share, so both look like one product.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from grabbyhands.colors import parse_hex


@dataclass(frozen=True)
class Scheme:
    id: str
    name: str
    accent: str  # main accent, e.g. buttons and selections
    accent2: str  # gradient partner


def schemes() -> list[Scheme]:
    return [
        Scheme("violet", _("Violet"), "#7c5cff", "#c053f0"),
        Scheme("ocean", _("Ocean"), "#2d7ff9", "#1fc4e8"),
        Scheme("mint", _("Mint"), "#0ea89a", "#3fd17f"),
        Scheme("sunset", _("Sunset"), "#f2621c", "#e8408f"),
        Scheme("rose", _("Rose"), "#e1306c", "#ff7a9c"),
        Scheme("amber", _("Amber"), "#d97a06", "#f2b705"),
        Scheme("graphite", _("Graphite"), "#4d5b73", "#8796ad"),
        Scheme("system", _("GNOME Accent"), "", ""),
    ]


DEFAULT = "violet"

# -- design tokens -----------------------------------------------------------------------
# Corner radii. Nested shapes stay concentric: a bar (18) pads its cards (12) by 6.
RADIUS_S = 6  # chips, key caps, thumbnails
RADIUS_M = 8  # tiles and other controls inside a card
RADIUS_L = 12  # cards, panels, the magnifier
RADIUS_XL = 18  # the floating bars on the capture screen
# Hand-drawn lines: a 2 px accent stroke with a 1 px dark halo, so it shows on any picture.
STROKE = 2.0
HALO = (0.0, 0.0, 0.0, 0.38)
HANDLE_RADIUS = 5.5
GLASS_ALPHA = 0.965  # how solid the capture screen's dark surfaces are (nothing blurs behind)
# Smallest contrast we accept (WCAG): normal text, and the outlines of controls.
TEXT_CONTRAST = 4.5
LINE_CONTRAST = 3.0
WHITE = "#ffffff"
INK = "#14121f"  # near-black lettering, a touch violet like the dark surfaces
# The strongest accent wash that text sits on (badges, selected cards).
TINT = 0.16
# Backgrounds the accent has to read on: libadwaita's window, view/card, header bar and
# popover colors (1.5 and current values), light and dark.
LIGHT_SURFACES = ("#fafafb", "#ffffff", "#ebebed", "#ebebeb")
DARK_SURFACES = ("#222226", "#242424", "#1d1d20", "#2e2e32", "#303030", "#343437", "#383838")
# Opacity of secondary text. libadwaita's 55% is only about 3.2:1 on its light surfaces.
DIM_LIGHT = 0.74
DIM_DARK = 0.68
# Neutral outline of a control (the Settings pill, color swatches), as an alpha of the text
# color: 3:1 against the surface behind it.
OUTLINE_LIGHT = 0.56
OUTLINE_DARK = 0.42


# -- color math ------------------------------------------------------------------------
def _rgb(value: str) -> tuple[int, int, int]:
    r, g, b, _a = parse_hex(value)
    return round(r * 255), round(g * 255), round(b * 255)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def mix(a: str, b: str, t: float) -> str:
    """``t`` of the way from color ``a`` to color ``b``."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * t for x, y in zip(ra, rb, strict=True)))


def _luminance(value: str) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(value)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_on(color: str) -> str:
    """White or near-black text, whichever reads better on ``color``."""
    return WHITE if contrast(color, WHITE) >= contrast(color, INK) else INK


def fit(color: str, toward: str, good) -> str:
    """``color``, moved in the direction of ``toward`` only as far as it takes to make
    ``good(color)`` true. This is how a scheme's own color is kept wherever it already reads
    well."""
    steps = 50
    for i in range(steps + 1):
        candidate = mix(color, toward, i / steps)
        if good(candidate):
            return candidate
    return toward


def text_on_gradient(a: str, b: str) -> str:
    """Lettering for a gradient from ``a`` to ``b``: judged at its worse end. White is kept
    when it is nearly as good as dark (it looks cleaner on a saturated color)."""

    def worst(text: str) -> float:
        return min(contrast(text, a), contrast(text, b))

    return WHITE if worst(WHITE) >= worst(INK) - 0.7 else INK


def _partner(accent: str) -> str:
    """A gradient partner for an arbitrary accent: rotate the hue a little, lift it."""
    r, g, b = (c / 255 for c in _rgb(accent))
    h, light, s = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb((h + 0.08) % 1, min(0.72, light + 0.1), s)
    return _hex((r2 * 255, g2 * 255, b2 * 255))


# -- the active palette ----------------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    scheme: str
    accent: str
    accent2: str
    on_accent: str
    chrome: str  # dark surface for the capture overlay (always dark: it floats over anything)
    chrome_fg: str
    # The gradient that carries lettering (buttons, selected tiles): the two colors above,
    # each moved just far enough for ``on_accent`` to reach TEXT_CONTRAST on it.
    fill: str = ""
    fill2: str = ""
    line: str = ""  # the accent for lines drawn on the dark capture screen

    def rgb(self, which: str = "accent") -> tuple[float, float, float]:
        r, g, b = _rgb(getattr(self, which))
        return r / 255, g / 255, b / 255

    def text(self, dark: bool) -> str:
        """The accent as a text or outline color on the app's own (light or dark)
        surfaces. Hover, pressed and selected surfaces are washed with this same color
        (up to TINT), and it stays readable on those too."""
        surfaces = DARK_SURFACES if dark else LIGHT_SURFACES

        def readable(c: str) -> bool:
            backgrounds = [*surfaces, *(mix(s, c, TINT) for s in surfaces)]
            return all(contrast(c, b) >= TEXT_CONTRAST + 0.1 for b in backgrounds)

        return fit(self.accent, WHITE if dark else "#000000", readable)


def system_accent() -> str | None:
    try:
        from gi.repository import Adw

        rgba = Adw.StyleManager.get_default().get_accent_color_rgba()
    except (AttributeError, ImportError, TypeError):
        return None
    return _hex((rgba.red * 255, rgba.green * 255, rgba.blue * 255))


def build(scheme_id: str, accent: str, accent2: str) -> Palette:
    """The whole palette for an accent and its gradient partner. Works for any two colors
    (the GNOME accent can be anything): what has to carry text is adjusted until it does."""
    chrome = mix("#0c0b16", accent, 0.14)
    on = text_on_gradient(accent, accent2)
    away = "#000000" if on == WHITE else WHITE

    def carries_text(c: str) -> bool:
        return contrast(on, c) >= TEXT_CONTRAST + 0.1

    return Palette(
        scheme_id,
        accent,
        accent2,
        on,
        chrome,
        "#efedf9",
        fill=fit(accent, away, carries_text),
        fill2=fit(accent2, away, carries_text),
        line=fit(accent, WHITE, lambda c: contrast(c, chrome) >= LINE_CONTRAST + 0.5),
    )


def palette(scheme_id: str) -> Palette:
    by_id = {s.id: s for s in schemes()}
    scheme = by_id.get(scheme_id) or by_id[DEFAULT]
    if scheme.id == "system":
        accent = system_accent() or by_id[DEFAULT].accent
        accent2 = _partner(accent)
    else:
        accent, accent2 = scheme.accent, scheme.accent2
    return build(scheme.id, accent, accent2)


_current: Palette | None = None


def current() -> Palette:
    global _current
    if _current is None:
        _current = palette(DEFAULT)
    return _current


def set_current(scheme_id: str) -> Palette:
    global _current
    _current = palette(scheme_id)
    return _current
