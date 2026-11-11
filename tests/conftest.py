# SPDX-License-Identifier: GPL-3.0-or-later
import builtins
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The launcher installs gettext's `_`; tests use the identity function.
builtins.__dict__.setdefault("_", lambda s: s)
