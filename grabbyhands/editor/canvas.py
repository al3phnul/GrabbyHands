# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The editor canvas: shows the screenshot and its paint layer, routes input to tools."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import cairo
from gi.repository import Gdk, GLib, Gtk
from PIL import Image

from grabbyhands import chrome, theme
from grabbyhands.editor.document import CalloutItem, Document, Item, TextItem, UndoStack
from grabbyhands.editor.render import EffectSources, render_layer
from grabbyhands.editor.tools import freehand, select, shapes, stamp, text
from grabbyhands.editor.tools.base import Style, Tool

HANDLE_RADIUS = 6
DOUBLE_CLICK_MS = 400
MAX_ZOOM = 16.0
BURST_SECONDS = 1.0  # changes of one kind closer together than this are one undo step


def make_tools(canvas: Canvas) -> dict[str, Tool]:
    classes = [
        select.SelectTool,
        shapes.ArrowTool,
        shapes.LineTool,
        shapes.RectTool,
        shapes.EllipseTool,
        freehand.PenTool,
        freehand.HighlighterTool,
        text.TextTool,
        shapes.CalloutTool,
        stamp.StepTool,
        shapes.RedactTool,
        shapes.SpotlightTool,
        shapes.MagnifyTool,
        stamp.StickerTool,
        freehand.EraserTool,
        shapes.CropTool,
    ]
    return {cls.name: cls(canvas) for cls in classes}


class Canvas(Gtk.Overlay):
    def __init__(self, original: Image.Image, doc: Document, style: Style) -> None:
        super().__init__()
        self.original = original
        self.doc = doc
        self.undo = UndoStack(doc)
        self.style = style
        self.sources = EffectSources(original)
        self.image_size = (self.sources.width, self.sources.height)

        # callbacks the window sets
        self.on_change: Callable[[], None] = lambda: None
        self.on_selection: Callable[[Item | None], None] = lambda item: None
        self.on_tool_request: Callable[[str], None] = lambda name: None
        self.on_tool_state: Callable[[], None] = lambda: None

        self.tools = make_tools(self)
        self.tool: Tool = self.tools["select"]
        self.selected_id: str | None = None
        self.preview: Item | None = None
        self.hidden_id: str | None = None
        self._layer: cairo.ImageSurface | None = None

        self.scale = 1.0
        self.ox = self.oy = 0.0
        self.fit_mode = True
        self.space_pan = False
        self._pan_start: tuple[float, float, float, float] | None = None
        self._drag_origin = (0.0, 0.0)
        self._last_press = (0.0, 0.0, 0.0)  # time, x, y
        self._pointer: tuple[float, float] | None = None
        self._edit: tuple[Item, bool, Gtk.TextView] | None = None
        self._gesture_tool: Tool | None = None  # the tool that got the current drag's press
        self._burst: tuple[tuple, int, float] | None = None  # see begin_change

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.area.add_css_class("gh-canvas-bg")
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        self._setup_input()

    # -- coordinates --------------------------------------------------------------------
    def to_image(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.ox) / self.scale, (sy - self.oy) / self.scale

    def to_screen(self, ix: float, iy: float) -> tuple[float, float]:
        return self.ox + ix * self.scale, self.oy + iy * self.scale

    def image_tolerance(self, screen_px: float = 6) -> float:
        return screen_px / max(self.scale, 0.01)

    def _content_bounds(self) -> tuple[float, float, float, float]:
        iw, ih = self.image_size
        return 0.0, 0.0, float(iw), float(ih)

    def fit(self, width: int | None = None, height: int | None = None) -> None:
        width = width or self.area.get_width() or 800
        height = height or self.area.get_height() or 600
        x0, y0, x1, y1 = self._content_bounds()
        cw, ch = x1 - x0, y1 - y0
        margin = 32
        self.scale = max(0.02, min(1.0, (width - margin) / cw, (height - margin) / ch))
        self.ox = (width - cw * self.scale) / 2 - x0 * self.scale
        self.oy = (height - ch * self.scale) / 2 - y0 * self.scale
        self.fit_mode = True
        self.area.queue_draw()

    def zoom_to(self, scale: float, around: tuple[float, float] | None = None) -> None:
        scale = max(0.02, min(MAX_ZOOM, scale))
        w, h = self.area.get_width() or 800, self.area.get_height() or 600
        sx, sy = around or (w / 2, h / 2)
        ix, iy = self.to_image(sx, sy)
        self.scale = scale
        self.ox, self.oy = sx - ix * scale, sy - iy * scale
        self.fit_mode = False
        self._reposition_editor()
        self.area.queue_draw()

    # -- state used by tools ------------------------------------------------------------
    def queue_draw(self) -> None:  # noqa: D401 - Gtk.Widget API
        self.area.queue_draw()

    def set_cursor_name(self, name: str) -> None:
        self.area.set_cursor_from_name(name)

    def selected_item(self) -> Item | None:
        return self.doc.get(self.selected_id)

    def select(self, item: Item | None) -> None:
        new_id = item.id if item is not None else None
        if new_id != self.selected_id:
            self.selected_id = new_id
            self.on_selection(item)
        self.area.queue_draw()

    def handle_at(self, item: Item | None, x: float, y: float) -> str | None:
        if item is None:
            return None
        tol = self.image_tolerance(HANDLE_RADIUS + 3)
        for name, (hx, hy) in item.handles().items():
            if abs(hx - x) <= tol and abs(hy - y) <= tol:
                return name
        return None

    def set_preview(self, item: Item | None) -> None:
        self.preview = item
        self.area.queue_draw()

    def invalidate(self) -> None:
        self._layer = None
        self.area.queue_draw()

    def begin_change(self, burst: tuple | None = None) -> None:
        """Remember the document as it is, for Undo, before changing it.

        ``burst`` names a change that arrives many times in a row (a slider being dragged,
        an arrow key held down). A run of the same change is one undo step, not hundreds:
        dragging a slider twice used to push everything else out of the undo history.
        """
        now = time.monotonic()
        if burst is not None and self._burst is not None:
            key, serial, when = self._burst
            if key == burst and serial == self.undo.serial and now - when < BURST_SECONDS:
                self._burst = (key, serial, now)
                return
        self.undo.checkpoint()
        self._burst = (burst, self.undo.serial, now) if burst is not None else None

    def changed(self, live: bool = False) -> None:
        self.invalidate()
        if self.fit_mode:
            self.fit()
        if not live:
            self.on_change()

    def add_item(self, item: Item, select: bool = True) -> None:
        self.begin_change()
        self.doc.items.append(item)
        self.changed()
        self.select(item if select and item.selectable else None)

    def delete_selected(self) -> None:
        item = self.selected_item()
        if item is None:
            return
        self.begin_change()
        self.doc.items.remove(item)
        self.select(None)
        self.changed()

    def erase_all(self) -> None:
        if not self.doc.items:
            return
        self.begin_change()
        self.doc.items.clear()
        self.select(None)
        self.changed()

    def do_undo(self) -> None:
        if self._edit_history("undo"):
            return
        self.end_text_edit()
        if self.undo.undo():
            self.select(None)
            self.changed()

    def do_redo(self) -> None:
        if self._edit_history("redo"):
            return
        self.end_text_edit()
        if self.undo.redo():
            self.select(None)
            self.changed()

    def _edit_history(self, direction: str) -> bool:
        """While text is being typed, Undo and Redo belong to the typing."""
        if self._edit is None:
            return False
        buf = self._edit[2].get_buffer()
        if direction == "undo" and buf.get_can_undo():
            buf.undo()
            return True
        if direction == "redo" and buf.get_can_redo():
            buf.redo()
            return True
        return False

    def copy_edit_selection(self) -> None:
        """Ctrl+C while typing copies the selected text, as in any text field."""
        if self._edit is not None:
            self._edit[2].emit("copy-clipboard")

    def request_tool(self, name: str) -> None:
        self.on_tool_request(name)

    def notify_tool_state(self) -> None:
        self.on_tool_state()

    def set_tool(self, name: str) -> None:
        self.end_text_edit()
        if self.tool.name == name:
            return
        self._finish_gesture()
        self.tool.deactivate()
        self.tool = self.tools[name]
        self.tool.activate()
        self.set_cursor_name(self.tool.cursor)
        if name == "eraser":
            self.select(None)
        self.area.queue_draw()

    # -- layers ---------------------------------------------------------------------------
    def layer(self) -> cairo.ImageSurface:
        if self.preview is not None or self.hidden_id is not None:
            return render_layer(self.doc, self.sources, self.preview, self.hidden_id)
        if self._layer is None:
            self._layer = render_layer(self.doc, self.sources)
        return self._layer

    # -- drawing --------------------------------------------------------------------------
    def _draw(self, _area, ctx: cairo.Context, width: int, height: int) -> None:
        if self.fit_mode:
            self.fit(width, height)
        iw, ih = self.image_size
        crop_tool = self.tool.name == "crop"

        # The picture lies on the canvas like a sheet of paper: a soft shadow and a
        # hairline edge, so a white screenshot doesn't melt into a light background.
        sx0, sy0 = (round(v) for v in self.to_screen(0, 0))
        sx1, sy1 = (round(v) for v in self.to_screen(iw, ih))
        chrome.soft_shadow(ctx, sx0, sy0, sx1 - sx0, sy1 - sy0, 0, depth=12, strength=0.2)
        ctx.set_line_width(1)
        ctx.set_source_rgba(0, 0, 0, 0.16)
        ctx.rectangle(sx0 - 0.5, sy0 - 0.5, sx1 - sx0 + 1, sy1 - sy0 + 1)
        ctx.stroke()

        ctx.save()
        ctx.translate(self.ox, self.oy)
        ctx.scale(self.scale, self.scale)
        self._paint_image(ctx)
        if self.doc.crop and not crop_tool:
            cx, cy, cw, ch = self.doc.crop
            ctx.set_source_rgba(0, 0, 0, 0.55)
            ctx.rectangle(0, 0, iw, ih)
            ctx.rectangle(cx, cy, cw, ch)
            ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            ctx.fill()
        ctx.restore()

        self._draw_selection(ctx)
        self.tool.draw_overlay(ctx)

    def _paint_image(self, ctx: cairo.Context) -> None:
        nearest = self.scale >= 2
        ctx.set_source_surface(self.sources.surface, 0, 0)
        if nearest:
            ctx.get_source().set_filter(cairo.FILTER_NEAREST)
        ctx.paint()
        ctx.set_source_surface(self.layer(), 0, 0)
        if nearest:
            ctx.get_source().set_filter(cairo.FILTER_NEAREST)
        ctx.paint()

    def _draw_selection(self, ctx: cairo.Context) -> None:
        item = self.selected_item()
        if item is None or (self._edit and self._edit[0] is item):
            return
        x0, y0, x1, y1 = item.bounds()
        sx0, sy0 = self.to_screen(x0, y0)
        sx1, sy1 = self.to_screen(x1, y1)
        accent = theme.current().rgb()
        # A dashed hairline in the scheme colour over a white one: shows on any picture.
        box = (round(sx0) - 3.5, round(sy0) - 3.5, round(sx1 - sx0) + 7, round(sy1 - sy0) + 7)
        ctx.set_line_width(1)
        ctx.set_source_rgba(1, 1, 1, 0.85)
        ctx.rectangle(*box)
        ctx.stroke()
        ctx.set_dash([4, 3])
        ctx.set_source_rgb(*accent)
        ctx.rectangle(*box)
        ctx.stroke()
        ctx.set_dash([])
        for hx, hy in item.handles().values():
            chrome.handle(ctx, *self.to_screen(hx, hy), accent)

    def draw_chip(self, ctx: cairo.Context, x: float, y: float, label: str) -> None:
        chrome.chip(ctx, x, y, label)

    # -- input ----------------------------------------------------------------------------
    def _setup_input(self) -> None:
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

        pan = Gtk.GestureDrag(button=Gdk.BUTTON_MIDDLE)
        pan.connect("drag-begin", lambda _g, x, y: self._begin_pan())
        pan.connect("drag-update", lambda _g, dx, dy: self._update_pan(dx, dy))
        pan.connect("drag-end", lambda *_: setattr(self, "_pan_start", None))
        self.area.add_controller(pan)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_: self._on_leave())
        self.area.add_controller(motion)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.area.add_controller(scroll)

    def _begin_pan(self) -> None:
        self._pan_start = (self.ox, self.oy, 0.0, 0.0)
        self.set_cursor_name("grabbing")

    def _update_pan(self, dx: float, dy: float) -> None:
        if self._pan_start is None:
            return
        ox, oy, _a, _b = self._pan_start
        self.ox, self.oy = ox + dx, oy + dy
        self.fit_mode = False
        self._reposition_editor()
        self.area.queue_draw()

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        self.area.grab_focus()
        self._gesture_tool = None
        if self._edit is not None:
            # This click only finishes the text. The tool must not see the rest of it: a
            # release without its press made the Callout tool add another bubble.
            self.end_text_edit()
            return
        if self.space_pan:
            self._begin_pan()
            return
        now = time.monotonic() * 1000
        last_t, lx, ly = self._last_press
        n_press = 2 if now - last_t < DOUBLE_CLICK_MS and math.hypot(x - lx, y - ly) < 6 else 1
        self._last_press = (now, x, y)
        self._drag_origin = (x, y)
        state = gesture.get_current_event_state()
        self._gesture_tool = self.tool
        self.tool.press(*self.to_image(x, y), state, n_press)

    def _on_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._pan_start is not None:
            self._update_pan(dx, dy)
            return
        if self._gesture_tool is None or self._gesture_tool is not self.tool:
            return
        ox, oy = self._drag_origin
        self._pointer = (ox + dx, oy + dy)
        state = gesture.get_current_event_state()
        self.tool.drag(*self.to_image(ox + dx, oy + dy), state)

    def _on_drag_end(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._pan_start is not None:
            self._pan_start = None
            self.set_cursor_name(self.tool.cursor)
            return
        tool, self._gesture_tool = self._gesture_tool, None
        if tool is None or tool is not self.tool:
            return
        ox, oy = self._drag_origin
        state = gesture.get_current_event_state()
        tool.release(*self.to_image(ox + dx, oy + dy), state)

    def _finish_gesture(self) -> None:
        """End the drag in progress where it is (a tool key was pressed mid-drag). The old
        tool finishes what it was drawing; the new one never gets half a drag."""
        tool, self._gesture_tool = self._gesture_tool, None
        if tool is not None:
            x, y = self.to_image(*(self._pointer or self._drag_origin))
            tool.release(x, y, Gdk.ModifierType(0))

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        self._pointer = (x, y)
        if self.space_pan:
            self.set_cursor_name("grab")
            return
        self.tool.motion(*self.to_image(x, y))

    def _on_leave(self) -> None:
        self._pointer = None
        if isinstance(self.tool, freehand.EraserTool):
            self.tool.deactivate()
            self.area.queue_draw()

    def _on_scroll(self, ctrl: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            factor = 1.15 ** (-dy)
            self.zoom_to(self.scale * factor, self._pointer)
            return True
        if state & Gdk.ModifierType.SHIFT_MASK:
            dx, dy = dy, dx
        self.ox -= dx * 40
        self.oy -= dy * 40
        self.fit_mode = False
        self._reposition_editor()
        self.area.queue_draw()
        return True

    # -- text editing ---------------------------------------------------------------------
    @property
    def editing(self) -> bool:
        return self._edit is not None

    def begin_text_edit(self, item: Item, new: bool) -> None:
        self.end_text_edit()
        view = Gtk.TextView(accepts_tab=False)
        view.add_css_class("gh-text-edit")
        is_callout = isinstance(item, CalloutItem)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if is_callout else Gtk.WrapMode.NONE)
        view.set_halign(Gtk.Align.START)
        view.set_valign(Gtk.Align.START)
        buf = view.get_buffer()
        buf.begin_irreversible_action()  # Undo while typing stops at the text as it was
        buf.set_text(item.text)
        buf.end_irreversible_action()
        tag = buf.create_tag("look")

        def restyle(*_args) -> None:
            tag.set_property("font", f"Sans Bold {max(6, round(item.size * self.scale))}px")
            tag.set_property("foreground", item.color if not is_callout else "#ffffff")
            buf.apply_tag(tag, buf.get_start_iter(), buf.get_end_iter())

        buf.connect("changed", restyle)
        restyle()

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_edit_key)
        view.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.idle_add(self._commit_if_unfocused, view))
        view.add_controller(focus)

        self._edit = (item, new, view)
        if not new:
            self.hidden_id = item.id
            self.invalidate()
        self.add_overlay(view)
        self._reposition_editor()
        view.grab_focus()
        self.area.queue_draw()

    def _reposition_editor(self) -> None:
        if self._edit is None:
            return
        item, _new, view = self._edit
        if isinstance(item, CalloutItem):
            x, y, w, h = item.rect()
            sx, sy = self.to_screen(x, y)
            view.set_size_request(max(80, round(w * self.scale)), max(30, round(h * self.scale)))
        else:
            sx, sy = self.to_screen(item.x, item.y)
            view.set_size_request(max(60, round(item.size * self.scale * 4)), -1)
        view.set_margin_start(max(0, round(sx)))
        view.set_margin_top(max(0, round(sy)))

    def _on_edit_key(self, _ctrl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_Escape or (ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)):
            self.end_text_edit()
            self.area.grab_focus()
            return True
        return False

    def _commit_if_unfocused(self, view: Gtk.TextView) -> bool:
        if self._edit is not None and self._edit[2] is view and not view.has_focus():
            self.end_text_edit()
        return GLib.SOURCE_REMOVE

    def end_text_edit(self) -> None:
        if self._edit is None:
            return
        item, new, view = self._edit
        self._edit = None
        buf = view.get_buffer()
        value = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).rstrip()
        self.remove_overlay(view)
        self.hidden_id = None
        if isinstance(item, TextItem):
            if new:
                if value:
                    item.text = value
                    self.add_item(item)
            elif value != item.text:
                self.begin_change()
                if value:
                    item.text = value
                else:
                    self.doc.items.remove(item)
                    self.select(None)
                self.changed()
        elif isinstance(item, CalloutItem) and value != item.text:
            if not new:
                self.begin_change()
            item.text = value
            self.changed()
        self.invalidate()
