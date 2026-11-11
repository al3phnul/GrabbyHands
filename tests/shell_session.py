# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a Python script next to a private, headless GNOME Shell.

Used by tests/test_shell.py to try the Window Helper extension in a real Shell. Nothing here
touches the real desktop: the Shell draws to no screen, and it gets its own D-Bus session,
settings and XDG folders, all thrown away afterwards.

The outer half (``run``) prepares that private world and starts a script in it; the inner
half (``Shell``) is used by that script to start and stop the Shell and to talk to it.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parent.parent
# The extension under test: the checkout's, or an installed copy named by this variable
# (e.g. /usr/share/gnome-shell/extensions/grabbyhands@al3phnul.github.io).
HELPER_SOURCE = Path(os.environ.get("GRABBYHANDS_TEST_HELPER") or ROOT / "data" / "shell-extension")
HOOK_SOURCE = Path(__file__).resolve().parent / "shell" / "unsafe-mode@grabbyhands.test"
HELPER_UUID = "grabbyhands@al3phnul.github.io"
HOOK_UUID = HOOK_SOURCE.name
TOOLS = ("gnome-shell", "dbus-run-session", "glib-compile-schemas")
# GNOME's settings service (dconf). Started on the private bus, it keeps its database in the
# private config folder, and every program in the session saves through it, as on a real
# desktop. (A plain settings file would not do: several programs rewrite it at once.)
DCONF_SERVICE = Path("/usr/share/dbus-1/services/ca.desrt.dconf.service")

# One private service directory: nothing (portals, online accounts, a real GrabbyHands…) can
# be auto-started on this bus, except what ``run`` puts in that directory.
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <servicedir>{services}</servicedir>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


class ShellUnavailable(RuntimeError):
    pass


def available() -> bool:
    return all(shutil.which(tool) for tool in TOOLS) and HELPER_SOURCE.is_dir()


def _install(source: Path, extensions: Path) -> None:
    """Put an extension where the private Shell looks for it, as ``gnome-extensions install``
    would: the files, plus the compiled settings schema if it has one."""
    target = extensions / json.loads((source / "metadata.json").read_text("utf-8"))["uuid"]
    shutil.copytree(source, target)
    schemas = target / "schemas"
    if schemas.is_dir() and not (schemas / "gschemas.compiled").exists():
        subprocess.run(["glib-compile-schemas", "--strict", str(schemas)], check=True)


def run(
    script: Path,
    args: list[str] | None = None,
    timeout: float = 240,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``python script *args`` in a private session and return the finished process."""
    if not available():
        raise ShellUnavailable("needs " + ", ".join(TOOLS))
    # A short path: the Shell's Wayland socket lives in it, and socket paths are limited.
    home = Path(tempfile.mkdtemp(prefix="gh-sh-"))
    try:
        runtime = home / "run"
        runtime.mkdir(mode=0o700)
        extensions = home / "data" / "gnome-shell" / "extensions"
        extensions.mkdir(parents=True)
        _install(HELPER_SOURCE, extensions)
        _install(HOOK_SOURCE, extensions)
        services = home / "services"
        services.mkdir()
        env = {
            **os.environ,
            # Other extensions may run in this Shell too (a distro's session mode turns
            # some on); a private home keeps every one of them away from real files.
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_RUNTIME_DIR": str(runtime),
            "NO_AT_BRIDGE": "1",
            "GH_SHELL_LOG": str(home / "shell.log"),
            **(extra_env or {}),
        }
        # never let a child talk to the real desktop
        for name in ("WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_ID"):
            env.pop(name, None)
        # Settings that survive a restart of the Shell: dconf where there is one, else one
        # settings file (fine as long as the Shell is the only program that saves).
        for name in ("GSETTINGS_BACKEND", "DCONF_PROFILE"):
            env.pop(name, None)
        if DCONF_SERVICE.is_file():
            shutil.copy(DCONF_SERVICE, services)
        else:
            env["GSETTINGS_BACKEND"] = "keyfile"
        config = home / "bus.conf"
        config.write_text(BUS_CONFIG.format(services=services))
        cmd = [
            "dbus-run-session",
            f"--config-file={config}",
            "--",
            sys.executable,
            str(script),
            *(args or []),
        ]
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    finally:
        shutil.rmtree(home, ignore_errors=True)


class Shell:
    """A headless GNOME Shell on the private bus (use from the script ``run`` started)."""

    START_TIMEOUT = 60

    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.process: subprocess.Popen | None = None
        self.log = Path(os.environ["GH_SHELL_LOG"])

    # -- life cycle ----------------------------------------------------------------
    def start(self) -> str:
        """Start the Shell, wait until it answers, and return its version."""
        with self.log.open("a") as log:
            self.process = subprocess.Popen(
                [
                    "gnome-shell",
                    "--headless",
                    "--no-x11",
                    "--wayland-display=gh-test",
                    "--virtual-monitor",
                    "1280x720",
                    # e.g. --mode=ubuntu, to try it the way a distro sets GNOME up
                    *shlex.split(os.environ.get("GRABBYHANDS_TEST_SHELL_ARGS", "")),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + self.START_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ShellUnavailable(f"gnome-shell stopped at once:\n{self.log_tail()}")
            try:
                reply = self.call(
                    "org.gnome.Shell",
                    "/org/gnome/Shell",
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    GLib.Variant("(ss)", ("org.gnome.Shell", "ShellVersion")),
                )
                return reply[0]
            except GLib.Error:
                time.sleep(0.25)
        self.stop()
        raise ShellUnavailable(f"gnome-shell did not answer:\n{self.log_tail()}")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)
        self.process = None

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    # -- talking to it ---------------------------------------------------------------
    def call(self, name: str, path: str, interface: str, method: str, args=None):
        reply = self.bus.call_sync(
            name, path, interface, method, args, None, Gio.DBusCallFlags.NO_AUTO_START, 5000, None
        )
        return reply.unpack()

    def enable(self, uuid: str) -> None:
        self._switch("EnableExtension", uuid)

    def disable(self, uuid: str) -> None:
        self._switch("DisableExtension", uuid)

    def _switch(self, method: str, uuid: str) -> None:
        (done,) = self.call(
            "org.gnome.Shell",
            "/org/gnome/Shell",
            "org.gnome.Shell.Extensions",
            method,
            GLib.Variant("(s)", (uuid,)),
        )
        if not done:
            raise ShellUnavailable(f"{method}({uuid}) was refused:\n{self.log_tail()}")

    def eval(self, code: str):
        """Run JavaScript inside the Shell (needs the test hook) and return its value."""
        done, value = self.call(
            "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval",
            GLib.Variant("(s)", (code,)),
        )  # fmt: skip
        if not done:
            raise RuntimeError(f"the Shell could not run {code!r}: {value or 'not allowed'}")
        return json.loads(value) if value else None

    def wait_for(self, check, what: str, timeout: float = 15):
        """Poll ``check()`` until it returns something true; fail with ``what`` otherwise."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = check()
            except (GLib.Error, RuntimeError) as err:
                last = err
            else:
                if last:
                    return last
            time.sleep(0.2)
        raise AssertionError(f"{what} (last answer: {last!r})\n{self.log_tail()}")
