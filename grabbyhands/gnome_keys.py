# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Who owns the Print Screen key on GNOME, and handing it to GrabbyHands.

GNOME's own screenshot tool is bound to Print Screen (``org.gnome.shell.keybindings`` →
``show-screenshot-ui``). While it is, pressing Print Screen opens GNOME's tool, not ours.
With the user's explicit consent (a button click, never on our own) we move GNOME's tool
to Ctrl+Print Screen so the plain key is free. Everything here degrades gracefully: no
schema (not GNOME), a sandbox (Flatpak can't write host settings) or a locked key simply
mean "can't do it here", and the caller shows a command to copy instead.
"""

from __future__ import annotations

import logging
import os

from gi.repository import Gio

from grabbyhands.config import in_flatpak

log = logging.getLogger(__name__)

SCHEMA = "org.gnome.shell.keybindings"
KEY = "show-screenshot-ui"
MOVED_TO = "<Ctrl>Print"

# Status of the Print Screen key, as far as we can tell.
HELD = "held"  # GNOME's own screenshot tool is bound to plain Print Screen
FREE = "free"  # GNOME's tool uses something else (or nothing)
UNKNOWN = "unknown"  # GNOME, but its settings can't be read from here (Flatpak)
OTHER = "other"  # not a GNOME desktop


# -- pure helpers (no settings access; safe to unit-test anywhere) ---------------------
def _normalized(accel: str) -> str:
    text = accel.strip().lower()
    for alias in ("<control>", "<primary>"):
        text = text.replace(alias, "<ctrl>")
    return text


def is_plain_print(accel: str) -> bool:
    """True for the Print Screen key with no modifiers."""
    return _normalized(accel) == "print"


def holds_print(accels: list[str]) -> bool:
    return any(is_plain_print(a) for a in accels)


def moved(accels: list[str]) -> list[str]:
    """``accels`` without plain Print Screen; Ctrl+Print Screen takes its place.

    Any other shortcuts the user gave GNOME's tool are kept.
    """
    kept = [a for a in accels if not is_plain_print(a)]
    if not any(_normalized(a) == _normalized(MOVED_TO) for a in kept):
        kept.insert(0, MOVED_TO)
    return kept


def command() -> str:
    """What to run in a terminal when we can't change the setting ourselves."""
    return f"gsettings set {SCHEMA} {KEY} \"['{MOVED_TO}']\""


def undo_command() -> str:
    return f"gsettings reset {SCHEMA} {KEY}"


def on_gnome(desktop: str | None = None) -> bool:
    """True on GNOME and its flavors (``XDG_CURRENT_DESKTOP`` such as ``ubuntu:GNOME``)."""
    if desktop is None:
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return "gnome" in desktop.lower().split(":")


def _known_other_desktop() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return bool(desktop.strip()) and not on_gnome(desktop)


# -- settings access -------------------------------------------------------------------
def _settings() -> Gio.Settings | None:
    """GNOME Shell's keybinding settings, or None when they aren't reachable from here."""
    if in_flatpak() or _known_other_desktop():
        return None
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(SCHEMA, True) if source is not None else None
    if schema is None or not schema.has_key(KEY):
        return None
    if schema.get_key(KEY).get_value_type().dup_string() != "as":
        return None
    return Gio.Settings.new(SCHEMA)


def current() -> list[str] | None:
    """The shortcuts of GNOME's own screenshot tool, or None if unknown."""
    settings = _settings()
    return list(settings.get_strv(KEY)) if settings is not None else None


def status() -> str:
    accels = current()
    if accels is None:
        # Only a sandbox hides a GNOME desktop's settings from us. Anywhere else, no
        # GNOME Shell keybindings means there is no GNOME screenshot tool to move.
        return UNKNOWN if in_flatpak() and on_gnome() else OTHER
    return HELD if holds_print(accels) else FREE


def can_change() -> bool:
    settings = _settings()
    return settings is not None and settings.is_writable(KEY)


def is_customized() -> bool:
    """True when GNOME's tool no longer has its default shortcut (so it can be reset)."""
    settings = _settings()
    return settings is not None and settings.get_user_value(KEY) is not None


def release_print() -> list[str] | None:
    """Move GNOME's own screenshot tool off plain Print Screen.

    Only ever called from an explicit button click. Returns the previous shortcuts (for
    Undo), or None when nothing was changed.
    """
    settings = _settings()
    if settings is None or not settings.is_writable(KEY):
        return None
    previous = list(settings.get_strv(KEY))
    if not holds_print(previous):
        return None
    settings.set_strv(KEY, moved(previous))
    Gio.Settings.sync()
    log.info("moved GNOME's screenshot shortcut off Print Screen")
    return previous


def restore(previous: list[str] | None = None) -> bool:
    """Undo :func:`release_print`: put ``previous`` back, or GNOME's default if None."""
    settings = _settings()
    if settings is None or not settings.is_writable(KEY):
        return False
    if previous is None:
        settings.reset(KEY)
    else:
        settings.set_strv(KEY, previous)
    Gio.Settings.sync()
    log.info("restored GNOME's screenshot shortcut")
    return True


def describe(accels: list[str]) -> str:
    """Human-readable shortcuts, e.g. ``Ctrl+Print``."""
    from gi.repository import Gtk

    labels = []
    for accel in accels:
        ok, keyval, mods = Gtk.accelerator_parse(accel)
        labels.append(Gtk.accelerator_get_label(keyval, mods) if ok and keyval else accel)
    return ", ".join(labels)
