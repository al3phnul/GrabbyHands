# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Can Smart Edit run here? It needs Anthropic's Python library and a keyring.

Without them Smart Edit is simply hidden, the same way text recognition is without Tesseract.
"""

from __future__ import annotations

import functools
import re

MIN_SDK = (0, 79)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.split(".")[:3]:
        match = re.match(r"\d+", piece)  # "0rc1" -> 0
        parts.append(int(match.group()) if match else 0)
    return tuple(parts)


@functools.cache
def sdk_version() -> str | None:
    try:
        import anthropic
    except ImportError:
        return None
    return getattr(anthropic, "__version__", "0")


def sdk_ok() -> bool:
    version = sdk_version()
    return version is not None and _version_tuple(version) >= MIN_SDK


def smart_edit_available() -> bool:
    from grabbyhands.smartedit import secret

    return sdk_ok() and secret.available()
