// Desktop widgets, as on a Mac: small cards at the top left of the desktop
// (desktop icons start at the top right). Calendar (today and what's next),
// Weather (GNOME Weather's location), Prayer Times (only when turned on), a
// note, and the ones in morewidgets.js: Clock, System, To-do, Now Playing. Each opens its app when clicked. Windows cover them, as on a Mac;
// they sit just above the desktop icons layer, so they can be clicked.
// Chosen in Kulan Settings > Desktop and windows (org.kulan.desktop
// desktop-widgets).

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import GWeather from 'gi://GWeather?version=4.0';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Calendar from 'resource:///org/gnome/shell/ui/calendar.js';
import * as Weather from 'resource:///org/gnome/shell/misc/weather.js';

import {Card, label, launch, launchApp} from './cards.js';
import {ClockWidget, MediaWidget, SystemWidget, TodoWidget} from './morewidgets.js';
import {dayMoments} from './prayer.js';

const MARGIN = 24;
const GAP = 14;
const TICK_S = 30;
const MAX_EVENTS = 3;
const NOTE_FILE = GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan', 'quick-note.txt']);
const PRAYER_NAMES = {fajr: 'Fajr', sunrise: 'Sunrise', dhuhr: 'Dhuhr', asr: 'Asr', maghrib: 'Maghrib', isha: 'Isha'};

class CalendarWidget {
    constructor() {
        this.card = new Card('Calendar', 'x-office-calendar-symbolic', () => launchApp('org.gnome.Calendar.desktop'), 'calendar');
        this._source = null;
        try {
            this._source = new Calendar.DBusEventSource();
            this._sourceId = this._source.connect('changed', () => this.update());
        } catch (e) {
            logError(e, 'Kulan Shell: calendar widget events');
        }
        this.update();
    }

    update() {
        // requestRange() can emit 'changed' straight away, which calls update()
        // again in the middle of this one; without the guard both runs add
        // their rows ("Nothing else today" twice).
        if (this._updating)
            return;
        this._updating = true;
        try {
            this._update();
        } finally {
            this._updating = false;
        }
    }

    _update() {
        const now = GLib.DateTime.new_now_local();
        this.card.setTitle(now.format('%A').toUpperCase());
        this.card.clear();
        this.card.body.add_child(label(now.format('%-d'), 'kulan-widget-big'));
        this.card.body.add_child(label(now.format('%B %Y'), 'kulan-widget-detail'));

        if (!this._source)
            return;
        const begin = new Date();
        const end = new Date(begin.getFullYear(), begin.getMonth(), begin.getDate() + 2);
        this._source.requestRange(new Date(begin.getFullYear(), begin.getMonth(), begin.getDate()), end);
        const events = this._source.getEvents(begin, end).filter(e => e.end > begin).slice(0, MAX_EVENTS);
        if (events.length === 0) {
            this.card.body.add_child(label(this._source.hasCalendars ? 'Nothing else today' : 'No calendars yet',
                'kulan-widget-muted'));
            return;
        }
        for (const event of events) {
            const start = GLib.DateTime.new_from_unix_local(Math.floor(event.date.getTime() / 1000));
            const sameDay = start.get_day_of_year() === now.get_day_of_year() && start.get_year() === now.get_year();
            const allDay = event.end.getTime() - event.date.getTime() >= 86400000 &&
                event.date.getHours() === 0 && event.date.getMinutes() === 0;
            const when = allDay ? (sameDay ? 'All day' : 'Tomorrow') : `${sameDay ? '' : 'Tomorrow '}${start.format('%H:%M')}`;
            const row = new St.BoxLayout({style_class: 'kulan-widget-event', vertical: true});
            row.add_child(label(event.summary || 'Event', 'kulan-widget-line'));
            row.add_child(label(when, 'kulan-widget-muted'));
            this.card.body.add_child(row);
        }
    }

    destroy() {
        if (this._sourceId)
            this._source.disconnect(this._sourceId);
        this._source?.destroy();
        this._source = null;
        this.card.destroy();
    }
}

// The place the person chose ("Columbus"), not the weather station that
// reports for it ("Columbus Metropolitan Airport").
function placeName(info) {
    try {
        const place = info.get_location();
        const name = place?.get_city_name() || place?.get_name();
        if (name)
            return name;
    } catch {
        // Older GWeather: fall through to the station's name.
    }
    return info.get_location_name() || 'Weather';
}

// "Partly cloudy", "Light rain"... or, when the station doesn't say, how warm
// it feels; never "Unknown".
function skyText(info) {
    try {
        const [hasConditions] = info.get_value_conditions();
        if (hasConditions)
            return info.get_conditions();
        const [hasSky] = info.get_value_sky();
        if (hasSky)
            return info.get_sky();
        const [hasApparent] = info.get_value_apparent(GWeather.TemperatureUnit.DEFAULT);
        if (hasApparent)
            return `Feels like ${info.get_apparent().replace(/\s+/g, '')}`;
    } catch (e) {
        logError(e, 'Kulan Shell: weather details');
    }
    return '';
}

class WeatherWidget {
    constructor() {
        this.card = new Card('Weather', 'weather-few-clouds-symbolic', () => this._client?.activateApp(), 'weather');
        this._client = null;
        try {
            this._client = new Weather.WeatherClient();
            this._clientId = this._client.connect('changed', () => this.update());
        } catch (e) {
            logError(e, 'Kulan Shell: weather widget');
        }
        this.update();
    }

    get usable() {
        return this._client?.available ?? false;
    }

    update() {
        this.card.clear();
        const client = this._client;
        if (!client || !client.available) {
            this.card.actor.visible = false;
            return;
        }
        // No place chosen yet: no card, rather than an empty one asking for
        // setup on the desktop (Weather is still in the app grid).
        this.card.actor.visible = client.hasLocation;
        if (!client.hasLocation)
            return;
        const info = client.info;
        if (client.loading || !info || !info.is_valid()) {
            this.card.setTitle(info ? placeName(info) : 'Weather');
            this.card.body.add_child(label(client.loading ? 'Loading…' : 'Not available right now', 'kulan-widget-muted'));
            return;
        }
        this.card.setTitle(placeName(info));
        const row = new St.BoxLayout({style_class: 'kulan-widget-weather'});
        row.add_child(new St.Icon({icon_name: info.get_symbolic_icon_name(), style_class: 'kulan-widget-weather-icon'}));
        row.add_child(label(info.get_temp_summary().replace(/\s+/g, ''), 'kulan-widget-big'));
        this.card.body.add_child(row);
        const detail = skyText(info);
        if (detail)
            this.card.body.add_child(label(detail, 'kulan-widget-detail'));
    }

    tick() {
        // The client keeps itself current; nothing to do each tick.
    }

    destroy() {
        if (this._clientId)
            this._client.disconnect(this._clientId);
        this._client = null;
        this.card.destroy();
    }
}

class PrayerWidget {
    constructor(settings) {
        this._settings = settings;
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this.card = new Card('Prayer Times', 'preferences-system-time-symbolic', () => launch(['kulan-settings', 'prayer-times']), 'prayer');
        this.update();
    }

    get usable() {
        const s = this._settings;
        return s.settings_schema.has_key('prayer-times') && s.get_boolean('prayer-times') && s.get_string('prayer-city') !== '';
    }

    update() {
        this.card.clear();
        this.card.actor.visible = this.usable;
        if (!this.usable)
            return;
        const now = GLib.DateTime.new_now_local();
        const moments = [...dayMoments(this._settings, now.add_days(-1)), ...dayMoments(this._settings, now),
            ...dayMoments(this._settings, now.add_days(1))].filter(m => m.name !== 'sunrise');
        const next = moments.find(m => m.when.compare(now) > 0);
        this.card.setTitle(this._settings.get_string('prayer-city').split(',')[0]);
        if (!next) {
            this.card.body.add_child(label('—', 'kulan-widget-big'));
            return;
        }
        const minutes = Math.max(0, Math.round(next.when.difference(now) / 60e6));
        const wait = minutes >= 60 ? `in ${Math.floor(minutes / 60)} h ${minutes % 60} min` : `in ${minutes} min`;
        this.card.body.add_child(label(PRAYER_NAMES[next.name], 'kulan-widget-big-text'));
        this.card.body.add_child(label(`${this._format(next.when)} · ${wait}`, 'kulan-widget-detail'));
        const today = dayMoments(this._settings, now).filter(m => m.name !== 'sunrise');
        const list = new St.BoxLayout({vertical: true, style_class: 'kulan-widget-list'});
        for (const m of today) {
            const row = new St.BoxLayout({style_class: 'kulan-widget-list-row'});
            row.add_child(label(PRAYER_NAMES[m.name], 'kulan-widget-muted'));
            row.add_child(new St.Label({text: this._format(m.when), style_class: 'kulan-widget-muted'}));
            if (next.when.equal(m.when))
                row.add_style_class_name('kulan-widget-next');
            list.add_child(row);
        }
        this.card.body.add_child(list);
    }

    _format(dateTime) {
        return this._interface.get_string('clock-format') === '12h' ? dateTime.format('%l:%M %p').trim() : dateTime.format('%H:%M');
    }

    tick() {
        this.update();
    }

    destroy() {
        this.card.destroy();
    }
}

class NotesWidget {
    constructor(openNote) {
        this.card = new Card('Note', 'document-edit-symbolic', openNote, 'note');
        this._monitor = null;
        try {
            this._monitor = Gio.File.new_for_path(NOTE_FILE).monitor_file(Gio.FileMonitorFlags.NONE, null);
            this._monitorId = this._monitor.connect('changed', () => this.update());
        } catch (e) {
            logError(e, 'Kulan Shell: note widget');
        }
        this.update();
    }

    update() {
        this.card.clear();
        let text = '';
        try {
            const [ok, bytes] = GLib.file_get_contents(NOTE_FILE);
            if (ok)
                text = new TextDecoder().decode(bytes).trim();
        } catch {
            text = '';
        }
        if (!text) {
            this.card.body.add_child(label('Click to jot something down', 'kulan-widget-muted', true));
            return;
        }
        const lines = text.split('\n').slice(0, 6).join('\n');
        const note = label(lines, 'kulan-widget-note', true);
        note.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        this.card.body.add_child(note);
    }

    destroy() {
        if (this._monitorId)
            this._monitor.disconnect(this._monitorId);
        this._monitor?.cancel();
        this._monitor = null;
        this.card.destroy();
    }
}

// Cards top to bottom, each at its natural height, starting a new column
// to the right when the next card wouldn't fit above the bottom of the screen.
const ColumnsLayout = GObject.registerClass(
class ColumnsLayout extends Clutter.LayoutManager {
    _init() {
        super._init();
        this.spacing = GAP;
        this.maxHeight = Infinity;
    }

    _columns(container) {
        const columns = [];
        let column = null;
        for (const child of container.get_children()) {
            if (!child.visible)
                continue;
            const [, width] = child.get_preferred_width(-1);
            const [, height] = child.get_preferred_height(width);
            if (column && column.height + this.spacing + height > this.maxHeight) {
                columns.push(column);
                column = null;
            }
            if (!column)
                column = {width: 0, height: -this.spacing, cards: []};
            column.cards.push({child, width, height});
            column.width = Math.max(column.width, width);
            column.height += this.spacing + height;
        }
        if (column)
            columns.push(column);
        return columns;
    }

    vfunc_get_preferred_width(container, _forHeight) {
        const columns = this._columns(container);
        const width = columns.reduce((sum, c) => sum + c.width, 0) + this.spacing * Math.max(0, columns.length - 1);
        return [width, width];
    }

    vfunc_get_preferred_height(container, _forWidth) {
        const height = Math.max(0, ...this._columns(container).map(c => c.height));
        return [height, height];
    }

    vfunc_allocate(container, box) {
        let x = box.x1;
        for (const column of this._columns(container)) {
            let y = box.y1;
            for (const {child, width, height} of column.cards) {
                const childBox = new Clutter.ActorBox();
                childBox.set_origin(Math.round(x), Math.round(y));
                childBox.set_size(width, height);
                child.allocate(childBox);
                y += height + this.spacing;
            }
            x += column.width + this.spacing;
        }
    }
});

export class DesktopWidgets {
    constructor(settings, openNote) {
        this._settings = settings;
        this._openNote = openNote;
        this._widgets = new Map();
        // Cards run down the left edge and carry on in a second column when
        // they don't fit (a short screen, or many widgets).
        this._columns = new ColumnsLayout();
        this._box = new St.Widget({layout_manager: this._columns, style_class: 'kulan-widgets', reactive: false});
        global.window_group.add_child(this._box);
        this._restackId = global.display.connect('restacked', () => this._restack());
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());
        this._workareasId = global.display.connect('workareas-changed', () => this._place());
        this._settingsId = settings.connect('changed', (_s, key) => {
            if (key === 'desktop-widgets' || key.startsWith('prayer-'))
                this.sync();
        });
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, TICK_S, () => {
            for (const widget of this._widgets.values())
                widget.tick?.();
            this._calendarDay();
            return GLib.SOURCE_CONTINUE;
        });
        this._day = GLib.DateTime.new_now_local().get_day_of_year();
        this.sync();
    }

    // The calendar card shows today: redraw it when the day changes.
    _calendarDay() {
        const day = GLib.DateTime.new_now_local().get_day_of_year();
        if (day !== this._day) {
            this._day = day;
            this._widgets.get('calendar')?.update();
        }
    }

    _wanted() {
        if (!this._settings.settings_schema.has_key('desktop-widgets'))
            return [];
        return this._settings.get_strv('desktop-widgets');
    }

    sync() {
        const wanted = Main.sessionMode.isLocked ? [] : this._wanted();
        for (const [id, widget] of this._widgets) {
            if (!wanted.includes(id)) {
                widget.destroy();
                this._widgets.delete(id);
            }
        }
        for (const id of wanted) {
            if (this._widgets.has(id))
                continue;
            let widget = null;
            if (id === 'calendar')
                widget = new CalendarWidget();
            else if (id === 'weather')
                widget = new WeatherWidget();
            else if (id === 'prayer')
                widget = new PrayerWidget(this._settings);
            else if (id === 'notes')
                widget = new NotesWidget(this._openNote);
            else if (id === 'clock')
                widget = this._make(ClockWidget);
            else if (id === 'system')
                widget = this._make(SystemWidget);
            else if (id === 'todo')
                widget = this._make(TodoWidget);
            else if (id === 'media')
                widget = this._make(MediaWidget);
            if (widget)
                this._widgets.set(id, widget);
        }
        // Keep the order chosen in the settings.
        this._box.remove_all_children();
        for (const id of wanted) {
            const widget = this._widgets.get(id);
            if (widget) {
                if (id === 'prayer')
                    widget.update();
                this._box.add_child(widget.card.actor);
            }
        }
        this._box.visible = this._widgets.size > 0;
        this._place();
        this._restack();
    }

    // One widget that fails to start (a missing service, say) never takes
    // the others with it.
    _make(Widget) {
        try {
            return new Widget();
        } catch (e) {
            logError(e, `Kulan Shell: ${Widget.name} could not start`);
            return null;
        }
    }

    _place() {
        const index = Main.layoutManager.primaryIndex;
        if (index < 0)
            return;
        const area = Main.layoutManager.getWorkAreaForMonitor(index);
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._columns.spacing = GAP * scale;
        this._columns.maxHeight = Math.max(0, area.height - 2 * MARGIN);
        this._columns.layout_changed();
        this._box.set_position(area.x + MARGIN, area.y + MARGIN);
    }

    // Just above the desktop icons' window (so the cards can be clicked),
    // below every other window.
    _restack() {
        // The desktop icons extension (DING) names its windows "@!x,y;...".
        const actors = global.window_group.get_children();
        const desktop = actors.filter(a => {
            const win = a.meta_window;
            return win && (win.get_window_type() === Meta.WindowType.DESKTOP || (win.get_title() ?? '').startsWith('@!'));
        });
        // The wallpaper is in this group too, at the bottom: stay above it.
        const background = Main.layoutManager._backgroundGroup;
        const below = desktop.length > 0 ? desktop[desktop.length - 1]
            : actors.includes(background) ? background : null;
        if (below)
            global.window_group.set_child_above_sibling(this._box, below);
    }

    destroy() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        for (const widget of this._widgets.values())
            widget.destroy();
        this._widgets.clear();
        if (this._restackId)
            global.display.disconnect(this._restackId);
        if (this._workareasId)
            global.display.disconnect(this._workareasId);
        if (this._monitorsId)
            Main.layoutManager.disconnect(this._monitorsId);
        if (this._settingsId)
            this._settings.disconnect(this._settingsId);
        this._restackId = this._workareasId = this._monitorsId = this._settingsId = 0;
        this._box.destroy();
        this._box = null;
    }
}

export {NOTE_FILE};
