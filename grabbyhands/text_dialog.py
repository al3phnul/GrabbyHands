# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Copy Text: pick the words you want, right on the captured image (or as plain text)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cairo
from gi.repository import Adw, Gdk, Gtk

from grabbyhands import chrome, keys, theme
from grabbyhands.i18n import ngettext
from grabbyhands.imaging import pil_to_surface
from grabbyhands.ocr import Word, join_words

if TYPE_CHECKING:
    from PIL import Image


class WordPicker(Gtk.DrawingArea):
    """The capture with its recognized words; drag across words to select them."""

    def __init__(self, image: Image.Image, words: list[Word], on_change) -> None:
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.words = words
        self.surface = pil_to_surface(image)
        self.iw, self.ih = image.size
        self.on_change = on_change
        self.anchor: int | None = None
        self.focus_idx: int | None = None
        self.hover: int | None = None
        self.scale, self.ox, self.oy = 1.0, 0.0, 0.0
        self._origin = (0.0, 0.0)
        self._drag_anchor: int | None = None
        self.set_draw_func(self._draw)
        self.set_cursor_from_name("text")

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_begin)
        drag.connect("drag-update", self._on_update)
        self.add_controller(drag)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", self._on_click)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    # -- selection ------------------------------------------------------------------------
    @property
    def selection(self) -> tuple[int, int] | None:
        if self.anchor is None or self.focus_idx is None:
            return None
        return min(self.anchor, self.focus_idx), max(self.anchor, self.focus_idx)

    def selected_words(self) -> list[Word]:
        sel = self.selection
        return self.words[sel[0] : sel[1] + 1] if sel else []

    def select_range(self, start: int | None, end: int | None) -> None:
        self.anchor, self.focus_idx = start, end
        self.queue_draw()
        self.on_change()

    def select_all(self) -> None:
        if self.words:
            self.select_range(0, len(self.words) - 1)

    def select_line(self, idx: int) -> None:
        w = self.words[idx]
        key = (w.block, w.par, w.line)
        same = [i for i, o in enumerate(self.words) if (o.block, o.par, o.line) == key]
        self.select_range(same[0], same[-1])

    # -- geometry -------------------------------------------------------------------------
    def layout(self, width: int, height: int) -> None:
        margin = 16
        scale = min(2.0, (width - 2 * margin) / self.iw, (height - 2 * margin) / self.ih)
        self.scale = max(0.05, scale)
        self.ox = (width - self.iw * self.scale) / 2
        self.oy = (height - self.ih * self.scale) / 2

    def to_screen(self, ix: float, iy: float) -> tuple[float, float]:
        return self.ox + ix * self.scale, self.oy + iy * self.scale

    def word_at(self, x: float, y: float, nearest: bool = False) -> int | None:
        ix, iy = (x - self.ox) / self.scale, (y - self.oy) / self.scale
        pad = 3 / self.scale
        best, best_d = None, math.inf
        for i, w in enumerate(self.words):
            if (
                w.left - pad <= ix <= w.left + w.width + pad
                and w.top - pad <= iy <= w.top + w.height + pad
            ):
                return i
            if nearest:
                dx = max(w.left - ix, 0, ix - (w.left + w.width))
                dy = max(w.top - iy, 0, iy - (w.top + w.height))
                d = math.hypot(dx, dy * 2)  # prefer words on the same line
                if d < best_d:
                    best, best_d = i, d
        return best

    # -- input ----------------------------------------------------------------------------
    def _on_begin(self, _g, x: float, y: float) -> None:
        self.grab_focus()
        self._origin = (x, y)
        self._drag_anchor = None

    def _on_update(self, _g, dx: float, dy: float) -> None:
        if self._drag_anchor is None and math.hypot(dx, dy) < 4:
            return
        ox, oy = self._origin
        if self._drag_anchor is None:
            self._drag_anchor = self.word_at(ox, oy, nearest=True)
        idx = self.word_at(ox + dx, oy + dy, nearest=True)
        if idx is not None and self._drag_anchor is not None:
            self.select_range(self._drag_anchor, idx)

    def _on_click(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        if self._drag_anchor is not None:  # that was the end of a drag
            self._drag_anchor = None
            return
        idx = self.word_at(x, y)
        if idx is None:
            self.select_range(None, None)
        elif n_press >= 3:
            self.select_all()
        elif n_press == 2:
            self.select_line(idx)
        else:
            self.select_range(idx, idx)

    def _on_motion(self, _c, x: float, y: float) -> None:
        idx = self.word_at(x, y)
        if idx != self.hover:
            self.hover = idx
            self.queue_draw()

    # -- drawing --------------------------------------------------------------------------
    def _draw(self, _area, ctx: cairo.Context, width: int, height: int) -> None:
        self.layout(width, height)
        accent = theme.current().rgb()
        # the picture, with the same soft shadow and hairline edge as in the editor
        x0, y0 = round(self.ox), round(self.oy)
        w, h = round(self.iw * self.scale), round(self.ih * self.scale)
        chrome.soft_shadow(ctx, x0, y0, w, h, 0, depth=12, strength=0.2)
        ctx.set_line_width(1)
        ctx.set_source_rgba(0, 0, 0, 0.16)
        ctx.rectangle(x0 - 0.5, y0 - 0.5, w + 1, h + 1)
        ctx.stroke()

        ctx.save()
        ctx.translate(self.ox, self.oy)
        ctx.scale(self.scale, self.scale)
        ctx.set_source_surface(self.surface, 0, 0)
        ctx.paint()
        sel = self.selection
        pad = 2
        radius = 3 / self.scale
        # every word that can be picked gets a whisper of color; the one under the pointer more
        for i, word in enumerate(self.words):
            if sel and sel[0] <= i <= sel[1]:
                continue
            chrome.rounded_rect(
                ctx,
                word.left - pad,
                word.top - pad,
                word.width + 2 * pad,
                word.height + 2 * pad,
                radius,
            )
            ctx.set_source_rgba(*accent, 0.22 if i == self.hover else 0.07)
            ctx.fill()
        # the selection: one bar per line of text, like selected text anywhere else
        if sel:
            lines: dict[tuple[int, int, int], list[Word]] = {}
            for word in self.words[sel[0] : sel[1] + 1]:
                lines.setdefault((word.block, word.par, word.line), []).append(word)
            ctx.set_source_rgba(*accent, 0.4)
            for words in lines.values():
                left = min(word.left for word in words) - pad
                top = min(word.top for word in words) - pad
                right = max(word.left + word.width for word in words) + pad
                bottom = max(word.top + word.height for word in words) + pad
                chrome.rounded_rect(ctx, left, top, right - left, bottom - top, radius)
                ctx.fill()
        ctx.restore()


class TextWindow(Adw.Window):
    """Shows recognized text. With an image and word boxes, words can be picked on the image."""

    def __init__(
        self, app, text: str, image: Image.Image | None = None, words: list[Word] | None = None
    ) -> None:
        super().__init__(application=app, title=_("Copy Text"))
        self.app = app
        self.words = words or []
        has_picker = image is not None and bool(self.words)
        if has_picker:
            text = join_words(self.words)
            w, h = image.size
            self.set_default_size(max(560, min(1100, w + 60)), max(420, min(800, h + 170)))
        else:
            self.set_default_size(560, 420)

        self.copy_button = Gtk.Button(label=_("Copy Selected"))
        self.copy_button.add_css_class("suggested-action")
        self.copy_button.connect("clicked", lambda *_: self.copy_selected())
        copy_all = Gtk.Button(label=_("Copy All"))
        copy_all.connect("clicked", lambda *_: self.copy_all())
        header = Adw.HeaderBar()
        header.pack_end(self.copy_button)
        header.pack_end(copy_all)

        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.set_top_margin(12)
        self.view.set_bottom_margin(12)
        self.view.set_left_margin(14)
        self.view.set_right_margin(14)
        buf = self.view.get_buffer()
        buf.set_text(text)
        buf.connect("notify::has-selection", lambda *_: self._sync())
        text_page = Gtk.ScrolledWindow(child=self.view, vexpand=True)

        self.stack = Adw.ViewStack()
        self.picker: WordPicker | None = None
        if has_picker:
            self.picker = WordPicker(image, self.words, self._sync)
            hint = Gtk.Label(
                label=_(
                    "Drag across words to select them · double-click selects a line · "
                    "Ctrl+A selects all · Ctrl+C copies"
                ),
                margin_top=6,
                margin_bottom=8,
            )
            hint.add_css_class("gh-tool-tip")
            image_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            image_page.append(self.picker)
            image_page.append(hint)
            self.stack.add_titled_with_icon(
                image_page, "image", _("On Image"), "image-x-generic-symbolic"
            )
            self.stack.add_titled_with_icon(
                text_page, "text", _("As Text"), "text-x-generic-symbolic"
            )
            header.set_title_widget(Adw.ViewSwitcher(stack=self.stack))
            self.stack.connect("notify::visible-child", lambda *_: self._sync())
        else:
            self.stack.add_named(text_page, "text")

        self.toasts = Adw.ToastOverlay(child=self.stack)
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._sync()
        if self.picker is not None:
            self.picker.grab_focus()

    @property
    def on_image(self) -> bool:
        return self.picker is not None and self.stack.get_visible_child_name() == "image"

    def selected_text(self) -> str:
        if self.on_image:
            return join_words(self.picker.selected_words())
        buf = self.view.get_buffer()
        bounds = buf.get_selection_bounds()
        return buf.get_text(*bounds, False) if bounds else ""

    def all_text(self) -> str:
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _sync(self) -> None:
        self.copy_button.set_sensitive(bool(self.selected_text()))

    def _copy(self, text: str) -> None:
        if not text:
            return
        self.app.copy_text(text, self)
        count = len(text.split())
        title = ngettext("Copied {n} word", "Copied {n} words", count)
        self.toasts.add_toast(Adw.Toast(title=title.format(n=count), timeout=2))

    def copy_selected(self) -> None:
        self._copy(self.selected_text())

    def copy_all(self) -> None:
        self._copy(self.all_text())

    def _on_key(self, _ctrl, keyval: int, code: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if not self.on_image or not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        keyval = keys.latin_keyval(keyval, code)  # Ctrl+A, Ctrl+C on any keyboard layout
        if keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.picker.select_all()
            return True
        if keyval in (Gdk.KEY_c, Gdk.KEY_C):
            if self.picker.selection:
                self.copy_selected()
            else:
                self.copy_all()
            return True
        return False
