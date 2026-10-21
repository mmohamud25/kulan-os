// Kulan Assistant in the shell: writing tools for the selected text in any
// app (Shift+Super+W), and Translate / Explain on the Live Text
// notification. The assistant itself (the kulan-assistant package) runs on
// this computer; this only hands it the text and puts the result back.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');

const TITLE = 'Kulan Assistant';
const SCHEMA = 'org.kulan.assistant';
// kulan-assistant run … --replace: how the window was closed.
const EXIT_REPLACE = 3;
const EXIT_COPY = 4;
// Apps where Ctrl+V types a control character and Shift+Ctrl+V pastes.
const TERMINALS = /^(org\.gnome\.(Console|Terminal|Ptyxis)|kgx|gnome-terminal|ptyxis|xterm|kitty|alacritty|foot|org\.wezfurlong\.wezterm)/i;
// Long enough for the app to receive the paste before the clipboard is put back.
const RESTORE_CLIPBOARD_MS = 1000;
const FOCUS_WAIT_MS = 150;
const MAX_TEXT = 100000;

function readText(type) {
    return new Promise(resolve => {
        St.Clipboard.get_default().get_text(type, (_c, text) => resolve(text ?? ''));
    });
}

function wait(ms) {
    return new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    }));
}

export class Assistant {
    constructor() {
        this._busy = false;
        this._keyboard = null;
        this._settings = null;
    }

    // Looked up when needed, so installing Kulan Assistant works without
    // signing out.
    get installed() {
        if (!this._settings) {
            // From the file as it is now: the shell's own list is from when it started,
            // and asking that for a schema it doesn't know would stop the shell.
            let schema = null;
            try {
                schema = Gio.SettingsSchemaSource.new_from_directory('/usr/share/glib-2.0/schemas',
                    null, true).lookup(SCHEMA, false);
            } catch {
                // no compiled settings at all
            }
            if (schema)
                this._settings = new Gio.Settings({settings_schema: schema});
        }
        return this._settings !== null && GLib.find_program_in_path('kulan-assistant') !== null;
    }

    enabled(key) {
        return this.installed && this._settings.get_boolean(key);
    }

    // Shift+Super+W: the selected text, in the Kulan Assistant window.
    async writingTools() {
        if (!this.installed) {
            Main.notify(TITLE, 'Kulan Assistant isn’t installed.');
            return;
        }
        if (!this._settings.get_boolean('writing-tools')) {
            Main.notify(TITLE, 'Writing tools are off. Turn them on in Kulan Settings > Assistant.');
            return;
        }
        if (this._busy)
            return;
        // What is selected is the PRIMARY selection (no copying needed).
        const text = (await readText(St.ClipboardType.PRIMARY)).slice(0, MAX_TEXT);
        if (!text.trim()) {
            Main.notify(TITLE, 'Select some text first, then press Shift+Super+W.');
            return;
        }
        const window = global.display.focus_window;
        this._busy = true;
        try {
            const [code, result] = await this._run(['run', 'proofread', '--replace'], text);
            if (!result)
                return;
            if (code === EXIT_REPLACE)
                await this._paste(window, result);
            else if (code === EXIT_COPY)
                St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, result);
        } catch (e) {
            logError(e, 'Kulan Shell: writing tools');
            Main.notify(TITLE, 'Couldn’t open the writing tools.');
        } finally {
            this._busy = false;
        }
    }

    // A window of its own for text that isn't being replaced (Live Text).
    async show(task, text) {
        try {
            await this._run(['run', task], text.slice(0, MAX_TEXT));
        } catch (e) {
            logError(e, `Kulan Shell: assistant ${task}`);
        }
    }

    async _run(args, text) {
        const proc = Gio.Subprocess.new(['kulan-assistant', ...args],
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE);
        const [out] = await proc.communicate_utf8_async(text, null);
        return [proc.get_if_exited() ? proc.get_exit_status() : 1, out ?? ''];
    }

    // Put the result in place of the selection: back to the app it came
    // from, paste, then give the clipboard back what was on it.
    async _paste(window, result) {
        const clipboard = St.Clipboard.get_default();
        const previous = await readText(St.ClipboardType.CLIPBOARD);
        clipboard.set_text(St.ClipboardType.CLIPBOARD, result);
        if (!window || window.is_override_redirect?.()) {
            Main.notify(TITLE, 'Copied. Paste it where you want it.');
            return;
        }
        try {
            window.activate(global.get_current_time());
        } catch {
            Main.notify(TITLE, 'Copied. Paste it where you want it.');
            return;
        }
        await wait(FOCUS_WAIT_MS);
        if (global.display.focus_window !== window) {
            Main.notify(TITLE, 'Copied. Paste it where you want it.');
            return;
        }
        const app = Shell.WindowTracker.get_default().get_window_app(window);
        const terminal = TERMINALS.test(app?.get_id() ?? '') || TERMINALS.test(window.get_wm_class() ?? '');
        this._pressPaste(terminal);
        if (previous) {
            await wait(RESTORE_CLIPBOARD_MS);
            // Only if nothing else was copied meanwhile.
            if (await readText(St.ClipboardType.CLIPBOARD) === result)
                clipboard.set_text(St.ClipboardType.CLIPBOARD, previous);
        }
    }

    _pressPaste(withShift) {
        if (!this._keyboard) {
            const seat = Clutter.get_default_backend().get_default_seat();
            this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        }
        const keys = withShift
            ? [Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter.KEY_V]
            : [Clutter.KEY_Control_L, Clutter.KEY_v];
        let time = GLib.get_monotonic_time();
        for (const key of keys)
            this._keyboard.notify_keyval(time++, key, Clutter.KeyState.PRESSED);
        for (const key of keys.reverse())
            this._keyboard.notify_keyval(time++, key, Clutter.KeyState.RELEASED);
    }

    destroy() {
        this._keyboard?.run_dispose();
        this._keyboard = null;
        this._settings = null;
    }
}
