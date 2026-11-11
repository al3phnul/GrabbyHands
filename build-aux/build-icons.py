#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the app's symbolic icons from data/icons/src (maintainers only; needs Inkscape).

GTK's symbolic icon renderer fills shapes and ignores strokes, so the hand-drawn sources
(which use strokes) are converted to filled paths. The results in grabbyhands/icons are
committed, so building GrabbyHands itself never needs Inkscape.
"""

import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "icons" / "src"
OUT = ROOT / "grabbyhands" / "icons"
SVG = "http://www.w3.org/2000/svg"
HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->\n'


def clean(path: Path) -> str:
    """Keep only what GTK needs: paths with fill, fill-rule and opacity."""
    ET.register_namespace("", SVG)
    tree = ET.parse(path)
    shapes = []
    for el in tree.iter(f"{{{SVG}}}path"):
        style = dict(item.split(":", 1) for item in el.get("style", "").split(";") if ":" in item)
        attrs = {"d": re.sub(r"\s+", " ", el.get("d", "")).strip(), "fill": "#2e3436"}
        rule = style.get("fill-rule") or el.get("fill-rule")
        if rule == "evenodd":
            attrs["fill-rule"] = "evenodd"
        opacity = float(style.get("opacity", el.get("opacity", "1")) or 1)
        opacity *= float(style.get("fill-opacity", "1") or 1)
        if opacity < 0.999:
            attrs["opacity"] = f"{opacity:.2f}"
        shapes.append(" ".join(f'{k}="{v}"' for k, v in attrs.items()))
    body = "".join(f"<path {s}/>" for s in shapes)
    return (
        HEADER + '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
        f'viewBox="0 0 16 16">{body}</svg>\n'
    )


def main() -> int:
    inkscape = shutil.which("inkscape")
    if inkscape is None:
        print("Inkscape is needed to build icons.", file=sys.stderr)
        return 1
    sources = sorted(SRC.glob("*.svg"))
    with tempfile.TemporaryDirectory() as tmp:
        for src in sources:
            converted = Path(tmp) / src.name
            subprocess.run(
                [
                    inkscape,
                    str(src),
                    "--actions=select-all:all;object-to-path;object-stroke-to-path",
                    "--export-plain-svg",
                    f"--export-filename={converted}",
                ],
                check=True,
                capture_output=True,
            )
            (OUT / src.name).write_text(clean(converted))
            print("built", src.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
