# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""App-wide styling: a stylesheet generated from the active color scheme, plus widgets."""

from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

from grabbyhands import theme
from grabbyhands.config import icons_dir


def _rgba(color: str, alpha: float) -> str:
    r, g, b = theme._rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha})"


def supports_css_variables() -> bool:
    """CSS custom properties arrived in GTK 4.16; libadwaita 1.6+ reads its accent from
    them, while 1.5 (on GTK 4.14) reads ``@define-color`` and would warn about the rest."""
    return (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 16)


# -- the design system (see also the tokens in theme.py) -----------------------------------
# Type scale, relative to the desktop's interface font.
T_MICRO = "0.7em"  # section headings in capitals, badges
T_SMALL = "0.76em"  # tile labels, key caps
T_CAPTION = "0.88em"  # hints and other secondary lines
# One easing for every hover / press / selection change. Short: these are tools.
EASE = (
    "transition: background-color 140ms ease-out, background-image 140ms ease-out, "
    "border-color 140ms ease-out, box-shadow 140ms ease-out, color 140ms ease-out, "
    "filter 140ms ease-out, opacity 140ms ease-out"
)
# Shadow levels. "Floating" is for things that sit on top of an arbitrary screenshot: the
# dark hairline gives them an edge on a white page, the soft layers lift them off a dark one.
SHADOW_FLOATING = (
    "0 0 0 1px rgba(0, 0, 0, 0.42), 0 2px 6px rgba(0, 0, 0, 0.3), 0 12px 32px rgba(0, 0, 0, 0.42)"
)
GLASS_SHEEN = "rgba(255, 255, 255, 0.075)"  # the light that falls on their top edge


def bar_label_color(p: theme.Palette) -> str:
    """Section headings on the dark capture bars: the scheme color, lifted toward white."""
    return theme.mix(p.line, "#ffffff", 0.4)


# How strongly a capture-bar button is washed with the scheme color in each state.
BAR_HOVER, BAR_PRESSED = 0.22, 0.34
BAR_DISABLED = 0.45  # opacity of a bar button that can't be used right now
BAR_OUTLINE = 0.4  # the Settings pill's white outline on the bars


def _bar_card(p: theme.Palette, behind: str) -> str:
    """A card on a capture bar, as a solid color, with ``behind`` showing through."""
    glass = theme.mix(behind, p.chrome, theme.GLASS_ALPHA)
    return theme.mix(glass, "#ffffff", 0.05)


def bar_wash_color(p: theme.Palette) -> str:
    """What hovered and pressed buttons on the capture bars are washed with: the scheme
    color, toned down only if it is so bright that the bar's text would get lost on it."""
    card = _bar_card(p, "#ffffff")
    return theme.fit(
        p.line,
        p.chrome,
        lambda c: (
            theme.contrast(p.chrome_fg, theme.mix(card, c, BAR_PRESSED))
            >= theme.TEXT_CONTRAST + 0.1
        ),
    )


def contrast_pairs(p: theme.Palette, dark: bool) -> list[tuple[str, str, str, float]]:
    """Every (what, foreground, background, least contrast) pair the stylesheet makes
    from this palette. The tests check them for all schemes, light and dark; they live
    here, next to the stylesheet, so the two can't drift apart."""
    text_min, line_min = theme.TEXT_CONTRAST, theme.LINE_CONTRAST
    surfaces = theme.DARK_SURFACES if dark else theme.LIGHT_SURFACES
    accent_text = p.text(dark)
    washed = [theme.mix(s, accent_text, theme.TINT) for s in surfaces]
    ink, ink_alpha = ("#ffffff", 1.0) if dark else ("#000006", 0.8)  # libadwaita's text
    dim = theme.DIM_DARK if dark else theme.DIM_LIGHT
    outline = theme.OUTLINE_DARK if dark else theme.OUTLINE_LIGHT
    pairs = [
        ("lettering on the gradient's first color", p.on_accent, p.fill, text_min),
        ("lettering on the gradient's second color", p.on_accent, p.fill2, text_min),
    ]
    for surface in [*surfaces, *washed]:
        pairs.append(("accent text", accent_text, surface, text_min))
        dim_text = theme.mix(surface, ink, ink_alpha * dim)
        pairs.append(("secondary text", dim_text, surface, text_min))
    for surface in surfaces:
        edge = theme.mix(surface, ink, ink_alpha * outline)
        pairs.append(("outline of a control", edge, surface, line_min))
    # The capture screen: its dark glass over the brightest and the darkest screen there is.
    wash_color = bar_wash_color(p)
    for behind in ("#ffffff", "#000000"):
        glass = theme.mix(behind, p.chrome, theme.GLASS_ALPHA)
        card = _bar_card(p, behind)
        pairs += [
            ("capture bar text", p.chrome_fg, glass, text_min),
            ("capture bar heading", bar_label_color(p), card, text_min),
            ("capture bar button that is off", theme.mix(card, p.chrome_fg, BAR_DISABLED), card, 3),
            ("capture bar Settings outline", theme.mix(glass, "#ffffff", BAR_OUTLINE), glass, 3),
            ("lines on the capture screen", p.line, p.chrome, line_min),
        ]
        for wash in (BAR_HOVER, BAR_PRESSED):
            pairs.append(
                (
                    "capture bar text, washed",
                    p.chrome_fg,
                    theme.mix(card, wash_color, wash),
                    text_min,
                )
            )
    return pairs


def stylesheet(p: theme.Palette, dark: bool = False, css_variables: bool | None = None) -> str:
    """The app's stylesheet for a palette, for light or dark windows.

    ``css_variables`` adds the ``:root`` block libadwaita 1.6+ needs; by default it follows
    the running GTK. Everything else sticks to what GTK 4.14 parses: colors are worked out
    here, in Python, instead of with ``var()`` or ``color-mix()``.
    """
    if css_variables is None:
        css_variables = supports_css_variables()
    a, on, chrome, cfg = p.accent, p.on_accent, p.chrome, p.chrome_fg
    grad = f"linear-gradient(135deg, {p.fill}, {p.fill2})"
    text = p.text(dark)  # the accent as text or as an outline: readable on our surfaces
    wash = bar_wash_color(p)
    dim = theme.DIM_DARK if dark else theme.DIM_LIGHT
    outline = theme.OUTLINE_DARK if dark else theme.OUTLINE_LIGHT
    raised = f"0 1px 2px rgba(0, 0, 0, 0.18), 0 2px 8px {_rgba(a, 0.32)}"
    rs, rm, rl, rxl = theme.RADIUS_S, theme.RADIUS_M, theme.RADIUS_L, theme.RADIUS_XL
    # The dark "glass" of the capture screen: one recipe for bars, hint, messages and menus.
    glass = f"""
  color: {cfg};
  background-color: {_rgba(chrome, theme.GLASS_ALPHA)};
  background-image: linear-gradient(to bottom, {GLASS_SHEEN}, rgba(255, 255, 255, 0) 55%);
  border: 1px solid {_rgba(p.line, 0.42)};
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.1), {SHADOW_FLOATING},
    0 0 22px {_rgba(a, 0.16)};"""
    root = (
        f"""
:root {{
  --accent-bg-color: {p.fill};
  --accent-fg-color: {on};
  --accent-color: {text};
}}"""
        if css_variables
        else ""
    )
    return f"""
/* Color scheme: {p.scheme} */
@define-color accent_bg_color {p.fill};
@define-color accent_fg_color {on};
@define-color accent_color {text};{root}

/* Secondary text. libadwaita's own 55% is only about 3:1 on its light surfaces. */
.dim-label, .dimmed, row label.subtitle, row.property label.title, windowtitle .subtitle,
.gh-tool-how, .gh-feature-sub {{ opacity: {dim}; }}
row.property label.subtitle {{ opacity: 1; }}

/* == buttons ==
   One gradient "primary" per view; "tinted" is the step below it; "danger" is a quiet
   destructive action that only turns red where it is. */
button.suggested-action,
button.gh-primary {{
  background-image: {grad};
  color: {on};
  box-shadow: {raised};
  {EASE};
}}
button.suggested-action:hover:not(:disabled), button.gh-primary:hover:not(:disabled) {{
  filter: brightness(1.06);
}}
button.suggested-action:active:not(:disabled), button.gh-primary:active:not(:disabled) {{
  filter: brightness(0.93);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
}}
/* Switched off: flat, and dimmed by libadwaita (which does that with "filter" or with
   "opacity", depending on its version, so neither is touched here). */
button.suggested-action:disabled, button.gh-primary:disabled {{
  background-image: none;
  box-shadow: none;
}}
button.gh-tinted {{
  background-color: {_rgba(text, 0.1)};
  color: {text};
  box-shadow: none;
  {EASE};
}}
button.gh-tinted:hover {{ background-color: {_rgba(text, 0.13)}; }}
button.gh-tinted:active {{ background-color: {_rgba(text, theme.TINT)}; }}
button.gh-danger {{ color: @destructive_color; {EASE}; }}
button.gh-danger:hover {{ background-color: alpha(@destructive_color, 0.1); }}
button.gh-danger:active {{ background-color: alpha(@destructive_color, 0.18); }}

/* Key hints, drawn like keyboard keys */
.gh-keycap {{
  font-size: {T_SMALL};
  font-weight: 700;
  padding: 1px 6px;
  min-width: 10px;
  border-radius: {rs}px;
  background-color: alpha(currentColor, 0.06);
  border: 1px solid alpha(currentColor, 0.24);
  box-shadow: inset 0 -1px 0 alpha(currentColor, 0.24);
}}

/* Keyboard focus on our own controls: a full-strength ring in the scheme color */
button.gh-settings:focus-visible, button.gh-feature:focus-visible,
button.gh-tool:focus-visible {{
  outline: 2px solid {text};
  outline-offset: 2px;
}}

/* == capture overlay (always dark) == */
window.gh-overlay {{ background: none; }}
.gh-bar {{{glass}
  border-radius: {rxl}px;
  padding: 6px;
}}
.gh-bar button {{
  color: {cfg};
  background: none;
  border-radius: {rm}px;
  min-height: 34px;
  padding: 4px 10px;
  box-shadow: none;
  {EASE};
}}
.gh-bar button:hover {{ background-color: {_rgba(wash, BAR_HOVER)}; }}
.gh-bar button:active {{ background-color: {_rgba(wash, BAR_PRESSED)}; }}
.gh-bar button:checked, .gh-bar button.gh-active {{
  background-color: {_rgba(wash, BAR_PRESSED)};
}}
.gh-bar button.gh-primary {{ color: {on}; background-image: {grad}; }}
/* Off: one fixed dimming of the lettering (libadwaita's own would come on top of it) */
.gh-bar button:disabled {{ color: {_rgba(cfg, BAR_DISABLED)}; filter: none; opacity: 1; }}
.gh-bar separator {{
  background-color: rgba(255, 255, 255, 0.14); margin: 8px 4px; min-width: 1px;
}}
.gh-bar .gh-keycap {{ border-color: {_rgba(cfg, 0.35)}; }}
.gh-bar .gh-tool-card {{
  background-color: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: {rl}px;
  padding: 5px 5px 4px 5px;
}}
.gh-bar .gh-section {{ color: {bar_label_color(p)}; }}
.gh-bar button.gh-tile {{ min-width: 66px; padding: 5px 4px 3px 4px; }}
.gh-bar button.gh-tile.gh-active, .gh-bar button.gh-tile:checked,
.gh-bar button.gh-tile.gh-primary {{
  background-image: {grad};
  color: {on};
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.16), 0 1px 2px rgba(0, 0, 0, 0.3),
    0 2px 10px {_rgba(a, 0.36)};
}}
.gh-bar button.gh-tile.gh-active:hover, .gh-bar button.gh-tile:checked:hover,
.gh-bar button.gh-tile.gh-primary:hover {{ filter: brightness(1.06); }}
.gh-bar button.gh-settings {{
  border: 1px solid rgba(255, 255, 255, {BAR_OUTLINE});
  background-color: rgba(255, 255, 255, 0.06);
  border-radius: 999px;
  padding: 4px 14px;
  min-height: 30px;
}}
.gh-bar button.gh-settings:hover {{ background-color: rgba(255, 255, 255, 0.14); }}
.gh-bar button.gh-settings:active {{ background-color: rgba(255, 255, 255, 0.2); }}
.gh-bar button.gh-close {{
  border-radius: 999px;
  min-width: 32px;
  min-height: 32px;
  padding: 0;
}}
.gh-bar button.gh-close:hover {{ background-color: rgba(255, 255, 255, 0.14); }}
.gh-bar button.gh-cancel:hover {{ background-color: rgba(255, 80, 100, 0.4); }}

.gh-hint {{{glass}
  border-radius: 999px;
  padding: 7px 18px;
  font-size: {T_CAPTION};
}}
.gh-busy {{{glass}
  border-radius: {rl}px;
  padding: 14px 20px;
  font-weight: 600;
}}
popover.gh-popover > contents {{{glass}
  border-radius: {rl}px;
  padding: 6px;
}}
popover.gh-popover > arrow {{
  background-color: {_rgba(chrome, theme.GLASS_ALPHA)};
  border: 1px solid {_rgba(p.line, 0.42)};
}}
popover.gh-popover button {{ color: {cfg}; border-radius: {rm}px; {EASE}; }}
popover.gh-popover button:hover {{ background-color: {_rgba(wash, BAR_HOVER)}; }}
popover.gh-popover button:active {{ background-color: {_rgba(wash, BAR_PRESSED)}; }}

/* == pinned screenshots == */
window.gh-pin {{ background: none; }}
.gh-pin-frame {{
  border-radius: {rm}px;
  border: 1px solid {_rgba(p.line, 0.75)};
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.42), 0 1px 3px rgba(0, 0, 0, 0.3),
    0 3px 9px rgba(0, 0, 0, 0.3);
}}

/* Settings stands apart from tools: an outlined, neutral pill */
button.gh-settings {{
  border: 1px solid alpha(currentColor, {outline});
  background: none;
  border-radius: 999px;
  padding: 3px 14px;
  box-shadow: none;
  {EASE};
}}
button.gh-settings:hover {{ background-color: alpha(currentColor, 0.07); }}
button.gh-settings:active {{ background-color: alpha(currentColor, 0.13); }}

/* == tiles and cards (editor sidebar; the capture bars restyle them) == */
.gh-sidebar {{ padding: 8px; }}
.gh-tool-card {{
  background-color: alpha(currentColor, 0.035);
  border: 1px solid alpha(currentColor, 0.1);
  border-radius: {rl}px;
  padding: 6px 5px 5px 5px;
}}
.gh-section {{
  font-size: {T_MICRO};
  font-weight: 800;
  letter-spacing: 0.08em;
  color: {text};
  margin: 0 4px 2px 4px;
}}
.gh-tile-label {{ font-size: {T_SMALL}; font-weight: 600; }}
button.gh-tool {{
  min-width: 66px;
  min-height: 0;
  padding: 5px 2px 3px 2px;
  border-radius: {rm}px;
  background: none;
  box-shadow: none;
  {EASE};
}}
button.gh-tool:hover {{ background-color: {_rgba(text, 0.12)}; }}
button.gh-tool:active {{ background-color: {_rgba(text, 0.22)}; }}
button.gh-tool:checked {{
  background-image: {grad};
  color: {on};
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.16),
    {raised};
}}
button.gh-tool:checked:hover {{ filter: brightness(1.06); }}
button.gh-feature {{
  border-radius: {rm}px;
  padding: 5px 8px;
  margin-top: 3px;
  border: 1px solid {_rgba(text, 0.5)};
  background-color: {_rgba(text, 0.1)};
  box-shadow: none;
  {EASE};
}}
button.gh-feature:hover {{ background-color: {_rgba(text, theme.TINT)}; }}
button.gh-feature:active {{ background-color: {_rgba(text, 0.22)}; }}
button.gh-feature .gh-feature-title {{ font-weight: 700; font-size: {T_CAPTION}; }}
button.gh-feature .gh-feature-sub {{ font-size: {T_SMALL}; }}
button.gh-feature image {{ color: {text}; }}

/* == editor: options bar and canvas == */
.gh-options {{ padding: 5px 12px; min-height: 44px; }}
.gh-options > separator {{ margin: 8px 0; }}
.gh-options spinbutton {{ min-width: 0; }}
.gh-tool-hint {{
  padding: 3px 14px 3px 4px;
  border-radius: 999px;
  background-color: {_rgba(text, 0.1)};
}}
.gh-tool-hint image {{
  background-image: {grad};
  color: {on};
  border-radius: 999px;
  padding: 5px;
}}
.gh-tool-hint .gh-tool-name {{ font-weight: 700; }}
.gh-tool-tip {{ font-size: {T_CAPTION}; opacity: {dim}; }}
.gh-swatch {{
  min-width: 20px; min-height: 20px; border-radius: 999px; padding: 0;
  margin: 3px;
  border: 1px solid alpha(currentColor, 0.2);
  box-shadow: none;
  outline: 2px solid transparent;
  outline-offset: 2px;
  {EASE}, outline-color 140ms ease-out;
}}
.gh-swatch:hover {{ box-shadow: 0 0 0 3px alpha(currentColor, 0.12); }}
.gh-swatch:checked {{ outline-color: {text}; }}
.gh-swatch-0 {{ background: #ff2d6f; }}
.gh-swatch-1 {{ background: #ff7a1a; }}
.gh-swatch-2 {{ background: #ffd60a; }}
.gh-swatch-3 {{ background: #22c55e; }}
.gh-swatch-4 {{ background: #06b6d4; }}
.gh-swatch-5 {{ background: #3b82f6; }}
.gh-swatch-6 {{ background: #7c6cff; }}
.gh-swatch-7 {{ background: #111111; }}
.gh-swatch-8 {{ background: #ffffff; }}
/* black and white need a real edge: one of them is always close to the bar's own color */
.gh-swatch-7, .gh-swatch-8 {{ border-color: alpha(currentColor, {outline}); }}
/* "Any color": the same round shape as the swatches, inside a rainbow ring */
.gh-options colorbutton > button {{
  min-width: 0; min-height: 0; padding: 3px; margin: 3px;
  border-radius: 999px;
  background-image: conic-gradient(#ff2d6f, #ffd60a, #22c55e, #06b6d4, #3b82f6, #7c6cff, #ff2d6f);
  box-shadow: none;
  {EASE};
}}
.gh-options colorbutton > button:hover {{ box-shadow: 0 0 0 3px alpha(currentColor, 0.12); }}
.gh-options colorbutton colorswatch {{
  min-width: 16px; min-height: 16px;
  border-radius: 999px;
  box-shadow: 0 0 0 2px @window_bg_color;
}}
.gh-options .gh-stamps button {{ min-width: 20px; {EASE}; }}
.gh-options .gh-stamps button:checked {{
  background-color: {_rgba(text, theme.TINT)};
  color: {text};
  box-shadow: inset 0 0 0 1px {_rgba(text, 0.5)};
}}
textview.gh-text-edit, textview.gh-text-edit text {{
  background-color: {_rgba(chrome, 0.62)};
  border-radius: {rs}px;
}}
textview.gh-text-edit {{
  outline: 2px solid {p.line};
  outline-offset: 2px;
  box-shadow: 0 0 0 5px rgba(0, 0, 0, 0.25);
}}
.gh-canvas-bg {{
  background-color: alpha(currentColor, 0.05);
  background-image: radial-gradient(circle, alpha(currentColor, 0.13) 1px, transparent 1.5px);
  background-size: 16px 16px;
}}
.gh-title-icon {{ color: {text}; }}

/* == history == */
gridview.gh-history-grid {{ padding: 10px; background: none; }}
.gh-history-grid > child {{ padding: 6px; border-radius: {rl}px; {EASE}; }}
.gh-history-grid > child:hover {{ background-color: {_rgba(text, 0.1)}; }}
.gh-history-grid > child:selected {{
  background-color: {_rgba(text, theme.TINT)};
  box-shadow: inset 0 0 0 2px {text};
}}
.gh-thumb {{
  border-radius: {rs}px;
  background-color: alpha(currentColor, 0.07);
  border: 1px solid alpha(currentColor, 0.1);
}}
.gh-history-cell .gh-thumb-delete {{ opacity: 0; transition: opacity 140ms ease-out; }}
.gh-history-cell:hover .gh-thumb-delete,
.gh-history-grid > child:selected .gh-thumb-delete,
.gh-thumb-delete:focus-visible {{ opacity: 1; }}
.gh-badge {{
  font-size: {T_MICRO}; font-weight: 700; padding: 1px 8px; border-radius: 999px;
  background-color: {_rgba(text, theme.TINT)}; color: {text};
}}

/* == settings: color scheme and light/dark pickers == */
.gh-scheme-card {{ padding: 14px 8px 10px 8px; }}
button.gh-scheme {{
  min-width: 44px; min-height: 44px; padding: 0; border-radius: 999px;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.14);
  outline: 2px solid transparent;
  outline-offset: 3px;
  {EASE}, outline-color 140ms ease-out;
}}
button.gh-scheme:hover {{ filter: brightness(1.06); }}
button.gh-scheme:focus-visible {{ outline-color: @window_fg_color; }}
button.gh-scheme:checked {{ outline-color: {text}; }}
button.gh-scheme:checked:focus-visible {{
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.14), 0 0 0 8px {_rgba(text, 0.35)};
}}
.gh-scheme-name {{ font-size: {T_CAPTION}; }}
.gh-segmented > button:checked {{
  background-image: {grad};
  color: {on};
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.1);
}}

/* == color window == */
.gh-color-swatch {{ border-radius: {rl}px; }}
"""


def _scheme_buttons_css() -> str:
    """Each scheme's own gradient on its button in Settings, with a check mark on the chosen one."""
    rules = []
    for scheme in theme.schemes():
        p = theme.palette(scheme.id)
        gradient = f"linear-gradient(135deg, {p.fill}, {p.fill2})"
        rules.append(
            f"button.gh-scheme-{scheme.id} {{ background-image: {gradient}; "
            f"color: {p.on_accent}; }}\n"
            f"button.gh-scheme-{scheme.id}:checked {{ background-image: "
            f'-gtk-icontheme("gh-check-symbolic"), {gradient}; '
            "background-size: 18px 18px, cover; background-repeat: no-repeat; "
            "background-position: center; }"
        )
    return "\n".join(rules)


_provider: Gtk.CssProvider | None = None
_icons_added = False


def apply(scheme_id: str | None = None, display: Gdk.Display | None = None) -> theme.Palette:
    """(Re)generate the stylesheet for ``scheme_id`` and install it."""
    global _provider, _icons_added
    display = display or Gdk.Display.get_default()
    palette = theme.set_current(scheme_id or theme.current().scheme)
    dark = False
    try:
        from gi.repository import Adw

        dark = Adw.StyleManager.get_default().get_dark()
    except (ImportError, AttributeError):
        pass
    css = stylesheet(palette, dark) + _scheme_buttons_css()
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    if hasattr(_provider, "load_from_string"):
        _provider.load_from_string(css)
    else:  # GTK < 4.12
        _provider.load_from_data(css, -1)
    if not _icons_added:
        icon_theme = Gtk.IconTheme.get_for_display(display)
        icon_theme.add_search_path(str(icons_dir()))
        from grabbyhands.config import SOURCE_ROOT

        dev_icons = SOURCE_ROOT / "data" / "icons"
        if (dev_icons / "hicolor").is_dir():  # running from a checkout: the app icon too
            icon_theme.add_search_path(str(dev_icons))
        _icons_added = True
    return palette


def install(display: Gdk.Display | None = None) -> None:
    if _provider is None:
        apply(None, display)


def keycap(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class("gh-keycap")
    return label


def tooltip(title: str, body: str, key: str | None = None) -> str:
    """Two-line tooltip markup: bold title (and key), then what it does."""
    head = f"<b>{GLib.markup_escape_text(title)}</b>"
    if key:
        head += f"   <tt>{GLib.markup_escape_text(key)}</tt>"
    return f"{head}\n{GLib.markup_escape_text(body)}"


def icon_button(
    icon: str, label: str | None = None, tooltip: str | None = None, key: str | None = None
) -> Gtk.Button:
    """A flat button: icon, optional label, and a key hint drawn like a keyboard key.

    ``tooltip`` is markup (see :func:`tooltip`) or plain text.
    """
    button = Gtk.Button()
    box = Gtk.Box(spacing=7)
    box.append(Gtk.Image.new_from_icon_name(icon))
    if label:
        box.append(Gtk.Label(label=label))
    if key:
        box.append(keycap(key))
    button.set_child(box)
    if tooltip:
        button.set_tooltip_markup(tooltip)
    button.set_focusable(False)
    button.set_focus_on_click(False)
    return button


def tile_content(icon: str, label: str) -> Gtk.Box:
    """Icon over a one-line label: the content of every tool tile."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    image = Gtk.Image.new_from_icon_name(icon)
    image.set_pixel_size(18)
    image.add_css_class("gh-tile-icon")
    image.set_halign(Gtk.Align.CENTER)
    box.append(image)
    text = Gtk.Label(label=label)  # never shortened: tiles grow to fit their name
    text.add_css_class("gh-tile-label")
    box.append(text)
    return box


def tile_button(
    icon: str,
    label: str,
    tooltip: str | None = None,
    toggle: bool = False,
) -> Gtk.Button:
    """A tool tile. Keyboard keys live in the tooltip, not on the tile."""
    button = Gtk.ToggleButton() if toggle else Gtk.Button()
    button.add_css_class("gh-tile")
    button.set_child(tile_content(icon, label))
    if tooltip:
        button.set_tooltip_markup(tooltip)
    button.set_focusable(False)
    button.set_focus_on_click(False)
    return button


def section_card(title: str, tiles: list, columns: int | None = None) -> Gtk.Box:
    """A framed group with a small heading and identical tiles (one row, or a grid)."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    card.add_css_class("gh-tool-card")
    header = Gtk.Label(label=title, xalign=0)
    header.add_css_class("gh-section")
    card.append(header)
    grid = Gtk.Grid(row_spacing=2, column_spacing=3, column_homogeneous=True)
    per_row = columns or len(tiles)
    for i, tile in enumerate(tiles):
        grid.attach(tile, i % per_row, i // per_row, 1, 1)
    card.append(grid)
    return card


def settings_button(tooltip_markup: str | None = None) -> Gtk.Button:
    """The Settings button: an outlined pill, deliberately unlike the tool tiles."""
    from gi.repository import Adw

    button = Gtk.Button(
        child=Adw.ButtonContent(icon_name="gh-settings-symbolic", label=_("Settings"))
    )
    button.add_css_class("gh-settings")
    button.set_valign(Gtk.Align.CENTER)
    button.set_tooltip_markup(
        tooltip_markup or tooltip(_("Settings"), _("Colors, saving, shortcuts and more"))
    )
    button.set_focus_on_click(False)
    return button


def redraw_all(app: Gtk.Application) -> None:
    """Redraw every hand-drawn area (they read colors from theme.current() when drawing)."""

    def walk(widget: Gtk.Widget) -> None:
        if isinstance(widget, Gtk.DrawingArea):
            widget.queue_draw()
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    for window in app.get_windows():
        walk(window)
