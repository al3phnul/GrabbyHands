#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Consistency checks for the repository, and release notes from CHANGELOG.md.

    python3 build-aux/check-repo.py                    # run every check
    python3 build-aux/check-repo.py --tag v0.1.0       # also: is the repo ready for this tag?
    python3 build-aux/check-repo.py --print-version
    python3 build-aux/check-repo.py --notes 0.1.0      # that version's CHANGELOG section
    python3 build-aux/check-repo.py --notes unreleased
    python3 build-aux/check-repo.py --release-notes    # the text of the GitHub Release

Needs only the Python standard library. Used by CI and by the release workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.al3phnul.GrabbyHands"
METAINFO = ROOT / "data" / f"{APP_ID}.metainfo.xml.in"
MANIFEST = ROOT / "build-aux" / "flatpak" / f"{APP_ID}.yml"

# Files that can't carry a comment, or are plain data or documentation. debian/ is covered
# by debian/copyright.
NO_HEADER_NEEDED = ("LICENSE",)
NO_HEADER_PREFIXES = ("debian/",)
NO_HEADER_SUFFIXES = (".md", ".png", ".json")

problems: list[str] = []


def problem(text: str) -> None:
    problems.append(text)
    print(f"FAIL {text}")


def ok(text: str) -> None:
    print(f"ok   {text}")


SKIP_DIRS = {".git", "build", "_build", "builddir", "dist", ".flatpak-builder"}
SKIP_DIRS |= {"__pycache__", ".pytest_cache", ".ruff_cache", ".debhelper"}


def tracked_files() -> list[str]:
    """Files under version control (or every file, when this isn't a git checkout)."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
        return [line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(ROOT).parts)
        )


# -- versions --------------------------------------------------------------------------
def meson_version() -> str:
    text = (ROOT / "meson.build").read_text("utf-8")
    return re.search(r"^\s*version:\s*'([^']+)'", text, re.M).group(1)


def versions() -> dict[str, str]:
    found = {"meson.build": meson_version()}
    init = (ROOT / "grabbyhands" / "__init__.py").read_text("utf-8")
    found["grabbyhands/__init__.py"] = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    release = ET.parse(METAINFO).getroot().find("releases/release")
    found["metainfo (newest release)"] = release.get("version") if release is not None else ""
    changelog = (ROOT / "debian" / "changelog").read_text("utf-8")
    found["debian/changelog"] = re.match(r"\S+ \(([^)]+)\)", changelog).group(1)
    return found


def check_versions() -> str:
    found = versions()
    version = found["meson.build"]
    if len(set(found.values())) == 1:
        ok(f"version {version} is the same in {', '.join(found)}")
    else:
        problem("version mismatch: " + ", ".join(f"{k} = {v}" for k, v in found.items()))
    return version


# -- changelog -------------------------------------------------------------------------
def changelog_sections() -> dict[str, tuple[str, str]]:
    """{'unreleased' or version: (heading line, body)} from CHANGELOG.md."""
    text = (ROOT / "CHANGELOG.md").read_text("utf-8")
    sections: dict[str, tuple[str, str]] = {}
    matches = list(re.finditer(r"^## \[([^\]]+)\](.*)$", text, re.M))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        body = re.split(r"^\[[^\]]+\]:\s", body, maxsplit=1, flags=re.M)[0]  # link footnotes
        sections[match.group(1).lower()] = (match.group(0), body.strip())
    return sections


def check_changelog(version: str, tag: str | None) -> None:
    sections = changelog_sections()
    if "unreleased" not in sections and version not in sections:
        problem("CHANGELOG.md has neither an [Unreleased] section nor one for " + version)
        return
    if tag is None:
        ok("CHANGELOG.md has " + ", ".join(f"[{name}]" for name in sections))
        return
    if version not in sections:
        problem(f"CHANGELOG.md has no '## [{version}] - <date>' section (still Unreleased?)")
        return
    heading, body = sections[version]
    date = re.search(r"\d{4}-\d{2}-\d{2}", heading)
    if not date:
        problem(f"CHANGELOG.md: the [{version}] heading has no date (YYYY-MM-DD)")
    if not body:
        problem(f"CHANGELOG.md: the [{version}] section is empty")
    release = ET.parse(METAINFO).getroot().find("releases/release")
    if date and release is not None and release.get("date") != date.group(0):
        problem(
            f"release date differs: CHANGELOG.md says {date.group(0)}, "
            f"the metainfo says {release.get('date')}"
        )
    if date and body:
        ok(f"CHANGELOG.md has a dated [{version}] section")


def check_tag(version: str, tag: str) -> None:
    if tag == f"v{version}":
        ok(f"tag {tag} matches version {version}")
    else:
        problem(f"tag {tag} does not match version {version} (expected v{version})")


# -- translations ----------------------------------------------------------------------
def check_potfiles(files: list[str]) -> None:
    listed = {
        line.strip()
        for line in (ROOT / "po" / "POTFILES.in").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    uses_gettext = re.compile(r"(^|[^A-Za-z0-9_.])_\(")
    wanted = {
        name
        for name in files
        if name.startswith("grabbyhands/")
        and name.endswith(".py")
        and uses_gettext.search((ROOT / name).read_text("utf-8"))
    }
    missing = sorted(wanted - listed)
    stale = sorted(name for name in listed if not (ROOT / name).exists())
    if missing:
        problem("po/POTFILES.in is missing: " + ", ".join(missing))
    if stale:
        problem("po/POTFILES.in lists files that don't exist: " + ", ".join(stale))
    if not missing and not stale:
        ok(f"po/POTFILES.in lists all {len(listed)} files with translatable strings")


# -- license headers -------------------------------------------------------------------
def check_spdx(files: list[str]) -> None:
    missing = []
    for name in files:
        if name in NO_HEADER_NEEDED or name.endswith(NO_HEADER_SUFFIXES):
            continue
        if name.startswith(NO_HEADER_PREFIXES):
            continue
        head = (ROOT / name).read_text("utf-8", errors="replace")[:600]
        if "SPDX-License-Identifier:" not in head:
            missing.append(name)
    if missing:
        problem("no SPDX-License-Identifier header: " + ", ".join(missing))
    else:
        ok("every source and config file has an SPDX license header")


# -- flatpak manifest ------------------------------------------------------------------
def check_manifest() -> None:
    text = MANIFEST.read_text("utf-8")
    urls = re.findall(r"^\s*url:\s*(\S+)\s*$", text, re.M)
    sums = re.findall(r"^\s*sha256:\s*([0-9a-f]{64})\s*$", text, re.M)
    bad = [url for url in urls if not url.startswith("https://")]
    if bad:
        problem("Flatpak sources not fetched over https: " + ", ".join(bad))
    if len(urls) != len(sums):
        problem(f"Flatpak manifest: {len(urls)} source URLs but {len(sums)} sha256 checksums")
    if re.search(r"^\s*-\s*--share=network", text, re.M):
        problem("Flatpak manifest asks for network access")
    if not bad and len(urls) == len(sums):
        ok(f"Flatpak manifest: {len(urls)} sources, all https and pinned by sha256")


# -- shell extension -------------------------------------------------------------------
def check_extension() -> None:
    meta = json.loads((ROOT / "data" / "shell-extension" / "metadata.json").read_text("utf-8"))
    data_meson = (ROOT / "data" / "meson.build").read_text("utf-8")
    if meta["uuid"] not in data_meson:
        problem(f"data/meson.build does not install the extension as {meta['uuid']}")
    else:
        ok(f"Shell extension {meta['uuid']} for GNOME {', '.join(meta['shell-version'])}")
    # Its settings schema must travel with it, in the build and in the zip.
    schema = f"schemas/{meta.get('settings-schema', '')}.gschema.xml"
    pack = (ROOT / "build-aux" / "pack-extension.sh").read_text("utf-8")
    if not (ROOT / "data" / "shell-extension" / schema).is_file():
        problem(f"the extension's settings schema data/shell-extension/{schema} is missing")
    elif schema not in data_meson or "schemas" not in pack:
        problem(f"{schema} is not installed by data/meson.build or not packed into the zip")
    else:
        ok(f"Shell extension settings schema {meta['settings-schema']}")


def notes(which: str) -> int:
    sections = changelog_sections()
    key = which.lower().lstrip("v")
    if key not in sections:
        print(f"CHANGELOG.md has no [{which}] section", file=sys.stderr)
        return 1
    print(sections[key][1])
    return 0


RELEASE_FOOTER = """
## Install

- **Flatpak** (any distro): download `GrabbyHands-{version}-x86_64.flatpak`, then
  `flatpak install --user GrabbyHands-{version}-x86_64.flatpak`
- **Debian / Ubuntu**: download `grabbyhands_{version}_all.deb`, then
  `sudo apt install ./grabbyhands_{version}_all.deb`
- **Window capture with the Flatpak**: also download
  `grabbyhands@al3phnul.github.io.shell-extension.zip` and run
  `gnome-extensions install grabbyhands@al3phnul.github.io.shell-extension.zip`

The [README](https://github.com/al3phnul/GrabbyHands#install) has the full steps, including
what to do on first run. To check a download, run `sha256sum -c SHA256SUMS --ignore-missing`
in the folder you saved it to.
"""


def release_notes() -> int:
    """The body of the GitHub Release: this version's CHANGELOG section plus install help."""
    version = meson_version()
    sections = changelog_sections()
    if version in sections:
        body = sections[version][1]
    elif "unreleased" in sections:
        body = "_Not released yet: these are the Unreleased notes._\n\n" + sections["unreleased"][1]
    else:
        print("CHANGELOG.md has no section to take release notes from", file=sys.stderr)
        return 1
    print(body)
    print(RELEASE_FOOTER.format(version=version))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tag", help="release tag to check the repository against, e.g. v0.1.0")
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument("--notes", metavar="VERSION", help="print a CHANGELOG.md section")
    parser.add_argument(
        "--release-notes", action="store_true", help="print the text for the GitHub Release"
    )
    args = parser.parse_args()
    if args.print_version:
        print(meson_version())
        return 0
    if args.notes:
        return notes(args.notes)
    if args.release_notes:
        return release_notes()

    files = tracked_files()
    version = check_versions()
    check_changelog(version, args.tag)
    if args.tag:
        check_tag(version, args.tag)
    check_potfiles(files)
    check_spdx(files)
    check_manifest()
    check_extension()
    print(f"\n{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
