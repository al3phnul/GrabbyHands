# SPDX-License-Identifier: GPL-3.0-or-later
"""First-run and wording helpers that need no display.

IMPORTANT: this file runs in the developer's real session, so it only ever calls the *pure*
helpers of ``gnome_keys``. Anything that reads or writes GNOME's settings (``status``,
``release_print``, ``restore``…) is exercised in tests/gui_smoke.py, which runs on a private
in-memory settings backend.
"""

from pathlib import Path

from grabbyhands import export, gnome_keys


def test_plain_print_screen_is_recognized():
    assert gnome_keys.is_plain_print("Print")
    assert gnome_keys.is_plain_print(" print ")
    for other in ("<Ctrl>Print", "<Control>Print", "<Shift>Print", "<Alt>Print", "F9", ""):
        assert not gnome_keys.is_plain_print(other), other
    assert gnome_keys.holds_print(["<Shift>Print", "Print"])
    assert not gnome_keys.holds_print(["<Ctrl>Print"])
    assert not gnome_keys.holds_print([])


def test_moving_gnomes_tool_frees_print_screen_and_keeps_other_keys():
    # stock GNOME: exactly the value the Getting Started button promises
    assert gnome_keys.moved(["Print"]) == ["<Ctrl>Print"]
    # a shortcut the user added themselves is not thrown away
    assert gnome_keys.moved(["Print", "<Super><Shift>s"]) == ["<Ctrl>Print", "<Super><Shift>s"]
    # already there (in any spelling): no duplicate
    assert gnome_keys.moved(["Print", "<Control>Print"]) == ["<Control>Print"]
    assert gnome_keys.moved(["Print", "<Primary>Print"]) == ["<Primary>Print"]
    for accels in (["Print"], ["Print", "<Super><Shift>s"], []):
        assert not gnome_keys.holds_print(gnome_keys.moved(accels))


def test_commands_for_when_we_cannot_change_the_setting():
    command = gnome_keys.command()
    assert command == (
        "gsettings set org.gnome.shell.keybindings show-screenshot-ui \"['<Ctrl>Print']\""
    )
    assert gnome_keys.undo_command() == (
        "gsettings reset org.gnome.shell.keybindings show-screenshot-ui"
    )


def test_gnome_is_recognized_from_the_desktop_name():
    assert gnome_keys.on_gnome("GNOME")
    assert gnome_keys.on_gnome("ubuntu:GNOME")
    assert gnome_keys.on_gnome("GNOME-Classic:GNOME")
    assert not gnome_keys.on_gnome("KDE")
    assert not gnome_keys.on_gnome("")


def test_paths_are_shown_the_way_people_write_them():
    home = Path.home()
    assert export.display_path(home / "Pictures" / "GrabbyHands" / "a.png") == (
        "~/Pictures/GrabbyHands/a.png"
    )
    assert export.display_path(home) == "~"
    assert export.display_path("/mnt/usb/a.png") == "/mnt/usb/a.png"
    assert export.display_path(str(home) + "-other/a.png") == str(home) + "-other/a.png"


def test_text_recognition_errors_are_explained():
    from grabbyhands import ocr

    missing = ocr.friendly_error("Error opening data file /usr/share/tessdata/deu.traineddata")
    assert "Settings → Text" in missing and "traineddata" not in missing
    assert "Settings → Text" in ocr.friendly_error("Failed loading language 'deu'")
    assert "timed out" in ocr.friendly_error("timed out")


def test_every_file_with_text_for_translators_is_listed():
    import pytest
    from conftest import ROOT

    if not (ROOT / "grabbyhands").is_dir():
        # CI also runs the tests against an installed package, with the source folder removed
        pytest.skip("no source tree here (testing an installed copy)")
    listed = {
        line.strip()
        for line in (ROOT / "po" / "POTFILES.in").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    for path in sorted((ROOT / "grabbyhands").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if '_("' in path.read_text() or "_(\n" in path.read_text():
            assert relative in listed, f"{relative} has translatable text but isn't in POTFILES.in"
    for name in listed:
        assert (ROOT / name).exists(), f"POTFILES.in lists {name}, which doesn't exist"
