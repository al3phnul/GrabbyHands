# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The annotation editor window."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from PIL import Image

from grabbyhands import APP_ID, config, export, keys
from grabbyhands.editor.canvas import Canvas
from grabbyhands.editor.document import Document, Item, RedactItem
from grabbyhands.editor.render import render_document
from grabbyhands.editor.tools.base import Style
from grabbyhands.editor.tools.stamp import STAMPS
from grabbyhands.style import section_card, settings_button, tile_button, tooltip

if TYPE_CHECKING:
    from grabbyhands.application import GrabbyHandsApp
    from grabbyhands.history import Entry

log = logging.getLogger(__name__)

SWATCHES = (
    "#ff2d6f",
    "#ff7a1a",
    "#ffd60a",
    "#22c55e",
    "#06b6d4",
    "#3b82f6",
    "#7c6cff",
    "#111111",
    "#ffffff",
)
SWATCH_NAMES = (
    _("Pink"),
    _("Orange"),
    _("Yellow"),
    _("Green"),
    _("Cyan"),
    _("Blue"),
    _("Violet"),
    _("Black"),
    _("White"),
)
EMOJI_PLACEHOLDER = "😀"

# (tool id, icon, short label, key, section, how to use it)
TOOLS = [
    (
        "select",
        "gh-tool-select-symbolic",
        _("Select"),
        "V",
        "basics",
        _(
            "Click something you drew to move, resize or recolor it. "
            "Double-click text to edit it. Delete removes it."
        ),
    ),
    (
        "crop",
        "gh-tool-crop-symbolic",
        _("Crop"),
        "C",
        "basics",
        _("Drag the area to keep, then press Enter. Esc cancels."),
    ),
    (
        "arrow",
        "gh-tool-arrow-symbolic",
        _("Arrow"),
        "A",
        "draw",
        _("Drag from the tail to the tip. Hold Shift for straight angles."),
    ),
    (
        "line",
        "gh-tool-line-symbolic",
        _("Line"),
        "L",
        "draw",
        _("Drag to draw a line. Hold Shift for straight angles."),
    ),
    (
        "rect",
        "gh-tool-rect-symbolic",
        _("Box"),
        "R",
        "draw",
        _("Drag to draw a box. Hold Shift for a square."),
    ),
    (
        "ellipse",
        "gh-tool-ellipse-symbolic",
        _("Circle"),
        "O",
        "draw",
        _("Drag to draw an oval. Hold Shift for a circle."),
    ),
    (
        "pen",
        "gh-tool-pen-symbolic",
        _("Pen"),
        "P",
        "draw",
        _("Draw freehand. Hold Shift for a straight stroke."),
    ),
    (
        "highlighter",
        "gh-tool-highlighter-symbolic",
        _("Highlight"),
        "H",
        "draw",
        _("Paint over text to highlight it, like a marker."),
    ),
    (
        "text",
        "gh-tool-text-symbolic",
        _("Text"),
        "T",
        "text",
        _("Click where the text goes and type. Click away or press Esc when done."),
    ),
    (
        "callout",
        "gh-tool-callout-symbolic",
        _("Callout"),
        "K",
        "text",
        _("Drag from what you're pointing at to where the bubble goes, then type. Esc when done."),
    ),
    (
        "step",
        "gh-tool-step-symbolic",
        _("Steps"),
        "N",
        "text",
        _("Click to place numbered markers: 1, 2, 3…"),
    ),
    (
        "sticker",
        "gh-tool-sticker-symbolic",
        _("Stickers"),
        "G",
        "text",
        _("Pick an emoji or stamp, then click to place it (drag to size it)."),
    ),
    (
        "redact",
        "gh-tool-redact-symbolic",
        _("Blur"),
        "B",
        "hide",
        _(
            "Drag over anything private to pixelate, blur or black it out. "
            "Auto-Redact finds emails, numbers and keys for you."
        ),
    ),
    (
        "spotlight",
        "gh-tool-spotlight-symbolic",
        _("Spotlight"),
        "S",
        "hide",
        _("Drag around what matters. Everything else is dimmed."),
    ),
    (
        "magnify",
        "gh-tool-magnify-symbolic",
        _("Magnify"),
        "M",
        "hide",
        _("Drag over a small detail to show it zoomed in nearby."),
    ),
    (
        "eraser",
        "gh-tool-eraser-symbolic",
        _("Eraser"),
        "E",
        "hide",
        _("Rub out your drawing. The screenshot underneath is never erased."),
    ),
]
SECTIONS = [
    ("basics", _("BASICS")),
    ("draw", _("DRAW")),
    ("text", _("TEXT & MARKERS")),
    ("hide", _("HIDE & FOCUS")),
]
TOOL_INFO = {t[0]: t for t in TOOLS}

# Which option groups each tool (or selected item type) shows.
OPTIONS = {
    "select": set(),
    "arrow": {"color", "width"},
    "line": {"color", "width"},
    "rect": {"color", "width", "fill"},
    "ellipse": {"color", "width", "fill"},
    "pen": {"color", "width"},
    "highlighter": {"color", "width"},
    "text": {"color", "font"},
    "callout": {"color", "font"},
    "step": {"color", "font"},
    "redact": {"redact"},
    "spotlight": {"spot"},
    "magnify": {"color", "width", "zoom"},
    "sticker": {"color", "sticker"},
    "eraser": {"eraser"},
    "crop": {"crop"},
}

FILLS = [("none", _("Outline")), ("translucent", _("Tinted")), ("solid", _("Filled"))]
REDACT_MODES = [("pixelate", _("Pixelate")), ("blur", _("Blur")), ("solid", _("Black out"))]
SPOT_SHAPES = [("rect", _("Rectangle")), ("ellipse", _("Ellipse"))]
ZOOMS = [(1.5, "1.5×"), (2.0, "2×"), (3.0, "3×"), (4.0, "4×")]


def _labeled(label: str, widget: Gtk.Widget) -> Gtk.Box:
    box = Gtk.Box(spacing=6)
    text = Gtk.Label(label=label)
    text.add_css_class("dim-label")
    box.append(text)
    box.append(widget)
    return box


def _dropdown(choices: list[tuple]) -> Gtk.DropDown:
    return Gtk.DropDown.new_from_strings([label for _value, label in choices])


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app: GrabbyHandsApp, entry: Entry) -> None:
        self.app = app
        self.entry = entry
        self.settings = app.settings
        self.original = Image.open(app.history.original_path(entry.id)).convert("RGB")
        self.doc_path = app.history.document_path(entry.id)
        doc = Document()
        if self.doc_path.exists():
            try:
                doc = Document.load(self.doc_path)
            except (OSError, ValueError) as err:
                log.warning("could not read annotations: %s", type(err).__name__)
        # What the saved picture shows, and what document.json holds. A file that couldn't
        # be read counts as "holds this": it is only replaced once something is drawn.
        self._saved_json = self._doc_json(doc)
        self._written_json = self._saved_json
        if not doc.empty and not entry.edited:
            # Markup the saved picture never got: the app was killed (or the computer
            # switched off) with the editor open. Closing this time writes the picture.
            self._saved_json = self._doc_json(Document())
        self._save_source: int | None = None
        self._loading = False
        self._finished = False

        display = Gdk.Display.get_default()
        monitor = display.get_monitors().get_item(0) if display else None
        geo = monitor.get_geometry() if monitor else None
        max_w = int(geo.width * 0.9) if geo else 1400
        max_h = int(geo.height * 0.88) if geo else 900
        width = max(760, min(max_w, self.original.width + 140))
        height = max(520, min(max_h, self.original.height + 190))
        super().__init__(
            application=app, title=_("Edit"), default_width=width, default_height=height
        )

        self.style_ = Style(
            color=self.settings.get_string("editor-color"),
            width=self.settings.get_double("editor-stroke"),
            font_size=self.settings.get_double("editor-font-size"),
            # how to hide things is a habit (and a privacy choice): remember it
            redact_mode=self.settings.get_string("editor-redact-mode"),
            strength=self.settings.get_double("editor-redact-strength"),
        )
        self.canvas = Canvas(self.original, doc, self.style_)
        self.canvas.on_change = self._on_doc_changed
        self.canvas.on_selection = self._on_selection
        self.canvas.on_tool_request = self.set_tool
        self.canvas.on_tool_state = self._sync_options

        self.toasts = Adw.ToastOverlay()
        self._build_ui()
        self._install_actions()
        self.connect("close-request", self._on_close)
        self.set_tool("arrow")
        self._sync_undo()
        # Keys belong to the picture from the start (tool letters, Space to pan, Enter to
        # crop), not to whichever button happens to come first in the window.
        self.set_focus(self.canvas.area)

    # -- persistence ------------------------------------------------------------------
    @staticmethod
    def _doc_json(doc: Document) -> str:
        return json.dumps(doc.to_dict(), sort_keys=True)

    @property
    def doc(self) -> Document:
        return self.canvas.doc

    def render(self) -> Image.Image:
        self.canvas.end_text_edit()
        return render_document(self.doc, self.original, self.canvas.sources)

    def _write_document(self) -> None:
        current = self._doc_json(self.doc)
        if current == self._written_json:
            return  # nothing new (opening and closing never touches the file)
        if self.app.history.get(self.entry.id) is None:
            return  # deleted from History meanwhile: don't bring its folder back
        try:
            if self.doc.empty:
                self.doc_path.unlink(missing_ok=True)
            else:
                self.doc.save(self.doc_path)
            self._written_json = current
        except OSError as err:
            log.warning("could not save annotations: %s", err.strerror)

    def _schedule_document_save(self) -> None:
        if self._save_source is not None:
            GLib.source_remove(self._save_source)

        def save() -> bool:
            self._save_source = None
            self._write_document()
            return GLib.SOURCE_REMOVE

        self._save_source = GLib.timeout_add(800, save)

    def save_to_file(self, image: Image.Image | None = None) -> Path | None:
        """Write the marked-up image over the capture's file (or a new file)."""
        image = image or self.render()
        entry = self.entry
        quality = self.settings.get_int("export-quality")
        try:
            if entry.saved_path:
                path = export.write_image(image, Path(entry.saved_path), quality)
            else:
                path = export.save_image(
                    image,
                    config.save_folder(),
                    self.settings.get_string("filename-pattern"),
                    self.settings.get_string("export-format"),
                    quality,
                )
                entry.saved_path = str(path)
        except OSError as err:
            self.app.notify(_("Couldn't save the capture"), str(err), tag="error")
            # History still shows the capture as it is now (its markup was kept).
            self._update_history(image)
            return None
        self._update_history(image)
        self._saved_json = self._doc_json(self.doc)
        return path

    def _update_history(self, image: Image.Image) -> None:
        if self.app.history.get(self.entry.id) is None:
            return  # deleted from History while it was open here
        try:
            self.app.history.write_thumbnail(self.entry.id, image)
        except OSError as err:
            log.warning("could not update a thumbnail: %s", err.strerror)
        self.entry.edited = not self.doc.empty
        try:
            self.app.history.update(self.entry)
        except OSError as err:
            log.warning("could not update the history index: %s", err.strerror)

    def _on_close(self, _win: Gtk.Window) -> bool:
        self.finish()
        return False

    def finish(self) -> None:
        """Save everything: the markup, the picture, the tool settings.

        Runs when the window closes, and from the application when it quits with this
        window still open (quitting closes no windows, so nothing would be saved). Only
        the first call does anything.
        """
        if self._finished:
            return
        self._finished = True
        self.canvas.end_text_edit()
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
            self._save_source = None
        self.settings.set_string("editor-color", self.style_.color)
        self.settings.set_double("editor-stroke", self.style_.width)
        self.settings.set_double("editor-font-size", self.style_.font_size)
        self.settings.set_string("editor-redact-mode", self.style_.redact_mode)
        self.settings.set_double("editor-redact-strength", self.style_.strength)
        if self.app.history.get(self.entry.id) is None:
            return  # deleted from History while it was open: nothing is brought back
        self._write_document()
        if self._doc_json(self.doc) != self._saved_json:
            image = self.render()
            if self.entry.saved_path or self.settings.get_boolean("auto-save"):
                self.save_to_file(image)
            else:
                self._update_history(image)

    # -- UI -----------------------------------------------------------------------------
    def _build_ui(self) -> None:
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(
            title=_("Edit"), subtitle=f"{self.original.width} × {self.original.height}"
        )
        title_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        app_icon = Gtk.Image.new_from_icon_name(APP_ID)
        app_icon.set_pixel_size(22)
        title_box.append(app_icon)
        title_box.append(self.title)
        header.set_title_widget(title_box)

        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.undo_btn.set_tooltip_markup(
            tooltip(_("Undo"), _("Take back the last change"), "Ctrl+Z")
        )
        self.undo_btn.set_action_name("win.undo")
        self.redo_btn = Gtk.Button(icon_name="edit-redo-symbolic")
        self.redo_btn.set_tooltip_markup(tooltip(_("Redo"), _("Bring it back"), "Ctrl+Shift+Z"))
        self.redo_btn.set_action_name("win.redo")
        header.pack_start(self.undo_btn)
        header.pack_start(self.redo_btn)

        copy = Gtk.Button(child=Adw.ButtonContent(icon_name="gh-copy-symbolic", label=_("Copy")))
        copy.set_tooltip_markup(tooltip(_("Copy"), _("Copy the finished image"), "Ctrl+C"))
        copy.add_css_class("suggested-action")
        copy.set_action_name("win.copy")
        save = Gtk.Button(child=Adw.ButtonContent(icon_name="gh-save-symbolic", label=_("Save")))
        save.set_tooltip_markup(
            tooltip(
                _("Save"),
                _("Save over the capture's file now. Closing the editor saves your changes too."),
                "Ctrl+S",
            )
        )
        save.set_action_name("win.save")

        menu = Gio.Menu()
        menu.append(_("Save As…"), "win.save-as")
        menu.append(_("Pin to Screen"), "win.pin")
        menu.append(_("Copy Text…"), "win.ocr")
        menu.append(_("Auto-Redact"), "win.auto-redact")
        menu.append(_("Show in Folder"), "win.show-folder")
        help_section = Gio.Menu()
        help_section.append(_("Keyboard Shortcuts"), "app.keyboard-shortcuts")
        menu.append_section(None, help_section)
        danger = Gio.Menu()
        danger.append(_("Erase All Drawing"), "win.erase-all")
        menu.append_section(None, danger)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu)
        more.set_tooltip_text(_("More actions"))

        settings = settings_button()
        settings.set_action_name("app.preferences")

        header.pack_end(more)
        header.pack_end(settings)
        divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        divider.set_margin_top(8)
        divider.set_margin_bottom(8)
        divider.set_margin_start(4)
        divider.set_margin_end(4)
        header.pack_end(divider)
        header.pack_end(copy)
        header.pack_end(save)

        self.options = self._build_options()
        rail = self._build_rail()

        body = Gtk.Box()
        body.append(rail)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(self.canvas)
        self.toasts.set_child(body)

        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        view.add_top_bar(self.options)
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        keys.connect("key-released", self._on_key_released)
        self.add_controller(keys)
        # A number box that was just clicked keeps the keyboard and swallows letters.
        # Catch tool letters before it does, so "T" still means Text after changing a size.
        early = Gtk.EventControllerKey()
        early.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        early.connect("key-pressed", self._on_key_in_number_box)
        self.add_controller(early)

    def _build_rail(self) -> Gtk.Widget:
        # One card per section; inside, a strict two-column grid of identical tiles.
        # No scroll area: the window can't get shorter than the sidebar, so nothing covers it.
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.START)
        column.add_css_class("gh-sidebar")
        self.tool_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for section, title in SECTIONS:
            tiles = []
            for tool_id, icon, label, key, tool_section, how in TOOLS:
                if tool_section != section:
                    continue
                btn = tile_button(icon, label, tooltip(label, how, key), toggle=True)
                btn.add_css_class("gh-tool")
                if group is not None:
                    btn.set_group(group)
                group = group or btn
                btn.connect("toggled", self._on_tool_toggled, tool_id)
                self.tool_buttons[tool_id] = btn
                tiles.append(btn)
            card = section_card(title, tiles, columns=2)
            if section == "hide":
                card.append(self._auto_redact_tile())
            column.append(card)
        self.rail_grid = column
        return column

    def _auto_redact_tile(self) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("gh-feature")
        button.set_focus_on_click(False)
        button.set_action_name("win.auto-redact")
        button.set_tooltip_markup(
            tooltip(
                _("Auto-Redact"),
                _(
                    "Finds emails, phone numbers, card numbers, ID numbers, IP addresses "
                    "and keys in the screenshot and hides them. Review before sharing."
                ),
                "Ctrl+Shift+R",
            )
        )
        box = Gtk.Box(spacing=8)
        icon = Gtk.Image.new_from_icon_name("gh-redact-symbolic")
        icon.set_pixel_size(20)
        box.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label=_("Auto-Redact"), xalign=0)
        title.add_css_class("gh-feature-title")
        sub = Gtk.Label(label=_("Hide emails, cards…"), xalign=0)
        sub.add_css_class("gh-feature-sub")
        text.append(title)
        text.append(sub)
        box.append(text)
        button.set_child(box)
        self.auto_redact_btn = button
        return button

    def _build_options(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=14)
        bar.add_css_class("toolbar")
        bar.add_css_class("gh-options")
        self.groups: dict[str, Gtk.Widget] = {}

        self.tool_hint = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.tool_hint.add_css_class("gh-tool-hint")
        self.tool_hint_icon = Gtk.Image()
        self.tool_hint_icon.set_pixel_size(16)
        self.tool_hint_name = Gtk.Label()
        self.tool_hint_name.add_css_class("gh-tool-name")
        self.tool_hint_how = Gtk.Label(ellipsize=3)  # Pango.EllipsizeMode.END
        self.tool_hint_how.add_css_class("gh-tool-how")
        self.tool_hint.append(self.tool_hint_icon)
        self.tool_hint.append(self.tool_hint_name)
        self.tool_hint.append(self.tool_hint_how)
        bar.append(self.tool_hint)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # colour
        colors = Gtk.Box(spacing=4)
        self.swatches: dict[str, Gtk.ToggleButton] = {}
        group = None
        for i, value in enumerate(SWATCHES):
            btn = Gtk.ToggleButton(tooltip_text=SWATCH_NAMES[i])
            btn.add_css_class("gh-swatch")
            btn.add_css_class(f"gh-swatch-{i}")
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_focusable(False)
            if group is not None:
                btn.set_group(group)
            group = group or btn
            btn.connect("toggled", lambda b, v=value: b.get_active() and self._set_color(v))
            self.swatches[value] = btn
            colors.append(btn)
        self.color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
        self.color_button.set_tooltip_text(_("Choose any color"))
        self.color_button.connect("notify::rgba", self._on_custom_color)
        colors.append(self.color_button)
        self.groups["color"] = colors

        self.width_spin = Gtk.SpinButton.new_with_range(1, 64, 1)
        self.width_spin.connect("value-changed", lambda s: self._set_option("width", s.get_value()))
        self.groups["width"] = _labeled(_("Size"), self.width_spin)

        self.fill_drop = _dropdown(FILLS)
        self.fill_drop.connect(
            "notify::selected",
            lambda d, _p: self._set_option("fill", FILLS[d.get_selected()][0]),
        )
        self.groups["fill"] = _labeled(_("Fill"), self.fill_drop)

        self.font_spin = Gtk.SpinButton.new_with_range(8, 160, 1)
        self.font_spin.connect("value-changed", lambda s: self._set_option("font", s.get_value()))
        self.groups["font"] = _labeled(_("Text size"), self.font_spin)

        redact = Gtk.Box(spacing=10)
        self.redact_drop = _dropdown(REDACT_MODES)
        self.redact_drop.connect(
            "notify::selected",
            lambda d, _p: self._set_option("redact_mode", REDACT_MODES[d.get_selected()][0]),
        )
        self.strength = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
        self.strength.set_size_request(180, -1)
        self.strength.set_draw_value(True)
        self.strength.set_value_pos(Gtk.PositionType.RIGHT)
        self.strength.set_format_value_func(lambda _s, v: f"{v:.0f}%")
        self.strength.connect(
            "value-changed", lambda s: self._set_option("strength", s.get_value())
        )
        redact.append(self.redact_drop)
        redact.append(_labeled(_("Strength"), self.strength))
        # Same feature, same name as the Auto-Redact card in the sidebar.
        find = Gtk.Button(label=_("Auto-Redact"))
        find.set_tooltip_text(
            _("Find and hide emails, phone numbers, card numbers, IP addresses and keys for you")
        )
        find.set_action_name("win.auto-redact")
        redact.append(find)
        self.auto_redact_option = find
        hint = Gtk.Label(
            label=_("Low strength can leave text readable. Use Black out for secrets.")
        )
        hint.add_css_class("gh-tool-tip")
        redact.append(hint)
        self.groups["redact"] = redact

        spot = Gtk.Box(spacing=10)
        self.spot_drop = _dropdown(SPOT_SHAPES)
        self.spot_drop.connect(
            "notify::selected",
            lambda d, _p: self._set_option("spot_shape", SPOT_SHAPES[d.get_selected()][0]),
        )
        self.dim_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.2, 0.9, 0.05)
        self.dim_scale.set_size_request(140, -1)
        self.dim_scale.connect("value-changed", lambda s: self._set_option("dim", s.get_value()))
        spot.append(self.spot_drop)
        spot.append(_labeled(_("Dim"), self.dim_scale))
        self.groups["spot"] = spot

        self.zoom_drop = _dropdown(ZOOMS)
        self.zoom_drop.connect(
            "notify::selected", lambda d, _p: self._set_option("zoom", ZOOMS[d.get_selected()][0])
        )
        self.groups["zoom"] = _labeled(_("Zoom"), self.zoom_drop)

        sticker = Gtk.Box(spacing=4)
        sticker.add_css_class("gh-stamps")
        emoji = Gtk.MenuButton(label=EMOJI_PLACEHOLDER, tooltip_text=_("Pick any emoji"))
        chooser = Gtk.EmojiChooser()
        chooser.connect("emoji-picked", lambda _c, text: self._set_sticker(text))
        emoji.set_popover(chooser)
        sticker.append(emoji)
        self.emoji_button = emoji
        self.stamp_buttons: dict[str, Gtk.ToggleButton] = {}
        group = None
        for stamp_text in STAMPS:
            btn = Gtk.ToggleButton(label=stamp_text)
            btn.set_focusable(False)
            if group is not None:
                btn.set_group(group)
            group = group or btn
            btn.connect("toggled", lambda b, t=stamp_text: b.get_active() and self._set_sticker(t))
            self.stamp_buttons[stamp_text] = btn
            sticker.append(btn)
        self.groups["sticker"] = sticker

        eraser = Gtk.Box(spacing=10)
        self.eraser_spin = Gtk.SpinButton.new_with_range(4, 200, 2)
        self.eraser_spin.connect(
            "value-changed", lambda s: self._set_option("eraser_width", s.get_value())
        )
        erase_all = Gtk.Button(label=_("Erase All"), tooltip_text=_("Remove all your drawing"))
        erase_all.add_css_class("destructive-action")
        erase_all.set_action_name("win.erase-all")
        eraser.append(_labeled(_("Eraser size"), self.eraser_spin))
        eraser.append(erase_all)
        self.groups["eraser"] = eraser

        crop = Gtk.Box(spacing=6)
        apply_btn = Gtk.Button(label=_("Apply Crop"))
        apply_btn.add_css_class("gh-tinted")  # the header's Copy stays the one primary button
        apply_btn.connect("clicked", lambda *_: self.canvas.tools["crop"].apply())
        reset_btn = Gtk.Button(label=_("Reset"))
        reset_btn.connect("clicked", lambda *_: self.canvas.tools["crop"].reset())
        crop.append(apply_btn)
        crop.append(reset_btn)
        self.groups["crop"] = crop

        for widget in self.groups.values():
            widget.set_valign(Gtk.Align.CENTER)  # natural height, not stretched to the bar
            bar.append(widget)

        # How to use the tool, in whatever room is left after its options. (Tools without
        # options show it inside the pill on the left instead.)
        self.tool_tip = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)  # Pango END
        self.tool_tip.add_css_class("gh-tool-tip")
        bar.append(self.tool_tip)
        return bar

    # -- tools & options ----------------------------------------------------------------
    def set_tool(self, tool_id: str) -> None:
        btn = self.tool_buttons[tool_id]
        if not btn.get_active():
            btn.set_active(True)  # emits toggled → _on_tool_toggled
        else:
            self._on_tool_toggled(btn, tool_id)

    def _on_tool_toggled(self, btn: Gtk.ToggleButton, tool_id: str) -> None:
        if not btn.get_active():
            return
        self.canvas.set_tool(tool_id)
        self._sync_options()

    def _active_kind(self) -> str:
        tool = self.canvas.tool.name
        if tool == "select":
            item = self.canvas.selected_item()
            return item.type if item is not None and item.type in OPTIONS else "select"
        return tool

    def _sync_options(self) -> None:
        kind = self._active_kind()
        shown = OPTIONS.get(kind, set())
        for name, widget in self.groups.items():
            widget.set_visible(name in shown)
        tool_id = self.canvas.tool.name
        _tid, icon, label, _key, _section, how = TOOL_INFO[tool_id]
        self.tool_hint_icon.set_from_icon_name(icon)
        self.tool_hint_name.set_label(label)
        self.tool_hint_how.set_label(how)
        # Instructions sit in the pill when the tool has (next to) no options, otherwise
        # after the options. Not for Blur (its options end with their own advice) or
        # Stickers (its options fill the bar; a few clipped words would only add noise).
        in_pill = not shown or tool_id in ("crop", "eraser")
        self.tool_hint_how.set_visible(in_pill)
        self.tool_tip.set_label(how)
        self.tool_tip.set_tooltip_text(how)
        self.tool_tip.set_visible(not in_pill and not shown & {"redact", "sticker"})
        self._loading = True
        s = self.style_
        item = self.canvas.selected_item()
        color = s.highlight_color if kind == "highlighter" else s.color
        width = s.highlight_width if kind == "highlighter" else s.width
        font, fill = s.font_size, s.fill
        if item is not None and self.canvas.tool.name in ("select", item.type):
            color = item.color
            width = item.width
            font = getattr(item, "size", font)
            fill = getattr(item, "fill", fill)
        for value, btn in self.swatches.items():
            btn.set_active(value.lower() == color.lower())
        rgba = Gdk.RGBA()
        if rgba.parse(color):
            self.color_button.set_rgba(rgba)
        self.width_spin.set_value(width)
        self.font_spin.set_value(font)
        self.fill_drop.set_selected([v for v, _label in FILLS].index(fill) if fill else 0)
        mode = getattr(item, "mode", s.redact_mode)
        self.redact_drop.set_selected([v for v, _label in REDACT_MODES].index(mode))
        self.strength.set_value(getattr(item, "strength", s.strength))
        shape = getattr(item, "shape", s.spot_shape)
        self.spot_drop.set_selected([v for v, _label in SPOT_SHAPES].index(shape))
        self.dim_scale.set_value(getattr(item, "dim", s.dim))
        zoom = getattr(item, "zoom", s.zoom)
        zooms = [v for v, _label in ZOOMS]
        self.zoom_drop.set_selected(zooms.index(zoom) if zoom in zooms else 1)
        self.eraser_spin.set_value(s.eraser_width)
        for text, btn in self.stamp_buttons.items():
            btn.set_active(text == s.sticker)
        # an emoji picked from the chooser shows on its button, so it's clear what's next
        self.emoji_button.set_label(s.sticker if s.sticker not in STAMPS else EMOJI_PLACEHOLDER)
        self._loading = False

    def _on_selection(self, item: Item | None) -> None:
        self._sync_options()

    def _set_color(self, value: str) -> None:
        self._set_option("color", value)

    def _on_custom_color(self, button: Gtk.ColorDialogButton, _pspec) -> None:
        if self._loading:
            return
        c = button.get_rgba()
        value = f"#{round(c.red * 255):02x}{round(c.green * 255):02x}{round(c.blue * 255):02x}"
        self._set_option("color", value)

    def _set_sticker(self, text: str) -> None:
        if self._loading:
            return
        self.style_.sticker = text
        item = self.canvas.selected_item()
        if item is not None and item.type == "sticker":
            self.canvas.begin_change()
            item.text = text
            self.canvas.changed()
        if self.canvas.tool.name not in ("sticker", "select"):
            self.set_tool("sticker")
        self._sync_options()

    def _set_option(self, name: str, value) -> None:
        if self._loading:
            return
        s = self.style_
        kind = self._active_kind()
        if name == "color":
            if kind == "highlighter":
                s.highlight_color = value
            else:
                s.color = value
        elif name == "width":
            if kind == "highlighter":
                s.highlight_width = value
            else:
                s.width = value
        elif name == "font":
            s.font_size = value
        elif name in (
            "fill",
            "redact_mode",
            "strength",
            "spot_shape",
            "dim",
            "zoom",
            "eraser_width",
        ):
            setattr(s, name, value)
        item = self.canvas.selected_item()
        if item is None:
            return
        attr = {
            "color": "color",
            "width": "width",
            "font": "size",
            "fill": "fill",
            "redact_mode": "mode",
            "strength": "strength",
            "spot_shape": "shape",
            "dim": "dim",
            "zoom": "zoom",
        }.get(name)
        if attr and hasattr(item, attr) and getattr(item, attr) != value:
            # (a slider being dragged is one undo step, not one per value it passes)
            self.canvas.begin_change(burst=("option", item.id, attr))
            setattr(item, attr, value)
            self.canvas.changed()

    # -- actions ------------------------------------------------------------------------
    def _install_actions(self) -> None:
        actions = {
            "undo": (self.canvas.do_undo, ["<Control>z"]),
            "redo": (self.canvas.do_redo, ["<Control><Shift>z", "<Control>y"]),
            "copy": (self.copy, ["<Control>c"]),
            "save": (self.save, ["<Control>s"]),
            "save-as": (self.save_as, ["<Control><Shift>s"]),
            "pin": (self.pin, []),
            "ocr": (self.copy_text, []),
            "show-folder": (self.show_folder, []),
            "erase-all": (self.erase_all, []),
            "auto-redact": (self.auto_redact, ["<Control><Shift>r"]),
            "zoom-fit": (lambda: self.canvas.fit(), ["<Control>0"]),
            "zoom-actual": (lambda: self.canvas.zoom_to(1.0), ["<Control>1"]),
            "zoom-in": (
                lambda: self.canvas.zoom_to(self.canvas.scale * 1.25),
                ["<Control>plus", "<Control>equal"],
            ),
            "zoom-out": (lambda: self.canvas.zoom_to(self.canvas.scale / 1.25), ["<Control>minus"]),
        }
        for name, (callback, accels) in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
            if accels:
                self.app.set_accels_for_action(f"win.{name}", accels)
        self.app.set_accels_for_action("window.close", ["<Control>w"])

    def _sync_undo(self) -> None:
        self.lookup_action("undo").set_enabled(self.canvas.undo.can_undo)
        self.lookup_action("redo").set_enabled(self.canvas.undo.can_redo)

    def _on_doc_changed(self) -> None:
        self._sync_undo()
        self._schedule_document_save()

    def toast(self, text: str, timeout: int = 2) -> None:
        # Plain text: toasts read their title as markup by default, and a file name or an
        # error message with "&" or "<" in it would then show up empty.
        self.toasts.add_toast(Adw.Toast(title=text, timeout=timeout, use_markup=False))

    def copy(self) -> None:
        if self.canvas.editing:
            # Ctrl+C is ours window-wide; while typing it has to copy the selected text.
            self.canvas.copy_edit_selection()
            return
        self.app.copy_image(self.render(), self)
        self.toast(_("Copied to the clipboard"))

    def save(self) -> None:
        path = self.save_to_file()
        if path is not None:
            self.toast(_("Saved {name}").format(name=path.name))

    def save_as(self) -> None:
        self.app.save_as(self.render(), self.entry, self)

    def pin(self) -> None:
        self.app.pin(self.render(), self.entry)

    def show_folder(self) -> None:
        if not self.app._show_capture_by_id(self.entry.id):
            self.toast(_("This capture has no file yet. Save it first (Ctrl+S)."), timeout=4)

    def erase_all(self) -> None:
        if not self.doc.items:
            self.toast(_("Nothing to erase"))
            return
        self.canvas.erase_all()
        self.toast(_("Erased all drawing. Ctrl+Z brings it back."))

    def auto_redact(self) -> None:
        from grabbyhands import ocr, redact

        if not ocr.available():
            self.toast(
                _("Auto-Redact needs Tesseract, which isn't installed. See Settings → Text.")
            )
            return
        action = self.lookup_action("auto-redact")
        if not action.get_enabled():
            return
        action.set_enabled(False)
        self.toast(_("Looking for personal information…"))
        image = self.original
        language = self.settings.get_string("ocr-language")

        def work() -> None:
            try:
                found = redact.find_sensitive(ocr.read_words(image, language))
            except Exception as err:  # noqa: BLE001
                log.warning("auto-redact failed: %s", err)
                found = None
            GLib.idle_add(done, found)

        def done(found) -> bool:
            action.set_enabled(True)
            if self._finished:
                return GLib.SOURCE_REMOVE  # closed meanwhile: add nothing the user never saw
            if found is None:
                self.toast(_("Auto-Redact didn't work. Details are in the system log."))
                return GLib.SOURCE_REMOVE
            existing = {
                (round(i.x0), round(i.y0), round(i.x1), round(i.y1))
                for i in self.doc.items
                if isinstance(i, RedactItem)
            }
            new = [f for f in found if f.box not in existing]
            if not new:
                self.toast(
                    _(
                        "Found nothing to hide automatically. "
                        "Use the Blur tool to cover anything else."
                    ),
                    timeout=5,
                )
                return GLib.SOURCE_REMOVE
            s = self.style_
            self.canvas.begin_change()
            for f in new:
                x0, y0, x1, y1 = f.box
                self.doc.items.append(
                    RedactItem(x0=x0, y0=y0, x1=x1, y1=y1, mode=s.redact_mode, strength=s.strength)
                )
            self.canvas.select(None)
            self.canvas.changed()
            self.set_tool("select")
            self.toasts.add_toast(
                Adw.Toast(
                    title=_("Hid {what}. Check nothing was missed before sharing.").format(
                        what=redact.summary(new)
                    ),
                    timeout=6,
                )
            )
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def copy_text(self) -> None:
        from grabbyhands import ocr

        if not ocr.available():
            self.toast(_("Copy Text needs Tesseract, which isn't installed. See Settings → Text."))
            return
        image = self.render()
        language = self.settings.get_string("ocr-language")
        self.toast(_("Reading text…"))

        def work() -> None:
            try:
                words, error = ocr.read_words(image, language), None
            except Exception as err:  # noqa: BLE001
                log.warning("OCR failed: %s", err)
                words, error = [], str(err)
            GLib.idle_add(done, words, error)

        def done(words: list, error: str | None) -> bool:
            if self._finished:
                return GLib.SOURCE_REMOVE
            if words:
                self.app.show_text_picker(image, words)
            elif error:
                self.toast(ocr.friendly_error(error), timeout=5)
            else:
                self.toast(_("No text found in this picture"))
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    # -- keys -----------------------------------------------------------------------------
    def _on_key(self, _ctrl, keyval: int, code: int, state: Gdk.ModifierType) -> bool:
        if self.canvas.editing:
            return False
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)
        if keyval == Gdk.KEY_space and not mods:
            self.canvas.space_pan = True
            return True
        if self.canvas.tool.key(keyval, state):
            return True
        if keyval == Gdk.KEY_Escape:
            if self.canvas.selected_item() is not None:
                self.canvas.select(None)
            elif self.canvas.tool.name != "select":
                self.set_tool("select")
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and self.canvas.selected_item():
            self.canvas.delete_selected()
            return True
        if mods:
            return False
        # (the letter the key has on a Latin layout, so tools work on any keyboard)
        char = chr(Gdk.keyval_to_unicode(keys.latin_keyval(keyval, code)) or 0).upper()
        for tool_id, _icon, _label, key, _section, _how in TOOLS:
            if char == key:
                self.set_tool(tool_id)
                return True
        return False

    def _on_key_in_number_box(self, _ctrl, keyval: int, code: int, state) -> bool:
        focus = self.get_focus()
        if focus is None or focus.get_ancestor(Gtk.SpinButton) is None:
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if not chr(Gdk.keyval_to_unicode(keyval) or 0).isalpha():
            return False  # digits, arrows, Enter…: the number box needs those
        self.canvas.area.grab_focus()
        return self._on_key(None, keyval, code, state)

    def _on_key_released(self, _ctrl, keyval: int, _code: int, _state) -> None:
        if keyval == Gdk.KEY_space:
            self.canvas.space_pan = False
            self.canvas.set_cursor_name(self.canvas.tool.cursor)
