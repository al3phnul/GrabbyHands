# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The tools Smart Edit offers Claude, and how each call is checked and applied.

This is the whole surface Claude can touch: add, replace or delete annotation items, set or
remove the crop, and two read-only lookups that run on this computer (text positions from
OCR, and personal details found by Auto-Redact). There is no tool for files, commands or
the network.

Coordinates in tool calls are pixels of the picture Claude was sent, which may be a
scaled-down copy. ``Context.scale`` converts them to the original's pixels. Every item goes
through ``item_from_dict``, the same check a saved document gets, and is kept on the picture.

No GTK in here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from grabbyhands.editor.document import (
    Document,
    Item,
    PathItem,
    item_from_dict,
)

# Limits per message (one request from the user, however many tool rounds it takes).
MAX_ITEMS_PER_MESSAGE = 50
MAX_TEXT_PER_MESSAGE = 1000
MAX_FIND_RESULTS = 40

COORD_FIELDS = ("x0", "y0", "x1", "y1", "x", "y", "tail_x", "tail_y", "target_x", "target_y")
SIZE_FIELDS = ("width", "size")
TEXT_FIELDS = ("text",)

# Item types Claude may create. Pen, highlighter and eraser strokes stay hand-drawn.
CREATABLE = (
    "arrow",
    "line",
    "rect",
    "ellipse",
    "text",
    "callout",
    "step",
    "redact",
    "spotlight",
    "magnify",
    "sticker",
)


# -- schemas -------------------------------------------------------------------------------
def _num(desc: str) -> dict:
    return {"type": "number", "description": desc}


_COLOR = {"type": "string", "description": "Hex color like #ff2d6f. Default: the editor's color."}
_WIDTH = _num("Line width in pixels. Default 4.")
_BOX = {
    "x0": _num("Left edge"),
    "y0": _num("Top edge"),
    "x1": _num("Right edge"),
    "y1": _num("Bottom edge"),
}
_BOX_REQ = ["x0", "y0", "x1", "y1"]


def _variant(kind: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "description": desc,
        "properties": {"type": {"type": "string", "const": kind}, **props},
        "required": ["type", *required],
        "additionalProperties": False,
    }


ITEM_SCHEMAS: dict[str, dict] = {
    "arrow": _variant(
        "arrow",
        "Arrow from (x0, y0) to the head at (x1, y1).",
        {**_BOX, "color": _COLOR, "width": _WIDTH},
        _BOX_REQ,
    ),
    "line": _variant(
        "line", "Straight line.", {**_BOX, "color": _COLOR, "width": _WIDTH}, _BOX_REQ
    ),
    "rect": _variant(
        "rect",
        "Rectangle. fill 'translucent' makes a highlight box; 'solid' covers what is under it.",
        {
            **_BOX,
            "color": _COLOR,
            "width": _WIDTH,
            "fill": {"type": "string", "enum": ["none", "translucent", "solid"]},
        },
        _BOX_REQ,
    ),
    "ellipse": _variant(
        "ellipse",
        "Ellipse inside the box, e.g. to circle something.",
        {
            **_BOX,
            "color": _COLOR,
            "width": _WIDTH,
            "fill": {"type": "string", "enum": ["none", "translucent", "solid"]},
        },
        _BOX_REQ,
    ),
    "text": _variant(
        "text",
        "Text whose top-left corner is at (x, y).",
        {
            "x": _num("Left"),
            "y": _num("Top"),
            "text": {"type": "string"},
            "size": _num("Font size in pixels. Default 22."),
            "color": _COLOR,
        },
        ["x", "y", "text"],
    ),
    "callout": _variant(
        "callout",
        "Speech bubble filling the box, with its tail pointing at (tail_x, tail_y).",
        {
            **_BOX,
            "text": {"type": "string"},
            "tail_x": _num("Where the tail points, x"),
            "tail_y": _num("Where the tail points, y"),
            "size": _num("Font size in pixels. Default 22."),
            "color": _COLOR,
        },
        [*_BOX_REQ, "text", "tail_x", "tail_y"],
    ),
    "step": _variant(
        "step",
        "Numbered circle centered on (x, y), for numbering steps.",
        {
            "x": _num("Center x"),
            "y": _num("Center y"),
            "number": {"type": "integer"},
            "size": _num("Font size in pixels. Default 22."),
            "color": _COLOR,
        },
        ["x", "y", "number"],
    ),
    "redact": _variant(
        "redact",
        "Hide what is in the box. Use 'solid' for anything that must not be readable.",
        {
            **_BOX,
            "mode": {"type": "string", "enum": ["solid", "pixelate", "blur"]},
            "strength": _num("0 to 100 for pixelate and blur. Default 50."),
        },
        [*_BOX_REQ, "mode"],
    ),
    "spotlight": _variant(
        "spotlight",
        "Dim everything except the box.",
        {
            **_BOX,
            "shape": {"type": "string", "enum": ["rect", "ellipse"]},
            "dim": _num("How dark the rest gets, 0 to 1. Default 0.6."),
        },
        _BOX_REQ,
    ),
    "magnify": _variant(
        "magnify",
        "Show the box zoomed in, in a circle centered on (target_x, target_y).",
        {
            **_BOX,
            "target_x": _num("Center of the zoomed circle, x"),
            "target_y": _num("Center of the zoomed circle, y"),
            "zoom": _num("1 to 16. Default 2."),
            "color": _COLOR,
        },
        [*_BOX_REQ, "target_x", "target_y"],
    ),
    "sticker": _variant(
        "sticker",
        "An emoji or short stamp drawn to fill the box.",
        {**_BOX, "text": {"type": "string", "description": "One emoji or a short word"}},
        [*_BOX_REQ, "text"],
    ),
}

_ITEM = {"anyOf": [ITEM_SCHEMAS[k] for k in CREATABLE]}


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        },
    }


TOOLS: list[dict] = [
    _tool(
        "add_annotations",
        "Draw new annotations on the picture. Coordinates are pixels of the picture you see. "
        "All items of one request can go in a single call.",
        {"items": {"type": "array", "items": _ITEM}},
        ["items"],
    ),
    _tool(
        "replace_annotation",
        "Change an existing annotation: give its id and the complete new item. It keeps its id "
        "and its place in the stacking order.",
        {"id": {"type": "string"}, "item": _ITEM},
        ["id", "item"],
    ),
    _tool(
        "delete_annotations",
        "Remove annotations by id.",
        {"ids": {"type": "array", "items": {"type": "string"}}},
        ["ids"],
    ),
    _tool(
        "set_crop",
        "Crop the finished picture to this box. Annotations outside it are cut off.",
        dict(_BOX),
        list(_BOX_REQ),
    ),
    _tool("remove_crop", "Show the whole picture again.", {}, []),
    _tool(
        "find_text",
        "Find where text appears in the picture, read on this computer. Returns exact boxes; "
        "use them instead of estimating positions of text. Case is ignored.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "find_personal_info",
        "List boxes of personal details found on this computer: email addresses, phone "
        "numbers, card numbers, keys and similar.",
        {},
        [],
    ),
]

TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def tool_definitions(strict: bool = True) -> list[dict]:
    """The tools for a request. ``strict`` asks the API to keep arguments to the schemas
    (an extra guard; everything is checked here anyway)."""
    return [{**t, "strict": True} if strict else dict(t) for t in TOOLS]


# -- applying calls -----------------------------------------------------------------------------
@dataclass
class Context:
    """One message's worth of state while its tool calls are applied."""

    width: int  # original picture, pixels
    height: int
    scale: float = 1.0  # sent picture pixels per original pixel
    words: Callable[[], list] | None = None  # OCR words (grabbyhands.ocr.Word), lazily
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    removed: int = 0
    crop_changed: bool = False
    text_chars: int = 0
    created: int = 0  # items made in this message, never counted down (the limit's counter)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.replaced or self.removed or self.crop_changed)

    def summary(self) -> dict:
        return {
            "added": len(self.added),
            "changed": len(self.replaced),
            "removed": self.removed,
            "crop": self.crop_changed,
        }


@dataclass
class Result:
    content: str  # JSON text for the tool_result block
    is_error: bool = False


def _ok(data: object) -> Result:
    return Result(json.dumps(data, ensure_ascii=False))


def _err(message: str) -> Result:
    return Result(json.dumps({"error": message}), is_error=True)


def _finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _to_original(raw: object, ctx: Context) -> tuple[dict | None, str | None]:
    """A tool-call item in sent-picture pixels -> item data in original pixels, kept on the
    picture. Returns (data, None) or (None, reason)."""
    if not isinstance(raw, dict):
        return None, "an item must be an object"
    kind = raw.get("type")
    if kind not in CREATABLE:
        return None, f"unknown or not allowed type: {kind!r}"
    allowed = set(ITEM_SCHEMAS[kind]["properties"])
    extra = set(raw) - allowed
    if extra:
        return None, f"unknown fields for {kind}: {', '.join(sorted(extra))}"
    missing = [f for f in ITEM_SCHEMAS[kind]["required"] if f not in raw]
    if missing:
        return None, f"missing fields for {kind}: {', '.join(missing)}"
    data = dict(raw)
    scale = ctx.scale if ctx.scale > 0 else 1.0
    for name in COORD_FIELDS + SIZE_FIELDS:
        if name in data:
            if not _finite(data[name]):
                return None, f"{name} must be a number"
            data[name] = data[name] / scale
    for name in ("x0", "x1", "x", "tail_x", "target_x"):
        if name in data:
            data[name] = max(0.0, min(float(ctx.width), data[name]))
    for name in ("y0", "y1", "y", "tail_y", "target_y"):
        if name in data:
            data[name] = max(0.0, min(float(ctx.height), data[name]))
    for name in ("strength", "dim", "zoom", "number"):
        if name in data and not _finite(data[name]):
            return None, f"{name} must be a number"
    for name in TEXT_FIELDS:
        if name in data and not isinstance(data[name], str):
            return None, f"{name} must be text"
    flat = "x0" in data and (abs(data["x1"] - data["x0"]) < 1 or abs(data["y1"] - data["y0"]) < 1)
    if flat and kind not in ("arrow", "line"):
        return None, "the box is empty (on or outside the picture's edge?)"
    if (
        kind in ("arrow", "line")
        and math.hypot(data["x1"] - data["x0"], data["y1"] - data["y0"]) < 2
    ):
        return None, "the line has no length"
    return data, None


def _make_item(raw: object, ctx: Context, keep_id: str | None = None) -> tuple[Item | None, str]:
    data, reason = _to_original(raw, ctx)
    if data is None:
        return None, reason or "invalid item"
    text = data.get("text", "")
    if ctx.text_chars + len(text) > MAX_TEXT_PER_MESSAGE:
        return None, f"too much text in one request (limit {MAX_TEXT_PER_MESSAGE} characters)"
    data.pop("id", None)
    item = item_from_dict(data)
    if item is None:
        return None, "the item did not pass the editor's checks"
    if keep_id:
        item.id = keep_id
    elif ctx.created >= MAX_ITEMS_PER_MESSAGE:
        return None, f"limit of {MAX_ITEMS_PER_MESSAGE} items per request reached"
    ctx.text_chars += len(text)
    return item, ""


def apply_call(doc: Document, name: str, args: object, ctx: Context) -> Result:
    """Apply one tool call to ``doc``. Never raises for bad input: errors go back to Claude."""
    if name not in TOOL_NAMES:
        return _err(f"no such tool: {name}")
    if not isinstance(args, dict):
        return _err("arguments must be an object")
    try:
        handler = _HANDLERS[name]
        return handler(doc, args, ctx)
    except (TypeError, ValueError, KeyError) as err:  # defensive: report, don't crash
        return _err(f"could not apply: {type(err).__name__}")


def _add(doc: Document, args: dict, ctx: Context) -> Result:
    items = args.get("items")
    if not isinstance(items, list) or not items:
        return _err("items must be a non-empty list")
    added: list[dict] = []
    errors: list[dict] = []
    for index, raw in enumerate(items):
        if ctx.created >= MAX_ITEMS_PER_MESSAGE:
            errors.append({"index": index, "error": f"limit of {MAX_ITEMS_PER_MESSAGE} reached"})
            continue
        item, reason = _make_item(raw, ctx)
        if item is None:
            errors.append({"index": index, "error": reason})
            continue
        doc.items.append(item)
        ctx.created += 1
        ctx.added.append(item.id)
        added.append({"id": item.id, "type": item.type})
    result = {"added": added}
    if errors:
        result["errors"] = errors
    return Result(json.dumps(result, ensure_ascii=False), is_error=not added)


def _replace(doc: Document, args: dict, ctx: Context) -> Result:
    item_id = args.get("id")
    old = doc.get(item_id) if isinstance(item_id, str) else None
    if old is None:
        return _err(f"no annotation with id {item_id!r}")
    if isinstance(old, PathItem):
        return _err("hand-drawn strokes can be deleted but not changed")
    item, reason = _make_item(args.get("item"), ctx, keep_id=old.id)
    if item is None:
        return _err(reason)
    doc.items[doc.items.index(old)] = item
    if old.id not in ctx.added:
        ctx.replaced.append(old.id)
    return _ok({"replaced": old.id, "type": item.type})


def _delete(doc: Document, args: dict, ctx: Context) -> Result:
    ids = args.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return _err("ids must be a list of ids")
    wanted = set(ids)
    existing = {i.id for i in doc.items}
    unknown = sorted(wanted - existing)
    doc.items = [i for i in doc.items if i.id not in wanted]
    gone = len(wanted & existing)
    # Something added earlier in this same message and deleted again was never "removed".
    ctx.removed += len((wanted & existing) - set(ctx.added))
    ctx.added = [i for i in ctx.added if i not in wanted]
    ctx.replaced = [i for i in ctx.replaced if i not in wanted]
    if not gone:
        return _err("none of those ids exist")
    result: dict = {"removed": gone}
    if unknown:
        result["not_found"] = unknown
    return _ok(result)


def _set_crop(doc: Document, args: dict, ctx: Context) -> Result:
    data, reason = _to_original({"type": "rect", **args}, ctx)
    if data is None:
        return _err(reason or "invalid box")
    x0, x1 = sorted((data["x0"], data["x1"]))
    y0, y1 = sorted((data["y0"], data["y1"]))
    crop = (round(x0), round(y0), round(x1 - x0), round(y1 - y0))
    if crop[2] < 8 or crop[3] < 8:
        return _err("the crop is too small")
    doc.crop = crop
    ctx.crop_changed = True
    return _ok({"crop": "set"})


def _remove_crop(doc: Document, args: dict, ctx: Context) -> Result:
    if doc.crop is None:
        return _ok({"crop": "there was none"})
    doc.crop = None
    ctx.crop_changed = True
    return _ok({"crop": "removed"})


def _box_out(x0: float, y0: float, x1: float, y1: float, ctx: Context) -> dict:
    s = ctx.scale if ctx.scale > 0 else 1.0
    return {"x0": round(x0 * s), "y0": round(y0 * s), "x1": round(x1 * s), "y1": round(y1 * s)}


def _words(ctx: Context) -> list:
    if ctx.words is None:
        raise LookupError("text recognition is not available on this computer")
    return ctx.words()


def _find_text(doc: Document, args: dict, ctx: Context) -> Result:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return _err("query must be text")
    try:
        words = _words(ctx)
    except LookupError as err:
        return _err(str(err))
    needle = query.casefold().split()
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((w.block, w.par, w.line), []).append(w)
    matches = []
    for line in lines.values():
        tokens = [w.text.casefold() for w in line]
        for start in range(len(line)):
            window = line[start : start + len(needle)]
            if len(window) < len(needle):
                break
            here = tokens[start : start + len(needle)]
            if all(n in t for n, t in zip(needle, here, strict=True)):
                x0 = min(w.left for w in window)
                y0 = min(w.top for w in window)
                x1 = max(w.left + w.width for w in window)
                y1 = max(w.top + w.height for w in window)
                matches.append(
                    {"text": " ".join(w.text for w in window), **_box_out(x0, y0, x1, y1, ctx)}
                )
            if len(matches) >= MAX_FIND_RESULTS:
                break
    return _ok({"matches": matches[:MAX_FIND_RESULTS]})


def _find_personal(doc: Document, args: dict, ctx: Context) -> Result:
    from grabbyhands.redact import find_sensitive

    try:
        words = _words(ctx)
    except LookupError as err:
        return _err(str(err))
    found = [{"kind": f.kind, **_box_out(*f.box, ctx)} for f in find_sensitive(words)][
        :MAX_FIND_RESULTS
    ]
    return _ok({"found": found})


_HANDLERS = {
    "add_annotations": _add,
    "replace_annotation": _replace,
    "delete_annotations": _delete,
    "set_crop": _set_crop,
    "remove_crop": _remove_crop,
    "find_text": _find_text,
    "find_personal_info": _find_personal,
}


# -- describing the document to Claude ------------------------------------------------------------
def describe(doc: Document, ctx: Context) -> dict:
    """The current annotations in sent-picture pixels, for the next request."""
    s = ctx.scale if ctx.scale > 0 else 1.0
    items = []
    for item in doc.items:
        if isinstance(item, PathItem):
            x0, y0, x1, y1 = item.bounds()
            items.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "drawn_by_hand": True,
                    "bounds": _box_out(x0, y0, x1, y1, ctx),
                }
            )
            continue
        data = item.to_dict()
        out: dict = {}
        for key, value in data.items():
            if key in COORD_FIELDS or key in SIZE_FIELDS:
                out[key] = round(value * s, 1)
            elif key in ("opacity",) and value == 1.0:
                continue
            else:
                out[key] = value
        items.append(out)
    crop = None
    if doc.crop:
        x, y, w, h = doc.crop
        crop = _box_out(x, y, x + w, y + h, ctx)
    return {
        "picture": {"width": round(ctx.width * s), "height": round(ctx.height * s)},
        "crop": crop,
        "annotations": items,
    }
