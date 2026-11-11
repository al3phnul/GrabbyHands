#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
#
# Pack the "GrabbyHands Window Helper" GNOME Shell extension into
# <uuid>.shell-extension.zip (the name and layout `gnome-extensions pack` produces), for
# people who installed the Flatpak. They install it with:
#   gnome-extensions install grabbyhands@al3phnul.github.io.shell-extension.zip
#
#   sh build-aux/pack-extension.sh [output folder, default: dist]
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$root/dist}
src="$root/data/shell-extension"
uuid=$(sed -n 's/^ *"uuid": *"\([^"]*\)".*/\1/p' "$src/metadata.json")
[ -n "$uuid" ] || { echo "error: no uuid in $src/metadata.json" >&2; exit 1; }

mkdir -p "$out"
out=$(cd "$out" && pwd)
zipfile="$out/$uuid.shell-extension.zip"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

cp "$src/metadata.json" "$src/extension.js" "$root/LICENSE" "$stage"/
# The settings schema goes in as source: `gnome-extensions install` compiles it.
mkdir "$stage/schemas"
cp "$src"/schemas/*.gschema.xml "$stage/schemas"/
schema=$(cd "$stage" && echo schemas/*.gschema.xml)
find "$stage" -type f -exec chmod 644 {} +
# Same bytes on every build: fixed file times, fixed order, no extra attributes.
find "$stage" -exec touch -d '2026-01-01 00:00:00 UTC' {} +
rm -f "$zipfile"
(cd "$stage" && TZ=UTC zip -X -q "$zipfile" extension.js LICENSE metadata.json "$schema")
echo "$zipfile"
