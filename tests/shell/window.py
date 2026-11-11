# SPDX-License-Identifier: GPL-3.0-or-later
"""A plain window with a telling heading, for tests/shell_smoke.py."""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

loop = GLib.MainLoop()
window = Gtk.Window(title=sys.argv[1], default_width=420, default_height=260)
window.connect("close-request", lambda *_: loop.quit())
window.present()
loop.run()
