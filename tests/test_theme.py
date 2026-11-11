# SPDX-License-Identifier: GPL-3.0-or-later
"""The colour system and the generated stylesheet, checked without a display.

Contrast is computed, not eyeballed: every text/background pair the stylesheet makes has to
reach WCAG's 4.5:1 (3:1 for the outlines of controls) in every scheme, light and dark. And
the stylesheet has to stay inside what GTK 4.14 parses, whatever GTK runs the tests.
"""

import colorsys
import re

import pytest

from grabbyhands import style, theme

SCHEMES = [scheme.id for scheme in theme.schemes() if scheme.id != "system"]
MODES = [False, True]


def _failures(p: theme.Palette, dark: bool) -> list[str]:
    return [
        f"{what}: {fg} on {bg} is {theme.contrast(fg, bg):.2f}, needs {need}"
        for what, fg, bg, need in style.contrast_pairs(p, dark)
        if theme.contrast(fg, bg) < need
    ]


@pytest.mark.parametrize("scheme_id", SCHEMES)
@pytest.mark.parametrize("dark", MODES)
def test_every_scheme_is_readable(scheme_id, dark):
    p = theme.palette(scheme_id)
    assert not _failures(p, dark)
    # the scheme's own colours are kept; only what carries text is moved, and not far
    assert theme.contrast(p.accent, p.fill) < 1.25, (p.accent, p.fill)
    assert p.on_accent in (theme.WHITE, theme.INK)


def test_the_default_scheme_keeps_its_look():
    p = theme.palette(theme.DEFAULT)
    assert p.scheme == "violet" and p.accent == "#7c5cff"
    assert p.on_accent == theme.WHITE  # white lettering on violet, as always
    assert p.line == p.accent  # already bright enough on the dark capture screen


# The accent colours GNOME and Ubuntu let people choose.
DESKTOP_ACCENTS = [
    *("#3584e4", "#2190a4", "#3a944a", "#c88800", "#ed5b00", "#e62d42", "#d56199", "#9141ac"),
    *("#6f8396", "#e95420", "#787859", "#657b69", "#4b8501", "#03875b", "#308280", "#0073e5"),
    *("#7764d8", "#b34cb3", "#da3450"),
]


def test_any_gnome_accent_is_readable():
    """The "GNOME Accent" scheme takes whatever colour the desktop has: those on offer
    today, and any other hue of ordinary strength."""
    accents = list(DESKTOP_ACCENTS)
    for hue in range(0, 360, 15):
        for lightness in (0.3, 0.45, 0.6):
            for saturation in (0.4, 0.85):
                r, g, b = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
                accents.append(theme._hex((r * 255, g * 255, b * 255)))
    for accent in accents:
        p = theme.build("system", accent, theme._partner(accent))
        for dark in MODES:
            assert not _failures(p, dark), accent


def test_fit_moves_a_colour_only_as_far_as_needed():
    assert theme.fit("#123456", "#ffffff", lambda c: True) == "#123456"
    assert theme.fit("#123456", "#ffffff", lambda c: False) == "#ffffff"
    lifted = theme.fit("#404040", "#ffffff", lambda c: theme.contrast(c, "#000000") >= 7)
    assert 7 <= theme.contrast(lifted, "#000000") < 7.6


# -- the stylesheet --------------------------------------------------------------------
# What GTK 4.14 understands, of the things a stylesheet like ours might reach for.
# (":root", "var()", custom properties, "color-mix()", relative colours and the
# space-separated "rgb(r g b / a)" form all arrived in 4.16.)
KNOWN_PSEUDO_CLASSES = {
    "hover",
    "active",
    "checked",
    "disabled",
    "selected",
    "focus-visible",
    "not",
}
KNOWN_FUNCTIONS = {
    "rgba",
    "alpha",
    "linear-gradient",
    "radial-gradient",
    "conic-gradient",
    "brightness",
    "-gtk-icontheme",
}
KNOWN_PROPERTIES = {
    "background",
    "background-color",
    "background-image",
    "background-position",
    "background-repeat",
    "background-size",
    "border",
    "border-color",
    "border-radius",
    "box-shadow",
    "color",
    "filter",
    "font-size",
    "font-weight",
    "letter-spacing",
    "margin",
    "margin-top",
    "min-height",
    "min-width",
    "opacity",
    "outline",
    "outline-color",
    "outline-offset",
    "padding",
    "transition",
}


def _sheets(css_variables: bool):
    for scheme in theme.schemes():
        p = theme.build(scheme.id, scheme.accent or "#3584e4", scheme.accent2 or "#62a0ea")
        for dark in MODES:
            yield (
                scheme.id,
                dark,
                style.stylesheet(p, dark, css_variables) + style._scheme_buttons_css(),
            )


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector, declarations) of every rule, comments and @define-color lines removed."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@define-color [^;]+;", "", css)
    assert "@" not in css.replace("@define-color", "").replace("alpha(@", "").replace(" @", " "), (
        "an at-rule other than @define-color"
    )
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    assert rules and "".join(f"{s}{{{b}}}" for s, b in rules).strip() == css.strip()
    return [(selector.strip(), body) for selector, body in rules]


def test_the_plain_stylesheet_is_gtk_4_14_css():
    """Without CSS variables (GTK older than 4.16) nothing newer may slip in."""
    for scheme_id, dark, css in _sheets(css_variables=False):
        where = (scheme_id, dark)
        assert ":root" not in css and "var(" not in css and "--" not in css, where
        for selector, body in _rules(css):
            pseudo = set(re.findall(r"(?<!:):([a-z-]+)", selector))
            assert pseudo <= KNOWN_PSEUDO_CLASSES, (where, selector)
            properties = set(re.findall(r"(?:^|;)\s*([a-z-]+)\s*:", body))
            assert properties <= KNOWN_PROPERTIES, (where, selector, properties)
            functions = set(re.findall(r"([a-z-]+)\(", body))
            assert functions <= KNOWN_FUNCTIONS, (where, selector, functions)
            # colours are "#rrggbb", "rgba(r, g, b, a)" with commas, or a named colour
            for inside in re.findall(r"rgba\(([^)]*)\)", body):
                assert inside.count(",") == 3 and "/" not in inside, (where, selector, inside)


def test_css_variables_are_one_block_on_top_of_the_plain_stylesheet():
    """GTK 4.16+ (libadwaita 1.6+) gets the same stylesheet plus one ``:root`` block."""
    for (scheme_id, dark, plain), (_s, _d, modern) in zip(
        _sheets(css_variables=False), _sheets(css_variables=True), strict=True
    ):
        blocks = re.findall(r":root \{[^}]*\}", modern)
        assert len(blocks) == 1, (scheme_id, dark)
        assert set(re.findall(r"--[a-z-]+", blocks[0])) == {
            "--accent-bg-color",
            "--accent-fg-color",
            "--accent-color",
        }
        assert modern.replace("\n" + blocks[0], "") == plain, (scheme_id, dark)
        # the named colours libadwaita 1.5 reads are always there
        for name in ("accent_bg_color", "accent_fg_color", "accent_color"):
            assert f"@define-color {name} #" in plain, (scheme_id, dark, name)


def test_the_stylesheet_follows_the_running_gtk():
    from gi.repository import Gtk

    p = theme.palette(theme.DEFAULT)
    modern = (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 16)
    assert style.supports_css_variables() == modern
    assert (":root" in style.stylesheet(p)) == modern
