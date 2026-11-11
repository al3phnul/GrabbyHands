# Contributing to GrabbyHands

Thanks for helping! GrabbyHands is a small, friendly project. Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and ideas

- Search [existing issues](https://github.com/al3phnul/GrabbyHands/issues) first.
- Use the issue templates. For bugs, include your distro, GNOME version, how you installed
  GrabbyHands (Flatpak, .deb, source), and what happened versus what you expected.
- **Please don't attach screenshots that contain personal information.** Use GrabbyHands' own
  Redact tool first.
- Security problems: see [SECURITY.md](SECURITY.md), not public issues.

## Development setup

On Ubuntu or Debian:

```sh
sudo apt install git meson gettext desktop-file-utils appstream gtk-update-icon-cache \
    libglib2.0-bin python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
    python3-pil python3-pytest tesseract-ocr python3-zxing-cpp fonts-dejavu-core \
    libgtk-4-bin dbus-daemon
pipx install ruff==0.17.4      # the version CI uses; or: uv tool install ruff==0.17.4
```

`libgtk-4-bin` and `dbus-daemon` are for the GUI test (it runs on GTK's Broadway backend).

Run it straight from the checkout:

```sh
python3 -m grabbyhands --capture      # GRABBYHANDS_DEBUG=1 for logs
```

GrabbyHands is a single background app. If an installed copy is already running, the command
above just talks to that one, so quit it first (`grabbyhands --quit`).

## Before you open a pull request

```sh
ruff check . && ruff format --check .
python3 -m pytest
meson setup build && meson test -C build   # validates the desktop file, metainfo and schema
python3 build-aux/check-repo.py            # versions, POTFILES, licence headers, Flatpak pins
```

What the tests are:

- `tests/test_*.py`: unit tests for the logic (colours, export, history, OCR, QR, redaction,
  the editor's document and renderer).
- `tests/test_gui.py` runs `tests/gui_smoke.py`: it starts the real app on a private Broadway
  display with a private D-Bus session, in-memory settings and a fake screenshot portal, and
  drives every window by calling what the mouse and keys call. Nothing appears on your screen
  and your own settings and captures are not touched. Add a step there for UI behaviour.
- `tests/test_shell.py` runs `tests/shell_smoke.py`: it loads the Window Helper extension into
  a real GNOME Shell that is headless and private (its own D-Bus session, settings, home and
  folders; nothing appears on your screen), then asks it what GrabbyHands asks and clicks the
  system menu's screenshot button. It is skipped where `gnome-shell` isn't installed. To try
  it the way a distro sets GNOME up, with that distro's own extensions running too:
  `GRABBYHANDS_TEST_SHELL_ARGS=--mode=ubuntu python3 -m pytest tests/test_shell.py`.

CI runs all of this on Ubuntu 26.04 and on Ubuntu 24.04, the oldest platform GrabbyHands
supports (GTK 4.14, libadwaita 1.5, Python 3.12). Before using a GTK, libadwaita or CSS
feature, check that it exists in those versions.

## Looking at the interface, and the screenshots in the docs

`build-aux/screenshots.py` renders the real windows to PNG files (again on a private display):

```sh
python3 build-aux/screenshots.py --out /tmp/shots                  # every scene
python3 build-aux/screenshots.py --out /tmp/shots --only editor --scheme ocean --dark
```

The scenes live in `build-aux/screenshot_scenes.py`; all content in them is made up.

The pictures in the README and in the app-store metadata are the `docs-*` scenes. If your
change alters how GrabbyHands looks, regenerate them with one command and commit the result:

```sh
sh build-aux/update-screenshots.sh         # rewrites docs/screenshots/
```

## Pull requests

- Work on a branch; keep each pull request focused on one change.
- `main` must always work. CI has to pass: lint, tests, validators, the package builds and
  the install tests.
- Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- New source files start with the SPDX header the others use:
  `# SPDX-License-Identifier: GPL-3.0-or-later`.
- User-visible strings go through `_()` so they can be translated, and the file is listed in
  `po/POTFILES.in`.
- No network access, telemetry or uploads. Nothing should ever leave the user's machine.
- No new dependencies without discussing it in an issue first: each one has to be available
  in Debian, Ubuntu and the Flatpak.

## Licensing of contributions

GrabbyHands is licensed under GPL-3.0-or-later. By submitting a contribution you agree that it
is licensed under the same terms ("inbound = outbound"). There is no CLA. Please only submit
work you have the right to share.

## Translations

Strings are extracted with gettext. Add your language code to `po/LINGUAS`, then:

```sh
meson setup build
meson compile -C build grabbyhands-pot          # writes po/grabbyhands.pot
meson compile -C build grabbyhands-update-po    # creates or updates po/<language>.po
```

Translate `po/<language>.po` and open a pull request with it.

## Building the packages

CI builds and tests every package on each pull request (`.github/workflows/build.yml`); the
files are attached to the run as artifacts. To build them yourself:

```sh
# Flatpak (needs flatpak-builder and the GNOME 50 SDK from Flathub)
flatpak-builder --force-clean --user --install-deps-from=flathub \
    build/flatpak build-aux/flatpak/io.github.al3phnul.GrabbyHands.yml
flatpak-builder --run build/flatpak build-aux/flatpak/io.github.al3phnul.GrabbyHands.yml \
    python3 - < build-aux/selftest.py

# .deb (on Debian or Ubuntu; the package lands in the parent folder)
sudo apt build-dep ./
dpkg-buildpackage --build=binary --no-sign

# Window Helper extension zip
sh build-aux/pack-extension.sh
```

`build-aux/selftest.py` checks an installed copy (Flatpak, .deb or a Meson prefix) without
opening a window: Python modules, GTK and libadwaita versions, the settings schema, image
formats, text recognition and QR reading.

## Releasing (maintainers)

1. In `CHANGELOG.md`, rename **Unreleased** to `## [X.Y.Z] - YYYY-MM-DD` and start a new, empty
   **Unreleased** section above it.
2. Set the same version in `meson.build`, `grabbyhands/__init__.py` and `debian/changelog`, and
   add a `<release>` with the same version and date to
   `data/io.github.al3phnul.GrabbyHands.metainfo.xml.in`.
3. Run `python3 build-aux/check-repo.py --tag vX.Y.Z`. It has to print `0 problem(s)`.
4. Commit and push to `main`, and wait for CI to pass.
5. Optional rehearsal: run the **Release** workflow by hand (Actions → Release → Run workflow).
   It builds and tests everything and leaves the files in the run's `release` artifact, but
   publishes nothing.
6. Tag the commit `vX.Y.Z` and push the tag. The Release workflow checks the tag, builds the
   Flatpak bundle, the `.deb` and the extension zip, tests them, writes `SHA256SUMS`, attests
   the build and publishes the GitHub Release with that version's changelog section as notes.
