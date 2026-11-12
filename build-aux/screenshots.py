#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render GrabbyHands' real windows to PNG files, without touching the real desktop.

    python3 build-aux/screenshots.py --out /tmp/shots [--scheme ocean] [--dark]
                                     [--only editor,history]

The scenes live in build-aux/screenshot_scenes.py. Everything runs on a private GTK Broadway
display with a fake screenshot portal, so the pictures show the actual widgets and drawing
code. Used for reviewing the UI and for the README / metainfo screenshots.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import broadway  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="folder for the PNG files")
    parser.add_argument("--scheme", default="violet", help="color scheme id (default: violet)")
    parser.add_argument("--dark", action="store_true", help="dark style")
    parser.add_argument("--only", default="", help="comma-separated scene names (default: all)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    inner = [str(args.out.resolve()), args.scheme, "dark" if args.dark else "light", args.only]
    result = broadway.run(ROOT / "build-aux" / "screenshot_scenes.py", inner, timeout=300)
    shown = [
        line
        for line in result.stdout.splitlines()
        if line.startswith(("saved", "SKIPPED", "FAILED"))
    ]
    print("\n".join(shown))
    if result.returncode != 0:
        print(result.stdout[-3000:], file=sys.stderr)
        print(result.stderr[-6000:], file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
