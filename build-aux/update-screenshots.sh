#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
#
# Regenerate every picture the README and the metainfo use, into docs/screenshots/.
#
#   sh build-aux/update-screenshots.sh
#
# The pictures are true renders of the real windows on a private GTK Broadway display
# (see build-aux/screenshots.py); nothing appears on your screen and all content is made up.
# Needs the development dependencies from CONTRIBUTING.md, including tesseract-ocr.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
out="$root/docs/screenshots"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

scenes=docs-capture,docs-window-capture,docs-identify-color,docs-editor,docs-auto-redact
scenes=$scenes,docs-copy-text,docs-history,docs-settings,docs-allow-screenshots,docs-pin
scenes=$scenes,docs-getting-started

run() {
    # $1 = output folder, the rest = extra options for screenshots.py
    dest=$1
    shift
    python3 -B "$root/build-aux/screenshots.py" --out "$dest" --only "$scenes" "$@" \
        | tee "$dest.log"
    if grep -q -E '^(SKIPPED|FAILED)' "$dest.log"; then
        echo "error: some pictures were not made (see above); nothing was changed" >&2
        exit 1
    fi
}

mkdir -p "$tmp/light" "$tmp/dark"
run "$tmp/light"
# One alternative look, to show color schemes and dark style.
run "$tmp/dark" --scheme ocean --dark

mkdir -p "$out"
rm -f "$out"/*.png
cp "$tmp"/light/*.png "$out"/
cp "$tmp/dark/capture.png" "$out/capture-ocean-dark.png"
cp "$tmp/dark/editor.png" "$out/editor-ocean-dark.png"

echo
echo "Updated $(find "$out" -name '*.png' | wc -l) pictures in docs/screenshots/:"
ls "$out"
