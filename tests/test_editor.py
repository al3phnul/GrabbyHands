# SPDX-License-Identifier: GPL-3.0-or-later
"""Editor model and rendering, checked with pixel probes (no GTK needed)."""

import io

import pytest
from PIL import Image, ImageDraw, ImageStat

from grabbyhands.editor.document import (
    ArrowItem,
    CalloutItem,
    Document,
    EllipseItem,
    EraserItem,
    HighlighterItem,
    MagnifyItem,
    PenItem,
    RectItem,
    RedactItem,
    SpotlightItem,
    StepItem,
    StickerItem,
    TextItem,
    UndoStack,
)
from grabbyhands.editor.render import EffectSources, render_document

RED = "#ff0000"


def checker(w=200, h=120) -> Image.Image:
    """Busy test image: 4px black/white checkerboard plus a gray band."""
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            if (x // 4 + y // 4) % 2:
                draw.rectangle((x, y, x + 3, y + 3), fill="black")
    draw.rectangle((0, 100, w, h), fill=(128, 128, 128))
    return img


def plain(w=200, h=120, color="white") -> Image.Image:
    return Image.new("RGB", (w, h), color)


def render(items, base=None, **kw) -> Image.Image:
    return render_document(Document(items=list(items), **kw), base or plain())


# -- model -----------------------------------------------------------------------------
def test_json_round_trip_keeps_every_type():
    doc = Document(
        items=[
            ArrowItem(x0=1, y0=2, x1=30, y1=40),
            RectItem(x0=5, y0=5, x1=50, y1=30, fill="translucent"),
            EllipseItem(x0=0, y0=0, x1=10, y1=10),
            PenItem(points=[[1, 1], [5, 5]]),
            HighlighterItem(points=[[1, 1], [9, 1]]),
            EraserItem(points=[[3, 3]]),
            TextItem(x=4, y=4, text="hi ✓", size=18),
            CalloutItem(x0=10, y0=10, x1=90, y1=40, text="look", tail_x=5, tail_y=60),
            StepItem(x=20, y=20, number=3),
            RedactItem(x0=0, y0=0, x1=9, y1=9, mode="blur", strength=12),
            SpotlightItem(x0=0, y0=0, x1=9, y1=9, shape="ellipse"),
            MagnifyItem(x0=0, y0=0, x1=9, y1=9, target_x=50, target_y=50, zoom=3),
            StickerItem(x0=0, y0=0, x1=40, y1=40, text="🎉"),
        ],
        crop=(1, 2, 30, 40),
    )
    again = Document.from_dict(doc.to_dict())
    assert again.to_dict() == doc.to_dict()
    assert [i.type for i in again.items] == [i.type for i in doc.items]
    assert again.crop == (1, 2, 30, 40)


def test_unknown_items_are_skipped():
    doc = Document.from_dict({"items": [{"type": "hologram"}, {"type": "line", "x1": 3}]})
    assert [i.type for i in doc.items] == ["line"]


def test_undo_redo():
    doc = Document()
    undo = UndoStack(doc)
    undo.checkpoint()
    doc.items.append(RectItem(x0=0, y0=0, x1=5, y1=5))
    undo.checkpoint()
    doc.items[0].move(10, 0)
    assert doc.items[0].x0 == 10
    assert undo.undo() and doc.items[0].x0 == 0
    assert undo.undo() and doc.items == []
    assert not undo.undo()
    assert undo.redo() and len(doc.items) == 1
    assert undo.redo() and doc.items[0].x0 == 10
    assert not undo.can_redo


def test_hit_testing_and_handles():
    outline = RectItem(x0=10, y0=10, x1=100, y1=100, width=4)
    assert outline.hit_test(10, 50, 3) and not outline.hit_test(55, 55, 3)
    filled = RectItem(x0=10, y0=10, x1=100, y1=100, fill="solid")
    assert filled.hit_test(55, 55, 3)
    line = ArrowItem(x0=0, y0=0, x1=100, y1=0, width=4)
    assert line.hit_test(50, 3, 2) and not line.hit_test(50, 20, 2)
    doc = Document(items=[filled, line, EraserItem(points=[[50, 0]])])
    assert doc.item_at(50, 1) is line  # top-most selectable; erasers are never picked
    filled.set_handle("se", 120, 130)
    assert filled.rect() == (10, 10, 110, 120)
    assert set(CalloutItem().handles()) >= {"nw", "se", "tail"}


def test_step_numbers_continue():
    doc = Document(items=[StepItem(number=1), StepItem(number=4)])
    assert doc.next_step_number() == 5


# -- rendering ---------------------------------------------------------------------------
def test_arrow_and_line_paint_their_color():
    out = render([ArrowItem(x0=10, y0=60, x1=190, y1=60, color=RED, width=6)])
    assert out.getpixel((60, 60)) == (255, 0, 0)
    assert out.getpixel((60, 20)) == (255, 255, 255)
    assert out.getpixel((186, 60))[0] > 200  # arrow head reaches the tip


def test_rect_outline_vs_fill():
    out = render([RectItem(x0=20, y0=20, x1=120, y1=90, color=RED, width=4)])
    assert out.getpixel((20, 50)) == (255, 0, 0)
    assert out.getpixel((70, 55)) == (255, 255, 255)
    solid = render([RectItem(x0=20, y0=20, x1=120, y1=90, color=RED, fill="solid")])
    assert solid.getpixel((70, 55)) == (255, 0, 0)


def test_highlighter_multiplies():
    base = plain()
    ImageDraw.Draw(base).rectangle((100, 0, 199, 119), fill="black")
    out = render([HighlighterItem(points=[[10, 60], [190, 60]], color="#ffe14d", width=20)], base)
    assert out.getpixel((50, 60)) == (255, 225, 77)  # white turns highlighter yellow
    assert out.getpixel((150, 60)) == (0, 0, 0)  # black text stays black


@pytest.mark.parametrize("mode", ["pixelate", "blur", "solid"])
def test_redaction_destroys_detail(mode):
    base = checker()
    box = (40, 20, 160, 90)
    out = render([RedactItem(x0=box[0], y0=box[1], x1=box[2], y1=box[3], mode=mode)], base)
    before = ImageStat.Stat(base.crop(box).convert("L")).stddev[0]
    after = ImageStat.Stat(out.crop(box).convert("L")).stddev[0]
    assert after < before * 0.25, (mode, before, after)
    if mode == "solid":
        assert out.crop(box).getextrema() == ((0, 0), (0, 0), (0, 0))
    assert out.getpixel((5, 5)) == base.getpixel((5, 5))  # outside untouched


def test_redaction_survives_jpeg_export():
    from grabbyhands import export

    base = checker()
    out = render([RedactItem(x0=40, y0=20, x1=160, y1=90, mode="solid")], base)
    jpeg = Image.open(io.BytesIO(export.encode(out, "jpg", 90))).convert("L")
    assert ImageStat.Stat(jpeg.crop((48, 28, 152, 82))).extrema[0][1] < 12


def test_eraser_removes_paint_but_not_the_screenshot():
    base = checker()
    rect = RectItem(x0=0, y0=0, x1=200, y1=120, color=RED, fill="solid")
    eraser = EraserItem(points=[[20, 50], [180, 50]], width=20)
    out = render([rect, eraser], base)
    for x in (30, 81, 150):
        assert out.getpixel((x, 50)) == base.getpixel((x, 50))  # exact original pixels back
    assert out.getpixel((30, 100)) == (255, 0, 0)  # paint outside the stroke stays


def test_eraser_erases_effects_too():
    base = checker()
    redact = RedactItem(x0=0, y0=0, x1=200, y1=100, mode="solid")
    out = render([redact, EraserItem(points=[[10, 40], [190, 40]], width=16)], base)
    assert out.getpixel((61, 40)) == base.getpixel((61, 40))
    assert out.getpixel((61, 80)) == (0, 0, 0)


def test_items_after_an_eraser_are_drawn_normally():
    out = render(
        [
            EraserItem(points=[[0, 60], [200, 60]], width=30),
            PenItem(points=[[0, 60], [200, 60]], color=RED, width=6),
        ]
    )
    assert out.getpixel((100, 60)) == (255, 0, 0)


def test_spotlight_dims_outside_only_once():
    base = plain(color=(200, 200, 200))
    a = SpotlightItem(x0=20, y0=20, x1=100, y1=100, dim=0.5)
    b = SpotlightItem(x0=60, y0=20, x1=180, y1=100, dim=0.5)
    out = render([a, b], base)
    assert out.getpixel((40, 50)) == (200, 200, 200)
    assert out.getpixel((80, 50)) == (200, 200, 200)  # overlap stays bright
    assert out.getpixel((5, 5)) == (100, 100, 100)  # outside dimmed exactly once


def test_magnify_draws_ring_and_zoomed_pixels():
    base = plain()
    ImageDraw.Draw(base).rectangle((10, 10, 14, 14), fill="blue")
    item = MagnifyItem(x0=8, y0=8, x1=16, y1=16, target_x=100, target_y=60, zoom=4, color=RED)
    out = render([item], base)
    assert out.getpixel((100, 60))[2] > 200 and out.getpixel((100, 60))[0] < 60  # zoomed blue
    r = item.radius()
    assert out.getpixel((round(100 + r), 60))[0] > 200  # red ring


def test_text_step_callout_sticker_paint_something():
    out = render(
        [
            TextItem(x=10, y=10, text="Hello", size=24, color=RED),
            StepItem(x=150, y=30, number=7, color=RED),
            CalloutItem(x0=20, y0=60, x1=120, y1=110, tail_x=180, tail_y=110, text="hi"),
            StickerItem(x0=150, y0=70, x1=190, y1=110, text="★", color="#0000ff"),
        ]
    )
    assert any(out.getpixel((x, 22))[1] < 100 for x in range(10, 70))  # red text pixels
    assert out.getpixel((150, 30))[0] > 180 or out.getpixel((150, 30)) == (255, 255, 255)
    assert out.getpixel((150 + 18, 30)) != (255, 255, 255)  # circle body
    assert out.getpixel((30, 66)) != (255, 255, 255)  # bubble
    blue = [out.getpixel((x, 90)) for x in range(150, 190)]
    assert any(p[2] > 150 and p[0] < 100 for p in blue)


def test_crop_size():
    assert render([], crop=(10, 20, 50, 40)).size == (50, 40)


def test_old_documents_with_beautify_still_load():
    doc = Document.from_dict({"version": 2, "beautify": {"padding": 30}, "items": []})
    assert doc.empty and "beautify" not in doc.to_dict()


def test_effect_sources_are_cached():
    src = EffectSources(checker())
    assert src.get("highlight", "#ffe14d") is src.get("highlight", "#ffe14d")
    assert src.region("blur", 40, 0, 0, 50, 50) is src.region("blur", 40, 0, 0, 50, 50)


def noisy(w=200, h=120) -> Image.Image:
    """Seeded random noise: detail at every scale, no grid for blocks to line up with."""
    import random

    rng = random.Random(7)
    return Image.frombytes("L", (w, h), bytes(rng.randrange(256) for _ in range(w * h))).convert(
        "RGB"
    )


def _detail(mode: str, percent: float) -> float:
    base = noisy()
    box = (40, 20, 160, 90)
    item = RedactItem(x0=box[0], y0=box[1], x1=box[2], y1=box[3], mode=mode, strength=percent)
    return ImageStat.Stat(render([item], base).crop(box).convert("L")).stddev[0]


@pytest.mark.parametrize("mode", ["pixelate", "blur"])
def test_redaction_strength_is_gradual(mode):
    original = ImageStat.Stat(noisy().crop((40, 20, 160, 90)).convert("L")).stddev[0]
    levels = [_detail(mode, p) for p in (1, 10, 35, 70, 100)]
    assert levels[0] > original * 0.9, levels  # 1% barely changes anything
    assert all(a >= b - 0.5 for a, b in zip(levels, levels[1:], strict=False)), levels
    assert levels[-1] < original * 0.25, levels  # 100% still hides fine detail


def test_strength_maximums_are_moderate():
    from grabbyhands.editor.render import blur_radius, pixel_block

    assert pixel_block(0) == 1 and pixel_block(100) == 20 and pixel_block(50) == 10
    assert blur_radius(0) == 0 and blur_radius(100) == 12


def test_version1_redactions_never_get_weaker():
    from grabbyhands.editor.render import blur_radius, pixel_block

    v1 = {
        "version": 1,
        "items": [
            {"type": "redact", "mode": "pixelate", "strength": 14},
            {"type": "redact", "mode": "blur", "strength": 6},
        ],
    }
    pix, blur = Document.from_dict(v1).items
    assert pixel_block(pix.strength) >= 14
    assert blur_radius(blur.strength) >= 8  # version 1 drew a 6 with its 8 px minimum
    assert Document.from_dict(Document.from_dict(v1).to_dict()).items[0].strength == pix.strength
