#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write tests/fixtures/too-new-api.json: toolkit names GrabbyHands must not use.

GrabbyHands promises to run on GTK 4.14 and libadwaita 1.5 (Ubuntu 24.04), but is developed
on newer versions, where using something too new goes unnoticed. The .gir files that come
with the development packages (or the GNOME SDK) say since when each class, method,
property and signal exists. This collects every name that only exists in newer versions;
tests/test_functionality.py then checks that the source uses none of them.

Listed are: types, functions and constants newer than the minimum, and members newer than
the minimum whose name isn't also the name of an older member somewhere (so that seeing
the name can only mean the new API).

Usage: build-aux/too-new-api.py [directory with the .gir files]
Run it again when a minimum version is raised (edit MINIMUM first).
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MINIMUM = {
    "Gtk-4.0": (4, 14),
    "Gdk-4.0": (4, 14),
    "Gsk-4.0": (4, 14),
    "Adw-1": (1, 5),
    "Gio-2.0": (2, 80),
    "GLib-2.0": (2, 80),
    "GObject-2.0": (2, 80),
    "Pango-1.0": (1, 52),
    "PangoCairo-1.0": (1, 52),
}
CORE = "{http://www.gtk.org/introspection/core/1.0}"
MEMBERS = ("method", "function", "property", "signal", "member", "virtual-method", "field")
OUTPUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "too-new-api.json"


def version(text: str | None) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in (text or "").split(".")[:2])
    except ValueError:
        return None


def main() -> int:
    gir_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/gir-1.0")
    old: set[str] = set()
    new_types: dict[str, str] = {}
    new_members: dict[str, str] = {}
    for gir, minimum in MINIMUM.items():
        path = gir_dir / f"{gir}.gir"
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        namespace = ET.parse(path).getroot().find(f"{CORE}namespace")
        prefix = gir.split("-")[0]
        for element in namespace:
            name = element.get("name")
            if not name:
                continue
            since = version(element.get("version"))
            type_is_new = bool(since and since > minimum)
            if type_is_new:
                new_types[name] = f"{prefix}.{name}, since {element.get('version')}"
            else:
                old.add(name)
            for child in element:
                kind = child.tag.split("}")[-1]
                child_name = child.get("name")
                if not child_name or kind not in MEMBERS:
                    continue
                python = child_name.replace("-", "_")
                names = {child_name.upper()} if kind == "member" else {python}
                if kind == "property":
                    names |= {f"get_{python}", f"set_{python}"}
                child_since = version(child.get("version"))
                if type_is_new or (child_since and child_since > minimum):
                    where = child.get("version") or element.get("version")
                    for candidate in names:
                        new_members.setdefault(
                            candidate, f"{prefix}.{name}.{child_name}, since {where}"
                        )
                else:
                    old |= names
    found = {name: where for name, where in new_types.items() if name not in old}
    found.update({n: w for n, w in new_members.items() if n not in old and n not in found})
    OUTPUT.write_text(json.dumps(dict(sorted(found.items())), indent=0, ensure_ascii=False) + "\n")
    print(f"{len(found)} names written to {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
