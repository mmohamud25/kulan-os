// Ask Kulan (Shift+Super+Space): a panel over whatever you're doing. Ask a
// question ("why is my laptop slow?") or say what you'd like done ("tidy my
// Downloads", "remind me at 5 to call Amina"). The assistant runs on this
// computer (the kulan-assistant package's Ask service, over the session bus).
// Anything that changes the computer is shown first and waits for Do it, and
// most things can be undone. Escape or the shortcut again closes it.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'org.kulan.Assistant.Ask';
const OBJECT_PATH = '/org/kulan/Assistant/Ask';
const INTERFACE = 'org.kulan.Assistant.Ask';
// Loading a model and planning can take a while on a slow computer.
const CALL_TIMEOUT_MS = 5 * 60 * 1000;
const PANEL_WIDTH = 640;
const MAX_FOUND = 8;
const SUPER = Clutter.ModifierType.SUPER_MASK | Clutter.ModifierType.MOD4_MASK;
const EXAMPLES = ['Why is my computer slow?', 'Tidy up my Downloads', 'Remind me in 30 minutes to stretch',
    'Turn on dark mode', 'My Wi-Fi isn’t working'];

// org.kulan.Shell.Ask on the session bus: lets Kulan Assistant's search
// results ("Ask Kulan" in Activities) open this panel with the question.
const SHELL_INTERFACE = `
<node>
  <interface name="org.kulan.Shell.Ask">
    <method name="Open"><arg type="s" name="text" direction="in"/></method>
    <method name="Close"/>
  </interface>
</node>`;
const SHELL_PATH = '/org/kulan/Shell/Ask';

export class AskKulan {
    constructor() {
        this._backdrop = null;
        this._grab = null;
        this._cancellable = null;
        this._generation = 0;
        this._plan = null; // {id, text} while a plan waits for Do it
        this._dotsId = 0;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(SHELL_INTERFACE, {
            Open: text => this.open(String(text ?? '').slice(0, 500), true),
            Close: () => this.close(),
        });
        try {
            this._dbus.export(Gio.DBus.session, SHELL_PATH);
        } catch (e) {
            logError(e, 'Kulan Shell: Ask Kulan on the session bus');
            this._dbus = null;
        }
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

    open(text = '', ask = false) {
        if (Main.sessionMode.isLocked)
            return;
        if (this.isOpen) {
            if (text) {
                this._entry.set_text(text);
                if (ask)
                    this._ask();
            }
            return;
        }
        if (Main.overview.visible)
            Main.overview.hide();
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const width = Math.min(PANEL_WIDTH, monitor.width - 32);

        this._backdrop = new St.Widget({
            reactive: true, x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height,
            style_class: 'kulan-spotlight-backdrop',
        });
        this._panel = new St.BoxLayout({
            vertical: true, style_class: 'kulan-spotlight kulan-ask', width,
            x: Math.round((monitor.width - width) / 2), y: Math.round(monitor.height * 0.16),
        });
        this._entry = new St.Entry({
            hint_text: 'Ask Kulan, or say what you’d like done',
            can_focus: true, x_expand: true, style_class: 'kulan-spotlight-entry',
            primary_icon: new St.Icon({
                gicon: Gio.ThemedIcon.new_from_names(['org.kulan.Assistant', 'kulan-logo-symbolic']),
                style_class: 'kulan-ask-entry-icon',
            }),
        });
        this._entry.clutter_text.connect('key-press-event', (_a, event) => this._onKey(event));
        this._entry.clutter_text.connect('activate', () => this._onEnter());
        this._entry.clutter_text.connect('text-changed', () => {
            // Typing something new after a plan: Enter asks again.
            if (this._plan && this._entry.get_text() !== this._plan.text)
                this._plan = null;
        });

        this._body = new St.BoxLayout({vertical: true, style_class: 'kulan-ask-body', x_expand: true});
        this._panel.add_child(this._entry);
        this._panel.add_child(this._body);
        this._backdrop.add_child(this._panel);
        this._backdrop.connect('button-press-event', (_a, event) => {
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
            this.close(); // something else holds the keyboard
            return;
        }
        this._entry.grab_key_focus();
        this._panel.opacity = 0;
        this._panel.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        if (text) {
            this._entry.set_text(text);
            if (ask) {
                this._ask();
                return;
            }
        }
        this._showExamples();
    }

    close() {
        if (!this._backdrop)
            return;
        this._cancellable?.cancel();
        this._cancellable = null;
        this._generation++;
        this._stopDots();
        this._plan = null;
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        this._backdrop.destroy();
        this._backdrop = null;
        this._panel = this._entry = this._body = null;
    }

    destroy() {
        this.close();
        this._dbus?.unexport();
        this._dbus = null;
    }

    // --- keys -----------------------------------------------------------------

    _onKey(event) {
        const symbol = event.get_key_symbol();
        const state = event.get_state();
        if (symbol === Clutter.KEY_Escape) {
            this.close();
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_space && (state & SUPER) && (state & Clutter.ModifierType.SHIFT_MASK)) {
            this.close(); // the shortcut again closes it
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _onEnter() {
        // Enter on the same words as the plan waiting: Do it.
        if (this._plan && this._entry.get_text() === this._plan.text)
            this._run();
        else
            this._ask();
    }

    // --- talking to the assistant ----------------------------------------------

    _call(method, args, onReply) {
        this._cancellable?.cancel();
        const cancellable = new Gio.Cancellable();
        this._cancellable = cancellable;
        const generation = ++this._generation;
        Gio.DBus.session.call(BUS_NAME, OBJECT_PATH, INTERFACE, method, args, new GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, cancellable, (connection, result) => {
                let reply;
                try {
                    const [text] = connection.call_finish(result).deepUnpack();
                    reply = JSON.parse(text);
                } catch (e) {
                    if (e instanceof GLib.Error && e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        return;
                    reply = e instanceof GLib.Error && Gio.DBusError.get_remote_error(e) ===
                        'org.freedesktop.DBus.Error.ServiceUnknown'
                        ? {error: 'Kulan Assistant isn’t installed. Turn it on in Kulan Settings › Add-ons.', settings: 'add-ons'}
                        : {error: 'Kulan Assistant didn’t answer. Try again in a moment.'};
                    if (!(e instanceof GLib.Error))
                        logError(e, 'Kulan Shell: Ask Kulan');
                }
                if (generation !== this._generation || !this._body)
                    return;
                this._stopDots();
                onReply(reply ?? {});
            });
    }

    _ask() {
        const text = this._entry.get_text().trim();
        if (!text)
            return;
        this._plan = null;
        this._thinking('Thinking (on this computer)');
        this._call('Ask', new GLib.Variant('(s)', [text]), reply => this._showReply(text, reply));
    }

    _run() {
        const plan = this._plan;
        if (!plan)
            return;
        this._plan = null;
        this._thinking('Doing it');
        this._call('Run', new GLib.Variant('(s)', [plan.id]), reply => this._showResults(plan, reply));
    }

    _undo(plan) {
        this._thinking('Putting it back');
        this._call('Undo', new GLib.Variant('(s)', [plan.id]), reply => {
            this._clear();
            this._addText(reply.message || 'Put back as it was.', 'kulan-ask-reply');
            this._addButtons([['Close', () => this.close(), true]]);
        });
    }

    // --- showing ------------------------------------------------------------------

    _clear() {
        this._body?.destroy_all_children();
    }

    _addText(text, style) {
        const label = new St.Label({text, style_class: style, x_expand: true});
        label.clutter_text.line_wrap = true;
        label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        this._body.add_child(label);
        return label;
    }

    _addRow(iconName, title, subtitle, styleClass = '', onClick = null) {
        const row = new (onClick ? St.Button : St.BoxLayout)({
            style_class: `kulan-ask-step ${styleClass}`, x_expand: true, reactive: Boolean(onClick),
            can_focus: Boolean(onClick),
        });
        const box = new St.BoxLayout({style_class: 'kulan-spotlight-row-box', x_expand: true});
        box.add_child(new St.Icon({icon_name: iconName, style_class: 'kulan-ask-step-icon'}));
        const labels = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        const name = new St.Label({text: title, style_class: 'kulan-ask-step-title'});
        name.clutter_text.line_wrap = true;
        labels.add_child(name);
        if (subtitle) {
            const detail = new St.Label({text: subtitle, style_class: 'kulan-spotlight-description'});
            detail.clutter_text.line_wrap = true;
            labels.add_child(detail);
        }
        box.add_child(labels);
        if (onClick) {
            row.set_child(box);
            row.connect('clicked', onClick);
        } else {
            row.add_child(box);
        }
        this._body.add_child(row);
    }

    _addButtons(buttons) {
        const bar = new St.BoxLayout({style_class: 'kulan-ask-buttons', x_align: Clutter.ActorAlign.END, x_expand: true});
        for (const [label, action, primary] of buttons) {
            const button = new St.Button({
                label, can_focus: true,
                style_class: `kulan-ask-button${primary ? ' kulan-ask-button-primary' : ''}`,
            });
            button.connect('clicked', action);
            bar.add_child(button);
        }
        this._body.add_child(bar);
    }

    _thinking(words) {
        this._clear();
        this._stopDots();
        const label = this._addText(`${words}…`, 'kulan-ask-thinking');
        let dots = 0;
        this._dotsId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
            dots = (dots + 1) % 4;
            label.text = `${words}${'.'.repeat(dots).padEnd(3, ' ')}`;
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stopDots() {
        if (this._dotsId) {
            GLib.source_remove(this._dotsId);
            this._dotsId = 0;
        }
    }

    _showExamples() {
        this._clear();
        const chips = new St.Widget({
            style_class: 'kulan-ask-chips', x_expand: true,
            layout_manager: new Clutter.FlowLayout({column_spacing: 8, row_spacing: 8, homogeneous: false}),
        });
        for (const example of EXAMPLES) {
            const chip = new St.Button({label: example, style_class: 'kulan-ask-chip', can_focus: true});
            chip.connect('clicked', () => {
                this._entry.set_text(example);
                this._ask();
            });
            chips.add_child(chip);
        }
        this._body.add_child(chips);
        this._addText('Runs on this computer. Nothing you type leaves it.', 'kulan-ask-note');
    }

    _showError(reply) {
        this._clear();
        this._addText(reply.error, 'kulan-ask-reply');
        const buttons = [];
        if (reply.settings)
            buttons.push(['Open Kulan Settings', () => this._launch(['kulan-settings', reply.settings]), true]);
        buttons.push(['Close', () => this.close(), !reply.settings]);
        this._addButtons(buttons);
    }

    _showReply(text, reply) {
        if (reply.error) {
            this._showError(reply);
            return;
        }
        this._clear();
        if (reply.reply)
            this._addText(reply.reply, 'kulan-ask-reply');
        if (reply.results) { // nothing to confirm: already done
            this._showResults({id: reply.id, text}, reply, false);
            return;
        }
        for (const step of reply.steps ?? [])
            this._addRow(step.changes ? 'emblem-system-symbolic' : 'go-next-symbolic', step.text, '');
        if (reply.confirm) {
            this._plan = {id: reply.id, text: this._entry.get_text()};
            this._addButtons([
                ['Continue in Kulan Assistant', () => this._continue(text), false],
                ['Cancel', () => this.close(), false],
                ['Do it', () => this._run(), true],
            ]);
        } else {
            this._addButtons([
                ['Continue in Kulan Assistant', () => this._continue(text), false],
                ['Done', () => this.close(), true],
            ]);
        }
    }

    _showResults(plan, reply, clear = true) {
        if (reply.error) {
            this._showError(reply);
            return;
        }
        if (clear)
            this._clear();
        for (const result of reply.results ?? []) {
            this._addRow(result.ok ? 'object-select-symbolic' : 'dialog-warning-symbolic', result.text,
                result.message, result.ok ? 'kulan-ask-ok' : 'kulan-ask-warning');
            for (const path of (result.found ?? []).slice(0, MAX_FOUND)) {
                const file = Gio.File.new_for_path(path);
                this._addRow('document-open-symbolic', file.get_basename() ?? path,
                    GLib.path_get_dirname(path).replace(GLib.get_home_dir(), '~'), 'kulan-ask-file', () => {
                        this.close();
                        Gio.AppInfo.launch_default_for_uri_async(file.get_uri(), null, null, null);
                    });
            }
        }
        if (clear && reply.summary)
            this._addText(reply.summary, 'kulan-ask-note');
        const buttons = [];
        if (reply.undo)
            buttons.push(['Undo', () => this._undo(plan), false]);
        buttons.push(['Done', () => this.close(), true]);
        this._addButtons(buttons);
    }

    _continue(text) {
        this.close();
        this._launch(['kulan-assistant', 'do', text]);
    }

    _launch(argv) {
        try {
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
        } catch (e) {
            Main.notify('Kulan Assistant', 'Kulan Assistant isn’t installed. Turn it on in Kulan Settings › Add-ons.');
        }
    }
}
