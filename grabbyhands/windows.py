# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The GrabbyHands Window Helper GNOME Shell extension, seen from the app.

Wayland hides window geometry from apps, so native window capture needs this helper
(data/shell-extension). It returns positions and app names only. The helper can also make
the screenshot button in GNOME's system menu start GrabbyHands (``menu_button``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

EXTENSION_UUID = "grabbyhands@al3phnul.github.io"
BUS_NAME = "io.github.al3phnul.GrabbyHands.Shell"
OBJECT_PATH = "/io/github/al3phnul/GrabbyHands/Shell"
INTERFACE = "io.github.al3phnul.GrabbyHands.Shell"


@dataclass(frozen=True)
class WindowRect:
    """A window's frame in logical layout coordinates (same space as Gdk monitor geometry)."""

    x: int
    y: int
    width: int
    height: int
    app: str = ""

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def _bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def available() -> bool:
    """True when the helper extension is enabled and answering."""
    bus = _bus()
    if bus is None:
        return False
    try:
        reply = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (BUS_NAME,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            500,
            None,
        )
    except GLib.Error:
        return False
    return bool(reply.unpack()[0])


def get_windows() -> list[WindowRect]:
    """Visible windows on the current workspace, top-most first ([] if unavailable)."""
    bus = _bus()
    if bus is None:
        return []
    try:
        reply = bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            "GetWindows",
            None,
            GLib.VariantType.new("(a(iiiis))"),
            Gio.DBusCallFlags.NO_AUTO_START,
            500,
            None,
        )
    except GLib.Error as err:
        log.debug("window helper unavailable: %s", err.message)
        return []
    return [WindowRect(*item) for item in reply.unpack()[0]]


def get_stage_size() -> tuple[int, int] | None:
    """The size of the compositor's whole canvas, in the units ``get_windows`` reports in.

    None without the helper, or with a helper from before it could answer this.
    """
    bus = _bus()
    if bus is None:
        return None
    try:
        reply = bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            "GetStageSize",
            None,
            GLib.VariantType.new("(ii)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            500,
            None,
        )
    except GLib.Error:
        return None
    width, height = reply.unpack()
    return (width, height) if width > 0 and height > 0 else None


def menu_button() -> str:
    """Whether the screenshot button in GNOME's system menu starts GrabbyHands.

    ``"on"`` or ``"off"``: the user's choice, kept by the helper. Otherwise why there is no
    choice: ``"no-helper"`` (the helper is off or missing), ``"old-helper"`` (GNOME still
    runs a helper from before it could do this, until the next login) or ``"unavailable"``
    (this GNOME has no such button where the helper looks for it).
    """
    bus = _bus()
    if bus is None or not available():
        return "no-helper"
    try:
        reply = bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            "GetMenuButton",
            None,
            GLib.VariantType.new("(bb)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            500,
            None,
        )
    except GLib.Error as err:
        if err.matches(Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD):
            return "old-helper"
        log.debug("window helper did not answer about the menu button: %s", err.message)
        return "no-helper"
    enabled, possible = reply.unpack()
    if not possible:
        return "unavailable"
    return "on" if enabled else "off"


def set_menu_button(enabled: bool) -> bool:
    """Ask the helper to swap (or give back) the system menu's screenshot button.

    True when the button is now as asked.
    """
    bus = _bus()
    if bus is None:
        return False
    try:
        reply = bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            "SetMenuButton",
            GLib.Variant("(b)", (enabled,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            2000,
            None,
        )
    except GLib.Error as err:
        log.debug("window helper could not change the menu button: %s", err.message)
        return False
    return bool(reply.unpack()[0])


def to_layout(
    windows: list[WindowRect], stage: tuple[int, int] | None, monitors: list
) -> list[WindowRect]:
    """Window positions in the coordinates the app's monitors use (logical pixels).

    GNOME reports windows in "stage" units. Mostly those are logical pixels already. On a
    HiDPI screen without fractional scaling (the default up to GNOME 48) they are physical
    pixels, twice what the app sees, and every window would be highlighted in the wrong
    place at double size. Comparing the stage with the monitors' combined size (``monitors``
    are their geometries: x, y, width, height) tells which case this is.
    """
    if not windows or not stage or not monitors:
        return windows
    ox = min(g.x for g in monitors)
    oy = min(g.y for g in monitors)
    span_w = max(g.x + g.width for g in monitors) - ox
    span_h = max(g.y + g.height for g in monitors) - oy
    stage_w, stage_h = stage
    if min(stage_w, stage_h, span_w, span_h) <= 0 or (stage_w, stage_h) == (span_w, span_h):
        return windows
    fx, fy = span_w / stage_w, span_h / stage_h
    return [
        WindowRect(
            round(ox + w.x * fx),
            round(oy + w.y * fy),
            round(w.width * fx),
            round(w.height * fy),
            w.app,
        )
        for w in windows
    ]


def window_at(windows: list[WindowRect], x: float, y: float) -> WindowRect | None:
    """The top-most window under a point (``windows`` must be top-most first)."""
    return next((w for w in windows if w.contains(x, y)), None)
