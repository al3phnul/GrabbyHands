# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a Python script against a private GTK Broadway display and a private session bus.

Used by the GUI smoke test (tests/test_gui.py) and the screenshot tool
(build-aux/screenshots.py). Nothing here touches the real desktop: the display, the D-Bus
session, settings (memory backend) and all XDG folders are private to one run, so several
runs can happen at the same time.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BROADWAYD = shutil.which("gtk4-broadwayd")

# No service directories: nothing (portals, keyring…) can be auto-started on this bus.
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""

FIRST_DISPLAY = 100
DISPLAY_SPAN = 800  # displays 100..899 → HTTP ports 8180..8979
ATTEMPTS = 8


class BroadwayUnavailable(RuntimeError):
    pass


def available() -> bool:
    return BROADWAYD is not None


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _start_daemon(env: dict[str, str]) -> tuple[subprocess.Popen, int]:
    """Start gtk4-broadwayd on a free display. Each process starts from its own slot (by
    PID) and moves on if another run got there first, so parallel runs don't collide."""
    start = os.getpid() % DISPLAY_SPAN
    last_error = ""
    for attempt in range(ATTEMPTS):
        display = FIRST_DISPLAY + (start + attempt * 37) % DISPLAY_SPAN
        port = 8080 + display
        if _port_open(port):
            continue
        daemon = subprocess.Popen(
            [BROADWAYD, "--address", "127.0.0.1", f":{display}"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if daemon.poll() is not None:
                last_error = daemon.stdout.read() if daemon.stdout else ""
                break  # lost a race for this display: try the next one
            if _port_open(port):
                return daemon, display
            time.sleep(0.1)
        else:
            daemon.terminate()
            last_error = "did not start listening"
    raise BroadwayUnavailable(f"could not start gtk4-broadwayd: {last_error}")


def run(
    script: Path,
    args: list[str] | None = None,
    timeout: float = 180,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``python script *args`` on a private display/bus and return the finished process."""
    if BROADWAYD is None:
        raise BroadwayUnavailable("gtk4-broadwayd (package libgtk-4-bin) is not installed")
    home = Path(tempfile.mkdtemp(prefix="gh-gui-"))
    runtime = home / "runtime"
    runtime.mkdir(mode=0o700)
    env = {
        **os.environ,
        "GDK_BACKEND": "broadway",
        "GDK_DEBUG": "no-portals",
        "GSETTINGS_BACKEND": "memory",
        "GRABBYHANDS_DEV": "1",
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_RUNTIME_DIR": str(runtime),
        "NO_AT_BRIDGE": "1",
        **(extra_env or {}),
    }
    # never let a child talk to the real desktop
    for name in ("WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        env.pop(name, None)
    daemon, display = _start_daemon(env)
    env["BROADWAY_DISPLAY"] = f":{display}"
    try:
        cmd = [sys.executable, str(script), *(args or [])]
        if shutil.which("dbus-run-session"):
            config = home / "bus.conf"
            config.write_text(BUS_CONFIG)
            cmd = ["dbus-run-session", f"--config-file={config}", "--", *cmd]
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    finally:
        daemon.terminate()
        try:
            daemon.wait(5)
        except subprocess.TimeoutExpired:
            daemon.kill()
        shutil.rmtree(home, ignore_errors=True)
