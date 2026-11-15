# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""The Smart Edit panel at the right of the editor: a short chat with Claude about the picture.

Requests run in a worker thread. While one runs, the picture can't be edited by hand, so the
result never overwrites a change made meanwhile. Each finished message lands as one undo step.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from gi.repository import Adw, GLib, Gtk, Pango

from grabbyhands import config
from grabbyhands.editor.document import Document
from grabbyhands.smartedit.pricing import format_usd

if TYPE_CHECKING:
    from grabbyhands.editor.window import EditorWindow

log = logging.getLogger(__name__)

KEYS_URL = "https://console.anthropic.com/settings/keys"
LIMITS_URL = "https://console.anthropic.com/settings/limits"
PRIVACY_URL = "https://www.anthropic.com/legal/privacy"


def error_text(kind: str, detail: str = "", chat_limit: float = 0, month_limit: float = 0) -> str:
    """What the panel says when a message couldn't be handled."""
    if kind == "budget":
        if detail == "chat":
            return _(
                "This message could take the chat over its limit of {limit}. Start a new chat, "
                "or raise the limit in Settings → Smart Edit."
            ).format(limit=format_usd(chat_limit))
        if detail == "month":
            return _(
                "This message could go over this month's limit of {limit}. "
                "You can raise it in Settings → Smart Edit."
            ).format(limit=format_usd(month_limit))
        return _(
            "GrabbyHands doesn't know this model's price, so it can't keep to your limits. "
            "Pick another model in Settings → Smart Edit."
        )
    texts = {
        "key": _("Anthropic didn't accept the API key. Check it in Settings → Smart Edit."),
        "permission": _(
            "This API key isn't allowed to use that. Check it in the Anthropic Console."
        ),
        "model": _(
            "This model isn't available to your API key. Pick another in Settings → Smart Edit."
        ),
        "rate": _("Your Anthropic account has hit its rate limit. Try again in a minute."),
        "overloaded": _("Anthropic is very busy right now. Try again in a moment."),
        "server": _("Anthropic had a problem with this request. Try again in a moment."),
        "network": _("Couldn't reach Anthropic. Check your internet connection."),
        "credit": _("Your Anthropic account is out of credit. Add some in the Anthropic Console."),
        "refused": _("Claude declined this request. Nothing was changed."),
        "cancelled": _("Stopped. Nothing was changed."),
        "ocr": _(
            "Personal details can't be hidden because text recognition (Tesseract) isn't "
            "installed. Install it, or turn off “Hide personal details” in Settings → Smart Edit."
        ),
    }
    if kind == "request":
        short = (detail[:200] + "…") if len(detail) > 200 else detail
        return _("Anthropic turned the request down: {why}").format(why=short)
    return texts.get(kind, _("Something went wrong. Details are in the system log."))


def summary_text(summary: dict) -> str:
    parts = []
    if summary.get("added"):
        parts.append(_("added {n}").format(n=summary["added"]))
    if summary.get("changed"):
        parts.append(_("changed {n}").format(n=summary["changed"]))
    if summary.get("removed"):
        parts.append(_("removed {n}").format(n=summary["removed"]))
    if summary.get("crop"):
        parts.append(_("crop changed"))
    text = ", ".join(parts)
    return text[:1].upper() + text[1:] if text else ""


def _label(text: str, *classes: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for css in classes:
        label.add_css_class(css)
    return label


class SmartPanel(Gtk.Box):
    def __init__(self, window: EditorWindow) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.app = window.app
        self.settings = window.settings
        self.session = None
        self.busy = False
        self.cancel = threading.Event()
        self._undo_buttons: list[tuple[Gtk.Button, int]] = []
        self._words_cache: dict = {}
        self.add_css_class("gh-smart-panel")
        self.set_size_request(320, -1)

        header = Gtk.Box(spacing=6)
        header.set_margin_start(12)
        header.set_margin_end(6)
        header.set_margin_top(6)
        header.set_margin_bottom(6)
        title = Gtk.Label(label=_("Smart Edit"), xalign=0, hexpand=True)
        title.add_css_class("heading")
        header.append(title)
        self.new_chat = Gtk.Button(icon_name="list-add-symbolic")
        self.new_chat.set_tooltip_text(_("New chat"))
        self.new_chat.add_css_class("flat")
        self.new_chat.connect("clicked", lambda *_: self.reset())
        header.append(self.new_chat)
        close = Gtk.Button(icon_name="gh-close-symbolic")
        close.set_tooltip_text(_("Close Smart Edit"))
        close.add_css_class("flat")
        close.connect("clicked", lambda *_: window.show_smart_edit(False))
        header.append(close)
        self.append(header)
        self.append(Gtk.Separator())

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._consent_page(), "consent")
        self.stack.add_named(self._setup_page(), "setup")
        self.stack.add_named(self._chat_page(), "chat")
        self.append(self.stack)
        self.stack.set_visible_child_name("chat")

    # -- pages ---------------------------------------------------------------------------------
    def _consent_page(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            icon_name="gh-smart-edit-symbolic",
            title=_("Before You Start"),
            description=_(
                "Smart Edit sends this picture, your messages and the list of annotations to "
                "Anthropic, the company that makes Claude, using your own API key. Anthropic "
                "charges your account for each message; GrabbyHands shows an estimate.\n\n"
                "Personal details GrabbyHands can find are blacked out before sending (see "
                "Settings → Smart Edit). Nothing is sent until you press Send."
            ),
        )
        page.add_css_class("compact")
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        ok = Gtk.Button(label=_("Continue"))
        ok.add_css_class("suggested-action")
        ok.add_css_class("pill")
        ok.connect("clicked", lambda *_: self._accept())
        privacy = Gtk.Button(label=_("Anthropic's Privacy Policy"))
        privacy.add_css_class("flat")
        privacy.connect("clicked", lambda *_: self.app._open_url(PRIVACY_URL))
        buttons.append(ok)
        buttons.append(privacy)
        page.set_child(buttons)
        return page

    def _setup_page(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            icon_name="dialog-password-symbolic",
            title=_("Add Your API Key"),
            description=_(
                "Smart Edit uses your own Anthropic API key. Create one in the Anthropic "
                "Console, then paste it in Settings → Smart Edit. It is kept in your keyring."
            ),
        )
        page.add_css_class("compact")
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        settings = Gtk.Button(label=_("Open Settings"))
        settings.add_css_class("suggested-action")
        settings.add_css_class("pill")
        settings.connect(
            "clicked", lambda *_: self.app.show_preferences(self.window, page="smart-edit")
        )
        get_key = Gtk.Button(label=_("Get a Key"))
        get_key.add_css_class("flat")
        get_key.connect("clicked", lambda *_: self.app._open_url(KEYS_URL))
        retry = Gtk.Button(label=_("I Added It"))
        retry.add_css_class("flat")
        retry.connect("clicked", lambda *_: self.refresh())
        for b in (settings, get_key, retry):
            buttons.append(b)
        page.set_child(buttons)
        return page

    def _chat_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.list, f"set_margin_{side}")(12)
        self.hint = _label(
            _(
                "Describe how to mark up the picture, for example “blur the email addresses”, "
                "“put an arrow at the Save button” or “number the three steps”."
            ),
            "dim-label",
        )
        self.list.append(self.hint)
        self.scroller.set_child(self.list)
        box.append(self.scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "bottom"):
            getattr(footer, f"set_margin_{side}")(12)
        footer.set_margin_top(6)
        row = Gtk.Box(spacing=6)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text=_("Describe an edit…"))
        self.entry.set_max_length(2000)
        self.entry.connect("activate", lambda *_: self.send())
        self.send_btn = Gtk.Button(icon_name="go-up-symbolic")
        self.send_btn.set_tooltip_text(_("Send"))
        self.send_btn.add_css_class("suggested-action")
        self.send_btn.add_css_class("circular")
        self.send_btn.connect("clicked", lambda *_: self.send())
        self.stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic", visible=False)
        self.stop_btn.set_tooltip_text(_("Stop"))
        self.stop_btn.add_css_class("circular")
        self.stop_btn.connect("clicked", lambda *_: self.stop())
        row.append(self.entry)
        row.append(self.send_btn)
        row.append(self.stop_btn)
        footer.append(row)
        self.total = Gtk.Label(xalign=0)
        self.total.add_css_class("caption")
        self.total.add_css_class("dim-label")
        footer.append(self.total)
        box.append(footer)
        self._update_total()
        return box

    # -- state ---------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Pick the page: consent first, then a key, then the chat."""
        if not self.settings.get_boolean("smart-edit-consent"):
            self.stack.set_visible_child_name("consent")
            return
        if self.session is not None:
            self._show_chat()
            return

        def work() -> None:
            from grabbyhands.smartedit import secret

            has_key = secret.lookup() is not None
            GLib.idle_add(done, has_key)

        def done(has_key: bool) -> bool:
            self.stack.set_visible_child_name("chat" if has_key else "setup")
            if has_key:
                self._show_chat()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _show_chat(self) -> None:
        self.stack.set_visible_child_name("chat")
        self.entry.grab_focus()

    def _accept(self) -> None:
        self.settings.set_boolean("smart-edit-consent", True)
        self.refresh()

    def reset(self) -> None:
        if self.busy:
            return
        self.session = None
        self._undo_buttons.clear()
        child = self.list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            if child is not self.hint:
                self.list.remove(child)
            child = following
        self.hint.set_visible(True)
        self._update_total()
        self.refresh()

    def _update_total(self) -> None:
        spent = self.session.spent if self.session else 0.0
        unknown = bool(self.session and self.session.unknown_cost)
        model = self.settings.get_string("smart-edit-model")
        text = _("This chat ≈ {cost}").format(cost=format_usd(spent))
        if unknown:
            text += " " + _("(plus messages of unknown cost)")
        self.total.set_text(f"{text} · {model}")

    def sync_undo(self) -> None:
        """'Undo this' only works while that edit is still the latest change."""
        serial = self.window.canvas.undo.serial
        for button, after in self._undo_buttons:
            button.set_sensitive(serial == after)

    # -- chat bubbles ------------------------------------------------------------------
    def _bubble(self, *children: Gtk.Widget, css: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("gh-smart-bubble")
        box.add_css_class(css)
        if css == "gh-smart-user":
            box.set_margin_start(28)  # the person's own messages sit to the right
        for child in children:
            box.append(child)
        self.hint.set_visible(False)
        self.list.append(box)
        GLib.idle_add(self._scroll_down)
        return box

    def _scroll_down(self) -> bool:
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_upper())
        return GLib.SOURCE_REMOVE

    def _error(self, text: str) -> None:
        self._bubble(_label(text), css="gh-smart-error")

    # -- sending ---------------------------------------------------------------------------------
    def _words_provider(self, image, language: str):
        from grabbyhands import ocr

        if not ocr.available():
            return None
        lock = threading.Lock()
        cache = self._words_cache

        def words() -> list:
            with lock:
                if "words" not in cache:
                    cache["words"] = ocr.read_words(image, language)
                return cache["words"]

        return words

    def send(self) -> None:
        text = self.entry.get_text().strip()
        if not text or self.busy:
            return
        s = self.settings
        self.entry.set_text("")
        self._bubble(_label(text), css="gh-smart-user")
        spinner = Gtk.Spinner(spinning=True)
        status = Gtk.Box(spacing=8)
        status.append(spinner)
        self.status_label = Gtk.Label(label=_("Working…"), xalign=0)
        self.status_label.add_css_class("dim-label")
        status.append(self.status_label)
        self.status = self._bubble(status, css="gh-smart-status")
        self._set_busy(True)

        win = self.window
        snapshot = Document.from_dict(win.doc.to_dict())
        style = {
            "color": win.style_.color,
            "width": win.style_.width,
            "size": win.style_.font_size,
        }
        model = s.get_string("smart-edit-model")
        effort = s.get_string("smart-edit-effort")
        hide = s.get_boolean("smart-edit-hide-personal")
        language = s.get_string("ocr-language")
        ledger = config.data_dir() / "smart-edit-spend.json"
        chat_limit = s.get_double("smart-edit-chat-limit")
        month_limit = s.get_double("smart-edit-month-limit")
        original = win.original
        session = self.session
        cancel = self.cancel = threading.Event()
        words = self._words_provider(original, language)

        def work() -> None:
            from grabbyhands.smartedit import secret
            from grabbyhands.smartedit.budget import Budget
            from grabbyhands.smartedit.session import (
                Session,
                SmartEditError,
                make_client,
                prepare_image,
            )

            nonlocal session
            try:
                if session is None:
                    key = secret.lookup()
                    if not key:
                        raise SmartEditError("nokey")
                    hidden = []
                    if hide:
                        if words is None:
                            raise SmartEditError("ocr")
                        from grabbyhands import redact

                        hidden = [f.box for f in redact.find_sensitive(words())]
                    session = Session(
                        client=make_client(key),
                        image=prepare_image(original, hidden),
                        words=words,
                    )
                session.model = model
                session.effort = effort
                session.style = style
                budget = Budget(ledger, chat_limit, month_limit)
                result = session.send(snapshot, text, budget, cancel)
                GLib.idle_add(self._done, session, result, None, chat_limit, month_limit)
            except SmartEditError as err:
                GLib.idle_add(self._done, session, None, err, chat_limit, month_limit)
            except Exception as err:  # noqa: BLE001 (never let the worker die silently)
                log.warning("Smart Edit failed: %s", type(err).__name__)
                GLib.idle_add(
                    self._done, session, None, SmartEditError("unknown"), chat_limit, month_limit
                )

        threading.Thread(target=work, daemon=True).start()

    def stop(self) -> None:
        if self.busy:
            self.cancel.set()
            self.status_label.set_text(_("Stopping…"))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.entry.set_sensitive(not busy)
        self.send_btn.set_visible(not busy)
        self.stop_btn.set_visible(busy)
        self.new_chat.set_sensitive(not busy)
        self.window.set_editing_locked(busy)

    def _done(self, session, result, error, chat_limit: float, month_limit: float) -> bool:
        self._set_busy(False)
        self.list.remove(self.status)
        if self.window._finished:
            return GLib.SOURCE_REMOVE
        if session is not None:
            self.session = session
        if error is not None:
            if error.kind == "nokey":
                self.stack.set_visible_child_name("setup")
            else:
                self._error(error_text(error.kind, error.detail, chat_limit, month_limit))
            self._update_total()
            return GLib.SOURCE_REMOVE

        children: list[Gtk.Widget] = []
        if result.reply:
            children.append(_label(result.reply))
        notes = []
        done_text = summary_text(result.summary)
        if done_text:
            notes.append(done_text)
        notes.append(
            "≈ " + format_usd(result.cost) if result.cost is not None else _("cost unknown")
        )
        meta = _label(" · ".join(notes), "caption", "dim-label")
        children.append(meta)
        if result.notice == "rounds":
            children.append(
                _label(_("Stopped after several steps. Send “continue” to go on."), "caption")
            )
        elif result.notice == "max_tokens":
            children.append(_label(_("The reply was cut short."), "caption"))
        if result.changed:
            serial = self.window.apply_smart_edit(result.doc)
            undo = Gtk.Button(label=_("Undo This"), halign=Gtk.Align.START)
            undo.add_css_class("flat")
            undo.add_css_class("small")
            undo.connect("clicked", self._undo_clicked, serial)
            self._undo_buttons.append((undo, serial))
            children.append(undo)
        self._bubble(*children, css="gh-smart-reply")
        self.sync_undo()
        self._update_total()
        self.entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def _undo_clicked(self, button: Gtk.Button, serial: int) -> None:
        if self.window.canvas.undo.serial != serial:
            self.window.toast(_("Other changes came after this one. Use Undo (Ctrl+Z) instead."))
            return
        self.window.canvas.do_undo()
        button.set_label(_("Undone"))
        button.set_sensitive(False)
