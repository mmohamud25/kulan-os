// To-do: a short task list kept in ~/.local/share/kulan/todo.json (only on
// this computer). The To-do widget shows what's left and ticks tasks off;
// clicking the card opens this panel over everything, to add, tick, remove
// and clear tasks. Escape or Done closes it. Removing or clearing tasks offers
// Undo; if the list can't be saved (a full disk), it says so.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export const TODO_FILE = GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan', 'todo.json']);
const MAX_TASKS = 200;
const MAX_CHARS = 200;
const UNDO_SHOW_MS = 10000;
let lastError = null;

function saveFailed() {
    Main.notify('To-do wasn’t saved', `The change couldn’t be saved (${lastError}). Free up some space and try again.`);
}

export function readTodos() {
    try {
        const [ok, bytes] = GLib.file_get_contents(TODO_FILE);
        if (!ok)
            return [];
        const data = JSON.parse(new TextDecoder().decode(bytes));
        const items = Array.isArray(data?.items) ? data.items : [];
        return items.filter(i => i && typeof i.text === 'string' && i.text.trim() !== '')
            .map(i => ({id: String(i.id ?? GLib.uuid_string_random()), text: i.text.slice(0, MAX_CHARS), done: !!i.done}))
            .slice(0, MAX_TASKS);
    } catch {
        return []; // missing or damaged: start a fresh list
    }
}

export function writeTodos(items) {
    try {
        GLib.mkdir_with_parents(GLib.path_get_dirname(TODO_FILE), 0o700);
        const text = JSON.stringify({version: 1, items: items.slice(0, MAX_TASKS)}, null, 1);
        // Written whole and renamed into place, so a crash never leaves half a list.
        Gio.File.new_for_path(TODO_FILE).replace_contents(new TextEncoder().encode(text), null, false,
            Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        lastError = null;
        return true;
    } catch (e) {
        logError(e, 'Kulan Shell: could not save the to-do list');
        lastError = e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NO_SPACE)
            ? 'the disk is full' : (e.message || 'unknown error');
        return false;
    }
}

export function setDone(id, done) {
    const items = readTodos();
    const item = items.find(i => i.id === id);
    if (!item)
        return;
    item.done = done;
    if (!writeTodos(items))
        saveFailed();
}

// A round tick box, as in Reminders.
export function checkButton(done, onToggle, accessibleName) {
    const button = new St.Button({
        style_class: done ? 'kulan-todo-check kulan-todo-check-done' : 'kulan-todo-check',
        can_focus: true, y_align: Clutter.ActorAlign.CENTER, accessible_name: accessibleName,
        child: new St.Icon({icon_name: 'object-select-symbolic', style_class: 'kulan-todo-check-icon',
            opacity: done ? 255 : 0}),
    });
    button.connect('clicked', () => onToggle(!done));
    return button;
}

export class TodoPanel {
    constructor() {
        this._backdrop = null;
        this._grab = null;
        this._undoTimer = 0;
        this._undoItems = null;
    }

    get isOpen() {
        return this._backdrop !== null;
    }

    open() {
        if (this.isOpen || Main.sessionMode.isLocked)
            return;
        if (Main.overview.visible)
            Main.overview.hide();
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const width = Math.min(440, monitor.width - 32);
        const height = Math.min(460, monitor.height - 120);

        this._backdrop = new St.Widget({
            reactive: true, x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height,
            style_class: 'kulan-spotlight-backdrop',
        });
        this._panel = new St.BoxLayout({
            vertical: true, style_class: 'kulan-quicknote kulan-todo', width, height,
            x: Math.round((monitor.width - width) / 2), y: Math.round(monitor.height * 0.14),
        });
        const head = new St.BoxLayout({style_class: 'kulan-quicknote-head'});
        head.add_child(new St.Icon({icon_name: 'checkbox-checked-symbolic', style_class: 'kulan-widget-icon'}));
        head.add_child(new St.Label({text: 'To-do', style_class: 'kulan-quicknote-title', x_expand: true,
            y_align: Clutter.ActorAlign.CENTER}));
        const done = new St.Button({label: 'Done', style_class: 'kulan-quicknote-done', can_focus: true});
        done.connect('clicked', () => this.close());
        head.add_child(done);
        this._panel.add_child(head);

        this._entry = new St.Entry({
            style_class: 'kulan-todo-entry', can_focus: true, x_expand: true,
            hint_text: 'Add a task, then press Enter',
        });
        this._entry.clutter_text.max_length = MAX_CHARS;
        this._entry.clutter_text.connect('activate', () => this._add());
        this._entry.clutter_text.connect('key-press-event', (_a, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape) {
                this.close();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._panel.add_child(this._entry);

        this._list = new St.BoxLayout({vertical: true, style_class: 'kulan-todo-list', x_expand: true});
        const scroll = new St.ScrollView({
            style_class: 'kulan-quicknote-scroll', x_expand: true, y_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        scroll.child = this._list;
        this._panel.add_child(scroll);

        this._status = new St.Label({style_class: 'kulan-quicknote-status', visible: false, x_expand: true});
        this._status.clutter_text.line_wrap = true;
        this._panel.add_child(this._status);

        const foot = new St.BoxLayout({x_expand: true});
        this._undo = new St.Button({label: 'Undo', style_class: 'kulan-todo-clear', can_focus: true, visible: false});
        this._undo.connect('clicked', () => this._undoChange());
        foot.add_child(this._undo);
        foot.add_child(new St.Widget({x_expand: true}));
        this._clear = new St.Button({label: 'Clear completed', style_class: 'kulan-todo-clear', can_focus: true,
            x_align: Clutter.ActorAlign.END});
        this._clear.connect('clicked', () => this._change(items => items.filter(i => !i.done)));
        foot.add_child(this._clear);
        this._panel.add_child(foot);

        this._backdrop.add_child(this._panel);
        this._backdrop.connect('button-press-event', (_actor, event) => {
            const [x, y] = event.get_coords();
            const [px, py] = this._panel.get_transformed_position();
            const [pw, ph] = this._panel.get_transformed_size();
            if (x < px || x > px + pw || y < py || y > py + ph)
                this.close();
            return Clutter.EVENT_PROPAGATE;
        });
        this._backdrop.connect('key-press-event', (_a, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape) {
                this.close();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        Main.layoutManager.modalDialogGroup.add_child(this._backdrop);
        this._grab = Main.pushModal(this._backdrop, {actionMode: Shell.ActionMode.SYSTEM_MODAL});
        if ((this._grab.get_seat_state() & Clutter.GrabState.KEYBOARD) === 0) {
            this.close();
            return;
        }
        this._refresh();
        this._entry.grab_key_focus();
        this._panel.opacity = 0;
        this._panel.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    _add() {
        const text = this._entry.get_text().trim().replace(/\s+/g, ' ');
        if (!text)
            return;
        const items = readTodos();
        if (items.length >= MAX_TASKS) {
            Main.notify('To-do is full', `Clear completed tasks to add more (up to ${MAX_TASKS}).`);
            return;
        }
        // New tasks go to the top of what's left, as people expect.
        items.unshift({id: GLib.uuid_string_random(), text, done: false});
        if (writeTodos(items))
            this._entry.set_text('');
        this._refresh();
    }

    // Remove or clear tasks, with Undo for a few seconds.
    _change(edit) {
        const before = readTodos();
        if (!writeTodos(edit(before.map(i => ({...i}))))) {
            this._refresh();
            return;
        }
        this._undoItems = before;
        this._undo.visible = true;
        if (this._undoTimer)
            GLib.source_remove(this._undoTimer);
        this._undoTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, UNDO_SHOW_MS, () => {
            this._undoTimer = 0;
            this._undoItems = null;
            if (this._undo)
                this._undo.visible = false;
            return GLib.SOURCE_REMOVE;
        });
        this._refresh();
    }

    _undoChange() {
        if (!this._undoItems)
            return;
        if (writeTodos(this._undoItems)) {
            this._undoItems = null;
            this._undo.visible = false;
        }
        this._refresh();
    }

    _refresh() {
        if (!this._list)
            return;
        this._list.destroy_all_children();
        const items = readTodos();
        const ordered = [...items.filter(i => !i.done), ...items.filter(i => i.done)];
        if (ordered.length === 0) {
            this._list.add_child(new St.Label({text: 'Nothing to do yet.', style_class: 'kulan-todo-empty'}));
        }
        for (const item of ordered) {
            const row = new St.BoxLayout({style_class: 'kulan-todo-row', x_expand: true});
            row.add_child(checkButton(item.done, done => {
                const items = readTodos();
                const found = items.find(i => i.id === item.id);
                if (found) {
                    found.done = done;
                    writeTodos(items);
                }
                this._refresh();
            }, item.done ? `Mark “${item.text}” as not done` : `Mark “${item.text}” as done`));
            const text = new St.Label({style_class: item.done ? 'kulan-todo-text kulan-todo-text-done' : 'kulan-todo-text',
                x_expand: true, y_align: Clutter.ActorAlign.CENTER});
            text.clutter_text.set_markup(item.done ? `<s>${GLib.markup_escape_text(item.text, -1)}</s>`
                : GLib.markup_escape_text(item.text, -1));
            text.clutter_text.line_wrap = true;
            row.add_child(text);
            const remove = new St.Button({style_class: 'kulan-todo-remove', can_focus: true,
                accessible_name: `Remove “${item.text}”`,
                child: new St.Icon({icon_name: 'window-close-symbolic', icon_size: 14})});
            remove.connect('clicked', () => this._change(all => all.filter(i => i.id !== item.id)));
            row.add_child(remove);
            this._list.add_child(row);
        }
        this._clear.visible = items.some(i => i.done);
        this._status.visible = lastError !== null;
        this._status.text = lastError ? `Couldn’t save the list (${lastError}). Free up some space and try again.` : '';
    }

    close() {
        if (!this._backdrop)
            return;
        // Whatever was typed but not added yet is added, so it's never lost.
        if (this._entry?.get_text().trim())
            this._add();
        if (this._undoTimer) {
            GLib.source_remove(this._undoTimer);
            this._undoTimer = 0;
        }
        this._undoItems = null;
        if (lastError)
            saveFailed();
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        this._backdrop.destroy();
        this._backdrop = this._panel = this._entry = this._list = this._clear = this._status = this._undo = null;
    }

    destroy() {
        this.close();
    }
}
