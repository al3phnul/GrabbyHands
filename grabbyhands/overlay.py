# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Full-screen capture overlay: frozen screen, selection, loupe, toolbars.

One overlay window is opened per monitor. Each shows its part of the frozen
screenshot. Coordinates inside a window are logical pixels; the screenshot is in
physical pixels. ``ScreenMapping`` converts between the two.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cairo
from gi.repository import Gdk, Gtk, PangoCairo

from grabbyhands import APP_ID, chrome, keys, theme
from grabbyhands.colors import to_cmyk, to_hex
from grabbyhands.i18n import ngettext
from grabbyhands.imaging import Rect, ScreenImage
from grabbyhands.quickbar import QuickBar, action_name
from grabbyhands.style import (
    bar_label_color,
    section_card,
    settings_button,
    tile_button,
    tooltip,
)

if TYPE_CHECKING:
    from grabbyhands.capture import CaptureController

HANDLE = 6.0
HANDLE_HIT = 12.0
MIN_SIZE = 3
LOUPE_CELLS = 17
LOUPE_CELLS_COLOR = 21  # Identify Color: a bigger view, as wide as its three values
LOUPE_ZOOM = 9
DIM = 0.55  # how far the screen outside the selection is darkened
HINT_BAND = 84  # height of the strip at the bottom of the screen the hint needs
HINT_SEP = "   ·   "

HANDLE_CURSORS = {
    "nw": "nw-resize",
    "n": "n-resize",
    "ne": "ne-resize",
    "e": "e-resize",
    "se": "se-resize",
    "s": "s-resize",
    "sw": "sw-resize",
    "w": "w-resize",
}


class ScreenMapping:
    """Maps monitor-local logical coordinates to screenshot pixels."""

    def __init__(self, monitors: list[Gdk.Rectangle], width: int, height: int) -> None:
        self.ox = min(g.x for g in monitors)
        self.oy = min(g.y for g in monitors)
        span_w = max(g.x + g.width for g in monitors) - self.ox
        span_h = max(g.y + g.height for g in monitors) - self.oy
        self.sx = width / span_w if span_w else 1.0
        self.sy = height / span_h if span_h else 1.0

    def monitor_rect(self, geo: Gdk.Rectangle) -> Rect:
        return Rect(
            round((geo.x - self.ox) * self.sx),
            round((geo.y - self.oy) * self.sy),
            round(geo.width * self.sx),
            round(geo.height * self.sy),
        )


class OverlayWindow(Gtk.Window):
    """mode: 'select' | 'color' | 'window' (click a window) | 'flash' (repeat-last feedback)."""

    def __init__(
        self,
        controller: CaptureController,
        screen: ScreenImage,
        mapping: ScreenMapping,
        monitor: Gdk.Monitor,
        mode: str = "select",
        selection: Rect | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(application=controller.app)
        self.controller = controller
        self.screen = screen
        self.monitor = monitor
        self.mode = mode
        self.custom_hint = hint
        self.set_title("GrabbyHands")
        self.set_decorated(False)
        self.add_css_class("gh-overlay")

        geo = monitor.get_geometry()
        self.geo = geo
        self.mon_img = mapping.monitor_rect(geo)
        self.sx, self.sy = mapping.sx, mapping.sy

        self.sel: Rect | None = None
        if selection is not None:
            self.set_selection(selection, notify=False)
        self.pointer: tuple[float, float] | None = None
        self._drag_kind: str | None = None
        self._drag_start_sel: Rect | None = None
        self._drag_origin: tuple[float, float] = (0.0, 0.0)
        self._dragging = False
        self._drag_ignored = False  # a press that arrived while busy: its release is nothing
        self.busy = False
        self._quickbar_bottom = 0.0
        # Only promise keys that will do something in this capture.
        self._has_last_region = controller.last_region() is not None

        overlay = Gtk.Overlay()
        self.set_child(overlay)
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self._draw)
        overlay.set_child(self.area)

        self.toolbar = self._build_toolbar()
        overlay.add_overlay(self.toolbar)

        self.quickbar = QuickBar(controller, self)
        self.quickbar.set_halign(Gtk.Align.START)
        self.quickbar.set_valign(Gtk.Align.START)
        self.quickbar.set_visible(False)
        overlay.add_overlay(self.quickbar)

        self.hint = Gtk.Label()
        self.hint.add_css_class("gh-hint")
        self.hint.set_halign(Gtk.Align.CENTER)
        self.hint.set_valign(Gtk.Align.END)
        self.hint.set_margin_bottom(28)
        self.hint.set_can_target(False)
        overlay.add_overlay(self.hint)

        self.busy_box = Gtk.Box(spacing=12)
        self.busy_box.add_css_class("gh-busy")
        self.busy_box.set_halign(Gtk.Align.CENTER)
        self.busy_box.set_valign(Gtk.Align.CENTER)
        self._spinner = Gtk.Spinner()
        self._busy_label = Gtk.Label()
        self.busy_box.append(self._spinner)
        self.busy_box.append(self._busy_label)
        self.busy_box.set_visible(False)
        overlay.add_overlay(self.busy_box)

        if mode in ("select", "color", "window"):
            self._setup_input()
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

        self._update_chrome()
        self.fullscreen_on_monitor(monitor)

    # -- geometry -----------------------------------------------------------------
    def to_image(self, px: float, py: float) -> tuple[float, float]:
        return self.mon_img.x + px * self.sx, self.mon_img.y + py * self.sy

    def to_local(self, ix: float, iy: float) -> tuple[float, float]:
        return (ix - self.mon_img.x) / self.sx, (iy - self.mon_img.y) / self.sy

    def local_sel(self) -> tuple[float, float, float, float] | None:
        if self.sel is None:
            return None
        x, y = self.to_local(self.sel.x, self.sel.y)
        return x, y, self.sel.w / self.sx, self.sel.h / self.sy

    def local_to_image_rect(self, x: float, y: float, w: float, h: float) -> Rect:
        ix0, iy0 = self.to_image(x, y)
        ix1, iy1 = self.to_image(x + w, y + h)
        return Rect.from_points(round(ix0), round(iy0), round(ix1), round(iy1))

    def hovered_window(self) -> tuple[float, float, float, float, str] | None:
        """Window under the pointer in local coordinates, or this whole screen."""
        if self.pointer is None:
            return None
        px, py = self.pointer
        found = self.controller.window_at(self.geo.x + px, self.geo.y + py)
        if found is None:
            return 0.0, 0.0, float(self.geo.width), float(self.geo.height), _("Whole screen")
        return (
            float(found.x - self.geo.x),
            float(found.y - self.geo.y),
            float(found.width),
            float(found.height),
            found.app,
        )

    def set_selection(self, rect: Rect | None, notify: bool = True) -> None:
        if rect is not None:
            rect = rect.clamp(self.screen.width, self.screen.height)
            inside = Rect(
                max(rect.x, self.mon_img.x),
                max(rect.y, self.mon_img.y),
                0,
                0,
            )
            x1 = min(rect.x + rect.w, self.mon_img.x + self.mon_img.w)
            y1 = min(rect.y + rect.h, self.mon_img.y + self.mon_img.h)
            rect = Rect(inside.x, inside.y, x1 - inside.x, y1 - inside.y)
            if rect.empty:
                rect = None
        self.sel = rect
        if notify:
            self._update_chrome()
            self.area.queue_draw()

    def selection_or_full(self) -> Rect:
        return self.sel or self.mon_img

    # -- chrome -------------------------------------------------------------------
    def _build_toolbar(self) -> Gtk.Box:
        bar = Gtk.Box(spacing=6)
        bar.add_css_class("gh-bar")
        bar.set_halign(Gtk.Align.CENTER)
        bar.set_valign(Gtk.Align.START)
        bar.set_margin_top(18)

        brand = Gtk.Image.new_from_icon_name(APP_ID)
        brand.set_pixel_size(28)
        brand.set_valign(Gtk.Align.CENTER)
        brand.set_margin_start(4)
        brand.set_margin_end(2)
        brand.set_tooltip_text("GrabbyHands")
        bar.append(brand)

        # -- Capture: what to grab ---------------------------------------------------
        self.btn_region = tile_button(
            "gh-region-symbolic",
            _("Region"),
            tooltip(
                _("Region"),
                _("Drag over the part of the screen you want. R selects the last area again."),
            ),
        )
        self.btn_region.connect("clicked", lambda *_: self.controller.set_mode("select"))
        screen_btn = tile_button(
            "gh-screen-symbolic",
            _("Screen"),
            tooltip(_("Whole screen"), _("Select everything on this screen"), "Space"),
        )
        screen_btn.connect("clicked", lambda *_: self.select_full())
        self.btn_window = tile_button(
            "gh-window-symbolic",
            _("Window"),
            tooltip(_("Window"), _("Hover a window to highlight it, click to select it"), "W"),
        )
        self.btn_window.connect(
            "clicked",
            lambda *_: self.controller.set_mode("select" if self.mode == "window" else "window"),
        )
        default_delay = self.controller.delay_seconds

        def seconds(n: int) -> str:
            return ngettext("{n} second", "{n} seconds", n).format(n=n)

        delay = tile_button(
            "gh-timer-symbolic",
            _("Delay"),
            tooltip(
                _("Delay"),
                _(
                    "Wait a few seconds first, e.g. to open a menu. "
                    "Click to choose how long; D waits {delay}."
                ).format(delay=seconds(default_delay)),
                "D",
            ),
        )
        pop = Gtk.Popover()
        pop.add_css_class("gh-popover")
        pop.set_parent(delay)
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.delay_buttons: dict[int, Gtk.Button] = {}
        # The usual choices plus the user's own default, which is what the D key uses.
        for secs in sorted({3, 5, 10, default_delay}):
            label = seconds(secs)
            if secs == default_delay:
                label = _("{delay} (D key)").format(delay=label)
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, s=secs: (pop.popdown(), self.controller.delayed(s)))
            pop_box.append(b)
            self.delay_buttons[secs] = b
        pop.set_child(pop_box)
        delay.connect("clicked", lambda *_: pop.popup())
        self.delay_popover = pop
        capture_tiles = [self.btn_region, screen_btn]
        if self.controller.has_windows:
            capture_tiles.append(self.btn_window)
        capture_tiles.append(delay)
        bar.append(section_card(_("CAPTURE"), capture_tiles))

        # -- Tools ---------------------------------------------------------------------
        self.btn_color = tile_button(
            "gh-color-symbolic",
            _("Identify Color"),
            tooltip(_("Identify Color"), _("See the HEX, RGB and CMYK values of any pixel"), "I"),
        )
        self.btn_color.connect(
            "clicked",
            lambda *_: self.controller.set_mode("select" if self.mode == "color" else "color"),
        )
        tool_tiles = [self.btn_color]
        if self.controller.supports("history"):
            history = tile_button(
                "gh-history-symbolic",
                _("History"),
                tooltip(_("History"), _("Your earlier captures, ready to copy or edit"), "H"),
            )
            history.connect("clicked", lambda *_: self.controller.show_history())
            tool_tiles.append(history)
        bar.append(section_card(_("TOOLS"), tool_tiles))

        # -- Utility: set apart from the tools -------------------------------------------
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        settings = settings_button()
        settings.set_focusable(False)  # keys belong to the capture, like every other button here
        settings.connect("clicked", lambda *_: self.controller.show_settings())
        bar.append(settings)
        close = Gtk.Button(icon_name="gh-close-symbolic", valign=Gtk.Align.CENTER)
        close.add_css_class("gh-close")
        close.add_css_class("gh-cancel")
        close.set_focusable(False)
        close.set_tooltip_markup(tooltip(_("Cancel"), _("Close without capturing"), "Esc"))
        close.connect("clicked", lambda *_: self.controller.cancel())
        bar.append(close)
        return bar

    def _update_chrome(self) -> None:
        if self.mode == "flash":
            self.toolbar.set_visible(False)
            self.hint.set_visible(False)
            self.quickbar.set_visible(False)
            return
        for btn, active in (
            (self.btn_region, self.mode == "select"),
            (self.btn_window, self.mode == "window"),
            (self.btn_color, self.mode == "color"),
        ):
            if active:
                btn.add_css_class("gh-active")
            else:
                btn.remove_css_class("gh-active")
        dragging = self._dragging
        self.toolbar.set_visible(not dragging and not self.busy)
        show_bar = self.mode == "select" and self.sel is not None and not dragging and not self.busy
        self.quickbar.set_visible(show_bar)
        if show_bar:
            self._place_quickbar()
        self.hint.set_text(self.hint_text())
        show_hint = not dragging and not self.busy
        if show_hint and self.sel is not None:
            # With an area selected the hint explains what to do next, but it never
            # sits on top of the selection or the action bar.
            show_hint = self.mode == "select" and self._hint_has_room()
        self.hint.set_visible(show_hint)

    def hint_text(self) -> str:
        """What the user can do right now. Only keys that work in this capture are named."""
        if self.mode == "window":
            parts = [
                _("Click a window to select it"),
                _("W: back to dragging an area"),
                _("Esc: cancel"),
            ]
        elif self.mode == "color":
            parts = [
                _("Identify Color: click a pixel to see its HEX, RGB and CMYK values"),
                _("C: copy its {format} value right away").format(
                    format=self.controller.color_format.upper()
                ),
                _("Esc: cancel"),
            ]
        elif self.sel is not None:
            parts = [
                _("Enter: {action}").format(action=action_name(self.controller.default_action)),
                _("Drag the dots to adjust"),
                _("Arrow keys: nudge"),
                _("Right-click: start over"),
                _("Esc: cancel"),
            ]
        elif self.custom_hint:
            return self.custom_hint
        else:
            parts = [_("Drag to select"), _("Space: whole screen")]
            if self.controller.has_windows:
                parts.append(_("W: window"))
            if self._has_last_region:
                parts.append(_("R: last area"))
            parts += [_("I: identify color"), _("Esc: cancel")]
        return HINT_SEP.join(parts)

    def _hint_has_room(self) -> bool:
        rect = self.local_sel()
        if rect is None:
            return True
        band_top = (self.get_height() or self.geo.height) - HINT_BAND
        _x, y, _w, h = rect
        return y + h <= band_top and self._quickbar_bottom <= band_top

    def _place_quickbar(self) -> bool:
        rect = self.local_sel()
        if rect is None:
            return False
        x, y, w, h = rect
        win_w = self.get_width() or self.geo.width
        win_h = self.get_height() or self.geo.height
        _min, nat_w, _b1, _b2 = self.quickbar.measure(Gtk.Orientation.HORIZONTAL, -1)
        _min, nat_h, _b1, _b2 = self.quickbar.measure(Gtk.Orientation.VERTICAL, nat_w)
        # measure() includes the margins we position the bar with. Take the old ones off,
        # or every second placement thinks the bar is huge and throws it to the top.
        nat_w -= self.quickbar.get_margin_start()
        nat_h -= self.quickbar.get_margin_top()
        gap = 12
        if y + h + gap + nat_h + 8 <= win_h:
            top = y + h + gap
        elif y - gap - nat_h >= 8:
            top = y - gap - nat_h
        else:
            top = y + h - nat_h - gap
        left = x + w / 2 - nat_w / 2
        left = max(8, min(left, win_w - nat_w - 8))
        self.quickbar.set_margin_start(int(left))
        self.quickbar.set_margin_top(int(max(8, top)))
        self._quickbar_bottom = max(8, top) + nat_h
        return False

    def show_busy(self, text: str) -> None:
        """Working on something: a spinner and what is happening."""
        self.busy = True
        self._busy_label.set_text(text)
        self._spinner.set_visible(True)
        self._spinner.start()
        self.busy_box.set_visible(True)
        self._update_chrome()

    def show_message(self, text: str) -> None:
        """A result or a problem, in the same place but without the spinner (nothing is
        still running). The caller hides it or closes the capture after a moment."""
        self.busy = True
        self._busy_label.set_text(text)
        self._spinner.stop()
        self._spinner.set_visible(False)
        self.busy_box.set_visible(True)
        self._update_chrome()

    def hide_busy(self) -> None:
        self.busy = False
        self._spinner.stop()
        self.busy_box.set_visible(False)
        self._update_chrome()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode in ("color", "window"):
            self.sel = None
        self._update_cursor()
        self._update_chrome()
        self.area.queue_draw()

    # -- input --------------------------------------------------------------------
    def _setup_input(self) -> None:
        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

        double = Gtk.GestureClick()
        double.set_button(Gdk.BUTTON_PRIMARY)
        double.connect("pressed", self._on_primary_pressed)
        self.area.add_controller(double)

        right = Gtk.GestureClick()
        right.set_button(Gdk.BUTTON_SECONDARY)
        right.connect("pressed", self._on_right_click)
        self.area.add_controller(right)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.area.add_controller(motion)

    def _hit_handle(self, px: float, py: float) -> str | None:
        rect = self.local_sel()
        if rect is None:
            return None
        x, y, w, h = rect
        points = {
            "nw": (x, y),
            "n": (x + w / 2, y),
            "ne": (x + w, y),
            "e": (x + w, y + h / 2),
            "se": (x + w, y + h),
            "s": (x + w / 2, y + h),
            "sw": (x, y + h),
            "w": (x, y + h / 2),
        }
        for name, (hx, hy) in points.items():
            if abs(px - hx) <= HANDLE_HIT and abs(py - hy) <= HANDLE_HIT:
                return name
        if x <= px <= x + w and y <= py <= y + h:
            return "move"
        return None

    def _update_cursor(self) -> None:
        name = "crosshair"
        if self.mode == "select" and self.pointer is not None and not self._dragging:
            hit = self._hit_handle(*self.pointer)
            if hit == "move":
                name = "move"
            elif hit:
                name = HANDLE_CURSORS[hit]
        elif self._dragging and self._drag_kind not in (None, "new"):
            name = "move" if self._drag_kind == "move" else HANDLE_CURSORS[self._drag_kind]
        self.area.set_cursor_from_name(name)

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        self.pointer = (x, y)
        self._update_cursor()
        self.area.queue_draw()

    def _on_leave(self, _ctrl) -> None:
        self.pointer = None
        self.area.queue_draw()

    def _on_primary_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        if self.mode == "select" and n_press == 2 and self._hit_handle(x, y) == "move":
            self.controller.run_default_action(self)

    def _on_right_click(self, _gesture, _n, _x, _y) -> None:
        if self.sel is not None and self.mode == "select":
            self.set_selection(None)
        else:
            self.controller.cancel()

    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        self._drag_ignored = self.busy
        if self.busy:
            return
        self._drag_origin = (x, y)
        self._drag_start_sel = self.sel
        if self.mode in ("color", "window"):
            self._drag_kind = None
            return
        hit = self._hit_handle(x, y)
        self._drag_kind = hit or "new"
        if self._drag_kind == "new":
            self.controller.selection_started(self)

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._drag_kind is None:
            return
        if not self._dragging and math.hypot(dx, dy) < 3:
            return
        self._dragging = True
        ox, oy = self._drag_origin
        if self._drag_kind == "new":
            ix0, iy0 = self.to_image(ox, oy)
            ix1, iy1 = self.to_image(ox + dx, oy + dy)
            self.set_selection(Rect.from_points(ix0, iy0, ix1, iy1), notify=False)
        else:
            start = self._drag_start_sel
            if start is None:
                return
            ddx, ddy = dx * self.sx, dy * self.sy
            if self._drag_kind == "move":
                nx = min(
                    max(start.x + ddx, self.mon_img.x), self.mon_img.x + self.mon_img.w - start.w
                )
                ny = min(
                    max(start.y + ddy, self.mon_img.y), self.mon_img.y + self.mon_img.h - start.h
                )
                self.sel = Rect(round(nx), round(ny), start.w, start.h)
            else:
                x0, y0, x1, y1 = start.x, start.y, start.x + start.w, start.y + start.h
                if "w" in self._drag_kind:
                    x0 += ddx
                if "e" in self._drag_kind:
                    x1 += ddx
                if "n" in self._drag_kind:
                    y0 += ddy
                if "s" in self._drag_kind:
                    y1 += ddy
                self.set_selection(Rect.from_points(x0, y0, x1, y1), notify=False)
        self.pointer = (ox + dx, oy + dy)
        self._update_chrome()
        self._update_cursor()
        self.area.queue_draw()

    def _on_drag_end(self, _gesture, dx: float, dy: float) -> None:
        was_dragging = self._dragging
        kind = self._drag_kind
        self._dragging = False
        self._drag_kind = None
        if self._drag_ignored:
            # e.g. a click while "Copied #7c6cff" is showing: it has no starting point,
            # and used to open the color window for wherever the last click had been
            self._drag_ignored = False
            return
        if self.mode == "color" and not was_dragging:
            ox, oy = self._drag_origin
            ix, iy = self.to_image(ox, oy)
            self.controller.show_color(self.screen.pixel(int(ix), int(iy)))
            return
        if self.mode == "window":
            self.pointer = self._drag_origin
            hovered = self.hovered_window()
            if hovered is not None:
                self.controller.window_selected(self, self.local_to_image_rect(*hovered[:4]))
            return
        if kind == "new" and (self.sel is None or self.sel.w < MIN_SIZE or self.sel.h < MIN_SIZE):
            self.sel = self._drag_start_sel if not was_dragging else None
        self._update_chrome()
        self._update_cursor()
        self.area.queue_draw()

    def select_full(self) -> None:
        self._select(self.mon_img)

    def select_last_region(self) -> bool:
        """Select the area captured last time (the R key). False if there isn't one."""
        region = self.controller.last_region()
        if region is None:
            return False
        # on the screen it was on, which need not be the one the key was pressed on
        self.controller.window_showing(region, self)._select(region)
        return True

    def _select(self, rect: Rect) -> None:
        if self.mode != "select":  # from Identify Color or window picking: back to areas
            self.controller.set_mode("select")
        self.controller.selection_started(self)
        self.set_selection(rect)

    def _nudge(self, dx: int, dy: int, resize: bool) -> None:
        if self.sel is None:
            return
        s = self.sel
        if resize:
            rect = Rect(s.x, s.y, max(MIN_SIZE, s.w + dx), max(MIN_SIZE, s.h + dy))
        else:
            # Stop at the edge of this screen. (Clamping the moved box instead made it
            # one step smaller with every press, until the selection was gone.)
            m = self.mon_img
            nx = max(m.x, min(s.x + dx, m.x + m.w - s.w))
            ny = max(m.y, min(s.y + dy, m.y + m.h - s.h))
            rect = Rect(nx, ny, s.w, s.h)
        self.set_selection(rect)

    def _on_key(self, _ctrl, keyval: int, keycode: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        # (letters as on a Latin layout, so the keys work with any keyboard layout)
        name = (Gdk.keyval_name(keys.latin_keyval(keyval, keycode)) or "").lower()

        if name == "escape":
            if self.delay_popover.get_visible():
                self.delay_popover.popdown()  # Esc closes the little menu first
            else:
                self.controller.cancel()
            return True
        if self.busy:
            return True
        if self.mode == "flash":
            return True
        # With several screens, the keyboard is on one overlay while the selection (or the
        # pointer) may be on another: the key is meant for that one.
        target = self.controller.key_window(self)
        if target is not self:
            return target._on_key(_ctrl, keyval, keycode, state)

        if name == "c" and not ctrl:
            if self.mode == "select" and self.sel is not None:
                # With an area selected, C means "copy the picture", same as Ctrl+C.
                self.controller.run_action("copy", self)
            elif self.pointer is not None:
                ix, iy = self.to_image(*self.pointer)
                self.controller.copy_color(self, self.screen.pixel(int(ix), int(iy)))
            return True
        if name == "i":
            self.controller.set_mode("select" if self.mode == "color" else "color")
            return True
        if name in ("space", "f"):
            self.select_full()
            return True
        if name == "w":
            if self.controller.has_windows:
                self.controller.set_mode("select" if self.mode == "window" else "window")
            return True
        if name == "r":
            self.select_last_region()
            return True
        if name == "d":
            self.controller.delayed(None)
            return True
        if name == "h" and self.controller.supports("history"):
            self.controller.show_history()
            return True
        arrows = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}
        if name in arrows and self.sel is not None:
            step = 10 if shift else 1
            ax, ay = arrows[name]
            self._nudge(ax * step, ay * step, resize=alt)
            return True
        if self.sel is not None and self.mode == "select":
            return self.quickbar.handle_key(name, ctrl)
        return False

    # -- drawing ------------------------------------------------------------------
    def _paint_screen(self, ctx: cairo.Context) -> None:
        ctx.save()
        ctx.scale(1 / self.sx, 1 / self.sy)
        ctx.set_source_surface(self.screen.surface, -self.mon_img.x, -self.mon_img.y)
        ctx.paint()
        ctx.restore()

    def _draw(self, _area, ctx: cairo.Context, width: int, height: int) -> None:
        self._paint_screen(ctx)
        if self.mode == "window":
            self._draw_window_mode(ctx, width, height)
            return
        rect = self.local_sel()
        if self.mode == "flash":
            ctx.set_source_rgba(1, 1, 1, 0.18)
            ctx.paint()
            if rect:
                self._draw_selection_border(ctx, rect, handles=False)
            return

        ctx.set_source_rgba(*theme.current().rgb("chrome"), DIM)
        ctx.rectangle(0, 0, width, height)
        if rect is not None:
            ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            ctx.rectangle(*rect)
        ctx.fill()
        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)

        if rect is not None:
            self._draw_selection_border(
                ctx, rect, handles=not self._dragging or self._drag_kind != "new"
            )
            self._draw_size_label(ctx, rect)
        elif self.pointer is not None and self.mode == "select":
            # guide lines through the pointer: a hairline on whole pixels
            px, py = self.pointer
            ctx.set_source_rgba(*theme.current().rgb("line"), 0.6)
            ctx.set_line_width(1)
            ctx.move_to(0, round(py) + 0.5)
            ctx.line_to(width, round(py) + 0.5)
            ctx.move_to(round(px) + 0.5, 0)
            ctx.line_to(round(px) + 0.5, height)
            ctx.stroke()

        if self.pointer is not None and self._wants_loupe():
            self._draw_loupe(ctx, *self.pointer, width, height)

    def _wants_loupe(self) -> bool:
        if self.mode == "color":
            return True
        if self.mode == "window":
            return False
        if not self.controller.show_loupe:
            return False
        if self._dragging:
            return self._drag_kind != "move"  # creating or resizing: precision matters
        return self.sel is None

    def _draw_selection_border(self, ctx: cairo.Context, rect, handles: bool) -> None:
        x, y, w, h = rect
        # Pull edges that touch the screen border inwards so a whole-screen selection
        # still shows a frame and grabbable handles.
        win_w = self.get_width() or self.geo.width
        win_h = self.get_height() or self.geo.height
        inset = HANDLE + 2
        x0, y0 = max(x, inset), max(y, inset)
        x1, y1 = min(x + w, win_w - inset), min(y + h, win_h - inset)
        if x1 > x0 and y1 > y0:
            x, y, w, h = x0, y0, x1 - x0, y1 - y0
        accent = theme.current().rgb("line")
        chrome.frame(ctx, x, y, w, h, accent)
        if not handles:
            return
        x, y, w, h = round(x), round(y), round(w), round(h)
        for hx, hy in (
            (x, y),
            (x + w / 2, y),
            (x + w, y),
            (x + w, y + h / 2),
            (x + w, y + h),
            (x + w / 2, y + h),
            (x, y + h),
            (x, y + h / 2),
        ):
            chrome.handle(ctx, hx, hy, accent)

    def _draw_size_label(self, ctx: cairo.Context, rect) -> None:
        if self.sel is None:
            return
        x, y, _w, _h = rect
        text = f"{self.sel.w} × {self.sel.h}"
        _tw, th = chrome.text_size(chrome.text_layout(ctx, text))
        chip_h = math.ceil(th) + 8
        # above the top-left corner, clear of its handle; inside when there is no room
        if y >= chip_h + 16:
            chrome.chip(ctx, x - theme.STROKE, y - chip_h - 12, text)
        else:
            chrome.chip(ctx, x + 12, y + 12, text)

    def _loupe_rows(self, rgb: tuple[int, int, int], ix: int, iy: int) -> list[tuple[str, str]]:
        """What the magnifier says under its picture: (label, value) lines."""
        if self.mode != "color":
            return [(f"{ix}, {iy}", to_hex(rgb))]
        c, m, y, k = to_cmyk(rgb)
        return [
            ("HEX", to_hex(rgb).upper()),
            ("RGB", f"{rgb[0]}, {rgb[1]}, {rgb[2]}"),
            ("CMYK", f"{c}%, {m}%, {y}%, {k}%"),
        ]

    def _draw_loupe(
        self, ctx: cairo.Context, px: float, py: float, width: int, height: int
    ) -> None:
        """The magnifier: one panel with the enlarged pixels on top and their values below."""
        p = theme.current()
        color_mode = self.mode == "color"
        ix, iy = self.to_image(px, py)
        ix, iy = int(ix), int(iy)
        cells = LOUPE_CELLS_COLOR if color_mode else LOUPE_CELLS
        size = cells * LOUPE_ZOOM
        half = cells // 2
        rgb = self.screen.pixel(ix, iy)
        rows = self._loupe_rows(rgb, ix, iy)
        pad, row_gap = 10, 3
        labels = [chrome.text_layout(ctx, label, 9, bold=not color_mode) for label, _v in rows]
        values = [chrome.text_layout(ctx, value, 9) for _l, value in rows]
        row_h = max(chrome.text_size(layout)[1] for layout in labels + values)
        band = 12 if color_mode else 0  # a strip of the exact color, right under the pixels
        text_h = len(rows) * row_h + (len(rows) - 1) * row_gap
        info_h = math.ceil(band + 8 + text_h + 8)
        total_h = size + info_h

        lx, ly = round(px) + 26, round(py) + 26
        if lx + size > width - 8:
            lx = round(px) - 26 - size
        # The hint at the bottom and the toolbar at the top are widgets drawn on top of this
        # layer: stay out from under them. Below the pointer if there is room, else above
        # it, else beside it.
        floor = height - (HINT_BAND if self.hint.get_visible() else 8)
        ceiling = 8
        if self.toolbar.get_visible():
            found, bar = self.toolbar.compute_bounds(self)
            if found and lx < bar.get_x() + bar.get_width() and lx + size > bar.get_x():
                ceiling = math.ceil(bar.get_y() + bar.get_height()) + 8
        if ly + total_h > floor:
            ly = round(py) - 26 - total_h
            if ly < ceiling:
                ly = max(ceiling, floor - total_h)
        radius = theme.RADIUS_L

        chrome.soft_shadow(ctx, lx, ly, size, total_h, radius, depth=8)
        ctx.save()
        chrome.rounded_rect(ctx, lx, ly, size, total_h, radius)
        ctx.clip()
        ctx.set_source_rgb(*p.rgb("chrome"))
        ctx.paint()

        # the enlarged pixels
        ctx.save()
        ctx.rectangle(lx, ly, size, size)
        ctx.clip()
        ctx.set_source_rgb(0, 0, 0)
        ctx.paint()
        ctx.translate(lx, ly)
        ctx.scale(LOUPE_ZOOM, LOUPE_ZOOM)
        ctx.set_source_surface(self.screen.surface, -(ix - half), -(iy - half))
        ctx.get_source().set_filter(cairo.FILTER_NEAREST)
        ctx.paint()
        ctx.restore()

        # cross-hair toward the middle pixel: dark under light, so it shows on anything
        cx, cy = lx + half * LOUPE_ZOOM, ly + half * LOUPE_ZOOM
        mid = LOUPE_ZOOM / 2
        for line_width, color in ((3, (0, 0, 0, 0.22)), (1, (1, 1, 1, 0.7))):
            ctx.set_line_width(line_width)
            ctx.set_source_rgba(*color)
            ctx.move_to(lx, cy + mid)
            ctx.line_to(cx - 2, cy + mid)
            ctx.move_to(cx + LOUPE_ZOOM + 2, cy + mid)
            ctx.line_to(lx + size, cy + mid)
            ctx.move_to(cx + mid, ly)
            ctx.line_to(cx + mid, cy - 2)
            ctx.move_to(cx + mid, cy + LOUPE_ZOOM + 2)
            ctx.line_to(cx + mid, ly + size)
            ctx.stroke()
        # the middle pixel: a white box inside a dark one
        ctx.set_line_width(1)
        ctx.set_source_rgba(0, 0, 0, 0.85)
        ctx.rectangle(cx - 1.5, cy - 1.5, LOUPE_ZOOM + 3, LOUPE_ZOOM + 3)
        ctx.stroke()
        ctx.set_source_rgb(1, 1, 1)
        ctx.rectangle(cx - 0.5, cy - 0.5, LOUPE_ZOOM + 1, LOUPE_ZOOM + 1)
        ctx.stroke()

        # the values
        top = ly + size
        if band:
            ctx.set_source_rgb(*(c / 255 for c in rgb))
            ctx.rectangle(lx, top, size, band)
            ctx.fill()
        ctx.set_line_width(1)
        ctx.set_source_rgba(0, 0, 0, 0.5)  # hairlines between picture, color and values
        for line_y in {top + 0.5, top + band + 0.5}:
            ctx.move_to(lx, line_y)
            ctx.line_to(lx + size, line_y)
        ctx.stroke()
        label_w = max(chrome.text_size(layout)[0] for layout in labels)
        y = top + band + 8
        label_rgb = theme._rgb(bar_label_color(p))
        for label, value in zip(labels, values, strict=True):
            vw = chrome.text_size(value)[0]
            if color_mode:  # two columns: dim name, then the value
                ctx.set_source_rgb(*(c / 255 for c in label_rgb))
                ctx.move_to(lx + pad, y)
                PangoCairo.show_layout(ctx, label)
                vx = lx + pad + label_w + 10
            else:  # position on the left, a dot of the color and its HEX on the right
                ctx.set_source_rgba(*p.rgb("chrome_fg"), 0.78)
                ctx.move_to(lx + pad, y)
                PangoCairo.show_layout(ctx, label)
                vx = lx + size - pad - vw
                dot = 5
                ctx.new_sub_path()
                ctx.arc(vx - dot - 5, y + row_h / 2, dot, 0, 2 * math.pi)
                ctx.set_source_rgb(*(c / 255 for c in rgb))
                ctx.fill_preserve()
                ctx.set_source_rgba(1, 1, 1, 0.55)
                ctx.set_line_width(1)
                ctx.stroke()
            ctx.set_source_rgb(*p.rgb("chrome_fg"))
            ctx.move_to(vx, y)
            PangoCairo.show_layout(ctx, value)
            y += row_h + row_gap
        ctx.restore()
        chrome.frame(ctx, lx, ly, size, total_h, p.rgb("line"), radius)

    def _draw_window_mode(self, ctx: cairo.Context, width: int, height: int) -> None:
        hovered = self.hovered_window()
        ctx.set_source_rgba(*theme.current().rgb("chrome"), DIM)
        ctx.rectangle(0, 0, width, height)
        if hovered is not None:
            ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            ctx.rectangle(*hovered[:4])
        ctx.fill()
        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        if hovered is None:
            return
        x, y, w, h, app = hovered
        self._draw_selection_border(ctx, (x, y, w, h), handles=False)
        size = f"{round(w * self.sx)} × {round(h * self.sy)}"
        name, detail = (app, size) if app else (size, "")
        # In the middle of the window, where it can't sit on the window's own title bar.
        left, right = max(x, 0), min(x + w, width)
        top, bottom = max(y, 0), min(y + h, height)
        chrome.chip(ctx, (left + right) / 2, (top + bottom) / 2, name, detail, 11, center=True)
