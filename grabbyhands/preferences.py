# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Settings (the preferences dialog)."""

from __future__ import annotations

import logging
import shutil
import threading
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from grabbyhands import APP_ID, config, export, ocr, qr, theme, windows
from grabbyhands.dialogs import when_closed

if TYPE_CHECKING:
    from grabbyhands.application import GrabbyHandsApp

log = logging.getLogger(__name__)

DEFAULT_ACTIONS = [
    ("copy", _("Copy to clipboard")),
    ("edit", _("Open in the editor")),
    ("save", _("Save to file")),
    ("pin", _("Pin to screen")),
]
COLOR_FORMATS = [("hex", "HEX"), ("rgb", "RGB"), ("cmyk", "CMYK"), ("hsl", "HSL")]
FILE_FORMATS = [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WebP")]
# Apps that switch GNOME Shell extensions on and off, best first. (org.gnome.Shell.Extensions
# is not one of them: it is a name-and-icon stub that can't be launched.)
EXTENSION_APPS = ("org.gnome.Extensions.desktop", "com.mattjakeman.ExtensionManager.desktop")
# Why the system menu's screenshot button can't be switched (windows.menu_button()).
MENU_BUTTON_NOTES = {
    "no-helper": _("Needs the Window Helper, below"),
    "old-helper": _("Log out and back in once to finish updating the Window Helper"),
    "unavailable": _("This version of GNOME doesn't have that button where GrabbyHands looks"),
}
LANGUAGE_NAMES = {
    "eng": "English",
    "deu": "Deutsch",
    "fra": "Français",
    "spa": "Español",
    "ita": "Italiano",
    "por": "Português",
    "nld": "Nederlands",
    "pol": "Polski",
    "rus": "Русский",
    "ukr": "Українська",
    "tur": "Türkçe",
    "swe": "Svenska",
    "dan": "Dansk",
    "nor": "Norsk",
    "fin": "Suomi",
    "ces": "Čeština",
    "jpn": "日本語",
    "chi_sim": "简体中文",
    "chi_tra": "繁體中文",
    "kor": "한국어",
    "ara": "العربية",
    "heb": "עברית",
    "hin": "हिन्दी",
}


def _combo(
    title: str, choices: list[tuple[str, str]], current: str, on_change, subtitle: str = ""
) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _v, label in choices]))
    if subtitle:
        row.set_subtitle(subtitle)
    values = [value for value, _label in choices]
    row.set_selected(values.index(current) if current in values else 0)
    row.connect("notify::selected", lambda r, _p: on_change(values[r.get_selected()]))
    return row


def extensions_app() -> Gio.DesktopAppInfo | None:
    """An installed app that can turn GNOME Shell extensions on, if there is one."""
    for desktop_id in EXTENSION_APPS:
        try:
            info = Gio.DesktopAppInfo.new(desktop_id)
        except (TypeError, AttributeError):
            # TypeError: PyGObject raises when the constructor returns NULL (not installed).
            # AttributeError: a GLib whose Gio has no DesktopAppInfo (it moved to GioUnix).
            info = None
        if info is not None:
            return info
    return None


def _launch(info: Gio.DesktopAppInfo) -> bool:
    try:
        return info.launch([], None)
    except GLib.Error as err:
        log.info("could not open %s: %s", info.get_id(), err.message)
        return False


def _link_row(title: str, subtitle: str, callback) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
    row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
    row.connect("activated", lambda *_: callback())
    return row


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app: GrabbyHandsApp) -> None:
        super().__init__(title=_("Settings"), search_enabled=True)
        self.app = app
        self.settings = app.settings
        self.add(self._appearance_page())
        self.add(self._general_page())
        self.add(self._saving_page())
        self.add(self._text_page())
        self.add(self._smart_edit_page())
        unwatch = app.watch_shortcuts(self._refresh_shortcuts)
        when_closed(self, unwatch)

    # -- Appearance ---------------------------------------------------------------------
    def _appearance_page(self) -> Adw.PreferencesPage:
        s = self.settings
        page = Adw.PreferencesPage(
            name="appearance", title=_("Appearance"), icon_name="applications-graphics-symbolic"
        )

        colors = Adw.PreferencesGroup(
            title=_("Color Scheme"),
            description=_("Tints buttons, selections, highlights and the capture screen."),
        )
        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=4,  # eight schemes: two even rows
            min_children_per_line=4,
            column_spacing=6,
            row_spacing=12,
            homogeneous=True,
        )
        flow.add_css_class("card")
        flow.add_css_class("gh-scheme-card")
        self.scheme_buttons: dict[str, Gtk.ToggleButton] = {}
        group = None
        current = s.get_string("accent-scheme")
        for scheme in theme.schemes():
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            button = Gtk.ToggleButton(halign=Gtk.Align.CENTER, tooltip_text=scheme.name)
            button.add_css_class("gh-scheme")
            button.add_css_class(f"gh-scheme-{scheme.id}")
            if group is not None:
                button.set_group(group)
            group = group or button
            button.set_active(scheme.id == current)
            button.connect(
                "toggled",
                lambda b, sid=scheme.id: b.get_active() and s.set_string("accent-scheme", sid),
            )
            name = Gtk.Label(label=scheme.name)
            name.add_css_class("gh-scheme-name")
            cell.append(button)
            cell.append(name)
            flow.append(cell)
            self.scheme_buttons[scheme.id] = button
        colors.add(flow)
        page.add(colors)

        look = Adw.PreferencesGroup(title=_("Style"))
        row = Adw.ActionRow(title=_("Light or dark"))
        toggles = Gtk.Box(valign=Gtk.Align.CENTER)
        toggles.add_css_class("linked")
        toggles.add_css_class("gh-segmented")
        style_group = None
        self.style_buttons: dict[str, Gtk.ToggleButton] = {}
        for value, label in (
            ("system", _("System")),
            ("light", _("Light")),
            ("dark", _("Dark")),
        ):
            button = Gtk.ToggleButton(label=label)
            if style_group is not None:
                button.set_group(style_group)
            style_group = style_group or button
            button.set_active(s.get_string("app-style") == value)
            button.connect(
                "toggled", lambda b, v=value: b.get_active() and s.set_string("app-style", v)
            )
            toggles.append(button)
            self.style_buttons[value] = button
        row.add_suffix(toggles)
        look.add(row)
        page.add(look)
        return page

    # -- General ------------------------------------------------------------------------
    def _general_page(self) -> Adw.PreferencesPage:
        s = self.settings
        page = Adw.PreferencesPage(
            name="general", title=_("General"), icon_name="preferences-system-symbolic"
        )

        capture = Adw.PreferencesGroup(title=_("Capture"))
        capture.add(
            _combo(
                _("When I press Enter"),
                DEFAULT_ACTIONS,
                s.get_string("default-action"),
                lambda v: s.set_string("default-action", v),
                subtitle=_("Also what a double-click on the selected area does"),
            )
        )
        loupe = Adw.SwitchRow(
            title=_("Magnifier"), subtitle=_("Show a zoomed view while selecting")
        )
        s.bind("show-loupe", loupe, "active", Gio.SettingsBindFlags.DEFAULT)
        capture.add(loupe)
        delay = Adw.SpinRow.new_with_range(1, 60, 1)
        delay.set_title(_("Delay"))
        delay.set_subtitle(_("Seconds to wait when you press D on the capture screen"))
        delay.set_value(s.get_int("delay-seconds"))
        delay.connect("notify::value", lambda r, _p: s.set_int("delay-seconds", int(r.get_value())))
        capture.add(delay)
        capture.add(
            _combo(
                _("Quick-copy color format"),
                COLOR_FORMATS,
                s.get_string("color-format"),
                lambda v: s.set_string("color-format", v),
                subtitle=_("What the C key copies while you identify a color"),
            )
        )
        page.add(capture)

        startup = Adw.PreferencesGroup(title=_("Startup and Shortcuts"))
        autostart = Adw.SwitchRow(
            title=_("Start at login"),
            subtitle=_("Keeps Print Screen ready. GrabbyHands runs quietly in the background."),
        )
        autostart.set_active(s.get_boolean("autostart"))
        autostart.connect("notify::active", lambda r, _p: self.app.set_autostart(r.get_active()))
        startup.add(autostart)

        self.shortcuts_row = Adw.ExpanderRow(title=_("Shortcuts that work anywhere"))
        self.shortcut_labels: dict[str, Gtk.Label] = {}
        for shortcut_id, name, _keys, _bound in self.app.shortcut_list():
            row = Adw.ActionRow(title=GLib.markup_escape_text(name))
            keys = Gtk.Label()
            keys.add_css_class("dim-label")
            row.add_suffix(keys)
            self.shortcuts_row.add_row(row)
            self.shortcut_labels[shortcut_id] = keys
        self._refresh_shortcuts()
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8, margin_bottom=8)
        buttons.set_margin_end(12)
        if not config.in_flatpak() and shutil.which("gnome-control-center"):
            change = Gtk.Button(label=_("Change in GNOME Settings…"))
            change.connect("clicked", lambda *_: self._open_shortcut_settings())
            buttons.append(change)
        again = Gtk.Button(label=_("Set Up Again"))
        again.set_tooltip_text(_("Ask GNOME for the shortcuts again (e.g. after declining)"))
        again.connect("clicked", lambda *_: self._setup_shortcuts_again())
        buttons.append(again)
        self.shortcuts_row.add_row(buttons)
        startup.add(self.shortcuts_row)
        self.menu_button_row = Adw.SwitchRow(title=_("Use the system menu's screenshot button"))
        self._menu_button_shown: bool | None = None
        self._refresh_menu_button()
        self.menu_button_row.connect("notify::active", lambda *_: self._menu_button_toggled())
        startup.add(self.menu_button_row)
        self.all_keys_row = _link_row(
            _("All Keyboard Shortcuts"),
            _("Every key for capturing, the editor and History"),
            lambda: self.app.show_shortcuts(self),
        )
        startup.add(self.all_keys_row)
        self.guide_row = _link_row(
            _("Getting Started"),
            _("What GrabbyHands does, and how to give it the Print Screen key"),
            lambda: self.app.show_welcome(self),
        )
        startup.add(self.guide_row)
        page.add(startup)

        window = Adw.PreferencesGroup(
            title=_("Window Capture"),
            description=_(
                "Wayland doesn't tell apps where windows are, so window capture uses the small "
                "“GrabbyHands Window Helper” GNOME Shell extension. It shares only window "
                "positions and app names."
            ),
        )
        active = windows.available()
        ext_app = None if active else extensions_app()
        if active:
            subtitle = _("On: press W while capturing to pick a window")
        elif ext_app is not None:
            subtitle = _(
                "Off: turn on “GrabbyHands Window Helper” in {app}. If it isn't listed, log "
                "out and back in once."
            ).format(app=GLib.markup_escape_text(ext_app.get_display_name()))
        else:
            subtitle = _(
                "Off: turn it on with the command below. If that says the extension doesn't "
                "exist, log out and back in once, then try again."
            )
        status = Adw.ActionRow(title=_("Window Helper"), subtitle=subtitle)
        icon = Gtk.Image.new_from_icon_name(
            "gh-check-symbolic" if active else "dialog-warning-symbolic"
        )
        status.add_prefix(icon)
        window.add(status)
        self.extensions_button: Gtk.Button | None = None
        self.helper_command_row: Adw.ActionRow | None = None
        if ext_app is not None:
            label = _("Open {app}").format(app=ext_app.get_display_name())
            self.extensions_button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            self.extensions_button.connect("clicked", lambda *_: self._open_extensions(ext_app))
            status.add_suffix(self.extensions_button)
        elif not active:
            command = f"gnome-extensions enable {windows.EXTENSION_UUID}"
            row = Adw.ActionRow(title=_("In a terminal"), subtitle=command)
            row.add_css_class("property")
            row.set_subtitle_selectable(True)
            copy = Gtk.Button(
                icon_name="gh-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Copy")
            )
            copy.add_css_class("flat")
            copy.connect("clicked", lambda *_: self._copy(command))
            row.add_suffix(copy)
            window.add(row)
            self.helper_command_row = row
        page.add(window)
        return page

    def _refresh_menu_button(self) -> None:
        """Show what the Window Helper says about the system menu's screenshot button."""
        state = windows.menu_button()
        row = self.menu_button_row
        possible = state in ("on", "off")
        self._menu_button_shown = state == "on"
        row.set_active(self._menu_button_shown)
        row.set_sensitive(possible)
        if possible:
            subtitle = _(
                "The camera button in the menu at the top right of the screen starts "
                "GrabbyHands instead of GNOME's own screenshot tool"
            )
        else:
            subtitle = MENU_BUTTON_NOTES[state]
        row.set_subtitle(GLib.markup_escape_text(subtitle))

    def _menu_button_toggled(self) -> None:
        wanted = self.menu_button_row.get_active()
        if wanted == self._menu_button_shown:
            return  # the switch was only brought in line with the helper
        done = windows.set_menu_button(wanted)
        self._refresh_menu_button()
        if not done:
            title = _("Couldn't change the system menu")
            self.add_toast(Adw.Toast(title=title, timeout=4, use_markup=False))

    def _open_extensions(self, info: Gio.DesktopAppInfo) -> None:
        if not _launch(info):
            title = _("Couldn't open {app}").format(app=info.get_display_name())
            self.add_toast(Adw.Toast(title=title, timeout=4, use_markup=False))

    def _copy(self, text: str) -> None:
        self.app.copy_text(text, self)
        self.add_toast(Adw.Toast(title=_("Copied"), timeout=2))

    def _refresh_shortcuts(self) -> None:
        """Show what the desktop has bound right now (also after Set Up Again)."""
        for shortcut_id, _name, keys, bound in self.app.shortcut_list():
            label = self.shortcut_labels.get(shortcut_id)
            if label is not None:
                label.set_label(keys if bound and keys else _("Not set"))
        self.shortcuts_row.set_subtitle(GLib.markup_escape_text(self._shortcut_summary()))

    def _shortcut_summary(self) -> str:
        state = self.app.shortcut_state()
        if state == "ready":
            keys = next(k for sid, _n, k, _b in self.app.shortcut_list() if sid == "capture")
            if keys:
                return _("Screenshot: {keys}").format(keys=keys)
            return _("Set up, but no key takes a screenshot yet")
        if state == "unavailable":
            return _("This desktop doesn't offer them. Getting Started shows another way.")
        if state == "declined":
            return _("Not set up: your desktop's shortcut dialog was dismissed")
        return _("Not set up yet")

    def _open_shortcut_settings(self) -> None:
        try:
            Gio.Subprocess.new(
                ["gnome-control-center", "applications", APP_ID], Gio.SubprocessFlags.NONE
            )
        except GLib.Error as err:
            log.info("could not open GNOME Settings: %s", err.message)

    def _setup_shortcuts_again(self) -> None:
        self.app.setup_shortcuts_again()
        self.add_toast(Adw.Toast(title=_("Your desktop may ask you to confirm the shortcuts")))

    # -- Saving -------------------------------------------------------------------------
    def _saving_page(self) -> Adw.PreferencesPage:
        s = self.settings
        page = Adw.PreferencesPage(name="saving", title=_("Saving"), icon_name="gh-save-symbolic")

        files = Adw.PreferencesGroup(title=_("Files"))
        auto = Adw.SwitchRow(
            title=_("Save every capture"),
            subtitle=_("Also keeps a file when you only copy or pin"),
        )
        s.bind("auto-save", auto, "active", Gio.SettingsBindFlags.DEFAULT)
        files.add(auto)

        self.folder_row = Adw.ActionRow(title=_("Folder"))
        self.folder_row.set_subtitle_selectable(True)
        self.folder_warning = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        self.folder_warning.add_css_class("warning")
        self.folder_warning.set_tooltip_text(
            _("GrabbyHands can't save into this folder. Choose another one.")
        )
        # a suffix, so the row keeps lining up with the others while the icon is hidden
        self.folder_row.add_suffix(self.folder_warning)
        choose = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER)
        choose.set_tooltip_text(_("Choose a folder"))
        choose.add_css_class("flat")
        choose.connect("clicked", lambda *_: self._choose_folder())
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER)
        reset.set_tooltip_text(_("Back to the default (Pictures/GrabbyHands)"))
        reset.add_css_class("flat")
        reset.connect("clicked", lambda *_: self._set_folder(""))
        self.folder_row.add_suffix(choose)
        self.folder_row.add_suffix(reset)
        files.add(self.folder_row)
        self._show_folder()

        self.pattern_row = Adw.EntryRow(title=_("File name"), show_apply_button=True)
        self.pattern_row.set_tooltip_text(
            _(
                "Codes are replaced with the date and time of the capture:\n"
                "%Y year · %m month · %d day · %H hour · %M minute · %S second"
            )
        )
        self.pattern_row.set_text(s.get_string("filename-pattern"))
        self.pattern_row.connect("apply", self._apply_pattern)
        self.pattern_row.connect("changed", lambda *_: self._update_example())
        files.add(self.pattern_row)
        self.example_row = Adw.ActionRow(title=_("Example"))
        self.example_row.add_css_class("property")
        files.add(self.example_row)

        files.add(
            _combo(
                _("Format"),
                FILE_FORMATS,
                s.get_string("export-format"),
                self._set_format,
            )
        )
        self.quality_row = Adw.SpinRow.new_with_range(10, 100, 5)
        self.quality_row.set_title(_("JPEG/WebP quality"))
        self.quality_row.set_value(s.get_int("export-quality"))
        self.quality_row.connect(
            "notify::value", lambda r, _p: s.set_int("export-quality", int(r.get_value()))
        )
        files.add(self.quality_row)
        page.add(files)
        self._update_example()
        self._sync_quality()

        history = Adw.PreferencesGroup(title=_("History"))
        limit = Adw.SpinRow.new_with_range(0, 5000, 50)
        limit.set_title(_("Keep this many captures"))
        limit.set_subtitle(_("The oldest are removed first. 0 keeps everything."))
        limit.set_value(s.get_int("history-limit"))
        limit.connect("notify::value", lambda r, _p: s.set_int("history-limit", int(r.get_value())))
        history.add(limit)
        open_history = Adw.ActionRow(title=_("Open History"), activatable=True)
        open_history.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        open_history.connect("activated", lambda *_: (self.close(), self.app.show_history()))
        history.add(open_history)
        page.add(history)
        return page

    def _set_format(self, value: str) -> None:
        self.settings.set_string("export-format", value)
        self._update_example()
        self._sync_quality()

    def _sync_quality(self) -> None:
        self.quality_row.set_sensitive(self.settings.get_string("export-format") != "png")

    def _update_example(self) -> None:
        pattern = self.pattern_row.get_text().strip() or "%Y-%m-%d_%H-%M-%S"
        suffix = export.FORMATS.get(self.settings.get_string("export-format"), (0, ".png"))[1]
        try:
            name = export.make_filename(pattern) + suffix
        except ValueError:
            name = _("Invalid pattern")
        self.example_row.set_subtitle(GLib.markup_escape_text(name))  # row text is markup

    def _apply_pattern(self, row: Adw.EntryRow) -> None:
        pattern = row.get_text().strip()
        if not pattern:
            self.settings.reset("filename-pattern")
            row.set_text(self.settings.get_string("filename-pattern"))
        else:
            self.settings.set_string("filename-pattern", pattern)
        self.add_toast(Adw.Toast(title=_("File name pattern saved"), timeout=2))

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title=_("Save Captures In"))
        dialog.set_initial_folder(Gio.File.new_for_path(str(config.save_folder())))
        parent = self.get_root() if isinstance(self.get_root(), Gtk.Window) else None

        def done(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is not None and folder.get_path():
                self._set_folder(folder.get_path())

        dialog.select_folder(parent, None, done)

    def _set_folder(self, path: str) -> None:
        self.settings.set_string("save-folder", path)
        self._show_folder()

    def _show_folder(self) -> None:
        """The save folder, with a warning when captures can't be written there."""
        folder = config.save_folder()
        writable = config.folder_writable(folder)
        # (in a Flatpak a chosen folder has a stand-in path: show where it really is)
        text = GLib.markup_escape_text(export.host_path(folder))
        if not writable:
            text = _("{folder}\nCan't save here. Choose another folder.").format(folder=text)
        self.folder_row.set_subtitle(text)
        self.folder_warning.set_visible(not writable)

    # -- Text recognition ---------------------------------------------------------------
    def _text_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(name="text", title=_("Text"), icon_name="gh-ocr-symbolic")
        group = Adw.PreferencesGroup(
            title=_("Text Recognition"),
            description=_(
                "Used by Copy Text and Auto-Redact. It runs on your computer with Tesseract."
            ),
        )
        langs = ocr.languages()
        self.more_languages_row: Adw.ActionRow | None = None
        if not langs:
            group.add(
                Adw.ActionRow(
                    title=_("Tesseract isn't installed"),
                    subtitle=_(
                        "Copy Text and Auto-Redact need it. Install Tesseract with your "
                        "package manager (tesseract-ocr on Ubuntu and Debian)."
                    ),
                )
            )
        else:
            choices = [(code, LANGUAGE_NAMES.get(code, code)) for code in langs]
            group.add(
                _combo(
                    _("Language"),
                    choices,
                    self.settings.get_string("ocr-language"),
                    lambda v: self.settings.set_string("ocr-language", v),
                )
            )
            if config.in_flatpak():
                # Nothing can be installed into the sandbox: the usual advice (a system
                # package) would be wrong here.
                advice = _(
                    "The Flatpak version of GrabbyHands comes with the languages listed "
                    "above. More can't be added to it."
                )
            else:
                advice = _(
                    "Install tesseract-ocr-<code> with your package manager, for example "
                    "tesseract-ocr-deu for German, then reopen Settings."
                )
            more = Adw.ActionRow(
                title=_("More languages"), subtitle=GLib.markup_escape_text(advice)
            )
            more.set_subtitle_selectable(True)
            self.more_languages_row = more
            group.add(more)
        page.add(group)

        codes = Adw.PreferencesGroup(title=_("QR Codes and Barcodes"))
        ready = qr.available()
        self.qr_row = Adw.ActionRow(
            title=_("Code reader"),
            subtitle=_("Ready: select an area, then choose QR Code")
            if ready
            else _(
                "Not installed, so the QR Code button stays hidden. Install zxing-cpp for "
                "Python with your package manager (python3-zxing-cpp on Ubuntu and Debian)."
            ),
        )
        if not ready:
            self.qr_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        codes.add(self.qr_row)
        page.add(codes)
        return page

    # -- Smart Edit -----------------------------------------------------------------------
    def _smart_edit_page(self) -> Adw.PreferencesPage:
        from grabbyhands.editor.smart_panel import KEYS_URL, LIMITS_URL
        from grabbyhands.smartedit import available, pricing
        from grabbyhands.smartedit.budget import LEDGER_NAME, Budget
        from grabbyhands.smartedit.session import MODELS

        s = self.settings
        page = Adw.PreferencesPage(
            name="smart-edit", title=_("Smart Edit"), icon_name="gh-smart-edit-symbolic"
        )
        intro = Adw.PreferencesGroup(
            description=_(
                "Describe an edit in plain words, like “blur the email addresses” or “put an "
                "arrow at the Save button”, and Claude makes it with the editor's own tools. "
                "Smart Edit uses your own Anthropic API key and is charged to your Anthropic "
                "account. GrabbyHands goes online only for Smart Edit, and only when you press "
                "Send."
            )
        )
        ready = available.smart_edit_available()
        enabled = Adw.SwitchRow(
            title=_("Use Smart Edit"), subtitle=_("Adds a Smart Edit button to the editor")
        )
        s.bind("smart-edit-enabled", enabled, "active", Gio.SettingsBindFlags.DEFAULT)
        intro.add(enabled)
        if not ready:
            enabled.set_sensitive(False)
            problem = available.sdk_problem()
            if problem in ("", "missing"):
                why = _(
                    "Smart Edit needs Anthropic's Python library (package python3-anthropic) "
                    "and the keyring library (gir1.2-secret-1). The Flatpak has both."
                )
            elif problem == "too-old":
                why = _(
                    "Anthropic's Python library on this computer is too old for Smart Edit "
                    "({version}). The Flatpak has a current one."
                ).format(version=available.sdk_version())
            else:
                why = _(
                    "Anthropic's Python library is installed but doesn't work: it can't load "
                    "“{module}”. The Flatpak has a working one."
                ).format(module=problem)
            missing = Adw.ActionRow(title=_("Not available on this computer"), subtitle=why)
            missing.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
            intro.add(missing)
        page.add(intro)

        key = Adw.PreferencesGroup(
            title=_("API Key"),
            description=_("Kept in your keyring, never in a file or a log."),
        )
        self.key_status = Adw.ActionRow(title=_("Saved key"), subtitle=_("Checking…"))
        self.key_badge: Gtk.Image | None = None
        self.key_test = Gtk.Button(label=_("Test"), valign=Gtk.Align.CENTER)
        self.key_test.set_tooltip_text(_("Check the key with Anthropic (free)"))
        self.key_test.connect("clicked", lambda *_: self._test_key())
        self.key_remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        self.key_remove.set_tooltip_text(_("Remove the key from this computer"))
        self.key_remove.add_css_class("flat")
        self.key_remove.connect("clicked", lambda *_: self._remove_key())
        self.key_status.add_suffix(self.key_test)
        self.key_status.add_suffix(self.key_remove)
        key.add(self.key_status)
        self.key_entry = Adw.PasswordEntryRow(title=_("Paste a key"), show_apply_button=True)
        self.key_entry.connect("apply", lambda *_: self._save_key())
        key.add(self.key_entry)
        key.add(
            _link_row(
                _("Get an API key"),
                _("Create one in the Anthropic Console"),
                lambda: self.app._open_url(KEYS_URL),
            )
        )
        key.set_sensitive(ready)
        page.add(key)
        if ready:
            self._refresh_key()

        claude = Adw.PreferencesGroup(title=_("Claude"))
        choices = []
        for model_id, label in MODELS:
            price = pricing.PRICES[model_id]
            choices.append((model_id, f"{label} · ${price.input:g} / ${price.output:g}"))
        claude.add(
            _combo(
                _("Model"),
                choices,
                s.get_string("smart-edit-model"),
                lambda v: s.set_string("smart-edit-model", v),
                subtitle=_("Price per million tokens, input / output"),
            )
        )
        claude.add(
            _combo(
                _("Effort"),
                [
                    ("low", _("Low: faster and cheaper")),
                    ("medium", _("Medium")),
                    ("high", _("High: more careful, costs more")),
                ],
                s.get_string("smart-edit-effort"),
                lambda v: s.set_string("smart-edit-effort", v),
            )
        )
        page.add(claude)

        spending = Adw.PreferencesGroup(
            title=_("Spending"),
            description=_(
                "Costs are estimates from Anthropic's prices of {date}. A message is only sent "
                "when its highest possible cost fits under both limits. 0 means no limit. For "
                "a hard cap, also set a limit in the Anthropic Console."
            ).format(date=pricing.PRICES_AS_OF),
        )
        for key_name, title, top, step in (
            ("smart-edit-chat-limit", _("Limit per chat (US$)"), 1000, 0.25),
            ("smart-edit-month-limit", _("Limit per month (US$)"), 10000, 1),
        ):
            row = Adw.SpinRow.new_with_range(0, top, step)
            row.set_title(title)
            row.set_digits(2)
            row.set_value(s.get_double(key_name))
            row.connect("notify::value", lambda r, _p, k=key_name: s.set_double(k, r.get_value()))
            spending.add(row)
        month = Budget(config.data_dir() / LEDGER_NAME, 0, 0).month_spent()
        spent = Adw.ActionRow(
            title=_("Spent this month"), subtitle="≈ " + pricing.format_usd(month)
        )
        spent.add_css_class("property")
        spending.add(spent)
        spending.add(
            _link_row(
                _("Set a limit in the Anthropic Console"),
                _("The limit Anthropic enforces, whatever an app does"),
                lambda: self.app._open_url(LIMITS_URL),
            )
        )
        page.add(spending)

        privacy = Adw.PreferencesGroup(title=_("Privacy"))
        hide = Adw.SwitchRow(
            title=_("Hide personal details before sending"),
            subtitle=_(
                "Blacks out email addresses, phone numbers, card numbers, keys and similar that "
                "text recognition finds, in the copy that is sent. Needs Tesseract."
            ),
        )
        s.bind("smart-edit-hide-personal", hide, "active", Gio.SettingsBindFlags.DEFAULT)
        privacy.add(hide)
        sent = Adw.ActionRow(
            title=_("What is sent"),
            subtitle=_(
                "The picture, your messages and the list of annotations, to Anthropic. Nothing "
                "else from your computer."
            ),
        )
        privacy.add(sent)
        page.add(privacy)
        return page

    def _refresh_key(self) -> None:
        def work() -> None:
            from grabbyhands.smartedit import secret

            key = secret.lookup()
            GLib.idle_add(done, key)

        def done(key: str | None) -> bool:
            if key:
                # Only the last four characters are ever shown.
                self.key_status.set_subtitle(_("Ends in …{tail}").format(tail=key[-4:]))
            else:
                self.key_status.set_subtitle(_("None yet"))
            self.key_test.set_sensitive(bool(key))
            self.key_remove.set_sensitive(bool(key))
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _save_key(self) -> None:
        from grabbyhands.smartedit import secret

        text = self.key_entry.get_text().strip()
        if not secret.looks_like_key(text):
            self.add_toast(
                Adw.Toast(title=_("That doesn't look like an Anthropic API key (sk-ant-…)"))
            )
            return
        self.key_entry.set_text("")

        def work() -> None:
            try:
                secret.store(text)
                ok = True
            except Exception as err:  # noqa: BLE001
                log.warning("could not store the API key: %s", type(err).__name__)
                ok = False
            GLib.idle_add(done, ok)

        def done(ok: bool) -> bool:
            if not ok:
                self._key_result(False, _("Couldn't save the key in your keyring."))
                return GLib.SOURCE_REMOVE
            self._refresh_key()
            self._test_key()  # a new key is checked straight away
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _remove_key(self) -> None:
        def work() -> None:
            from grabbyhands.smartedit import secret

            try:
                secret.clear()
            except Exception as err:  # noqa: BLE001
                log.warning("could not remove the API key: %s", type(err).__name__)
            GLib.idle_add(done)

        def done() -> bool:
            self.add_toast(Adw.Toast(title=_("Key removed")))
            self._refresh_key()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _test_key(self) -> None:
        self.key_test.set_sensitive(False)
        self.key_test.set_label(_("Testing…"))

        def work() -> None:
            from grabbyhands.smartedit import secret
            from grabbyhands.smartedit.session import SmartEditError, _translate, make_client

            ok, message = True, ""
            try:
                key = secret.lookup()
                if not key:
                    raise SmartEditError("nokey")
                make_client(key).models.list(limit=1)
            except SmartEditError:
                ok, message = False, _("No key is saved yet. Paste one first.")
            except Exception as err:  # noqa: BLE001
                from grabbyhands.editor.smart_panel import error_text

                ok, message = False, error_text(_translate(err).kind)
            GLib.idle_add(done, ok, message)

        def done(ok: bool, message: str) -> bool:
            self.key_test.set_sensitive(True)
            self.key_test.set_label(_("Test"))
            self._key_result(ok, message)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _key_result(self, ok: bool, message: str) -> None:
        """The outcome of a key check: marked on the key row, and in a dialog that can't be
        missed."""
        if self.key_badge is not None:
            self.key_status.remove(self.key_badge)
        self.key_badge = Gtk.Image.new_from_icon_name(
            "emblem-ok-symbolic" if ok else "dialog-error-symbolic"
        )
        self.key_badge.add_css_class("gh-key-ok" if ok else "gh-key-bad")
        self.key_badge.set_tooltip_text(_("The key works") if ok else message)
        self.key_status.add_prefix(self.key_badge)
        if ok:
            dialog = Adw.AlertDialog(
                heading=_("Your Key Works"),
                body=_(
                    "Smart Edit is ready. Open a capture in the editor and click Smart Edit at "
                    "the top left."
                ),
            )
            dialog.add_response("ok", _("Great"))
            dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        else:
            dialog = Adw.AlertDialog(heading=_("The Key Didn't Work"), body=message)
            dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present(self)


def show(app: GrabbyHandsApp, parent: Gtk.Widget | None = None) -> PreferencesDialog:
    dialog = PreferencesDialog(app)
    dialog.present(parent)
    return dialog
