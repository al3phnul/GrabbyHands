# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Freehand tools: pen, highlighter and the eraser."""

from __future__ import annotations

import math

import cairo

from grabbyhands.editor.document import EraserItem, HighlighterItem, PathItem, PenItem
from grabbyhands.editor.tools.base import HandleAwareTool, shift_held, snap_angle


class FreehandTool(HandleAwareTool):
    """Shift+drag draws a straight segment."""

    def new_item(self) -> PathItem:
        raise NotImplementedError

    def start(self, x, y, state):
        self._item = self.new_item()
        self._item.add_point(x, y)
        self._origin = (x, y)
        self.canvas.set_preview(self._item)

    def update(self, x, y, state):
        if shift_held(state):
            ox, oy = self._origin
            ex, ey = snap_angle(ox, oy, x, y)
            self._item.points = [[ox, oy], [ex, ey]]
        else:
            self._item.add_point(x, y, min_step=1.5 / max(self.canvas.scale, 0.01))
        self.canvas.set_preview(self._item)

    def finish(self, x, y, state):
        item = self._item
        self.canvas.set_preview(None)
        self._item = None
        if item is not None and item.points:
            self.commit(item)

    def commit(self, item: PathItem) -> None:
        self.canvas.add_item(item)


class PenTool(FreehandTool):
    name = "pen"

    def new_item(self):
        return PenItem(color=self.style.color, width=self.style.width)


class HighlighterTool(FreehandTool):
    name = "highlighter"

    def new_item(self):
        return HighlighterItem(color=self.style.highlight_color, width=self.style.highlight_width)


class EraserTool(FreehandTool):
    """Rubs out paint (anything you drew), never the screenshot."""

    name = "eraser"
    cursor = "none"

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self._pointer: tuple[float, float] | None = None

    def press(self, x, y, state, n_press=1):
        # Never grab handles: erasing near a selected item must still erase.
        self._delegate = False
        self.start(x, y, state)

    def new_item(self):
        return EraserItem(width=self.style.eraser_width)

    def commit(self, item):
        if not self.canvas.doc.items:
            return  # nothing to erase
        self.canvas.add_item(item, select=False)

    def motion(self, x, y):
        self._pointer = (x, y)
        self.canvas.queue_draw()

    def drag(self, x, y, state):
        self._pointer = (x, y)
        super().drag(x, y, state)

    def deactivate(self):
        self._pointer = None

    def draw_overlay(self, ctx: cairo.Context) -> None:
        if self._pointer is None:
            return
        px, py = self.canvas.to_screen(*self._pointer)
        r = max(2.0, self.style.eraser_width * self.canvas.scale / 2)
        ctx.arc(px, py, r, 0, 2 * math.pi)
        ctx.set_source_rgba(1, 1, 1, 0.9)
        ctx.set_line_width(2)
        ctx.stroke_preserve()
        ctx.set_source_rgba(0, 0, 0, 0.6)
        ctx.set_line_width(1)
        ctx.stroke()
