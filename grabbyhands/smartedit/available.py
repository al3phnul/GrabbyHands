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
def _probe() -> tuple[str | None, str]:
    """(version, problem). problem is "" when the library works, "missing" when it isn't
    installed, or the name of what it couldn't load (Ubuntu 26.04's python3-anthropic
    0.79 is installed without the jiter module it needs)."""
    try:
        import anthropic
    except ImportError as err:
        missing = getattr(err, "name", None)
        if missing in (None, "anthropic"):
            return None, "missing"
        return None, missing
    except Exception as err:  # noqa: BLE001 (a broken install must not take the app down)
        return None, type(err).__name__
    return getattr(anthropic, "__version__", "0"), ""


def sdk_version() -> str | None:
    return _probe()[0]


def sdk_problem() -> str:
    """Why the library can't be used: "", "missing", "too-old", or a module it lacks."""
    version, problem = _probe()
    if problem:
        return problem
    if _version_tuple(version or "0") < MIN_SDK:
        return "too-old"
    return ""


def sdk_ok() -> bool:
    return sdk_problem() == ""


def smart_edit_available() -> bool:
    from grabbyhands.smartedit import secret

    return sdk_ok() and secret.available()
