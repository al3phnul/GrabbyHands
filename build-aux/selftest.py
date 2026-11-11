#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Check that an installed GrabbyHands has everything it needs. Opens no windows.

    python3 build-aux/selftest.py [--fixtures tests/fixtures] [--expect-missing ocr,qr]

Looks for the installed package (Flatpak, .deb or a Meson prefix) unless PYTHONPATH already
points at one, then checks the Python modules, GTK and libadwaita versions, the settings
schema, image formats, text recognition (Tesseract) and QR reading (zxing-cpp). Used by CI on
every package it builds; also handy when a feature is missing after an install.
"""

from __future__ import annotations

import argparse
import builtins
import os
import sys
from pathlib import Path

# Never connect to a real display: nothing here needs one.
os.environ["GDK_BACKEND"] = "broadway"
os.environ.pop("WAYLAND_DISPLAY", None)
os.environ.pop("DISPLAY", None)

CANDIDATES = [
    "/app/share/grabbyhands",
    "/usr/share/grabbyhands",
    "/usr/local/share/grabbyhands",
    str(Path.home() / ".local/share/grabbyhands"),
]
MIN_PYTHON = (3, 11)
MIN_GTK = (4, 14)
MIN_ADW = (1, 5)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
    return ok


def find_package() -> None:
    try:
        import grabbyhands  # noqa: F401

        return
    except ImportError:
        pass
    for folder in CANDIDATES:
        if (Path(folder) / "grabbyhands" / "__init__.py").exists():
            sys.path.insert(0, folder)
            return


def text_image(lines: list[str]):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (720, 70 * len(lines) + 40), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default(size=34)
    for i, line in enumerate(lines):
        draw.text((24, 24 + i * 70), line, fill="black", font=font)
    return img


def check_platform() -> None:
    record("python", sys.version_info >= MIN_PYTHON, sys.version.split()[0])
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        import cairo
        from gi.repository import Adw, Gdk, Gio, Gtk, Pango, PangoCairo  # noqa: F401
    except (ImportError, ValueError) as err:
        record("gtk", False, str(err))
        return
    gtk = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
    adw = (Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version())
    record("gtk", gtk[:2] >= MIN_GTK, ".".join(map(str, gtk)))
    record("libadwaita", adw[:2] >= MIN_ADW, ".".join(map(str, adw)))
    record("pygobject", True, ".".join(map(str, gi.version_info)))
    record("pycairo", True, cairo.version)


def check_package() -> bool:
    try:
        import grabbyhands
        from grabbyhands import colors, export, imaging, redact, theme  # noqa: F401
        from grabbyhands.editor import document, render  # noqa: F401
    except Exception as err:  # noqa: BLE001 - report whatever stops the import
        return record("grabbyhands", False, f"{type(err).__name__}: {err}")
    where = Path(grabbyhands.__file__).resolve().parent
    record("grabbyhands", True, f"{grabbyhands.__version__} from {where}")
    icons = list((where / "icons").glob("*.svg"))
    record("icons", len(icons) >= 20, f"{len(icons)} bundled")

    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(grabbyhands.APP_ID, True) if source is not None else None
    record("settings schema", schema is not None, "installed" if schema else "not found")
    return True


def check_images() -> None:
    import PIL
    from PIL import Image

    from grabbyhands import export

    record("pillow", True, PIL.__version__)
    img = Image.new("RGBA", (64, 32), (124, 108, 255, 255))
    for fmt, magic in (("png", b"\x89PNG"), ("jpg", b"\xff\xd8"), ("webp", b"RIFF")):
        try:
            ok = export.encode(img, fmt, 80).startswith(magic)
            record(f"save as {fmt}", ok)
        except Exception as err:  # noqa: BLE001
            record(f"save as {fmt}", False, f"{type(err).__name__}: {err}")


def check_ocr(expect_missing: bool) -> None:
    from grabbyhands import ocr

    if expect_missing:
        record("text recognition absent", not ocr.available(), "as expected")
        return
    if not ocr.available():
        record("text recognition", False, "the tesseract command is not installed")
        return
    langs = ocr.languages()
    record("tesseract languages", "eng" in langs, ", ".join(langs) or "none")
    try:
        image = text_image(["Grabby Hands 2026", "copy this text"])
        text = ocr.read_text(image)
        words = ocr.read_words(image)
    except Exception as err:  # noqa: BLE001
        record("text recognition", False, f"{type(err).__name__}: {err}")
        return
    good = "Grabby Hands 2026" in text and "copy this text" in text
    record("text recognition", good, f"{len(words)} words with positions")
    record("word positions", len(words) >= 6 and all(w.width > 0 for w in words))


def check_qr(expect_missing: bool, fixtures: Path | None) -> None:
    from grabbyhands import qr

    if expect_missing:
        record("QR reading absent", not qr.available(), "as expected")
        return
    if not qr.available():
        record("QR reading", False, "the zxingcpp Python module is not installed")
        return
    import zxingcpp
    from PIL import Image

    version = getattr(zxingcpp, "__version__", "unknown version")
    record("zxing-cpp", True, version)
    url = "https://github.com/al3phnul/GrabbyHands"
    try:
        if fixtures is not None:
            image = Image.open(fixtures / "qr-url.png")
        else:
            code = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, url, 240, 240)
            image = Image.fromarray(code).convert("L")
        codes = qr.read_codes(image)
    except Exception as err:  # noqa: BLE001
        record("QR reading", False, f"{type(err).__name__}: {err}")
        return
    good = [c.text for c in codes] == [url] and codes[0].kind == "QR code"
    record("QR reading", good, codes[0].kind if codes else "nothing decoded")
    record("QR on a blank image", qr.read_codes(Image.new("RGB", (120, 120), "white")) == [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fixtures", type=Path, help="folder with qr-url.png (tests/fixtures)")
    parser.add_argument(
        "--expect-missing",
        default="",
        help="comma-separated optional features that must be absent: ocr, qr",
    )
    args = parser.parse_args()
    missing = {name for name in args.expect_missing.split(",") if name}

    builtins.__dict__.setdefault("_", lambda s: s)
    find_package()
    check_platform()
    if check_package():
        check_images()
        check_ocr("ocr" in missing)
        check_qr("qr" in missing, args.fixtures)
    failed = [name for name, ok, _detail in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
