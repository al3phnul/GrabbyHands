# SPDX-License-Identifier: GPL-3.0-or-later
"""The real portal client against a stand-in portal (tests/fake_portal.py), over D-Bus.

Run through tests/test_portal.py, which gives it a private session bus. Everything else
that touches portals in the tests uses a Python fake of the *client*; this is the one place
where grabbyhands/portals.py itself talks over a real connection: request objects and their
Response signals, the answers the app must tell apart (taken, cancelled, refused, no portal),
a portal that dies or restarts in the middle of things, and a global-shortcuts session.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import builtins  # noqa: E402

builtins._ = lambda s: s

from gi.repository import Gio, GLib  # noqa: E402

SPECS = [
    ("capture", "Take a screenshot", "Print"),
    ("capture-text", "Copy text from the screen", "LOGO+SHIFT+t"),
]


def pump(seconds: float = 0.05) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def wait_for(predicate, what: str, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        pump()
    raise AssertionError(f"timed out waiting for {what}")


class Portal:
    """Starts, stops and steers the stand-in portal process."""

    def __init__(self, state: Path) -> None:
        self.state = state
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.process: subprocess.Popen | None = None

    def running(self) -> bool:
        reply = self.bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", ("org.freedesktop.portal.Desktop",)),
            None,
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        return reply.unpack()[0]

    def start(self) -> None:
        script = [sys.executable, str(HERE / "fake_portal.py"), str(self.state)]
        self.process = subprocess.Popen(script)
        wait_for(self.running, "the stand-in portal to start")

    def stop(self) -> None:
        self.process.kill()
        self.process.wait()
        wait_for(lambda: not self.running(), "the stand-in portal to stop")

    def control(self, method: str, *args: str):
        signature = "(" + "s" * len(args) + ")"
        reply = self.bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.example.FakePortal",
            method,
            GLib.Variant(signature, args) if args else None,
            None,
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        return reply.unpack() if reply is not None else ()


def main() -> int:
    if "gh-gui-" not in os.environ.get("XDG_RUNTIME_DIR", ""):
        print("portal_smoke.py only runs on the private bus that tests/test_portal.py makes")
        return 2
    from grabbyhands import APP_ID, portals

    tmp = Path(tempfile.mkdtemp(prefix="gh-portal-"))
    portal = Portal(tmp / "bound.json")
    portal.start()

    client = portals.PortalClient()
    assert portal.control("Log")[0][0] == f"Register {APP_ID}", "registration wasn't the first call"
    print("ok     the app registers its id first")

    def screenshot(mode: str) -> tuple[str | None, int]:
        portal.control("Set", "screenshot", mode)
        answers: list = []
        client.screenshot(False, lambda uri, response: answers.append((uri, response)))
        wait_for(lambda: answers, f"a screenshot answer ({mode})")
        pump(0.1)
        assert len(answers) == 1 and not client._pending, (answers, client._pending)
        return answers[0]

    assert screenshot("ok:file:///tmp/example.png") == ("file:///tmp/example.png", 0)
    assert screenshot("cancel") == (None, portals.RESPONSE_CANCELLED)
    assert screenshot("refuse") == (None, portals.RESPONSE_OTHER)
    assert screenshot("error") == (None, portals.RESPONSE_OTHER)  # refused outright: same
    print("ok     screenshot: taken, cancelled, refused")

    # the portal dies while it owes an answer: the caller hears about it, once
    portal.control("Set", "screenshot", "silent")
    answers: list = []
    client.screenshot(False, lambda uri, response: answers.append((uri, response)))
    pump(0.3)
    assert not answers and len(client._pending) == 1
    restarts: list = []
    client.on_restart(lambda: restarts.append(True))
    portal.stop()
    wait_for(lambda: answers, "an answer for the request the portal dropped")
    assert answers == [(None, portals.RESPONSE_UNAVAILABLE)] and not client._pending
    # ...and with no portal at all, a new request says so right away
    answers.clear()
    client.screenshot(False, lambda uri, response: answers.append((uri, response)))
    wait_for(lambda: answers, "an answer without a portal")
    assert answers == [(None, portals.RESPONSE_UNAVAILABLE)] and not restarts
    print("ok     a portal that dies, a portal that isn't there")

    # it comes back: the app is told, has registered again, and everything works
    portal.start()
    wait_for(lambda: restarts, "the restart to be noticed")
    assert portal.control("Log")[0][0] == f"Register {APP_ID}"
    assert screenshot("ok:file:///tmp/again.png") == ("file:///tmp/again.png", 0)
    print("ok     a portal that comes back")

    # global shortcuts: first session binds (the desktop's dialog), keys are reported
    pressed, changes, done = [], [], []
    shortcuts = portals.GlobalShortcuts(client, pressed.append, lambda: changes.append(True))
    assert shortcuts.available
    shortcuts.start(SPECS, True, lambda ok, ids: done.append((ok, ids)))
    wait_for(lambda: done, "the shortcuts to be bound")
    assert done == [(True, ["capture", "capture-text"])], done
    assert shortcuts.triggers == {"capture": "Print", "capture-text": "LOGO+SHIFT+t"}
    assert portal.control("Log")[0].count("BindShortcuts") == 1
    portal.control("Press", "capture-text")
    wait_for(lambda: pressed, "the shortcut press")
    assert pressed == ["capture-text"]
    # the user removes one in the desktop's settings: forgotten here too, listeners told
    portal.control("Remove", "capture-text")
    wait_for(lambda: changes, "the change to arrive")
    assert shortcuts.triggers == {"capture": "Print"}
    print("ok     global shortcuts: bind, press, removed in the desktop's settings")

    # the next session start (here: after a portal restart): nothing is asked again, even
    # though one shortcut is missing; "Set Up Shortcuts" does ask for it
    portal.stop()
    portal.start()
    wait_for(lambda: len(restarts) == 2, "the second restart")
    done.clear()
    shortcuts.start(SPECS, True, lambda ok, ids: done.append((ok, ids)))
    wait_for(lambda: done, "the shortcuts of the new session")
    assert done == [(True, ["capture"])] and "BindShortcuts" not in portal.control("Log")[0]
    pressed.clear()
    portal.control("Press", "capture")
    wait_for(lambda: pressed, "a press in the new session")
    assert pressed == ["capture"], pressed  # once: the old session's subscription is gone
    done.clear()
    shortcuts.start(SPECS, True, lambda ok, ids: done.append((ok, ids)), complete=True)
    wait_for(lambda: done, "the missing shortcut to be bound")
    assert done == [(True, ["capture", "capture-text"])], done
    # the user dismisses the desktop's dialog
    portal.stop()
    (tmp / "bound.json").unlink()
    portal.start()
    wait_for(lambda: len(restarts) == 3, "the third restart")
    portal.control("Set", "bind", "dismiss")
    done.clear()
    shortcuts.start(SPECS, True, lambda ok, ids: done.append((ok, ids)))
    wait_for(lambda: done, "the dismissed dialog")
    assert done == [(True, [])] and shortcuts.triggers == {}
    print("ok     global shortcuts: later sessions, Set Up Shortcuts, a dismissed dialog")

    portal.control("Quit")
    portal.process.wait(5)
    print("0 error(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
