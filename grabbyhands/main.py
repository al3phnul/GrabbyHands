# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Entry point shared by the installed launcher and ``python3 -m grabbyhands``."""

import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")


def main(version: str) -> int:
    level = logging.DEBUG if os.environ.get("GRABBYHANDS_DEBUG") else logging.WARNING
    logging.basicConfig(level=level, format="grabbyhands %(levelname)s %(name)s: %(message)s")

    from grabbyhands.application import GrabbyHandsApp

    app = GrabbyHandsApp(version)
    return app.run(sys.argv)
