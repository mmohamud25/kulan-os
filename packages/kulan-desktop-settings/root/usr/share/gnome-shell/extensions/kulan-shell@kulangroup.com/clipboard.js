// Clipboard history: the last few things you copied, one shortcut away.
// Kept in memory only: never written to disk, cleared when the screen locks
// or you log out, and passwords copied from a password manager are skipped.

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';

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
        this._dialog = new HistoryDialog([...this._items],
            text => {
                this._clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
                Main.notify('Clipboard', 'Copied. Press Ctrl+V to paste.');
            },
            () => this.clear());
        this._dialog.connect('destroy', () => {
            this._dialog = null;
        });
        this._dialog.open();
    }

    clear() {
        this._items = [];
    }

    destroy() {
        this._dialog?.close();
        if (this._ownerId) {
            this._selection.disconnect(this._ownerId);
            this._ownerId = 0;
        }
        this._selection = null;
        this._items = [];
    }
}
