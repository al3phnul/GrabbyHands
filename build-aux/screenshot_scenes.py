# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenes for build-aux/screenshots.py. Runs inside the private Broadway session.

argv: <out dir> <scheme id> <light|dark> <comma-separated scene names or "">

Add a scene by writing a function and decorating it with @scene("name"). Use snap() to save
a widget as <name>.png. All content here is made up (no real data).
"""

from __future__ import annotations

import builtins
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
builtins._ = lambda s: s

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

OUT = Path(sys.argv[1])
SCHEME = sys.argv[2] if len(sys.argv) > 2 else "violet"
DARK = len(sys.argv) > 3 and sys.argv[3] == "dark"
ONLY = {name for name in (sys.argv[4] if len(sys.argv) > 4 else "").split(",") if name}

# Broadway's screen is 1024x768 and windows can't be larger, so every scene fits inside that.
SCREEN_W, SCREEN_H = 1024, 768
WINDOW_BOX = (62, 150, 962, 710)  # the mock app window on the fake desktop (x0, y0, x1, y1)
NONE = Gdk.ModifierType(0)

SCENES: dict[str, object] = {}


def scene(name: str):
    def register(fn):
        SCENES[name] = fn
        return fn

    return register


# -- helpers ---------------------------------------------------------------------------
def pump(seconds: float = 0.3) -> None:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


def settle(widget: Gtk.Widget, frames: int = 2) -> None:
    """Wait until GTK has really restyled and laid out ``widget``'s window.

    Without a browser attached, Broadway's frame clock only ticks about once a second, and
    style changes (a tile becoming highlighted, a page switching) are applied on a tick. A
    picture taken sooner shows the *previous* state. So ask for frames and wait for them.
    """
    root = widget.get_root()
    clock = root.get_frame_clock() if root is not None else None
    if clock is None:
        pump(0.5)
        return
    for _frame in range(frames):
        start = clock.get_frame_counter()
        root.queue_draw()
        clock.request_phase(Gdk.FrameClockPhase.LAYOUT)
        end = time.monotonic() + 3
        while clock.get_frame_counter() == start and time.monotonic() < end:
            pump(0.02)


def snap(widget: Gtk.Widget, name: str, backdrop: bool = False) -> None:
    """Save ``widget`` (a window, dialog or any widget) as <name>.png at its real size.

    The picture is never stretched: what you get is exactly what GTK laid out. Resize the
    window first (set_default_size) if you want a different size. ``backdrop`` paints the
    window background color behind a widget that has none of its own.
    """
    node, w, h = None, 0, 0
    for _attempt in range(12):
        pump(0.2)
        settle(widget)
        w, h = widget.get_width(), widget.get_height()
        if w <= 0 or h <= 0:
            continue
        snapshot = Gtk.Snapshot()
        if backdrop:
            color = Gdk.RGBA()
            color.parse("#222226" if Adw.StyleManager.get_default().get_dark() else "#fafafb")
            snapshot.append_color(color, Graphene.Rect().init(0, 0, w, h))
        Gtk.WidgetPaintable.new(widget).snapshot(snapshot, w, h)
        node = snapshot.to_node()
        if node is not None:
            break
    if node is None:
        raise RuntimeError(f"{name}: nothing was drawn (is the widget visible and laid out?)")
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(Gdk.Display.get_default())
    try:
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
        texture.save_to_png(str(OUT / f"{name}.png"))
    finally:
        renderer.unrealize()
    print(f"saved {name}.png {w}x{h}", flush=True)


def scrolled_content(widget: Gtk.Widget) -> Gtk.Widget | None:
    """The full-height content inside ``widget``'s first scrolled area (for long dialogs:
    snap() of this shows everything, not just the part that fits on Broadway's screen)."""
    if isinstance(widget, Gtk.Viewport):
        return widget.get_child()
    child = widget.get_first_child()
    while child is not None:
        found = scrolled_content(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def mock_app(width: int = 900, height: int = 560) -> Image.Image:
    """A made-up app window: something believable to capture and mark up."""
    img = Image.new("RGB", (width, height), (246, 247, 251))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, width, 56), fill=(40, 44, 64))
    d.text((24, 12), "Acme Dashboard", fill="white", font=font(28, bold=True))
    d.rounded_rectangle((30, 90, 420, 300), 14, fill="white", outline=(220, 222, 232))
    d.text((50, 110), "Revenue this month", fill=(90, 94, 110), font=font(22))
    d.text((50, 150), "$48,210", fill=(20, 24, 40), font=font(28, bold=True))
    d.text((50, 220), "Contact: jane.doe@example.com", fill=(60, 64, 80), font=font(18))
    d.rounded_rectangle((460, 90, 870, 300), 14, fill="white", outline=(220, 222, 232))
    for i, bar in enumerate((60, 110, 80, 150, 130, 170)):
        d.rectangle((490 + i * 60, 280 - bar, 525 + i * 60, 280), fill=(124, 108, 255))
    d.text((40, 340), "Phone: (555) 123-4567", fill=(60, 64, 80), font=font(22))
    d.text((40, 390), "Next review: Thursday at 10:00", fill=(60, 64, 80), font=font(22))
    return img


def mock_desktop() -> Image.Image:
    """A fake screen: wallpaper, top bar and the mock app window."""
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (30, 27, 60))
    d = ImageDraw.Draw(img)
    for y in range(SCREEN_H):  # soft vertical gradient wallpaper
        t = y / SCREEN_H
        d.line(
            (0, y, SCREEN_W, y),
            fill=(int(44 + 60 * t), int(36 + 30 * t), int(92 + 70 * t)),
        )
    d.rectangle((0, 0, SCREEN_W, 30), fill=(18, 18, 24))
    d.text((SCREEN_W // 2 - 40, 6), "Tue 10:42", fill=(230, 230, 235), font=font(14, bold=True))
    x0, y0, x1, y1 = WINDOW_BOX
    d.rounded_rectangle((x0 - 2, y0 + 6, x1 + 2, y1 + 10), 14, fill=(14, 12, 30))  # shadow
    img.paste(mock_app(x1 - x0, y1 - y0), (x0, y0))
    return img


class Session:
    """The app wired to a fake portal and a fake monitor the size of Broadway's screen."""

    def __init__(self) -> None:
        from gui_smoke import FakePortal

        from grabbyhands import application, capture, config, overlay
        from grabbyhands.windows import WindowRect

        tmp = Path(tempfile.mkdtemp(prefix="gh-shots-"))
        self.desktop = mock_desktop()  # what the next capture "sees"; scenes may swap it
        session = self

        class ScenePortal(FakePortal):
            def screenshot(self, interactive: bool, callback) -> None:
                self.calls += 1
                path = tmp / f"portal-{self.calls}.png"
                session.desktop.save(path)
                uri = Gio.File.new_for_path(str(path)).get_uri()
                GLib.idle_add(lambda: (callback(uri, 0), False)[1])

        class FakeMonitor:
            def __init__(self) -> None:
                self.geo = Gdk.Rectangle()
                self.geo.x = self.geo.y = 0
                self.geo.width, self.geo.height = SCREEN_W, SCREEN_H

            def get_geometry(self) -> Gdk.Rectangle:
                return self.geo

        application.PortalClient = ScenePortal
        # Broadway's own monitor is tiny; give the overlay a normal-sized window instead.
        overlay.OverlayWindow.fullscreen_on_monitor = lambda win, _monitor: win.set_default_size(
            SCREEN_W, SCREEN_H
        )
        x0, y0, x1, y1 = WINDOW_BOX
        capture.get_windows = lambda: [WindowRect(x0, y0, x1 - x0, y1 - y0, "Acme Dashboard")]

        self.app = application.GrabbyHandsApp("screenshots")
        self.app.register(None)
        self.app.send_notification = lambda *_args: None
        self.app.capture._monitors = lambda: [FakeMonitor()]
        self.settings = config.settings()
        self.settings.set_string("save-folder", str(tmp / "saved"))
        self.settings.set_string("accent-scheme", SCHEME)
        if DARK:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        # No transitions: a picture must never catch a button half-way through a fade
        # (see settle(): frames are rare here, so a 200 ms fade can take seconds).
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        pump(0.3)

    def window(self, type_name: str) -> Gtk.Window:
        return next(w for w in self.app.get_windows() if type(w).__name__ == type_name)

    def overlay(self):
        cap = self.app.capture
        cap.capture()
        end = time.monotonic() + 10
        while not cap.windows and time.monotonic() < end:
            pump(0.05)
        pump(0.4)
        return cap.windows[0]

    def fake_shortcuts(self, state: str = "ready") -> None:
        """Pretend the desktop answered about global shortcuts (there is no portal here).

        ``ready``: bound to the keys we suggest. ``unset``: available but not bound yet.
        ``unavailable``: the desktop has no global shortcuts for apps.
        """
        from grabbyhands.application import SHORTCUT_DEFAULT_KEYS

        app = self.app
        app._shortcuts_available = state != "unavailable"
        app.shortcuts.triggers = dict(SHORTCUT_DEFAULT_KEYS) if state == "ready" else {}
        app._shortcuts_changed()

    def fake_print_key(self, status: str, can_change: bool = True, customized: bool = False):
        """Pretend GNOME's Print Screen setting is in a given state (never touches settings)."""
        from grabbyhands import gnome_keys

        gnome_keys.status = lambda: status
        gnome_keys.can_change = lambda: can_change
        gnome_keys.is_customized = lambda: customized
        gnome_keys.current = lambda: ["<Ctrl>Print"] if customized else ["Print"]

    def record_capture(self, edited: bool = False):
        entry = self.app.record(mock_app())
        if edited:
            entry.edited = True
            self.app.history.update(entry)
        return entry


# -- scenes ----------------------------------------------------------------------------
@scene("capture-overlay")
def capture_overlay(s: Session) -> None:
    from grabbyhands.imaging import Rect

    win = s.overlay()
    x0, y0, x1, y1 = WINDOW_BOX
    win.set_selection(Rect(x0 + 440, y0 + 70, 450, 250))
    win.pointer = (x0 + 660, y0 + 200)
    snap(win, "capture-overlay")
    snap(win.toolbar, "capture-toolbar")
    snap(win.quickbar, "capture-actionbar")
    s.app.capture.cancel()


@scene("capture-empty")
def capture_empty(s: Session) -> None:
    win = s.overlay()
    win.pointer = (420, 400)  # crosshair + magnifier, before anything is selected
    win.area.queue_draw()
    snap(win, "capture-empty")
    win.delay_popover.popup()
    pump(0.3)
    snap(win.delay_popover, "capture-delay-menu")
    win.delay_popover.popdown()
    s.app.capture.cancel()


@scene("capture-window-mode")
def capture_window_mode(s: Session) -> None:
    win = s.overlay()
    s.app.capture.set_mode("window")
    win.pointer = (500, 400)
    win.area.queue_draw()
    snap(win, "capture-window-mode")
    s.app.capture.cancel()


@scene("identify-color")
def identify_color(s: Session) -> None:
    win = s.overlay()
    s.app.capture.set_mode("color")
    x0, y0, _x1, _y1 = WINDOW_BOX
    win.pointer = (x0 + 505, y0 + 250)  # on one of the violet chart bars
    win.area.queue_draw()
    snap(win, "identify-color")
    s.app.capture.cancel()
    s.app.show_color((124, 108, 255))
    color = s.window("ColorWindow")
    snap(color, "color-window")
    color.close()


@scene("editor")
def editor(s: Session) -> None:
    entry = s.record_capture()
    s.app.open_editor(entry)
    pump(0.5)
    win = s.window("EditorWindow")
    win.set_default_size(1024, 740)
    pump(0.4)

    def drag(tool: str, x0, y0, x1, y1) -> None:
        win.set_tool(tool)
        t = win.canvas.tool
        t.press(x0, y0, NONE, 1)
        t.drag((x0 + x1) / 2, (y0 + y1) / 2, NONE)
        t.drag(x1, y1, NONE)
        t.release(x1, y1, NONE)

    drag("highlighter", 48, 165, 200, 165)
    drag("rect", 440, 80, 880, 310)
    drag("arrow", 520, 420, 450, 318)
    drag("redact", 150, 214, 400, 246)
    win.set_tool("step")
    win.canvas.tool.press(30, 120, NONE, 1)
    win.canvas.tool.release(30, 120, NONE)
    win.set_tool("arrow")
    win.canvas.select(None)
    snap(win, "editor")
    win.set_tool("redact")
    snap(win.options, "editor-options-blur")
    win.set_tool("text")
    snap(win.options, "editor-options-text")
    win.set_tool("sticker")
    snap(win.options, "editor-options-stickers")
    win.set_tool("crop")
    snap(win.options, "editor-options-crop")
    win.set_tool("select")
    win.canvas.select(next(i for i in win.doc.items if i.type == "rect"))
    snap(win, "editor-selected")
    win.close()
    pump(0.3)


@scene("text-picker")
def text_picker(s: Session) -> None:
    from grabbyhands import ocr

    if not ocr.available():
        print("SKIPPED text-picker (tesseract not installed)")
        return
    image = mock_app()
    words = ocr.read_words(image)
    s.app.show_text_picker(image, words)
    win = s.window("TextWindow")
    pump(0.6)
    phone = [i for i, w in enumerate(words) if w.text.startswith("(555)")]
    if phone and win.picker is not None:
        win.picker.select_range(phone[0], min(phone[0] + 1, len(words) - 1))
    snap(win, "text-picker")
    win.close()


@scene("welcome")
def welcome(s: Session) -> None:
    """Getting Started in its main states. Opens by itself on first launch (over History)."""
    from grabbyhands import gnome_keys

    s.app.clear_history(trash_saved=False)
    s.app.show_history()
    pump(0.4)
    parent = s.app._history_window
    parent.set_default_size(980, 740)
    parent.present()
    pump(0.4)

    def shot(name: str) -> None:
        s.app.show_welcome(parent)
        pump(0.4)
        snap(s.app._welcome, name)
        # the whole scrolling page
        snap(scrolled_content(s.app._welcome), f"{name}-all", backdrop=True)
        s.app._welcome.close()
        pump(0.4)

    # a newcomer on stock GNOME: shortcuts not confirmed yet, Print Screen still GNOME's
    s.fake_shortcuts("unset")
    s.fake_print_key(gnome_keys.HELD)
    shot("welcome-first-run")
    # everything in place
    s.fake_shortcuts("ready")
    s.fake_print_key(gnome_keys.FREE, customized=True)
    shot("welcome-ready")
    # Flatpak on GNOME: we can't read or change GNOME's setting, so show the commands
    s.fake_print_key(gnome_keys.UNKNOWN, can_change=False)
    shot("welcome-commands")
    # a desktop without global shortcuts for apps
    s.fake_shortcuts("unavailable")
    s.fake_print_key(gnome_keys.OTHER, can_change=False)
    shot("welcome-other-desktop")
    s.fake_shortcuts("ready")
    s.fake_print_key(gnome_keys.FREE, customized=True)
    parent.close()
    pump(0.3)


@scene("shortcuts")
def shortcuts(s: Session) -> None:
    s.fake_shortcuts("ready")
    s.app.show_history()
    pump(0.4)
    parent = s.app._history_window
    parent.set_default_size(980, 740)
    parent.present()
    pump(0.4)
    s.app.show_shortcuts(parent)
    pump(0.4)
    snap(s.app._shortcuts_dialog, "keyboard-shortcuts")
    snap(scrolled_content(s.app._shortcuts_dialog), "keyboard-shortcuts-all", backdrop=True)
    s.app._shortcuts_dialog.close()
    pump(0.3)
    parent.close()
    pump(0.3)


@scene("history")
def history(s: Session) -> None:
    s.fake_shortcuts("ready")
    s.app.clear_history(trash_saved=False)
    s.app.show_history()
    pump(0.6)
    win = s.app._history_window
    win.set_default_size(980, 620)
    win.present()
    snap(win, "history-empty")
    base = mock_app()
    for i, box in enumerate(
        [
            (0, 0, 900, 560),
            (20, 80, 440, 310),
            (450, 80, 880, 310),
            (0, 0, 900, 120),
            (20, 320, 620, 430),
        ]
    ):
        entry = s.app.record(base.crop(box))
        if i in (1, 3):
            entry.edited = True
            s.app.history.update(entry)
    pump(0.6)
    snap(win, "history")


@scene("settings")
def settings(s: Session) -> None:
    from grabbyhands import preferences

    s.app.show_history()
    pump(0.4)
    parent = s.app._history_window
    parent.set_default_size(980, 740)
    parent.present()
    pump(0.4)
    dialog = preferences.show(s.app, parent)
    for page in ("appearance", "general", "saving", "text"):
        dialog.set_visible_page_name(page)
        snap(dialog, f"settings-{page}")
    dialog.close()
    pump(0.3)
    # The whole General page, as it is with the Window Helper on.
    from grabbyhands import windows

    real = windows.available, windows.menu_button
    windows.available, windows.menu_button = (lambda: True), (lambda: "off")
    try:
        dialog = preferences.show(s.app, parent)
        dialog.set_visible_page_name("general")
        pump(0.3)
        page = dialog.get_visible_page()
        snap(scrolled_content(page) or page, "settings-general-all", backdrop=True)
        dialog.close()
    finally:
        windows.available, windows.menu_button = real
    pump(0.3)


@scene("save-as")
def save_as(s: Session) -> None:
    from grabbyhands.export_dialog import SaveAsDialog

    s.app.show_history()
    pump(0.4)
    parent = s.app._history_window
    dialog = SaveAsDialog(s.app, mock_app())
    dialog.present(parent)
    snap(dialog, "save-as")
    dialog.close()
    pump(0.3)


@scene("permission")
def permission(s: Session) -> None:
    from grabbyhands.permission import PermissionWindow

    win = PermissionWindow(s.app, lambda _uri: None, lambda: None)
    win.present()
    snap(win, "permission")
    win.close()


@scene("pin")
def pin(s: Session) -> None:
    s.app.pin(mock_app().crop((440, 70, 890, 320)))
    win = s.window("PinWindow")
    snap(win, "pin")
    win.close()


# -- documentation set -----------------------------------------------------------------
# The pictures the README and the metainfo use, one scene each (docs/screenshots/<name>.png).
# build-aux/update-screenshots.sh runs exactly these; keep the file names stable.
def _editor_for(s: Session, entry):
    s.app.open_editor(entry)
    pump(0.5)
    win = next(
        w
        for w in s.app.get_windows()
        if type(w).__name__ == "EditorWindow" and w.entry.id == entry.id
    )
    win.set_default_size(1024, 740)
    pump(0.4)
    return win


def _drag(win, tool: str, x0: float, y0: float, x1: float, y1: float) -> None:
    win.set_tool(tool)
    t = win.canvas.tool
    t.press(x0, y0, NONE, 1)
    t.drag((x0 + x1) / 2, (y0 + y1) / 2, NONE)
    t.drag(x1, y1, NONE)
    t.release(x1, y1, NONE)


def _type(win, text: str) -> None:
    """Finish the inline text box the text and callout tools open."""
    if not win.canvas.editing:
        return
    _item, _new, view = win.canvas._edit
    view.get_buffer().set_text(text)
    win.canvas.end_text_edit()


@scene("docs-capture")
def docs_capture(s: Session) -> None:
    from grabbyhands.imaging import Rect

    win = s.overlay()
    x0, y0, _x1, _y1 = WINDOW_BOX
    win.set_selection(Rect(x0 + 440, y0 + 70, 450, 250))
    win.pointer = (x0 + 660, y0 + 200)
    snap(win, "capture")
    s.app.capture.cancel()


@scene("docs-window-capture")
def docs_window_capture(s: Session) -> None:
    win = s.overlay()
    s.app.capture.set_mode("window")
    win.pointer = (500, 400)
    win.area.queue_draw()
    snap(win, "window-capture")
    s.app.capture.cancel()


@scene("docs-identify-color")
def docs_identify_color(s: Session) -> None:
    win = s.overlay()
    s.app.capture.set_mode("color")
    x0, y0, _x1, _y1 = WINDOW_BOX
    win.pointer = (x0 + 505, y0 + 250)
    win.area.queue_draw()
    snap(win, "identify-color")
    s.app.capture.cancel()
    s.app.show_color((124, 108, 255))
    color = s.window("ColorWindow")
    snap(color, "color-window")
    color.close()


@scene("docs-editor")
def docs_editor(s: Session) -> None:
    win = _editor_for(s, s.record_capture())

    def step(x: float, y: float) -> None:
        win.canvas.select(None)  # or the click would grab a handle of the last shape
        win.set_tool("step")
        win.canvas.tool.press(x, y, NONE, 1)
        win.canvas.tool.release(x, y, NONE)

    step(30, 120)
    _drag(win, "highlighter", 48, 165, 200, 165)
    _drag(win, "redact", 150, 214, 400, 246)
    _drag(win, "rect", 440, 80, 880, 310)
    step(440, 80)
    _drag(win, "arrow", 590, 400, 700, 322)
    win.set_tool("text")
    win.canvas.tool.press(470, 404, NONE, 1)
    win.canvas.tool.release(470, 404, NONE)
    _type(win, "Best month so far")
    win.set_tool("arrow")
    win.canvas.select(None)
    win.canvas.area.queue_draw()
    snap(win, "editor")
    win.close()
    pump(0.3)


@scene("docs-auto-redact")
def docs_auto_redact(s: Session) -> None:
    from grabbyhands import ocr

    if not ocr.available():
        print("SKIPPED docs-auto-redact (tesseract not installed)")
        return
    win = _editor_for(s, s.record_capture())
    win.auto_redact()
    action = win.lookup_action("auto-redact")
    end = time.monotonic() + 60
    pump(0.3)
    while not action.get_enabled() and time.monotonic() < end:
        pump(0.1)
    win.set_tool("select")
    win.canvas.select(None)
    pump(0.5)  # the "Hid 1 email and 1 phone number" message stays in the picture
    snap(win, "auto-redact")
    win.close()
    pump(0.3)


@scene("docs-copy-text")
def docs_copy_text(s: Session) -> None:
    from grabbyhands import ocr

    if not ocr.available():
        print("SKIPPED docs-copy-text (tesseract not installed)")
        return
    image = mock_app()
    words = ocr.read_words(image)
    s.app.show_text_picker(image, words)
    win = s.window("TextWindow")
    pump(0.6)
    phone = [i for i, w in enumerate(words) if w.text.startswith("(555)")]
    if phone and win.picker is not None:
        win.picker.select_range(phone[0], min(phone[0] + 1, len(words) - 1))
    snap(win, "copy-text")
    win.close()


@scene("docs-history")
def docs_history(s: Session) -> None:
    s.app.clear_history(trash_saved=False)
    base = mock_app()
    boxes = [
        (0, 0, 900, 560),
        (20, 80, 440, 310),
        (450, 80, 880, 310),
        (0, 0, 900, 120),
        (20, 320, 620, 430),
        (450, 80, 880, 560),
    ]
    for i, box in enumerate(boxes):
        entry = s.app.record(base.crop(box))
        if i in (1, 3):
            entry.edited = True
            s.app.history.update(entry)
    s.app.show_history()
    pump(0.6)
    win = s.app._history_window
    win.set_default_size(980, 620)
    win.present()
    pump(0.6)
    snap(win, "history")
    win.close()
    pump(0.3)


@scene("docs-settings")
def docs_settings(s: Session) -> None:
    from types import SimpleNamespace

    from grabbyhands import preferences, windows

    s.app.show_history()
    pump(0.4)
    parent = s.app._history_window
    parent.set_default_size(980, 740)
    parent.present()
    pump(0.4)
    # Show a machine that is fully set up: shortcuts bound, Window Helper on.
    real_shortcuts, real_available = s.app.shortcuts, windows.available
    if hasattr(s, "fake_shortcuts"):
        s.fake_shortcuts("ready")
    else:
        s.app.shortcuts = SimpleNamespace(
            session_handle=None,
            triggers={
                "capture": "Print",
                "repeat-region": "Ctrl+Shift+Print",
                "color-picker": "Super+Shift+C",
                "capture-text": "Super+Shift+T",
            },
        )
    windows.available = lambda: True
    try:
        dialog = preferences.show(s.app, parent)
        dialog.set_visible_page_name("appearance")
        snap(dialog, "settings-appearance")
        dialog.set_visible_page_name("general")
        dialog.shortcuts_row.set_expanded(True)
        snap(dialog, "settings-general")
        dialog.set_visible_page_name("saving")
        # The real path contains the user's name; show the short form instead.
        dialog.folder_row.set_subtitle("~/Pictures/GrabbyHands")
        snap(dialog, "settings-saving")
        dialog.close()
    finally:
        s.app.shortcuts, windows.available = real_shortcuts, real_available
    parent.close()
    pump(0.3)


@scene("docs-getting-started")
def docs_getting_started(s: Session) -> None:
    """The first-run guide as a newcomer on stock GNOME sees it, whole page."""
    # Getting Started and its helpers arrive with the first-run guide; without them there
    # is nothing to picture (plain name lookups, so this file loads either way).
    whole_page = globals().get("scrolled_content")
    if not hasattr(s.app, "show_welcome") or whole_page is None:
        print("note: docs-getting-started left out (this build has no Getting Started window)")
        return
    from grabbyhands import gnome_keys

    saved = {
        name: getattr(gnome_keys, name)
        for name in ("status", "can_change", "is_customized", "current")
    }
    s.app.show_history()
    pump(0.4)
    parent = s.app._history_window
    parent.set_default_size(980, 740)
    parent.present()
    pump(0.4)
    try:
        s.fake_shortcuts("unset")
        s.fake_print_key(gnome_keys.HELD)
        s.app.show_welcome(parent)
        pump(0.4)
        snap(whole_page(s.app._welcome), "getting-started", backdrop=True)
        s.app._welcome.close()
        pump(0.4)
    finally:
        for name, function in saved.items():
            setattr(gnome_keys, name, function)
        s.fake_shortcuts("ready")
    parent.close()
    pump(0.3)


@scene("docs-allow-screenshots")
def docs_allow_screenshots(s: Session) -> None:
    from grabbyhands.permission import PermissionWindow

    win = PermissionWindow(s.app, lambda _uri: None, lambda: None)
    win.present()
    snap(win, "allow-screenshots")
    win.close()


@scene("docs-pin")
def docs_pin(s: Session) -> None:
    s.app.pin(mock_app().crop((440, 70, 890, 320)))
    win = s.window("PinWindow")
    snap(win, "pin")
    win.close()


def mock_page(width: int, height: int, dark: bool) -> Image.Image:
    """A made-up document window that is almost all white (or almost all black): the two
    hardest backgrounds for anything that floats on top of a screenshot."""
    paper, ink, soft = ((24, 24, 27), (236, 236, 240), (150, 150, 158))
    if not dark:
        paper, ink, soft = ((255, 255, 255), (28, 28, 32), (110, 110, 118))
    img = Image.new("RGB", (width, height), paper)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, width, 44), fill=(36, 36, 40) if dark else (246, 245, 244))
    d.text((20, 10), "Acme Notes", fill=ink, font=font(20, bold=True))
    d.text((40, 80), "Quarterly plan", fill=ink, font=font(30, bold=True))
    lines = (
        "Ship the new onboarding before the end of the month.",
        "Ask jane.doe@example.com for the final numbers.",
        "Book the meeting room for Thursday at 10:00.",
        "Send the summary to everyone at Acme.",
    )
    for i, line in enumerate(lines):
        d.text((40, 150 + i * 44), line, fill=soft, font=font(20))
    return img


def mock_plain_desktop(dark: bool) -> Image.Image:
    """A fake screen that is nearly all white or nearly all black (see mock_page)."""
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (8, 8, 10) if dark else (244, 244, 246))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, SCREEN_W, 30), fill=(0, 0, 0) if dark else (255, 255, 255))
    clock = (230, 230, 235) if dark else (40, 40, 46)
    d.text((SCREEN_W // 2 - 40, 6), "Tue 10:42", fill=clock, font=font(14, bold=True))
    x0, y0, x1, y1 = WINDOW_BOX
    d.rectangle((x0 - 1, y0 - 1, x1, y1), outline=(48, 48, 54) if dark else (214, 214, 220))
    img.paste(mock_page(x1 - x0, y1 - y0, dark), (x0, y0))
    return img


@scene("capture-extremes")
def capture_extremes(s: Session) -> None:
    """The capture screen over a nearly white and a nearly black screen: everything that
    floats (bars, selection, size label, magnifier, window label, hint) must hold up."""
    from grabbyhands.imaging import Rect

    x0, y0, _x1, _y1 = WINDOW_BOX
    try:
        for kind in ("light", "dark"):
            s.desktop = mock_plain_desktop(kind == "dark")
            win = s.overlay()
            win.set_selection(Rect(x0 + 30, y0 + 60, 560, 150))
            win.pointer = (x0 + 300, y0 + 130)
            snap(win, f"capture-on-{kind}")
            win.set_selection(None)
            win.pointer = (x0 + 250, y0 + 100)  # on the heading's lettering
            win.area.queue_draw()
            snap(win, f"capture-on-{kind}-empty")
            s.app.capture.set_mode("color")
            win.pointer = (x0 + 640, y0 + 330)  # plain paper
            win.area.queue_draw()
            snap(win, f"capture-on-{kind}-color")
            s.app.capture.set_mode("window")
            win.pointer = (500, 400)
            win.area.queue_draw()
            snap(win, f"capture-on-{kind}-window")
            s.app.capture.cancel()
    finally:
        s.desktop = mock_desktop()


@scene("capture-messages")
def capture_messages(s: Session) -> None:
    """What the capture screen says in the middle: a result, and work in progress."""
    win = s.overlay()
    s.app.capture.set_mode("color")
    win.show_message("Copied #7C6CFF")
    snap(win, "capture-message")
    win.show_busy("Reading text…")
    snap(win, "capture-busy")
    win.hide_busy()
    s.app.capture.cancel()


@scene("states")
def states(s: Session) -> None:
    """Hover, pressed, keyboard-focus and selected looks, plus the editor's crop frame and
    text box. States are set directly on the widgets (nothing is clicked)."""
    hover, pressed = Gtk.StateFlags.PRELIGHT, Gtk.StateFlags.ACTIVE

    # the capture bars
    win = s.overlay()
    win.btn_window.set_state_flags(hover, False)
    win.btn_color.set_state_flags(pressed, False)
    snap(win, "states-capture")
    s.app.capture.cancel()

    # the editor: sidebar tiles, a focused Auto-Redact, then crop and typing
    entry = s.record_capture()
    s.app.open_editor(entry)
    pump(0.5)
    win = s.window("EditorWindow")
    win.set_default_size(1024, 740)
    pump(0.4)
    win.tool_buttons["line"].set_state_flags(hover, False)
    win.tool_buttons["rect"].set_state_flags(pressed, False)
    win.swatches["#22c55e"].set_state_flags(hover, False)
    win.set_focus_visible(True)
    win.auto_redact_btn.grab_focus()
    snap(win, "states-editor")
    win.set_focus(win.canvas.area)
    win.set_tool("crop")
    tool = win.canvas.tool
    tool.press(440, 80, NONE, 1)
    tool.drag(880, 310, NONE)
    tool.release(880, 310, NONE)
    snap(win, "editor-crop")
    win.set_tool("text")
    win.canvas.tool.press(60, 470, NONE, 1)
    win.canvas.tool.release(60, 470, NONE)
    pump(0.3)
    if win.canvas._edit is not None:
        win.canvas._edit[2].get_buffer().set_text("Check this number")
    snap(win, "editor-text-edit")
    win.canvas.end_text_edit()
    for tool_id in ("eraser", "spotlight", "magnify", "rect"):  # the other option sets
        win.set_tool(tool_id)
        snap(win.options, f"editor-options-{tool_id}")
    win.close()
    pump(0.3)

    # History: one card selected, one under the pointer, keyboard focus on Settings
    s.app.clear_history(trash_saved=False)
    for box in ((0, 0, 900, 560), (20, 80, 440, 310), (450, 80, 880, 310)):
        s.app.record(mock_app().crop(box))
    s.app.show_history()
    pump(0.6)
    win = s.app._history_window
    win.set_default_size(980, 520)
    win.present()
    pump(0.4)
    win.selection.set_selected(1)
    cell = win.grid.get_first_child()
    if cell is not None:
        cell.set_state_flags(hover, False)
    win.set_focus_visible(True)
    snap(win, "states-history")

    # Settings: keyboard focus on a color scheme that is not the chosen one
    from grabbyhands import preferences

    win.set_default_size(980, 740)
    pump(0.3)
    dialog = preferences.show(s.app, win)
    dialog.set_visible_page_name("appearance")
    pump(0.3)
    dialog.scheme_buttons["mint"].grab_focus()
    win.set_focus_visible(True)
    snap(dialog, "states-settings")
    dialog.close()
    pump(0.3)
    win.close()
    pump(0.3)

    # Copy Text with nothing picked: the primary button switched off
    from grabbyhands import ocr

    if ocr.available():
        image = mock_app()
        s.app.show_text_picker(image, ocr.read_words(image))
        win = s.window("TextWindow")
        pump(0.6)
        snap(win, "states-copy-text")
        win.close()
        pump(0.3)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if Gdk.Display.get_default() is None:
        print("FAILED: no display (run this through build-aux/screenshots.py)")
        return 2
    unknown = ONLY - set(SCENES)
    if unknown:
        print(f"FAILED: unknown scene(s) {sorted(unknown)}; available: {sorted(SCENES)}")
        return 2
    session = Session()
    failed = 0
    for name, fn in SCENES.items():
        if ONLY and name not in ONLY:
            continue
        try:
            fn(session)
        except Exception:  # noqa: BLE001 - report and carry on with the other scenes
            failed += 1
            print(f"FAILED {name}\n{traceback.format_exc()}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
