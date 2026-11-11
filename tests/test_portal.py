# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs tests/portal_smoke.py on a private session bus (never the real one)."""

from pathlib import Path

import broadway
import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(not broadway.available(), reason="gtk4-broadwayd (libgtk-4-bin) not installed")
def test_portal_client_over_dbus():
    result = broadway.run(HERE / "portal_smoke.py", timeout=120)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr[-6000:]
