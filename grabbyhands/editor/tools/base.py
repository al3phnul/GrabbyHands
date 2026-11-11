# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Tool base class and the shared drawing style."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gi.repository import Gdk

if TYPE_CHECKING:
    import cairo

    from grabbyhands.editor.canvas import Canvas


@dataclass
class Style:
    """What new items look like. The options bar edits this."""

    color: str = "#ff2d6f"
    width: float = 4.0
    fill: str = "none"
    font_size: float = 22.0
    highlight_color: str = "#ffe14d"
    highlight_width: float = 22.0
    redact_mode: str = "pixelate"
    strength: float = 50.0  # percent
    spot_shape: str = "rect"
    dim: float = 0.6
    zoom: float = 2.0
    eraser_width: float = 24.0
    sticker: str = "✓"


def shift_held(state: Gdk.ModifierType) -> bool:
    return bool(state & Gdk.ModifierType.SHIFT_MASK)


def snap_angle(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float]:
    """Constrain the end point to 45° steps."""
    angle = math.atan2(y1 - y0, x1 - x0)
    step = math.pi / 4
    angle = round(angle / step) * step
    length = math.hypot(x1 - x0, y1 - y0)
    return x0 + math.cos(angle) * length, y0 + math.sin(angle) * length


def square(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float]:
    """Constrain the end point so the box is a square."""
    side = max(abs(x1 - x0), abs(y1 - y0))
    return x0 + math.copysign(side, x1 - x0 or 1), y0 + math.copysign(side, y1 - y0 or 1)


class Tool:
    name = ""
    cursor = "crosshair"

    def __init__(self, canvas: Canvas) -> None:
        self.canvas = canvas

    @property
    def style(self) -> Style:
        return self.canvas.style

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass

    # Coordinates are image pixels.
    def press(self, x: float, y: float, state: Gdk.ModifierType, n_press: int = 1) -> None:
        pass

    def drag(self, x: float, y: float, state: Gdk.ModifierType) -> None:
        pass

    def release(self, x: float, y: float, state: Gdk.ModifierType) -> None:
        pass

    def motion(self, x: float, y: float) -> None:
        pass

    def key(self, keyval: int, state: Gdk.ModifierType) -> bool:
        return False

    def draw_overlay(self, ctx: cairo.Context) -> None:
        """Extra drawing in screen space (after the image)."""


class HandleAwareTool(Tool):
    """Drawing tools still let you grab the selected item's handles or move it."""

    def __init__(self, canvas: Canvas) -> None:
        super().__init__(canvas)
        self._delegate = False

    def press(self, x, y, state, n_press=1):
        selected = self.canvas.selected_item()
        if selected is not None and self.canvas.handle_at(selected, x, y):
            self._delegate = True
            self.canvas.tools["select"].press(x, y, state, n_press)
            return
        self._delegate = False
        self.start(x, y, state)

    def drag(self, x, y, state):
        if self._delegate:
            self.canvas.tools["select"].drag(x, y, state)
        else:
            self.update(x, y, state)

    def release(self, x, y, state):
        if self._delegate:
            self.canvas.tools["select"].release(x, y, state)
            self._delegate = False
        else:
            self.finish(x, y, state)

    def start(self, x: float, y: float, state: Gdk.ModifierType) -> None:
        pass

    def update(self, x: float, y: float, state: Gdk.ModifierType) -> None:
        pass

    def finish(self, x: float, y: float, state: Gdk.ModifierType) -> None:
        pass
