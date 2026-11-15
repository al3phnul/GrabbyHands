# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart Edit's tools: every call is checked like a saved document, and nothing else happens."""

import json
import math
import random

import pytest
from PIL import Image

from grabbyhands.editor.document import (
    ArrowItem,
    Document,
    PenItem,
    RedactItem,
    TextItem,
)
from grabbyhands.editor.render import render_document
from grabbyhands.ocr import Word
from grabbyhands.smartedit import tools
from grabbyhands.smartedit.tools import (
    MAX_ITEMS_PER_MESSAGE,
    MAX_TEXT_PER_MESSAGE,
    Context,
    apply_call,
    describe,
    tool_definitions,
)


def ctx(scale=1.0, words=None, w=400, h=300):
    return Context(width=w, height=h, scale=scale, words=words)


def call(doc, name, args, c=None):
    c = c or ctx()
    return apply_call(doc, name, args, c), c


def word(text, left, top, width=40, height=12, line=1, block=1):
    return Word(text, left, top, width, height, 90.0, block, 1, line)


# -- schemas --------------------------------------------------------------------------------
def _objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


def test_every_object_in_every_schema_is_closed():
    for tool in tool_definitions():
        assert tool["strict"] is True
        for obj in _objects(tool["input_schema"]):
            assert obj["additionalProperties"] is False, tool["name"]
            assert set(obj["required"]) <= set(obj["properties"]), tool["name"]


def test_tool_set_is_exactly_the_editing_surface():
    names = {t["name"] for t in tool_definitions()}
    assert names == {
        "add_annotations",
        "replace_annotation",
        "delete_annotations",
        "set_crop",
        "remove_crop",
        "find_text",
        "find_personal_info",
    }
    assert "strict" not in tool_definitions(strict=False)[0]


def test_schemas_offer_no_hand_drawn_types():
    variants = tool_definitions()[0]["input_schema"]["properties"]["items"]["items"]["anyOf"]
    kinds = {v["properties"]["type"]["const"] for v in variants}
    assert kinds == set(tools.CREATABLE)
    assert not kinds & {"pen", "highlighter", "eraser"}


# -- add ------------------------------------------------------------------------------------
def test_add_scales_sent_pixels_to_the_original():
    doc = Document()
    res, c = call(
        doc,
        "add_annotations",
        {"items": [{"type": "arrow", "x0": 10, "y0": 20, "x1": 100, "y1": 50, "width": 2}]},
        ctx(scale=0.5),
    )
    assert not res.is_error
    (item,) = doc.items
    assert isinstance(item, ArrowItem)
    assert (item.x0, item.y0, item.x1, item.y1) == (20, 40, 200, 100)
    assert item.width == 4
    assert c.added == [item.id]
    assert json.loads(res.content)["added"][0] == {"id": item.id, "type": "arrow"}


def test_add_keeps_items_on_the_picture():
    doc = Document()
    call(
        doc,
        "add_annotations",
        {"items": [{"type": "rect", "x0": -500, "y0": -20, "x1": 9999, "y1": 100}]},
    )
    (item,) = doc.items
    assert item.bounds() == (0, 0, 400, 100)


@pytest.mark.parametrize(
    "bad, reason",
    [
        ({"type": "pen", "points": [[0, 0], [5, 5]]}, "not allowed"),
        ({"type": "script", "code": "import os"}, "not allowed"),
        ({"type": "rect", "x0": 0, "y0": 0, "x1": 5, "y1": 5, "onclick": "x"}, "unknown fields"),
        ({"type": "rect", "x0": 0, "y0": 0, "x1": 5}, "missing"),
        ({"type": "rect", "x0": float("nan"), "y0": 0, "x1": 5, "y1": 5}, "number"),
        ({"type": "rect", "x0": True, "y0": 0, "x1": 5, "y1": 5}, "number"),
        ({"type": "rect", "x0": "10", "y0": 0, "x1": 5, "y1": 5}, "number"),
        ({"type": "rect", "x0": 500, "y0": 0, "x1": 600, "y1": 5}, "empty"),
        ({"type": "text", "x": 1, "y": 1, "text": 42}, "text"),
        ({"type": "arrow", "x0": 1, "y0": 1, "x1": 1, "y1": 1}, "no length"),
        ("rect", "object"),
    ],
)
def test_add_rejects_bad_items_with_a_reason(bad, reason):
    doc = Document()
    res, c = call(doc, "add_annotations", {"items": [bad]})
    assert res.is_error
    assert reason in json.loads(res.content)["errors"][0]["error"]
    assert doc.items == [] and c.added == []


def test_partial_success_applies_the_good_items():
    doc = Document()
    res, _ = call(
        doc,
        "add_annotations",
        {
            "items": [
                {"type": "step", "x": 50, "y": 50, "number": 1},
                {"type": "nope"},
                {"type": "redact", "x0": 10, "y0": 10, "x1": 60, "y1": 30, "mode": "solid"},
            ]
        },
    )
    data = json.loads(res.content)
    assert not res.is_error
    assert len(data["added"]) == 2 and data["errors"][0]["index"] == 1
    assert [i.type for i in doc.items] == ["step", "redact"]


def test_bad_choices_fall_back_to_safe_values():
    doc = Document()
    call(
        doc,
        "add_annotations",
        {"items": [{"type": "redact", "x0": 0, "y0": 0, "x1": 50, "y1": 50, "mode": "invisible"}]},
    )
    (item,) = doc.items
    assert isinstance(item, RedactItem) and item.mode == "solid"


def test_item_limit_per_message():
    doc = Document()
    c = ctx()
    many = [{"type": "step", "x": 10, "y": 10, "number": n} for n in range(80)]
    res, _ = call(doc, "add_annotations", {"items": many}, c)
    assert len(doc.items) == MAX_ITEMS_PER_MESSAGE
    assert "limit" in json.loads(res.content)["errors"][0]["error"]
    # deleting does not make room for more in the same message
    call(doc, "delete_annotations", {"ids": [doc.items[0].id]}, c)
    res, _ = call(doc, "add_annotations", {"items": many[:1]}, c)
    assert res.is_error


def test_text_limit_per_message():
    doc = Document()
    c = ctx()
    long = "x" * (MAX_TEXT_PER_MESSAGE - 10)
    call(doc, "add_annotations", {"items": [{"type": "text", "x": 1, "y": 1, "text": long}]}, c)
    res, _ = call(
        doc, "add_annotations", {"items": [{"type": "text", "x": 1, "y": 1, "text": "y" * 20}]}, c
    )
    assert res.is_error and len(doc.items) == 1


# -- replace / delete -------------------------------------------------------------------------
def test_replace_keeps_id_and_stacking_order():
    first, second = ArrowItem(x0=0, y0=0, x1=50, y1=50), TextItem(x=5, y=5, text="hi")
    doc = Document(items=[first, second])
    res, c = call(
        doc,
        "replace_annotation",
        {"id": first.id, "item": {"type": "rect", "x0": 1, "y0": 1, "x1": 80, "y1": 40}},
    )
    assert not res.is_error
    assert [i.type for i in doc.items] == ["rect", "text"]
    assert doc.items[0].id == first.id
    assert c.replaced == [first.id]


def test_replace_refuses_hand_drawn_and_unknown_ids():
    pen = PenItem(points=[[1, 1], [9, 9]])
    doc = Document(items=[pen])
    item = {"type": "rect", "x0": 1, "y0": 1, "x1": 8, "y1": 8}
    assert call(doc, "replace_annotation", {"id": pen.id, "item": item})[0].is_error
    assert call(doc, "replace_annotation", {"id": "nope", "item": item})[0].is_error
    assert doc.items == [pen]


def test_delete_and_summary():
    a, b = ArrowItem(x0=0, y0=0, x1=50, y1=50), TextItem(x=5, y=5, text="hi")
    doc = Document(items=[a, b])
    c = ctx()
    res, _ = call(doc, "delete_annotations", {"ids": [a.id, "ghost"]}, c)
    assert json.loads(res.content) == {"removed": 1, "not_found": ["ghost"]}
    assert doc.items == [b]
    assert c.summary() == {"added": 0, "changed": 0, "removed": 1, "crop": False}
    assert call(doc, "delete_annotations", {"ids": ["ghost"]})[0].is_error


def test_added_then_deleted_in_one_message_counts_as_nothing():
    doc = Document()
    c = ctx()
    res, _ = call(
        doc, "add_annotations", {"items": [{"type": "step", "x": 5, "y": 5, "number": 1}]}, c
    )
    new_id = json.loads(res.content)["added"][0]["id"]
    call(doc, "delete_annotations", {"ids": [new_id]}, c)
    assert c.summary() == {"added": 0, "changed": 0, "removed": 0, "crop": False}
    assert not c.changed


# -- crop -----------------------------------------------------------------------------------
def test_crop_set_scaled_and_removed():
    doc = Document()
    c = ctx(scale=0.5)
    assert not call(doc, "set_crop", {"x0": 10, "y0": 10, "x1": 110, "y1": 60}, c)[0].is_error
    assert doc.crop == (20, 20, 200, 100)
    assert c.crop_changed
    assert call(doc, "set_crop", {"x0": 10, "y0": 10, "x1": 12, "y1": 12})[0].is_error
    call(doc, "remove_crop", {})
    assert doc.crop is None


# -- lookups ----------------------------------------------------------------------------------
def test_find_text_returns_exact_boxes_in_sent_pixels():
    words = [word("Click", 100, 40), word("Save", 150, 40, width=50), word("Cancel", 220, 40)]
    doc = Document()
    res, _ = call(doc, "find_text", {"query": "save"}, ctx(scale=0.5, words=lambda: words))
    assert json.loads(res.content)["matches"] == [
        {"text": "Save", "x0": 75, "y0": 20, "x1": 100, "y1": 26}
    ]
    res, _ = call(doc, "find_text", {"query": "click save"}, ctx(words=lambda: words))
    (match,) = json.loads(res.content)["matches"]
    assert match["text"] == "Click Save" and match["x0"] == 100 and match["x1"] == 200
    assert doc.items == []  # lookups never change the document


def test_lookups_without_ocr_say_so():
    for name, args in (("find_text", {"query": "x"}), ("find_personal_info", {})):
        res, _ = call(Document(), name, args, ctx(words=None))
        assert res.is_error and "not available" in res.content


def test_find_personal_info_uses_auto_redact():
    words = [word("Mail", 10, 10), word("someone@example.com", 60, 10, width=150)]
    res, _ = call(Document(), "find_personal_info", {}, ctx(words=lambda: words))
    (found,) = json.loads(res.content)["found"]
    assert found["kind"] == "email" and found["x0"] <= 60 and found["x1"] >= 210


# -- robustness -------------------------------------------------------------------------------
def test_unknown_tool_and_bad_arguments():
    assert apply_call(Document(), "run_python", {"code": "1"}, ctx()).is_error
    assert apply_call(Document(), "add_annotations", "items", ctx()).is_error
    assert apply_call(Document(), "add_annotations", {"items": []}, ctx()).is_error


def _junk(rng, depth=0):
    choices = [
        lambda: rng.choice([0, -1, 1e308, -1e308, float("inf"), float("nan"), 3.5, 10**30]),
        lambda: rng.choice(["", "x" * 5000, "#zzz", "rect", "arrow", "\x00"]),
        lambda: rng.choice([None, True, False]),
    ]
    if depth < 2:
        choices.append(lambda: [_junk(rng, depth + 1) for _ in range(rng.randint(0, 3))])
        choices.append(
            lambda: {
                rng.choice(["type", "x0", "y0", "x1", "y1", "text", "mode", "zoom", "id"]): _junk(
                    rng, depth + 1
                )
                for _ in range(rng.randint(0, 6))
            }
        )
    return rng.choice(choices)()


def test_random_junk_never_crashes_and_leaves_a_drawable_document():
    rng = random.Random(1234)
    doc = Document(items=[ArrowItem(x0=0, y0=0, x1=50, y1=50)])
    image = Image.new("RGB", (400, 300), "white")
    kinds = list(tools.CREATABLE)
    for _ in range(600):
        name = rng.choice(sorted(tools.TOOL_NAMES) + ["nope"])
        if rng.random() < 0.5:
            item = {"type": rng.choice(kinds)}
            for key in (
                "x0",
                "y0",
                "x1",
                "y1",
                "x",
                "y",
                "tail_x",
                "tail_y",
                "target_x",
                "target_y",
                "size",
                "width",
                "zoom",
                "strength",
                "dim",
                "number",
            ):
                if rng.random() < 0.6:
                    item[key] = _junk(rng, 2) if rng.random() < 0.3 else rng.uniform(-50, 900)
            if rng.random() < 0.5:
                item["text"] = rng.choice(["ok", "😀", "x" * 2000])
            args = {
                "items": [item],
                "id": rng.choice([i.id for i in doc.items] or ["x"]),
                "item": item,
                "ids": [],
                "query": "x",
            }
        else:
            args = _junk(rng)
        apply_call(doc, name, args, ctx(scale=rng.choice([0.25, 0.5, 1.0])))
    for item in doc.items:
        for value in vars(item).values():
            if isinstance(value, float):
                assert math.isfinite(value)
    render_document(doc, image)  # must not raise


# -- describe -------------------------------------------------------------------------------------
def test_describe_uses_sent_pixels():
    doc = Document(
        items=[ArrowItem(x0=20, y0=40, x1=200, y1=100), PenItem(points=[[10, 10], [30, 50]])],
        crop=(0, 0, 200, 100),
    )
    out = describe(doc, ctx(scale=0.5))
    assert out["picture"] == {"width": 200, "height": 150}
    arrow, pen = out["annotations"]
    assert (arrow["x0"], arrow["y1"]) == (10, 50)
    assert pen["drawn_by_hand"] is True and "points" not in pen
    assert out["crop"] == {"x0": 0, "y0": 0, "x1": 100, "y1": 50}


def test_items_get_the_editor_style_unless_given():
    doc = Document()
    c = Context(
        width=400, height=300, scale=0.5, style={"color": "#3b82f6", "width": 6, "size": 30}
    )
    call(
        doc,
        "add_annotations",
        {
            "items": [
                {"type": "arrow", "x0": 1, "y0": 1, "x1": 50, "y1": 50},
                {"type": "text", "x": 1, "y": 1, "text": "hi", "color": "#ff0000", "size": 10},
                {"type": "redact", "x0": 1, "y0": 1, "x1": 50, "y1": 50, "mode": "blur"},
            ]
        },
        c,
    )
    arrow, text, redact = doc.items
    assert (arrow.color, arrow.width) == ("#3b82f6", 6)  # style is already in original pixels
    assert (text.color, text.size) == ("#ff0000", 20)  # given: scaled from sent pixels
    assert redact.mode == "blur"
