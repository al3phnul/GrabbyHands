# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Select, move, resize and delete items."""

from __future__ import annotations

from gi.repository import Gdk

from grabbyhands.editor.document import CalloutItem, TextItem
from grabbyhands.editor.tools.base import Tool, shift_held


class SelectTool(Tool):
    name = "select"
    cursor = "default"

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self._mode: str | None = None  # "move" | handle name
        self._last = (0.0, 0.0)
        self._changed = False

    def press(self, x, y, state, n_press=1):
        canvas = self.canvas
        self._mode = None
        self._changed = False
        self._last = (x, y)
        selected = canvas.selected_item()
        if selected is not None:
            handle = canvas.handle_at(selected, x, y)
            if handle:
                self._mode = handle
                return
        hit = canvas.doc.item_at(x, y, canvas.image_tolerance())
        canvas.select(hit)
        if hit is None:
            return
        if n_press == 2 and isinstance(hit, TextItem | CalloutItem):
            canvas.begin_text_edit(hit, new=False)
            return
        self._mode = "move"

    def drag(self, x, y, state):
        item = self.canvas.selected_item()
        if item is None or self._mode is None:
            return
        if not self._changed:
            self.canvas.begin_change()
            self._changed = True
        if self._mode == "move":
            lx, ly = self._last
            item.move(x - lx, y - ly)
        else:
            item.set_handle(self._mode, x, y)
        self._last = (x, y)
        self.canvas.changed(live=True)

    def release(self, x, y, state):
        if self._changed:
            item = self.canvas.selected_item()
            if item is not None and hasattr(item, "normalize"):
                item.normalize()
            self.canvas.changed()
        self._mode = None
        self._changed = False

    def motion(self, x, y):
        item = self.canvas.selected_item()
        handle = self.canvas.handle_at(item, x, y) if item else None
        if handle:
            self.canvas.set_cursor_name(_HANDLE_CURSORS.get(handle, "move"))
        elif self.canvas.doc.item_at(x, y, self.canvas.image_tolerance()):
            self.canvas.set_cursor_name("move")
        else:
            self.canvas.set_cursor_name("default")

    def key(self, keyval, state):
        item = self.canvas.selected_item()
        if item is None:
            return False
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete, Gdk.KEY_BackSpace):
            self.canvas.delete_selected()
            return True
        arrows = {
            Gdk.KEY_Left: (-1, 0),
            Gdk.KEY_Right: (1, 0),
            Gdk.KEY_Up: (0, -1),
            Gdk.KEY_Down: (0, 1),
        }
        if keyval in arrows:
            step = 10 if shift_held(state) else 1
            dx, dy = arrows[keyval]
            self.canvas.begin_change(burst=("nudge", item.id))  # a held key: one undo step
            item.move(dx * step, dy * step)
            self.canvas.changed()
            return True
        return False


_HANDLE_CURSORS = {
    "nw": "nw-resize",
    "n": "n-resize",
    "ne": "ne-resize",
    "e": "e-resize",
    "se": "se-resize",
    "s": "s-resize",
    "sw": "sw-resize",
    "w": "w-resize",
    "start": "crosshair",
    "end": "crosshair",
    "tail": "crosshair",
    "target": "move",
}
