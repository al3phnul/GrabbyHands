# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs tests/gui_smoke.py against a private GTK Broadway display (no real desktop needed)."""

from pathlib import Path

import broadway
import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(not broadway.available(), reason="gtk4-broadwayd (libgtk-4-bin) not installed")
def test_gui_smoke():
    # Generous: on old libadwaita every dialog waits about a second for its frames.
    result = broadway.run(HERE / "gui_smoke.py", timeout=480)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr[-6000:]
