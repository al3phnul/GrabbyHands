# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Annotation document: items drawn over a screenshot, in image pixel coordinates.

No GTK in here, so the model can be tested on its own. Rendering lives in render.py.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import ClassVar

from grabbyhands.colors import parse_hex

VERSION = 2  # 2: redact strength became a 0-100% scale
DEFAULT_COLOR = "#ff2d6f"
BOX_HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _seg_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _norm(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


# -- items ---------------------------------------------------------------------------
@dataclass(kw_only=True)
class Item:
    type: ClassVar[str] = "item"
    selectable: ClassVar[bool] = True
    color: str = DEFAULT_COLOR
    width: float = 4.0
    opacity: float = 1.0
    id: str = field(default_factory=_new_id)

    def bounds(self) -> tuple[float, float, float, float]:
        raise NotImplementedError

    def hit_test(self, x: float, y: float, tol: float) -> bool:
        x0, y0, x1, y1 = self.bounds()
        return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol

    def handles(self) -> dict[str, tuple[float, float]]:
        return {}

    def move(self, dx: float, dy: float) -> None:
        raise NotImplementedError

    def set_handle(self, name: str, x: float, y: float) -> None:
        pass

    def to_dict(self) -> dict:
        data = asdict(self)
        data["type"] = self.type
        return data


@dataclass(kw_only=True)
class LineItem(Item):
    type: ClassVar[str] = "line"
    x0: float = 0
    y0: float = 0
    x1: float = 0
    y1: float = 0

    def bounds(self):
        return _norm(self.x0, self.y0, self.x1, self.y1)

    def hit_test(self, x, y, tol):
        return _seg_distance(x, y, self.x0, self.y0, self.x1, self.y1) <= tol + self.width / 2

    def handles(self):
        return {"start": (self.x0, self.y0), "end": (self.x1, self.y1)}

    def move(self, dx, dy):
        self.x0 += dx
        self.y0 += dy
        self.x1 += dx
        self.y1 += dy

    def set_handle(self, name, x, y):
        if name == "start":
            self.x0, self.y0 = x, y
        elif name == "end":
            self.x1, self.y1 = x, y

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass(kw_only=True)
class ArrowItem(LineItem):
    type: ClassVar[str] = "arrow"


@dataclass(kw_only=True)
class BoxItem(Item):
    """Anything defined by a rectangle (x0, y0)-(x1, y1)."""

    type: ClassVar[str] = "box"
    x0: float = 0
    y0: float = 0
    x1: float = 0
    y1: float = 0

    def rect(self) -> tuple[float, float, float, float]:
        """(x, y, w, h), normalized."""
        x0, y0, x1, y1 = _norm(self.x0, self.y0, self.x1, self.y1)
        return x0, y0, x1 - x0, y1 - y0

    def bounds(self):
        return _norm(self.x0, self.y0, self.x1, self.y1)

    def handles(self):
        x0, y0, x1, y1 = self.bounds()
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

    def move(self, dx, dy):
        self.x0 += dx
        self.y0 += dy
        self.x1 += dx
        self.y1 += dy

    def set_handle(self, name, x, y):
        x0, y0, x1, y1 = self.bounds()
        if "w" in name:
            x0 = x
        if "e" in name:
            x1 = x
        if "n" in name:
            y0 = y
        if "s" in name:
            y1 = y
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    def normalize(self) -> None:
        self.x0, self.y0, self.x1, self.y1 = self.bounds()


@dataclass(kw_only=True)
class RectItem(BoxItem):
    type: ClassVar[str] = "rect"
    fill: str = "none"  # none | translucent | solid

    def hit_test(self, x, y, tol):
        if self.fill != "none":
            return super().hit_test(x, y, tol)
        x0, y0, x1, y1 = self.bounds()
        t = tol + self.width / 2
        inside_outer = x0 - t <= x <= x1 + t and y0 - t <= y <= y1 + t
        inside_inner = x0 + t < x < x1 - t and y0 + t < y < y1 - t
        return inside_outer and not inside_inner


@dataclass(kw_only=True)
class EllipseItem(BoxItem):
    type: ClassVar[str] = "ellipse"
    fill: str = "none"

    def hit_test(self, x, y, tol):
        x0, y0, x1, y1 = self.bounds()
        rx, ry = max((x1 - x0) / 2, 0.5), max((y1 - y0) / 2, 0.5)
        cx, cy = x0 + rx, y0 + ry
        d = math.hypot((x - cx) / rx, (y - cy) / ry)
        if self.fill != "none":
            return d <= 1 + tol / min(rx, ry)
        band = (tol + self.width / 2) / min(rx, ry)
        return abs(d - 1) <= band


@dataclass(kw_only=True)
class RedactItem(BoxItem):
    type: ClassVar[str] = "redact"
    mode: str = "pixelate"  # pixelate | blur | solid
    strength: float = 50  # percent


@dataclass(kw_only=True)
class SpotlightItem(BoxItem):
    type: ClassVar[str] = "spotlight"
    shape: str = "rect"  # rect | ellipse
    dim: float = 0.6


@dataclass(kw_only=True)
class StickerItem(BoxItem):
    type: ClassVar[str] = "sticker"
    text: str = "✓"


@dataclass(kw_only=True)
class CalloutItem(BoxItem):
    """A speech bubble (the box) whose tail points at (tail_x, tail_y)."""

    type: ClassVar[str] = "callout"
    text: str = ""
    size: float = 22
    tail_x: float = 0
    tail_y: float = 0

    def handles(self):
        h = super().handles()
        h["tail"] = (self.tail_x, self.tail_y)
        return h

    def move(self, dx, dy):
        super().move(dx, dy)
        self.tail_x += dx
        self.tail_y += dy

    def set_handle(self, name, x, y):
        if name == "tail":
            self.tail_x, self.tail_y = x, y
        else:
            super().set_handle(name, x, y)


@dataclass(kw_only=True)
class MagnifyItem(BoxItem):
    """Source box (x0..y1) shown zoomed in a circle centered on (target_x, target_y)."""

    type: ClassVar[str] = "magnify"
    zoom: float = 2.0
    target_x: float = 0
    target_y: float = 0
    width: float = 3.0

    def radius(self) -> float:
        _x, _y, w, h = self.rect()
        return max(w, h) / 2 * self.zoom

    def bounds(self):
        x0, y0, x1, y1 = super().bounds()
        r = self.radius()
        return (
            min(x0, self.target_x - r),
            min(y0, self.target_y - r),
            max(x1, self.target_x + r),
            max(y1, self.target_y + r),
        )

    def hit_test(self, x, y, tol):
        bx0, by0, bx1, by1 = super().bounds()
        in_source = bx0 - tol <= x <= bx1 + tol and by0 - tol <= y <= by1 + tol
        in_lens = math.hypot(x - self.target_x, y - self.target_y) <= self.radius() + tol
        return in_source or in_lens

    def handles(self):
        h = super().handles()
        h["target"] = (self.target_x, self.target_y)
        return h

    def move(self, dx, dy):
        super().move(dx, dy)
        self.target_x += dx
        self.target_y += dy

    def set_handle(self, name, x, y):
        if name == "target":
            self.target_x, self.target_y = x, y
        else:
            super().set_handle(name, x, y)


@dataclass(kw_only=True)
class PathItem(Item):
    type: ClassVar[str] = "path"
    points: list[list[float]] = field(default_factory=list)

    def bounds(self):
        if not self.points:
            return 0, 0, 0, 0
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        pad = self.width / 2
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad

    def hit_test(self, x, y, tol):
        pts = self.points
        if len(pts) == 1:
            return math.hypot(x - pts[0][0], y - pts[0][1]) <= tol + self.width / 2
        return any(
            _seg_distance(x, y, a[0], a[1], b[0], b[1]) <= tol + self.width / 2
            for a, b in zip(pts, pts[1:], strict=False)
        )

    def move(self, dx, dy):
        self.points = [[p[0] + dx, p[1] + dy] for p in self.points]

    def add_point(self, x: float, y: float, min_step: float = 1.5) -> None:
        if self.points:
            lx, ly = self.points[-1]
            if math.hypot(x - lx, y - ly) < min_step:
                return
        self.points.append([x, y])


@dataclass(kw_only=True)
class PenItem(PathItem):
    type: ClassVar[str] = "pen"


@dataclass(kw_only=True)
class HighlighterItem(PathItem):
    type: ClassVar[str] = "highlighter"
    color: str = "#ffe14d"
    width: float = 22.0


@dataclass(kw_only=True)
class EraserItem(PathItem):
    """Clears the paint layer along its path. Not selectable: it isn't a visible thing."""

    type: ClassVar[str] = "eraser"
    selectable: ClassVar[bool] = False
    width: float = 24.0


@dataclass(kw_only=True)
class TextItem(Item):
    type: ClassVar[str] = "text"
    x: float = 0
    y: float = 0
    text: str = ""
    size: float = 22

    def bounds(self):
        from grabbyhands.editor.textmetrics import measure

        w, h = measure(self.text or " ", self.size)
        return self.x, self.y, self.x + w, self.y + h

    def move(self, dx, dy):
        self.x += dx
        self.y += dy


@dataclass(kw_only=True)
class StepItem(Item):
    type: ClassVar[str] = "step"
    x: float = 0
    y: float = 0
    number: int = 1
    size: float = 22  # font size; the circle grows with it

    @property
    def radius(self) -> float:
        return self.size * 0.95

    def bounds(self):
        r = self.radius
        return self.x - r, self.y - r, self.x + r, self.y + r

    def hit_test(self, x, y, tol):
        return math.hypot(x - self.x, y - self.y) <= self.radius + tol

    def move(self, dx, dy):
        self.x += dx
        self.y += dy


ITEM_TYPES: dict[str, type[Item]] = {
    cls.type: cls
    for cls in (
        LineItem,
        ArrowItem,
        RectItem,
        EllipseItem,
        RedactItem,
        SpotlightItem,
        StickerItem,
        CalloutItem,
        MagnifyItem,
        PenItem,
        HighlighterItem,
        EraserItem,
        TextItem,
        StepItem,
    )
}


# Values a field may take. Anything else falls back to the safe choice listed first: a
# redaction this version can't draw the intended way must still hide what is under it.
CHOICES = {
    ("redact", "mode"): ("solid", "pixelate", "blur"),
    ("rect", "fill"): ("none", "translucent", "solid"),
    ("ellipse", "fill"): ("none", "translucent", "solid"),
    ("spotlight", "shape"): ("rect", "ellipse"),
}
# Numbers that would break drawing outside these limits: (lowest, highest).
LIMITS = {
    "opacity": (0.0, 1.0),
    "dim": (0.0, 1.0),
    "zoom": (1.0, 16.0),
    "width": (0.1, 2000.0),
    "size": (1.0, 2000.0),
}
# A version-1 redaction can be stronger than today's 100% (see _v1_strength_to_percent).
MAX_STRENGTH = 400.0
COORDINATE_LIMIT = 1_000_000.0


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _valid_points(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(p, list | tuple)
        and len(p) == 2
        and all(_is_number(c) and abs(c) <= COORDINATE_LIMIT for c in p)
        for p in value
    )


def item_from_dict(data: object) -> Item | None:
    """Build an item from saved data, or None if it isn't a well-formed item of a known type.

    A document that was damaged, edited by hand or written by a newer version must never
    stop the rest from loading (or crash the renderer later), so everything is checked here.
    """
    if not isinstance(data, dict):
        return None
    kind = data.get("type")
    cls = ITEM_TYPES.get(kind) if isinstance(kind, str) else None
    if cls is None:
        return None
    values: dict = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        if f.name == "points":
            if not _valid_points(value):
                return None
            value = [[float(p[0]), float(p[1])] for p in value]
        elif f.type == "str":
            if not isinstance(value, str):
                return None
        elif f.type in ("float", "int"):
            if not _is_number(value) or abs(value) > COORDINATE_LIMIT:
                return None
            if f.type == "int":
                value = round(value)
            if f.name in LIMITS:
                low, high = LIMITS[f.name]
                value = max(low, min(high, value))
        else:
            continue
        values[f.name] = value
    item = cls(**values)
    for (item_type, name), allowed in CHOICES.items():
        if item_type == cls.type and getattr(item, name) not in allowed:
            setattr(item, name, allowed[0])
    if isinstance(item, RedactItem):
        item.strength = max(0.0, min(MAX_STRENGTH, item.strength))
    try:
        parse_hex(item.color)
    except ValueError:
        item.color = type(item)().color
    if not item.id:
        item.id = _new_id()
    return item


# What 100% means for a redaction (render.py draws with the same numbers).
MAX_PIXEL_BLOCK = 20
MAX_BLUR_RADIUS = 12.0


def _v1_strength_to_percent(mode: str, old: float) -> float:
    """Version 1 stored pixel sizes: pixelate blocks of 6 to 40 px, blur radii of 8 to 40 px.

    Today's scale ends at a 20 px block and a 12 px radius, so a strong old redaction comes
    out above 100%. It keeps exactly the strength it was drawn with (never weaker) until
    the user moves its Strength slider.
    """
    if mode == "pixelate":
        percent = (max(6, int(old)) - 1) / (MAX_PIXEL_BLOCK - 1) * 100
    elif mode == "blur":
        percent = max(8.0, float(old)) / MAX_BLUR_RADIUS * 100
    else:
        return 50.0
    return math.ceil(percent * 1000) / 1000  # rounded up: never a hair weaker either


# -- document ------------------------------------------------------------------------
@dataclass
class Document:
    items: list[Item] = field(default_factory=list)
    crop: tuple[int, int, int, int] | None = None  # x, y, w, h in image pixels

    # -- queries ------------------------------------------------------------------------
    def get(self, item_id: str | None) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None) if item_id else None

    def item_at(self, x: float, y: float, tol: float = 4) -> Item | None:
        for item in reversed(self.items):
            if item.selectable and item.hit_test(x, y, tol):
                return item
        return None

    def next_step_number(self) -> int:
        steps = [i.number for i in self.items if isinstance(i, StepItem)]
        return max(steps, default=0) + 1

    @property
    def empty(self) -> bool:
        return not self.items and self.crop is None

    # -- serialization --------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "crop": list(self.crop) if self.crop else None,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, data: object) -> Document:
        """Rebuild a document. Unknown or damaged parts are left out; ``ValueError`` only
        when ``data`` isn't a document at all."""
        if not isinstance(data, dict):
            raise ValueError("not an annotation document")
        raw_items = data.get("items")
        version = data.get("version", 1)
        old_strengths = isinstance(version, int) and not isinstance(version, bool) and version < 2
        items: list[Item] = []
        seen: set[str] = set()
        for raw in raw_items if isinstance(raw_items, list) else []:
            # version 1 strengths are pixel sizes: convert before the percent limits apply
            if old_strengths and isinstance(raw, dict) and raw.get("type") == "redact":
                strength = raw.get("strength", 14)  # 14 px was the version 1 default
                if _is_number(strength):
                    mode = raw.get("mode", "pixelate")
                    raw = {**raw, "strength": _v1_strength_to_percent(mode, strength)}
            item = item_from_dict(raw)
            if item is None:
                continue
            if item.id in seen:
                item.id = _new_id()
            seen.add(item.id)
            items.append(item)
        # (older documents may also have a "beautify" entry; that feature was removed)
        return cls(items=items, crop=cls._valid_crop(data.get("crop")))

    @staticmethod
    def _valid_crop(crop: object) -> tuple[int, int, int, int] | None:
        if not isinstance(crop, list | tuple) or len(crop) != 4:
            return None
        if not all(_is_number(v) and abs(v) <= COORDINATE_LIMIT for v in crop):
            return None
        x, y, w, h = (round(v) for v in crop)
        return (x, y, w, h) if w > 0 and h > 0 else None

    def replace_with(self, other: Document) -> None:
        """Take over another document's content, keeping this object's identity."""
        self.items, self.crop = other.items, other.crop

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), "utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Document:
        """Read a saved document. ``OSError`` or ``ValueError`` if it can't be read at all."""
        try:
            data = json.loads(path.read_text("utf-8"))
        except RecursionError as err:  # absurdly nested JSON
            raise ValueError("not an annotation document") from err
        return cls.from_dict(data)


class UndoStack:
    """Snapshot undo: call checkpoint() *before* each change."""

    LIMIT = 200

    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self.serial = 0  # goes up with every checkpoint, undo and redo

    def checkpoint(self) -> None:
        self._undo.append(self.doc.to_dict())
        if len(self._undo) > self.LIMIT:
            del self._undo[0]
        self._redo.clear()
        self.serial += 1

    def discard_checkpoint(self) -> None:
        """Drop the last checkpoint (the change it guarded didn't happen)."""
        if self._undo:
            self._undo.pop()
            self.serial += 1

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.doc.to_dict())
        self.doc.replace_with(Document.from_dict(self._undo.pop()))
        self.serial += 1
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.doc.to_dict())
        self.doc.replace_with(Document.from_dict(self._redo.pop()))
        self.serial += 1
        return True
