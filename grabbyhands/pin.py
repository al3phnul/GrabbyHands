# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Pin a capture to the screen as a small floating window.

Wayland doesn't let apps force "always on top"; the window menu (Alt+Space)
has "Always on Top", and we point users there.
"""

from __future__ import annotations

from gi.repository import Gdk, Gio, GLib, Gtk

from grabbyhands import keys
from grabbyhands.imaging import png_bytes

ZOOM_STEP = 1.15


class PinWindow(Gtk.Window):
    def __init__(self, app, image, entry=None) -> None:
        super().__init__(application=app, title=_("Pinned Screenshot"))
        self.app = app
        self.image = image
        self.entry = entry
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("gh-pin")

        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png_bytes(image)))
        self.picture = Gtk.Picture.new_for_paintable(texture)
        self.picture.set_content_fit(Gtk.ContentFit.FILL)
        self.picture.set_can_shrink(True)

        frame = Gtk.Box()
        frame.add_css_class("gh-pin-frame")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        frame.append(self.picture)
        frame.set_margin_top(14)
        frame.set_margin_bottom(14)
        frame.set_margin_start(14)
        frame.set_margin_end(14)
        self.set_child(frame)

        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        scale = monitor.get_scale_factor() if monitor else 1
        self.base_w = image.width / max(1, scale)
        self.base_h = image.height / max(1, scale)
        if monitor is not None:
            geo = monitor.get_geometry()
            fit = min(1.0, geo.width * 0.6 / self.base_w, geo.height * 0.6 / self.base_h)
        else:
            fit = 1.0
        self.zoom = fit
        self._apply_zoom()

        press = Gtk.GestureClick()
        press.set_button(0)
        press.connect("pressed", self._on_pressed)
        self.picture.add_controller(press)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.picture.add_controller(scroll)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

        self._install_menu()
        self.picture.set_tooltip_text(
            _(
                "Drag to move · Scroll to zoom · Ctrl+scroll to see through it\n"
                "Right-click for options · Esc or middle-click closes it\n"
                "Keep it on top: press Alt+Space, then choose Always on Top"
            )
        )

    def _install_menu(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, callback in {
            "copy": lambda *_: self.app.copy_image(self.image, self),
            "save": lambda *_: self.app.save_as(self.image, self.entry, self),
            "edit": lambda *_: self._edit(),
            "reset": lambda *_: self._set_zoom(1.0),
            "close": lambda *_: self.close(),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            group.add_action(action)
        self.insert_action_group("pin", group)

        menu = Gio.Menu()
        menu.append(_("Copy"), "pin.copy")
        menu.append(_("Save As…"), "pin.save")
        if self.entry is not None and self.app.supports("edit"):
            menu.append(_("Edit"), "pin.edit")
        menu.append(_("Actual Size"), "pin.reset")
        # Wayland doesn't let an app put its own window on top, so say how the user can.
        # (No action behind it: the item is there to be read.)
        on_top = Gio.Menu()
        on_top.append(_("Keep on Top: Alt+Space, then Always on Top"), "pin.on-top-hint")
        menu.append_section(None, on_top)
        section = Gio.Menu()
        section.append(_("Close"), "pin.close")
        menu.append_section(None, section)
        self.popover = Gtk.PopoverMenu.new_from_model(menu)
        self.popover.set_parent(self.picture)
        self.popover.set_has_arrow(False)

    def _edit(self) -> None:
        if self.entry is not None:
            self.app.open_editor(self.entry)
            self.close()

    def _apply_zoom(self) -> None:
        self.picture.set_size_request(
            max(32, round(self.base_w * self.zoom)), max(32, round(self.base_h * self.zoom))
        )

    def _set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.1, min(zoom, 4.0))
        self._apply_zoom()

    def _on_pressed(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_SECONDARY:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            self.popover.set_pointing_to(rect)
            self.popover.popup()
        elif button == Gdk.BUTTON_MIDDLE:
            self.close()
        elif button == Gdk.BUTTON_PRIMARY:
            if n_press == 2:
                self._set_zoom(1.0)
                return
            surface = self.get_surface()
            if isinstance(surface, Gdk.Toplevel):
                # PyGObject returns (x, y), or None if the widgets aren't related.
                point = gesture.get_widget().translate_coordinates(self, x, y)
                wx, wy = point if point else (x, y)
                surface.begin_move(
                    gesture.get_device(), button, wx, wy, gesture.get_current_event_time()
                )

    def _on_scroll(self, ctrl: Gtk.EventControllerScroll, _dx: float, dy: float) -> bool:
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.set_opacity(max(0.2, min(1.0, self.get_opacity() - dy * 0.08)))
        else:
            self._set_zoom(self.zoom / ZOOM_STEP if dy > 0 else self.zoom * ZOOM_STEP)
        return True

    def _on_key(self, _ctrl, keyval: int, code: int, state: Gdk.ModifierType) -> bool:
        name = (Gdk.keyval_name(keys.latin_keyval(keyval, code)) or "").lower()
        if name == "escape":
            self.close()
            return True
        if name == "c" and state & Gdk.ModifierType.CONTROL_MASK:
            self.app.copy_image(self.image, self)
            return True
        return False
