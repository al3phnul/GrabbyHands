# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs tests/shell_smoke.py next to a private, headless GNOME Shell (never the real one)."""

import os
from pathlib import Path

import pytest
import shell_session

HERE = Path(__file__).resolve().parent
# CI's GNOME Shell job sets this: there, "no Shell" must fail instead of being skipped.
REQUIRED = bool(os.environ.get("GRABBYHANDS_REQUIRE_SHELL"))


def test_window_helper_in_a_real_shell():
    if not shell_session.available():
        assert not REQUIRED, "gnome-shell, dbus-run-session or glib-compile-schemas is missing"
        pytest.skip("gnome-shell is not installed")
    result = shell_session.run(HERE / "shell_smoke.py", timeout=300)
    print(result.stdout)
    if result.returncode == 77 and not REQUIRED:
        pytest.skip(result.stdout.strip().splitlines()[0])
    assert result.returncode == 0, result.stdout + result.stderr[-6000:]
