# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke test: drives the real windows on GTK's Broadway backend.

Run through tests/test_gui.py (which starts a private gtk4-broadwayd), not directly.
Every step calls the same code the buttons and keys call; any Python exception raised in a
GTK callback (draw functions, signal handlers) fails the test.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import builtins  # noqa: E402

builtins._ = lambda s: s

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402, F401
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ERRORS: list[str] = []


def _hook(exc_type, exc, tb) -> None:
    ERRORS.append("".join(traceback.format_exception(exc_type, exc, tb)))


TMP = Path(tempfile.mkdtemp(prefix="gh-smoke-"))
SCREEN_W, SCREEN_H = 1024, 768
PURPLE = (124, 108, 255)


def make_screen() -> Image.Image:
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (245, 245, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle((600, 100, 700, 200), fill=PURPLE)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default(size=34)
    draw.text((40, 60), "Grabby Hands smoke test", fill="black", font=font)
    return img


class FakeBus:
    def signal_subscribe(self, *_args) -> int:
        return 1

    def signal_unsubscribe(self, _subscription: int) -> None:
        pass


class FakePortal:
    """Stands in for the XDG portals: hands out our fake screen, binds nothing."""

    def __init__(self) -> None:
        self.bus = FakeBus()
        self.calls = 0
        # every request-style call (global shortcuts): (method, callback). Never answered
        # unless a step calls the callback itself.
        self.requests: list[tuple[str, object]] = []

    def version(self, _iface: str) -> int:
        return 0

    def _token(self) -> str:
        return f"token{len(self.requests)}"

    def request(self, _interface: str, method: str, _build_params, callback) -> None:
        self.requests.append((method, callback))

    def screenshot(self, interactive: bool, callback) -> None:
        self.calls += 1
        path = TMP / f"portal-{self.calls}.png"
        make_screen().save(path)
        uri = Gio.File.new_for_path(str(path)).get_uri()
        GLib.idle_add(lambda: (callback(uri, 0), False)[1])

    def request_background(self, *_args, **_kwargs) -> None:
        pass

    def on_restart(self, _listener) -> None:
        pass


def pump(seconds: float = 0.25) -> None:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


def wait_for(predicate, timeout: float = 20.0, what: str = "condition") -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        pump(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def render(win) -> None:
    """Run an overlay's draw function on an offscreen surface (Broadway paints nothing
    without a browser attached, so draw code would otherwise never execute)."""
    import cairo

    w, h = win.geo.width or SCREEN_W, win.geo.height or SCREEN_H
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    win._draw(win.area, cairo.Context(surface), w, h)


def clipboard_formats(widget=None) -> str:
    display = Gdk.Display.get_default()
    return display.get_clipboard().get_formats().to_string()


def clipboard_text() -> str:
    box: dict[str, str] = {}
    cb = Gdk.Display.get_default().get_clipboard()

    def done(clip, result) -> None:
        box["text"] = clip.read_text_finish(result) or ""

    cb.read_text_async(None, done)
    wait_for(lambda: "text" in box, 5, "clipboard text")
    return box["text"]


def _labels(widget):
    """Every Gtk.Label inside ``widget``."""
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            yield child
        yield from _labels(child)
        child = child.get_next_sibling()


# -- behavior and regressions -------------------------------------------------------------
# Steps that pin down behavior and past bugs. Each takes ``S``: the app, its capture controller,
# settings, the list of notification tags (``sent``), full notifications (``notes``) and
# ``overlay()`` from main(). They are registered at the end of the step list.
NO_MODS = Gdk.ModifierType(0)
CTRL = Gdk.ModifierType.CONTROL_MASK
SHIFT = Gdk.ModifierType.SHIFT_MASK
ALT = Gdk.ModifierType.ALT_MASK


class FakeGesture:
    """All the canvas asks of a drag gesture."""

    def get_current_event_state(self) -> Gdk.ModifierType:
        return NO_MODS


class FakeMonitor:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.geo = Gdk.Rectangle()
        self.geo.x, self.geo.y, self.geo.width, self.geo.height = x, y, width, height

    def get_geometry(self) -> Gdk.Rectangle:
        return self.geo


def close_dialog(dialog, window, timeout: float = 15.0) -> None:
    """Close an Adw.Dialog and wait until its window has really let go of it.

    On libadwaita 1.5 a dialog only counts as open two frames after it was presented.
    Closing it before that does nothing; it then opens after all and swallows the window's
    next close(). Frames are rare here (no browser is attached), so try until it is gone.
    """
    end = time.monotonic() + timeout
    while True:
        dialog.close()
        pump(0.1)
        if window.get_visible_dialog() is None:
            return
        if time.monotonic() > end:
            raise AssertionError(f"{type(dialog).__name__} would not close")


DIALOG_WAIT = [0.0]  # seconds spent waiting for dialogs to really open or close


def patient_dialogs() -> None:
    """Let Adw.Dialog work here, where frames are rare.

    Up to libadwaita 1.6 a dialog only counts as open two frames after it was presented:
    close() before that does nothing, and the dialog then also keeps its window from
    closing. A person never notices (two frames are 33 ms), but no browser is attached to
    Broadway and a frame can be more than a second away. So present() returns here once the
    dialog has really opened, and close() once it has really closed (the way the app itself
    notices: grabbyhands.dialogs.when_closed).
    """
    real_present, real_close = Adw.Dialog.present, Adw.Dialog.close
    version = (Adw.get_major_version(), Adw.get_minor_version())
    needs_frames = version < (1, 7) or bool(os.environ.get("GRABBYHANDS_TEST_SLOW_DIALOGS"))
    showing: set = set()
    gone: set = set()

    def closed(dialog) -> None:
        showing.discard(dialog)
        gone.add(dialog)

    def present(dialog, parent=None):
        from grabbyhands.dialogs import when_closed

        real_present(dialog, parent)
        if dialog in showing:
            return
        gone.discard(dialog)
        showing.add(dialog)
        when_closed(dialog, lambda: closed(dialog))
        root = dialog.get_root()
        own_window = root is not None and root.get_child() == dialog  # opens at once
        if not needs_frames or own_window:
            return
        frames = [0]

        def tick(_widget, _clock) -> bool:
            frames[0] += 1
            return frames[0] < 3

        dialog.add_tick_callback(tick)
        start = time.monotonic()
        while frames[0] < 3 and dialog in showing and time.monotonic() - start < 10:
            pump(0.05)
        DIALOG_WAIT[0] += time.monotonic() - start

    def close(dialog):
        if dialog in gone:
            # Closed already. On libadwaita 1.5.0, closing a dialog again after its own
            # window went away crashes inside the library.
            return False
        waiting = dialog in showing
        result = real_close(dialog)
        start = time.monotonic()
        while waiting and dialog in showing and time.monotonic() - start < 10:
            pump(0.05)
            if dialog in showing and dialog.get_mapped():
                real_close(dialog)
        DIALOG_WAIT[0] += time.monotonic() - start
        return result

    Adw.Dialog.present = present
    Adw.Dialog.close = close


def file_uri(path: Path) -> str:
    return Gio.File.new_for_path(str(path)).get_uri()


def windows_of(S, type_name: str) -> list:
    return [w for w in S.app.get_windows() if type(w).__name__ == type_name]


def open_editor(S, image: Image.Image | None = None):
    """A new capture, opened in the editor: (entry, window)."""
    entry = S.app.record(image or make_screen().crop((0, 0, 800, 500)))
    S.app.open_editor(entry)
    pump(0.4)
    win = next(w for w in windows_of(S, "EditorWindow") if w.entry.id == entry.id)
    return entry, win


def canvas_drag(win, x0, y0, x1, y1) -> None:
    """A mouse drag on the editor canvas (image coordinates), through the real handlers."""
    canvas = win.canvas
    sx0, sy0 = canvas.to_screen(x0, y0)
    sx1, sy1 = canvas.to_screen(x1, y1)
    gesture = FakeGesture()
    canvas._on_drag_begin(gesture, sx0, sy0)
    if (sx0, sy0) != (sx1, sy1):
        canvas._on_drag_update(gesture, (sx1 - sx0) / 2, (sy1 - sy0) / 2)
        canvas._on_drag_update(gesture, sx1 - sx0, sy1 - sy0)
    canvas._on_drag_end(gesture, sx1 - sx0, sy1 - sy0)


def canvas_click(win, x, y) -> None:
    canvas_drag(win, x, y, x, y)


def fx_setup(S) -> None:
    """Keep what notifications say (not only their tags) and when they are taken down."""
    real_notify = S.app.notify

    def notify(title, body=None, tag="info", transient=False, buttons=()):
        S.notes.append(
            types.SimpleNamespace(
                title=title, body=body or "", tag=tag, transient=transient, buttons=buttons
            )
        )
        return real_notify(title, body, tag, transient, buttons)

    S.app.notify = notify
    S.app.withdraw_notification = lambda tag: S.withdrawn.append((tag, time.monotonic()))
    # the history folder of this run is private to its owner
    import stat

    from grabbyhands import config

    root = config.data_dir()
    assert str(root).startswith(os.environ["XDG_DATA_HOME"]), root
    assert stat.S_IMODE(root.stat().st_mode) == 0o700, oct(root.stat().st_mode)
    assert stat.S_IMODE((root / "history.json").stat().st_mode) == 0o600


def fx_other_keyboard_layouts(S) -> None:
    """Keys are letters; with a Russian, Greek or Arabic layout the same physical keys
    type other letters. They used to do nothing then."""
    win = S.overlay()
    win._on_key(None, Gdk.KEY_Greek_iota, 31, NO_MODS)  # the I key on a Greek layout
    assert win.mode == "color", win.mode
    win._on_key(None, Gdk.KEY_Greek_iota, 31, NO_MODS)
    win.set_selection(_rect(100, 100, 300, 200))
    S.sent.clear()
    assert win._on_key(None, Gdk.KEY_Cyrillic_es, 54, NO_MODS)  # the C key, Russian layout
    assert not S.cap.windows and "capture" in S.sent, S.sent
    assert "image/png" in clipboard_formats()
    win = S.overlay()
    win.set_selection(_rect(100, 100, 300, 200))
    S.sent.clear()
    assert win._on_key(None, Gdk.KEY_Arabic_seen, 39, NO_MODS)  # the S key, Arabic layout
    assert not S.cap.windows and "capture" in S.sent, S.sent
    # a Latin layout's own keys are untouched: on AZERTY the key in the Q position types A
    entry, ed = open_editor(S)
    ed.set_tool("select")
    assert ed._on_key(None, Gdk.KEY_a, 24, NO_MODS) and ed.canvas.tool.name == "arrow"
    assert ed._on_key(None, Gdk.KEY_Cyrillic_ie, 28, NO_MODS)  # the T key, Russian layout
    assert ed.canvas.tool.name == "text", ed.canvas.tool.name
    assert not ed._on_key(None, Gdk.KEY_eacute, 11, NO_MODS)  # é is no tool's key
    ed.close()
    S.app.show_history()
    pump(0.3)
    hist = S.app._history_window
    S.app.copy_text("not a picture", hist)
    hist.selection.set_selected(0)
    assert hist._on_key(None, Gdk.KEY_Cyrillic_es, 54, CTRL)  # Ctrl+C
    assert "image/png" in clipboard_formats(), clipboard_formats()
    hist.close()
    S.app.delete_capture(entry.id, trash_saved=False)


def fx_nudging_at_the_edges(S) -> None:
    win = S.overlay()
    win.set_selection(_rect(SCREEN_W - 120, 300, 100, 80))
    for _press in range(6):  # 60 px to the right with only 20 px of room
        win._on_key(None, Gdk.KEY_Right, 0, SHIFT)
    assert win.sel == _rect(SCREEN_W - 100, 300, 100, 80), f"the selection shrank: {win.sel}"
    for _press in range(200):
        win._on_key(None, Gdk.KEY_Left, 0, SHIFT)
        win._on_key(None, Gdk.KEY_Up, 0, SHIFT)
    assert win.sel == _rect(0, 0, 100, 80), win.sel
    for _press in range(200):
        win._on_key(None, Gdk.KEY_Down, 0, SHIFT)
    assert win.sel == _rect(0, SCREEN_H - 80, 100, 80), win.sel
    for _press in range(20):  # Alt+arrows resize: never below 3 px, never past the screen
        win._on_key(None, Gdk.KEY_Left, 0, ALT | SHIFT)
        win._on_key(None, Gdk.KEY_Down, 0, ALT | SHIFT)
    assert win.sel == _rect(0, SCREEN_H - 80, 3, 80), win.sel
    for _press in range(200):
        win._on_key(None, Gdk.KEY_Right, 0, ALT | SHIFT)
    assert win.sel == _rect(0, SCREEN_H - 80, SCREEN_W, 80), win.sel
    # a drag that ends outside the screen stops at its edge; a tiny one selects nothing
    win.set_selection(None)
    win._on_drag_begin(None, 900, 700)
    win._on_drag_update(None, 500, 500)
    win._on_drag_end(None, 500, 500)
    assert win.sel == _rect(900, 700, SCREEN_W - 900, SCREEN_H - 700), win.sel
    win.set_selection(None)
    win._on_drag_begin(None, 200, 200)
    win._on_drag_update(None, 2, 2)
    win._on_drag_end(None, 2, 2)
    assert win.sel is None and not win.quickbar.get_visible()
    S.cap.run_action("copy", win)  # nothing selected: Enter takes this whole screen
    assert not S.cap.windows
    assert (S.app.history.entries()[0].width, S.app.history.entries()[0].height) == (1024, 768)
    # one pixel is a valid capture
    win = S.overlay()
    win.set_selection(_rect(650, 150, 1, 1))
    S.cap.run_action("copy", win)
    newest = S.app.history.entries()[0]
    assert (newest.width, newest.height) == (1, 1)
    assert Image.open(S.app.history.original_path(newest.id)).getpixel((0, 0)) == PURPLE


def fx_two_screens(S) -> None:
    """Two screens, the second one to the LEFT of the main one (negative coordinates, as
    GDK reports them), both at 150%: one 3072 x 1152 screenshot."""
    from grabbyhands import capture as capture_mod
    from grabbyhands import overlay as overlay_mod
    from grabbyhands.windows import WindowRect

    def wide_screen() -> Image.Image:
        img = Image.new("RGB", (3072, 1152), (245, 245, 250))
        ImageDraw.Draw(img).rectangle((2000, 300, 2099, 399), fill=PURPLE)  # right screen
        return img

    module = sys.modules[__name__]
    real_screen, real_fullscreen = (
        module.make_screen,
        overlay_mod.OverlayWindow.fullscreen_on_monitor,
    )
    real_windows = capture_mod.get_windows
    module.make_screen = wide_screen
    overlay_mod.OverlayWindow.fullscreen_on_monitor = lambda w, _m: w.set_default_size(
        SCREEN_W, SCREEN_H
    )
    S.cap._monitors = lambda: [FakeMonitor(-1024, 0, 1024, 768), FakeMonitor(0, 0, 1024, 768)]
    capture_mod.get_windows = lambda: [
        WindowRect(-900, 100, 400, 300, "Left app"),
        WindowRect(100, 100, 400, 300, "Right app"),
        WindowRect(-200, 200, 600, 300, "Straddler"),
    ]
    try:
        S.cap.capture()
        wait_for(lambda: len(S.cap.windows) == 2, what="one overlay per screen")
        pump(0.3)
        left, right = S.cap.windows
        assert (left.sx, left.sy) == (1.5, 1.5)
        assert left.mon_img == _rect(0, 0, 1536, 1152) and right.mon_img == _rect(
            1536, 0, 1536, 1152
        )
        assert right.to_image(100, 100) == (1686, 150)
        ix, iy = right.to_image(320, 220)
        assert S.cap.screen.pixel(int(ix), int(iy)) == PURPLE  # Identify Color, right screen
        # a drag that leaves its screen stays on it; a new selection clears the other one
        right._on_drag_begin(None, 200, 200)
        right._on_drag_update(None, -500, 100)
        right._on_drag_end(None, -500, 100)
        assert right.sel == _rect(1536, 300, 300, 150), right.sel
        left._on_drag_begin(None, 10, 10)
        left._on_drag_update(None, 100, 100)
        left._on_drag_end(None, 100, 100)
        assert right.sel is None and left.sel == _rect(15, 15, 150, 150), (left.sel, right.sel)
        # the keyboard is on one overlay; a key acts on the screen that has the selection
        right._on_key(None, Gdk.KEY_Right, 0, NO_MODS)
        assert left.sel == _rect(16, 15, 150, 150), left.sel
        # with nothing selected, Space takes the screen the pointer is on
        left.set_selection(None)
        left.pointer, right.pointer = None, (50, 50)
        left._on_key(None, Gdk.KEY_space, 0, NO_MODS)
        assert right.sel == right.mon_img and left.sel is None
        # windows: found on the screen they are on, clipped to the screen that was clicked
        S.cap.set_mode("window")
        left.pointer, right.pointer = (200, 200), (150, 150)
        assert left.hovered_window()[4] == "Left app" and right.hovered_window()[4] == "Right app"
        right.pointer = (50, 250)
        assert right.hovered_window()[4] == "Straddler"
        right._drag_origin = (50, 250)
        right._on_drag_end(None, 0, 0)
        assert right.mode == "select" and right.sel == _rect(1536, 300, 600, 450), right.sel
        # R selects the last area on the screen it was on, whichever overlay got the key
        S.settings.set_value(
            "last-region", GLib.Variant("(iiiiii)", (300, 300, 200, 100, 3072, 1152))
        )
        right._on_key(None, Gdk.KEY_r, 0, NO_MODS)
        assert left.sel == _rect(300, 300, 200, 100) and right.sel is None, (left.sel, right.sel)
        right._on_key(None, Gdk.KEY_Return, 0, NO_MODS)  # and Enter copies it
        assert not S.cap.windows
        newest = S.app.history.entries()[0]
        assert (newest.width, newest.height) == (200, 100)
        # the last area again: flashes on the left screen only
        S.cap.repeat_last()
        wait_for(lambda: S.cap.windows, what="repeat on two screens")
        assert len(S.cap.windows) == 1 and S.cap.windows[0].mon_img.x == 0
        wait_for(lambda: not S.cap.windows, what="repeat to finish")
    finally:
        S.cap.cancel()
        del S.cap._monitors
        module.make_screen = real_screen
        overlay_mod.OverlayWindow.fullscreen_on_monitor = real_fullscreen
        capture_mod.get_windows = real_windows
        S.settings.reset("last-region")


def fx_delay(S) -> None:
    """A countdown is announced where it can be seen (and canceled), is out of the way
    before the picture is taken, and comes back in the mode it was started from."""
    win = S.overlay()
    S.cap.set_mode("color")
    S.notes.clear()
    S.withdrawn.clear()
    calls = S.app.portal.calls
    win._on_key(None, Gdk.KEY_d, 0, NO_MODS)
    assert not S.cap.windows and S.cap._delay_source is not None
    S.cap.cancel_delay()
    assert S.cap._delay_source is None and S.cap._clear_source is None and not S.cap._busy
    S.overlay()
    S.cap.set_mode("color")
    S.notes.clear()
    S.withdrawn.clear()
    calls = S.app.portal.calls
    S.cap.delayed(1)
    note = S.notes[-1]
    assert note.tag == "countdown" and not note.transient, "a quiet notification shows no banner"
    assert note.buttons and note.buttons[0][1] == "app.cancel-delay"
    wait_for(lambda: S.app.portal.calls > calls, 5, "the delayed capture")
    shot = time.monotonic()
    cleared = [when for tag, when in S.withdrawn if tag == "countdown"]
    assert cleared and shot - cleared[0] >= 0.3, "the countdown would be in the picture"
    wait_for(lambda: S.cap.windows, what="the overlay after the countdown")
    assert S.cap.windows[0].mode == "color", "the delay forgot Identify Color"
    S.cap.cancel()


def fx_repeat_last_area(S) -> None:
    def remember(*region) -> None:
        S.settings.set_value("last-region", GLib.Variant("(iiiiii)", region))

    remember(10, 10, 200, 100, SCREEN_W, SCREEN_H)
    count = len(S.app.history.entries())
    S.cap.repeat_last()
    wait_for(lambda: S.cap.windows, what="the flash")
    S.cap.windows[0]._on_key(None, Gdk.KEY_Escape, 0, NO_MODS)  # canceled during the flash
    pump(0.5)
    assert len(S.app.history.entries()) == count, "Esc didn't stop the repeat"
    # an area from a different screen layout is not reused: the normal overlay opens instead
    remember(10, 10, 200, 100, 5120, 1440)
    S.cap.repeat_last()
    wait_for(lambda: S.cap.windows, what="overlay instead of repeat")
    pump(0.4)
    win = S.cap.windows[0]
    assert win.mode == "select" and win.sel is None and len(S.app.history.entries()) == count
    assert "R:" not in win.hint_text()
    assert not win.select_last_region()
    S.cap.cancel()
    S.settings.reset("last-region")


def fx_lost_screenshot_request(S) -> None:
    """The portal crashed while it owed us a screenshot: Print Screen must not stay dead."""
    portal = S.app.portal
    held = []
    portal.screenshot = lambda _interactive, callback: held.append(callback)
    try:
        S.cap.capture()
        S.cap.capture()  # still waiting: pressing again changes nothing...
        assert S.cap._busy and len(held) == 1
        S.cap._shot_started -= 60  # ...but a minute later it is not "still waiting"
        del portal.screenshot
        win = S.overlay()
        late = TMP / "late-answer.png"
        make_screen().save(late)
        held[0](file_uri(late), 0)  # the lost answer turns up after all
        assert S.cap.windows == [win], "a late answer opened a second capture"
        assert not late.exists(), "the portal's file was left behind"
        S.cap.cancel()
    finally:
        portal.__dict__.pop("screenshot", None)


def fx_permission_and_missing_portal(S) -> None:
    from grabbyhands.portals import RESPONSE_OTHER, RESPONSE_UNAVAILABLE

    portal = S.app.portal
    asked = []
    portal.screenshot = lambda _interactive, callback: asked.append(callback)
    try:
        # refused in the background: the permission window; closed before the desktop answers
        S.cap.capture()
        asked.pop()(None, RESPONSE_OTHER)
        window = S.cap._permission
        assert window is not None
        window.button.emit("clicked")
        window.close()
        pump(0.2)
        assert S.cap._permission is None and not S.cap._busy
        late = TMP / "late-permission.png"
        make_screen().save(late)
        asked.pop()(file_uri(late), 0)  # "Allow" is clicked after our window is gone
        pump(0.7)
        assert not late.exists(), "the portal's file was left behind"
        assert not asked and not S.cap.windows and not S.cap._busy, "a capture started by itself"
        # no portal to ask at all: said in so many words, without the permission window
        S.notes.clear()
        S.cap.capture()
        asked.pop()(None, RESPONSE_UNAVAILABLE)
        assert S.cap._permission is None and not S.cap._busy
        assert S.notes[-1].tag == "error" and "ermission" not in S.notes[-1].body, S.notes[-1]
        # canceled by the user: nothing at all
        S.notes.clear()
        S.cap.capture()
        asked.pop()(None, 1)
        assert not S.notes and not S.cap._busy
        # and the whole permission flow: refused, Allow, granted, capture
        S.cap.capture()
        asked.pop()(None, RESPONSE_OTHER)
        window = S.cap._permission
        window.button.emit("clicked")
        asked.pop()(None, RESPONSE_OTHER)  # denied in the desktop's dialog: stays, says so
        assert S.cap._permission is window and window.button.get_sensitive()
        assert "wasn't granted" in window.status.get_description()
        window.button.emit("clicked")
        granted = TMP / "granted.png"
        make_screen().save(granted)
        del portal.screenshot
        asked.pop()(file_uri(granted), 0)
        assert S.cap._permission is None and not granted.exists()
        wait_for(lambda: S.cap.windows, 5, "the capture after permission was given")
        S.cap.cancel()
    finally:
        portal.__dict__.pop("screenshot", None)
        if S.cap._permission is not None:
            S.cap._permission.close()


def fx_no_portal_on_the_bus(S) -> None:
    """The real portal client, on this run's private bus where no portal exists."""
    from grabbyhands import portals

    if "gh-gui-" not in os.environ.get("XDG_RUNTIME_DIR", ""):
        print("       (not on the private bus, skipped)")
        return
    client = portals.PortalClient()
    got = []
    client.screenshot(False, lambda uri, response: got.append((uri, response)))
    wait_for(lambda: got, 10, "an answer without a portal")
    assert got == [(None, portals.RESPONSE_UNAVAILABLE)], got
    assert not client._pending
    shortcuts = portals.GlobalShortcuts(client, lambda _id: None)
    assert client.version(shortcuts.IFACE) == 0 and not shortcuts.available
    done = []
    shortcuts.start([("capture", "Take a screenshot", "Print")], True, lambda *a: done.append(a))
    wait_for(lambda: done, 10, "the shortcuts answer without a portal")
    assert done == [(False, [])] and shortcuts.triggers == {}


def fx_click_while_a_message_shows(S) -> None:
    win = S.overlay()
    S.cap.set_mode("color")
    win.pointer = (650, 150)
    before = len(windows_of(S, "ColorWindow"))
    S.cap.copy_color(win, (1, 2, 3))  # "Copied…" shows for a moment
    win._on_drag_begin(None, 650, 150)
    win._on_drag_end(None, 0, 0)
    pump(0.2)
    assert len(windows_of(S, "ColorWindow")) == before, "a click during the message did something"
    wait_for(lambda: not S.cap.windows, 5, "the capture screen to close")


def fx_editor_callout_and_drags(S) -> None:
    entry, win = open_editor(S)
    doc, canvas = win.doc, win.canvas
    win.set_tool("callout")
    canvas_drag(win, 100, 300, 300, 150)
    assert canvas.editing and [i.type for i in doc.items] == ["callout"]
    canvas._edit[2].get_buffer().set_text("Look here")
    canvas_click(win, 600, 420)  # click away: done typing
    assert not canvas.editing
    assert [i.type for i in doc.items] == ["callout"], "clicking away added another bubble"
    assert doc.items[0].text == "Look here"
    # a tool key pressed in the middle of a drag: the stroke so far is kept, and the new
    # tool doesn't get the second half of somebody else's drag
    win.set_tool("pen")
    gesture = FakeGesture()
    canvas._on_drag_begin(gesture, *canvas.to_screen(50, 50))
    canvas._on_drag_update(gesture, 40, 10)
    win._on_key(None, Gdk.KEY_r, 0, NO_MODS)
    assert canvas.tool.name == "rect" and doc.items[-1].type == "pen" and canvas.preview is None
    count = len(doc.items)
    canvas._on_drag_update(gesture, 80, 60)
    canvas._on_drag_end(gesture, 80, 60)
    assert len(doc.items) == count and canvas.preview is None
    win.close()
    S.app.delete_capture(entry.id, trash_saved=False)


def fx_editor_undo(S) -> None:
    entry, win = open_editor(S)
    doc, canvas = win.doc, win.canvas

    def state() -> str:
        return win._doc_json(doc)

    # dragging a slider is ONE undo step (it used to be one per value, up to the limit of
    # the undo history, which pushed everything else out of it)
    win.set_tool("redact")
    canvas_drag(win, 400, 100, 600, 200)
    box = doc.items[-1]
    assert box.type == "redact" and canvas.selected_item() is box
    before, depth, start = state(), len(canvas.undo._undo), box.strength
    for value in range(1, 61):
        win._set_option("strength", float(value))
    assert doc.get(box.id).strength == 60.0
    assert len(canvas.undo._undo) == depth + 1, len(canvas.undo._undo) - depth
    canvas.do_undo()
    assert doc.get(box.id).strength == start and state() == before
    canvas.do_redo()
    assert doc.get(box.id).strength == 60.0
    # ...and so is holding an arrow key down
    win.set_tool("select")
    canvas.select(doc.get(box.id))
    before, depth = state(), len(canvas.undo._undo)
    for _press in range(30):
        win._on_key(None, Gdk.KEY_Right, 0, NO_MODS)
    assert doc.get(box.id).x0 == 430 and len(canvas.undo._undo) == depth + 1
    canvas.do_undo()
    assert state() == before
    # two different changes stay two steps
    canvas.select(doc.get(box.id))
    depth = len(canvas.undo._undo)
    win._set_option("strength", 10.0)
    win._set_option("redact_mode", "blur")
    assert len(canvas.undo._undo) == depth + 2
    win.close()
    S.app.delete_capture(entry.id, trash_saved=False)

    # every kind of change, undone one by one, gives back exactly the state before it
    entry, win = open_editor(S)
    doc, canvas = win.doc, win.canvas
    states = [state()]

    def did(what: str) -> None:
        assert state() != states[-1], f"{what} changed nothing"
        states.append(state())

    win.set_tool("arrow")
    canvas_drag(win, 20, 20, 200, 120)
    did("drawing an arrow")
    win.set_tool("text")
    canvas_click(win, 300, 300)
    canvas._edit[2].get_buffer().set_text("first line\nsecond line")
    canvas.end_text_edit()
    did("typing a text")
    text = doc.items[-1]
    canvas.begin_text_edit(text, new=False)
    canvas._edit[2].get_buffer().set_text("changed")
    canvas.end_text_edit()
    did("editing a text")
    win.set_tool("select")
    canvas.select(doc.get(text.id))
    win._set_option("color", "#123456")
    did("recoloring")
    arrow = doc.items[0]
    canvas_drag(win, 110, 70, 150, 200)  # move the arrow by its middle
    did("moving")
    canvas.select(doc.get(arrow.id))
    end = doc.get(arrow.id)
    canvas_drag(win, end.x1, end.y1, end.x1 + 60, end.y1 + 10)  # its end handle
    did("resizing")
    win.set_tool("crop")
    canvas_drag(win, 10, 10, 610, 410)
    canvas.tool.apply()
    did("cropping")
    win.set_tool("eraser")
    canvas_drag(win, 0, 0, 300, 300)
    did("erasing")
    win.erase_all()
    did("Erase All")
    assert doc.crop == (10, 10, 600, 400)  # Erase All removes the drawing, not the crop
    for expected in reversed(states[:-1]):
        canvas.do_undo()
        assert state() == expected
    assert not canvas.undo.can_undo
    for expected in states[1:]:
        canvas.do_redo()
        assert state() == expected
    assert not canvas.undo.can_redo
    win.close()
    S.app.delete_capture(entry.id, trash_saved=False)


def fx_editor_typing(S) -> None:
    entry, win = open_editor(S)
    doc, canvas = win.doc, win.canvas
    win.set_tool("text")
    canvas_click(win, 100, 100)
    assert canvas.editing
    buf = canvas._edit[2].get_buffer()
    buf.set_text("first line\nsecond line")
    # Ctrl+C belongs to the window ("copy the picture"); while typing it copies the text
    S.app.copy_text("something else", win)
    buf.select_range(buf.get_start_iter(), buf.get_iter_at_offset(5))
    win.lookup_action("copy").activate(None)
    assert canvas.editing and clipboard_text() == "first", clipboard_text()
    # Undo while typing takes back typing, not the drawing (and doesn't end the typing)
    buf.begin_user_action()
    buf.insert(buf.get_end_iter(), " and more")
    buf.end_user_action()
    canvas.do_undo()
    assert canvas.editing
    assert (
        buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) == "first line\nsecond line"
    )
    canvas.do_redo()
    assert buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).endswith(" and more")
    canvas.do_undo()
    canvas._on_edit_key(None, Gdk.KEY_Escape, 0, NO_MODS)  # Esc: done
    assert not canvas.editing and doc.items[-1].text == "first line\nsecond line"
    one_line = doc.items[-1].bounds()
    assert one_line[3] - one_line[1] > doc.items[-1].size * 2, "the second line is missing"
    # an empty text adds nothing; emptying an existing one removes it (Undo brings it back)
    count = len(doc.items)
    canvas_click(win, 500, 400)
    assert canvas.editing
    canvas.end_text_edit()
    assert len(doc.items) == count
    item = doc.items[-1]
    canvas.begin_text_edit(item, new=False)
    canvas._edit[2].get_buffer().set_text("   ")
    canvas.end_text_edit()
    assert len(doc.items) == count - 1
    canvas.do_undo()
    assert len(doc.items) == count and doc.items[-1].text == "first line\nsecond line"
    # a very long text still draws and saves
    canvas.begin_text_edit(doc.items[-1], new=False)
    canvas._edit[2].get_buffer().set_text("long " * 2000)
    canvas.end_text_edit()
    assert win.render().size == (800, 500)
    win.close()
    S.app.delete_capture(entry.id, trash_saved=False)


def fx_editor_closing(S) -> None:
    app = S.app
    # 1. opening and closing changes nothing on disk, even with a markup file that can't
    #    be read (it used to be deleted on close)
    entry = app.record(make_screen().crop((0, 0, 800, 500)))
    doc_path = app.history.document_path(entry.id)
    cut_short = '{"version": 2, "items": [{"type": "re'
    doc_path.write_text(cut_short)
    saved_before = Path(entry.saved_path).read_bytes()
    app.open_editor(entry)
    pump(0.4)
    win = next(w for w in windows_of(S, "EditorWindow") if w.entry.id == entry.id)
    assert win.doc.empty
    win.close()
    pump(0.3)
    assert doc_path.read_text() == cut_short and Path(entry.saved_path).read_bytes() == saved_before
    app.delete_capture(entry.id, trash_saved=False)

    # 2. deleted from History while it is open: closing brings nothing back
    entry, win = open_editor(S)
    win.set_tool("arrow")
    canvas_drag(win, 20, 20, 200, 120)
    saved = Path(entry.saved_path)
    app.delete_capture(entry.id, trash_saved=False)
    saved.unlink()
    win.close()
    pump(0.3)
    assert not app.history.dir_for(entry.id).exists(), "the deleted capture's folder is back"
    assert not saved.exists(), "the deleted capture's file is back"

    # 3. the saved file can't be replaced: the user is told, and History still gets the markup
    entry, win = open_editor(S)
    thumb_before = app.history.thumb_path(entry.id).read_bytes()
    win.set_tool("rect")
    win._set_option("fill", "solid")
    canvas_drag(win, 0, 0, 800, 500)
    entry.saved_path = "/proc/grabbyhands-smoke/gone.png"
    S.notes.clear()
    win.close()
    pump(0.3)
    assert S.notes and S.notes[-1].tag == "error", S.notes
    kept = app.history.get(entry.id)
    assert kept.edited and app.history.document_path(entry.id).exists()
    assert app.history.thumb_path(entry.id).read_bytes() != thumb_before
    win._set_option("fill", "none")
    app.delete_capture(entry.id, trash_saved=False)

    # 4. quitting with the editor open saves like closing it does
    entry, win = open_editor(S)
    before = Image.open(entry.saved_path).convert("RGB").tobytes()
    win.set_tool("arrow")
    canvas_drag(win, 20, 20, 400, 300)
    app.finish_windows()  # what quitting (Ctrl+Q, --quit, logging out) runs
    assert Image.open(entry.saved_path).convert("RGB").tobytes() != before, "quit lost the edit"
    assert app.history.get(entry.id).edited
    written = Path(entry.saved_path).stat().st_mtime_ns
    win.close()  # and closing afterward doesn't save a second time
    pump(0.3)
    assert Path(entry.saved_path).stat().st_mtime_ns == written
    app.delete_capture(entry.id, trash_saved=False)

    # 4b. killed with the editor open: the markup was kept, but the picture never got it.
    #     Opening and closing the editor again finishes the job.
    entry, win = open_editor(S)
    win.set_tool("arrow")
    canvas_drag(win, 20, 20, 400, 300)
    win._finished = True  # (the process dies here: nothing else is saved)
    if win._save_source is not None:
        GLib.source_remove(win._save_source)
    win.doc.save(app.history.document_path(entry.id))  # what the autosave had written
    win.close()
    pump(0.3)
    assert not app.history.get(entry.id).edited
    before = Image.open(entry.saved_path).convert("RGB").tobytes()
    app.open_editor(entry)
    pump(0.4)
    again = next(w for w in windows_of(S, "EditorWindow") if w.entry.id == entry.id)
    assert len(again.doc.items) == 1
    again.close()
    pump(0.3)
    assert Image.open(entry.saved_path).convert("RGB").tobytes() != before, "still unmarked"
    assert app.history.get(entry.id).edited
    app.delete_capture(entry.id, trash_saved=False)

    # 5. a History delete that is still waiting for its Undo is carried out on quit
    entry = app.record(make_screen().crop((0, 0, 100, 100)))
    app.show_history()
    pump(0.3)
    hist = app._history_window
    hist.delete(entry)
    assert app.history.get(entry.id) is not None
    app.finish_windows()
    assert app.history.get(entry.id) is None, "quitting brought a deleted capture back"
    hist.close()

    # 6. Auto-Redact's answer arrives after the editor was closed: nothing is added behind
    #    the user's back
    if app.supports("ocr"):
        img = Image.new("RGB", (900, 120), "white")
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 30)
        except OSError:
            font = ImageFont.load_default(size=30)
        ImageDraw.Draw(img).text((20, 20), "Contact: jane.doe@example.com", fill="black", font=font)
        entry, win = open_editor(S, img)
        win.auto_redact()
        win.close()
        wait_for(lambda: win.lookup_action("auto-redact").get_enabled(), what="auto-redact")
        pump(1.2)  # (longer than the delayed save of the markup)
        assert not app.history.document_path(entry.id).exists(), "markup the user never saw"
        assert not app.history.get(entry.id).edited
        app.delete_capture(entry.id, trash_saved=False)


def fx_missing_pictures(S) -> None:
    import shutil

    app = S.app
    entry = app.record(make_screen().crop((0, 0, 300, 200)))
    shutil.rmtree(app.history.dir_for(entry.id))  # its pictures vanished behind our back
    editors, pins = len(windows_of(S, "EditorWindow")), len(windows_of(S, "PinWindow"))
    S.notes.clear()
    app.open_editor(entry)
    pump(0.3)
    assert len(windows_of(S, "EditorWindow")) == editors
    assert S.notes and S.notes[-1].tag == "error", "nothing told the user why"
    target = GLib.Variant("s", entry.id)
    S.notes.clear()
    app.activate_action("pin-capture", target)
    pump(0.2)
    assert len(windows_of(S, "PinWindow")) == pins and S.notes[-1].tag == "error"
    app.show_history()
    pump(0.3)
    hist = app._history_window
    app.copy_text("still this", hist)
    hist._copy_by_id(entry.id)
    hist._save_by_id(entry.id)
    assert clipboard_text() == "still this" and hist.get_visible_dialog() is None
    hist.close()
    # notification buttons of a capture that was deleted since: nothing happens, nothing breaks
    app.delete_capture(entry.id, trash_saved=False)
    for action in ("edit-capture", "show-capture", "pin-capture"):
        app.activate_action(action, target)
    pump(0.2)
    assert not app._show_capture_by_id("no-such-capture")
    assert len(windows_of(S, "EditorWindow")) == editors
    # a markup file that can't be read any more: what History hands out is the saved,
    # marked-up picture, not the bare original with everything visible again
    entry, win = open_editor(S)
    win.set_tool("redact")
    win._set_option("redact_mode", "solid")
    canvas_drag(win, 0, 0, 800, 500)
    win.close()
    pump(0.3)
    win._set_option("redact_mode", "pixelate")
    assert app.history.get(entry.id).edited
    assert Image.open(entry.saved_path).convert("RGB").getpixel((400, 250)) == (0, 0, 0)
    app.history.document_path(entry.id).write_text("{ not json")
    shared = app.render_entry(app.history.get(entry.id))
    assert shared.getpixel((400, 250)) == (0, 0, 0), "a damaged markup file un-hid the capture"
    app.delete_capture(entry.id, trash_saved=False)
    S.settings.set_string("editor-redact-mode", "pixelate")


def fx_first_run_shortcut_order(S) -> None:
    """On the first run GNOME's shortcut dialog must not open on top of Getting Started,
    and must come after the Print Screen hand-over, not before and again after."""
    from grabbyhands import application

    app, portal = S.app, S.app.portal
    real_wait = application.FIRST_RUN_WAIT_MS
    application.FIRST_RUN_WAIT_MS = 200
    portal.version = lambda _iface: 1  # this desktop has global shortcuts for apps

    def session(first_run: bool) -> None:
        app.shortcuts, app._shortcuts_asked, app._first_run = None, False, first_run
        portal.requests.clear()
        app._startup_shortcuts()

    def asked() -> list[str]:
        return [method for method, _callback in portal.requests]

    app.show_history()
    pump(0.3)
    hist = app._history_window
    try:
        session(first_run=False)  # every later session: asked right away
        assert asked() == ["CreateSession"]
        app._portal_restarted()  # the desktop's portal was restarted: a new session
        assert asked() == ["CreateSession", "CreateSession"]
        session(first_run=True)  # first run in the background (no window): after a moment
        assert asked() == [] and app.shortcut_state() == "unset"
        wait_for(lambda: asked() == ["CreateSession"], 5, "the background start to ask")
        session(first_run=True)  # first run with Getting Started open: not while it is read
        app.show_welcome(hist)
        guide = app._welcome
        pump(0.6)
        assert asked() == [], "the desktop's dialog would cover Getting Started"
        assert guide.setup_button is not None
        guide.setup_button.emit("clicked")  # its button asks...
        assert asked() == ["CreateSession"]
        close_dialog(guide, hist)
        pump(0.9)
        assert asked() == ["CreateSession"], "closing the guide asked a second time"
        session(first_run=True)  # ...or closing it does
        app.show_welcome(hist)
        guide = app._welcome
        pump(0.6)
        assert asked() == []
        close_dialog(guide, hist)
        wait_for(lambda: asked() == ["CreateSession"], 5, "closing the guide to ask")
        session(first_run=True)  # but never in the middle of a capture started from it
        app.show_welcome(hist)
        guide = app._welcome
        pump(0.6)
        guide.capture_button.emit("clicked")
        wait_for(lambda: S.cap.windows, what="capture from Getting Started")
        if app._welcome is not None:  # (libadwaita 1.5 without frames: see close_dialog)
            close_dialog(guide, hist)
        pump(1.0)
        assert asked() == [], "the desktop's dialog would open over the capture screen"
        S.cap.cancel()
        wait_for(lambda: asked() == ["CreateSession"], 5, "asking once the capture is over")
    finally:
        application.FIRST_RUN_WAIT_MS = real_wait
        del portal.version
        if app._history_window is not None:
            app._history_window.close()
        app.shortcuts, app._shortcuts_asked, app._first_run = None, False, False
        portal.requests.clear()
        app._startup_shortcuts()  # back to "this desktop has none"
        assert app.shortcut_state() == "unavailable"


def fx_command_line(S) -> None:
    app, cap = S.app, S.cap
    calls: list = []
    names = ("capture", "repeat_last", "pick_color", "capture_text")
    for name in names:
        setattr(cap, name, lambda *a, _n=name, **k: calls.append((_n, a, k)))

    class Line:
        def __init__(self, **options) -> None:
            self.options = options

        def get_options_dict(self) -> GLib.VariantDict:
            found = GLib.VariantDict.new(None)
            for key, value in self.options.items():
                variant = GLib.Variant("b", True) if value is True else GLib.Variant("i", value)
                found.insert_value(key.replace("_", "-"), variant)
            return found

    def run(**options) -> list:
        calls.clear()
        assert app.do_command_line(Line(**options)) == 0
        return list(calls)

    try:
        assert run(capture=True) == [("capture", (), {"delay": 0})]
        assert run(capture=True, delay=3) == [("capture", (), {"delay": 3})]
        assert run(window=True, delay=2) == [("capture", (), {"delay": 2, "mode": "window"})]
        assert run(repeat=True) == [("repeat_last", (), {})]
        assert run(pick_color=True, delay=4) == [("pick_color", (4,), {})]  # was: no delay
        assert run(ocr=True, delay=4) == [("capture_text", (4,), {})]
        assert run(ocr=True) == [("capture_text", (0,), {})]
        assert run(delay=5) == [("capture", (), {"delay": 5})]
        assert run(capture=True, delay=-3) == [("capture", (), {"delay": 0})]
        assert run(background=True) == []
        assert not windows_of(S, "HistoryWindow")  # a background start opens nothing
        assert run(history=True) == [] and app._history_window is not None
        assert run(preferences=True) == []
        pump(0.4)
        assert app._preferences is not None
        close_dialog(app._preferences, app._history_window)
        app._history_window.close()
        version = GLib.VariantDict.new(None)
        assert app.do_handle_local_options(version) == -1
        version.insert_value("version", GLib.Variant("b", True))
        assert app.do_handle_local_options(version) == 0
    finally:
        for name in names:
            delattr(cap, name)


def fx_desktop_integration(S) -> None:
    app = S.app
    # the launcher is started over D-Bus (DBusActivatable): each of its actions must be an
    # action of the application, or choosing it from the dock does nothing
    desktop = (ROOT / "data" / "io.github.al3phnul.GrabbyHands.desktop.in").read_text()
    keyfile = GLib.KeyFile()
    keyfile.load_from_data(desktop, len(desktop.encode()), GLib.KeyFileFlags.NONE)
    assert keyfile.get_boolean("Desktop Entry", "DBusActivatable")
    actions = [a for a in keyfile.get_string("Desktop Entry", "Actions").split(";") if a]
    assert actions == ["capture", "history", "preferences"], actions
    for action in actions:
        assert app.lookup_action(action) is not None, action
        option = keyfile.get_string(f"Desktop Action {action}", "Exec").split()[-1]
        assert option == f"--{action}"  # the same thing without D-Bus activation
    service = (ROOT / "data" / "io.github.al3phnul.GrabbyHands.service.in").read_text()
    assert "Name=@APP_ID@" in service and "--gapplication-service" in service
    assert app.get_flags() & Gio.ApplicationFlags.HANDLES_COMMAND_LINE
    # links from a QR code: only web addresses are ever opened
    opened: list = []

    class Launcher:
        def launch(self, *_args) -> None:
            pass

    real_new = Gtk.UriLauncher.new
    Gtk.UriLauncher.new = lambda uri: (opened.append(uri), Launcher())[1]
    try:
        for target in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "ssh://example.com",
            " https://example.com",
            "smb://example.com/share",
            "",
        ):
            app._open_url(target)
            app.activate_action("open-url", GLib.Variant("s", target))
        assert not opened, opened
        app._open_url("HTTPS://example.com/a?b=c")
        app.activate_action("open-url", GLib.Variant("s", "http://example.com"))
        assert opened == ["HTTPS://example.com/a?b=c", "http://example.com"]
    finally:
        Gtk.UriLauncher.new = real_new
    # several codes at once: all are copied, and only a web address gets an "Open Link"
    code = types.SimpleNamespace
    buttons: list = []
    real_send = app.send_notification
    app.send_notification = lambda tag, note: buttons.append(note)
    try:
        app.notify_codes(
            [
                code(text="WIFI:S:example;;", kind="QR code"),
                code(text="12345", kind="EAN-13 barcode"),
            ]
        )
        assert app._last_text == "WIFI:S:example;;\n12345"
    finally:
        app.send_notification = real_send
    # History: Undo really brings a deleted capture back, and it stays
    entry = app.record(make_screen().crop((0, 0, 120, 90)))
    app.show_history()
    pump(0.3)
    hist = app._history_window
    count = hist.model.get_n_items()
    hist.delete(entry)
    assert hist.model.get_n_items() == count - 1
    toast = hist._pending[entry.id]
    toast.emit("button-clicked")  # Undo
    assert hist.model.get_n_items() == count and entry.id not in hist._pending
    toast.emit("dismissed")  # the toast going away afterward deletes nothing
    assert app.history.get(entry.id) is not None and app.history.original_path(entry.id).exists()
    hist.close()
    app.delete_capture(entry.id, trash_saved=False)
    # numbered steps continue from the highest number in the picture
    entry, win = open_editor(S)
    win.set_tool("step")
    for x in (100, 200, 300):
        canvas_click(win, x, 100)
    numbers = lambda: [i.number for i in win.doc.items if i.type == "step"]  # noqa: E731
    assert numbers() == [1, 2, 3]
    win.canvas.do_undo()
    canvas_click(win, 300, 200)
    assert numbers() == [1, 2, 3], "an undone step's number was skipped"
    win.set_tool("select")
    win.canvas.select(win.doc.items[-1])
    win.canvas.delete_selected()
    win.set_tool("step")
    canvas_click(win, 400, 100)
    assert numbers() == [1, 2, 3]
    win.close()
    app.delete_capture(entry.id, trash_saved=False)


def fx_signals_quit_properly(S) -> None:
    """Logging out ends the app with SIGTERM. It must quit the normal way (which lets open
    windows save, see the editor steps), not just die."""
    import signal

    if not S.app._signals_handled:
        print("       (this GLib can't watch signals from Python; skipped)")
        return
    quits: list = []
    S.app.quit = lambda: quits.append(True)
    try:
        for number in (signal.SIGTERM, signal.SIGHUP):
            quits.clear()
            os.kill(os.getpid(), number)
            wait_for(lambda: quits, 5, f"signal {number} to quit the app")
    finally:
        del S.app.quit


def fx_save_as_names(S) -> None:
    from grabbyhands.export_dialog import SaveAsDialog

    class Chooser:
        def __init__(self, location) -> None:
            self.location = location

        def save_finish(self, _result) -> Gio.File:
            if self.location is None:
                raise GLib.Error("dismissed")
            if isinstance(self.location, Path):
                return Gio.File.new_for_path(str(self.location))
            return Gio.File.new_for_uri(self.location)

    S.settings.set_string("save-as-format", "jpg")
    S.settings.set_int("save-as-scale", 100)
    dialog = SaveAsDialog(S.app, make_screen().crop((0, 0, 200, 100)))
    closed: list = []
    dialog.close = lambda: closed.append(True)  # (it isn't on a window here)
    folder = TMP / "save-as"
    folder.mkdir()
    S.notes.clear()
    dialog._chosen(Chooser(folder / "Report 11.2026"), None)
    assert closed, "the dialog stayed open after saving"
    assert [p.name for p in folder.iterdir()] == ["Report 11.2026.jpg"]
    assert Image.open(folder / "Report 11.2026.jpg").format == "JPEG"
    assert [n.tag for n in S.notes] == ["saved"]
    dialog._chosen(Chooser(folder / "Report 11.2026"), None)  # again: never over the first
    assert sorted(p.name for p in folder.iterdir()) == [
        "Report 11.2026-2.jpg",
        "Report 11.2026.jpg",
    ]
    dialog._chosen(Chooser(folder / "as.typed.PNG"), None)  # a picture extension wins
    assert Image.open(folder / "as.typed.PNG").format == "PNG"
    S.notes.clear()
    closed.clear()
    dialog._chosen(Chooser(None), None)  # the file chooser was dismissed
    assert not S.notes and not closed
    dialog._chosen(Chooser("sftp://files.example.com/shot.png"), None)  # not a local folder
    assert [n.tag for n in S.notes] == ["error"] and not closed  # stays, to choose again
    S.settings.reset("save-as-format")


def fx_autostart_entry(S) -> None:
    from grabbyhands import APP_ID

    entry = Path(GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"
    assert str(entry).startswith(os.environ["XDG_CONFIG_HOME"]), entry
    S.app._host_autostart(True)
    text = entry.read_text()
    keyfile = GLib.KeyFile()
    keyfile.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
    command = keyfile.get_string("Desktop Entry", "Exec")
    _ok, argv = GLib.shell_parse_argv(command.replace("%%", "%"))
    assert len(argv) == 2 and Path(argv[0]).is_absolute() and argv[1] == "--background", argv
    assert keyfile.get_string("Desktop Entry", "Type") == "Application"
    assert keyfile.get_boolean("Desktop Entry", "X-GNOME-Autostart-enabled")
    assert keyfile.get_boolean("Desktop Entry", "NoDisplay")  # never a second launcher icon
    S.app._host_autostart(False)
    assert not entry.exists()
    S.app._host_autostart(False)  # switching it off twice is fine


def fx_notification_icon(S) -> None:
    from grabbyhands import config

    entry = S.app.record(make_screen().crop((0, 0, 300, 200)))
    assert isinstance(S.app._thumbnail_icon(entry), Gio.FileIcon)
    real = config.in_flatpak
    config.in_flatpak = lambda: True
    try:
        # the notification portal takes picture data, not files (old ones drop the lot)
        icon = S.app._thumbnail_icon(entry)
        assert isinstance(icon, Gio.BytesIcon) and icon.get_bytes().get_size() > 100
        S.app.history.thumb_path(entry.id).unlink()
        assert isinstance(S.app._thumbnail_icon(entry), Gio.ThemedIcon)
    finally:
        config.in_flatpak = real
    assert isinstance(S.app._thumbnail_icon(entry), Gio.ThemedIcon)
    S.app.delete_capture(entry.id, trash_saved=False)


def fx_flatpak_wording(S) -> None:
    """Inside a Flatpak nothing can be installed with a package manager, and a chosen
    folder has a stand-in path: Settings must not say or show either."""
    from grabbyhands import config, preferences

    if not S.app.supports("ocr"):
        print("       (tesseract missing, skipped)")
        return
    S.app.show_history()
    pump(0.3)
    hist = S.app._history_window
    dialog = preferences.show(S.app, hist)
    pump(0.3)
    assert "tesseract-ocr-" in dialog.more_languages_row.get_subtitle()
    close_dialog(dialog, hist)
    real_in_flatpak, real_xattr = config.in_flatpak, os.getxattr
    good = S.settings.get_string("save-folder")
    config.in_flatpak = lambda: True
    try:
        # (a user id nobody has: nothing under the real /run/user is ever touched)
        stand_in = "/run/user/4294900000/doc/1a2b3c4d/Screenshots"
        S.settings.set_string("save-folder", stand_in)
        os.getxattr = lambda path, name: b"/media/example/Screenshots\0"
        dialog = preferences.show(S.app, hist)
        pump(0.3)
        advice = dialog.more_languages_row.get_subtitle()
        assert "package manager" not in advice and "Flatpak" in advice, advice
        assert "/run/user" not in dialog.folder_row.get_subtitle()
        assert "/media/example/Screenshots" in dialog.folder_row.get_subtitle()
        close_dialog(dialog, hist)
    finally:
        config.in_flatpak, os.getxattr = real_in_flatpak, real_xattr
        S.settings.set_string("save-folder", good)
        hist.close()


def fx_dialog_close_is_noticed(S) -> None:
    """libadwaita 1.5.0 (Ubuntu 24.04) never emits "closed" for a dialog that has a window
    of its own. The app must notice such a dialog closing all the same, or Settings and
    Getting Started could only be opened once."""
    from grabbyhands.dialogs import when_closed

    app = S.app
    for early in (True, False):  # asked before present(), and after it
        dialog = Adw.Dialog(title="Test", child=Gtk.Label(label="Test"))
        # what 1.5.0 does: nobody ever hears "closed"
        dialog.connect("closed", lambda d: d.stop_emission_by_name("closed"))
        heard: list[int] = []
        if early:
            when_closed(dialog, lambda heard=heard: heard.append(1))
        dialog.present(None)
        pump(0.3)
        if not early:
            when_closed(dialog, lambda heard=heard: heard.append(1))
        assert dialog.get_root().get_child() == dialog, "expected a window of its own"
        assert heard == []
        dialog.close()
        pump(0.3)
        assert heard == [1], (early, heard)
    # The app's own dialogs, over a plain window (what the capture screen is).
    plain = Gtk.Window()
    plain.present()
    pump(0.2)
    try:
        for show, slot in (
            (lambda: app.show_preferences(plain), "_preferences"),
            (lambda: app.show_shortcuts(plain), "_shortcuts_dialog"),
            (lambda: app.show_welcome(plain), "_welcome"),
        ):
            for _again in range(2):  # the second time is the point
                show()
                pump(0.3)
                dialog = getattr(app, slot)
                assert dialog is not None and dialog.get_mapped(), slot
                assert dialog.get_root() is not plain, f"{slot}: expected a window of its own"
                dialog.close()
                pump(0.3)
                assert getattr(app, slot) is None, f"{slot} still counts as open"
                assert not dialog.get_mapped(), slot
    finally:
        plain.destroy()
        pump(0.2)


FUNCTIONALITY_STEPS = [
    ("setup: notifications are recorded, history folder is private", fx_setup),
    ("keys on Russian, Greek and Arabic keyboard layouts", fx_other_keyboard_layouts),
    ("capture: nudging and dragging at the edges", fx_nudging_at_the_edges),
    ("capture: two screens", fx_two_screens),
    ("capture: countdown is visible, cancelable, keeps its mode", fx_delay),
    ("capture: last area again (cancel, changed screens)", fx_repeat_last_area),
    ("capture: a lost screenshot request", fx_lost_screenshot_request),
    ("capture: permission window, missing portal", fx_permission_and_missing_portal),
    ("portal client without a portal on the bus", fx_no_portal_on_the_bus),
    ("capture: a click while a message shows", fx_click_while_a_message_shows),
    ("editor: callout click-away, tool key mid-drag", fx_editor_callout_and_drags),
    ("editor: undo steps and exact undo/redo", fx_editor_undo),
    ("editor: typing (copy, undo, lines, empty, long)", fx_editor_typing),
    ("editor: closing, quitting, deleted meanwhile", fx_editor_closing),
    ("captures whose pictures or markup are gone", fx_missing_pictures),
    ("first run: when the desktop is asked for shortcuts", fx_first_run_shortcut_order),
    ("command-line options", fx_command_line),
    ("desktop file, links, several codes, History undo, step numbers", fx_desktop_integration),
    ("SIGTERM and SIGHUP quit the normal way", fx_signals_quit_properly),
    ("Save As: names and formats", fx_save_as_names),
    ("autostart entry", fx_autostart_entry),
    ("notification icon (Flatpak)", fx_notification_icon),
    ("Settings wording and folder inside a Flatpak", fx_flatpak_wording),
    ("dialogs with a window of their own: closing is noticed", fx_dialog_close_is_noticed),
]


def main() -> int:
    from grabbyhands import application, config, gnome_keys

    # Exceptions raised inside GTK callbacks never propagate; collect them so they fail the run.
    sys.excepthook = _hook
    # If GTK itself crashes, say where the test was.
    import faulthandler

    faulthandler.enable()

    # The steps describe a normal install. CI also runs them inside the Flatpak sandbox (to
    # test the libraries it bundles), so start from "not in a Flatpak" there too; the step
    # about the Print Screen key switches the Flatpak answers on where it wants them.
    config.in_flatpak = gnome_keys.in_flatpak = lambda: False

    if Gdk.Display.get_default() is None:
        print("no display: is the Broadway daemon running?")
        return 2
    # No browser is attached to Broadway, so frames are rare and animations would never end.
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    patient_dialogs()
    application.PortalClient = FakePortal
    app = application.GrabbyHandsApp("smoke")
    app.register(None)  # runs do_startup without entering a main loop
    settings = config.settings()
    settings.set_string("save-folder", str(TMP / "saved"))
    sent: list[str] = []
    app.send_notification = lambda tag, note: sent.append(tag)
    cap = app.capture

    def overlay():
        cap.capture()
        wait_for(lambda: cap.windows, what="overlay windows")
        pump(0.3)
        return cap.windows[0]

    def step(name, fn) -> None:
        before = len(ERRORS)
        try:
            fn()
        except Exception:  # noqa: BLE001
            ERRORS.append(f"[{name}] " + traceback.format_exc())
        pump(0.2)
        status = "ok" if len(ERRORS) == before else "FAILED"
        print(f"{status:6} {name}", flush=True)
        for err in ERRORS[before:]:  # right away: a later crash must not swallow them
            print(err, file=sys.stderr, flush=True)

    # -- first run --------------------------------------------------------------------
    def fake_shortcuts(state: str) -> None:
        """Pretend the desktop answered about global shortcuts (the harness has no portal)."""
        from grabbyhands.application import SHORTCUT_DEFAULT_KEYS

        app._shortcuts_available = state != "unavailable"
        app.shortcuts.triggers = dict(SHORTCUT_DEFAULT_KEYS) if state == "ready" else {}
        settings.set_boolean("shortcuts-declined", state == "declined")
        app._shortcuts_changed()

    def first_run_welcome():
        assert not settings.get_boolean("welcome-shown")
        app.activate()  # what clicking the app icon does
        pump(0.5)
        hist = app._history_window
        dialog = app._welcome
        assert hist is not None and dialog is not None, "no Getting Started on first launch"
        assert settings.get_boolean("welcome-shown")
        assert hist.stack.get_visible_child_name() == "empty"
        # No GlobalShortcuts portal here: the guide offers commands for hand-made shortcuts.
        assert app.shortcut_state() == "unavailable"
        assert dialog.setup_button is None
        assert dialog.command_rows["capture"].command == "grabbyhands --capture"
        assert dialog.command_rows["capture-text"].command == "grabbyhands --ocr"
        dialog._copy(dialog.command_rows["capture"].command)
        assert clipboard_text() == "grabbyhands --capture"
        assert "Press" not in hist.empty.get_description(), "promised a key that isn't set up"
        assert "<tt>" not in hist.capture_button.get_tooltip_markup()  # no key to show yet
        # optional features that are off are mentioned (window capture needs the helper)
        assert dialog.extras_row is not None
        assert "Window Helper" in dialog.extras_row.get_subtitle()
        # The desktop offers shortcuts but hasn't bound them yet: one button sets them up.
        fake_shortcuts("unset")
        assert app.shortcut_state() == "unset" and dialog.setup_button is not None
        assert "capture" not in dialog.command_rows
        fake_shortcuts("declined")
        assert app.shortcut_state() == "declined"
        dialog.setup_button.emit("clicked")
        assert not settings.get_boolean("shortcuts-declined")
        # Bound: nothing left to set up, and History names the real key.
        fake_shortcuts("ready")
        assert app.shortcut_state() == "ready" and dialog.setup_button is None
        assert "Print Screen" in hist.empty.get_description()
        assert "Print Screen" in hist.capture_button.get_tooltip_markup()
        dialog.close()
        pump(0.4)
        assert app._welcome is None
        app.activate()  # second launch: no guide by itself
        pump(0.4)
        assert app._welcome is None, "Getting Started must only open by itself once"
        # ...but it stays one click away, and its big button really takes a screenshot.
        app.activate_action("welcome", None)
        pump(0.4)
        assert app._welcome is not None
        app._welcome.capture_button.emit("clicked")
        wait_for(lambda: cap.windows, what="capture from Getting Started")
        assert app._history_window is None, "History would be in the screenshot"
        cap.cancel()
        fake_shortcuts("unavailable")

    def print_screen_handover():
        from grabbyhands import gnome_keys, welcome

        if os.environ.get("GSETTINGS_BACKEND") != "memory":
            print("       (not on the private in-memory settings, skipped)")
            return
        saved_desktop = os.environ.get("XDG_CURRENT_DESKTOP")
        real_in_flatpak = gnome_keys.in_flatpak
        app.show_history()
        pump(0.3)
        hist = app._history_window

        def guide():
            if app._welcome is not None:
                app._welcome.close()
                pump(0.3)
            app.show_welcome(hist)
            pump(0.3)
            return app._welcome

        try:
            # Not GNOME: nothing about GNOME's key is shown at all.
            os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
            assert gnome_keys.status() == gnome_keys.OTHER
            assert not guide().print_group.get_visible()
            # A GNOME-based desktop without GNOME Shell's keybindings (no such tool there).
            os.environ["XDG_CURRENT_DESKTOP"] = "Budgie:GNOME"
            real_settings, gnome_keys._settings = gnome_keys._settings, lambda: None
            assert gnome_keys.status() == gnome_keys.OTHER
            gnome_keys._settings = real_settings
            # GNOME inside Flatpak: host settings are out of reach, so commands to copy.
            os.environ["XDG_CURRENT_DESKTOP"] = "ubuntu:GNOME"
            gnome_keys.in_flatpak = lambda: True
            assert gnome_keys.status() == gnome_keys.UNKNOWN and not gnome_keys.can_change()
            dialog = guide()
            assert dialog.print_group.get_visible() and dialog.print_button is None
            assert dialog.command_rows["print"].command == gnome_keys.command()
            assert dialog.command_rows["print-undo"].command == gnome_keys.undo_command()
            assert gnome_keys.release_print() is None  # and it really doesn't write
            gnome_keys.in_flatpak = real_in_flatpak
            assert welcome.shortcut_command("capture") == "grabbyhands --capture"
            # GNOME with its settings reachable: the real hand-over, on in-memory settings.
            if gnome_keys._settings() is None:
                print("       (GNOME's keybinding schema isn't installed; hand-over skipped)")
                return
            gnome = Gio.Settings.new(gnome_keys.SCHEMA)
            gnome.set_strv(gnome_keys.KEY, ["Print", "<Super><Shift>s"])
            assert gnome_keys.status() == gnome_keys.HELD
            dialog = guide()
            assert dialog.print_button is not None and dialog.undo_button is None
            dialog.print_button.emit("clicked")  # nothing changes before this click
            assert gnome.get_strv(gnome_keys.KEY) == ["<Ctrl>Print", "<Super><Shift>s"]
            assert gnome_keys.status() == gnome_keys.FREE
            assert dialog.print_button is None and dialog.undo_button is not None
            dialog.undo_button.emit("clicked")
            assert gnome.get_strv(gnome_keys.KEY) == ["Print", "<Super><Shift>s"]
            assert dialog.print_button is not None
            dialog.print_button.emit("clicked")
            dialog = guide()  # later, in a new dialog, it can still be given back
            assert dialog.reset_button is not None and dialog.print_button is None
            dialog.reset_button.emit("clicked")
            assert gnome.get_user_value(gnome_keys.KEY) is None
            assert gnome_keys.describe(["<Ctrl>Print"]) == "Ctrl+Print"
        finally:
            gnome_keys.in_flatpak = real_in_flatpak
            if gnome_keys._settings() is not None:
                Gio.Settings.new(gnome_keys.SCHEMA).reset(gnome_keys.KEY)
            if saved_desktop is None:
                os.environ.pop("XDG_CURRENT_DESKTOP", None)
            else:
                os.environ["XDG_CURRENT_DESKTOP"] = saved_desktop
            if app._welcome is not None:
                app._welcome.close()
            hist.close()
            pump(0.3)

    def shortcuts_list():
        from grabbyhands import quickbar, shortcuts_dialog
        from grabbyhands.editor.window import TOOLS

        fake_shortcuts("ready")
        app.show_history()
        pump(0.3)
        app.activate_action("keyboard-shortcuts", None)
        pump(0.4)
        dialog = app._shortcuts_dialog
        assert dialog is not None and len(dialog.groups) == 8, dialog
        sections = {title: rows for title, _text, rows in shortcuts_dialog.sections(app)}
        assert ("Print Screen", "Take a screenshot") in sections["Anywhere"]
        # The list is built from the tables the features use, so it can't promise a dead key.
        for action, _icon, _label, key, _what in quickbar.actions():
            mapped = quickbar.KEYS[(key.split("+")[-1].lower(), key.startswith("Ctrl+"))]
            assert mapped == action, (key, mapped)
            if app.supports(action):  # Text/Redact/QR need their optional helpers
                listed = (key, quickbar.action_name(action))
                assert listed in sections["With an Area Selected"], action
        tool_keys = [key for key, _label in sections["Editor Tools"]]
        assert tool_keys == [t[3] for t in TOOLS] and len(set(tool_keys)) == len(tool_keys)
        dialog.close()
        pump(0.3)
        assert app._shortcuts_dialog is None
        app._history_window.close()
        # Every single key the list names for the capture screen is handled there.
        win = overlay()
        for key, _what in shortcuts_dialog.capture_keys(has_windows=True):
            if len(key) == 1 and key not in "DHC":  # those three end the capture
                assert win._on_key(None, Gdk.keyval_from_name(key.lower()), 0, NONE), key
        app.show_shortcuts()  # never on top of the frozen capture screen
        assert app._shortcuts_dialog is None
        cap.cancel()
        fake_shortcuts("unavailable")

    def overlay_hints_and_keys():
        from grabbyhands import capture as capture_mod
        from grabbyhands import overlay as overlay_mod

        # Broadway can't make a window full-screen; give the overlay its real size instead,
        # because where the hint may sit depends on it.
        real_fullscreen = overlay_mod.OverlayWindow.fullscreen_on_monitor
        overlay_mod.OverlayWindow.fullscreen_on_monitor = lambda w, _m: w.set_default_size(
            SCREEN_W, SCREEN_H
        )
        try:
            hints_and_keys(capture_mod)
        finally:
            overlay_mod.OverlayWindow.fullscreen_on_monitor = real_fullscreen
            capture_mod.get_windows = lambda: []

    def hints_and_keys(capture_mod):
        from grabbyhands.windows import WindowRect

        settings.reset("last-region")
        capture_mod.get_windows = lambda: []
        win = overlay()
        assert win.get_height() == SCREEN_H, win.get_height()
        hint = win.hint_text()
        assert "W:" not in hint and "R:" not in hint, hint  # neither key would do anything
        assert win.btn_region.has_css_class("gh-active")
        cap.set_mode("color")
        assert win.btn_color.has_css_class("gh-active")
        assert not win.btn_region.has_css_class("gh-active")
        assert "HEX" in win.hint_text() and win.hint.get_visible()
        win._on_key(None, Gdk.KEY_space, 0, NONE)  # Space leaves Identify Color for an area
        assert win.mode == "select" and win.sel is not None and win.sel.w == SCREEN_W
        assert not win.hint.get_visible(), "hint would sit on a whole-screen selection"
        win.set_selection(_rect(100, 100, 300, 200))
        pump(0.2)
        assert win.hint.get_visible() and "Enter: Copy" in win.hint_text(), win.hint_text()
        # The action bar stays under the selection while it is nudged. (It used to jump to
        # the top of the screen on every second adjustment.)
        below = win.quickbar.get_margin_top()
        assert below == 100 + 200 + 12, below
        for _nudge in range(3):
            win._on_key(None, Gdk.KEY_Right, 0, NONE)
            assert win.quickbar.get_margin_top() == below, win.quickbar.get_margin_top()
        assert win.sel.x == 103
        win.set_selection(_rect(100, 400, 300, 360))  # reaches the bottom: no room
        assert not win.hint.get_visible()
        win.set_selection(_rect(100, 100, 300, 200))
        win.pointer = (650, 150)
        sent.clear()
        win._on_key(None, Gdk.KEY_c, 0, NONE)  # with an area selected, C copies the picture
        assert not cap.windows and "capture" in sent, sent
        assert "image/png" in clipboard_formats(), clipboard_formats()
        # The last area and the Window Helper are now there: their keys appear and work.
        capture_mod.get_windows = lambda: [WindowRect(100, 100, 400, 300, "Files")]
        win = overlay()
        hint = win.hint_text()
        assert "W: window" in hint and "R: last area" in hint, hint
        cap.set_mode("window")
        win._on_key(None, Gdk.KEY_r, 0, NONE)
        assert win.mode == "select" and (win.sel.w, win.sel.h) == (300, 200), win.sel
        cap.cancel()

    def delay_menu_and_cancel():
        settings.set_int("delay-seconds", 7)
        win = overlay()
        assert sorted(win.delay_buttons) == [3, 5, 7, 10], sorted(win.delay_buttons)
        assert "D key" in win.delay_buttons[7].get_label()
        assert "D key" not in win.delay_buttons[5].get_label()
        win.delay_popover.popup()
        pump(0.3)
        if win.delay_popover.get_visible():
            win._on_key(None, Gdk.KEY_Escape, 0, NONE)  # Esc closes the little menu first
            assert cap.windows, "Esc closed the whole capture instead of the Delay menu"
            pump(0.3)
        win._on_key(None, Gdk.KEY_Escape, 0, NONE)
        assert not cap.windows
        settings.reset("delay-seconds")
        # A countdown can be called off (the notification's Cancel button).
        sent.clear()
        cap.capture(delay=30)
        assert cap._busy and "countdown" in sent
        calls = app.portal.calls
        app.activate_action("cancel-delay", None)
        assert not cap._busy and cap._delay_source is None
        pump(0.3)
        assert app.portal.calls == calls and not cap.windows
        cap.cancel_delay()  # harmless when nothing is counting down
        win = overlay()
        win._on_key(None, Gdk.KEY_d, 0, NONE)  # the D key starts the default countdown
        assert not cap.windows and cap._delay_source is not None
        cap.cancel_delay()

    def save_problems_are_explained():
        from grabbyhands.export_dialog import SaveAsDialog

        image = make_screen().crop((0, 0, 200, 100))
        good = settings.get_string("save-folder")
        settings.set_string("save-folder", "/proc/grabbyhands-smoke/no-such-place")
        try:
            assert not config.folder_writable(config.save_folder())  # and it didn't raise
            sent.clear()
            entry = app.record(image)
            assert entry.saved_path is None and sent == ["error"], sent
            # no file, so nothing to show: never GrabbyHands' private data folder instead
            assert app.saved_file(entry) is None and not app._show_capture_by_id(entry.id)
            win = overlay()
            win.set_selection(_rect(10, 10, 200, 100))
            sent.clear()
            cap.run_action("save", win)
            assert sent == ["error"], f"a failed save must not also say it was saved: {sent}"
            from grabbyhands import preferences

            dialog = preferences.show(app, None)
            pump(0.3)
            assert dialog.folder_warning.get_visible()
            dialog._set_folder(good)
            assert not dialog.folder_warning.get_visible()
            dialog.close()
        finally:
            settings.set_string("save-folder", good)
        save_as = SaveAsDialog(app, image)
        sent.clear()
        assert save_as.write(Path("/proc/grabbyhands-smoke/x.png")) is None
        assert sent == ["error"], sent
        app.activate_action("settings-page", GLib.Variant("s", "saving"))
        pump(0.3)
        assert app._preferences is not None
        assert app._preferences.get_visible_page_name() == "saving"
        app._preferences.close()
        pump(0.3)
        assert app._preferences is None

    # -- capture & copy -----------------------------------------------------------
    def copy_region():
        win = overlay()
        win.pointer = (300, 300)
        render(win)  # crosshair + loupe
        win.set_selection(_rect(10, 10, 300, 120))
        render(win)  # selection, handles, size label
        win._dragging, win._drag_kind = True, "se"
        render(win)  # loupe while resizing
        win._dragging, win._drag_kind = False, None
        cap.run_action("copy", win)
        assert not cap.windows, "overlay should close after copy"
        assert "image/png" in clipboard_formats(), clipboard_formats()
        assert len(app.history.entries()) == 1
        assert "capture" in sent

    def whole_screen():
        win = overlay()
        win.select_full()
        pump(0.2)
        render(win)
        assert win.sel is not None and win.sel.w == SCREEN_W, win.sel
        assert win.quickbar.get_visible(), "quick bar hidden for whole screen"
        cap.run_action("save", win)
        saved = list((TMP / "saved").glob("*.png"))
        assert saved, "nothing saved"
        newest = app.history.entries()[0]
        assert app.saved_file(newest) is not None
        os.rename(newest.saved_path, newest.saved_path + ".moved")  # the user moved the file
        assert app.saved_file(newest) is None
        os.rename(newest.saved_path + ".moved", newest.saved_path)

    def color_pick():
        win = overlay()
        cap.set_mode("color")
        win.pointer = (650, 150)
        render(win)  # three-line HEX/RGB/CMYK loupe
        win.pointer = (SCREEN_W - 5, SCREEN_H - 5)
        render(win)  # loupe flipped near the corner
        cap.show_color(cap.screen.pixel(650, 150))
        pump(0.3)
        assert not cap.windows
        color_win = next(w for w in app.get_windows() if type(w).__name__ == "ColorWindow")
        import cairo

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 120)
        color_win._draw_swatch(None, cairo.Context(surf), 300, 120)
        color_win.copy_buttons["CMYK"].emit("clicked")
        assert clipboard_text() == "cmyk(51%, 58%, 0%, 0%)", clipboard_text()
        color_win.copy_buttons["HEX"].emit("clicked")
        assert clipboard_text() == "#7c6cff"
        color_win.close()

    def quick_color_key():
        win = overlay()
        win.pointer = (650, 150)
        cap.copy_color(win, cap.screen.pixel(650, 150))
        assert clipboard_text() == "#7c6cff"
        # the capture screen says what was copied for a moment, then closes by itself
        assert win.busy and win._busy_label.get_text() == "Copied #7c6cff"
        assert not win._spinner.get_visible(), "a result isn't work in progress"
        wait_for(lambda: not cap.windows, 5, "the capture screen to close after copying a color")
        # a new capture started in that moment is left alone
        win = overlay()
        cap.copy_color(win, (1, 2, 3))
        cap.cancel()
        second = overlay()
        pump(0.9)
        assert cap.windows == [second], "the old timer closed a newer capture"
        cap.cancel()

    def ocr_text():
        if not app.supports("ocr"):
            print("       (tesseract missing, skipped)")
            return
        win = overlay()
        win.set_selection(_rect(20, 40, 700, 80))
        cap.run_action("ocr", win)
        wait_for(lambda: not cap.windows, what="OCR to finish")
        picker_win = next(w for w in app.get_windows() if type(w).__name__ == "TextWindow")
        picker = picker_win.picker
        assert picker is not None and len(picker.words) >= 3, picker
        import cairo

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 300)
        picker._draw(picker, cairo.Context(surf), 800, 300)  # sets the layout too
        assert not picker_win.copy_button.get_sensitive()  # nothing selected yet
        words = picker.words
        smoke = next(i for i, w in enumerate(words) if w.text.lower() == "smoke")
        sx, sy = picker.to_screen(words[smoke].left + 2, words[smoke].top + 2)
        picker._on_click(None, 1, sx, sy)  # click one word
        assert picker_win.selected_text().lower() == "smoke"
        picker_win.copy_selected()
        assert clipboard_text().lower() == "smoke", clipboard_text()
        picker._on_begin(None, sx, sy)  # drag from "smoke" to the end of the line
        last = words[-1]
        ex, ey = picker.to_screen(last.left + last.width - 1, last.top + 2)
        picker._on_update(None, ex - sx, ey - sy)
        assert picker_win.selected_text().lower() == "smoke test", picker_win.selected_text()
        picker_win.copy_all()
        assert "grabby hands smoke test" in clipboard_text().lower()
        picker_win.stack.set_visible_child_name("text")
        assert not picker_win.on_image
        picker_win.close()

    def repeat_region():
        cap.repeat_last()
        wait_for(lambda: not cap.windows and cap.screen is not None, what="repeat")
        pump(0.5)
        assert "image/png" in clipboard_formats()

    def window_mode():
        from grabbyhands import capture as capture_mod
        from grabbyhands.windows import WindowRect

        fake = [WindowRect(100, 100, 400, 300, "Files"), WindowRect(0, 0, 900, 700, "Firefox")]
        capture_mod.get_windows = lambda: list(fake)
        cap.window_capture()
        wait_for(lambda: cap.windows, what="window-mode overlay")
        win = cap.windows[0]
        assert win.mode == "window", win.mode
        assert win.btn_window.get_parent() is not None
        geo = win.geo
        win.pointer = (geo.x + 150 - geo.x, 150)
        render(win)  # highlight + app chip
        x, y, w, h, app = win.hovered_window()
        assert (w, h, app) == (400, 300, "Files"), (w, h, app)
        win.pointer = (geo.width - 2, geo.height - 2)
        assert win.hovered_window()[4] == "Whole screen"
        win._drag_origin = (150, 150)
        win._on_drag_end(None, 0, 0)  # click
        assert win.mode == "select" and win.sel is not None
        assert win.sel.w == round(400 * win.sx), win.sel
        assert cap._source == "window"
        render(win)
        cap.run_action("copy", win)
        assert not cap.windows

    def window_mode_without_helper():
        from grabbyhands import capture as capture_mod

        capture_mod.get_windows = lambda: []
        sent.clear()
        cap.window_capture()
        wait_for(lambda: cap.windows, what="fallback overlay")
        win = cap.windows[0]
        assert win.mode == "select", win.mode
        assert win.btn_window.get_parent() is None
        assert "window-helper" in sent, sent
        cap.cancel()

    def pin_window():
        app.pin(make_screen().crop((0, 0, 400, 300)))
        pump(0.3)
        pin = next(w for w in app.get_windows() if type(w).__name__ == "PinWindow")
        pin._set_zoom(1.5)
        pump(0.2)
        pin.close()

    def history_window():
        app.show_history()
        pump(0.5)
        hist = app._history_window
        n = hist.model.get_n_items()
        assert n >= 3, n
        first = hist.model.get_item(0).entry
        hist.delete(first)
        pump(0.2)
        assert hist.model.get_n_items() == n - 1
        assert app.history.get(first.id) is not None, "deleted before Undo timed out"
        hist._commit_pending()
        assert app.history.get(first.id) is None
        hist._copy_by_id(hist.model.get_item(0).entry.id)
        assert "image/png" in clipboard_formats()
        app.copy_text("not a picture", hist)
        hist.selection.set_selected(0)
        ctrl = Gdk.ModifierType.CONTROL_MASK
        assert hist._on_key(None, Gdk.KEY_c, 0, ctrl)  # Ctrl+C copies the selected capture
        assert "image/png" in clipboard_formats(), clipboard_formats()
        assert app.clear_history(trash_saved=False) >= 2
        pump(0.3)
        assert hist.model.get_n_items() == 0
        assert hist.stack.get_visible_child_name() == "empty"
        hist.close()

    def permission_window():
        from grabbyhands.permission import PermissionWindow

        win = PermissionWindow(app, lambda uri: None, lambda: None)
        win.present()
        pump(0.3)
        win.button.emit("clicked")
        pump(0.3)

    # -- editor ---------------------------------------------------------------------
    NONE = Gdk.ModifierType(0)
    ed: dict = {}

    def draw_canvas(win) -> None:
        import cairo

        canvas = win.canvas
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 900, 700)
        canvas._draw(canvas.area, cairo.Context(surf), 900, 700)

    def gesture(win, x0, y0, x1, y1) -> None:
        tool = win.canvas.tool
        tool.press(x0, y0, NONE, 1)
        tool.drag((x0 + x1) / 2, (y0 + y1) / 2, NONE)
        draw_canvas(win)  # live preview
        tool.drag(x1, y1, NONE)
        tool.release(x1, y1, NONE)
        draw_canvas(win)

    def type_text(win, text) -> None:
        _item, _new, view = win.canvas._edit
        view.get_buffer().set_text(text)
        draw_canvas(win)
        win.canvas.end_text_edit()

    def editor_open():
        settings.set_boolean("auto-save", True)
        entry = app.record(make_screen().crop((0, 0, 800, 500)))
        assert entry.saved_path
        ed["entry"] = entry
        ed["before"] = Image.open(entry.saved_path).convert("RGB").getpixel((400, 250))
        assert app.supports("edit")
        app.open_editor(entry)
        pump(0.4)
        win = next(w for w in app.get_windows() if type(w).__name__ == "EditorWindow")
        ed["win"] = win
        assert win.canvas.tool.name == "arrow"
        # keys go to the picture from the start, not to a button (it used to be Auto-Redact)
        assert win.get_focus() is win.canvas.area, win.get_focus()
        assert win.tool_tip.get_visible() and "Shift" in win.tool_tip.get_label()
        draw_canvas(win)

    def editor_tools_never_squeezed():
        win = ed["win"]
        grid = win.rail_grid
        assert not isinstance(grid.get_parent(), Gtk.Viewport | Gtk.ScrolledWindow)
        win.set_default_size(700, 120)  # try to make it far too short
        pump(0.8)
        ok, bounds = grid.compute_bounds(grid)
        _m, need_w, _a, _b = grid.measure(Gtk.Orientation.HORIZONTAL, -1)
        _m, need_h, _a, _b = grid.measure(Gtk.Orientation.VERTICAL, -1)
        assert ok and bounds.get_width() >= need_w and bounds.get_height() >= need_h, (
            bounds.get_width(),
            bounds.get_height(),
            need_w,
            need_h,
        )
        win.set_default_size(1000, 700)
        pump(0.3)

    def editor_every_tool():
        win = ed["win"]
        doc = win.doc
        plan = [
            ("arrow", (20, 20, 200, 120)),
            ("line", (20, 140, 200, 140)),
            ("rect", (300, 40, 420, 120)),
            ("ellipse", (440, 40, 560, 120)),
            ("pen", (20, 200, 180, 260)),
            ("highlighter", (40, 60, 400, 60)),
            ("redact", (600, 100, 700, 200)),
            ("spotlight", (300, 200, 500, 320)),
            ("magnify", (40, 300, 80, 340)),
            ("sticker", (650, 300, 720, 370)),
        ]
        for tool, coords in plan:
            before = len(doc.items)
            win.set_tool(tool)
            gesture(win, *coords)
            assert len(doc.items) == before + 1, (tool, len(doc.items))
            assert doc.items[-1].type == tool, (tool, doc.items[-1].type)
        win.set_tool("step")
        for x in (100, 160):
            win.canvas.tool.press(x, 420, NONE, 1)
            win.canvas.tool.release(x, 420, NONE)
        assert [i.number for i in doc.items if i.type == "step"] == [1, 2]
        win.set_tool("text")
        win.canvas.tool.press(250, 420, NONE, 1)
        win.canvas.tool.release(250, 420, NONE)
        assert win.canvas.editing
        type_text(win, "Hello there")
        assert doc.items[-1].type == "text" and doc.items[-1].text == "Hello there"
        win.set_tool("callout")
        gesture(win, 500, 450, 650, 380)
        assert win.canvas.editing
        type_text(win, "Look here")
        assert doc.items[-1].type == "callout" and doc.items[-1].text == "Look here"
        for tool in ("magnify", "redact", "spotlight", "eraser", "crop", "sticker"):
            win.set_tool(tool)  # every options bar variant
            draw_canvas(win)
            # instructions show once: in the pill, after the options, or (Blur) as its advice
            assert not (win.tool_hint_how.get_visible() and win.tool_tip.get_visible()), tool
        assert win.auto_redact_option.get_label() == "Auto-Redact"  # one name everywhere
        # an emoji from the chooser shows on its button; the stamps show as pressed
        win._set_sticker("🎉")
        assert win.emoji_button.get_label() == "🎉"
        assert not any(b.get_active() for b in win.stamp_buttons.values())
        win._set_sticker("★")
        assert win.stamp_buttons["★"].get_active() and win.emoji_button.get_label() != "🎉"
        # a number box that has the keyboard keeps digits but lets tool letters through
        win.set_tool("arrow")
        win.set_focus(win.width_spin)
        assert win.get_focus().get_ancestor(Gtk.SpinButton) is win.width_spin
        assert not win._on_key_in_number_box(None, Gdk.KEY_5, 0, NONE)
        assert win._on_key_in_number_box(None, Gdk.KEY_t, 0, NONE)
        assert win.canvas.tool.name == "text" and win.get_focus() is win.canvas.area
        assert not win._on_key_in_number_box(None, Gdk.KEY_l, 0, NONE)  # not in a number box

    def editor_select_and_options():
        win = ed["win"]
        doc = win.doc
        win.set_tool("select")
        rect = next(i for i in doc.items if i.type == "rect")
        win.canvas.tool.press(300, 80, NONE, 1)  # left edge of the outline rect
        assert win.canvas.selected_item() is rect, win.canvas.selected_item()
        win.canvas.tool.drag(320, 90, NONE)
        win.canvas.tool.release(320, 90, NONE)
        assert rect.x0 == 320, rect.x0
        win._set_option("color", "#00ff00")
        assert rect.color == "#00ff00"
        win.canvas.tool.press(rect.x1, rect.y1, NONE, 1)  # se handle
        win.canvas.tool.drag(rect.x1 + 30, rect.y1 + 20, NONE)
        win.canvas.tool.release(rect.x1 + 30, rect.y1 + 20, NONE)
        draw_canvas(win)
        count = len(doc.items)
        win._on_key(None, Gdk.KEY_Delete, 0, NONE)
        assert len(doc.items) == count - 1
        win.canvas.do_undo()
        assert len(doc.items) == count
        win.canvas.do_redo()
        assert len(doc.items) == count - 1
        win.canvas.do_undo()
        win._on_key(None, Gdk.KEY_r, 0, NONE)
        assert win.canvas.tool.name == "rect"
        text = next(i for i in doc.items if i.type == "text")
        win.set_tool("select")
        win.canvas.tool.press(text.x + 5, text.y + 5, NONE, 2)  # double-click edits
        assert win.canvas.editing
        type_text(win, "Edited")
        assert text.text == "Edited"

    def editor_eraser():
        win = ed["win"]
        doc = win.doc
        count = len(doc.items)
        win.set_tool("eraser")
        win.canvas.tool.motion(100, 100)
        gesture(win, 10, 20, 210, 130)
        assert len(doc.items) == count + 1 and doc.items[-1].type == "eraser"
        assert win.canvas.selected_item() is None
        win.erase_all()
        assert doc.items == []
        win.canvas.do_undo()
        assert len(doc.items) == count + 1

    def editor_crop():
        win = ed["win"]
        win.set_tool("crop")
        gesture(win, 10, 10, 610, 410)
        win.canvas.tool.apply()
        assert win.doc.crop == (10, 10, 600, 400), win.doc.crop
        assert win.canvas.tool.name == "select"
        draw_canvas(win)
        assert win.render().size == (600, 400)

    def editor_copy_and_save_as():
        win = ed["win"]
        win.copy()
        assert "image/png" in clipboard_formats()
        # a toast shows a file name as it is, even with characters that mean markup
        shown: list = []
        real_add, win.toasts.add_toast = win.toasts.add_toast, shown.append
        win.toast("Saved R&D <draft>.png")
        win.toasts.add_toast = real_add
        assert shown[0].get_title() == "Saved R&D <draft>.png" and not shown[0].get_use_markup()
        from grabbyhands.export_dialog import SaveAsDialog

        settings.set_int("save-as-scale", 50)
        dialog = SaveAsDialog(app, win.render())
        dialog.present(win)
        pump(0.2)
        sent.clear()
        out = dialog.write(TMP / "saved-as.webp")
        assert sent == ["saved"], sent  # Save As confirms what it wrote
        img = Image.open(out)
        assert img.format == "WEBP" and img.width == round(win.render().width / 2)
        close_dialog(dialog, win)  # (a plain close() can be a no-op on libadwaita 1.5)

    def editor_close_writes_file():
        win = ed["win"]
        entry = ed["entry"]
        win.set_tool("redact")
        win._set_option("redact_mode", "solid")  # the way to hide things is remembered
        win._set_option("strength", 80.0)
        assert win.get_visible_dialog() is None, "a dialog would swallow the close"
        win.close()
        pump(0.3)
        saved = Image.open(entry.saved_path)
        assert saved.size != (800, 500), "file wasn't replaced with the marked-up version"
        assert app.history.get(entry.id).edited
        assert app.history.document_path(entry.id).exists()
        app.open_editor(entry)  # re-open: markup is still editable
        pump(0.3)
        again = next(w for w in app.get_windows() if type(w).__name__ == "EditorWindow")
        assert (again.style_.redact_mode, again.style_.strength) == ("solid", 80.0)
        again.style_.redact_mode, again.style_.strength = "pixelate", 50.0
        assert len(again.doc.items) > 5 and again.doc.crop == (10, 10, 600, 400)
        rendered = app.render_entry(app.history.get(entry.id))
        assert rendered.size == saved.size
        again.close()
        pump(0.2)

    def editor_auto_redact():
        if not app.supports("ocr"):
            print("       (tesseract missing, skipped)")
            return
        img = Image.new("RGB", (900, 220), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 30)
        except OSError:
            font = ImageFont.load_default(size=30)
        draw.text((20, 20), "Contact: jane.doe@example.com", fill="black", font=font)
        draw.text((20, 80), "Phone: (555) 123-4567", fill="black", font=font)
        draw.text((20, 140), "Nothing secret on this line", fill="black", font=font)
        entry = app.record(img)
        app.open_editor(entry)
        pump(0.3)
        win = next(
            w
            for w in app.get_windows()
            if type(w).__name__ == "EditorWindow" and w.entry.id == entry.id
        )
        win.auto_redact()
        wait_for(lambda: win.lookup_action("auto-redact").get_enabled(), what="auto-redact")
        redactions = [i for i in win.doc.items if i.type == "redact"]
        assert len(redactions) == 2, redactions
        win.auto_redact()  # running it again doesn't duplicate boxes
        wait_for(lambda: win.lookup_action("auto-redact").get_enabled(), what="auto-redact 2")
        assert len([i for i in win.doc.items if i.type == "redact"]) == 2
        win.canvas.do_undo()  # one undo removes them all
        assert not [i for i in win.doc.items if i.type == "redact"]
        win.close()
        pump(0.2)

    def preferences_dialog():
        from grabbyhands import preferences

        dialog = preferences.show(app, None)
        pump(0.4)
        # the shortcut rows follow the desktop's answer while Settings is open
        assert dialog.shortcut_labels["capture"].get_label() == "Not set"
        fake_shortcuts("ready")
        assert dialog.shortcut_labels["capture"].get_label() == "Print Screen"
        assert "Print Screen" in dialog.shortcuts_row.get_subtitle()
        fake_shortcuts("unavailable")
        assert dialog.shortcut_labels["capture"].get_label() == "Not set"
        # Getting Started and the key list are one click away from here too
        dialog.guide_row.emit("activated")
        pump(0.3)
        assert app._welcome is not None
        app._welcome.close()
        dialog.all_keys_row.emit("activated")
        pump(0.3)
        assert app._shortcuts_dialog is not None
        app._shortcuts_dialog.close()
        pump(0.3)
        dialog.scheme_buttons["mint"].set_active(True)
        assert settings.get_string("accent-scheme") == "mint"
        dialog.scheme_buttons["violet"].set_active(True)
        dialog.style_buttons["light"].set_active(True)
        assert settings.get_string("app-style") == "light"
        dialog.style_buttons["system"].set_active(True)
        dialog._set_format("jpg")
        assert settings.get_string("export-format") == "jpg"
        assert dialog.quality_row.get_sensitive()
        assert dialog.example_row.get_subtitle().endswith(".jpg")
        dialog.pattern_row.set_text("R&D <%H>")  # markup characters show up as typed
        # What the row really shows (get_subtitle() gives the markup on new libadwaita and
        # the shown text on older ones; text that isn't valid markup leaves the label empty).
        shown = [
            lab.get_text()
            for lab in _labels(dialog.example_row)
            if "subtitle" in lab.get_css_classes()
        ]
        assert shown and shown[0].startswith("R&D <"), shown
        dialog.pattern_row.set_text("shot-%H%M")
        dialog._apply_pattern(dialog.pattern_row)
        assert settings.get_string("filename-pattern") == "shot-%H%M"
        dialog._set_folder(str(TMP / "elsewhere"))
        assert (TMP / "elsewhere").is_dir()
        dialog._set_folder(str(TMP / "saved"))
        settings.reset("filename-pattern")
        settings.set_string("export-format", "png")
        dialog.close()
        # Window Helper off: a button for whichever extensions app exists, else a command
        real_app = preferences.extensions_app
        preferences.extensions_app = lambda: None
        try:
            dialog = preferences.show(app, None)
            pump(0.3)
            assert dialog.extensions_button is None and dialog.helper_command_row is not None
            assert "gnome-extensions enable" in dialog.helper_command_row.get_subtitle()
            dialog.close()
        finally:
            preferences.extensions_app = real_app
        assert "org.gnome.Shell.Extensions.desktop" not in preferences.EXTENSION_APPS
        # The system menu's screenshot button: the switch shows what the Window Helper
        # says, asks it on a click, and explains itself when there is nobody to ask.
        from grabbyhands import windows

        real_state, real_set = windows.menu_button, windows.set_menu_button
        helper = {"state": "off", "asked": [], "obeys": True}

        def fake_set(enabled):
            helper["asked"].append(enabled)
            if helper["obeys"]:
                helper["state"] = "on" if enabled else "off"
            return helper["obeys"]

        windows.menu_button = lambda: helper["state"]
        windows.set_menu_button = fake_set
        try:
            dialog = preferences.show(app, None)
            pump(0.3)
            row = dialog.menu_button_row
            assert row.get_sensitive() and not row.get_active()
            assert helper["asked"] == []  # opening Settings changes nothing
            row.set_active(True)
            assert helper["asked"] == [True] and row.get_active()
            row.set_active(False)
            assert helper["asked"] == [True, False] and not row.get_active()
            helper["obeys"] = False  # the helper can't do it: the switch goes back
            row.set_active(True)
            assert helper["asked"] == [True, False, True] and not row.get_active()
            dialog.close()
            pump(0.2)
            for state, words in (
                ("on", "GrabbyHands"),
                ("no-helper", "Window Helper"),
                ("old-helper", "Log out"),
                ("unavailable", "GNOME"),
            ):
                helper["state"], helper["asked"] = state, []
                dialog = preferences.show(app, None)
                pump(0.3)
                row = dialog.menu_button_row
                assert row.get_active() == (state == "on"), state
                assert row.get_sensitive() == (state == "on"), state
                shown = [lab.get_text() for lab in _labels(row)]
                assert any(words in text for text in shown), (state, shown)
                assert helper["asked"] == [], state
                dialog.close()
                pump(0.2)
        finally:
            windows.menu_button, windows.set_menu_button = real_state, real_set

    def stylesheets_parse_cleanly():
        from grabbyhands import style, theme

        errors = []
        for scheme in theme.schemes():
            for dark in (False, True):
                # Both forms: without CSS variables (GTK 4.14) and with them (4.16+).
                for variables in (False, True) if style.supports_css_variables() else (False,):
                    provider = Gtk.CssProvider()
                    provider.connect(
                        "parsing-error",
                        lambda _p, _s, err, sid=scheme.id, d=dark, v=variables: errors.append(
                            (sid, d, v, err.message)
                        ),
                    )
                    css = style.stylesheet(theme.palette(scheme.id), dark, variables)
                    if not variables:
                        assert ":root" not in css and "var(" not in css and "--" not in css
                    provider.load_from_string(css + style._scheme_buttons_css())
        assert not errors, errors[:5]
        # what the app itself loads follows the running GTK
        assert (":root" in style.stylesheet(theme.palette("violet"))) == (
            style.supports_css_variables()
        )

    def scheme_switching():
        from grabbyhands import theme

        settings.set_string("accent-scheme", "ocean")
        pump(0.2)
        assert theme.current().scheme == "ocean"
        win = overlay()
        win.set_selection(_rect(10, 10, 200, 100))
        render(win)  # the overlay draws with the new colors
        cap.cancel()
        settings.set_string("app-style", "dark")
        pump(0.2)
        assert Adw.StyleManager.get_default().get_dark()
        settings.reset("app-style")
        settings.reset("accent-scheme")
        pump(0.2)
        assert theme.current().scheme == "violet"

    def quick_redact_action():
        if not app.supports("redact"):
            print("       (tesseract missing, skipped)")
            return
        win = overlay()
        assert "redact" in win.quickbar.buttons
        win.set_selection(_rect(0, 0, SCREEN_W, SCREEN_H))
        cap.run_action("redact", win)
        pump(0.3)
        ed_win = next(w for w in app.get_windows() if type(w).__name__ == "EditorWindow")
        wait_for(lambda: ed_win.lookup_action("auto-redact").get_enabled(), what="auto-redact")
        ed_win.close()
        pump(0.2)

    def about_dialog():
        app.show_about()
        pump(0.3)

    # what the functionality steps (above main) work with
    fx = types.SimpleNamespace(
        app=app, cap=cap, settings=settings, sent=sent, overlay=overlay, notes=[], withdrawn=[]
    )

    for name, fn in [
        ("first run: Getting Started", first_run_welcome),
        ("Getting Started: the Print Screen key", print_screen_handover),
        ("keyboard shortcuts list", shortcuts_list),
        ("copy a region", copy_region),
        ("whole screen + save", whole_screen),
        ("color window (HEX/RGB/CMYK)", color_pick),
        ("quick color copy (C key)", quick_color_key),
        ("copy text (OCR)", ocr_text),
        ("repeat last region", repeat_region),
        ("window mode (Window Helper)", window_mode),
        ("window mode without the helper", window_mode_without_helper),
        ("capture: hints and keys match what works", overlay_hints_and_keys),
        ("capture: Delay menu, cancel a countdown", delay_menu_and_cancel),
        ("pin window", pin_window),
        ("history: delete, undo window, clear", history_window),
        ("permission window", permission_window),
        ("save problems are explained", save_problems_are_explained),
        ("editor: open", editor_open),
        ("editor: tools never squeezed", editor_tools_never_squeezed),
        ("editor: every tool", editor_every_tool),
        ("editor: select, options, undo", editor_select_and_options),
        ("editor: eraser + erase all", editor_eraser),
        ("editor: crop", editor_crop),
        ("editor: copy + save as", editor_copy_and_save_as),
        ("editor: close updates the file", editor_close_writes_file),
        ("editor: auto-redact", editor_auto_redact),
        ("preferences", preferences_dialog),
        ("stylesheets parse cleanly", stylesheets_parse_cleanly),
        ("color scheme + dark mode switching", scheme_switching),
        ("quick bar: Redact", quick_redact_action),
        ("about dialog", about_dialog),
        *[(name, lambda fn=fn: fn(fx)) for name, fn in FUNCTIONALITY_STEPS],
    ]:
        step(name, fn)

    print(f"{len(ERRORS)} error(s); waited {DIALOG_WAIT[0]:.1f} s for dialogs")
    return 1 if ERRORS else 0


def _rect(x, y, w, h):
    from grabbyhands.imaging import Rect

    return Rect(x, y, w, h)


if __name__ == "__main__":
    os.environ.setdefault("GRABBYHANDS_DEV", "1")
    sys.exit(main())
