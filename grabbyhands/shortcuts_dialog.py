# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Keyboard Shortcuts: every key GrabbyHands understands, in one findable list.

The lists are built from the same tables the features use (the action bar's keys, the
editor's tools) wherever possible, so this window can't drift away from what the keys do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, GLib

from grabbyhands import quickbar
from grabbyhands.style import keycap

if TYPE_CHECKING:
    from grabbyhands.application import GrabbyHandsApp

Section = tuple[str, str, list[tuple[str, str]]]


def capture_keys(has_windows: bool = True) -> list[tuple[str, str]]:
    """Keys on the capture screen, before and while an area is selected."""
    rows = [
        (_("Drag"), _("Select an area")),
        ("Space", _("Select the whole screen")),
    ]
    if has_windows:
        rows.append(("W", _("Pick a window")))
    rows += [
        ("R", _("Select the last area again")),
        ("I", _("Identify Color")),
        ("C", _("Copy the color under the pointer")),
        ("D", _("Capture after a delay")),
        ("H", _("Open History")),
        (_("Arrow keys"), _("Move the selection (with Shift: 10 pixels at a time)")),
        (_("Alt+Arrow keys"), _("Resize the selection")),
        (_("Right-click"), _("Clear the selection, or cancel")),
        ("Esc", _("Cancel")),
    ]
    return rows


def action_keys(supports=lambda _action: True) -> list[tuple[str, str]]:
    """Keys that act on the selected area (the action bar)."""
    rows = [(_("Enter"), _("Your default action (also: double-click the selection)"))]
    for action, _icon, _label, key, _what in quickbar.actions():
        if supports(action):
            rows.append((key, quickbar.action_name(action)))
            if action == "save":
                rows.append(("Ctrl+S", quickbar.action_name("save-as")))
    return rows


def editor_tool_keys() -> list[tuple[str, str]]:
    from grabbyhands.editor.window import TOOLS

    return [(key, label) for _id, _icon, label, key, _section, _how in TOOLS]


def editor_keys() -> list[tuple[str, str]]:
    return [
        ("Ctrl+Z", _("Undo")),
        ("Ctrl+Shift+Z", _("Redo")),
        ("Ctrl+C", _("Copy the finished image")),
        ("Ctrl+S", _("Save")),
        ("Ctrl+Shift+S", _("Save As…")),
        ("Ctrl+Shift+R", _("Auto-Redact")),
        ("Delete", _("Remove the selected item")),
        (_("Arrow keys"), _("Move the selected item (with Shift: 10 pixels at a time)")),
        (_("Shift while drawing"), _("Straight lines, squares and circles")),
        (_("Double-click"), _("Edit a text or callout")),
        (_("Esc or Ctrl+Enter"), _("Finish typing")),
        ("Enter", _("Apply the crop")),
        ("Esc", _("Deselect, then go back to Select")),
        (_("Ctrl+Scroll"), _("Zoom")),
        ("Ctrl+0", _("Fit the image in the window")),
        ("Ctrl+1", _("Actual size")),
        (_("Space+Drag"), _("Move around when zoomed in (or drag with the middle button)")),
        ("Ctrl+W", _("Close the editor (your changes are saved)")),
    ]


def history_keys() -> list[tuple[str, str]]:
    return [
        (_("Enter or double-click"), _("Edit the capture")),
        ("Ctrl+C", _("Copy the capture")),
        ("Delete", _("Delete the capture")),
        (_("Right-click"), _("More actions")),
        ("Esc", _("Close History")),
    ]


def text_keys() -> list[tuple[str, str]]:
    return [
        (_("Drag"), _("Select words")),
        (_("Double-click"), _("Select a line")),
        ("Ctrl+A", _("Select everything")),
        ("Ctrl+C", _("Copy the selection (or everything)")),
        ("Esc", _("Close")),
    ]


def pin_keys() -> list[tuple[str, str]]:
    return [
        (_("Drag"), _("Move")),
        (_("Scroll"), _("Zoom")),
        (_("Ctrl+Scroll"), _("See-through")),
        (_("Double-click"), _("Actual size")),
        ("Ctrl+C", _("Copy")),
        (_("Right-click"), _("More actions")),
        (_("Esc or middle-click"), _("Close")),
        ("Alt+Space", _("Window menu: choose Always on Top to keep it above other windows")),
    ]


def sections(app: GrabbyHandsApp | None = None) -> list[Section]:
    """``(title, description, [(keys, what they do)])`` for the whole app."""
    if app is not None:
        global_rows = [
            (keys or _("Not set"), name) for _id, name, keys, _bound in app.shortcut_list()
        ]
        state = app.shortcut_state()
        has_windows = _window_helper_on()
        supports = app.supports
    else:
        from grabbyhands.application import SHORTCUT_DEFAULT_KEYS, _shortcut_specs

        global_rows = [(SHORTCUT_DEFAULT_KEYS[sid], name) for sid, name, _t in _shortcut_specs()]
        state, has_windows, supports = "ready", True, lambda _action: True
    anywhere = {
        "ready": _("These work at any time, even when no GrabbyHands window is open."),
        "unavailable": _(
            "This desktop doesn't let apps set these up. See Getting Started for another way."
        ),
    }.get(state, _("Not set up yet. Open Getting Started to turn them on."))
    return [
        (_("Anywhere"), anywhere, global_rows),
        (_("While Capturing"), "", capture_keys(has_windows)),
        (_("With an Area Selected"), "", action_keys(supports)),
        (_("Editor Tools"), _("Press a letter to switch tools."), editor_tool_keys()),
        (_("Editor"), "", editor_keys()),
        (_("History"), "", history_keys()),
        (_("Copy Text"), "", text_keys()),
        (_("Pinned Screenshot"), "", pin_keys()),
    ]


def _window_helper_on() -> bool:
    from grabbyhands import windows

    return windows.available()


def key_row(keys: str, what: str) -> Adw.ActionRow:
    """A list row: what a key does on the left, the key drawn like a keyboard key."""
    row = Adw.ActionRow(title=GLib.markup_escape_text(what))
    row.set_activatable(False)
    row.add_suffix(keycap(keys))
    return row


class ShortcutsDialog(Adw.Dialog):
    def __init__(self, app: GrabbyHandsApp) -> None:
        super().__init__(title=_("Keyboard Shortcuts"), content_width=560, content_height=640)
        self.app = app
        self.groups: dict[str, Adw.PreferencesGroup] = {}
        page = Adw.PreferencesPage()
        for title, description, rows in sections(app):
            group = Adw.PreferencesGroup(title=title)
            if description:
                group.set_description(description)
            for keys, what in rows:
                group.add(key_row(keys, what))
            page.add(group)
            self.groups[title] = group
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
