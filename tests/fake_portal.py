# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for xdg-desktop-portal, for tests/portal_smoke.py.

It speaks just enough of the Screenshot and GlobalShortcuts portals (and the host app
registry) for GrabbyHands' portal client to be tested over a real D-Bus connection: request
objects, Response signals, sessions, Activated and ShortcutsChanged. It only ever runs on
the private bus of a test run (it refuses to start anywhere else), and what it "binds" is
kept in a file so that a restarted portal remembers it, as GNOME does.

Usage: fake_portal.py <state file>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
XML = """
<node>
  <interface name="org.freedesktop.host.portal.Registry">
    <method name="Register">
      <arg type="s" direction="in"/><arg type="a{sv}" direction="in"/>
    </method>
  </interface>
  <interface name="org.freedesktop.portal.Screenshot">
    <method name="Screenshot">
      <arg type="s" direction="in"/><arg type="a{sv}" direction="in"/>
      <arg type="o" direction="out"/>
    </method>
    <property name="version" type="u" access="read"/>
  </interface>
  <interface name="org.freedesktop.portal.GlobalShortcuts">
    <method name="CreateSession">
      <arg type="a{sv}" direction="in"/><arg type="o" direction="out"/>
    </method>
    <method name="BindShortcuts">
      <arg type="o" direction="in"/><arg type="a(sa{sv})" direction="in"/>
      <arg type="s" direction="in"/><arg type="a{sv}" direction="in"/>
      <arg type="o" direction="out"/>
    </method>
    <method name="ListShortcuts">
      <arg type="o" direction="in"/><arg type="a{sv}" direction="in"/>
      <arg type="o" direction="out"/>
    </method>
    <property name="version" type="u" access="read"/>
  </interface>
  <interface name="org.example.FakePortal">
    <method name="Set"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
    <method name="Press"><arg type="s" direction="in"/></method>
    <method name="Remove"><arg type="s" direction="in"/></method>
    <method name="Log"><arg type="as" direction="out"/></method>
    <method name="Quit"/>
  </interface>
</node>
"""


class FakePortal:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self.bound: dict[str, str] = (
            json.loads(state_file.read_text()) if state_file.exists() else {}
        )
        self.screenshot = "refuse"  # ok:<uri> | cancel | refuse | silent | error
        self.bind = "accept"  # accept | dismiss
        self.log: list[str] = []
        self.sessions: dict[str, str] = {}  # session path -> owner
        self.loop = GLib.MainLoop()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        for interface in node.interfaces:
            self.bus.register_object(PATH, interface, self.call, self.get_property, None)
        Gio.bus_own_name_on_connection(
            self.bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, lambda *_: self.loop.quit()
        )

    # -- helpers ------------------------------------------------------------------
    def request_path(self, sender: str, options: dict) -> str:
        return f"{PATH}/request/{sender[1:].replace('.', '_')}/{options['handle_token']}"

    def respond(self, sender: str, path: str, code: int, results: dict) -> None:
        def emit() -> bool:
            self.bus.emit_signal(
                sender,
                path,
                "org.freedesktop.portal.Request",
                "Response",
                GLib.Variant("(ua{sv})", (code, results)),
            )
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(20, emit)

    def shortcuts(self) -> GLib.Variant:
        items = [
            (sid, {"trigger_description": GLib.Variant("s", keys)})
            for sid, keys in self.bound.items()
        ]
        return GLib.Variant("a(sa{sv})", items)

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.bound))

    # -- D-Bus --------------------------------------------------------------------
    def get_property(self, _bus, _sender, _path, _interface, name):
        return GLib.Variant("u", 1) if name == "version" else None

    def call(self, _bus, sender, _path, interface, method, params, invocation) -> None:
        args = params.unpack()
        self.log.append(method)
        if interface == "org.example.FakePortal":
            self.control(method, args, invocation)
        elif method == "Register":
            self.log[-1] = f"Register {args[0]}"
            invocation.return_value(None)
        elif method == "Screenshot":
            self.do_screenshot(sender, args[1], invocation)
        elif method == "CreateSession":
            options = args[0]
            session = (
                f"{PATH}/session/{sender[1:].replace('.', '_')}/{options['session_handle_token']}"
            )
            self.sessions[session] = sender
            path = self.request_path(sender, options)
            invocation.return_value(GLib.Variant("(o)", (path,)))
            self.respond(sender, path, 0, {"session_handle": GLib.Variant("s", session)})
        elif method == "ListShortcuts":
            path = self.request_path(sender, args[1])
            invocation.return_value(GLib.Variant("(o)", (path,)))
            self.respond(sender, path, 0, {"shortcuts": self.shortcuts()})
        elif method == "BindShortcuts":
            _session, wanted, _parent, options = args
            path = self.request_path(sender, options)
            invocation.return_value(GLib.Variant("(o)", (path,)))
            if self.bind == "dismiss":
                self.respond(sender, path, 1, {})
                return
            for shortcut_id, properties in wanted:
                self.bound.setdefault(shortcut_id, properties.get("preferred_trigger", ""))
            self.save()
            self.respond(sender, path, 0, {"shortcuts": self.shortcuts()})

    def do_screenshot(self, sender: str, options: dict, invocation) -> None:
        if self.screenshot == "error":
            invocation.return_dbus_error(
                "org.freedesktop.portal.Error.NotAllowed", "screenshots are not allowed"
            )
            return
        path = self.request_path(sender, options)
        invocation.return_value(GLib.Variant("(o)", (path,)))
        if self.screenshot.startswith("ok:"):
            uri = GLib.Variant("s", self.screenshot[3:])
            self.respond(sender, path, 0, {"uri": uri})
        elif self.screenshot == "cancel":
            self.respond(sender, path, 1, {})
        elif self.screenshot == "refuse":
            self.respond(sender, path, 2, {})
        # "silent": no answer at all

    def control(self, method: str, args: tuple, invocation) -> None:
        if method == "Set":
            setattr(self, args[0], args[1])
            invocation.return_value(None)
        elif method == "Press":  # the user presses a global shortcut
            for session, owner in self.sessions.items():
                self.bus.emit_signal(
                    owner,
                    PATH,
                    "org.freedesktop.portal.GlobalShortcuts",
                    "Activated",
                    GLib.Variant("(osta{sv})", (session, args[0], 0, {})),
                )
            invocation.return_value(None)
        elif method == "Remove":  # the user removes a shortcut in the desktop's settings
            self.bound.pop(args[0], None)
            self.save()
            for session, owner in self.sessions.items():
                self.bus.emit_signal(
                    owner,
                    PATH,
                    "org.freedesktop.portal.GlobalShortcuts",
                    "ShortcutsChanged",
                    GLib.Variant.new_tuple(GLib.Variant("o", session), self.shortcuts()),
                )
            invocation.return_value(None)
        elif method == "Log":
            invocation.return_value(GLib.Variant("(as)", (self.log,)))
        elif method == "Quit":
            invocation.return_value(None)
            GLib.idle_add(self.loop.quit)


def main() -> int:
    if "gh-gui-" not in os.environ.get("XDG_RUNTIME_DIR", ""):
        print("fake_portal.py only runs on the private bus of a test run", file=sys.stderr)
        return 2
    FakePortal(Path(sys.argv[1])).loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
