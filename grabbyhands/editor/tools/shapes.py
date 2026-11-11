# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Drag-to-draw tools: lines, arrows, boxes, redaction, spotlight, magnify, callouts, crop."""

from __future__ import annotations

import cairo
from gi.repository import Gdk

from grabbyhands import chrome, theme
from grabbyhands.editor.document import (
    ArrowItem,
    CalloutItem,
    EllipseItem,
    Item,
    LineItem,
    MagnifyItem,
    RectItem,
    RedactItem,
    SpotlightItem,
)
from grabbyhands.editor.tools.base import HandleAwareTool, Tool, shift_held, snap_angle, square

MIN_DRAG = 3  # image pixels


class DragShapeTool(HandleAwareTool):
    """Press, drag, release: the item spans the drag."""

    def start(self, x, y, state):
        self._origin = (x, y)
        self._item: Item | None = None

    def update(self, x, y, state):
        x0, y0 = self._origin
        self._item = self.make(x0, y0, x, y, shift_held(state))
        self.canvas.set_preview(self._item)

    def finish(self, x, y, state):
        item = self._item
        self.canvas.set_preview(None)
        self._item = None
        if item is None:
            self.clicked(*self._origin)
            return
        bx0, by0, bx1, by1 = item.bounds()
        if max(bx1 - bx0, by1 - by0) < MIN_DRAG:
            self.clicked(*self._origin)
            return
        self.canvas.add_item(item)
        self.after_add(item)

    def make(self, x0, y0, x1, y1, constrain) -> Item:
        raise NotImplementedError

    def clicked(self, x: float, y: float) -> None:
        """A click without a drag: select whatever is there."""
        self.canvas.select(self.canvas.doc.item_at(x, y, self.canvas.image_tolerance()))

    def after_add(self, item: Item) -> None:
        pass


class LineTool(DragShapeTool):
    name = "line"
    item_class: type[LineItem] = LineItem

    def make(self, x0, y0, x1, y1, constrain):
        if constrain:
            x1, y1 = snap_angle(x0, y0, x1, y1)
        s = self.style
        return self.item_class(x0=x0, y0=y0, x1=x1, y1=y1, color=s.color, width=s.width)


class ArrowTool(LineTool):
    name = "arrow"
    item_class = ArrowItem


class RectTool(DragShapeTool):
    name = "rect"

    def make(self, x0, y0, x1, y1, constrain):
        if constrain:
            x1, y1 = square(x0, y0, x1, y1)
        s = self.style
        return RectItem(x0=x0, y0=y0, x1=x1, y1=y1, color=s.color, width=s.width, fill=s.fill)


class EllipseTool(DragShapeTool):
    name = "ellipse"

    def make(self, x0, y0, x1, y1, constrain):
        if constrain:
            x1, y1 = square(x0, y0, x1, y1)
        s = self.style
        return EllipseItem(x0=x0, y0=y0, x1=x1, y1=y1, color=s.color, width=s.width, fill=s.fill)


class RedactTool(DragShapeTool):
    name = "redact"

    def make(self, x0, y0, x1, y1, constrain):
        s = self.style
        return RedactItem(x0=x0, y0=y0, x1=x1, y1=y1, mode=s.redact_mode, strength=s.strength)


class SpotlightTool(DragShapeTool):
    name = "spotlight"

    def make(self, x0, y0, x1, y1, constrain):
        if constrain:
            x1, y1 = square(x0, y0, x1, y1)
        s = self.style
        return SpotlightItem(x0=x0, y0=y0, x1=x1, y1=y1, shape=s.spot_shape, dim=s.dim)


class MagnifyTool(DragShapeTool):
    name = "magnify"

    def make(self, x0, y0, x1, y1, constrain):
        if constrain:
            x1, y1 = square(x0, y0, x1, y1)
        s = self.style
        item = MagnifyItem(x0=x0, y0=y0, x1=x1, y1=y1, color=s.color, width=s.width, zoom=s.zoom)
        self._place_lens(item)
        return item

    def _place_lens(self, item: MagnifyItem) -> None:
        """Put the zoomed circle beside the source box, inside the image if possible."""
        x, y, w, h = item.rect()
        r = item.radius()
        iw, ih = self.canvas.image_size
        cx, cy = x + w / 2, y + h / 2
        gap = r + max(w, h) / 2 + 24
        candidates = [(cx + gap, cy), (cx - gap, cy), (cx, cy + gap), (cx, cy - gap)]
        for tx, ty in candidates:
            if r <= tx <= iw - r and r <= ty <= ih - r:
                item.target_x, item.target_y = tx, ty
                return
        item.target_x, item.target_y = candidates[0]


class CalloutTool(DragShapeTool):
    """Drag from the thing you're pointing at to where the bubble should go."""

    name = "callout"

    def make(self, x0, y0, x1, y1, constrain):
        return self._bubble(x0, y0, x1, y1)

    def _bubble(self, tail_x, tail_y, bx, by) -> CalloutItem:
        s = self.style
        w, h = max(160.0, s.font_size * 9), max(56.0, s.font_size * 3.2)
        return CalloutItem(
            x0=bx - w / 2,
            y0=by - h / 2,
            x1=bx + w / 2,
            y1=by + h / 2,
            tail_x=tail_x,
            tail_y=tail_y,
            color=s.color,
            size=s.font_size,
        )

    def clicked(self, x, y):
        # A plain click puts the bubble up and to the right of the point.
        s = self.style
        item = self._bubble(x, y, x + s.font_size * 6, y - s.font_size * 4)
        self.canvas.add_item(item)
        self.after_add(item)

    def after_add(self, item):
        self.canvas.begin_text_edit(item, new=True)


class CropTool(Tool):
    """Drag a crop box; Enter applies, Esc cancels."""

    name = "crop"

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self.rect: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1
        self._mode: str | None = None
        self._origin = (0.0, 0.0)
        self._start_rect = None

    def activate(self):
        crop = self.canvas.doc.crop
        self.rect = (crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3]) if crop else None
        self.canvas.queue_draw()

    def deactivate(self):
        self.rect = None

    def _handles(self) -> dict[str, tuple[float, float]]:
        if self.rect is None:
            return {}
        x0, y0, x1, y1 = self.rect
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {
            "nw": (x0, y0),
            "n": (mx, y0),
            "ne": (x1, y0),
            "e": (x1, my),
            "se": (x1, y1),
            "s": (mx, y1),
            "sw": (x0, y1),
            "w": (x0, my),
        }

    def press(self, x, y, state, n_press=1):
        self._origin = (x, y)
        self._start_rect = self.rect
        tol = self.canvas.image_tolerance(10)
        for name, (hx, hy) in self._handles().items():
            if abs(hx - x) <= tol and abs(hy - y) <= tol:
                self._mode = name
                return
        if self.rect and self.rect[0] < x < self.rect[2] and self.rect[1] < y < self.rect[3]:
            self._mode = "move"
            if n_press == 2:
                self.apply()
            return
        self._mode = "new"

    def drag(self, x, y, state):
        iw, ih = self.canvas.image_size
        x = max(0.0, min(float(iw), x))
        y = max(0.0, min(float(ih), y))
        ox, oy = self._origin
        if self._mode == "new":
            self.rect = (min(ox, x), min(oy, y), max(ox, x), max(oy, y))
        elif self._mode == "move" and self._start_rect:
            x0, y0, x1, y1 = self._start_rect
            dx = max(-x0, min(iw - x1, x - ox))
            dy = max(-y0, min(ih - y1, y - oy))
            self.rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        elif self._mode and self._start_rect:
            x0, y0, x1, y1 = self._start_rect
            if "w" in self._mode:
                x0 = x
            if "e" in self._mode:
                x1 = x
            if "n" in self._mode:
                y0 = y
            if "s" in self._mode:
                y1 = y
            self.rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.canvas.queue_draw()

    def release(self, x, y, state):
        if self.rect and (self.rect[2] - self.rect[0] < 4 or self.rect[3] - self.rect[1] < 4):
            self.rect = self._start_rect
        self._mode = None
        self.canvas.queue_draw()
        self.canvas.notify_tool_state()

    def apply(self) -> None:
        if self.rect is None:
            return
        x0, y0, x1, y1 = (round(v) for v in self.rect)
        iw, ih = self.canvas.image_size
        new = None if (x0, y0, x1, y1) == (0, 0, iw, ih) else (x0, y0, x1 - x0, y1 - y0)
        if new != self.canvas.doc.crop:
            self.canvas.begin_change()
            self.canvas.doc.crop = new
            self.canvas.changed()
        self.canvas.request_tool("select")

    def reset(self) -> None:
        self.rect = None
        if self.canvas.doc.crop is not None:
            self.canvas.begin_change()
            self.canvas.doc.crop = None
            self.canvas.changed()
        self.canvas.queue_draw()

    def key(self, keyval, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.apply()
            return True
        if keyval == Gdk.KEY_Escape:
            self.canvas.request_tool("select")
            return True
        return False

    def draw_overlay(self, ctx: cairo.Context) -> None:
        canvas = self.canvas
        iw, ih = canvas.image_size
        ax, ay = canvas.to_screen(0, 0)
        bx, by = canvas.to_screen(iw, ih)
        ctx.set_source_rgba(0, 0, 0, 0.55)
        ctx.rectangle(ax, ay, bx - ax, by - ay)
        if self.rect:
            sx0, sy0 = canvas.to_screen(self.rect[0], self.rect[1])
            sx1, sy1 = canvas.to_screen(self.rect[2], self.rect[3])
            ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            ctx.rectangle(sx0, sy0, sx1 - sx0, sy1 - sy0)
        ctx.fill()
        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        if not self.rect:
            return
        # Rule-of-thirds guides and the frame: white over a dark hairline, so they show on
        # a white picture as well as on a dark one.
        sx0, sy0, sx1, sy1 = round(sx0), round(sy0), round(sx1), round(sy1)
        for offset, color in ((1, (0, 0, 0, 0.28)), (0, (1, 1, 1, 0.9))):
            ctx.set_source_rgba(*color)
            ctx.set_line_width(1)
            for i in (1, 2):
                gx = round(sx0 + (sx1 - sx0) * i / 3) + 0.5 + offset
                gy = round(sy0 + (sy1 - sy0) * i / 3) + 0.5 + offset
                ctx.move_to(gx, sy0)
                ctx.line_to(gx, sy1)
                ctx.move_to(sx0, gy)
                ctx.line_to(sx1, gy)
            ctx.stroke()
        chrome.frame(ctx, sx0, sy0, sx1 - sx0, sy1 - sy0, (1, 1, 1))
        accent = theme.current().rgb()
        for hx, hy in self._handles().values():
            chrome.handle(ctx, *canvas.to_screen(hx, hy), accent)
        size = f"{round(self.rect[2] - self.rect[0])} × {round(self.rect[3] - self.rect[1])}"
        canvas.draw_chip(ctx, sx0, max(4, sy0 - 38), size)
