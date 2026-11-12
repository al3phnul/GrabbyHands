# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The picked color: a swatch plus HEX, RGB and CMYK values with copy buttons."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk, PangoCairo

from grabbyhands import chrome, theme
from grabbyhands.colors import format_color, to_cmyk, to_hex

if TYPE_CHECKING:
    import cairo

    from grabbyhands.application import GrabbyHandsApp


def color_values(rgb: tuple[int, int, int]) -> list[tuple[str, str, str]]:
    """``(label, shown value, copied value)`` rows for the color window."""
    r, g, b = rgb
    c, m, y, k = to_cmyk(rgb)
    return [
        ("HEX", to_hex(rgb).upper(), to_hex(rgb)),
        ("RGB", f"{r}, {g}, {b}", format_color(rgb, "rgb")),
        ("CMYK", f"{c}%, {m}%, {y}%, {k}%", format_color(rgb, "cmyk")),
    ]


class ColorWindow(Adw.Window):
    def __init__(self, app: GrabbyHandsApp, rgb: tuple[int, int, int]) -> None:
        super().__init__(application=app, title=_("Identified Color"), default_width=380)
        self.app = app
        self.rgb = rgb
        self.set_resizable(False)

        swatch = Gtk.DrawingArea(content_height=120, hexpand=True)
        swatch.set_draw_func(self._draw_swatch)
        frame = Gtk.Frame(child=swatch)
        frame.add_css_class("gh-color-swatch")

        group = Adw.PreferencesGroup()
        self.copy_buttons: dict[str, Gtk.Button] = {}
        for label, shown, copied in color_values(rgb):
            row = Adw.ActionRow(title=label, subtitle=shown)
            row.add_css_class("property")
            row.set_subtitle_selectable(True)
            button = Gtk.Button(
                icon_name="gh-copy-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text=_("Copy {format}").format(format=label),
            )
            button.add_css_class("flat")
            button.connect("clicked", lambda _b, v=copied: self._copy(v))
            row.add_suffix(button)
            row.set_activatable_widget(button)
            group.add(row)
            self.copy_buttons[label] = button

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(18)
        box.append(frame)
        box.append(group)

        self.toasts = Adw.ToastOverlay(child=box)
        header = Adw.HeaderBar()
        again = Gtk.Button(label=_("Pick Another"))
        again.connect("clicked", self._pick_again)
        header.pack_start(again)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        esc = Gtk.ShortcutController()
        esc.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1]),
            )
        )
        self.add_controller(esc)

    def _draw_swatch(self, _area, ctx: cairo.Context, width: int, height: int) -> None:
        ctx.set_source_rgb(*(c / 255 for c in self.rgb))
        ctx.paint()
        # the HEX value in the interface font (the widgets' own), centered on the color, in
        # black or white: whichever has the higher contrast (always at least 4.5:1)
        value = to_hex(self.rgb)
        layout = chrome.text_layout(ctx, value.upper(), 13)
        text_w, text_h = chrome.text_size(layout)
        white = theme.contrast(value, "#ffffff") >= theme.contrast(value, "#000000")
        ctx.set_source_rgb(*((1, 1, 1) if white else (0, 0, 0)))
        ctx.move_to(round((width - text_w) / 2), round((height - text_h) / 2))
        PangoCairo.show_layout(ctx, layout)

    def _copy(self, value: str) -> None:
        self.app.copy_text(value, self)
        self.toasts.add_toast(Adw.Toast(title=_("Copied {value}").format(value=value), timeout=2))

    def _pick_again(self, _button: Gtk.Button) -> None:
        self.close()
        self.app.capture.pick_color()
