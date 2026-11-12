# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Save As: pick format, quality and size, then a file name."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from grabbyhands import config, export

if TYPE_CHECKING:
    from PIL import Image

    from grabbyhands.application import GrabbyHandsApp

FORMATS = [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WebP")]
SCALES = [100, 75, 50, 25]


class SaveAsDialog(Adw.Dialog):
    def __init__(self, app: GrabbyHandsApp, image: Image.Image) -> None:
        super().__init__(title=_("Save As"), content_width=420)
        self.app = app
        self.image = image
        settings = app.settings

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)

        self.format_row = Adw.ComboRow(
            title=_("Format"), model=Gtk.StringList.new([label for _v, label in FORMATS])
        )
        current = settings.get_string("save-as-format")
        codes = [code for code, _label in FORMATS]
        self.format_row.set_selected(codes.index(current) if current in codes else 0)
        self.format_row.connect("notify::selected", lambda *_: self._sync())
        group.add(self.format_row)

        self.quality_row = Adw.SpinRow.new_with_range(10, 100, 5)
        self.quality_row.set_title(_("Quality"))
        self.quality_row.set_subtitle(_("Lower quality makes a smaller file"))
        self.quality_row.set_value(settings.get_int("save-as-quality"))
        group.add(self.quality_row)

        w, h = image.size
        labels = [f"{s}%  ({round(w * s / 100)} × {round(h * s / 100)})" for s in SCALES]
        self.scale_row = Adw.ComboRow(title=_("Size"), model=Gtk.StringList.new(labels))
        scale = settings.get_int("save-as-scale")
        self.scale_row.set_selected(SCALES.index(scale) if scale in SCALES else 0)
        group.add(self.scale_row)

        save = Gtk.Button(label=_("Choose Location…"), halign=Gtk.Align.CENTER)
        save.add_css_class("suggested-action")
        save.add_css_class("pill")
        save.connect("clicked", lambda *_: self._choose())
        footer = Adw.PreferencesGroup()
        footer.add(save)
        page.add(footer)

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        self.set_default_widget(save)  # Enter goes straight on to choosing the location
        self.set_focus(save)
        self._sync()

    @property
    def fmt(self) -> str:
        return FORMATS[self.format_row.get_selected()][0]

    def _sync(self) -> None:
        self.quality_row.set_sensitive(self.fmt != "png")

    def _choose(self) -> None:
        settings = self.app.settings
        settings.set_string("save-as-format", self.fmt)
        settings.set_int("save-as-quality", int(self.quality_row.get_value()))
        settings.set_int("save-as-scale", SCALES[self.scale_row.get_selected()])

        suffix = export.FORMATS[self.fmt][1]
        dialog = Gtk.FileDialog(title=_("Save Screenshot"))
        dialog.set_initial_name(
            export.make_filename(settings.get_string("filename-pattern")) + suffix
        )
        dialog.set_initial_folder(Gio.File.new_for_path(str(config.save_folder())))
        parent = self.get_root() if isinstance(self.get_root(), Gtk.Window) else None
        dialog.save(parent, None, self._chosen)

    def _chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return  # canceled
        location = gfile.get_path()
        if location is None:  # a place that isn't a folder on this computer
            self.app.notify(
                _("Couldn't save the screenshot"),
                _("{file} couldn't be written ({reason}). Try another folder.").format(
                    file=gfile.get_parse_name(), reason=os.strerror(errno.ENOTSUP)
                ),
                tag="error",
            )
            return
        path = export.path_for_format(Path(location), self.fmt)
        if self.write(path) is not None:
            self.close()  # on failure the dialog stays so another place can be chosen

    def write(self, path: Path) -> Path | None:
        """Write the file and say so. Returns None (after telling the user why) on failure."""
        settings = self.app.settings
        try:
            out = export.write_image(
                self.image,
                path,
                settings.get_int("save-as-quality"),
                scale_percent=settings.get_int("save-as-scale"),
            )
        except OSError as err:
            self.app.notify(
                _("Couldn't save the screenshot"),
                _("{file} couldn't be written ({reason}). Try another folder.").format(
                    file=export.display_path(path), reason=err.strerror or str(err)
                ),
                tag="error",
            )
            return None
        # A normal notification, like the Save button's: a quiet one may never be shown,
        # and then nothing would confirm that the file was written.
        self.app.notify(_("Screenshot saved"), export.display_path(out), tag="saved")
        return out


def run_save_as(app: GrabbyHandsApp, image: Image.Image, parent: Gtk.Widget | None) -> SaveAsDialog:
    dialog = SaveAsDialog(app, image)
    dialog.present(parent)
    return dialog
