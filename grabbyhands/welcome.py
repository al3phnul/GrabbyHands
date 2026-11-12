# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Getting Started: shown once on first launch, and any time from History or Settings.

It answers what a newcomer can't know: what GrabbyHands is, which keys it listens to,
whether those keys are active yet, and how to give it the Print Screen key on GNOME (where
that key belongs to GNOME's own screenshot tool until the user says otherwise).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk

from grabbyhands import APP_ID, config, gnome_keys
from grabbyhands.dialogs import when_closed
from grabbyhands.shortcuts_dialog import key_row

if TYPE_CHECKING:
    from grabbyhands.application import GrabbyHandsApp


# What a hand-made system shortcut should run for each of our global shortcuts.
COMMAND_OPTIONS = {
    "capture": "--capture",
    "repeat-region": "--repeat",
    "color-picker": "--pick-color",
    "capture-text": "--ocr",
}


def shortcut_command(shortcut_id: str = "capture") -> str:
    """The command that does what a global shortcut does (for custom system shortcuts)."""
    program = f"flatpak run {APP_ID}" if config.in_flatpak() else "grabbyhands"
    return f"{program} {COMMAND_OPTIONS[shortcut_id]}"


def missing_extras(app: GrabbyHandsApp) -> list[str]:
    """Optional features that are off right now, each with what it needs (see Settings)."""
    from grabbyhands import windows

    missing = []
    if not windows.available():
        missing.append(_("Capturing a single window needs the Window Helper"))
    if not app.supports("ocr"):
        missing.append(_("Copy Text and Auto-Redact need Tesseract"))
    if not app.supports("qr"):
        missing.append(_("Reading QR codes needs zxing-cpp"))
    return missing


class WelcomeDialog(Adw.Dialog):
    def __init__(self, app: GrabbyHandsApp) -> None:
        super().__init__(title=_("Getting Started"), content_width=560, content_height=700)
        self.app = app
        self._previous: list[str] | None = None  # GNOME's shortcut before we moved it
        self._key_widgets: list[Gtk.Widget] = []
        self._print_widgets: list[Gtk.Widget] = []
        self.setup_button: Gtk.Button | None = None
        self.print_button: Gtk.Button | None = None
        self.undo_button: Gtk.Button | None = None
        self.reset_button: Gtk.Button | None = None
        self.command_rows: dict[str, Adw.ActionRow] = {}

        page = Adw.PreferencesPage()
        page.add(self._intro_group())
        self.keys_group = Adw.PreferencesGroup(title=_("Shortcuts That Work Anywhere"))
        self.print_group = Adw.PreferencesGroup(title=_("The Print Screen Key"))
        # When Print Screen still needs handing over, that comes first: it is the one step
        # a newcomer can't guess. (Decided once, so nothing jumps around afterward.)
        if gnome_keys.status() == gnome_keys.HELD:
            page.add(self.print_group)
            page.add(self.keys_group)
        else:
            page.add(self.keys_group)
            page.add(self.print_group)
        page.add(self._tips_group())

        self.toasts = Adw.ToastOverlay(child=page)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        self.set_default_widget(self.capture_button)
        self.set_focus(self.capture_button)

        self.refresh()
        unwatch = app.watch_shortcuts(self.refresh)
        when_closed(self, unwatch)

    # -- fixed parts ----------------------------------------------------------------------
    def _intro_group(self) -> Adw.PreferencesGroup:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(APP_ID)
        icon.set_pixel_size(72)
        box.append(icon)
        heading = Gtk.Label(label=_("Welcome to GrabbyHands"))
        heading.add_css_class("title-1")
        box.append(heading)
        about = Gtk.Label(
            label=_(
                "Grab any part of your screen, then copy it, save it, pin it or mark it up. "
                "Nothing ever leaves your computer."
            ),
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        box.append(about)
        self.capture_button = Gtk.Button(label=_("Take a Screenshot"), halign=Gtk.Align.CENTER)
        self.capture_button.add_css_class("suggested-action")
        self.capture_button.add_css_class("pill")
        self.capture_button.set_margin_top(6)
        self.capture_button.connect("clicked", lambda *_: self._take_screenshot())
        box.append(self.capture_button)
        group = Adw.PreferencesGroup()
        group.add(box)
        return group

    def _tips_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Good to Know"))
        background = Adw.ActionRow(
            title=_("GrabbyHands stays ready in the background"),
            subtitle=_(
                "Closing its windows doesn't quit it, so your shortcuts keep working. "
                "It also starts when you log in; you can turn that off in Settings."
            ),
        )
        permission = Adw.ActionRow(
            title=_("Your first screenshot asks for permission"),
            subtitle=_("Your desktop asks once whether GrabbyHands may take screenshots."),
        )
        for row in (background, permission):
            row.set_activatable(False)
            group.add(row)
        # Features that are simply absent until something is installed or turned on:
        # say so here, or a newcomer never learns they exist.
        self.extras_row: Adw.ActionRow | None = None
        missing = missing_extras(self.app)
        if missing:
            self.extras_row = Adw.ActionRow(
                title=_("Some extras are waiting to be turned on"),
                subtitle=GLib.markup_escape_text(" · ".join(missing)),
                activatable=True,
            )
            self.extras_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            self.extras_row.connect("activated", lambda *_: self._show_settings())
            group.add(self.extras_row)
        self.all_keys_row = self._link_row(_("All Keyboard Shortcuts"), self._show_all_keys)
        group.add(self.all_keys_row)
        self.settings_row = self._link_row(_("Settings"), self._show_settings)
        group.add(self.settings_row)
        return group

    @staticmethod
    def _link_row(title: str, callback) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, activatable=True)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda *_: callback())
        return row

    def _command_row(self, name: str, title: str, command: str) -> Adw.ActionRow:
        """A command to copy, for the cases where we can't do the step ourselves."""
        row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(command))
        row.add_css_class("property")
        row.set_subtitle_selectable(True)
        copy = Gtk.Button(
            icon_name="gh-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Copy")
        )
        copy.add_css_class("flat")
        copy.connect("clicked", lambda *_: self._copy(command))
        row.add_suffix(copy)
        row.command = command  # for tests
        self.command_rows[name] = row
        return row

    @staticmethod
    def _action_button(label: str, callback) -> Gtk.Button:
        button = Gtk.Button(label=label, halign=Gtk.Align.START, margin_top=10)
        button.connect("clicked", lambda *_: callback())
        return button

    # -- the parts that follow the system's state -------------------------------------------
    def refresh(self) -> None:
        self._refresh_keys()
        self._refresh_print()

    def _refresh_keys(self) -> None:
        group = self.keys_group
        for widget in self._key_widgets:
            group.remove(widget)
        self._key_widgets = []
        self.setup_button = None
        for shortcut_id in COMMAND_OPTIONS:
            self.command_rows.pop(shortcut_id, None)

        def add(widget: Gtk.Widget) -> None:
            group.add(widget)
            self._key_widgets.append(widget)

        state = self.app.shortcut_state()
        if state == "unavailable":
            # No keys to show: the user makes the shortcuts, so show what each should run.
            group.set_description(
                _(
                    "This desktop doesn't let apps set up shortcuts themselves. You can add "
                    "them in your system's keyboard settings: make a custom shortcut for each "
                    "of these commands, with any keys you like."
                )
            )
            for shortcut_id, name, _keys, _bound in self.app.shortcut_list():
                add(self._command_row(shortcut_id, name, shortcut_command(shortcut_id)))
            return

        for _id, name, keys, bound in self.app.shortcut_list():
            row = key_row(keys or _("Not set"), name)
            if not bound:
                row.set_subtitle(_("Not active yet"))
            add(row)

        if state == "ready":
            group.set_description(
                _("Press them at any time, even when no GrabbyHands window is open.")
            )
        else:
            if state == "declined":
                text = _(
                    "These aren't active because your desktop's shortcut dialog was dismissed. "
                    "Set them up to use them."
                )
            else:
                text = _(
                    "Your desktop asks you to confirm these once. If you don't see its "
                    "dialog, press Set Up Shortcuts."
                )
            group.set_description(text)
            self.setup_button = self._action_button(_("Set Up Shortcuts"), self._setup_shortcuts)
            add(self.setup_button)

    def _refresh_print(self) -> None:
        group = self.print_group
        for widget in self._print_widgets:
            group.remove(widget)
        self._print_widgets = []
        self.print_button = self.undo_button = self.reset_button = None
        for name in ("print", "print-undo"):
            self.command_rows.pop(name, None)

        def add(widget: Gtk.Widget) -> None:
            group.add(widget)
            self._print_widgets.append(widget)

        status = gnome_keys.status()
        can_change = gnome_keys.can_change()
        group.set_visible(True)
        if status == gnome_keys.HELD and can_change:
            group.set_description(
                _(
                    "Right now Print Screen opens GNOME's own screenshot tool. GrabbyHands can "
                    "move that tool to Ctrl+Print Screen, so that Print Screen opens "
                    "GrabbyHands instead. You can undo this at any time."
                )
            )
            self.print_button = self._action_button(
                _("Use Print Screen for GrabbyHands"), self._release_print
            )
            # the step below "Take a Screenshot", which stays the one primary button
            self.print_button.add_css_class("gh-tinted")
            add(self.print_button)
        elif status in (gnome_keys.HELD, gnome_keys.UNKNOWN):
            group.set_description(
                _(
                    "On GNOME, Print Screen opens GNOME's own screenshot tool until you move "
                    "that tool to another key. GrabbyHands can't change this from here, but "
                    "you can: run the first command in a terminal (the second one undoes it)."
                )
            )
            add(self._command_row("print", _("Move GNOME's tool"), gnome_keys.command()))
            add(self._command_row("print-undo", _("Undo"), gnome_keys.undo_command()))
        elif status == gnome_keys.FREE and self._previous is not None:
            if self.app.shortcut_state() == "unavailable":
                done = _(
                    "Done. GNOME's own screenshot tool now opens with Ctrl+Print Screen, and "
                    "Print Screen is free. Give it to the “Take a screenshot” command below "
                    "in GNOME Settings → Keyboard → View and Customize Shortcuts → Custom "
                    "Shortcuts."
                )
            else:
                done = _(
                    "Done. GNOME's own screenshot tool now opens with Ctrl+Print Screen, and "
                    "Print Screen is free for GrabbyHands. If GNOME asks, confirm the "
                    "shortcuts. If Print Screen doesn't respond yet, log out and back in once."
                )
            group.set_description(done)
            changed = Adw.ActionRow(
                title=_("What changed"),
                subtitle=_(
                    "GNOME Settings → Keyboard → View and Customize Shortcuts → Screenshots → "
                    "“Take a screenshot interactively” is now Ctrl+Print Screen."
                ),
            )
            changed.set_activatable(False)
            self.undo_button = Gtk.Button(label=_("Undo"), valign=Gtk.Align.CENTER)
            self.undo_button.connect("clicked", lambda *_: self._undo_release())
            changed.add_suffix(self.undo_button)
            add(changed)
        elif status == gnome_keys.FREE and gnome_keys.is_customized() and can_change:
            # group descriptions are markup, and an odd shortcut may contain "<"
            keys = GLib.markup_escape_text(gnome_keys.describe(gnome_keys.current() or []))
            keys = keys or _("no key")
            group.set_description(
                _(
                    "Print Screen is free for GrabbyHands: GNOME's own screenshot tool uses "
                    "{keys}. To change that, open GNOME Settings → Keyboard → View and "
                    "Customize Shortcuts → Screenshots, or give the key back here."
                ).format(keys=keys)
            )
            self.reset_button = self._action_button(
                _("Give Print Screen Back to GNOME"), self._reset_print
            )
            add(self.reset_button)
        else:
            group.set_visible(False)  # not GNOME, or nothing to set up

    # -- actions --------------------------------------------------------------------------
    def _toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def _copy(self, text: str) -> None:
        self.app.copy_text(text, self)
        self._toast(_("Copied"))

    def _take_screenshot(self) -> None:
        self.close()
        self.app.capture_from_ui()

    def _setup_shortcuts(self) -> None:
        self.app.setup_shortcuts_again()
        self._toast(_("Your desktop may ask you to confirm the shortcuts"))

    def _release_print(self) -> None:
        """The user clicked the button: move GNOME's tool to Ctrl+Print Screen."""
        previous = gnome_keys.release_print()
        if previous is None:
            self._toast(_("Couldn't change GNOME's shortcut"))
            self._refresh_print()
            return
        self._previous = previous
        self._refresh_print()
        # Now that the key is free, ask the desktop to (re)bind our shortcuts.
        self.app.setup_shortcuts_again()

    def _undo_release(self) -> None:
        if gnome_keys.restore(self._previous):
            self._toast(_("Print Screen opens GNOME's screenshot tool again"))
        self._previous = None
        self._refresh_print()

    def _reset_print(self) -> None:
        if gnome_keys.restore(None):
            self._toast(_("GNOME's screenshot shortcut is back to its default"))
        self._previous = None
        self._refresh_print()

    def _show_all_keys(self) -> None:
        parent = self.get_root()
        self.close()
        self.app.show_shortcuts(parent if isinstance(parent, Gtk.Window) else None)

    def _show_settings(self) -> None:
        parent = self.get_root()
        self.close()
        self.app.show_preferences(parent if isinstance(parent, Gtk.Window) else None)
