# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The quick action bar shown next to a selection: labelled tiles with their keys."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Gtk

from grabbyhands.style import section_card, tile_button, tooltip

if TYPE_CHECKING:
    from grabbyhands.capture import CaptureController


def groups() -> list[tuple[str, list[tuple[str, str, str, str, str]]]]:
    """(heading, [(action, icon, label, key, what it does)])."""
    return [
        (
            _("SHARE"),
            [
                ("copy", "gh-copy-symbolic", _("Copy"), "Ctrl+C", _("Copy the image")),
                ("save", "gh-save-symbolic", _("Save"), "S", _("Save to your folder")),
                ("pin", "gh-pin-symbolic", _("Pin"), "P", _("Keep it floating on screen")),
            ],
        ),
        (
            _("MARK UP"),
            [
                ("edit", "gh-edit-symbolic", _("Edit"), "E", _("Draw arrows, boxes, text, blur…")),
                (
                    "redact",
                    "gh-redact-symbolic",
                    _("Redact"),
                    "X",
                    _(
                        "Opens the editor and hides emails, phone numbers, card numbers and "
                        "keys for you. Use the Blur tool there for anything else."
                    ),
                ),
            ],
        ),
        (
            _("READ"),
            [
                ("ocr", "gh-ocr-symbolic", _("Text"), "T", _("Pick text in the image to copy")),
                (
                    "qr",
                    "gh-qr-symbolic",
                    _("QR Code"),
                    "Q",
                    _("Read a QR code or barcode and copy what it says"),
                ),
            ],
        ),
    ]


def actions() -> list[tuple[str, str, str, str, str]]:
    return [a for _title, items in groups() for a in items]


def action_name(action: str) -> str:
    """The full name of an action, as used in tooltips, hints and the shortcut list.

    Tiles keep their short labels; these are the names the rest of the app uses for the
    same features (the editor's Auto-Redact, the Copy Text window).
    """
    full = {
        "redact": _("Auto-Redact"),
        "ocr": _("Copy Text"),
        "qr": _("Read QR Code"),
        "save-as": _("Save As…"),
    }
    if action in full:
        return full[action]
    return next((label for a, _icon, label, _key, _what in actions() if a == action), action)


KEYS = {
    ("return", False): "default",
    ("kp_enter", False): "default",
    ("c", True): "copy",
    ("e", False): "edit",
    ("x", False): "redact",
    ("t", False): "ocr",
    ("q", False): "qr",
    ("p", False): "pin",
    ("s", False): "save",
    ("s", True): "save-as",
}


class QuickBar(Gtk.Box):
    def __init__(self, controller: CaptureController, window: Gtk.Window) -> None:
        super().__init__(spacing=6)
        self.add_css_class("gh-bar")
        self.controller = controller
        self.window = window
        self.buttons: dict[str, Gtk.Button] = {}
        default = controller.default_action
        for title, items in groups():
            tiles = []
            for action, icon, label, key, what in items:
                if not controller.supports(action):
                    continue
                is_default = action == default
                # The default action answers to Enter as well as its own key.
                keys = _("Enter or {key}").format(key=key) if is_default else key
                button = tile_button(icon, label, tooltip(action_name(action), what, keys))
                if is_default:
                    button.add_css_class("gh-primary")
                button.connect(
                    "clicked", lambda _b, a=action: self.controller.run_action(a, self.window)
                )
                self.buttons[action] = button
                tiles.append(button)
            if tiles:
                self.append(section_card(title, tiles))

        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        utility = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        save_as = Gtk.Button(icon_name="gh-save-as-symbolic")
        save_as.add_css_class("gh-close")
        save_as.set_focusable(False)
        save_as.set_tooltip_markup(
            tooltip(_("Save As…"), _("Choose the format, quality and size"), "Ctrl+S")
        )
        save_as.connect("clicked", lambda *_: self.controller.run_action("save-as", self.window))
        self.buttons["save-as"] = save_as
        cancel = Gtk.Button(icon_name="gh-close-symbolic")
        cancel.add_css_class("gh-close")
        cancel.add_css_class("gh-cancel")
        cancel.set_focusable(False)
        cancel.set_tooltip_markup(tooltip(_("Cancel"), _("Close without capturing"), "Esc"))
        cancel.connect("clicked", lambda *_: self.controller.cancel())
        utility.append(save_as)
        utility.append(cancel)
        self.append(utility)

    def handle_key(self, name: str, ctrl: bool) -> bool:
        action = KEYS.get((name, ctrl))
        if action is None:
            return False
        if action == "default":
            self.controller.run_default_action(self.window)
        elif self.controller.supports(action):
            self.controller.run_action(action, self.window)
        return True
