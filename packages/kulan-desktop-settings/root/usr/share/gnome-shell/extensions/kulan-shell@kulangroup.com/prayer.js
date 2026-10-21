// Prayer times (off until turned on in Kulan Settings > Prayer Times): the
// next prayer in the top bar, today's times in its menu, and a gentle
// notification when each prayer time comes. Calculated on this computer
// (praycalc.js) from the city chosen in Settings; nothing is looked up online.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {PRAYERS, methodForCountry, times} from './praycalc.js';

const NAMES = {fajr: 'Fajr', sunrise: 'Sunrise', dhuhr: 'Dhuhr', asr: 'Asr', maghrib: 'Maghrib', isha: 'Isha'};
// Sunrise is shown but isn't a prayer: no notification for it.
const NOTIFY = ['fajr', 'dhuhr', 'asr', 'maghrib', 'isha'];
const KEYS = ['prayer-times', 'prayer-method', 'prayer-asr', 'prayer-city', 'prayer-country',
    'prayer-latitude', 'prayer-longitude', 'prayer-notify'];
const TICK_S = 20;

// A day's prayers as moments (GLib.DateTime), in order. Worked out with the
// day's own UTC offset (taken at noon), so the days the clocks change are
// right too. A prayer the sun doesn't allow that day (midnight sun far
// north) is left out.
export function dayMoments(settings, day) {
    const y = day.get_year(), m = day.get_month(), d = day.get_day_of_month();
    const noon = GLib.DateTime.new_local(y, m, d, 12, 0, 0);
    const method = settings.get_string('prayer-method') || methodForCountry(settings.get_string('prayer-country'));
    const hours = times({year: y, month: m, day: d}, settings.get_double('prayer-latitude'),
        settings.get_double('prayer-longitude'), noon.get_utc_offset() / 3.6e9,
        {method, asr: settings.get_string('prayer-asr')});
    const moments = [];
    let previous = -1;
    for (const name of PRAYERS) {
        const h = hours[name];
        if (!Number.isFinite(h))
            continue;
        // In summer far north, isha can fall after midnight: it belongs to the next day.
        const date = h < previous ? noon.add_days(1) : noon;
        previous = Math.max(previous, h);
        let minutes = Math.round(h * 60);
        let when = date;
        if (minutes >= 24 * 60) {
            minutes -= 24 * 60;
            when = date.add_days(1);
        }
        moments.push({name, day: d, when: GLib.DateTime.new_local(when.get_year(), when.get_month(),
            when.get_day_of_month(), Math.floor(minutes / 60), minutes % 60, 0)});
    }
    return moments;
}

export class PrayerTimes {
    constructor(settings) {
        this._settings = settings;
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._indicator = null;
        this._timer = 0;
        this._notified = new Set();
        this._ids = KEYS.filter(k => settings.settings_schema.has_key(k))
            .map(k => settings.connect(`changed::${k}`, () => this._sync()));
        this._clockId = this._interface.connect('changed::clock-format', () => this._update());
        this._sync();
    }

    get _ready() {
        const s = this._settings;
        return s.settings_schema.has_key('prayer-times') && s.get_boolean('prayer-times') &&
            s.get_string('prayer-city') !== '';
    }

    _sync() {
        if (this._ready && !this._indicator)
            this._build();
        else if (!this._ready && this._indicator)
            this._teardown();
        this._update();
    }

    _build() {
        this._indicator = new PanelMenu.Button(0.0, 'Prayer Times');
        this._label = new St.Label({text: '', y_align: Clutter.ActorAlign.CENTER, style_class: 'kulan-prayer-label'});
        this._indicator.add_child(this._label);
        this._rows = {};
        this._place = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._indicator.menu.addMenuItem(this._place);
        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        for (const name of PRAYERS) {
            const item = new PopupMenu.PopupMenuItem(NAMES[name], {reactive: false});
            const time = new St.Label({text: '', x_expand: true, x_align: Clutter.ActorAlign.END});
            item.add_child(time);
            this._rows[name] = {item, time};
            this._indicator.menu.addMenuItem(item);
        }
        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._indicator.menu.addAction('Prayer Times Settings', () => {
            try {
                Gio.Subprocess.new(['kulan-settings', 'prayer-times'], Gio.SubprocessFlags.NONE);
            } catch (e) {
                logError(e, 'Kulan Shell: prayer settings');
            }
        });
        Main.panel.addToStatusArea('kulan-prayer-times', this._indicator, 0, 'right');
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, TICK_S, () => {
            this._update();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _teardown() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        this._indicator?.destroy();
        this._indicator = null;
        this._label = null;
        this._rows = null;
        this._notified.clear();
    }

    _format(dateTime) {
        return this._interface.get_string('clock-format') === '12h'
            ? dateTime.format('%l:%M %p').trim() : dateTime.format('%H:%M');
    }

    _update() {
        if (!this._indicator)
            return;
        const now = GLib.DateTime.new_now_local();
        // Yesterday's too: its isha can come after midnight.
        const yesterday = dayMoments(this._settings, now.add_days(-1));
        const today = dayMoments(this._settings, now);
        const tomorrow = dayMoments(this._settings, now.add_days(1));
        const all = [...yesterday, ...today, ...tomorrow];
        const next = all.find(m => m.when.compare(now) > 0 && m.name !== 'sunrise');
        this._label.set_text(next ? `${NAMES[next.name]} ${this._format(next.when)}` : 'Prayer times');
        this._place.label.set_text(`${this._settings.get_string('prayer-city')} · today`);
        for (const name of PRAYERS) {
            const row = this._rows[name];
            const moment = today.find(m => m.name === name);
            row.time.set_text(moment ? this._format(moment.when) : '—');
            if (moment && next && moment.when.equal(next.when))
                row.item.add_style_class_name('kulan-prayer-next');
            else
                row.item.remove_style_class_name('kulan-prayer-next');
        }
        this._maybeNotify(now, all);
    }

    // When a prayer's time arrives (within the last two ticks), once.
    _maybeNotify(now, moments) {
        if (!this._settings.get_boolean('prayer-notify'))
            return;
        for (const {name, when} of moments) {
            if (!NOTIFY.includes(name))
                continue;
            const since = now.difference(when) / 1e6; // seconds since the prayer time
            const key = `${when.format('%F %R')}-${name}`;
            if (since >= 0 && since < TICK_S * 2 + 5 && !this._notified.has(key)) {
                this._notified.add(key);
                Main.notify(`${NAMES[name]}`, `It’s time for ${NAMES[name]} (${this._format(when)}).`);
                global.display.get_sound_player().play_from_theme('complete', `${NAMES[name]} prayer time`, null);
            }
        }
        // Forget only what is long past.
        const cutoff = now.add_days(-2).format('%F');
        for (const key of this._notified) {
            if (key.slice(0, 10) < cutoff)
                this._notified.delete(key);
        }
    }

    destroy() {
        for (const id of this._ids)
            this._settings.disconnect(id);
        this._ids = [];
        if (this._clockId) {
            this._interface.disconnect(this._clockId);
            this._clockId = 0;
        }
        this._teardown();
    }
}
