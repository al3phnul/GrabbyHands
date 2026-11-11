// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 al3phnul and contributors
//
// Test hook, never installed: tests/shell_session.py loads it into a private, headless
// GNOME Shell so the test can ask that Shell what its system menu looks like
// (org.gnome.Shell.Eval only answers in "unsafe mode").

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class TestHook extends Extension {
    enable() {
        global.context.unsafe_mode = true;
    }

    disable() {
        global.context.unsafe_mode = false;
    }
}
