// The card every desktop widget is drawn on, and small helpers they share.

import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import St from 'gi://St';

export function launch(argv) {
    try {
        Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
    } catch (e) {
        logError(e, `Kulan Shell: widget could not start ${argv[0]}`);
    }
}

export function launchApp(desktopId) {
    const app = Gio.DesktopAppInfo.new(desktopId);
    if (!app)
        return false;
    try {
        app.launch([], global.create_app_launch_context(0, -1));
        return true;
    } catch (e) {
        logError(e, `Kulan Shell: widget could not open ${desktopId}`);
        return false;
    }
}

export function label(text, styleClass, wrap = false) {
    const l = new St.Label({text, style_class: styleClass, x_expand: true});
    l.clutter_text.ellipsize = Pango.EllipsizeMode.END;
    if (wrap) {
        l.clutter_text.line_wrap = true;
        l.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
    }
    return l;
}

// One card: a button with a title row and a body that each widget fills.
export class Card {
    constructor(title, icon, onClick, kind = '') {
        this.actor = new St.Button({style_class: 'kulan-widget', reactive: true, can_focus: false, x_expand: false});
        if (kind)
            this.actor.add_style_class_name(`kulan-card-${kind}`); // e.g. the note's paper colour
        this._box = new St.BoxLayout({vertical: true, style_class: 'kulan-widget-box', x_expand: true});
        const head = new St.BoxLayout({style_class: 'kulan-widget-head'});
        head.add_child(new St.Icon({icon_name: icon, style_class: 'kulan-widget-icon'}));
        this._title = label(title, 'kulan-widget-title');
        head.add_child(this._title);
        this._box.add_child(head);
        this.body = new St.BoxLayout({vertical: true, style_class: 'kulan-widget-body', x_expand: true});
        this._box.add_child(this.body);
        this.actor.set_child(this._box);
        this.actor.connect('clicked', onClick);
    }

    setTitle(text) {
        this._title.text = text;
    }

    clear() {
        this.body.destroy_all_children();
    }

    destroy() {
        this.actor.destroy();
    }
}
