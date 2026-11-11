# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-redact detection. All data here is made up."""

import pytest
from PIL import Image, ImageDraw, ImageFont

from grabbyhands import ocr
from grabbyhands.ocr import Word
from grabbyhands.redact import find_sensitive, luhn_ok, summary


def line(text: str, top: int = 10, block: int = 1, line_no: int = 1) -> list[Word]:
    """Fake OCR words, 10 px per character, single spaces."""
    words, x = [], 0
    for token in text.split(" "):
        words.append(Word(token, x, top, len(token) * 10, 20, 90.0, block, 1, line_no))
        x += (len(token) + 1) * 10
    return words


def kinds(text: str) -> list[str]:
    return [f.kind for f in find_sensitive(line(text))]


def test_luhn():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("mail me at someone@example.com today", ["email"]),
        ("card 4111 1111 1111 1111 exp 12/29", ["card"]),
        ("card 4111-1111-1111-1111", ["card"]),
        ("not a card 4111 1111 1111 1112", []),
        ("call (555) 123-4567 now", ["phone"]),
        ("call +1 555 123 4567", ["phone"]),
        ("ssn 123-45-6789", ["ssn"]),
        ("server 203.0.113.42 is up", ["ip"]),  # RFC 5737 documentation range
        ("version 1.2.3.400 is not an IP", []),
        ("token ghp_abcdefghijklmnopqrstuvwx1234", ["secret"]),
        ("key sk-proj-AbC123xYz789QwErTy", ["secret"]),
        ("random Xk9fQ2mZ7pL4vR8tY1wN3bC6 here", ["secret"]),
        ("plain words only, nothing to hide here at all", []),
        ("total 1234 items in 2026", []),
    ],
)
def test_detection(text, expected):
    assert kinds(text) == expected, text


def test_box_spans_every_word_of_a_split_match():
    words = line("card 4111 1111 1111 1111 exp")
    (finding,) = find_sensitive(words, pad=0)
    first, last = words[1], words[4]
    assert finding.box == (first.left, first.top, last.left + last.width, last.top + last.height)


def test_matches_never_cross_lines():
    words = line("call 555", line_no=1) + line("123 4567", top=40, line_no=2)
    assert find_sensitive(words) == []


def test_summary_text():
    found = find_sensitive(line("a@b.co c@d.org (555) 123-4567"))
    assert summary(found) == "2 emails and 1 phone number"
    assert summary([]) == ""


@pytest.mark.skipif(not ocr.available(), reason="tesseract not installed")
def test_end_to_end_with_real_ocr():
    img = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default(size=30)
    draw.text((20, 20), "Contact: jane.doe@example.com", fill="black", font=font)
    draw.text((20, 80), "Phone: (555) 123-4567", fill="black", font=font)
    draw.text((20, 140), "Card: 4111 1111 1111 1111", fill="black", font=font)
    found = find_sensitive(ocr.read_words(img))
    assert sorted(f.kind for f in found) == ["card", "email", "phone"], found
    email = next(f for f in found if f.kind == "email")
    assert 130 < email.box[0] < 220 and email.box[1] < 40 < email.box[3]
