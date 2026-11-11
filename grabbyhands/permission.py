# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""One-time "Allow screenshots" window.

GNOME only lets the *focused* app show the screenshot permission dialog. GrabbyHands normally
runs in the background without a window, so the first capture is refused. This window gives
the user something to click, which focuses the app, so GNOME can ask for permission.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from grabbyhands import APP_ID

if TYPE_CHECKING:
    from grabbyhands.application import GrabbyHandsApp


class PermissionWindow(Adw.Window):
    def __init__(
        self,
        app: GrabbyHandsApp,
        on_granted: Callable[[str], None],
        on_closed: Callable[[], None],
    ) -> None:
        super().__init__(
            application=app,
            title=_("Allow Screenshots"),
            default_width=440,
            resizable=False,
        )
        self.app = app
        self._on_granted = on_granted
        self._on_closed = on_closed
        self._done = False
        self._closed = False

        self.status = Adw.StatusPage(
            icon_name=APP_ID,
            title=_("Allow Screenshots"),
            description=_(
                "GrabbyHands needs your permission once before it can take screenshots.\n"
                "Click the button, then choose <b>Allow</b> in the dialog that appears."
            ),
        )
        self.status.add_css_class("compact")
        self.button = Gtk.Button(
            label=_("Allow Screenshots"),
            halign=Gtk.Align.CENTER,
            css_classes=["suggested-action", "pill"],
        )
        self.button.connect("clicked", self._ask)
        self.status.set_child(self.button)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_title=False))
        view.set_content(self.status)
        self.set_content(view)
        self.set_default_widget(self.button)
        self.connect("close-request", self._closing)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1]),
            )
        )
        self.add_controller(esc)

    def _ask(self, _button: Gtk.Button) -> None:
        self.button.set_sensitive(False)
        self.app.portal.screenshot(False, self._answered)

    def _answered(self, uri: str | None, _response: int) -> None:
        if self._closed:
            # The answer came after the window was closed. Nothing to show any more, but
            # the desktop still wrote a screenshot file, which must not be left lying about.
            if uri:
                self._on_granted(uri)
            return
        if uri:
            self._done = True
            self.close()
            self._on_granted(uri)
            return
        self.button.set_sensitive(True)
        self.status.set_description(
            _(
                "Permission wasn't granted, so GrabbyHands can't take screenshots yet.\n"
                "Click the button to try again, or turn on <b>Screenshots</b> for GrabbyHands "
                "in GNOME Settings → Apps."
            )
        )
        self.button.grab_focus()

    def _closing(self, _win: Gtk.Window) -> bool:
        self._closed = True
        if not self._done:
            self._on_closed()
        return False
