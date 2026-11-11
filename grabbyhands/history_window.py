# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""History gallery: every capture, newest first, with delete and clear-all."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from grabbyhands import config, keys
from grabbyhands.i18n import ngettext
from grabbyhands.style import settings_button, tooltip

if TYPE_CHECKING:
    from grabbyhands.application import GrabbyHandsApp
    from grabbyhands.history import Entry


class HistoryItem(GObject.Object):
    def __init__(self, entry: Entry) -> None:
        super().__init__()
        self.entry = entry


def describe(entry: Entry, now: datetime | None = None) -> str:
    """'Today 14:03', 'Yesterday 09:12' or 'Nov 3 17:40', plus the size."""
    now = now or datetime.now()
    when = entry.created_dt
    days = (now.date() - when.date()).days
    clock = when.strftime("%H:%M")
    if days == 0:
        day = _("Today")
    elif days == 1:
        day = _("Yesterday")
    elif when.year == now.year:
        day = f"{when:%b} {when.day}"
    else:
        day = f"{when:%b} {when.day}, {when.year}"
    return f"{day} {clock}  ·  {entry.width} × {entry.height}"


class HistoryWindow(Adw.ApplicationWindow):
    def __init__(self, app: GrabbyHandsApp) -> None:
        super().__init__(
            application=app,
            title=_("GrabbyHands History"),
            default_width=app.settings.get_int("history-width"),
            default_height=app.settings.get_int("history-height"),
        )
        self.app = app
        self.store = app.history
        self._pending: dict[str, Adw.Toast] = {}  # deleted, waiting for Undo to time out

        self.model = Gio.ListStore(item_type=HistoryItem)
        self.selection = Gtk.SingleSelection(model=self.model, autoselect=False)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_cell)
        factory.connect("bind", self._bind_cell)
        self.grid = Gtk.GridView(
            model=self.selection, factory=factory, max_columns=8, min_columns=1
        )
        self.grid.add_css_class("gh-history-grid")
        self.grid.connect("activate", lambda _g, pos: self._open(self._entry_at(pos)))

        scroller = Gtk.ScrolledWindow(child=self.grid, vexpand=True)
        self.empty = Adw.StatusPage(icon_name="gh-history-symbolic", title=_("No Captures Yet"))
        self.empty_capture = Gtk.Button(label=_("Take a Screenshot"))
        self.empty_capture.add_css_class("suggested-action")
        self.empty_capture.add_css_class("pill")
        self.empty_capture.connect("clicked", lambda *_: self._new_capture())
        self.empty_guide = Gtk.Button(label=_("Getting Started"))
        self.empty_guide.add_css_class("pill")
        self.empty_guide.set_tooltip_text(_("What GrabbyHands does, its keys, and Print Screen"))
        self.empty_guide.set_action_name("app.welcome")
        empty_buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        empty_buttons.append(self.empty_capture)
        empty_buttons.append(self.empty_guide)
        self.empty.set_child(empty_buttons)
        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "grid")
        self.stack.add_named(self.empty, "empty")
        self.toasts = Adw.ToastOverlay(child=self.stack)

        header = Adw.HeaderBar()
        capture = Gtk.Button(
            child=Adw.ButtonContent(icon_name="gh-region-symbolic", label=_("New Capture"))
        )
        capture.add_css_class("suggested-action")
        capture.connect("clicked", lambda *_: self._new_capture())
        header.pack_start(capture)
        self.capture_button = capture
        folder = Gtk.Button(
            child=Adw.ButtonContent(icon_name="folder-open-symbolic", label=_("Open Folder"))
        )
        folder.set_tooltip_markup(tooltip(_("Open Folder"), _("Where your captures are saved")))
        folder.connect("clicked", lambda *_: self._open_folder())
        header.pack_start(folder)
        help_menu = Gio.Menu()
        help_menu.append(_("Getting Started"), "app.welcome")
        help_menu.append(_("Keyboard Shortcuts"), "app.keyboard-shortcuts")
        help_menu.append(_("About GrabbyHands"), "app.about")
        self.help_button = Gtk.MenuButton(icon_name="help-about-symbolic", menu_model=help_menu)
        self.help_button.set_tooltip_markup(
            tooltip(_("Help"), _("Getting started, keyboard shortcuts and about"))
        )
        header.pack_end(self.help_button)
        settings = settings_button()
        settings.set_action_name("app.preferences")
        header.pack_end(settings)
        # Settings and Help are set apart from the actions, as in the editor's header.
        divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        divider.set_margin_top(8)
        divider.set_margin_bottom(8)
        divider.set_margin_start(4)
        divider.set_margin_end(4)
        header.pack_end(divider)
        self.clear_button = Gtk.Button(label=_("Clear History…"))
        self.clear_button.add_css_class("flat")  # quiet: red lettering, no filled button
        self.clear_button.add_css_class("gh-danger")
        self.clear_button.connect("clicked", lambda *_: self._confirm_clear())
        header.pack_end(self.clear_button)

        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        for name, callback in (
            ("copy-capture", self._copy_by_id),
            ("save-capture", self._save_by_id),
            ("delete-capture", self._delete_by_id),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, p, cb=callback: cb(p.get_string()))
            self.add_action(action)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self._disconnect = self.store.connect(self._reload)
        self._unwatch_shortcuts = app.watch_shortcuts(self._update_empty_text)
        self.connect("close-request", self._on_close)
        self._update_empty_text()
        self._reload()
        self.grid.grab_focus()  # arrow keys, Enter and Delete work right away

    # -- model --------------------------------------------------------------------
    def _reload(self) -> None:
        items = [HistoryItem(e) for e in self.store.entries() if e.id not in self._pending]
        self.model.splice(0, self.model.get_n_items(), items)
        empty = not items
        self.stack.set_visible_child_name("empty" if empty else "grid")
        # One primary button at a time: with nothing here yet it is the big one in the middle.
        if empty:
            self.capture_button.remove_css_class("suggested-action")
        else:
            self.capture_button.add_css_class("suggested-action")
        self.clear_button.set_sensitive(bool(self.store.entries()))

    def _entry_at(self, position: int) -> Entry | None:
        item = self.model.get_item(position)
        return item.entry if item is not None else None

    def _update_empty_text(self) -> None:
        """Name the key that actually takes a screenshot, if there is one yet."""
        keys = next(
            (k for sid, _name, k, bound in self.app.shortcut_list() if sid == "capture" and bound),
            "",
        )
        if keys:
            text = _("Press {keys} at any time to grab something. Your captures show up here.")
            self.empty.set_description(GLib.markup_escape_text(text.format(keys=keys)))
        else:
            self.empty.set_description(
                _("Take a screenshot to get going. Your captures show up here.")
            )
        # the button's tooltip shows the same key, and no key at all while none is set up
        self.capture_button.set_tooltip_markup(
            tooltip(_("New Capture"), _("Select part of the screen"), keys or None)
        )

    # -- cells --------------------------------------------------------------------
    def _setup_cell(self, _factory, list_item: Gtk.ListItem) -> None:
        # The whole capture, never a cropped slice of it, on a mat that gives light and
        # dark pictures an edge.
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.SCALE_DOWN, can_shrink=True)
        picture.set_size_request(208, 126)
        for side in ("top", "bottom", "start", "end"):
            getattr(picture, f"set_margin_{side}")(6)
        mat = Gtk.Box()
        mat.add_css_class("gh-thumb")
        mat.append(picture)
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        badge = Gtk.Label(label=_("Edited"), valign=Gtk.Align.CENTER)
        badge.add_css_class("gh-badge")
        meta = Gtk.Box(spacing=6)
        meta.append(label)
        meta.append(badge)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(mat)
        box.append(meta)

        delete = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text=_("Delete"),
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            margin_top=6,
            margin_end=6,
        )
        delete.add_css_class("osd")
        delete.add_css_class("circular")
        delete.add_css_class("gh-thumb-delete")
        overlay = Gtk.Overlay(child=box)
        overlay.add_overlay(delete)
        overlay.add_css_class("gh-history-cell")

        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", self._on_cell_menu, list_item)
        overlay.add_controller(right)
        delete.connect("clicked", lambda _b: self._delete_item(list_item))

        list_item.set_child(overlay)
        list_item._widgets = (picture, label, badge)  # noqa: SLF001 - our own bookkeeping

    def _bind_cell(self, _factory, list_item: Gtk.ListItem) -> None:
        entry = list_item.get_item().entry
        picture, label, badge = list_item._widgets  # noqa: SLF001
        badge.set_visible(entry.edited)
        thumb = self.store.thumb_path(entry.id)
        picture.set_filename(str(thumb) if thumb.exists() else None)
        label.set_text(describe(entry))
        where = entry.saved_path or _("Not saved to a file (kept in history only)")
        how = _("Double-click to edit · Right-click for more")
        picture.set_tooltip_text(f"{where}\n{how}")

    def _delete_item(self, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        if item is not None:
            self.delete(item.entry)

    def _on_cell_menu(self, gesture: Gtk.GestureClick, _n, x: float, y: float, list_item) -> None:
        item = list_item.get_item()
        if item is None:
            return
        entry = item.entry
        self.selection.set_selected(list_item.get_position())
        menu = Gio.Menu()
        target = GLib.Variant("s", entry.id)
        if self.app.supports("edit"):
            menu.append_item(self._menu_item(_("Edit"), "app.edit-capture", target))
        menu.append_item(self._menu_item(_("Copy"), "win.copy-capture", target))
        if self.app.supports("pin"):
            menu.append_item(self._menu_item(_("Pin to Screen"), "app.pin-capture", target))
        menu.append_item(self._menu_item(_("Save As…"), "win.save-capture", target))
        if self.app.saved_file(entry) is not None:  # only captures that have a file
            menu.append_item(self._menu_item(_("Show in Folder"), "app.show-capture", target))
        danger = Gio.Menu()
        danger.append_item(self._menu_item(_("Delete"), "win.delete-capture", target))
        menu.append_section(None, danger)
        popover = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        popover.set_parent(gesture.get_widget())
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    @staticmethod
    def _menu_item(label: str, action: str, target: GLib.Variant) -> Gio.MenuItem:
        item = Gio.MenuItem.new(label, None)
        item.set_action_and_target_value(action, target)
        return item

    # -- actions ------------------------------------------------------------------
    def _image_for(self, entry: Entry):
        """The capture with its markup, or None (the user has been told) if it is gone."""
        return self.app.entry_image(entry)

    def _copy_by_id(self, entry_id: str) -> None:
        entry = self.store.get(entry_id)
        image = self._image_for(entry) if entry is not None else None
        if image is not None:
            self.app.copy_image(image, self)
            self.toasts.add_toast(Adw.Toast(title=_("Copied to the clipboard"), timeout=2))

    def _save_by_id(self, entry_id: str) -> None:
        entry = self.store.get(entry_id)
        image = self._image_for(entry) if entry is not None else None
        if image is not None:
            self.app.save_as(image, entry, self)

    def _delete_by_id(self, entry_id: str) -> None:
        entry = self.store.get(entry_id)
        if entry is not None:
            self.delete(entry)

    def _open(self, entry: Entry | None) -> None:
        if entry is None:
            return
        if self.app.supports("edit"):
            self.app.open_editor(entry)
        elif self.app.supports("pin"):
            image = self._image_for(entry)
            if image is not None:
                self.app.pin(image, entry)

    def delete(self, entry: Entry) -> None:
        """Hide the capture now; really delete it unless the user presses Undo."""
        if entry.id in self._pending:
            return
        has_file = bool(entry.saved_path) and Path(entry.saved_path).is_file()
        title = (
            _("Capture deleted. Its file goes to the Trash.") if has_file else _("Capture deleted")
        )
        toast = Adw.Toast(title=title, button_label=_("Undo"), timeout=5)
        self._pending[entry.id] = toast

        def undo(_toast: Adw.Toast) -> None:
            self._pending.pop(entry.id, None)
            self._reload()

        def dismissed(_toast: Adw.Toast) -> None:
            if self._pending.pop(entry.id, None) is not None:
                self.app.delete_capture(entry.id)

        toast.connect("button-clicked", undo)
        toast.connect("dismissed", dismissed)
        self.toasts.add_toast(toast)
        self._reload()

    def _commit_pending(self) -> None:
        for entry_id in list(self._pending):
            self._pending.pop(entry_id, None)
            self.app.delete_capture(entry_id)

    def finish(self) -> None:
        """The app is quitting with this window open: what was deleted stays deleted."""
        self._commit_pending()

    def _confirm_clear(self) -> None:
        count = len(self.store.entries())
        if count == 1:
            body = _("This removes the only capture from GrabbyHands' history. It can't be undone.")
        else:
            body = _(
                "This removes all {n} captures from GrabbyHands' history. It can't be undone."
            ).format(n=count)
        dialog = Adw.AlertDialog(heading=_("Clear History?"), body=body)
        trash = Gtk.CheckButton(
            label=_("Also move their saved files in {folder} to the Trash").format(
                folder=config.save_folder().name
            ),
            active=True,
        )
        dialog.set_extra_child(trash)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear History"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def chosen(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "clear":
                return
            self._pending.clear()
            removed = self.app.clear_history(trash_saved=trash.get_active())
            done = ngettext("Cleared {n} capture", "Cleared {n} captures", removed)
            self.toasts.add_toast(Adw.Toast(title=done.format(n=removed), timeout=3))

        dialog.connect("response", chosen)
        dialog.present(self)

    def _new_capture(self) -> None:
        self.app.capture_from_ui()  # closes this window first so it isn't in the picture

    def _open_folder(self) -> None:
        folder = config.save_folder()
        folder.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(folder))).launch(self, None, None)

    def _on_key(self, _ctrl, keyval: int, code: int, state: Gdk.ModifierType) -> bool:
        item = self.selection.get_selected_item()
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and item is not None:
            self.delete(item.entry)
            return True
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        keyval = keys.latin_keyval(keyval, code)  # Ctrl+C on any keyboard layout
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C) and item is not None:
            self._copy_by_id(item.entry.id)
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_close(self, _win: Gtk.Window) -> bool:
        self._commit_pending()
        self._disconnect()
        self._unwatch_shortcuts()
        if not self.is_maximized():
            self.app.settings.set_int("history-width", self.get_width())
            self.app.settings.set_int("history-height", self.get_height())
        return False
