# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Capture flow: screenshot portal → overlay → quick action."""

from __future__ import annotations

import logging
import os
import threading
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Gdk, Gio, GLib

from grabbyhands import config
from grabbyhands.colors import format_color
from grabbyhands.i18n import ngettext
from grabbyhands.imaging import Rect, ScreenImage
from grabbyhands.overlay import OverlayWindow, ScreenMapping
from grabbyhands.portals import RESPONSE_CANCELLED, RESPONSE_OTHER, RESPONSE_UNAVAILABLE
from grabbyhands.windows import WindowRect, get_stage_size, get_windows, to_layout, window_at

if TYPE_CHECKING:
    from PIL import Image

    from grabbyhands.application import GrabbyHandsApp

log = logging.getLogger(__name__)

# The desktop answers a screenshot request in well under a second. One that is still open
# after this long was lost (the portal crashed or was restarted), not slow.
LOST_REQUEST_SECONDS = 10
# How long before a delayed capture its countdown notification is taken down, so that the
# banner has slid away and isn't in the picture.
COUNTDOWN_CLEAR_MS = 600


def _trace(err: BaseException) -> str:
    """Where an error happened, without its message (which can name a capture's file)."""
    frames = "".join(traceback.format_tb(err.__traceback__))
    return f"{type(err).__name__}\n{frames}"


class CaptureController:
    def __init__(self, app: GrabbyHandsApp) -> None:
        self.app = app
        self.settings = config.settings()
        self.windows: list[OverlayWindow] = []
        self.screen: ScreenImage | None = None
        self._busy = False
        self._forced_action: str | None = None
        self._source = "region"
        self._hint: str | None = None
        # Window positions from the Window Helper extension, taken with each screenshot.
        self.desktop_windows: list[WindowRect] = []
        self._permission = None  # PermissionWindow while we wait for the user
        self._delay_source: int | None = None  # countdown of a delayed capture
        self._clear_source: int | None = None  # takes the countdown notification down early
        self._shot = 0  # number of the screenshot request we are waiting for
        self._shot_started = 0.0

    # -- properties used by the overlay -------------------------------------------
    @property
    def default_action(self) -> str:
        return self._forced_action or self.settings.get_string("default-action")

    @property
    def show_loupe(self) -> bool:
        return self.settings.get_boolean("show-loupe")

    @property
    def color_format(self) -> str:
        """The format the C key copies while identifying a color (hex, rgb, cmyk or hsl)."""
        return self.settings.get_string("color-format")

    def supports(self, action: str) -> bool:
        return self.app.supports(action)

    @property
    def active(self) -> bool:
        """A capture is under way: counting down, asking for permission, or on screen."""
        return bool(self._busy or self.windows or self._permission is not None)

    # -- entry points -------------------------------------------------------------
    def capture(
        self,
        delay: int = 0,
        mode: str = "select",
        forced_action: str | None = None,
        hint: str | None = None,
    ) -> None:
        if self.windows:
            self.windows[0].present()
            return
        if self._permission is not None:
            self._permission.present()
            return
        if self._busy and not self._give_up_lost_request():
            return
        self._forced_action = forced_action
        self._hint = hint
        self._source = "region"
        if delay > 0:
            self._busy = True
            title = ngettext("Capturing in {n} second…", "Capturing in {n} seconds…", delay)
            # A normal notification, not a quiet one: its banner is the only sign that a
            # countdown is running, and the only place its Cancel button can be reached.
            self.app.notify(
                title.format(n=delay),
                tag="countdown",
                buttons=((_("Cancel"), "app.cancel-delay"),),
            )

            def clear() -> bool:
                self._clear_source = None
                self.app.withdraw_notification("countdown")
                return GLib.SOURCE_REMOVE

            def fire() -> bool:
                self._delay_source = None
                self._stop_countdown()
                self._shoot(mode)
                return GLib.SOURCE_REMOVE

            # ...and it is gone again just before the picture is taken.
            ahead = min(COUNTDOWN_CLEAR_MS, delay * 500)
            self._clear_source = GLib.timeout_add(delay * 1000 - ahead, clear)
            self._delay_source = GLib.timeout_add(delay * 1000, fire)
        else:
            self._shoot(mode)

    def _stop_countdown(self) -> None:
        if self._clear_source is not None:
            GLib.source_remove(self._clear_source)
            self._clear_source = None
        self.app.withdraw_notification("countdown")

    def cancel_delay(self) -> None:
        """Call off a delayed capture that hasn't fired yet."""
        if self._delay_source is None:
            return
        GLib.source_remove(self._delay_source)
        self._delay_source = None
        self._busy = False
        self._forced_action = None
        self._hint = None
        self._stop_countdown()

    def _give_up_lost_request(self) -> bool:
        """True if we were only "busy" waiting for a screenshot that will never come.

        The desktop's portal can crash or be restarted while it owes us an answer. Without
        this, Print Screen would do nothing for the rest of the session.
        """
        waiting = self._delay_source is None and self._permission is None and not self.windows
        age = GLib.get_monotonic_time() / 1e6 - self._shot_started
        if not (waiting and self._shot_started and age > LOST_REQUEST_SECONDS):
            return False
        log.warning("no answer to a screenshot request after %d s; starting over", age)
        self._shot += 1  # an answer that still turns up is only tidied away
        self._busy = False
        return True

    @property
    def delay_seconds(self) -> int:
        """The delay the D key uses (Settings → General → Delay)."""
        return self.settings.get_int("delay-seconds")

    def pick_color(self, delay: int = 0) -> None:
        self.capture(delay=delay, mode="color")

    def capture_text(self, delay: int = 0) -> None:
        self.capture(
            delay=delay,
            forced_action="ocr",
            hint=_("Drag over the text you want to copy   ·   Esc: cancel"),
        )

    def delayed(self, seconds: int | None) -> None:
        seconds = seconds or self.delay_seconds
        forced, hint = self._forced_action, self._hint
        # Identify Color and window picking come back as they were (waiting for a menu
        # to open and then clicking its window is what the delay is for).
        mode = self.windows[0].mode if self.windows else "select"
        self._close_windows()
        self._busy = False
        self.capture(
            delay=seconds,
            mode=mode if mode in ("color", "window") else "select",
            forced_action=forced,
            hint=hint,
        )

    def window_capture(self) -> None:
        """Start in window mode: hover highlights a window, click selects it."""
        if self.windows:
            self.set_mode("window")
            return
        self.capture(mode="window")

    def window_at(self, x: float, y: float) -> WindowRect | None:
        """Top-most window under a point in logical layout coordinates."""
        return window_at(self.desktop_windows, x, y)

    def window_showing(self, rect: Rect, default: OverlayWindow) -> OverlayWindow:
        """The overlay whose screen holds (most of) an area of the screenshot."""

        def shared(win: OverlayWindow) -> int:
            m = win.mon_img
            w = min(rect.x + rect.w, m.x + m.w) - max(rect.x, m.x)
            h = min(rect.y + rect.h, m.y + m.h) - max(rect.y, m.y)
            return max(0, w) * max(0, h)

        best = max(self.windows, key=shared, default=default)
        return best if shared(best) > 0 else default

    def key_window(self, origin: OverlayWindow) -> OverlayWindow:
        """The overlay a key press is meant for: the one with the selection, else the one
        under the pointer, else the one that got it. (With one screen: always that one.)"""
        if origin.sel is not None or origin not in self.windows:
            return origin
        for win in self.windows:
            if win.sel is not None:
                return win
        if origin.pointer is None:
            for win in self.windows:
                if win.pointer is not None:
                    return win
        return origin

    def repeat_last(self) -> None:
        if self.windows or (self._busy and not self._give_up_lost_request()):
            return
        self._forced_action = None
        self._hint = None
        self._source = "region"

        def after(screen: ScreenImage) -> None:
            self.screen = screen
            region = self.last_region()
            if region is None:
                self._open(screen, "select")
                return
            monitors = self._monitors()
            mapping = ScreenMapping(
                [m.get_geometry() for m in monitors], screen.width, screen.height
            )
            target = monitors[0]
            for m in monitors:
                r = mapping.monitor_rect(m.get_geometry())
                if r.x <= region.x < r.x + r.w and r.y <= region.y < r.y + r.h:
                    target = m
                    break
            win = OverlayWindow(self, screen, mapping, target, mode="flash", selection=region)
            self.windows.append(win)
            win.present()

            def act() -> bool:
                if win in self.windows:  # not canceled (Esc) during the flash
                    self.run_default_action(win)
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(180, act)

        self._shoot("select", after)

    def last_region(self) -> Rect | None:
        x, y, w, h, iw, ih = self.settings.get_value("last-region").unpack()
        if w <= 0 or h <= 0:
            return None
        if self.screen is not None and (iw, ih) != (self.screen.width, self.screen.height):
            return None  # the screen layout changed since then
        return Rect(x, y, w, h)

    # -- overlay callbacks --------------------------------------------------------
    def cancel(self) -> None:
        self._close_windows()
        self._busy = False
        self._forced_action = None

    def set_mode(self, mode: str) -> None:
        for win in self.windows:
            win.set_mode(mode)

    @property
    def has_windows(self) -> bool:
        return bool(self.desktop_windows)

    def window_selected(self, origin: OverlayWindow, rect: Rect) -> None:
        self._source = "window"
        self.selection_started(origin)
        self.set_mode("select")
        origin.set_selection(rect)

    def selection_started(self, origin: OverlayWindow) -> None:
        for win in self.windows:
            if win is not origin and win.sel is not None:
                win.set_selection(None)

    def copy_color(self, win: OverlayWindow, rgb: tuple[int, int, int]) -> None:
        """Quick copy (C key) in the user's preferred format."""
        text = format_color(rgb, self.color_format)
        self.app.copy_text(text, win)
        self.app.notify(_("Copied color {color}").format(color=text), tag="color", transient=True)
        # Say so on the capture screen itself for a moment: a quiet notification may never
        # be seen, and then the screen would just vanish without a word.
        win.show_message(_("Copied {color}").format(color=text))
        GLib.timeout_add(700, lambda: (self._finish_shown(win), GLib.SOURCE_REMOVE)[1])

    def _finish_shown(self, win: OverlayWindow) -> None:
        if win in self.windows:  # not canceled (or replaced by a new capture) meanwhile
            self._finish()

    def show_settings(self) -> None:
        self._finish()
        self.app.show_preferences()

    def show_history(self) -> None:
        self._finish()
        self.app.show_history()

    def show_color(self, rgb: tuple[int, int, int]) -> None:
        """Click in color mode: show HEX, RGB and CMYK with copy buttons."""
        self._finish()
        self.app.show_color(rgb)

    def run_default_action(self, win: OverlayWindow) -> None:
        self.run_action(self.default_action, win)

    def run_action(self, action: str, win: OverlayWindow) -> None:
        try:
            self._run_action(action, win)
        except Exception as err:  # noqa: BLE001
            # Never leave a frozen full-screen overlay behind.
            log.error("action %s failed: %s", action, _trace(err))
            self._finish()
            self.app.notify(
                _("Something went wrong"),
                _("GrabbyHands couldn't finish that. Details are in the system log."),
                tag="error",
            )

    def _run_action(self, action: str, win: OverlayWindow) -> None:
        if self.screen is None or win.busy:
            return
        rect = win.selection_or_full()
        if rect.empty:
            return
        image = self.screen.crop(rect)
        if win.mode in ("select", "flash"):
            self._remember_region(rect)
        source = self._source

        if action == "copy":
            self.app.copy_image(image, win)
            entry = self.app.record(image, source)
            self.app.notify_capture(entry, _("Copied to the clipboard"))
            self._finish()
        elif action == "save":
            entry = self.app.record(image, source, force_save=True)
            if entry.saved_path:  # otherwise record() already said why it couldn't save
                self.app.notify_capture(entry, _("Screenshot saved"), saved=True)
            self._finish()
        elif action == "save-as":
            entry = self.app.record(image, source)
            self._finish()
            self.app.save_as(image, entry)
        elif action in ("edit", "redact"):
            entry = self.app.record(image, source)
            self._finish()
            self.app.open_editor(entry, auto_redact=action == "redact")
        elif action == "pin":
            entry = self.app.record(image, source)
            self._finish()
            self.app.pin(image, entry)
        elif action in ("ocr", "qr"):
            self._run_text_action(action, image, win)
        else:
            log.warning("unknown action %s", action)

    # -- internals ----------------------------------------------------------------
    def _run_text_action(self, action: str, image: Image.Image, win: OverlayWindow) -> None:
        win.show_busy(_("Reading text…") if action == "ocr" else _("Looking for codes…"))
        language = self.settings.get_string("ocr-language")

        def work() -> None:
            try:
                if action == "ocr":
                    from grabbyhands import ocr

                    result: object = (image, ocr.read_words(image, language))
                else:
                    from grabbyhands import qr

                    result = qr.read_codes(image)
                error = None
            except Exception as err:  # noqa: BLE001 - shown to the user
                # (Tesseract's own message is safe to log; a code reader's might quote
                # what it read, so only its kind is)
                log.warning("%s failed: %s", action, err if action == "ocr" else type(err).__name__)
                result, error = None, str(err)
            GLib.idle_add(self._text_done, action, win, result, error)

        threading.Thread(target=work, daemon=True).start()

    def _text_done(self, action: str, win: OverlayWindow, result, error: str | None) -> bool:
        try:
            self._show_text_result(action, win, result, error)
        except Exception as err:  # noqa: BLE001
            log.error("%s result handling failed: %s", action, _trace(err))
            self._finish()
        return GLib.SOURCE_REMOVE

    def _show_text_result(self, action: str, win: OverlayWindow, result, error: str | None):
        if win not in self.windows:
            return GLib.SOURCE_REMOVE
        if error:
            win.show_message(self._text_error(action, error))
            GLib.timeout_add(4000, lambda: (win.hide_busy(), GLib.SOURCE_REMOVE)[1])
            return GLib.SOURCE_REMOVE
        if action == "ocr":
            image, words = result
            if not words:
                win.show_message(_("No text found here. Try a bigger area."))
                GLib.timeout_add(1800, lambda: (win.hide_busy(), GLib.SOURCE_REMOVE)[1])
                return GLib.SOURCE_REMOVE
            self._finish()
            self.app.show_text_picker(image, words)
            return GLib.SOURCE_REMOVE
        else:
            codes = result or []
            if not codes:
                win.show_message(_("No QR code or barcode found here."))
                GLib.timeout_add(1800, lambda: (win.hide_busy(), GLib.SOURCE_REMOVE)[1])
                return GLib.SOURCE_REMOVE
            text = "\n".join(c.text for c in codes)
            self.app.copy_text(text, win)
            self.app.notify_codes(codes)
        self._finish()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _text_error(action: str, error: str) -> str:
        """A readable message for a failed text or code read (``error`` is the raw reason)."""
        if action == "ocr":
            from grabbyhands import ocr

            return ocr.friendly_error(error)
        return _("Couldn't read codes here: {error}").format(error=error)

    def _remember_region(self, rect: Rect) -> None:
        if self.screen is None:
            return
        value = GLib.Variant(
            "(iiiiii)", (rect.x, rect.y, rect.w, rect.h, self.screen.width, self.screen.height)
        )
        self.settings.set_value("last-region", value)

    def _monitors(self) -> list[Gdk.Monitor]:
        model = Gdk.Display.get_default().get_monitors()
        return [model.get_item(i) for i in range(model.get_n_items())]

    def _shoot(self, mode: str, after: Callable[[ScreenImage], None] | None = None) -> None:
        self._busy = True
        self._shot += 1
        shot = self._shot
        self._shot_started = GLib.get_monotonic_time() / 1e6

        def got(uri: str | None, response: int) -> None:
            if shot != self._shot:  # given up on meanwhile: only remove the portal's file
                if uri:
                    self._discard_portal_file(Gio.File.new_for_uri(uri).get_path())
                return
            self._shot_started = 0.0
            if uri is None and response == RESPONSE_OTHER:
                self._ask_permission(lambda: self._shoot(mode, after))
                return
            screen = self._load(uri, response)
            if screen is None:
                self._busy = False
                return
            # Ask right away, before our overlay exists, so the list matches the screenshot.
            found = get_windows()
            self.desktop_windows = to_layout(
                found,
                get_stage_size() if found else None,
                [m.get_geometry() for m in self._monitors()],
            )
            if after is not None:
                after(screen)
            else:
                self._open(screen, mode)

        self.app.portal.screenshot(False, got)

    def _ask_permission(self, retry: Callable[[], None]) -> None:
        """The desktop refused a background screenshot: ask the user, then retry."""
        from grabbyhands.permission import PermissionWindow

        def granted(uri: str) -> None:
            asked_here = self._permission is window
            if asked_here:
                self._permission = None
            path = Gio.File.new_for_uri(uri).get_path()
            if path:
                self._discard_portal_file(path)
            if not asked_here:
                return  # the window was closed before the desktop answered: no capture

            def again() -> bool:
                retry()  # our window is gone now, so it isn't in the capture
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(400, again)

        def closed() -> None:
            if self._permission is window:
                self._permission = None
                self._busy = False

        log.info("screenshot permission needed; asking the user")
        window = PermissionWindow(self.app, granted, closed)
        self._permission = window
        window.present()

    def _load(self, uri: str | None, response: int = 0) -> ScreenImage | None:
        if not uri:
            if response == RESPONSE_CANCELLED:
                return None
            if response == RESPONSE_UNAVAILABLE:
                # No portal to ask (not installed, crashed, restarting): this isn't about
                # permission, so don't send the user to the permission settings.
                self.app.notify(
                    _("Couldn't take a screenshot"),
                    _("Your desktop's screenshot service isn't answering. Try again in a moment."),
                    tag="error",
                )
                return None
            self.app.notify(
                _("Couldn't take a screenshot"),
                _(
                    "Screenshot permission may be turned off. Check GNOME Settings → Apps → "
                    "GrabbyHands."
                ),
                tag="error",
            )
            return None
        path = Gio.File.new_for_uri(uri).get_path()
        try:
            screen = ScreenImage.from_file(path)
        except Exception as err:  # noqa: BLE001
            log.warning("could not read screenshot: %s", type(err).__name__)
            self.app.notify(_("Couldn't read the screenshot"), str(err), tag="error")
            self._discard_portal_file(path)
            return None
        self._discard_portal_file(path)
        return screen

    def _discard_portal_file(self, path: str | None) -> None:
        """The portal writes a file for every request; we keep our own copy, so drop it."""
        try:
            if path and os.path.exists(path):
                real = os.path.realpath(path)
                if real.startswith(str(config.save_folder()) + os.sep):
                    return  # never touch the user's own saved captures
                os.unlink(path)
                log.debug("removed the portal's screenshot file")
        except OSError as err:
            log.info("could not remove the portal's screenshot file: %s", err.strerror)

    def _open(self, screen: ScreenImage, mode: str, selection: Rect | None = None) -> None:
        self.screen = screen
        if mode == "window" and not self.desktop_windows:
            mode = "select"
            self.app.notify(
                _("Window capture needs the Window Helper"),
                _(
                    "Turn on the “GrabbyHands Window Helper” extension in the Extensions app "
                    "(you may need to log out and back in once after installing GrabbyHands)."
                ),
                tag="window-helper",
                buttons=((_("Open Settings"), "app.settings-page::general"),),
            )
        monitors = self._monitors()
        mapping = ScreenMapping([m.get_geometry() for m in monitors], screen.width, screen.height)
        for monitor in monitors:
            win = OverlayWindow(self, screen, mapping, monitor, mode, selection, self._hint)
            self.windows.append(win)
            win.present()

    def _close_windows(self) -> None:
        windows, self.windows = self.windows, []
        for win in windows:
            win.destroy()

    def _finish(self) -> None:
        self._close_windows()
        self._busy = False
        self._forced_action = None
        self._hint = None
