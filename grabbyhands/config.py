# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Settings, paths and environment helpers."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from gi.repository import Gio, GLib

from grabbyhands import APP_ID

log = logging.getLogger(__name__)

SOURCE_ROOT = Path(__file__).resolve().parent.parent
_settings: Gio.Settings | None = None


def in_flatpak() -> bool:
    return os.path.exists("/.flatpak-info")


def _dev_schema_source() -> Gio.SettingsSchemaSource | None:
    """Compile data/*.gschema.xml into build/dev-schemas when running from a checkout."""
    data_dir = SOURCE_ROOT / "data"
    if not (data_dir / f"{APP_ID}.gschema.xml").exists():
        return None
    target = SOURCE_ROOT / "build" / "dev-schemas"
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["glib-compile-schemas", "--strict", f"--targetdir={target}", str(data_dir)],
        check=True,
    )
    return Gio.SettingsSchemaSource.new_from_directory(
        str(target), Gio.SettingsSchemaSource.get_default(), False
    )


def settings() -> Gio.Settings:
    global _settings
    if _settings is not None:
        return _settings
    default = Gio.SettingsSchemaSource.get_default()
    if default is not None and default.lookup(APP_ID, True) is not None:
        _settings = Gio.Settings.new(APP_ID)
    else:
        source = _dev_schema_source()
        schema = source.lookup(APP_ID, False) if source else None
        if schema is None:
            raise RuntimeError(f"GSettings schema {APP_ID} is not installed")
        log.info("using development schema from %s", SOURCE_ROOT / "data")
        _settings = Gio.Settings.new_full(schema, None, None)
    return _settings


def data_dir() -> Path:
    """Private app data: capture history, originals, annotations. Only the owner may look
    inside (screenshots can show anything); HistoryStore enforces that on the folder."""
    path = Path(GLib.get_user_data_dir()) / "grabbyhands"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def default_save_folder() -> Path:
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    base = Path(pictures) if pictures else Path.home() / "Pictures"
    return base / "GrabbyHands"


def save_folder() -> Path:
    """Where captures are saved. Created if possible; never raises (a folder that can't be
    created or written shows up as an error when something is actually saved)."""
    custom = settings().get_string("save-folder").strip()
    folder = Path(custom).expanduser() if custom else default_save_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        log.info("save folder %s is not usable: %s", folder, err)
    return folder


def folder_writable(folder: Path) -> bool:
    return folder.is_dir() and os.access(folder, os.W_OK | os.X_OK)


def exec_quote(argument: str) -> str:
    """One argument of a desktop entry's ``Exec`` line, quoted the way the spec wants it.

    A launcher path with a space in it (a home folder named "Jane Doe") must be quoted, or
    the entry starts something else or nothing at all.
    """
    if argument and all(c.isalnum() or c in "_@+=:,./-" for c in argument):
        return argument
    quoted = "".join("\\" + c if c in '"`$\\' else c for c in argument)
    # values in a desktop file have their own escaping on top, and % starts a field code
    return '"' + quoted.replace("\\", "\\\\").replace("%", "%%") + '"'


def autostart_entry(launcher: str) -> str:
    """The desktop entry that starts GrabbyHands in the background at login."""
    return (
        "[Desktop Entry]\nType=Application\nName=GrabbyHands\n"
        f"Exec={exec_quote(launcher)} --background\nIcon={APP_ID}\nNoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def icons_dir() -> Path:
    return Path(__file__).resolve().parent / "icons"
