# SPDX-License-Identifier: GPL-3.0-or-later
import re
from datetime import datetime

import pytest
from conftest import FIXTURES, ROOT
from PIL import Image, ImageDraw, ImageFont

from grabbyhands import __version__, colors, export, ocr, qr
from grabbyhands.history import HistoryStore
from grabbyhands.imaging import Rect, ScreenImage, surface_to_pil


def test_version_matches_meson():
    meson = (ROOT / "meson.build").read_text()
    assert re.search(r"version:\s*'([^']+)'", meson).group(1) == __version__


# -- colors ------------------------------------------------------------------------
def test_color_formats():
    assert colors.format_color((124, 108, 255), "hex") == "#7c6cff"
    assert colors.format_color((124, 108, 255), "rgb") == "rgb(124, 108, 255)"
    assert colors.format_color((255, 0, 0), "hsl") == "hsl(0, 100%, 50%)"
    assert colors.format_color((124, 108, 255), "cmyk") == "cmyk(51%, 58%, 0%, 0%)"
    assert colors.to_cmyk((0, 0, 0)) == (0, 0, 0, 100)
    assert colors.to_cmyk((255, 255, 255)) == (0, 0, 0, 0)
    assert colors.parse_hex("#fff") == (1.0, 1.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        colors.parse_hex("nope")


# -- geometry & pixels -------------------------------------------------------------
def test_rect_helpers():
    assert Rect.from_points(10, 20, 4, 2) == Rect(4, 2, 6, 18)
    assert Rect(-5, -5, 20, 20).clamp(10, 10) == Rect(0, 0, 10, 10)
    assert Rect(0, 0, 0, 5).empty


def test_screen_image_pixel_and_crop():
    img = Image.new("RGB", (40, 30), (10, 20, 30))
    img.putpixel((5, 7), (124, 108, 255))
    screen = ScreenImage.from_pil(img)
    assert screen.pixel(5, 7) == (124, 108, 255)
    assert screen.pixel(0, 0) == (10, 20, 30)
    crop = screen.crop(Rect(5, 7, 10, 5))
    assert crop.size == (10, 5)
    assert crop.getpixel((0, 0)) == (124, 108, 255)
    # round trip through cairo keeps colors
    assert surface_to_pil(screen.surface).convert("RGB").getpixel((5, 7)) == (124, 108, 255)


# -- export ------------------------------------------------------------------------
def test_filename_pattern_and_unique(tmp_path):
    when = datetime(2026, 11, 10, 14, 3, 22)
    assert export.make_filename("%Y-%m-%d_%H-%M-%S", when) == "2026-11-10_14-03-22"
    assert export.make_filename("shot/%H:%M", when) == "shot-14:03"
    (tmp_path / "a.png").write_bytes(b"x")
    assert export.unique_path(tmp_path, "a", ".png").name == "a-2.png"


@pytest.mark.parametrize(
    "fmt,magic", [("png", b"\x89PNG"), ("jpg", b"\xff\xd8"), ("webp", b"RIFF")]
)
def test_encode_formats(fmt, magic):
    img = Image.new("RGBA", (64, 32), (124, 108, 255, 255))
    assert export.encode(img, fmt, 80).startswith(magic)


def test_scale_and_save(tmp_path):
    img = Image.new("RGB", (400, 200), "white")
    assert export.scale_image(img, 50).size == (200, 100)
    assert export.scale_image(img, 100, max_width=100).size == (100, 50)
    path = export.save_image(img, tmp_path, "%Y", "jpg", 70, datetime(2026, 1, 1))
    assert path.name == "2026.jpg" and Image.open(path).format == "JPEG"
    out = export.write_image(img, tmp_path / "x.webp", 60, scale_percent=25)
    assert Image.open(out).size == (100, 50)


# -- history -----------------------------------------------------------------------
def test_history_add_update_prune(tmp_path):
    store = HistoryStore(tmp_path)
    changes = []
    store.connect(lambda: changes.append(1))
    first = store.add(Image.new("RGB", (800, 600), "red"), when=datetime(2026, 11, 10, 9))
    second = store.add(Image.new("RGB", (10, 10), "blue"), when=datetime(2026, 11, 10, 10))
    assert [e.id for e in store.entries()] == [second.id, first.id]
    assert store.original_path(first.id).exists()
    assert Image.open(store.thumb_path(first.id)).width <= 360
    first.saved_path = "/tmp/x.png"
    store.update(first)
    assert HistoryStore(tmp_path).get(first.id).saved_path == "/tmp/x.png"
    assert store.prune(1) == [first.id]
    assert not store.dir_for(first.id).exists()
    assert len(changes) >= 4
    store.add(Image.new("RGB", (5, 5)))
    removed = store.clear()
    assert len(removed) == 2 and store.entries() == []
    assert HistoryStore(tmp_path).entries() == []


# -- QR & OCR ----------------------------------------------------------------------
@pytest.mark.skipif(not qr.available(), reason="zxing-cpp not installed")
def test_qr_reads_fixture():
    codes = qr.read_codes(Image.open(FIXTURES / "qr-url.png"))
    assert [c.text for c in codes] == ["https://github.com/al3phnul/GrabbyHands"]
    assert codes[0].kind == "QR code"


@pytest.mark.skipif(not qr.available(), reason="zxing-cpp not installed")
def test_qr_nothing_found():
    assert qr.read_codes(Image.new("RGB", (120, 120), "white")) == []


def _text_image(lines: list[str], dark: bool = False) -> Image.Image:
    img = Image.new("RGB", (720, 70 * len(lines) + 40), "#16123a" if dark else "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default(size=34)
    for i, line in enumerate(lines):
        draw.text((24, 24 + i * 70), line, fill="white" if dark else "black", font=font)
    return img


@pytest.mark.skipif(not ocr.available(), reason="tesseract not installed")
@pytest.mark.parametrize("dark", [False, True])
def test_ocr_reads_text(dark):
    text = ocr.read_text(_text_image(["Grabby Hands 2026", "copy this text"], dark))
    assert "Grabby Hands 2026" in text
    assert "copy this text" in text


@pytest.mark.skipif(not ocr.available(), reason="tesseract not installed")
def test_ocr_words_have_boxes_in_original_coordinates():
    img = _text_image(["mail me at someone@example.com"])
    words = ocr.read_words(img)
    email = next(w for w in words if "@" in w.text)
    assert 0 <= email.left < img.width and 0 <= email.top < img.height
    assert email.left + email.width <= img.width + 2


# -- window helper -----------------------------------------------------------------
def test_window_at_picks_topmost():
    from grabbyhands.windows import WindowRect, window_at

    top = WindowRect(100, 100, 200, 100, "Top")
    below = WindowRect(0, 0, 800, 600, "Below")
    assert window_at([top, below], 150, 150) is top
    assert window_at([top, below], 50, 50) is below
    assert window_at([top, below], 900, 50) is None
    assert not top.contains(300, 150)  # right edge is exclusive


def test_shell_extension_metadata():
    import json

    from grabbyhands import windows

    ext = ROOT / "data" / "shell-extension"
    meta = json.loads((ext / "metadata.json").read_text())
    assert meta["uuid"] == windows.EXTENSION_UUID
    assert "50" in meta["shell-version"]
    source = (ext / "extension.js").read_text()
    assert windows.BUS_NAME in source and windows.OBJECT_PATH in source
    assert "a(iiiis)" in source
    assert "get_title" not in source  # privacy promise: no window titles


def test_join_words_rebuilds_lines_and_paragraphs():
    from grabbyhands.ocr import Word, join_words

    def w(text, block, par, line):
        return Word(text, 0, 0, 1, 1, 90.0, block, par, line)

    words = [w("Hello", 1, 1, 1), w("there", 1, 1, 1), w("next", 1, 1, 2), w("New", 2, 1, 1)]
    assert join_words(words) == "Hello there\nnext\n\nNew"
    assert join_words(words[1:3]) == "there\nnext"
    assert join_words([]) == ""


# -- theme ---------------------------------------------------------------------------
def test_every_scheme_has_readable_colours():
    from grabbyhands import theme

    for scheme in theme.schemes():
        p = theme.palette(scheme.id)
        assert p.accent.startswith("#") and len(p.accent) == 7, scheme.id
        assert theme.contrast(p.accent, p.on_accent) >= 3.0, (scheme.id, p.accent, p.on_accent)
        assert theme.contrast(p.chrome, p.chrome_fg) >= 7.0, scheme.id
    assert theme.palette("nonsense").scheme == "violet"
    assert theme.text_on("#ffffff") != "#ffffff" and theme.text_on("#000000") == "#ffffff"
    assert theme.mix("#000000", "#ffffff", 0.5) == "#808080"
