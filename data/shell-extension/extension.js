// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 al3phnul and contributors
//
// GrabbyHands Window Helper.
//
// It does two things for GrabbyHands that an app can't do by itself on Wayland:
//
// 1. Window positions. Wayland doesn't let apps see where windows are, so this extension
//    answers one question over D-Bus: "where are the visible windows on the current
//    workspace, top-most first?" It returns positions and app names only;
//    never window titles or contents. (GetStageSize says how big the whole desktop is in
//    the same units, so the positions can be matched to the screenshot on any scaling.)
//
// 2. The screenshot button in the system menu, only if the user asks for it in GrabbyHands'
//    Settings: the button then starts a GrabbyHands capture instead of GNOME's own tool.
//    The choice is kept in this extension's settings, so it is already in place at login.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'io.github.al3phnul.GrabbyHands.Shell';
const OBJECT_PATH = '/io/github/al3phnul/GrabbyHands/Shell';
const INTERFACE = `
<node>
  <interface name="io.github.al3phnul.GrabbyHands.Shell">
    <method name="GetWindows">
      <!-- x, y, width, height (stage units, see GetStageSize), app name -->
      <arg type="a(iiiis)" direction="out" name="windows"/>
    </method>
    <method name="GetStageSize">
      <!-- width and height of all monitors together, in the units GetWindows uses -->
      <arg type="i" direction="out" name="width"/>
      <arg type="i" direction="out" name="height"/>
    </method>
    <method name="GetMenuButton">
      <!-- enabled: the user's choice. available: this GNOME has the button where this
           extension looks for it, so the choice can take effect. -->
      <arg type="b" direction="out" name="enabled"/>
      <arg type="b" direction="out" name="available"/>
    </method>
    <method name="SetMenuButton">
      <!-- true: the screenshot button in the system menu starts GrabbyHands.
           Returns whether the button is now as asked. -->
      <arg type="b" direction="in" name="enabled"/>
      <arg type="b" direction="out" name="done"/>
    </method>
    <property name="Version" type="u" access="read"/>
  </interface>
</node>`;

const APP_ID = 'io.github.al3phnul.GrabbyHands';
const APP_PATH = '/io/github/al3phnul/GrabbyHands';
const MENU_BUTTON_KEY = 'replace-screenshot-button';
const SCREENSHOT_ICON = 'screenshooter-symbolic';
const MENU_BUTTON_TRIES = 10;  // one a second, while GNOME is still starting

const CAPTURABLE = new Set([
    Meta.WindowType.NORMAL,
    Meta.WindowType.DIALOG,
    Meta.WindowType.MODAL_DIALOG,
    Meta.WindowType.UTILITY,
    Meta.WindowType.TOOLBAR,
    Meta.WindowType.SPLASHSCREEN,
]);

// GNOME's own screenshot tool: what the button did before, and what it falls back to.
function openGnomeScreenshotTool() {
    Main.screenshotUI.open().catch(e => console.error(e));
}

// Ask GrabbyHands for a capture (this starts it if it isn't running). If it can't be
// reached, e.g. because it was uninstalled, open GNOME's tool so the button always works.
function startCapture() {
    Gio.DBus.session.call(
        APP_ID,
        APP_PATH,
        'org.freedesktop.Application',
        'ActivateAction',
        new GLib.Variant('(sava{sv})', ['capture', [], {}]),
        null,
        Gio.DBusCallFlags.NONE,
        -1,
        null,
        (connection, result) => {
            try {
                connection.call_finish(result);
            } catch (e) {
                console.warn(`GrabbyHands could not be started (${e.message}); opening GNOME's screenshot tool`);
                openGnomeScreenshotTool();
            }
        });
}

// The screenshot button in the system menu (top-right corner of the screen).
//
// GNOME doesn't offer extensions a way to change that button, so this looks for it where
// GNOME 46 to 50 keep it and does nothing if it isn't there. GNOME's own button is never
// removed or changed: it is hidden while a look-alike stands in its place, and shown again
// the moment this is turned off.
class MenuButton {
    constructor() {
        this._original = null;
        this._button = null;
        this._originalGone = 0;
    }

    static find() {
        try {
            const box = Main.panel.statusArea.quickSettings?._system?._systemItem?.child;
            const items = box?.get_children() ?? [];
            return items.find(item =>
                item.constructor?.name === 'ScreenshotItem' || item.icon_name === SCREENSHOT_ICON) ?? null;
        } catch (e) {
            console.warn(`GrabbyHands: no screenshot button in the system menu (${e.message})`);
            return null;
        }
    }

    get active() {
        return this._button !== null;
    }

    get available() {
        return this.active || MenuButton.find() !== null;
    }

    enable() {
        if (this.active)
            return true;
        const original = MenuButton.find();
        const box = original?.get_parent();
        if (!box || !original.visible)
            return false;

        const button = new St.Button({
            style_class: 'icon-button',
            can_focus: true,
            icon_name: SCREENSHOT_ICON,
            accessible_name: original.accessible_name,  // GNOME's wording, already translated
        });
        button.connect('clicked', () => {
            // As GNOME's button does: close the menu at once, then act before the next
            // frame, so the menu is not in the picture.
            Main.panel.statusArea.quickSettings.menu.close(PopupAnimation.NONE);
            global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
                startCapture();
                return GLib.SOURCE_REMOVE;
            });
        });
        button.connect('destroy', () => {
            // The menu itself went away (or disable() ran): forget the stand-in.
            if (this._button === button)
                this._forget();
        });
        box.insert_child_above(button, original);
        original.hide();

        this._original = original;
        this._originalGone = original.connect('destroy', () => {
            this._original = null;
            this._originalGone = 0;
        });
        this._button = button;
        return true;
    }

    disable() {
        const button = this._button;
        const original = this._forget();
        button?.destroy();
        original?.show();
    }

    _forget() {
        const original = this._original;
        if (original && this._originalGone)
            original.disconnect(this._originalGone);
        this._original = null;
        this._originalGone = 0;
        this._button = null;
        return original;
    }
}

class HelperService {
    constructor(extension) {
        this._menuButton = new MenuButton();
        this._settings = null;
        this._changed = 0;
        this._retry = 0;
        this._triesLeft = MENU_BUTTON_TRIES;
        try {
            this._settings = extension.getSettings();
            this._changed = this._settings.connect(
                `changed::${MENU_BUTTON_KEY}`, () => this._applyMenuButton());
        } catch (e) {
            // Window capture still works; only the menu button can't be remembered.
            console.warn(`GrabbyHands: settings unavailable (${e.message})`);
        }
        this._applyMenuButton();
    }

    destroy() {
        if (this._settings && this._changed)
            this._settings.disconnect(this._changed);
        this._settings = null;
        this._changed = 0;
        this._stopRetrying();
        this._menuButton.disable();
    }

    _wanted() {
        return this._settings?.get_boolean(MENU_BUTTON_KEY) ?? false;
    }

    _applyMenuButton() {
        this._stopRetrying();
        try {
            if (!this._wanted()) {
                this._menuButton.disable();
            } else if (!this._menuButton.enable() && this._triesLeft > 0) {
                // Right after login GNOME may still be putting its menu together.
                this._triesLeft--;
                this._retry = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                    this._retry = 0;
                    this._applyMenuButton();
                    return GLib.SOURCE_REMOVE;
                });
            }
        } catch (e) {
            console.warn(`GrabbyHands: could not change the system menu (${e.message})`);
        }
    }

    _stopRetrying() {
        if (this._retry)
            GLib.source_remove(this._retry);
        this._retry = 0;
    }

    get Version() {
        return 3;
    }

    GetStageSize() {
        return global.display.get_size();
    }

    GetWindows() {
        const workspace = global.workspace_manager.get_active_workspace();
        const tracker = Shell.WindowTracker.get_default();
        const result = [];
        // Window actors come bottom-to-top; GrabbyHands wants top-most first.
        for (const actor of global.get_window_actors().reverse()) {
            const win = actor.get_meta_window();
            if (!win || win.minimized || !CAPTURABLE.has(win.get_window_type()))
                continue;
            if (!win.showing_on_its_workspace() || !win.located_on_workspace(workspace))
                continue;
            const rect = win.get_frame_rect();
            if (rect.width <= 0 || rect.height <= 0)
                continue;
            const app = tracker.get_window_app(win);
            result.push([rect.x, rect.y, rect.width, rect.height, app ? app.get_name() : '']);
        }
        return result;
    }

    GetMenuButton() {
        return [this._wanted(), this._settings !== null && this._menuButton.available];
    }

    SetMenuButton(enabled) {
        if (!this._settings)
            return false;
        // Saving the choice applies it too (see the "changed" handler above).
        this._settings.set_boolean(MENU_BUTTON_KEY, enabled);
        this._triesLeft = MENU_BUTTON_TRIES;
        this._applyMenuButton();
        return this._menuButton.active === enabled;
    }
}

export default class GrabbyHandsWindowHelper extends Extension {
    enable() {
        this._helper = new HelperService(this);
        this._service = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this._helper);
        this._ownerId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            connection => this._service.export(connection, OBJECT_PATH),
            null,
            null);
    }

    disable() {
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
        this._service?.unexport();
        this._service = null;
        this._helper?.destroy();
        this._helper = null;
    }
}
