# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Thin async wrappers around the XDG desktop portals GrabbyHands uses.

Portal calls return a Request object path; the answer arrives later as a
``org.freedesktop.portal.Request.Response`` signal on that path. We subscribe
before calling (with our own ``handle_token``) so we never miss the reply.
"""

from __future__ import annotations

import itertools
import logging
import os
from collections.abc import Callable
from typing import Any

from gi.repository import Gio, GLib

from grabbyhands import APP_ID
from grabbyhands.config import in_flatpak

log = logging.getLogger(__name__)

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"

# Response codes from the portal spec.
RESPONSE_OK = 0
RESPONSE_CANCELLED = 1
RESPONSE_OTHER = 2
# Ours, not the spec's: the portal itself can't be reached (it isn't installed, it crashed,
# or it doesn't know the interface). Nothing was asked of the user, nothing was refused.
RESPONSE_UNAVAILABLE = -1

_UNREACHABLE = (
    Gio.DBusError.SERVICE_UNKNOWN,
    Gio.DBusError.NAME_HAS_NO_OWNER,
    Gio.DBusError.UNKNOWN_METHOD,
    Gio.DBusError.UNKNOWN_INTERFACE,
    Gio.DBusError.UNKNOWN_OBJECT,
    Gio.DBusError.NO_REPLY,
    Gio.DBusError.TIMEOUT,
    Gio.DBusError.TIMED_OUT,
    Gio.DBusError.DISCONNECTED,
    Gio.DBusError.SPAWN_SERVICE_NOT_FOUND,
)


def _unreachable(err: GLib.Error) -> bool:
    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT) or err.matches(
        Gio.io_error_quark(), Gio.IOErrorEnum.CLOSED
    ):
        return True
    return any(err.matches(Gio.dbus_error_quark(), code) for code in _UNREACHABLE)


ResponseCallback = Callable[[int, dict[str, Any]], None]

_counter = itertools.count(1)


def _v(sig: str, value: Any) -> GLib.Variant:
    return GLib.Variant(sig, value)


class PortalClient:
    def __init__(self) -> None:
        # A private connection: GTK already talks to portals on the shared one, and the
        # host app registry only accepts registration as the first call on a connection.
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.bus: Gio.DBusConnection = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        unique = self.bus.get_unique_name() or ""
        self._sender = unique.lstrip(":").replace(".", "_")
        self._registered = False
        self._restart_listeners: list[Callable[[], None]] = []
        self._pending: dict[int, ResponseCallback] = {}  # requests waiting for a Response
        self._gone = False  # the portal service left the bus and hasn't come back yet
        # Must be the very first portal call on this connection.
        self.register_host_app()
        self.bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            BUS_NAME,
            Gio.DBusSignalFlags.NONE,
            self._on_owner_changed,
        )

    # -- plumbing -----------------------------------------------------------------
    def _token(self) -> str:
        return f"grabbyhands_{os.getpid()}_{next(_counter)}"

    def register_host_app(self) -> None:
        """Tell the portal our app ID when running unsandboxed (must be the first call)."""
        if self._registered or in_flatpak():
            return
        self._registered = True
        try:
            self.bus.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                "org.freedesktop.host.portal.Registry",
                "Register",
                GLib.Variant("(sa{sv})", (APP_ID, {})),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            log.debug("registered host app id %s", APP_ID)
        except GLib.Error as err:  # older portals: harmless
            log.info("host app registration unavailable: %s", err.message)

    def on_restart(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` when the portal service comes back after going away. Sessions
        made with the old one (global shortcuts) are gone then and must be made again."""
        self._restart_listeners.append(listener)

    def _on_owner_changed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        _name, old_owner, new_owner = params.unpack()
        if old_owner:
            # The portal went away. Whatever it still owed us will never arrive: answer
            # those requests ourselves instead of leaving their callers waiting forever.
            self._gone = True
            pending, self._pending = self._pending, {}
            for sub_id, callback in pending.items():
                self.bus.signal_unsubscribe(sub_id)
                callback(RESPONSE_UNAVAILABLE, {"error": "the portal service stopped"})
        if new_owner and self._gone:  # (its first start is not a restart)
            self._gone = False
            self._registered = False
            self.register_host_app()  # the first call the new instance sees from us
            for listener in list(self._restart_listeners):
                listener()

    def request(
        self,
        interface: str,
        method: str,
        build_params: Callable[[str], GLib.Variant],
        callback: ResponseCallback,
    ) -> None:
        """Call a request-style portal method; ``build_params(token)`` builds its arguments.

        ``callback(response, results)`` is called exactly once: with the portal's answer,
        with RESPONSE_UNAVAILABLE when the portal can't be reached (or goes away before it
        answers), or with RESPONSE_OTHER when it refuses the call.
        """
        self.register_host_app()
        token = self._token()
        path = f"{OBJECT_PATH}/request/{self._sender}/{token}"
        sub_id = 0

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            if self._pending.pop(sub_id, None) is None:
                return  # already answered (the call itself failed, or the portal went away)
            self.bus.signal_unsubscribe(sub_id)
            response, results = params.unpack()
            callback(response, results)

        sub_id = self.bus.signal_subscribe(
            BUS_NAME,
            REQUEST_IFACE,
            "Response",
            path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )
        self._pending[sub_id] = callback

        def on_called(bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                bus.call_finish(result)
            except GLib.Error as err:
                if self._pending.pop(sub_id, None) is None:
                    return
                self.bus.signal_unsubscribe(sub_id)
                log.warning("%s.%s failed: %s", interface, method, err.message)
                code = RESPONSE_UNAVAILABLE if _unreachable(err) else RESPONSE_OTHER
                callback(code, {"error": err.message})

        self.bus.call(
            BUS_NAME,
            OBJECT_PATH,
            interface,
            method,
            build_params(token),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            on_called,
        )

    def version(self, interface: str) -> int:
        try:
            reply = self.bus.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                "org.freedesktop.DBus.Properties",
                "Get",
                GLib.Variant("(ss)", (interface, "version")),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            return int(reply.unpack()[0])
        except GLib.Error:
            return 0

    # -- Screenshot ---------------------------------------------------------------
    def screenshot(self, interactive: bool, callback: Callable[[str | None, int], None]) -> None:
        """Take a screenshot. Calls ``callback(uri, response)``; ``uri`` is None on failure.

        ``response`` is RESPONSE_CANCELLED when the user backed out, RESPONSE_OTHER when the
        desktop refused (for example because screenshot permission hasn't been granted) and
        RESPONSE_UNAVAILABLE when there is no screenshot portal to ask.
        """

        def params(token: str) -> GLib.Variant:
            options = {
                "handle_token": _v("s", token),
                "modal": _v("b", True),
                "interactive": _v("b", interactive),
            }
            return GLib.Variant("(sa{sv})", ("", options))

        def done(response: int, results: dict[str, Any]) -> None:
            if response == RESPONSE_OK and results.get("uri"):
                callback(results["uri"], response)
            else:
                log.info("screenshot not taken (response %s)", response)
                callback(None, response)

        self.request("org.freedesktop.portal.Screenshot", "Screenshot", params, done)

    # -- Background / autostart ---------------------------------------------------
    def request_background(self, autostart: bool, callback: ResponseCallback | None = None) -> None:
        def params(token: str) -> GLib.Variant:
            options = {
                "handle_token": _v("s", token),
                "reason": _v("s", _("Keep GrabbyHands ready for the Print Screen key")),
                "autostart": _v("b", autostart),
                "commandline": _v("as", ["grabbyhands", "--background"]),
                "dbus-activatable": _v("b", False),
            }
            return GLib.Variant("(sa{sv})", ("", options))

        self.request(
            "org.freedesktop.portal.Background",
            "RequestBackground",
            params,
            callback or (lambda r, _res: log.debug("background request: %s", r)),
        )


class GlobalShortcuts:
    """One GlobalShortcuts session for the lifetime of the app."""

    IFACE = "org.freedesktop.portal.GlobalShortcuts"

    def __init__(
        self,
        portal: PortalClient,
        on_activated: Callable[[str], None],
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        self.portal = portal
        self.on_activated = on_activated
        self.on_changed = on_changed  # the user changed the keys in the desktop's settings
        self.session_handle: str | None = None
        self._signal_ids: list[int] = []
        self._starts = 0  # goes up whenever the session is dropped (see stop)
        self.triggers: dict[str, str] = {}  # shortcut id -> keys as GNOME describes them

    @property
    def available(self) -> bool:
        return self.portal.version(self.IFACE) >= 1

    def start(
        self,
        shortcuts: list[tuple[str, str, str]],
        bind_if_missing: bool,
        done: Callable[[bool, list[str]], None],
        complete: bool = False,
    ) -> None:
        """Create a session; bind ``(id, description, preferred_trigger)`` shortcuts if needed.

        Without ``complete``, the desktop is only asked when it has none of our shortcuts
        yet. One that the user removed, or left without a key, stays that way: asking for it
        again at every login would be nagging. ``complete`` (the user pressed "Set Up
        Shortcuts") asks for whatever is missing.

        ``done(ok, bound_ids)`` reports what the desktop has bound for us.
        """
        self.stop()
        started = self._starts
        for signal, handler in (
            ("Activated", self._on_activated),
            ("ShortcutsChanged", self._on_shortcuts_changed),
        ):
            self._signal_ids.append(
                self.portal.bus.signal_subscribe(
                    BUS_NAME,
                    self.IFACE,
                    signal,
                    OBJECT_PATH,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    handler,
                )
            )
        session_token = self.portal._token()

        def params(token: str) -> GLib.Variant:
            return GLib.Variant(
                "(a{sv})",
                (
                    {
                        "handle_token": _v("s", token),
                        "session_handle_token": _v("s", session_token),
                    },
                ),
            )

        def created(response: int, results: dict[str, Any]) -> None:
            if started != self._starts:
                return  # stopped or started again meanwhile: this answer is for nobody
            if response != RESPONSE_OK:
                done(False, [])
                return
            self.session_handle = results.get("session_handle")
            self.list(lambda ids: self._after_list(ids, shortcuts, bind_if_missing, complete, done))

        self.portal.request(self.IFACE, "CreateSession", params, created)

    def stop(self) -> None:
        """Forget the session (the portal that made it is gone, or a new one is starting)."""
        for signal_id in self._signal_ids:
            self.portal.bus.signal_unsubscribe(signal_id)
        self._signal_ids = []
        self._starts += 1
        self.session_handle = None
        self.triggers = {}

    def _after_list(self, ids, shortcuts, bind_if_missing, complete, done) -> None:
        missing = not {s[0] for s in shortcuts}.issubset(ids)
        if bind_if_missing and missing and (complete or not ids):
            self.bind(shortcuts, done)
        else:
            done(bool(ids), ids)

    def _remember_triggers(self, results: dict[str, Any]) -> None:
        """Keep each bound shortcut's human-readable keys, e.g. 'Print' (for Settings).

        The answer lists everything that is bound now, so it replaces what we knew: a
        shortcut the user removed in the desktop's settings is forgotten here too.
        """
        triggers = {}
        for shortcut_id, props in results.get("shortcuts", []):
            text = props.get("trigger_description", "") if isinstance(props, dict) else ""
            triggers[shortcut_id] = text
        self.triggers = triggers

    def list(self, callback: Callable[[list[str]], None]) -> None:
        session = self.session_handle

        def params(token: str) -> GLib.Variant:
            return GLib.Variant("(oa{sv})", (session, {"handle_token": _v("s", token)}))

        def got(response: int, results: dict[str, Any]) -> None:
            if session != self.session_handle:
                return  # that session is gone
            ids = [s[0] for s in results.get("shortcuts", [])] if response == RESPONSE_OK else []
            if response == RESPONSE_OK:
                self._remember_triggers(results)
            log.debug("bound shortcuts: %s", ids)
            callback(ids)

        self.portal.request(self.IFACE, "ListShortcuts", params, got)

    def bind(
        self, shortcuts: list[tuple[str, str, str]], done: Callable[[bool, list[str]], None]
    ) -> None:
        session = self.session_handle

        def params(token: str) -> GLib.Variant:
            items = [
                (sid, {"description": _v("s", desc), "preferred_trigger": _v("s", trig)})
                for sid, desc, trig in shortcuts
            ]
            return GLib.Variant(
                "(oa(sa{sv})sa{sv})",
                (session, items, "", {"handle_token": _v("s", token)}),
            )

        def bound(response: int, results: dict[str, Any]) -> None:
            if session != self.session_handle:
                return  # that session is gone
            ids = [s[0] for s in results.get("shortcuts", [])] if response == RESPONSE_OK else []
            if response == RESPONSE_OK:
                self._remember_triggers(results)
            # "answered" = the user saw the dialog (accepted or dismissed it)
            done(response in (RESPONSE_OK, RESPONSE_CANCELLED), ids)

        self.portal.request(self.IFACE, "BindShortcuts", params, bound)

    def _on_activated(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        session, shortcut_id, _timestamp, _options = params.unpack()
        if session == self.session_handle:
            log.debug("shortcut activated: %s", shortcut_id)
            self.on_activated(shortcut_id)

    def _on_shortcuts_changed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        session, shortcuts = params.unpack()
        if session == self.session_handle:
            self._remember_triggers({"shortcuts": shortcuts})
            if self.on_changed is not None:
                self.on_changed()
