# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The Anthropic API key, kept in the desktop's keyring (GNOME Keyring / the Secret portal).

It is never written to settings, files or logs.
"""

from __future__ import annotations

SCHEMA_NAME = "io.github.al3phnul.GrabbyHands.ApiKey"
ATTRIBUTES = {"service": "anthropic"}
LABEL = "GrabbyHands: Anthropic API key"

_schema = None


def _secret():
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    return Secret


def available() -> bool:
    try:
        _secret()
    except (ImportError, ValueError):
        return False
    return True


def _get_schema():
    global _schema
    if _schema is None:
        Secret = _secret()
        _schema = Secret.Schema.new(
            SCHEMA_NAME, Secret.SchemaFlags.NONE, {"service": Secret.SchemaAttributeType.STRING}
        )
    return _schema


def store(key: str) -> None:
    Secret = _secret()
    Secret.password_store_sync(
        _get_schema(), ATTRIBUTES, Secret.COLLECTION_DEFAULT, LABEL, key.strip(), None
    )


def lookup() -> str | None:
    """The stored key, or None. Blocks briefly on D-Bus: call it off the main thread."""
    try:
        key = _secret().password_lookup_sync(_get_schema(), ATTRIBUTES, None)
    except Exception:  # noqa: BLE001 (any keyring failure means "no key")
        return None
    return key or None


def clear() -> None:
    _secret().password_clear_sync(_get_schema(), ATTRIBUTES, None)


def looks_like_key(text: str) -> bool:
    text = text.strip()
    return text.startswith("sk-ant-") and len(text) > 30 and " " not in text
