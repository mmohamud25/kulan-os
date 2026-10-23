// Clipboard history: the last few things you copied, one shortcut away.
// Choosing one pastes it into the window you were in (Ctrl+Shift+V in
// terminals); when that window can't be reached, it's copied instead.
// Kept in memory only: never written to disk, cleared when the screen locks
// or you log out, and passwords copied from a password manager are skipped.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';

import {TERMINALS} from './assistant.js';

// How long the window gets to take the keyboard back after the dialog closes.
const FOCUS_WAIT_MS = 150;

const MAX_ITEMS = 25;
const MAX_LENGTH = 100000;
const PREVIEW_LENGTH = 90;
const TEXT_TYPES = ['text/plain;charset=utf-8', 'text/plain', 'UTF8_STRING', 'STRING', 'TEXT'];
// Set by KeePassXC, Bitwarden and other password managers on secrets.
const SECRET_TYPES = ['x-kde-passwordManagerHint'];

function preview(text) {
    const line = text.trim().split('\n')[0].replace(/\s+/g, ' ');
    const more = text.trim().includes('\n') || line.length > PREVIEW_LENGTH;
    return `${line.slice(0, PREVIEW_LENGTH)}${more ? '…' : ''}`;
}

const HistoryDialog = GObject.registerClass(
class HistoryDialog extends ModalDialog.ModalDialog {
    _init(items, onPick, onClear) {
        super._init({styleClass: 'kulan-clipboard-dialog', destroyOnClose: true});

        const title = new St.Label({text: 'Clipboard', style_class: 'kulan-clipboard-title'});
        this.contentLayout.add_child(title);

        const list = new St.BoxLayout({style_class: 'kulan-clipboard-list'});
        // GNOME 48 renamed "vertical" to "orientation".
        if ('orientation' in list)
            list.orientation = Clutter.Orientation.VERTICAL;
        else
            list.vertical = true;
        let first = null;
        if (items.length === 0) {
            list.add_child(new St.Label({
                text: 'Nothing copied yet. Things you copy appear here until you lock the screen.',
                style_class: 'kulan-clipboard-empty',
            }));
        }
        for (const text of items) {
            const label = new St.Label({text: preview(text), x_align: Clutter.ActorAlign.START, x_expand: true});
            label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            const button = new St.Button({
                child: label,
                style_class: 'kulan-clipboard-item',
                can_focus: true,
                x_expand: true,
                x_align: Clutter.ActorAlign.FILL,
                accessible_name: preview(text),
            });
            button.connect('clicked', () => {
                onPick(text);
                this.close();
            });
            list.add_child(button);
            first ??= button;
        }
        const scroll = new St.ScrollView({
            style_class: 'kulan-clipboard-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        scroll.set_child(list);
        this.contentLayout.add_child(scroll);

        this.setButtons([
            {label: 'Clear', action: () => {
                onClear();
                this.close();
            }},
            {label: 'Close', action: () => this.close(), key: Clutter.KEY_Escape, isDefault: items.length === 0},
        ]);
        if (first)
            this.setInitialKeyFocus(first);
    }
});

export class ClipboardHistory {
    constructor() {
        this._items = [];
        this._dialog = null;
        this._clipboard = St.Clipboard.get_default();
        this._selection = global.display.get_selection();
        this._ownerId = this._selection.connect('owner-changed', (_s, type, source) => {
            if (type === Meta.SelectionType.SELECTION_CLIPBOARD)
                this._onCopied(source);
        });
    }

    _onCopied(source) {
        if (!source)
            return;
        const types = source.get_mimetypes();
        if (types.some(t => SECRET_TYPES.includes(t)) || !types.some(t => TEXT_TYPES.includes(t)))
            return;
        this._clipboard.get_text(St.ClipboardType.CLIPBOARD, (_c, text) => {
            if (!this._selection || !text || !text.trim() || text.length > MAX_LENGTH)
                return;
            this._items = [text, ...this._items.filter(t => t !== text)].slice(0, MAX_ITEMS);
        });
    }

    show() {
        if (this._dialog)
            return;
        const window = global.display.focus_window;
        let picked = null;
        this._dialog = new HistoryDialog([...this._items],
            text => {
                this._clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
                picked = text;
            },
            () => this.clear());
        this._dialog.connect('destroy', () => {
            this._dialog = null;
            if (picked !== null)
                this._pasteInto(window);
        });
        this._dialog.open();
    }

    // Paste into the window that was in front, once it has the keyboard back.
    _pasteInto(window) {
        const copiedOnly = () => Main.notify('Clipboard', 'Copied. Press Ctrl+V to paste.');
        if (!window || window.is_override_redirect?.() || !this._selection) {
            copiedOnly();
            return;
        }
        try {
            window.activate(global.get_current_time());
        } catch {
            copiedOnly();
            return;
        }
        this._pasteSource = GLib.timeout_add(GLib.PRIORITY_DEFAULT, FOCUS_WAIT_MS, () => {
            this._pasteSource = 0;
            if (!this._selection || global.display.focus_window !== window) {
                copiedOnly();
                return GLib.SOURCE_REMOVE;
            }
            const app = Shell.WindowTracker.get_default().get_window_app(window);
            const terminal = TERMINALS.test(app?.get_id() ?? '') || TERMINALS.test(window.get_wm_class() ?? '');
            this._pressPaste(terminal);
            return GLib.SOURCE_REMOVE;
        });
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

    clear() {
        this._items = [];
    }

    destroy() {
        if (this._pasteSource) {
            GLib.source_remove(this._pasteSource);
            this._pasteSource = 0;
        }
        this._keyboard?.run_dispose();
        this._keyboard = null;
        this._dialog?.close();
        if (this._ownerId) {
            this._selection.disconnect(this._ownerId);
            this._ownerId = 0;
        }
        this._selection = null;
        this._items = [];
    }
}
