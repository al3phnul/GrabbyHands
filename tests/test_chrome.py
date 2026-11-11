# SPDX-License-Identifier: GPL-3.0-or-later
"""The hand-drawn chrome (grabbyhands/chrome.py), drawn off screen.

Straight edges have to land on whole device pixels at scale 1 and at scale 2: a frame or a
panel edge that straddles two pixels turns into a blurry grey line.
"""

import cairo
import pytest

from grabbyhands import chrome, theme


def _surface(scale: int, width: int = 80, height: int = 60):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
    ctx = cairo.Context(surface)
    ctx.scale(scale, scale)
    return surface, ctx


def _alpha(surface: cairo.ImageSurface, x: int, y: int) -> int:
    surface.flush()
    offset = y * surface.get_stride() + x * 4
    return int.from_bytes(bytes(surface.get_data()[offset : offset + 4]), "little") >> 24


@pytest.mark.parametrize("scale", [1, 2])
def test_frame_edges_sit_on_whole_pixels(scale):
    surface, ctx = _surface(scale)
    chrome.frame(ctx, 20, 15, 40, 30, (1.0, 0.0, 0.0))
    halo = round(theme.HALO[3] * 255)
    row = 30 * scale  # through the middle of the box: only vertical edges cross it
    seen = [_alpha(surface, x, row) for x in range(surface.get_width())]
    assert set(seen) <= {0, halo, halo + 1, halo - 1, 255}, sorted(set(seen))
    # from the outside in: nothing, a one-pixel halo, a two-pixel stroke, then the picture
    stroke = round(theme.STROKE)
    left = 20 * scale
    assert seen[left - (stroke + 1) * scale - 1] == 0
    assert all(
        abs(a - halo) <= 1 for a in seen[left - (stroke + 1) * scale : left - stroke * scale]
    )
    assert all(a == 255 for a in seen[left - stroke * scale : left])
    assert seen[left] == 0  # the frame never covers the box itself


@pytest.mark.parametrize("scale", [1, 2])
def test_frame_rounds_fractional_boxes(scale):
    # a selection on a fractionally scaled screen: still crisp
    surface, ctx = _surface(scale)
    chrome.frame(ctx, 20.4, 15.3, 39.7, 30.2, (0.0, 0.0, 1.0))
    halo = round(theme.HALO[3] * 255)
    row = 30 * scale
    seen = {_alpha(surface, x, row) for x in range(surface.get_width())}
    assert seen <= {0, halo - 1, halo, halo + 1, 255}, sorted(seen)


@pytest.mark.parametrize("scale", [1, 2])
def test_panel_has_a_crisp_opaque_body(scale):
    surface, ctx = _surface(scale)
    chrome.panel(ctx, 20, 15, 40, 30, radius=theme.RADIUS_S, shadow=False)
    row = 30 * scale
    # outside the dark hairline nothing is drawn; inside, the panel is almost solid
    assert _alpha(surface, 20 * scale - scale - 1, row) == 0
    inside = _alpha(surface, 40 * scale, row)
    assert inside >= round(theme.GLASS_ALPHA * 255) - 1
    assert _alpha(surface, 60 * scale + scale, row) == 0


def test_rounded_rect_handles_degenerate_sizes():
    _surface_, ctx = _surface(1)
    for w, h, r in ((0, 0, 6), (4, 2, 12), (10, 10, 0), (10, 10, -3)):
        chrome.rounded_rect(ctx, 5, 5, w, h, r)
        ctx.fill()  # must not raise


def test_handle_is_a_white_dot_with_an_accent_ring():
    surface, ctx = _surface(1)
    chrome.handle(ctx, 40, 30, (1.0, 0.0, 0.0))
    surface.flush()
    offset = 30 * surface.get_stride() + 40 * 4
    blue, green, red, alpha = surface.get_data()[offset : offset + 4]
    assert (red, green, blue, alpha) == (255, 255, 255, 255)  # white in the middle
    # the ring covers radius - 1 … radius + 1; the pixel just inside the radius is all ring
    offset = 30 * surface.get_stride() + (40 + int(theme.HANDLE_RADIUS)) * 4
    blue, green, red, alpha = surface.get_data()[offset : offset + 4]
    assert red > 200 and green < 80 and blue < 80
