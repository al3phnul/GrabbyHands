# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Run from a source checkout: ``python3 -m grabbyhands [options]``."""

import gettext
import os
import sys

gettext.install("grabbyhands")
os.environ.setdefault("GRABBYHANDS_DEV", "1")  # no autostart entry from a checkout

from grabbyhands import __version__, main  # noqa: E402

sys.exit(main.main(__version__))
