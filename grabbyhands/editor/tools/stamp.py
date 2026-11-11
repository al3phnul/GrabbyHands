# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Click-to-place tools: numbered steps and stickers (emoji and stamps)."""

from __future__ import annotations

from grabbyhands.editor.document import StepItem, StickerItem
from grabbyhands.editor.tools.base import HandleAwareTool, square

STAMPS = ("✓", "✗", "★", "!", "?", "➜", "♥")


class StepTool(HandleAwareTool):
    """Each click adds the next number. Drag before releasing to position it."""

    name = "step"

    def start(self, x, y, state):
        s = self.style
        self._item = StepItem(
            x=x, y=y, number=self.canvas.doc.next_step_number(), size=s.font_size, color=s.color
        )
        self.canvas.set_preview(self._item)

    def update(self, x, y, state):
        self._item.x, self._item.y = x, y
        self.canvas.set_preview(self._item)

    def finish(self, x, y, state):
        item, self._item = self._item, None
        self.canvas.set_preview(None)
        if item is not None:
            self.canvas.add_item(item)


class StickerTool(HandleAwareTool):
    """Click to drop the current sticker, or drag to size it."""

    name = "sticker"
    DEFAULT = 72

    def start(self, x, y, state):
        self._origin = (x, y)
        self._item = None

    def update(self, x, y, state):
        x0, y0 = self._origin
        x1, y1 = square(x0, y0, x, y)
        s = self.style
        self._item = StickerItem(x0=x0, y0=y0, x1=x1, y1=y1, text=s.sticker, color=s.color)
        self.canvas.set_preview(self._item)

    def finish(self, x, y, state):
        item, self._item = self._item, None
        self.canvas.set_preview(None)
        if item is None or max(item.rect()[2:]) < 12:
            half = self.DEFAULT / 2
            ox, oy = self._origin
            s = self.style
            item = StickerItem(
                x0=ox - half,
                y0=oy - half,
                x1=ox + half,
                y1=oy + half,
                text=s.sticker,
                color=s.color,
            )
        item.normalize()
        self.canvas.add_item(item)
