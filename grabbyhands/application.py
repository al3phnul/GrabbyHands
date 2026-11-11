# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The resident GrabbyHands application."""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from grabbyhands import APP_ID, APP_NAME, config, export, style
from grabbyhands.capture import CaptureController
from grabbyhands.dialogs import when_closed
from grabbyhands.history import Entry, HistoryStore
from grabbyhands.i18n import ngettext
from grabbyhands.imaging import png_bytes
from grabbyhands.portals import GlobalShortcuts, PortalClient

if TYPE_CHECKING:
    from PIL import Image

log = logging.getLogger(__name__)


def _shortcut_specs() -> list[tuple[str, str, str]]:
    return [
        ("capture", _("Take a screenshot"), "Print"),
        ("repeat-region", _("Capture the last area again"), "CTRL+SHIFT+Print"),
        ("color-picker", _("Identify a color on the screen"), "LOGO+SHIFT+c"),
        ("capture-text", _("Copy text from the screen"), "LOGO+SHIFT+t"),
    ]


# The keys we suggest, the way a person reads them (the portal wants the form above).
SHORTCUT_DEFAULT_KEYS = {
    "capture": "Print Screen",
    "repeat-region": "Ctrl+Shift+Print Screen",
    "color-picker": "Super+Shift+C",
    "capture-text": "Super+Shift+T",
}
# On the very first run, how long to wait for a window (Getting Started) before asking the
# desktop for the shortcuts anyway. See _startup_shortcuts.
FIRST_RUN_WAIT_MS = 1500


class GrabbyHandsApp(Adw.Application):
    def __init__(self, version: str) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.version = version
        self.set_option_context_summary(_("Grab, mark up and copy screenshots."))
        flag = GLib.OptionFlags.NONE
        none, integer = GLib.OptionArg.NONE, GLib.OptionArg.INT
        self.add_main_option("capture", ord("c"), flag, none, _("Select an area to capture"), None)
        self.add_main_option("window", ord("w"), flag, none, _("Capture a window"), None)
        self.add_main_option("repeat", ord("r"), flag, none, _("Capture the last area again"), None)
        self.add_main_option("pick-color", 0, flag, none, _("Identify a color on the screen"), None)
        self.add_main_option("ocr", 0, flag, none, _("Copy text from part of the screen"), None)
        self.add_main_option("delay", ord("d"), flag, integer, _("Wait N seconds first"), "N")
        self.add_main_option("history", 0, flag, none, _("Show capture history"), None)
        self.add_main_option("preferences", 0, flag, none, _("Show settings"), None)
        self.add_main_option("background", 0, flag, none, _("Start in the background"), None)
        self.add_main_option("quit", ord("q"), flag, none, _("Quit the running instance"), None)
        self.add_main_option("version", 0, flag, none, _("Print the version"), None)

        self.portal: PortalClient | None = None
        self.shortcuts: GlobalShortcuts | None = None
        self.history: HistoryStore | None = None
        self.capture: CaptureController | None = None
        self.settings: Gio.Settings | None = None
        self._last_text = ""
        self._history_window = None
        self._welcome = None
        self._shortcuts_dialog = None
        self._preferences = None
        self._shortcuts_available = False
        self._shortcuts_asked = False  # a shortcuts session was started (see _setup_shortcuts)
        self._first_run = False
        self._signals_handled = False
        self._shortcut_listeners: list[Callable[[], None]] = []
        self._pins: list[Gtk.Window] = []

    # -- lifecycle ----------------------------------------------------------------
    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"{APP_NAME} {self.version}")
            return 0
        return -1

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.settings = config.settings()
        style.apply(self.settings.get_string("accent-scheme"))
        self._apply_app_style()
        self.settings.connect("changed::accent-scheme", lambda *_: self.apply_scheme())
        self.settings.connect("changed::app-style", lambda *_: self._apply_app_style())
        manager = Adw.StyleManager.get_default()
        manager.connect("notify::dark", lambda *_: self.apply_scheme())
        if hasattr(manager.props, "accent_color"):
            manager.connect("notify::accent-color", lambda *_: self._system_accent_changed())
        Gtk.Window.set_default_icon_name(APP_ID)
        self._first_run = not self.settings.get_boolean("welcome-shown")
        self.portal = PortalClient()
        self.portal.on_restart(self._portal_restarted)
        self.history = HistoryStore(config.data_dir())
        self.capture = CaptureController(self)
        self._install_actions()
        self.hold()  # stay resident: instant Print Screen, clipboard stays alive
        self._quit_on_signals()
        GLib.idle_add(self._startup_shortcuts)
        GLib.idle_add(self._sync_autostart)

    def do_shutdown(self) -> None:
        # Quitting doesn't close windows, so nothing they save on closing would be saved:
        # an open editor's picture, a History delete still waiting for its Undo.
        try:
            self.finish_windows()
        finally:
            Adw.Application.do_shutdown(self)

    def finish_windows(self) -> None:
        """Let every open window save what it would save when closed."""
        if self.capture is not None:
            self.capture.cancel()
            self.capture.cancel_delay()
        for win in self.get_windows():
            finish = getattr(win, "finish", None)
            if callable(finish):
                try:
                    finish()
                except Exception as err:  # noqa: BLE001 - the other windows still get their turn
                    log.error("a window could not save on quit: %s", type(err).__name__)

    def _quit_on_signals(self) -> None:
        """Logging out or `kill` ends the app with SIGTERM: quit properly (see do_shutdown)
        instead of dying with unsaved work."""

        def stop() -> bool:
            self.quit()
            return GLib.SOURCE_REMOVE

        try:
            try:
                import gi

                gi.require_version("GLibUnix", "2.0")
                from gi.repository import GLibUnix

                add = GLibUnix.signal_add
            except (ImportError, ValueError, AttributeError):
                add = GLib.unix_signal_add
            for number in (signal.SIGTERM, signal.SIGHUP):
                add(GLib.PRIORITY_DEFAULT, number, stop)
            self._signals_handled = True
        except Exception as err:  # noqa: BLE001 - a convenience, never a reason not to start
            log.info("no signal handling: %s", type(err).__name__)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        opts = command_line.get_options_dict().end().unpack()
        if opts.get("quit"):
            self.quit()
            return 0
        delay = max(0, min(int(opts.get("delay", 0) or 0), 3600))
        if opts.get("capture"):
            self.capture.capture(delay=delay)
        elif opts.get("window"):
            self.capture.capture(delay=delay, mode="window")
        elif opts.get("repeat"):
            self.capture.repeat_last()
        elif opts.get("pick-color"):
            self.capture.pick_color(delay)
        elif opts.get("ocr"):
            self.capture.capture_text(delay)
        elif opts.get("history"):
            self.show_history()
        elif opts.get("preferences"):
            self.show_preferences()
        elif opts.get("background"):
            pass
        elif delay:
            self.capture.capture(delay=delay)
        else:
            self.activate()
        return 0

    def do_activate(self) -> None:
        if self.supports("history"):
            self.show_history()
            if not self.settings.get_boolean("welcome-shown"):
                self.show_welcome(self._history_window)  # first run: explain the basics once
        else:
            self.capture.capture()

    # -- appearance ---------------------------------------------------------------
    def apply_scheme(self) -> None:
        style.apply(self.settings.get_string("accent-scheme"))
        style.redraw_all(self)

    def _system_accent_changed(self) -> None:
        if self.settings.get_string("accent-scheme") == "system":
            self.apply_scheme()

    def _apply_app_style(self) -> None:
        schemes = {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }
        value = schemes.get(self.settings.get_string("app-style"), Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(value)

    # -- features -----------------------------------------------------------------
    def _module(self, feature: str):
        try:
            return importlib.import_module(f"grabbyhands.{feature}")
        except ImportError as err:
            log.debug("feature module %s unavailable: %s", feature, err)
            return None

    def supports(self, action: str) -> bool:
        if action in ("copy", "save", "save-as"):
            return True
        if action == "edit":
            return self._module("editor.window") is not None
        if action == "redact":
            return self.supports("edit") and self.supports("ocr")
        if action == "pin":
            return self._module("pin") is not None
        if action == "history":
            return self._module("history_window") is not None
        if action == "ocr":
            mod = self._module("ocr")
            return bool(mod and mod.available())
        if action == "qr":
            mod = self._module("qr")
            return bool(mod and mod.available())
        return False

    # -- actions ------------------------------------------------------------------
    def _install_actions(self) -> None:
        simple = {
            "capture": lambda *_: self.capture.capture(),
            "capture-window": lambda *_: self.capture.window_capture(),
            "repeat": lambda *_: self.capture.repeat_last(),
            "pick-color": lambda *_: self.capture.pick_color(),
            "capture-text": lambda *_: self.capture.capture_text(),
            "cancel-delay": lambda *_: self.capture.cancel_delay(),
            "history": lambda *_: self.show_history(),
            "preferences": lambda *_: self.show_preferences(),
            "show-last-text": lambda *_: self.show_text(self._last_text),
            "setup-shortcuts": lambda *_: self.setup_shortcuts_again(),
            "welcome": lambda *_: self.show_welcome(),
            "keyboard-shortcuts": lambda *_: self.show_shortcuts(),
            "about": lambda *_: self.show_about(),
            "quit": lambda *_: self.quit(),
        }
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        with_id = {
            "edit-capture": self._edit_capture_by_id,
            "show-capture": self._show_capture_by_id,
            "pin-capture": self._pin_capture_by_id,
            "open-url": self._open_url,
            "settings-page": lambda page: self.show_preferences(page=page),
        }
        for name, callback in with_id.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, param, cb=callback: cb(param.get_string()))
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.keyboard-shortcuts", ["<Control>question", "F1"])

    def _edit_capture_by_id(self, entry_id: str) -> None:
        entry = self.history.get(entry_id)
        if entry is not None:
            self.open_editor(entry)

    def saved_file(self, entry: Entry | None) -> Path | None:
        """The capture's own file in the user's folder, if it has one that still exists."""
        if entry is None or not entry.saved_path:
            return None
        path = Path(entry.saved_path)
        return path if path.is_file() else None

    def _show_capture_by_id(self, entry_id: str) -> bool:
        """Show the capture's saved file in the file manager. False if it has none: we never
        open GrabbyHands' private data folder instead (nothing in there is for people)."""
        target = self.saved_file(self.history.get(entry_id))
        if target is None:
            return False
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(target))).open_containing_folder(
            None, None, None
        )
        return True

    def _pin_capture_by_id(self, entry_id: str) -> None:
        entry = self.history.get(entry_id)
        if entry is None:
            return
        image = self.entry_image(entry)
        if image is not None:
            self.pin(image, entry)

    def _open_url(self, url: str) -> None:
        if url.lower().startswith(("http://", "https://")):
            Gtk.UriLauncher.new(url).launch(None, None, None)

    # -- clipboard ----------------------------------------------------------------
    @staticmethod
    def _clipboard(widget: Gtk.Widget | None) -> Gdk.Clipboard:
        return widget.get_clipboard() if widget else Gdk.Display.get_default().get_clipboard()

    def copy_image(self, image: Image.Image, widget: Gtk.Widget | None = None) -> None:
        # A texture provider offers PNG, JPEG, TIFF… so every app finds a format it takes.
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png_bytes(image)))
        provider = Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, texture))
        self._clipboard(widget).set_content(provider)

    def copy_text(self, text: str, widget: Gtk.Widget | None = None) -> None:
        self._clipboard(widget).set(text)

    # -- saving & history ---------------------------------------------------------
    def record(self, image: Image.Image, source: str = "region", force_save: bool = False) -> Entry:
        entry = self.history.add(image, source)
        if force_save or self.settings.get_boolean("auto-save"):
            try:
                path = export.save_image(
                    image,
                    config.save_folder(),
                    self.settings.get_string("filename-pattern"),
                    self.settings.get_string("export-format"),
                    self.settings.get_int("export-quality"),
                )
                entry.saved_path = str(path)
                self.history.update(entry)
            except OSError as err:
                # (not the error's own text: it names the file)
                log.warning("auto-save failed: %s", err.strerror or type(err).__name__)
                self.notify_save_failed(err)
        self.history.prune(self.settings.get_int("history-limit"))
        return entry

    def render_entry(self, entry: Entry, original: Image.Image | None = None) -> Image.Image:
        """The capture with its annotations applied (or the original if there are none)."""
        from PIL import Image

        original = original or Image.open(self.history.original_path(entry.id))
        doc_path = self.history.document_path(entry.id)
        render = self._module("editor.render")
        if render is not None and doc_path.exists():
            try:
                return render.render_document_file(doc_path, original)
            except Exception as err:  # noqa: BLE001
                log.warning("could not render annotations: %s", type(err).__name__)
                # The markup can't be read, and it may have hidden something. The capture's
                # own file is the last version that had it applied: better that than the
                # bare original.
                saved = self.saved_file(entry)
                if entry.edited and saved is not None:
                    try:
                        with Image.open(saved) as marked_up:
                            return marked_up.convert("RGB")
                    except OSError:
                        pass
        return original.convert("RGB")

    def entry_image(self, entry: Entry) -> Image.Image | None:
        """The capture as it is shared (markup applied). None, after telling the user, when
        its picture is gone from the history folder."""
        try:
            return self.render_entry(entry)
        except (OSError, ValueError) as err:
            log.warning("a capture's picture can't be read: %s", type(err).__name__)
            self.notify_capture_missing()
            return None

    def notify_capture_missing(self) -> None:
        self.notify(
            _("This capture can't be opened"),
            _("Its picture is missing from GrabbyHands' history."),
            tag="error",
        )

    def save_as(
        self, image: Image.Image, entry: Entry | None = None, parent: Gtk.Window | None = None
    ) -> None:
        dialog_mod = self._module("export_dialog")
        if dialog_mod is not None:
            dialog_mod.run_save_as(self, image, parent)
            return
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(
            export.make_filename(self.settings.get_string("filename-pattern")) + ".png"
        )
        dialog.set_initial_folder(Gio.File.new_for_path(str(config.save_folder())))

        def done(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                gfile = dlg.save_finish(result)
            except GLib.Error:
                return
            path = Path(gfile.get_path())
            export.write_image(image, path, self.settings.get_int("export-quality"))
            self.notify(_("Screenshot saved"), export.display_path(path), tag="saved")

        dialog.save(parent, None, done)

    def delete_capture(self, entry_id: str, trash_saved: bool = True) -> None:
        entry = self.history.remove(entry_id)
        if entry is not None and trash_saved:
            self._trash_saved_file(entry)

    def clear_history(self, trash_saved: bool = True) -> int:
        removed = self.history.clear()
        if trash_saved:
            for entry in removed:
                self._trash_saved_file(entry)
        return len(removed)

    def _trash_saved_file(self, entry: Entry) -> None:
        """Move a capture's auto-saved file to the Trash (recoverable, never deleted)."""
        if not entry.saved_path:
            return
        path = Path(entry.saved_path)
        if not path.is_file():
            return
        try:
            Gio.File.new_for_path(str(path)).trash(None)
        except GLib.Error as err:
            # (neither the file's name nor the message, which quotes it, goes in the log)
            log.info("could not move a saved capture to the trash (%s)", err.domain)

    # -- windows ------------------------------------------------------------------
    def show_color(self, rgb: tuple[int, int, int]) -> None:
        from grabbyhands.color_window import ColorWindow

        ColorWindow(self, rgb).present()

    def open_editor(self, entry: Entry, auto_redact: bool = False) -> None:
        mod = self._module("editor.window")
        if mod is None:
            self.notify(_("The editor isn't available in this build"), tag="error")
            return
        for win in self.get_windows():
            if isinstance(win, mod.EditorWindow) and win.entry.id == entry.id:
                win.present()
                if auto_redact:
                    win.auto_redact()
                return
        try:
            win = mod.EditorWindow(self, entry)
        except (OSError, ValueError) as err:  # its picture is gone or unreadable
            log.warning("could not open a capture in the editor: %s", type(err).__name__)
            self.notify_capture_missing()
            return
        win.present()
        if auto_redact:
            GLib.idle_add(lambda: (win.auto_redact(), GLib.SOURCE_REMOVE)[1])

    def pin(self, image: Image.Image, entry: Entry | None = None) -> None:
        mod = self._module("pin")
        if mod is None:
            return
        win = mod.PinWindow(self, image, entry)
        self._pins.append(win)
        win.connect("close-request", lambda w: (self._pins.remove(w), False)[1])
        win.present()

    def capture_from_ui(self) -> None:
        """Start a capture from a button in one of our own windows.

        History closes first (and gets a moment to disappear) so it isn't in the picture.
        """
        if self._history_window is not None:
            self._history_window.close()
        GLib.timeout_add(250, lambda: (self.capture.capture(), GLib.SOURCE_REMOVE)[1])

    def show_history(self) -> None:
        mod = self._module("history_window")
        if mod is None:
            self.capture.capture()
            return
        if self._history_window is None:
            self._history_window = mod.HistoryWindow(self)
            self._history_window.connect(
                "close-request", lambda *_: (setattr(self, "_history_window", None), False)[1]
            )
        self._history_window.present()

    def show_preferences(self, parent: Gtk.Window | None = None, page: str | None = None) -> None:
        mod = self._module("preferences")
        if mod is None:
            return
        if self._preferences is None:  # one Settings dialog at a time
            self._preferences = mod.show(self, parent or self.get_active_window())
            when_closed(self._preferences, lambda: setattr(self, "_preferences", None))
        if page:
            self._preferences.set_visible_page_name(page)
        self.withdraw_notification("window-helper")

    def show_welcome(self, parent: Gtk.Widget | None = None) -> None:
        """The getting-started guide: what GrabbyHands is, its keys, Print Screen setup."""
        mod = self._module("welcome")
        if mod is None:
            return
        self.settings.set_boolean("welcome-shown", True)
        if self._welcome is not None:
            return
        self._welcome = mod.WelcomeDialog(self)
        when_closed(self._welcome, self._welcome_closed)
        self._welcome.present(parent or self.get_active_window())

    def _welcome_closed(self) -> None:
        self._welcome = None
        if self._first_run and not self._shortcuts_asked:
            # First run: the guide has been read (see _startup_shortcuts). Now ask, but
            # not in the middle of a capture: the guide's own button may have started one.
            def ask() -> bool:
                if self.capture.active:
                    return GLib.SOURCE_CONTINUE
                self._setup_shortcuts()
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(600, ask)

    def show_shortcuts(self, parent: Gtk.Widget | None = None) -> None:
        """The list of every key GrabbyHands understands."""
        mod = self._module("shortcuts_dialog")
        if mod is None or self._shortcuts_dialog is not None:
            return
        if self.capture.windows:
            return  # never on top of the frozen capture screen
        self._shortcuts_dialog = mod.ShortcutsDialog(self)
        when_closed(self._shortcuts_dialog, lambda: setattr(self, "_shortcuts_dialog", None))
        self._shortcuts_dialog.present(parent or self.get_active_window())

    def show_text_picker(self, image: Image.Image, words: list) -> None:
        """Let the user pick which recognized words to copy."""
        mod = self._module("text_dialog")
        if mod is not None:
            mod.TextWindow(self, "", image=image, words=words).present()

    def show_text(self, text: str) -> None:
        mod = self._module("text_dialog")
        if mod is not None and text:
            mod.TextWindow(self, text).present()

    def show_about(self, parent: Gtk.Window | None = None) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name="al3phnul",
            version=self.version,
            website="https://github.com/al3phnul/GrabbyHands",
            issue_url="https://github.com/al3phnul/GrabbyHands/issues",
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 al3phnul and contributors",
            comments=_("Grab, mark up and copy screenshots. Nothing ever leaves your computer."),
        )
        about.present(parent or self.get_active_window())

    # -- notifications ------------------------------------------------------------
    def notify(
        self,
        title: str,
        body: str | None = None,
        tag: str = "info",
        transient: bool = False,
        buttons: tuple[tuple[str, str], ...] = (),
    ) -> Gio.Notification:
        """Send a notification. ``buttons`` are ``(label, detailed action name)`` pairs,
        for example ``("Open Settings", "app.settings-page::saving")``."""
        note = Gio.Notification.new(title)
        if body:
            note.set_body(body)
        note.set_icon(Gio.ThemedIcon.new(APP_ID))
        if transient:
            note.set_priority(Gio.NotificationPriority.LOW)
        for label, action in buttons:
            note.add_button(label, action)
        self.send_notification(tag, note)
        return note

    def notify_save_failed(self, err: OSError) -> None:
        """Saving to the user's folder failed: say why and where to fix it."""
        try:
            folder = export.display_path(config.save_folder())
        except OSError:
            folder = self.settings.get_string("save-folder")
        self.notify(
            _("Couldn't save the screenshot"),
            _(
                "GrabbyHands can't write to {folder} ({reason}). Your capture is still in "
                "History. Choose another folder in Settings → Saving."
            ).format(folder=folder, reason=err.strerror or str(err)),
            tag="error",
            buttons=((_("Open Settings"), "app.settings-page::saving"),),
        )

    def notify_capture(self, entry: Entry, title: str, saved: bool = False) -> None:
        """Tell the user a capture is done. ``saved``: saving was the point (not a bonus)."""
        note = Gio.Notification.new(title)
        if entry.saved_path:
            path = Path(entry.saved_path)
            if saved:
                note.set_body(export.display_path(path))
            else:
                note.set_body(_("Also saved as {name}").format(name=path.name))
        note.set_icon(self._thumbnail_icon(entry))
        target = GLib.Variant("s", entry.id)
        if self.supports("edit"):
            note.set_default_action_and_target("app.edit-capture", target)
            note.add_button_with_target(_("Edit"), "app.edit-capture", target)
        if self.saved_file(entry) is not None:  # nothing to show for a clipboard-only capture
            note.add_button_with_target(_("Show in Folder"), "app.show-capture", target)
        self.send_notification("capture", note)

    def _thumbnail_icon(self, entry: Entry) -> Gio.Icon:
        thumb = self.history.thumb_path(entry.id)
        if not config.in_flatpak():
            if thumb.is_file():
                return Gio.FileIcon.new(Gio.File.new_for_path(str(thumb)))
            return Gio.ThemedIcon.new(APP_ID)
        # Sandboxed, notifications go through the portal, which takes only named icons and
        # picture data. Older portals (1.18) drop the whole notification for a file icon.
        try:
            return Gio.BytesIcon.new(GLib.Bytes.new(thumb.read_bytes()))
        except OSError:
            return Gio.ThemedIcon.new(APP_ID)

    def notify_text(self, title: str, text: str) -> None:
        self._last_text = text
        lines = text.count("\n") + 1
        note = Gio.Notification.new(title)
        preview = text if len(text) <= 140 else text[:140] + "…"
        body = ngettext("{n} line: {preview}", "{n} lines: {preview}", lines)
        note.set_body(body.format(n=lines, preview=preview))
        note.set_icon(Gio.ThemedIcon.new(APP_ID))
        if self._module("text_dialog") is not None:
            note.set_default_action("app.show-last-text")
            note.add_button(_("Show Text"), "app.show-last-text")
        self.send_notification("text", note)

    def notify_codes(self, codes: list) -> None:
        first = codes[0]
        title = (
            _("Copied {kind}").format(kind=first.kind)
            if len(codes) == 1
            else _("Copied {n} codes").format(n=len(codes))
        )
        note = Gio.Notification.new(title)
        preview = first.text if len(first.text) <= 140 else first.text[:140] + "…"
        note.set_body(preview)
        note.set_icon(Gio.ThemedIcon.new(APP_ID))
        if first.text.lower().startswith(("http://", "https://")):
            note.add_button_with_target(
                _("Open Link"), "app.open-url", GLib.Variant("s", first.text)
            )
        self._last_text = "\n".join(c.text for c in codes)
        self.send_notification("text", note)

    # -- desktop integration ------------------------------------------------------
    def shortcut_state(self) -> str:
        """Where the global shortcuts stand, for the UI.

        ``ready``: the desktop bound them. ``unset``: not yet (the desktop may still be
        asking). ``declined``: the user dismissed the desktop's dialog. ``unavailable``: this
        desktop has no global shortcuts for apps.
        """
        shortcuts = self.shortcuts
        if shortcuts is None:
            return "unset"
        if not self._shortcuts_available:
            return "unavailable"
        if "capture" in shortcuts.triggers:
            return "ready"
        return "declined" if self.settings.get_boolean("shortcuts-declined") else "unset"

    def shortcut_list(self) -> list[tuple[str, str, str, bool]]:
        """``(id, what it does, keys, bound?)`` for every global shortcut.

        ``keys`` is what the desktop says it bound; for a shortcut that isn't bound yet it is
        the key we suggest.
        """
        triggers = self.shortcuts.triggers if self.shortcuts is not None else {}
        rows = []
        for shortcut_id, name, _trigger in _shortcut_specs():
            bound = shortcut_id in triggers
            keys = triggers[shortcut_id] if bound else SHORTCUT_DEFAULT_KEYS[shortcut_id]
            rows.append((shortcut_id, name, keys, bound))
        return rows

    def watch_shortcuts(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the global shortcuts change; returns a disconnector."""
        self._shortcut_listeners.append(listener)

        def disconnect() -> None:
            if listener in self._shortcut_listeners:
                self._shortcut_listeners.remove(listener)

        return disconnect

    def _shortcuts_changed(self) -> None:
        for listener in list(self._shortcut_listeners):
            listener()

    def setup_shortcuts_again(self) -> None:
        """Ask the desktop for the shortcuts again (after declining, or to change keys)."""
        self.settings.set_boolean("shortcuts-declined", False)
        self._setup_shortcuts(force=True)

    def _startup_shortcuts(self) -> bool:
        """Ask the desktop for the global shortcuts, at the start of every session.

        The very first run is different. GNOME shows a dialog for the shortcuts, and at
        that moment Print Screen still belongs to GNOME's own tool. Getting Started explains
        both and offers the hand-over first; its buttons, or closing it, then ask (once,
        with Print Screen already free). So on a first run, find out whether the desktop
        has shortcuts for apps at all, and give a window a moment to open. Without one
        (started in the background), ask as on any other start.
        """
        if not self._first_run:
            return self._setup_shortcuts()
        self._setup_shortcuts(ask=False)

        def unless_guide_is_open() -> bool:
            if self._welcome is None:
                self._setup_shortcuts()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(FIRST_RUN_WAIT_MS, unless_guide_is_open)
        return GLib.SOURCE_REMOVE

    def _portal_restarted(self) -> None:
        """The desktop's portal service was restarted: our shortcuts session went with it."""
        if self.shortcuts is None or not self._shortcuts_asked:
            return
        log.info("the portal was restarted; setting the global shortcuts up again")
        self.shortcuts.stop()
        self._shortcuts_asked = False
        self._shortcuts_changed()
        self._setup_shortcuts()

    def _setup_shortcuts(self, force: bool = False, ask: bool = True) -> bool:
        if self.shortcuts is None:
            self.shortcuts = GlobalShortcuts(
                self.portal, self._on_shortcut, self._shortcuts_changed
            )
            self._shortcuts_available = self.shortcuts.available
            if not self._shortcuts_available:
                log.info("GlobalShortcuts portal not available")
                self._shortcuts_changed()
                return GLib.SOURCE_REMOVE
        if not self._shortcuts_available:
            return GLib.SOURCE_REMOVE
        if not ask:
            self._shortcuts_changed()
            return GLib.SOURCE_REMOVE
        if not self._shortcuts_asked:
            self._shortcuts_asked = True
            # The portal wants shortcuts bound in every new session. GNOME remembers the
            # user's approval, so only the very first bind shows a dialog.
            declined = self.settings.get_boolean("shortcuts-declined")

            def done(ok: bool, ids: list[str]) -> None:
                log.info("global shortcuts answered=%s bound=%s", ok, ids)
                if ids:
                    self.settings.set_boolean("shortcuts-declined", False)
                elif ok:
                    self.settings.set_boolean("shortcuts-declined", True)
                self._shortcuts_changed()

            self.shortcuts.start(
                _shortcut_specs(), bind_if_missing=force or not declined, done=done, complete=force
            )
        elif force and self.shortcuts.session_handle:

            def rebound(ok: bool, ids: list[str]) -> None:
                log.info("rebound: %s", ids)
                self._shortcuts_changed()

            self.shortcuts.bind(_shortcut_specs(), rebound)
        return GLib.SOURCE_REMOVE

    def _on_shortcut(self, shortcut_id: str) -> None:
        handlers = {
            "capture": self.capture.capture,
            "repeat-region": self.capture.repeat_last,
            "color-picker": self.capture.pick_color,
            "capture-text": self.capture.capture_text,
        }
        handler = handlers.get(shortcut_id)
        if handler:
            handler()

    def _sync_autostart(self) -> bool:
        enabled = self.settings.get_boolean("autostart")
        if config.in_flatpak():
            self.portal.request_background(enabled)
        elif not os.environ.get("GRABBYHANDS_DEV"):
            self._host_autostart(enabled)
        return GLib.SOURCE_REMOVE

    def _host_autostart(self, enabled: bool) -> None:
        autostart = Path(GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"
        launcher = shutil.which("grabbyhands") or os.path.realpath(sys.argv[0])
        try:
            if not enabled:
                autostart.unlink(missing_ok=True)
                return
            content = config.autostart_entry(launcher)
            autostart.parent.mkdir(parents=True, exist_ok=True)
            if not autostart.exists() or autostart.read_text() != content:
                autostart.write_text(content)
        except (OSError, ValueError) as err:
            log.info("could not update the autostart entry: %s", err)

    def set_autostart(self, enabled: bool) -> None:
        self.settings.set_boolean("autostart", enabled)
        self._sync_autostart()
