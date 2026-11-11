# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Plural forms.

``_`` is installed by the launcher. Texts with a number in them need more than that: many
languages have several plural forms (and some use the "singular" for 21, 31…), so "is it 1?"
can't be decided in code. ``ngettext`` leaves the choice to the translation.
"""

from __future__ import annotations

import gettext

DOMAIN = "grabbyhands"


def ngettext(singular: str, plural: str, n: int) -> str:
    """``singular`` or ``plural`` (or a translation's form) for ``n``. Both forms carry the
    number as ``{n}``: ``ngettext("{n} word", "{n} words", n).format(n=n)``."""
    return gettext.dngettext(DOMAIN, singular, plural, n)
