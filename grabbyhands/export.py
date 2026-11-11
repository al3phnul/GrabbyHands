# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Encoding and saving images: file names, formats, quality and scaling."""

from __future__ import annotations

import contextlib
import errno
import io
import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image

FORMATS = {
    "png": ("PNG", ".png", "image/png"),
    "jpg": ("JPEG", ".jpg", "image/jpeg"),
    "webp": ("WEBP", ".webp", "image/webp"),
}

_UNSAFE = re.compile(r"[\\/\x00-\x1f]")
# Most file systems stop at 255 bytes per name; this leaves room for "-2" and ".webp".
MAX_NAME_BYTES = 200


def format_from_path(path: str | Path, default: str = "png") -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix == "jpeg":
        suffix = "jpg"
    return suffix if suffix in FORMATS else default


def make_filename(pattern: str, when: datetime | None = None) -> str:
    """Expand strftime codes and strip characters that can't be in a file name."""
    when = when or datetime.now()
    try:
        name = when.strftime(pattern or "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        name = when.strftime("%Y-%m-%d_%H-%M-%S")
    name = _UNSAFE.sub("-", name)
    name = name.encode("utf-8", "ignore")[:MAX_NAME_BYTES].decode("utf-8", "ignore").strip(" .")
    return name or when.strftime("%Y-%m-%d_%H-%M-%S")


# Inside a Flatpak, a folder picked in the file chooser is reached through the document
# portal: /run/user/1000/doc/1a2b3c4d/Screenshots. That path means nothing to a person.
_PORTAL_PATH = re.compile(r"/run/user/\d+/doc/[0-9A-Za-z]+/(.+)")


def host_path(path: str | Path) -> str:
    """``path`` as the user knows it. Only different for a document-portal path: then it
    is the real location if the portal tells (newer ones do), else the part the user chose
    (the folder's own name onwards)."""
    text = str(path)
    portal = _PORTAL_PATH.fullmatch(text)
    if portal is None:
        return text
    try:
        real = os.getxattr(text, "user.document-portal.host-path")
        real_text = real.rstrip(b"\0").decode("utf-8")
        if real_text.startswith("/"):
            return real_text
    except (OSError, AttributeError, UnicodeDecodeError):
        pass
    return portal.group(1)


def display_path(path: str | Path) -> str:
    """A path the way people write it: ``~/Pictures/GrabbyHands/shot.png``."""
    text = host_path(path)
    home = str(Path.home())
    if text == home or text.startswith(home + "/"):
        return "~" + text[len(home) :]
    return text


def unique_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def path_for_format(path: Path, fmt: str) -> Path:
    """The file a Save As choice really means.

    A name that ends in a picture extension we write (``.png``, ``.jpg``, ``.jpeg``,
    ``.webp``) is kept, and that extension decides the format. Any other name gets the
    chosen format's extension added: ``Report 11.2026`` becomes ``Report 11.2026.jpg``
    instead of a file with no usable extension. A name made that way never replaces an
    existing file (the file chooser only asked about the name the user typed).
    """
    if format_from_path(path, default=""):
        return path
    suffix = FORMATS.get(fmt, FORMATS["png"])[1]
    return unique_path(path.parent, path.name, suffix)


def _umask() -> int:
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    current = os.umask(0o022)  # no way to read it without setting it
    os.umask(current)
    return current


def write_file(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, all or nothing.

    The bytes go to a temporary file next to it, which then takes the place of ``path``.
    A full disk or a crash half-way leaves an existing picture as it was (not cut short),
    and never leaves a broken new one behind.
    """
    if path.is_symlink():
        path = Path(os.path.realpath(path))
    exists = path.exists()
    if exists and not os.access(path, os.W_OK):
        # Replacing a file needs no permission on the file itself, only on its folder.
        # Respect a picture the user made read-only all the same.
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
    mode = stat.S_IMODE(path.stat().st_mode) if exists else 0o666 & ~_umask()
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".grabbyhands-", suffix=".part")
    except OSError:
        if not exists:
            raise
        path.write_bytes(data)  # e.g. a writable file in a folder we can't add files to
        return
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with contextlib.suppress(OSError):  # not every file system has permissions
            os.chmod(tmp, mode)
        try:
            os.replace(tmp, path)
        except OSError:
            # Some network shares and phones can't put one file in another's place. The
            # data is complete (it is all in the temporary file), so write it directly.
            path.write_bytes(data)
            os.unlink(tmp)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def scale_image(image: Image.Image, scale_percent: int = 100, max_width: int = 0) -> Image.Image:
    w, h = image.size
    factor = max(1, min(scale_percent, 100)) / 100
    if max_width and w * factor > max_width:
        factor = max_width / w
    if factor >= 0.999:
        return image
    size = (max(1, round(w * factor)), max(1, round(h * factor)))
    return image.resize(size, Image.Resampling.LANCZOS)


def encode(
    image: Image.Image,
    fmt: str = "png",
    quality: int = 90,
    scale_percent: int = 100,
    max_width: int = 0,
) -> bytes:
    """Encode to bytes. Metadata is never copied over."""
    pil_format = FORMATS.get(fmt, FORMATS["png"])[0]
    image = scale_image(image, scale_percent, max_width)
    out = io.BytesIO()
    if pil_format == "JPEG":
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.convert("RGBA").split()[-1])
            image = background
        image.convert("RGB").save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    elif pil_format == "WEBP":
        image.save(out, "WEBP", quality=quality, method=4)
    else:
        image.save(out, "PNG", optimize=False, compress_level=6)
    return out.getvalue()


def save_image(
    image: Image.Image,
    folder: Path,
    pattern: str,
    fmt: str = "png",
    quality: int = 90,
    when: datetime | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    suffix = FORMATS.get(fmt, FORMATS["png"])[1]
    path = unique_path(folder, make_filename(pattern, when), suffix)
    write_file(path, encode(image, fmt, quality))
    return path


def write_image(
    image: Image.Image, path: Path, quality: int = 90, scale_percent: int = 100, max_width: int = 0
) -> Path:
    fmt = format_from_path(path)
    write_file(path, encode(image, fmt, quality, scale_percent, max_width))
    return path
