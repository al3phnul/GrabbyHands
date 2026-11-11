# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Which key was pressed, whatever keyboard layout is active.

GrabbyHands' own keys are letters (C to copy, T for the Text tool, Ctrl+C…). With a Russian,
Greek, Arabic or Hebrew layout active, the same physical keys produce other letters and none
of those keys would do anything. ``latin_keyval`` answers "which Latin letter is this key?".
"""

from __future__ import annotations

import unicodedata

from gi.repository import Gdk

# Hardware key codes of the letter keys (the same on Wayland and X11), by the letter a US
# keyboard prints on them. Only used when no Latin layout is set up at all.
_QWERTY = {
    24: "q", 25: "w", 26: "e", 27: "r", 28: "t", 29: "y", 30: "u", 31: "i", 32: "o", 33: "p",
    38: "a", 39: "s", 40: "d", 41: "f", 42: "g", 43: "h", 44: "j", 45: "k", 46: "l",
    52: "z", 53: "x", 54: "c", 55: "v", 56: "b", 57: "n", 58: "m",
}  # fmt: skip


def _is_latin_letter(keyval: int) -> bool:
    return ord("a") <= keyval <= ord("z") or ord("A") <= keyval <= ord("Z")


def _layout_entries(keycode: int) -> list[tuple[int, int]]:
    """(level, keyval) for everything the hardware key produces in the user's layouts."""
    display = Gdk.Display.get_default()
    if display is None or not keycode:
        return []
    try:
        found, keymap_keys, keyvals = display.map_keycode(keycode)
    except (TypeError, ValueError):
        return []
    if not found:
        return []
    return [(key.level, keyval) for key, keyval in zip(keymap_keys, keyvals, strict=False)]


def latin_keyval(
    keyval: int, keycode: int = 0, entries: list[tuple[int, int]] | None = None
) -> int:
    """The key value to match single-letter keys and Ctrl+letter against.

    Unchanged for anything typed on a Latin layout (also its own letters such as é, ö or
    ğ, wherever they sit), and for keys that aren't letters. For a letter of another script
    it is the Latin letter the same physical key gives in one of the user's other layouts;
    without any Latin layout, the letter in that position on a US keyboard. ``entries``
    (``(level, keyval)`` pairs for the key) is looked up from the display when not given.
    """
    if keyval < 0x80 or (keyval & 0xFF00) == 0xFF00:
        return keyval  # ASCII, or a function, cursor, keypad or modifier key
    char = chr(Gdk.keyval_to_unicode(keyval) or 0)
    if not char.isalpha() or unicodedata.name(char, "").startswith("LATIN"):
        return keyval
    if entries is None:
        entries = _layout_entries(keycode)
    for level in (0, 1):
        for entry_level, candidate in entries:
            if entry_level == level and _is_latin_letter(candidate):
                return Gdk.keyval_to_lower(candidate)
    letter = _QWERTY.get(keycode)
    if letter is not None and not any(_is_latin_letter(v) for _lvl, v in entries):
        return ord(letter)
    return keyval
