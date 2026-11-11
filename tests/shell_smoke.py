# SPDX-License-Identifier: GPL-3.0-or-later
"""The Window Helper extension inside a real GNOME Shell (started by tests/test_shell.py).

The Shell is headless and private (see tests/shell_session.py). This script plays
GrabbyHands: it asks the helper what GrabbyHands would ask, and it sits on the bus under
GrabbyHands' name to see what the system menu's screenshot button does when clicked.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from gi.repository import Gio, GLib

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import builtins  # noqa: E402

builtins.__dict__.setdefault("_", lambda text: text)

import shell_session  # noqa: E402

from grabbyhands import windows  # noqa: E402

HELPER = (
    "io.github.al3phnul.GrabbyHands.Shell",
    "/io/github/al3phnul/GrabbyHands/Shell",
    "io.github.al3phnul.GrabbyHands.Shell",
)
APP_ID = "io.github.al3phnul.GrabbyHands"
APP_PATH = "/io/github/al3phnul/GrabbyHands"
APP_XML = """<node><interface name="org.freedesktop.Application">
  <method name="ActivateAction">
    <arg type="s" direction="in"/><arg type="av" direction="in"/><arg type="a{sv}" direction="in"/>
  </method>
</interface></node>"""
SECRET = "Payroll 2026 - do not share"

# What the Shell's system menu holds, left to right: [class, visible, icon, spoken name].
MENU = (
    "Main.panel.statusArea.quickSettings._system._systemItem.child.get_children()"
    ".map(c => [c.constructor.name, c.visible, c.icon_name ?? '', c.accessible_name ?? ''])"
)
BUTTONS = (
    "Main.panel.statusArea.quickSettings._system._systemItem.child.get_children()"
    ".filter(c => c.icon_name === 'screenshooter-symbolic')"
)
QUICK_MENU = "Main.panel.statusArea.quickSettings.menu"
GNOME_TOOL = "Main.screenshotUI.visible"

shell = shell_session.Shell()
context = GLib.MainContext.default()


def pump(seconds: float = 0.0) -> None:
    """Let this process answer D-Bus calls for a while."""
    deadline = time.monotonic() + seconds
    while context.iteration(False) or time.monotonic() < deadline:
        if time.monotonic() >= deadline and not context.pending():
            break
        time.sleep(0.01)


def until(check, what: str, timeout: float = 15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        pump(0.1)
        try:
            last = check()
        except (GLib.Error, RuntimeError) as err:
            last = err
        else:
            if last:
                return last
    raise AssertionError(f"{what} (last answer: {last!r})")


def helper(method: str, *args):
    variant = None
    if args:
        variant = GLib.Variant("(" + "".join("b" for _ in args) + ")", args)
    return shell.call(*HELPER, method, variant)


def helper_version() -> int:
    (version,) = shell.call(
        HELPER[0],
        HELPER[1],
        "org.freedesktop.DBus.Properties",
        "Get",
        GLib.Variant("(ss)", (HELPER[2], "Version")),
    )
    return version


def screenshot_buttons() -> list[list]:
    """The screenshot buttons in the system menu: GNOME's own, and the stand-in if any."""
    return [item for item in shell.eval(MENU) if item[2] == "screenshooter-symbolic"]


def click_visible_button() -> None:
    shell.eval(f"{QUICK_MENU}.open(); 1")
    until(lambda: shell.eval(f"{QUICK_MENU}.isOpen"), "the system menu did not open")
    shell.eval(f"{BUTTONS}.filter(c => c.visible)[0].emit('clicked', 0); 1")


class FakeGrabbyHands:
    """Owns GrabbyHands' bus name and notes the actions it is asked for."""

    def __init__(self) -> None:
        self.actions: list[str] = []
        self.bus = shell.bus
        info = Gio.DBusNodeInfo.new_for_xml(APP_XML).interfaces[0]
        self.registration = self.bus.register_object(APP_PATH, info, self._call, None, None)
        self.bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "RequestName",
            GLib.Variant("(su)", (APP_ID, 0)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )

    def _call(self, _bus, _sender, _path, _interface, method, parameters, invocation) -> None:
        if method == "ActivateAction":
            self.actions.append(parameters.unpack()[0])
        invocation.return_value(None)

    def leave(self) -> None:
        self.bus.unregister_object(self.registration)
        self.bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ReleaseName",
            GLib.Variant("(s)", (APP_ID,)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )


# -- steps -----------------------------------------------------------------------------
def helper_answers():
    until(windows.available, "the Window Helper never answered")
    assert helper_version() == 3
    assert helper("GetStageSize") == (1280, 720)
    assert helper("GetWindows") == ([],)
    # and GrabbyHands' own code understands it
    assert windows.get_stage_size() == (1280, 720)
    assert windows.get_windows() == []


def windows_without_their_headings():
    env = {**os.environ, "WAYLAND_DISPLAY": "gh-test", "GDK_BACKEND": "wayland"}
    env["GSK_RENDERER"] = "cairo"
    client = subprocess.Popen(
        [sys.executable, str(HERE / "shell" / "window.py"), SECRET],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    readings: list = []

    def settled():
        # A new window may still be on its way to its place and size: wait until the
        # helper says the same thing twice.
        readings.append(helper("GetWindows")[0])
        return readings[-1] if readings[-1] and readings[-2:-1] == readings[-1:] else None

    try:
        (found,) = until(settled, "the test window never showed up and stayed put")
        x, y, width, height, app = found
        assert width >= 420 and height >= 260, found
        assert x >= 0 and y >= 0 and x + width <= 1280 and y + height <= 720, found
        # The privacy promise, checked on a program without a launcher (for those GNOME
        # makes a name up): the helper says where the window is, never what its heading is.
        assert shell.eval("global.get_window_actors().map(a => a.meta_window.title)") == [SECRET]
        assert "Payroll" not in app and SECRET not in repr(helper("GetWindows")), found
        seen = windows.get_windows()
        assert seen == [windows.WindowRect(x, y, width, height, app)], (seen, readings)
    finally:
        client.terminate()
        client.wait(10)
    until(lambda: helper("GetWindows") == ([],), "the closed window is still reported")


def off_by_default():
    assert helper("GetMenuButton") == (False, True)
    assert windows.menu_button() == "off"
    assert screenshot_buttons() == [
        ["ScreenshotItem", True, "screenshooter-symbolic", "Take Screenshot"]
    ]


def turning_it_on_swaps_the_button():
    assert windows.set_menu_button(True) is True  # as the switch in Settings does
    assert windows.menu_button() == "on"
    assert helper("GetMenuButton") == (True, True)
    menu = shell.eval(MENU)
    names = [item[0] for item in menu]
    at = names.index("ScreenshotItem")
    # GNOME's button is still there, hidden, and the stand-in sits in its place: same
    # icon, same spoken name, right before Settings.
    assert menu[at] == ["ScreenshotItem", False, "screenshooter-symbolic", "Take Screenshot"]
    assert menu[at + 1][1:] == [True, "screenshooter-symbolic", "Take Screenshot"], menu
    assert names[at + 2] == "SettingsItem", names
    assert len(screenshot_buttons()) == 2
    assert helper("SetMenuButton", True) == (True,)  # asking twice adds no second one
    assert len(screenshot_buttons()) == 2


def a_click_starts_grabbyhands():
    app = FakeGrabbyHands()
    try:
        click_visible_button()
        until(lambda: app.actions, "GrabbyHands was not asked for a capture")
        pump(0.5)
        assert app.actions == ["capture"], app.actions
        assert shell.eval(f"{QUICK_MENU}.isOpen") is False  # the menu is out of the picture
        assert shell.eval(GNOME_TOOL) is False  # and GNOME's own tool stayed away
    finally:
        app.leave()


def without_grabbyhands_the_button_still_works():
    # GrabbyHands isn't installed here and nobody owns its name: the click must not be a
    # dead end. It opens GNOME's own tool, as the button always did.
    click_visible_button()
    until(lambda: shell.eval(GNOME_TOOL), "GNOME's screenshot tool did not open instead")
    shell.eval("Main.screenshotUI.close(true); 1")
    until(lambda: not shell.eval(GNOME_TOOL), "GNOME's screenshot tool did not close")


def turning_it_off_restores_gnomes_button():
    assert windows.set_menu_button(False) is True
    assert helper("GetMenuButton") == (False, True)
    off_by_default()
    # and GNOME's button does what it always did
    click_visible_button()
    until(lambda: shell.eval(GNOME_TOOL), "GNOME's own button stopped working")
    shell.eval("Main.screenshotUI.close(true); 1")
    until(lambda: not shell.eval(GNOME_TOOL), "GNOME's screenshot tool did not close")


def switching_the_extension_off_restores_it_too():
    assert helper("SetMenuButton", True) == (True,)
    shell.disable(shell_session.HELPER_UUID)  # what locking the screen does as well
    until(lambda: len(screenshot_buttons()) == 1, "the stand-in outlived the extension")
    assert screenshot_buttons()[0][:2] == ["ScreenshotItem", True]
    until(lambda: windows.menu_button() == "no-helper", "the helper is still on the bus")
    assert windows.set_menu_button(True) is False  # nobody to ask: Settings says so
    shell.enable(shell_session.HELPER_UUID)
    # The choice was kept: back on, the button is swapped again without being asked.
    until(lambda: len(screenshot_buttons()) == 2, "the choice was forgotten")
    assert helper("GetMenuButton") == (True, True)


def the_choice_is_there_at_login():
    shell.stop()
    shell.start()  # the same settings, a new Shell: what logging in again looks like
    until(lambda: helper("GetMenuButton") == (True, True), "the choice did not survive")
    until(lambda: len(screenshot_buttons()) == 2, "the button was not swapped at login")
    assert [item[1] for item in screenshot_buttons()] == [False, True]
    assert helper("SetMenuButton", False) == (True,)
    off_by_default()


STEPS = [
    ("the helper answers", helper_answers),
    ("windows, without their headings", windows_without_their_headings),
    ("the menu button is off by default", off_by_default),
    ("turning it on swaps the button", turning_it_on_swaps_the_button),
    ("a click starts GrabbyHands", a_click_starts_grabbyhands),
    ("without GrabbyHands the button still works", without_grabbyhands_the_button_still_works),
    ("turning it off restores GNOME's button", turning_it_off_restores_gnomes_button),
    ("switching the extension off restores it", switching_the_extension_off_restores_it_too),
    ("the choice is there at login", the_choice_is_there_at_login),
]


def main() -> int:
    try:
        version = shell.start()
    except shell_session.ShellUnavailable as err:
        print(f"SKIP: {err}")
        return 77
    print(f"GNOME Shell {version}")
    errors = 0
    try:
        shell.enable(shell_session.HOOK_UUID)
        shell.enable(shell_session.HELPER_UUID)
        for name, step in STEPS:
            try:
                step()
            except Exception:
                errors += 1
                print(f"FAIL  {name}")
                traceback.print_exc(file=sys.stdout)
            else:
                print(f"ok    {name}")
    finally:
        shell.stop()
    if errors:
        print("--- the Shell's log ---")
        print(shell.log_tail(60))
    print(f"{errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
