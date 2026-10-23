// Quick Note (Cmd+Shift+N, or click the Note widget): a note that opens over
// whatever you're doing, as on a Mac. What you type is saved as you go to
// ~/.local/share/kulan/quick-note.txt (only on this computer). Escape or the
// shortcut again closes it. If saving fails (a full disk), the note says so and
// keeps the text open; after a large deletion, Undo brings the text back.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {NOTE_FILE} from './widgets.js';

const SAVE_DELAY_MS = 600;
// Deleting at least this much at once (select all, delete) offers Undo.
const UNDO_AFTER_CHARS = 40;
const UNDO_SHOW_MS = 10000;
const MAX_CHARS = 20000;
const SUPER = Clutter.ModifierType.SUPER_MASK | Clutter.ModifierType.MOD4_MASK;

function readNote() {
    try {
        const [ok, bytes] = GLib.file_get_contents(NOTE_FILE);
        return ok ? new TextDecoder().decode(bytes) : '';
    } catch {
        return '';
    }
}

// Returns null when saved, or why it couldn't be.
export function writeNote(text) {
    try {
        GLib.mkdir_with_parents(GLib.path_get_dirname(NOTE_FILE), 0o700);
        // Written whole and renamed into place, so a crash never leaves half a note.
        const file = Gio.File.new_for_path(NOTE_FILE);
        file.replace_contents(new TextEncoder().encode(text), null, false,
            Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        return null;
    } catch (e) {
        logError(e, 'Kulan Shell: could not save the quick note');
        return e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NO_SPACE)
            ? 'the disk is full' : (e.message || 'unknown error');
    }
}

export class QuickNote {
    constructor() {
        this._backdrop = null;
        this._grab = null;
        this._saveTimer = 0;
        this._undoTimer = 0;
        this._saveError = null;
    }

    get isOpen() {
        return this._backdrop !== null;
    }

    toggle() {
        if (this.isOpen)
            this.close();
        else
            this.open();
    }

    open() {
        if (this.isOpen || Main.sessionMode.isLocked)
            return;
        if (Main.overview.visible)
            Main.overview.hide();
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const width = Math.min(460, monitor.width - 32);
        const height = Math.min(380, monitor.height - 120);

        this._backdrop = new St.Widget({
            reactive: true, x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height,
            style_class: 'kulan-spotlight-backdrop',
        });
        this._panel = new St.BoxLayout({
            vertical: true, style_class: 'kulan-quicknote', width, height,
            x: Math.round((monitor.width - width) / 2), y: Math.round(monitor.height * 0.16),
        });
        const head = new St.BoxLayout({style_class: 'kulan-quicknote-head'});
        head.add_child(new St.Icon({icon_name: 'document-edit-symbolic', style_class: 'kulan-widget-icon'}));
        head.add_child(new St.Label({text: 'Quick Note', style_class: 'kulan-quicknote-title', x_expand: true,
            y_align: Clutter.ActorAlign.CENTER}));
        this._undo = new St.Button({label: 'Undo', style_class: 'kulan-todo-clear', can_focus: true,
            visible: false});
        this._undo.connect('clicked', () => this._undoDelete());
        head.add_child(this._undo);
        const done = new St.Button({label: 'Done', style_class: 'kulan-quicknote-done', can_focus: true});
        done.connect('clicked', () => this.close());
        head.add_child(done);
        this._panel.add_child(head);
        this._status = new St.Label({style_class: 'kulan-quicknote-status', visible: false, x_expand: true});
        this._status.clutter_text.line_wrap = true;
        this._panel.add_child(this._status);

        this._entry = new St.Entry({
            style_class: 'kulan-quicknote-entry', can_focus: true, x_expand: true, y_expand: false,
            hint_text: 'Write anything. It’s saved as you type.',
        });
        const text = this._entry.clutter_text;
        text.single_line_mode = false;
        text.activatable = false;
        text.line_wrap = true;
        text.max_length = MAX_CHARS;
        // Text that couldn't be saved last time comes back, not the older saved copy.
        this._entry.set_text(this._unsaved ?? readNote());
        this._lastText = this._entry.get_text();
        text.connect('text-changed', () => {
            const now = this._entry.get_text();
            if (this._lastText.length - now.length >= UNDO_AFTER_CHARS)
                this._offerUndo(this._lastText);
            this._lastText = now;
            this._queueSave();
        });
        text.connect('key-press-event', (_a, event) => {
            const symbol = event.get_key_symbol();
            const state = event.get_state();
            if (symbol === Clutter.KEY_Escape ||
                ((symbol === Clutter.KEY_n || symbol === Clutter.KEY_N) && (state & SUPER) &&
                 (state & Clutter.ModifierType.SHIFT_MASK))) {
                this.close();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        const scroll = new St.ScrollView({
            style_class: 'kulan-quicknote-scroll', x_expand: true, y_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        // A scroll view needs a scrollable child: the entry goes in a box.
        const box = new St.BoxLayout({vertical: true, x_expand: true, y_expand: true,
            style_class: 'kulan-quicknote-field', reactive: true});
        // Clicking the empty part of the note puts the cursor at the end.
        box.connect('button-press-event', () => {
            this._entry?.grab_key_focus();
            return Clutter.EVENT_PROPAGATE;
        });
        box.add_child(this._entry);
        scroll.child = box;
        this._panel.add_child(scroll);
        this._backdrop.add_child(this._panel);
        this._backdrop.connect('button-press-event', (_actor, event) => {
            const [x, y] = event.get_coords();
            const [px, py] = this._panel.get_transformed_position();
            const [pw, ph] = this._panel.get_transformed_size();
            if (x < px || x > px + pw || y < py || y > py + ph)
                this.close();
            return Clutter.EVENT_PROPAGATE;
        });

        Main.layoutManager.modalDialogGroup.add_child(this._backdrop);
        this._grab = Main.pushModal(this._backdrop, {actionMode: Shell.ActionMode.SYSTEM_MODAL});
        if ((this._grab.get_seat_state() & Clutter.GrabState.KEYBOARD) === 0) {
            this.close();
            return;
        }
        this._entry.grab_key_focus();
        text.set_cursor_position(-1); // carry on at the end
        if (this._unsaved !== undefined)
            this._save(); // try again now
        this._panel.opacity = 0;
        this._panel.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    _queueSave() {
        if (this._saveTimer)
            GLib.source_remove(this._saveTimer);
        this._saveTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, SAVE_DELAY_MS, () => {
            this._saveTimer = 0;
            this._save();
            return GLib.SOURCE_REMOVE;
        });
    }

    _save() {
        if (!this._entry)
            return;
        this._saveError = writeNote(this._entry.get_text());
        if (this._saveError === null)
            this._unsaved = undefined;
        if (this._status) {
            this._status.visible = this._saveError !== null;
            this._status.text = this._saveError
                ? `Couldn’t save the note (${this._saveError}). What you typed is still here; it’s saved again as you type.`
                : '';
        }
    }

    _offerUndo(before) {
        this._undoText = before;
        this._undo.visible = true;
        if (this._undoTimer)
            GLib.source_remove(this._undoTimer);
        this._undoTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, UNDO_SHOW_MS, () => {
            this._undoTimer = 0;
            if (this._undo)
                this._undo.visible = false;
            return GLib.SOURCE_REMOVE;
        });
    }

    _undoDelete() {
        if (this._undoText === undefined || !this._entry)
            return;
        const text = this._undoText;
        this._undoText = undefined;
        this._undo.visible = false;
        this._entry.set_text(text);
        this._entry.clutter_text.set_cursor_position(-1);
        this._entry.grab_key_focus();
    }

    close() {
        if (!this._backdrop)
            return;
        if (this._saveTimer) {
            GLib.source_remove(this._saveTimer);
            this._saveTimer = 0;
            this._save(); // what was typed last is never lost
        }
        if (this._undoTimer) {
            GLib.source_remove(this._undoTimer);
            this._undoTimer = 0;
        }
        if (this._saveError) {
            // Closed while the last save failed: keep the text (in memory, until
            // you log out) and say so where it's still seen.
            this._unsaved = this._entry.get_text();
            Main.notify('Quick Note isn’t saved yet', `Your note couldn’t be saved (${this._saveError}). ` +
                'It’s kept until you log out: free up some space, then open Quick Note again to save it.');
        }
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        this._backdrop.destroy();
        this._backdrop = this._panel = this._entry = this._status = this._undo = null;
        this._undoText = undefined;
    }

    destroy() {
        this.close();
    }
}
