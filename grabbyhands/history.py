# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Capture history: originals, annotation documents, thumbnails and an index.

Layout under the app data dir::

    history.json                  index, newest first
    captures/<id>/original.png    the untouched capture
    captures/<id>/document.json   annotations (re-editable), written by the editor
    captures/<id>/thumb.png       gallery thumbnail
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

THUMB_SIZE = (360, 240)
# An id names a folder. Anything else (a damaged index, "../..") must never become a path.
_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")


@dataclass
class Entry:
    id: str
    created: str
    width: int
    height: int
    source: str = "region"
    saved_path: str | None = None
    edited: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def created_dt(self) -> datetime:
        return datetime.fromisoformat(self.created)


class HistoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.captures = root / "captures"
        self.index_path = root / "history.json"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._make_private(root)
        self.captures.mkdir(exist_ok=True)
        self._listeners: list[Callable[[], None]] = []
        self._entries: list[Entry] = self._load()

    @staticmethod
    def _make_private(folder: Path) -> None:
        """Captures can show anything: nobody but the owner gets into their folder."""
        try:
            if stat.S_IMODE(folder.stat().st_mode) & 0o077:
                folder.chmod(0o700)
        except OSError as err:
            log.warning("could not make the history folder private: %s", err.strerror)

    # -- index --------------------------------------------------------------------
    def _load(self) -> list[Entry]:
        """Read the index and make it agree with what is really on disk.

        Entries that can't be understood, or whose pictures are gone, are left out. Captures
        that are on disk but not listed (the index was damaged, or the app died between
        saving a capture and listing it) are listed again, so nothing stays behind unseen.
        """
        raw: object = None
        damaged = False
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text("utf-8"))
            except (OSError, ValueError, RecursionError) as err:
                log.warning("history index unreadable (%s); rebuilding it", type(err).__name__)
            damaged = not isinstance(raw, list)
        entries: list[Entry] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            entry = self._entry_from(item)
            if entry is None or entry.id in seen or not self.original_path(entry.id).is_file():
                damaged = True
                continue
            seen.add(entry.id)
            entries.append(entry)
        recovered = [e for e in map(self._recover, self._unlisted(seen)) if e is not None]
        if recovered:
            entries = sorted(entries + recovered, key=lambda e: e.created, reverse=True)
        if damaged or recovered:
            self._entries = entries
            try:
                self._save()
            except OSError as err:
                log.warning("could not rewrite the history index: %s", err.strerror)
        return entries

    def _entry_from(self, item: object) -> Entry | None:
        if not isinstance(item, dict):
            return None
        entry_id, created = item.get("id"), item.get("created")
        if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id):
            return None
        try:
            datetime.fromisoformat(created)
        except (TypeError, ValueError):
            return None
        width, height = item.get("width"), item.get("height")
        if not all(
            isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)
        ):
            return None
        saved_path, source, extra = item.get("saved_path"), item.get("source"), item.get("extra")
        return Entry(
            id=entry_id,
            created=created,
            width=width,
            height=height,
            source=source if isinstance(source, str) else "region",
            saved_path=saved_path if isinstance(saved_path, str) and saved_path else None,
            edited=item.get("edited") is True,
            extra=extra if isinstance(extra, dict) else {},
        )

    def _unlisted(self, listed: set[str]) -> list[Path]:
        try:
            return sorted(p for p in self.captures.iterdir() if p.name not in listed and p.is_dir())
        except OSError:
            return []

    def _recover(self, folder: Path) -> Entry | None:
        """An entry for a capture folder the index doesn't know, if its picture is intact."""
        if not _ID.fullmatch(folder.name):
            return None
        original = folder / "original.png"
        try:
            with Image.open(original) as image:
                image.load()
                width, height = image.size
                if not (folder / "thumb.png").exists():
                    self.write_thumbnail(folder.name, image)
            try:
                when = datetime.strptime(folder.name[:15], "%Y%m%d-%H%M%S")
            except ValueError:
                when = datetime.fromtimestamp(original.stat().st_mtime)
        except Exception:  # noqa: BLE001 - whatever is wrong with it, it isn't a capture
            return None
        return Entry(
            id=folder.name,
            created=when.isoformat(timespec="seconds"),
            width=width,
            height=height,
            edited=(folder / "document.json").exists(),
        )

    def _save(self) -> None:
        data = json.dumps([asdict(e) for e in self._entries], indent=1)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".history-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, self.index_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)  # no half-written leftovers next to the index
            raise
        for listener in list(self._listeners):
            listener()

    def connect(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def disconnect() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return disconnect

    # -- paths --------------------------------------------------------------------
    def dir_for(self, entry_id: str) -> Path:
        return self.captures / entry_id

    def original_path(self, entry_id: str) -> Path:
        return self.dir_for(entry_id) / "original.png"

    def document_path(self, entry_id: str) -> Path:
        return self.dir_for(entry_id) / "document.json"

    def thumb_path(self, entry_id: str) -> Path:
        return self.dir_for(entry_id) / "thumb.png"

    # -- operations ---------------------------------------------------------------
    def entries(self) -> list[Entry]:
        return list(self._entries)

    def get(self, entry_id: str) -> Entry | None:
        return next((e for e in self._entries if e.id == entry_id), None)

    def _new_id(self, when: datetime) -> str:
        base = when.strftime("%Y%m%d-%H%M%S-%f")
        candidate, n = base, 2
        while self.dir_for(candidate).exists():
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def add(
        self, image: Image.Image, source: str = "region", when: datetime | None = None
    ) -> Entry:
        when = when or datetime.now()
        entry = Entry(
            id=self._new_id(when),
            created=when.isoformat(timespec="seconds"),
            width=image.width,
            height=image.height,
            source=source,
        )
        folder = self.dir_for(entry.id)
        folder.mkdir(parents=True)
        try:
            image.save(self.original_path(entry.id), "PNG")
            self.write_thumbnail(entry.id, image)
            self._entries.insert(0, entry)
            self._save()
        except BaseException:
            # e.g. the disk is full: no half-saved capture stays behind
            self._entries = [e for e in self._entries if e.id != entry.id]
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return entry

    def write_thumbnail(self, entry_id: str, image: Image.Image) -> None:
        thumb = image.convert("RGBA").copy()
        thumb.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
        thumb.save(self.thumb_path(entry_id), "PNG")

    def update(self, entry: Entry) -> None:
        for i, existing in enumerate(self._entries):
            if existing.id == entry.id:
                self._entries[i] = entry
                break
        self._save()

    def remove(self, entry_id: str) -> Entry | None:
        entry = self.get(entry_id)
        if entry is None:
            return None
        self._entries = [e for e in self._entries if e.id != entry_id]
        shutil.rmtree(self.dir_for(entry_id), ignore_errors=True)
        self._save()
        return entry

    def clear(self) -> list[Entry]:
        """Remove every entry; returns what was removed."""
        removed, self._entries = self._entries, []
        # Everything in the captures folder goes, listed or not: after "Clear History"
        # nothing that was ever captured is left on disk.
        with contextlib.suppress(OSError):
            for child in list(self.captures.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        child.unlink()
        self._save()
        return removed

    def prune(self, limit: int) -> list[str]:
        """Drop the oldest entries beyond ``limit`` (0 = keep everything)."""
        if limit <= 0 or len(self._entries) <= limit:
            return []
        dropped = self._entries[limit:]
        self._entries = self._entries[:limit]
        for entry in dropped:
            shutil.rmtree(self.dir_for(entry.id), ignore_errors=True)
        self._save()
        return [e.id for e in dropped]
