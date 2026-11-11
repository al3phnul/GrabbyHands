# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Knowing when an Adw.Dialog has closed, on every libadwaita GrabbyHands supports."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk


def when_closed(dialog: Adw.Dialog, callback: Callable[[], None]) -> None:
    """Call ``callback`` once, when ``dialog`` has closed.

    "closed" is the signal for that, but libadwaita 1.5.0 (Ubuntu 24.04) only emits it for
    a dialog shown inside an Adw window. A dialog that got a window of its own (it was
    opened with no window to sit on, or over a plain one such as the capture screen) closes
    silently there. So for such a dialog its window going away counts too.

    Works whether it is called before or after ``present()``.
    """
    done = False
    handlers: list[int] = []

    def fire(*_args) -> None:
        nonlocal done
        if not done:
            done = True
            callback()

    def watch_own_window(shown: Adw.Dialog) -> None:
        if handlers:
            shown.disconnect(handlers.pop())
        root = shown.get_root()
        if isinstance(root, Gtk.Window) and root.get_child() == shown:
            root.connect("unmap", fire)

    dialog.connect("closed", fire)
    if dialog.get_mapped():
        watch_own_window(dialog)
    else:
        handlers.append(dialog.connect("map", watch_own_window))
