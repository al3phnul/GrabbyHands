# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests: one test (or a small group) per bug.

Everything here is pure logic on temporary folders. Nothing reads or writes real settings,
the real history, the clipboard or D-Bus; behaviour that needs windows or settings is
covered by steps in tests/gui_smoke.py instead.
"""

import io
import json

import pytest
from PIL import Image, ImageStat

from grabbyhands.editor import render as render_mod
from grabbyhands.editor.document import (
    Document,
    RectItem,
    RedactItem,
    item_from_dict,
)
from grabbyhands.editor.render import EffectSources, blur_radius, pixel_block, render_document


def noise(w=200, h=120, seed=7) -> Image.Image:
    import random

    rng = random.Random(seed)
    return Image.frombytes("RGB", (w, h), bytes(rng.randrange(256) for _ in range(w * h * 3)))


# -- documents ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "mode,old,at_least",
    [
        ("blur", 14, 14.0),  # the version-1 default used to come out as 12 px
        ("blur", 40, 40.0),
        ("blur", 6, 8.0),  # version 1 never blurred with less than 8 px
        ("pixelate", 14, 14),
        ("pixelate", 30, 30),  # used to be capped at today's 20 px
        ("pixelate", 40, 40),
        ("pixelate", 3, 6),  # version 1 never used blocks under 6 px
    ],
)
def test_version1_strengths_keep_their_full_strength(mode, old, at_least):
    v1 = {"version": 1, "items": [{"type": "redact", "mode": mode, "strength": old}]}
    item = Document.from_dict(v1).items[0]
    drawn = blur_radius(item.strength) if mode == "blur" else pixel_block(item.strength)
    assert drawn >= at_least, (mode, old, item.strength, drawn)
    # saving and loading again (now as version 2) must not convert a second time
    again = Document.from_dict(json.loads(json.dumps(Document(items=[item]).to_dict())))
    assert again.items[0].strength == item.strength


def test_version1_default_strength_applies_when_the_file_has_none():
    doc = Document.from_dict({"version": 1, "items": [{"type": "redact", "mode": "blur"}]})
    assert blur_radius(doc.items[0].strength) >= 14  # the version-1 default


def test_strength_scale_constants_agree():
    from grabbyhands.editor import document

    assert pixel_block(100) == document.MAX_PIXEL_BLOCK
    assert blur_radius(100) == document.MAX_BLUR_RADIUS
    assert pixel_block(10_000) == pixel_block(document.MAX_STRENGTH)  # bounded all the same


DAMAGED_ITEMS = [
    5,
    "arrow",
    None,
    [],
    {"type": ["arrow"]},
    {"type": "hologram", "x0": 1},
    {"type": "arrow", "x0": "left"},
    {"type": "arrow", "x0": None},
    {"type": "arrow", "x0": float("nan")},
    {"type": "arrow", "y1": float("inf")},
    {"type": "arrow", "x0": True},
    {"type": "arrow", "x0": 1e300},
    {"type": "pen", "points": 5},
    {"type": "pen", "points": [[1, 2], [3]]},
    {"type": "pen", "points": [[1, "2"]]},
    {"type": "pen", "points": [None]},
    {"type": "text", "text": 5},
    {"type": "step", "number": "two"},
]


@pytest.mark.parametrize("raw", DAMAGED_ITEMS, ids=repr)
def test_damaged_items_are_left_out_not_fatal(raw):
    assert item_from_dict(raw) is None
    good = {"type": "rect", "x0": 5, "y0": 5, "x1": 50, "y1": 40, "fill": "solid"}
    doc = Document.from_dict({"version": 2, "items": [raw, good]})
    assert [i.type for i in doc.items] == ["rect"]
    render_document(doc, Image.new("RGB", (80, 60), "white"))  # and it still draws


def test_odd_values_are_made_safe_to_draw():
    doc = Document.from_dict(
        {
            "version": 2,
            "crop": [1, 2, 3],
            "items": [
                {"type": "rect", "x0": 1, "y0": 1, "x1": 30, "y1": 30, "fill": "tartan"},
                {"type": "rect", "x0": 1, "y0": 1, "x1": 30, "y1": 30, "color": "chartreuse"},
                {"type": "spotlight", "x0": 1, "y0": 1, "x1": 30, "y1": 30, "shape": "star"},
                {"type": "magnify", "x0": 1, "y0": 1, "x1": 9, "y1": 9, "zoom": 0},
                {"type": "step", "x": 10, "y": 10, "number": 3.0, "id": ""},
                {"type": "step", "x": 10, "y": 10, "id": "same"},
                {"type": "step", "x": 10, "y": 10, "id": "same"},
            ],
        }
    )
    rect, coloured, spot, magnify, step, twin_a, twin_b = doc.items
    assert rect.fill == "none" and spot.shape == "rect" and doc.crop is None
    assert coloured.color == RectItem().color
    assert magnify.zoom >= 1  # a zoom of 0 can't be drawn at all
    assert step.number == 3 and isinstance(step.number, int) and step.id
    assert twin_a.id != twin_b.id  # selection and undo go by id
    render_document(doc, Image.new("RGB", (80, 60), "white"))


@pytest.mark.parametrize("data", [[], "x", 5, None])
def test_something_that_is_not_a_document_is_an_error(data, tmp_path):
    with pytest.raises(ValueError):
        Document.from_dict(data)
    path = tmp_path / "document.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Document.load(path)


@pytest.mark.parametrize("text", ["", "{", '{"version": 2, "items": [{"type": "re', "\x00\x00"])
def test_truncated_document_files_are_an_error_not_a_crash(text, tmp_path):
    path = tmp_path / "document.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        Document.load(path)


@pytest.mark.parametrize(
    "crop", [[0, 0, 0, 10], [0, 0, 10, -1], "all", [1, 2, 3, "4"], [0, 0, float("nan"), 5]]
)
def test_bad_crops_are_ignored(crop):
    assert Document.from_dict({"version": 2, "crop": crop, "items": []}).crop is None


def test_fractional_crops_from_old_files_become_whole_pixels():
    assert Document.from_dict({"crop": [1.4, 2.6, 30.2, 20.0], "items": []}).crop == (1, 3, 30, 20)


# -- redaction ---------------------------------------------------------------------
def test_a_redaction_with_an_unknown_mode_blacks_out_instead_of_showing():
    base = noise()
    doc = Document.from_dict(
        {
            "version": 3,
            "items": [
                {"type": "redact", "mode": "frosted-glass", "x0": 40, "y0": 20, "x1": 160, "y1": 90}
            ],
        }
    )
    assert doc.items[0].mode == "solid"
    out = render_document(doc, base)
    assert out.crop((40, 20, 160, 90)).getextrema() == ((0, 0), (0, 0), (0, 0))
    # and if a mode ever reaches the renderer unchecked, the result is the same
    item = RedactItem(x0=40, y0=20, x1=160, y1=90, mode="frosted-glass")
    out = render_document(Document(items=[item]), base)
    assert out.crop((40, 20, 160, 90)).getextrema() == ((0, 0), (0, 0), (0, 0))


def test_black_out_covers_whole_pixels_at_its_edges():
    """A box drawn at fractional positions used to leave its edge pixels half visible."""
    base = Image.new("RGB", (100, 60), "white")
    item = RedactItem(x0=10.5, y0=10.5, x1=60.5, y1=40.5, mode="solid")
    out = render_document(Document(items=[item]), base)
    assert out.crop((10, 10, 61, 41)).getextrema() == ((0, 0), (0, 0), (0, 0))
    assert out.getpixel((9, 9)) == (255, 255, 255) and out.getpixel((61, 41)) == (255, 255, 255)


def test_a_redaction_that_cannot_be_drawn_still_hides(monkeypatch):
    base = noise()
    items = [
        RedactItem(x0=40, y0=20, x1=160, y1=90, mode="blur"),
        RectItem(x0=5, y0=5, x1=30, y1=30, color="#00ff00", fill="solid"),
    ]

    def broken(self, *args):
        raise MemoryError("no room for the blurred copy")

    monkeypatch.setattr(EffectSources, "region", broken)
    out = render_document(Document(items=items), base)
    assert out.crop((40, 20, 160, 90)).getextrema() == ((0, 0), (0, 0), (0, 0))
    assert out.getpixel((15, 15)) == (0, 255, 0)  # the rest of the drawing is unaffected


def test_one_broken_item_does_not_undo_the_redactions():
    base = noise()
    bad = RectItem(x0=5, y0=5, x1=30, y1=30)
    bad.color = "not-a-colour"  # (the loader would have replaced it; this is a direct hit)
    items = [RedactItem(x0=40, y0=20, x1=160, y1=90, mode="solid"), bad]
    out = render_document(Document(items=items), base)
    assert out.crop((40, 20, 160, 90)).getextrema() == ((0, 0), (0, 0), (0, 0))


@pytest.mark.parametrize("fmt", ["png", "jpg", "webp"])
@pytest.mark.parametrize("scale", [100, 50, 25])
def test_redacted_pixels_are_gone_from_every_export(fmt, scale):
    """Nothing of the original survives under a black-out, in any format or size, and the
    file carries no metadata that could hold a copy."""
    from grabbyhands import export

    base = noise(400, 240)
    base.info["icc_profile"] = b"not really a profile"
    base.info["exif"] = b"Exif\x00\x00secret"
    base.info["Comment"] = "secret"
    box = (80, 40, 320, 180)
    doc = Document(items=[RedactItem(x0=box[0], y0=box[1], x1=box[2], y1=box[3], mode="solid")])
    data = export.encode(render_document(doc, base), fmt, 90, scale_percent=scale)
    out = Image.open(io.BytesIO(data))
    assert not {"icc_profile", "exif", "Comment", "comment"} & set(out.info), out.info
    assert b"secret" not in data and b"not really a profile" not in data
    f = scale / 100
    inner = tuple(round(v * f) + d for v, d in zip(box, (4, 4, -4, -4), strict=True))
    assert ImageStat.Stat(out.convert("L").crop(inner)).extrema[0][1] <= 12, (fmt, scale)


def test_pixelated_blocks_hold_only_averages():
    """Each block of a pixelated box is one flat colour: nothing finer is left in the file."""
    base = noise()
    doc = Document(items=[RedactItem(x0=40, y0=20, x1=160, y1=100, mode="pixelate", strength=100)])
    out = render_document(doc, base)
    block = pixel_block(100)
    for bx in range(40, 160, block):
        for by in range(20, 100, block):
            tile = out.crop((bx, by, min(bx + block, 160), min(by + block, 100)))
            assert all(lo == hi for lo, hi in tile.getextrema()), (bx, by)


# -- effect cache ------------------------------------------------------------------
def test_effect_cache_is_bounded_by_size_not_only_by_count(monkeypatch):
    """Resizing one big blur box used to keep up to 24 whole-image copies alive."""
    monkeypatch.setattr(render_mod, "CACHE_BYTES", 2 * 1024 * 1024)
    src = EffectSources(Image.new("RGB", (400, 300), "white"))  # 480 kB per full copy
    budget = src._budget
    for step in range(60):  # a box dragged out over the whole picture
        src.region("blur", 50, 0, 0, 340 + step, 300)
        assert src._cache_bytes <= budget
    assert 1 <= len(src._cache) < 10
    newest = src.region("blur", 50, 0, 0, 399, 300)
    assert src.region("blur", 50, 0, 0, 399, 300) is newest  # what is on screen stays cached


def test_many_small_redactions_all_stay_cached():
    """Auto-Redact can make dozens of boxes; redrawing must not recompute them every time."""
    src = EffectSources(noise(400, 300))
    first = [src.region("pixelate", 50, x, 10, 8, 8) for x in range(0, 400, 8)]
    again = [src.region("pixelate", 50, x, 10, 8, 8) for x in range(0, 400, 8)]
    assert len(first) == 50 and all(a is b for a, b in zip(first, again, strict=True))


# -- screenshots from the desktop --------------------------------------------------
def _png16(path, w, h, alpha):
    """A 16-bit-per-channel PNG (left half orange, right half dark green), written by hand."""
    import struct
    import zlib

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b""
    for _y in range(h):
        rows += b"\0"
        for x in range(w):
            pixel = [0xFFFF, 0x8080, 0x0000] if x < w // 2 else [0x0000, 0x8080, 0x0000]
            rows += b"".join(struct.pack(">H", v) for v in pixel + ([0xFFFF] if alpha else []))
    header = struct.pack(">IIBBBBB", w, h, 16, 6 if alpha else 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("alpha", [False, True])
def test_16_bit_screenshots_read_the_right_colours(tmp_path, alpha):
    """HDR desktops can hand out 16-bit PNGs. Cairo loads those as floating-point surfaces;
    Identify Color and the loupe then read garbage unless the pixels are converted first."""
    from grabbyhands.imaging import Rect, ScreenImage

    path = tmp_path / "hdr.png"
    _png16(path, 8, 4, alpha)
    screen = ScreenImage.from_file(str(path))
    assert screen.pixel(0, 0) == (255, 128, 0) and screen.pixel(7, 3) == (0, 128, 0)
    assert screen.crop(Rect(0, 0, 8, 4)).getpixel((7, 3)) == (0, 128, 0)
    assert (screen.width, screen.height) == (8, 4)


# -- text recognition --------------------------------------------------------------
def test_a_missing_language_is_reported_as_such():
    """Tesseract's last line is only "Could not initialize tesseract."; the reason is above."""
    from grabbyhands import ocr

    stderr = (
        "Error opening data file /usr/share/tesseract-ocr/5/tessdata/zzz.traineddata\n"
        "Please make sure the TESSDATA_PREFIX environment variable is set to your "
        '"tessdata" directory.\n'
        "Failed loading language 'zzz'\n"
        "Tesseract couldn't load any languages!\n"
        "Could not initialize tesseract.\n"
    )
    reason = ocr._failure_reason(stderr)
    assert "Settings → Text" in ocr.friendly_error(reason), reason
    assert ocr._failure_reason("") == "tesseract failed"
    assert ocr._failure_reason("one\n\nlast line\n") == "last line"


@pytest.mark.skipif(not __import__("shutil").which("tesseract"), reason="tesseract not installed")
def test_a_language_that_is_not_installed_gives_the_language_hint():
    from grabbyhands import ocr

    with pytest.raises(RuntimeError) as failure:
        ocr.read_words(Image.new("RGB", (200, 60), "white"), "zzz-not-a-language")
    assert "Settings → Text" in ocr.friendly_error(str(failure.value))


def test_a_timeout_is_reported_in_words_not_as_a_command_line(monkeypatch):
    import subprocess

    from grabbyhands import ocr

    def too_slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", too_slow)
    with pytest.raises(RuntimeError) as failure:
        ocr.read_words(Image.new("RGB", (200, 60), "white"))
    message = ocr.friendly_error(str(failure.value))
    assert "timed out" in message and "stdin" not in message and "[" not in message
    assert ocr.languages() == []  # (and listing languages never raises either)


def test_malformed_rows_from_tesseract_are_skipped(monkeypatch):
    from grabbyhands import ocr

    columns = ("level", "page_num", "block_num", "par_num", "line_num", "word_num")
    header = "\t".join(columns + ("left", "top", "width", "height", "conf", "text"))
    rows = [
        header,
        "5\t1\t1\t1\t1\t1\t20\t30\t100\t40\t96.5\tHello",
        "5\t1\t1\t1\t1\t2\t140\t30\tx\t40\t96.5\tbroken",  # a width that isn't a number
        "5\t1\t1\t1\t1\t3\t260\t30",  # cut short
        "5\t1\t1\t1\t1\t4\t260\t30\t80\t40\tnan?\tconf",
        "5\t1\t1\t1\t1\t5\t360\t30\t80\t40\t91\tworld",
    ]
    monkeypatch.setattr(ocr, "_run", lambda *args: "\n".join(rows) + "\n")
    words = ocr.read_words(Image.new("RGB", (3000, 200), "white"))
    assert [w.text for w in words] == ["Hello", "world"]


def test_codes_without_text_are_not_offered(monkeypatch):
    import sys
    import types

    from grabbyhands import qr

    class Result:
        def __init__(self, text):
            self.valid, self.text, self.format = True, text, "QRCode"

    fake = types.SimpleNamespace(
        read_barcodes=lambda image: [Result(""), Result("hi"), Result("hi")]
    )
    monkeypatch.setitem(sys.modules, "zxingcpp", fake)
    assert [c.text for c in qr.read_codes(Image.new("RGB", (900, 900)))] == ["hi"]


# -- saving ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pattern",
    ["../../escape", "/etc/passwd", "..", ".", "a/../../b", "..\\..\\x", "\x00nul", "~/x", "%D"],
)
def test_no_file_name_pattern_leaves_the_save_folder(tmp_path, pattern):
    from datetime import datetime

    from grabbyhands import export

    folder = tmp_path / "Pictures" / "GrabbyHands"
    when = datetime(2026, 11, 10, 14, 3, 22)
    name = export.make_filename(pattern, when)
    assert name and "/" not in name and "\x00" not in name and name not in (".", "..")
    path = export.save_image(Image.new("RGB", (4, 4)), folder, pattern, "png", 90, when)
    assert path.parent == folder and path.is_file()
    assert [p.name for p in tmp_path.iterdir()] == ["Pictures"]  # nothing next to it


def test_very_long_patterns_still_give_a_name_that_can_be_saved(tmp_path):
    from datetime import datetime

    from grabbyhands import export

    name = export.make_filename("é" * 400 + " %H", datetime(2026, 1, 1, 9))
    assert 0 < len(name.encode()) <= export.MAX_NAME_BYTES
    path = export.save_image(Image.new("RGB", (4, 4)), tmp_path, "é" * 400, "webp")
    assert path.is_file() and path.suffix == ".webp"


def test_two_saves_in_the_same_second_get_different_files(tmp_path):
    from datetime import datetime

    from grabbyhands import export

    when = datetime(2026, 11, 10, 14, 3, 22)
    first = export.save_image(Image.new("RGB", (4, 4), "red"), tmp_path, "%H-%M-%S", when=when)
    second = export.save_image(Image.new("RGB", (4, 4), "blue"), tmp_path, "%H-%M-%S", when=when)
    assert first != second and Image.open(first).getpixel((0, 0)) == (255, 0, 0)


def test_a_failed_write_never_damages_the_picture_that_was_there(tmp_path, monkeypatch):
    """Closing the editor replaces the saved file. If the disk fills up half-way, the old
    file must still be whole (it used to be cut short), with nothing left lying around."""
    import os

    from grabbyhands import export

    path = tmp_path / "shot.png"
    export.write_image(Image.new("RGB", (40, 30), "red"), path)
    before = path.read_bytes()

    real_fdopen = os.fdopen

    class FullDisk:
        def __init__(self, fd, *args, **kwargs):
            self.file = real_fdopen(fd, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.file.close()

        def write(self, data):
            self.file.write(data[: len(data) // 2])
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fdopen", FullDisk)
    with pytest.raises(OSError):
        export.write_image(Image.new("RGB", (400, 300), "blue"), path)
    monkeypatch.undo()
    assert path.read_bytes() == before and Image.open(path).size == (40, 30)
    assert [p.name for p in tmp_path.iterdir()] == ["shot.png"]
    # a brand-new file that can't be finished doesn't stay behind either
    monkeypatch.setattr(os, "fdopen", FullDisk)
    with pytest.raises(OSError):
        export.save_image(Image.new("RGB", (40, 30)), tmp_path, "new")
    monkeypatch.undo()
    assert [p.name for p in tmp_path.iterdir()] == ["shot.png"]


def test_folders_that_cannot_swap_files_are_written_directly(tmp_path, monkeypatch):
    """Some network shares and phones refuse to put one file in another's place."""
    import os

    from grabbyhands import export

    def refuse(src, dst):
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr(os, "replace", refuse)
    path = export.save_image(Image.new("RGB", (12, 9), "red"), tmp_path, "phone")
    export.write_image(Image.new("RGB", (30, 20), "blue"), path)
    assert Image.open(path).size == (30, 20)
    assert [p.name for p in tmp_path.iterdir()] == ["phone.png"]  # no temporary file left


def test_saved_files_get_normal_permissions_and_keep_their_own(tmp_path):
    import os
    import stat

    from grabbyhands import export

    old = os.umask(0o022)
    try:
        path = export.save_image(Image.new("RGB", (4, 4)), tmp_path, "shared")
        assert stat.S_IMODE(path.stat().st_mode) == 0o644  # not the temp file's 0600
        path.chmod(0o640)
        export.write_image(Image.new("RGB", (8, 8)), path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o640 and Image.open(path).size == (8, 8)
    finally:
        os.umask(old)
    assert export._umask() == old


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root ignores file permissions")
def test_a_read_only_picture_is_not_replaced(tmp_path):
    from grabbyhands import export

    path = tmp_path / "keep.png"
    export.write_image(Image.new("RGB", (40, 30), "red"), path)
    path.chmod(0o444)
    with pytest.raises(PermissionError):
        export.write_image(Image.new("RGB", (4, 4), "blue"), path)
    assert Image.open(path).size == (40, 30)


def test_writing_through_a_link_keeps_the_link(tmp_path):
    from grabbyhands import export

    real = tmp_path / "real.png"
    export.write_image(Image.new("RGB", (4, 4)), real)
    link = tmp_path / "link.png"
    link.symlink_to(real)
    export.write_image(Image.new("RGB", (9, 9)), link)
    assert link.is_symlink() and Image.open(real).size == (9, 9)


@pytest.mark.parametrize(
    "typed,fmt,expected,written",
    [
        ("shot", "jpg", "shot.jpg", "JPEG"),
        ("Report 11.2026", "jpg", "Report 11.2026.jpg", "JPEG"),  # used to save PNG data here
        ("v1.2 final", "webp", "v1.2 final.webp", "WEBP"),
        ("notes.txt", "png", "notes.txt.png", "PNG"),
        ("shot.PNG", "jpg", "shot.PNG", "PNG"),  # a picture extension the user typed wins
        ("shot.jpeg", "png", "shot.jpeg", "JPEG"),
        ("shot.webp", "jpg", "shot.webp", "WEBP"),
    ],
)
def test_save_as_names_and_formats_agree(tmp_path, typed, fmt, expected, written):
    from grabbyhands import export

    path = export.path_for_format(tmp_path / typed, fmt)
    assert path.name == expected
    export.write_image(Image.new("RGB", (8, 8)), path, 80)
    assert Image.open(path).format == written


def test_a_name_completed_for_the_user_never_replaces_another_file(tmp_path):
    from grabbyhands import export

    (tmp_path / "shot.png").write_bytes(b"someone else's picture")
    assert export.path_for_format(tmp_path / "shot", "png").name == "shot-2.png"


@pytest.mark.parametrize("fmt", ["png", "jpg", "webp"])
def test_transparent_pictures_can_be_written_in_every_format(fmt):
    from grabbyhands import export

    image = Image.new("RGBA", (20, 10), (255, 0, 0, 0))  # fully transparent red
    out = Image.open(io.BytesIO(export.encode(image, fmt, 90)))
    if fmt == "jpg":  # no transparency in JPEG: it lands on white, not on black or red
        assert all(v > 240 for v in out.convert("RGB").getpixel((5, 5)))
    else:
        assert out.convert("RGBA").getpixel((5, 5))[3] == 0
    for mode in ("P", "LA", "L", "1"):
        assert export.encode(Image.new(mode, (8, 8)), fmt, 60)


def test_scale_and_width_limits_never_go_below_one_pixel():
    from grabbyhands import export

    thin = Image.new("RGB", (2000, 1))
    assert export.scale_image(thin, 25).size == (500, 1)
    assert export.scale_image(thin, 100, max_width=3).size == (3, 1)
    assert export.scale_image(thin, 0).size == (20, 1)  # 0 % is read as the smallest step
    assert export.scale_image(thin, 500) is thin  # never enlarged


# -- history -----------------------------------------------------------------------
def _store(tmp_path, n=3):
    from datetime import datetime

    from grabbyhands.history import HistoryStore

    store = HistoryStore(tmp_path / "data")
    entries = [
        store.add(Image.new("RGB", (30 + i, 20), "red"), when=datetime(2026, 11, 10, 9, i))
        for i in range(n)
    ]
    return store, entries


def test_history_folder_is_private(tmp_path):
    import stat

    from grabbyhands.history import HistoryStore

    root = tmp_path / "share" / "grabbyhands"
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)  # what an older version left behind
    store = HistoryStore(root)
    entry = store.add(Image.new("RGB", (10, 10)))
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.index_path.stat().st_mode) == 0o600
    assert store.original_path(entry.id).is_file()
    fresh = tmp_path / "new" / "grabbyhands"
    HistoryStore(fresh)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "index",
    ["", "{", "null", "{}", '"text"', "[1, 2]", '[{"id": 5}]', "[[]]", '[{"id": "x"}', "\x00\x01"],
)
def test_a_damaged_index_is_rebuilt_from_the_captures_on_disk(tmp_path, index):
    """A broken history.json used to mean an empty History (or a crash at startup), with
    every capture left on disk where the user could no longer see or delete it."""
    from grabbyhands.history import HistoryStore

    store, entries = _store(tmp_path)
    store.index_path.write_text(index)
    again = HistoryStore(tmp_path / "data")
    assert [e.id for e in again.entries()] == [e.id for e in reversed(entries)]
    assert [(e.width, e.height) for e in again.entries()] == [(32, 20), (31, 20), (30, 20)]
    assert again.entries()[0].created == entries[-1].created
    # and the repaired index was written back
    assert len(json.loads(store.index_path.read_text())) == 3


def test_a_half_written_capture_is_picked_up_and_broken_folders_are_ignored(tmp_path):
    from grabbyhands.history import HistoryStore

    store, entries = _store(tmp_path, 1)
    orphan = store.captures / "20261110-120000-000001"
    orphan.mkdir()
    Image.new("RGB", (64, 48), "blue").save(orphan / "original.png")
    (orphan / "document.json").write_text('{"version": 2, "items": []}')
    (store.captures / "20261110-130000-000001").mkdir()  # the app died before the picture
    broken = store.captures / "20261110-140000-000001"
    broken.mkdir()
    (broken / "original.png").write_bytes(b"\x89PNG\r\n\x1a\n not really")
    again = HistoryStore(tmp_path / "data")
    assert [e.id for e in again.entries()] == [orphan.name, entries[0].id]
    recovered = again.entries()[0]
    assert (recovered.width, recovered.height, recovered.edited) == (64, 48, True)
    assert again.thumb_path(recovered.id).is_file()


def test_entries_whose_pictures_are_gone_disappear_from_history(tmp_path):
    import shutil

    from grabbyhands.history import HistoryStore

    store, (a, b, c) = _store(tmp_path)
    shutil.rmtree(store.dir_for(b.id))
    store.original_path(c.id).unlink()
    again = HistoryStore(tmp_path / "data")
    assert [e.id for e in again.entries()] == [a.id]


def test_one_bad_entry_does_not_take_the_others_with_it(tmp_path):
    from grabbyhands.history import HistoryStore

    store, (a, b, c) = _store(tmp_path)
    raw = json.loads(store.index_path.read_text())
    raw[0]["future_field"] = {"anything": 1}  # written by a newer version: ignored
    raw[1]["created"] = "the day before yesterday"
    raw[2]["saved_path"] = 17
    raw.append({"id": "../../../etc", "created": a.created, "width": 1, "height": 1})
    raw.append(raw[0])  # the same capture listed twice
    store.index_path.write_text(json.dumps(raw))
    again = HistoryStore(tmp_path / "data")
    assert [e.id for e in again.entries()] == [c.id, b.id, a.id]  # b came back from disk
    assert again.get(a.id).saved_path is None
    assert all("/" not in e.id for e in again.entries())


def test_an_id_can_never_point_outside_the_captures_folder(tmp_path):
    from grabbyhands.history import HistoryStore

    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "original.png").write_bytes(b"x")
    store, (a,) = _store(tmp_path, 1)
    raw = json.loads(store.index_path.read_text())
    raw.append({"id": "../../precious", "created": a.created, "width": 1, "height": 1})
    store.index_path.write_text(json.dumps(raw))
    again = HistoryStore(tmp_path / "data")
    again.clear()
    assert (precious / "original.png").exists()


def test_clear_history_leaves_nothing_on_disk(tmp_path):
    from grabbyhands.history import HistoryStore

    store, entries = _store(tmp_path)
    leftover = store.captures / "not-in-the-index"
    leftover.mkdir()
    (leftover / "thumb.png").write_bytes(b"a picture of something private")
    (store.captures / "stray-file").write_bytes(b"x")
    assert len(store.clear()) == 3
    assert list(store.captures.iterdir()) == []
    assert HistoryStore(tmp_path / "data").entries() == []


def test_a_capture_that_cannot_be_stored_leaves_nothing_behind(tmp_path, monkeypatch):
    from grabbyhands.history import HistoryStore

    store, _entries = _store(tmp_path, 1)

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(HistoryStore, "write_thumbnail", full)
    with pytest.raises(OSError):
        store.add(Image.new("RGB", (10, 10)))
    monkeypatch.undo()
    assert len(store.entries()) == 1 and len(list(store.captures.iterdir())) == 1
    assert len(HistoryStore(tmp_path / "data").entries()) == 1


def test_an_index_that_cannot_be_written_leaves_the_old_one_whole(tmp_path, monkeypatch):
    import os

    store, entries = _store(tmp_path)
    before = store.index_path.read_text()

    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", fail)
    entries[0].saved_path = "/somewhere/else.png"
    with pytest.raises(OSError):
        store.update(entries[0])
    monkeypatch.undo()
    assert store.index_path.read_text() == before
    assert [p.name for p in store.root.iterdir() if p.name.startswith(".history-")] == []


def test_captures_in_the_same_instant_get_their_own_folders(tmp_path):
    from datetime import datetime

    from grabbyhands.history import HistoryStore

    store = HistoryStore(tmp_path)
    when = datetime(2026, 11, 10, 9, 0, 0, 123456)
    ids = {store.add(Image.new("RGB", (5, 5)), when=when).id for _ in range(3)}
    assert len(ids) == 3 and len(HistoryStore(tmp_path).entries()) == 3


@pytest.mark.parametrize("size", [(5000, 1), (1, 5000), (1, 1), (8000, 8000)])
def test_thumbnails_of_extreme_shapes(tmp_path, size):
    from grabbyhands.history import THUMB_SIZE, HistoryStore

    store = HistoryStore(tmp_path)
    entry = store.add(Image.new("RGB", size, "red"))
    thumb = Image.open(store.thumb_path(entry.id))
    assert 1 <= thumb.width <= THUMB_SIZE[0] and 1 <= thumb.height <= THUMB_SIZE[1]


def test_pruning_keeps_the_newest_and_removes_the_rest_from_disk(tmp_path):
    store, entries = _store(tmp_path, 5)
    assert store.prune(0) == [] and store.prune(9) == []  # 0 means keep everything
    assert store.prune(2) == [e.id for e in reversed(entries[:3])]
    assert [e.id for e in store.entries()] == [entries[4].id, entries[3].id]
    assert sorted(p.name for p in store.captures.iterdir()) == sorted(e.id for e in entries[3:])


def test_listeners_can_be_disconnected_twice(tmp_path):
    from grabbyhands.history import HistoryStore

    store = HistoryStore(tmp_path)
    disconnect = store.connect(lambda: None)
    disconnect()
    disconnect()  # e.g. a window closed from two places


# -- drawing -----------------------------------------------------------------------
def _stray_pixels(first, second) -> int:
    """Pixels that neither item draws by itself but the two draw together."""
    from PIL import ImageChops

    base = Image.new("RGB", (300, 200), "white")
    both = render_document(Document(items=[first, second]), base)
    alone = ImageChops.darker(
        render_document(Document(items=[first]), base),
        render_document(Document(items=[second]), base),
    )
    diff = ImageChops.difference(both, alone).convert("L")
    return sum(diff.histogram()[25:])


def test_items_do_not_draw_lines_to_each_other():
    """Two numbered steps in a row had a thin wedge between them (also in saved files): a
    circle started with a line from wherever the text before it had ended."""
    from grabbyhands.editor.document import (
        CalloutItem,
        EllipseItem,
        SpotlightItem,
        StepItem,
        StickerItem,
    )

    def step(x, y, n):
        return StepItem(x=x, y=y, number=n, color="#0000ff")

    callout = CalloutItem(x0=10, y0=10, x1=120, y1=60, text="hi", tail_x=20, tail_y=90)
    sticker = StickerItem(x0=10, y0=10, x1=60, y1=60, text="★")
    ellipse = EllipseItem(x0=220, y0=130, x1=280, y1=180)
    spot = SpotlightItem(x0=220, y0=130, x1=280, y1=180, shape="ellipse")
    pairs = [
        (step(40, 40, 1), step(250, 160, 2)),
        (step(40, 40, 1), ellipse),
        (sticker, step(250, 160, 2)),
        (callout, step(250, 160, 2)),
        (callout, ellipse),
    ]
    for first, second in pairs:
        assert _stray_pixels(first, second) == 0, (first.type, second.type)
    # a spotlight after text: its bright hole is only the ellipse, with no wedge cut out
    base = Image.new("RGB", (300, 200), "white")
    lit = render_document(Document(items=[step(40, 40, 1), spot]), base)
    assert lit.getpixel((140, 90))[0] < 200, "the dimming has a hole between the two items"


# -- keys on any keyboard layout ---------------------------------------------------
def test_letters_are_found_on_keyboards_that_do_not_type_latin():
    from gi.repository import Gdk

    from grabbyhands.keys import latin_keyval

    # (level, keyval) for the physical C key with a US and a Russian layout set up
    c_key = [(0, Gdk.KEY_c), (1, Gdk.KEY_C), (0, Gdk.KEY_Cyrillic_es), (1, Gdk.KEY_Cyrillic_ES)]
    assert latin_keyval(Gdk.KEY_Cyrillic_es, 54, c_key) == Gdk.KEY_c
    assert latin_keyval(Gdk.KEY_Cyrillic_ES, 54, c_key) == Gdk.KEY_c  # with Shift or Caps Lock
    # the user's own Latin layout decides, not the position: AZERTY has Q where US has A
    azerty = [(0, Gdk.KEY_q), (1, Gdk.KEY_Q), (0, Gdk.KEY_Cyrillic_ef)]
    assert latin_keyval(Gdk.KEY_Cyrillic_ef, 38, azerty) == Gdk.KEY_q
    # no Latin layout at all: the letter printed on a US keyboard in that position
    assert latin_keyval(Gdk.KEY_Cyrillic_es, 54, [(0, Gdk.KEY_Cyrillic_es)]) == Gdk.KEY_c
    assert latin_keyval(Gdk.KEY_Greek_alpha, 38, []) == Gdk.KEY_a
    assert latin_keyval(Gdk.KEY_hebrew_bet, 54, []) == Gdk.KEY_c
    assert latin_keyval(Gdk.KEY_Arabic_seen, 39, []) == Gdk.KEY_s


def test_keys_of_latin_layouts_are_left_alone():
    from gi.repository import Gdk

    from grabbyhands.keys import latin_keyval

    for keyval in (Gdk.KEY_c, Gdk.KEY_C, Gdk.KEY_space, Gdk.KEY_Escape, Gdk.KEY_Left, Gdk.KEY_F1):
        assert latin_keyval(keyval, 54, []) == keyval
    # a Latin layout's own letters never turn into the US letter of that position:
    # é (bépo, on the W key), ğ (Turkish F, on the E key), ö (German, right of L)
    assert latin_keyval(Gdk.KEY_eacute, 25, [(0, Gdk.KEY_eacute)]) == Gdk.KEY_eacute
    assert latin_keyval(Gdk.KEY_gbreve, 26, [(0, Gdk.KEY_gbreve)]) == Gdk.KEY_gbreve
    assert latin_keyval(Gdk.KEY_odiaeresis, 47, []) == Gdk.KEY_odiaeresis
    # digits and punctuation of other scripts are not letters
    assert latin_keyval(Gdk.KEY_Arabic_comma, 54, []) == Gdk.KEY_Arabic_comma
    # a key outside the letter block
    assert latin_keyval(Gdk.KEY_Cyrillic_ha, 34, []) == Gdk.KEY_Cyrillic_ha


# -- geometry ----------------------------------------------------------------------
def _geo(x, y, w, h):
    from gi.repository import Gdk

    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = x, y, w, h
    return rect


FHD = (1920, 1080)
SCREEN_LAYOUTS = [
    # (monitors as GDK reports them, screenshot size, where each lands in the screenshot)
    # one screen at 100 %, 125 %, 150 % and 200 %
    ([(0, 0, *FHD)], FHD, [(0, 0, *FHD)]),
    ([(0, 0, 1536, 864)], FHD, [(0, 0, *FHD)]),
    ([(0, 0, 1280, 720)], FHD, [(0, 0, *FHD)]),
    ([(0, 0, *FHD)], (3840, 2160), [(0, 0, 3840, 2160)]),
    # a second screen to the left (negative x), and one above (negative y)
    ([(-1920, 0, *FHD), (0, 0, *FHD)], (3840, 1080), [(0, 0, *FHD), (1920, 0, *FHD)]),
    ([(0, -1080, *FHD), (0, 0, *FHD)], (1920, 2160), [(0, 0, *FHD), (0, 1080, *FHD)]),
    # side by side at 150 %
    ([(0, 0, 1280, 720), (1280, 0, 1280, 720)], (3840, 1080), [(0, 0, *FHD), (1920, 0, *FHD)]),
    # a smaller screen next to a bigger one, set lower
    (
        [(0, 0, 2560, 1440), (2560, 360, *FHD)],
        (4480, 1440),
        [(0, 0, 2560, 1440), (2560, 360, *FHD)],
    ),
    # mixed scales (a 200 % laptop next to a 100 % monitor): the picture is taken at 200 %
    (
        [(0, 0, *FHD), (1920, 0, *FHD)],
        (7680, 2160),
        [(0, 0, 3840, 2160), (3840, 0, 3840, 2160)],
    ),
]


@pytest.mark.parametrize("monitors,image,expected", SCREEN_LAYOUTS)
def test_screens_map_onto_the_screenshot(monitors, image, expected):
    from grabbyhands.imaging import Rect
    from grabbyhands.overlay import ScreenMapping

    geos = [_geo(*m) for m in monitors]
    mapping = ScreenMapping(geos, *image)
    assert [mapping.monitor_rect(g) for g in geos] == [Rect(*e) for e in expected]
    # together they cover the picture exactly
    assert sum(r.w * r.h for r in map(mapping.monitor_rect, geos)) <= image[0] * image[1]


def test_window_positions_follow_the_desktop_scaling():
    """GNOME up to 48 reports windows in physical pixels on a HiDPI screen without
    fractional scaling: every window was highlighted in the wrong place, twice too big."""
    from grabbyhands.windows import WindowRect, to_layout

    windows = [WindowRect(200, 100, 1600, 1200, "Files"), WindowRect(0, 0, 3840, 2160, "Web")]
    hidpi = [_geo(0, 0, 1920, 1080)]  # a 4K screen at 200 %: the app sees 1920 x 1080
    assert to_layout(windows, (3840, 2160), hidpi) == [
        WindowRect(100, 50, 800, 600, "Files"),
        WindowRect(0, 0, 1920, 1080, "Web"),
    ]
    # logical pixels already (fractional scaling on, or a 100 % screen): unchanged
    assert to_layout(windows, (1920, 1080), hidpi) is windows
    # a helper from before it could tell, or no helper: as before
    assert to_layout(windows, None, hidpi) is windows
    assert to_layout([], (3840, 2160), hidpi) == []
    assert to_layout(windows, (0, 0), hidpi) is windows
    # two screens, the left one at negative coordinates
    pair = [_geo(-1920, 0, 1920, 1080), _geo(0, 0, 1920, 1080)]
    assert to_layout([WindowRect(3840, 0, 800, 600, "A")], (7680, 2160), pair) == [
        WindowRect(0, 0, 400, 300, "A")
    ]


class _HelperBus:
    """A session bus with a Window Helper on it that answers as the test says."""

    def __init__(self, running=True, answer=None, error=None):
        self.running, self.answer, self.error, self.calls = running, answer, error, []

    def call_sync(self, name, _path, _interface, method, args, *_rest):
        from gi.repository import GLib

        self.calls.append((method, args.unpack() if args is not None else None))
        if method == "NameHasOwner":
            return GLib.Variant("(b)", (self.running,))
        if self.error is not None:
            raise self.error
        return self.answer


def test_menu_button_states(monkeypatch):
    """What Settings learns about the system menu's screenshot button, case by case."""
    from gi.repository import Gio, GLib

    from grabbyhands import windows

    def state(**bus):
        monkeypatch.setattr(windows, "_bus", lambda: _HelperBus(**bus) if bus else None)
        return windows.menu_button()

    assert state() == "no-helper"  # no session bus at all
    assert state(running=False) == "no-helper"
    assert state(answer=GLib.Variant("(bb)", (True, True))) == "on"
    assert state(answer=GLib.Variant("(bb)", (False, True))) == "off"
    # the choice may be on, but this GNOME has no such button: say so, don't pretend
    assert state(answer=GLib.Variant("(bb)", (True, False))) == "unavailable"
    # GNOME still runs the helper from before the update, until the next login
    unknown = GLib.Error.new_literal(
        Gio.DBusError.quark(), "No such method", Gio.DBusError.UNKNOWN_METHOD
    )
    assert state(error=unknown) == "old-helper"
    gone = GLib.Error.new_literal(Gio.DBusError.quark(), "Gone", Gio.DBusError.NO_REPLY)
    assert state(error=gone) == "no-helper"


def test_set_menu_button_reports_what_happened(monkeypatch):
    from gi.repository import Gio, GLib

    from grabbyhands import windows

    bus = _HelperBus(answer=GLib.Variant("(b)", (True,)))
    monkeypatch.setattr(windows, "_bus", lambda: bus)
    assert windows.set_menu_button(True) is True
    assert bus.calls == [("SetMenuButton", (True,))]
    bus.answer = GLib.Variant("(b)", (False,))  # the helper could not do it
    assert windows.set_menu_button(False) is False
    assert bus.calls[-1] == ("SetMenuButton", (False,))
    bus.error = GLib.Error.new_literal(
        Gio.DBusError.quark(), "No such method", Gio.DBusError.UNKNOWN_METHOD
    )
    assert windows.set_menu_button(True) is False
    monkeypatch.setattr(windows, "_bus", lambda: None)
    assert windows.set_menu_button(True) is False


def test_shell_helper_menu_button_is_careful():
    """Static promises about the extension; tests/test_shell.py tries it in a real Shell."""
    import json

    from conftest import ROOT

    ext = ROOT / "data" / "shell-extension"
    source = (ext / "extension.js").read_text()
    meta = json.loads((ext / "metadata.json").read_text())
    schema = (ext / "schemas" / f"{meta['settings-schema']}.gschema.xml").read_text()
    assert f'id="{meta["settings-schema"]}"' in schema
    # off unless the user turns it on
    assert 'name="replace-screenshot-button"' in schema and "<default>false</default>" in schema
    assert "'replace-screenshot-button'" in source
    # GNOME's button is hidden and shown again, never destroyed or rewired
    assert "original.hide()" in source and "original?.show()" in source
    assert "original.destroy" not in source and "signal_handlers" not in source
    # the click never dead-ends: GNOME's own tool is the fallback
    assert "Main.screenshotUI.open()" in source
    # everything started in enable() is undone in disable()
    assert "GLib.source_remove" in source and "bus_unown_name" in source
    # the zip and the build both carry the schema
    assert "schemas" in (ROOT / "build-aux" / "pack-extension.sh").read_text()
    assert "gschemas.compiled" in (ROOT / "data" / "meson.build").read_text()


def test_shell_helper_reports_the_stage_size():
    from conftest import ROOT

    source = (ROOT / "data" / "shell-extension" / "extension.js").read_text()
    assert 'name="GetStageSize"' in source and "GetStageSize()" in source
    for private in ("get_title", "get_wm_class", "get_pid", "get_description", "title"):
        assert private not in source.replace("window titles", ""), private  # privacy promise


# -- global shortcuts --------------------------------------------------------------
class _Portal:
    """A portal that records requests; tests answer them by hand."""

    def __init__(self):
        self.requests, self.bus = [], self

    def signal_subscribe(self, *args):
        self.requests.append(("subscribe", args[2]))
        return len(self.requests)

    def signal_unsubscribe(self, _subscription):
        pass

    def _token(self):
        return "token"

    def request(self, _interface, method, build_params, callback):
        build_params("t")  # the arguments must be buildable
        self.requests.append((method, callback))

    def answer(self, method, response, results):
        index = next(i for i, (m, _cb) in enumerate(self.requests) if m == method)
        _method, callback = self.requests.pop(index)
        callback(response, results)

    def methods(self):
        return [m for m, _cb in self.requests if m != "subscribe"]


SPECS = [("capture", "Take a screenshot", "Print"), ("capture-text", "Copy text", "LOGO+SHIFT+t")]


def _bound(*ids):
    return {"shortcuts": [(i, {"trigger_description": f"Key for {i}"}) for i in ids]}


def _session(bind_if_missing=True, complete=False):
    from grabbyhands.portals import GlobalShortcuts

    portal, done, changed = _Portal(), [], []
    shortcuts = GlobalShortcuts(portal, lambda _id: None, lambda: changed.append(True))
    shortcuts.start(SPECS, bind_if_missing, lambda ok, ids: done.append((ok, ids)), complete)
    portal.answer("CreateSession", 0, {"session_handle": "/session/1"})
    return portal, shortcuts, done, changed


def test_shortcuts_are_asked_for_once_not_at_every_login():
    # nothing bound yet: ask (the desktop shows its dialog)
    portal, shortcuts, done, _changed = _session()
    portal.answer("ListShortcuts", 0, _bound())
    assert portal.methods() == ["BindShortcuts"]
    portal.answer("BindShortcuts", 0, _bound("capture", "capture-text"))
    assert done == [(True, ["capture", "capture-text"])]
    # all there: nothing to ask
    portal, shortcuts, done, _changed = _session()
    portal.answer("ListShortcuts", 0, _bound("capture", "capture-text"))
    assert portal.methods() == [] and done == [(True, ["capture", "capture-text"])]
    # one was removed by the user (or never got a key): don't nag at every session start...
    portal, shortcuts, done, _changed = _session()
    portal.answer("ListShortcuts", 0, _bound("capture-text"))
    assert portal.methods() == [] and done == [(True, ["capture-text"])]
    assert "capture" not in shortcuts.triggers
    # ...but "Set Up Shortcuts" asks for what is missing
    portal, shortcuts, done, _changed = _session(complete=True)
    portal.answer("ListShortcuts", 0, _bound("capture-text"))
    assert portal.methods() == ["BindShortcuts"]
    # the dialog was dismissed before: not asked at startup
    portal, shortcuts, done, _changed = _session(bind_if_missing=False)
    portal.answer("ListShortcuts", 0, _bound())
    assert portal.methods() == [] and done == [(False, [])]
    # the user dismisses the dialog: answered, nothing bound
    portal, shortcuts, done, _changed = _session()
    portal.answer("ListShortcuts", 0, _bound())
    portal.answer("BindShortcuts", 1, {})
    assert done == [(True, [])]
    # the portal refuses to make a session
    from grabbyhands.portals import GlobalShortcuts

    portal, done = _Portal(), []
    GlobalShortcuts(portal, lambda _id: None).start(SPECS, True, lambda *a: done.append(a))
    portal.answer("CreateSession", 2, {})
    assert done == [(False, [])] and portal.methods() == []


def test_shortcuts_removed_in_the_desktop_settings_are_forgotten():
    from gi.repository import GLib

    portal, shortcuts, _done, changed = _session()
    portal.answer("ListShortcuts", 0, _bound("capture", "capture-text"))
    assert shortcuts.triggers == {
        "capture": "Key for capture",
        "capture-text": "Key for capture-text",
    }
    # the desktop says the set changed (the user removed one in its settings)
    signal = GLib.Variant(
        "(oa(sa{sv}))",
        ("/session/1", [("capture-text", {"trigger_description": GLib.Variant("s", "Super+T")})]),
    )
    shortcuts._on_shortcuts_changed(None, None, None, None, None, signal)
    assert shortcuts.triggers == {"capture-text": "Super+T"} and changed == [True]
    # somebody else's session is none of our business
    other = GLib.Variant("(oa(sa{sv}))", ("/session/other", []))
    shortcuts._on_shortcuts_changed(None, None, None, None, None, other)
    assert shortcuts.triggers == {"capture-text": "Super+T"}
    # a later list replaces what was known
    shortcuts.list(lambda ids: None)
    portal.answer("ListShortcuts", 0, _bound("capture"))
    assert shortcuts.triggers == {"capture": "Key for capture"}


def test_answers_for_a_shortcuts_session_that_is_gone_are_ignored():
    from grabbyhands.portals import GlobalShortcuts

    portal, done = _Portal(), []
    shortcuts = GlobalShortcuts(portal, lambda _id: None)
    shortcuts.start(SPECS, True, lambda *a: done.append(("first", a)))
    shortcuts.start(SPECS, True, lambda *a: done.append(("second", a)))  # e.g. the portal restarted
    portal.answer("CreateSession", 0, {"session_handle": "/session/old"})  # the first one's answer
    assert shortcuts.session_handle is None and portal.methods() == ["CreateSession"]
    portal.answer("CreateSession", 0, {"session_handle": "/session/new"})
    assert shortcuts.session_handle == "/session/new"
    shortcuts.stop()
    portal.answer("ListShortcuts", 0, _bound("capture", "capture-text"))  # too late
    assert done == [] and shortcuts.triggers == {}
    # a shortcut that fires for an old session does nothing
    from gi.repository import GLib

    fired = []
    shortcuts.on_activated = fired.append
    shortcuts.session_handle = "/session/new"
    press = GLib.Variant("(osta{sv})", ("/session/old", "capture", 0, {}))
    shortcuts._on_activated(None, None, None, None, None, press)
    assert fired == []
    press = GLib.Variant("(osta{sv})", ("/session/new", "capture", 0, {}))
    shortcuts._on_activated(None, None, None, None, None, press)
    assert fired == ["capture"]


# -- desktop integration -----------------------------------------------------------
@pytest.mark.parametrize(
    "launcher",
    [
        "/usr/bin/grabbyhands",
        "/opt/Jane Doe/bin/grabbyhands",  # a space used to break the entry
        '/opt/odd "name"/$HOME/100%/back\\slash/`tick`/grabbyhands',
    ],
)
def test_autostart_entry_survives_any_launcher_path(launcher):
    from gi.repository import GLib

    from grabbyhands import APP_ID, config

    text = config.autostart_entry(launcher)
    keyfile = GLib.KeyFile()
    keyfile.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
    command = keyfile.get_string("Desktop Entry", "Exec")
    _ok, argv = GLib.shell_parse_argv(command.replace("%%", "%"))
    assert argv == [launcher, "--background"]
    assert keyfile.get_string("Desktop Entry", "Icon") == APP_ID
    assert "%" not in command.replace("%%", "")  # no accidental field codes


def test_folders_chosen_inside_a_flatpak_are_shown_as_the_user_knows_them(monkeypatch):
    """There, a folder picked in the file chooser is /run/user/1000/doc/<id>/<name>."""
    import os

    from grabbyhands import export

    stand_in = "/run/user/1000/doc/1a2b3c4d/Screenshots/2026-11-10.png"

    def newer_portal(path, name):
        assert name == "user.document-portal.host-path"
        return b"/media/example/Screenshots/2026-11-10.png\x00"

    monkeypatch.setattr(os, "getxattr", newer_portal)
    assert export.host_path(stand_in) == "/media/example/Screenshots/2026-11-10.png"
    assert export.display_path(stand_in) == "/media/example/Screenshots/2026-11-10.png"

    def older_portal(path, name):
        raise OSError(61, "No data available")

    monkeypatch.setattr(os, "getxattr", older_portal)
    assert export.display_path(stand_in) == "Screenshots/2026-11-10.png"  # what was chosen
    # anything else is never looked up and never changed

    def never(path, name):
        raise AssertionError("an ordinary path was looked up")

    monkeypatch.setattr(os, "getxattr", never)
    for ordinary in ("/mnt/usb/a.png", "/run/user/1000/other/x.png", "/run/user/1000/doc"):
        assert export.host_path(ordinary) == ordinary


# -- guards ------------------------------------------------------------------------
def _package():
    """The folder of the grabbyhands package under test: the checkout, or an installed copy
    (CI's package jobs delete the checkout's and test what the package installed)."""
    from pathlib import Path

    import grabbyhands

    return Path(grabbyhands.__file__).resolve().parent


def _sources():
    sources = sorted(_package().rglob("*.py"))
    assert len(sources) > 30, "the guards below would pass on nothing"
    return sources


def test_nothing_a_user_captured_can_reach_the_log():
    """Log calls may only name things that are safe to show a stranger: never recognized
    text, code contents, pictures, or the names and paths of capture files. An exception's
    own text can quote a file name, so tracebacks with messages are out too."""
    import ast

    private = {
        "text", "texts", "words", "word", "codes", "code", "image", "images", "saved_path",
        "path", "paths", "name", "uri", "preview", "result", "results", "found", "token",
        "content", "contents", "filename", "title", "body", "value",
    }  # fmt: skip
    problems = []
    for source in _sources():
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            target = node.func.value
            if not (isinstance(target, ast.Name) and target.id in ("log", "logging", "logger")):
                continue
            where = f"{source.name}:{node.lineno}"
            if node.func.attr == "exception":
                problems.append(f"{where}: log.exception() prints the error's own text")
            for arg in node.args[1:]:
                for part in ast.walk(arg):
                    named = getattr(part, "id", None) or getattr(part, "attr", None)
                    if named in private:
                        problems.append(f"{where}: logs `{named}`")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
                assert source.name == "application.py", f"{source.name}: print()"
    assert not problems, "\n".join(problems)


def test_nothing_in_the_app_can_reach_the_network():
    """GrabbyHands promises that nothing ever leaves the computer. It keeps that promise by
    not containing anything that could: no networking module is imported anywhere."""
    import ast

    network = {
        "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib",
        "telnetlib", "xmlrpc", "socketserver", "asyncio", "requests", "httpx", "aiohttp",
        "urllib3", "websockets", "paramiko", "Soup",
    }  # fmt: skip
    for source in _sources():
        for node in ast.walk(ast.parse(source.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            for name in names:
                assert name.split(".")[0] not in network, f"{source.name} imports {name}"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert node.value.id != "Soup", f"{source.name} uses libsoup"


TOO_NEW_PYTHON = {
    # name -> the Python version it arrived in (we promise 3.11)
    "batched": "3.12", "override": "3.12", "TypeAliasType": "3.12", "get_original_bases": "3.12",
    "onexc": "3.12", "delete_on_close": "3.12", "sumprod": "3.12", "walk_up": "3.12",
    "full_match": "3.13", "from_uri": "3.13", "process_cpu_count": "3.13", "ReadOnly": "3.13",
    "TypeIs": "3.13", "PythonFinalizationError": "3.13", "ShutDown": "3.13",
    "templatelib": "3.14", "uuid7": "3.14", "Placeholder": "3.14", "annotationlib": "3.14",
    "copy_into": "3.14", "move_into": "3.14",
}  # fmt: skip


def test_no_python_newer_than_3_11():
    """This machine runs a newer Python; these checks are what stands in for a 3.11 test run."""
    import ast
    import io
    import sys
    import tokenize

    problems = []
    for source in _sources():
        text = source.read_text()
        tree = ast.parse(text, feature_version=(3, 11))
        for node in ast.walk(tree):
            name = getattr(node, "attr", None) or getattr(node, "id", None)
            if isinstance(node, ast.keyword):
                name = node.arg
            if isinstance(node, ast.alias):
                name = node.name.rsplit(".", 1)[-1]
            if name in TOO_NEW_PYTHON:
                problems.append(f"{source.name}:{node.lineno}: {name} needs {TOO_NEW_PYTHON[name]}")
            if type(node).__name__ in ("TypeAlias", "TypeVar", "ParamSpec", "TypeVarTuple"):
                problems.append(f"{source.name}:{node.lineno}: type statements need 3.12")
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "walk":
                owner = getattr(node.func.value, "id", "")
                if owner not in ("ast", "os"):
                    problems.append(f"{source.name}:{node.lineno}: Path.walk needs 3.12")
        if sys.version_info < (3, 12):
            continue  # the f-string check below needs the 3.12 tokenizer
        # f-strings: 3.12 allows reusing the outer quote, backslashes and comments inside
        # the braces; 3.11 doesn't even parse those
        outer: list[str] = []
        depth: list[int] = []
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            where = f"{source.name}:{token.start[0]}"
            if token.type == tokenize.FSTRING_START:
                quote = token.string.lstrip("rRfFbBuU")
                if any(quote.startswith(q) for q in outer):
                    problems.append(f"{where}: nested f-string reuses the quote (3.12)")
                outer.append(quote)
                depth.append(0)
            elif token.type == tokenize.FSTRING_END:
                outer.pop()
                depth.pop()
            elif outer and token.type == tokenize.OP and token.string in "{}":
                depth[-1] += 1 if token.string == "{" else -1
            elif outer and depth[-1] > 0:
                if token.type == tokenize.STRING:
                    quote = token.string.lstrip("rRbBuU")
                    quote = quote[:3] if quote[:3] in ('"""', "'''") else quote[:1]
                    if any(quote.startswith(q) for q in outer):
                        problems.append(
                            f"{where}: string inside an f-string reuses its quote (3.12)"
                        )
                    if "\\" in token.string:
                        problems.append(f"{where}: backslash inside an f-string expression (3.12)")
                elif token.type == tokenize.COMMENT:
                    problems.append(f"{where}: comment inside an f-string expression (3.12)")
    assert not problems, "\n".join(problems)


# The toolkit promise is GTK 4.14 and libadwaita 1.5 (Ubuntu 24.04); this machine has much
# newer ones. tests/fixtures/too-new-api.json lists every name that only exists in newer
# versions (made by build-aux/too-new-api.py from the "since" versions in the .gir files).
GUARDED = {
    # too-new name -> the one file that may use it, because it checks first and falls back
    "accent_color": "application.py",
    "accent-color": "application.py",
    "get_accent_color_rgba": "theme.py",
}
# plain Python names that happen to be the same as a new toolkit name
NOT_TOOLKIT = {"suffix"}


def test_no_toolkit_api_newer_than_gtk_4_14_and_libadwaita_1_5():
    import ast
    import re

    from conftest import FIXTURES

    too_new = json.loads((FIXTURES / "too-new-api.json").read_text())
    assert len(too_new) > 300 and "ButtonRow" in too_new and "ToggleGroup" in too_new
    problems = []
    signal_like = re.compile(r"[a-z][a-z0-9-]*(::[a-z][a-z0-9-]*)?")

    def check(name, source, line):
        if name in too_new and name not in NOT_TOOLKIT and GUARDED.get(name) != source.name:
            problems.append(f"{source.name}:{line}: {name} ({too_new[name]})")

    for source in _sources():
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Attribute):
                base = node.value
                ours = node.attr[:1].isupper() and not (
                    isinstance(base, ast.Name) and base.id in ("Adw", "Gtk", "Gdk", "Gio", "GLib")
                )
                if not ours:  # (a class of our own may share a name: ShortcutsDialog)
                    check(node.attr, source, node.lineno)
            elif isinstance(node, ast.keyword) and node.arg:
                check(node.arg, source, node.lineno)  # properties set in a constructor
            elif isinstance(node, ast.Constant) and signal_like.fullmatch(str(node.value)):
                # signal and property names: "notify::accent-color", "close-attempt"
                for part in node.value.split("::"):
                    if "-" in part or "::" in node.value:
                        check(part, source, node.lineno)
                        check(part.replace("-", "_"), source, node.lineno)
    assert not problems, "\n".join(problems)


def test_guarded_new_api_really_is_guarded():
    application = (_package() / "application.py").read_text()
    assert 'hasattr(manager.props, "accent_color")' in application
    theme = (_package() / "theme.py").read_text()
    assert "get_accent_color_rgba" in theme and "except (AttributeError" in theme


# -- plurals -----------------------------------------------------------------------
def test_counted_texts_read_the_same_in_english_and_leave_plurals_to_translations():
    import re

    from grabbyhands import redact
    from grabbyhands.i18n import ngettext

    assert ngettext("{n} word", "{n} words", 1).format(n=1) == "1 word"
    assert ngettext("{n} word", "{n} words", 0).format(n=0) == "0 words"
    assert ngettext("{n} word", "{n} words", 21).format(n=21) == "21 words"
    assert redact.kind_label("email", 1) == "1 email"
    assert redact.kind_label("ip", 3) == "3 IP addresses"
    assert redact.kind_label("secret", 2) == "2 keys or tokens"
    # no "one or many" decided in code any more (that is wrong for most languages)
    either_or = re.compile(r"""_\(["'][^"']*["']\)\s+if\s+\w+\s*==\s*1\s+else\s+_\(""")
    for source in _sources():
        assert not either_or.search(source.read_text()), source.name
