// More desktop widgets: Clock (with the world clocks added in GNOME Clocks),
// System (processor, memory, disk, battery), To-do and Now Playing (music or
// video controls for whatever is playing). See widgets.js for how they're
// placed; each one has update(), tick() and destroy().

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GWeather from 'gi://GWeather?version=4.0';
import St from 'gi://St';


import {Card, label, launchApp} from './cards.js';
import {Players} from './players.js';
import {TODO_FILE, TodoPanel, checkButton, readTodos, setDone} from './todo.js';

const MAX_CITIES = 4;
const SYSTEM_EVERY_S = 4;
const MAX_TODOS = 5;
const BAR_WIDTH = 150; // the card's inner width, in logical pixels

function schemaExists(id) {
    return Gio.SettingsSchemaSource.get_default()?.lookup(id, true) !== null;
}

function scaled(px) {
    return Math.round(px * St.ThemeContext.get_for_stage(global.stage).scale_factor);
}

// --- Clock ------------------------------------------------------------------

export class ClockWidget {
    constructor() {
        this.card = new Card('Clock', 'preferences-system-time-symbolic',
            () => launchApp('org.gnome.clocks.desktop'), 'clock');
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._interfaceId = this._interface.connect('changed::clock-format', () => this.update());
        this._world = null;
        if (schemaExists('org.gnome.shell.world-clocks')) {
            // GNOME Clocks keeps this list (the top bar's calendar shows it too).
            this._world = new Gio.Settings({schema_id: 'org.gnome.shell.world-clocks'});
            this._worldId = this._world.connect('changed::locations', () => {
                this._loadCities();
                this.update();
            });
        }
        this._loadCities();
        this.update();
        this._schedule();
    }

    _loadCities() {
        this._cities = [];
        if (!this._world)
            return;
        const world = GWeather.Location.get_world();
        for (const value of this._world.get_value('locations').deepUnpack()) {
            try {
                const place = world?.deserialize(value);
                if (place?.get_timezone())
                    this._cities.push(place);
            } catch (e) {
                logError(e, 'Kulan Shell: a world clock could not be read');
            }
        }
    }

    // Redraw on the minute, not every half minute.
    _schedule() {
        const wait = 60 - GLib.DateTime.new_now_local().get_second();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, Math.max(1, wait), () => {
            this.update();
            this._schedule();
            return GLib.SOURCE_REMOVE;
        });
    }

    _time(dateTime) {
        return this._interface.get_string('clock-format') === '12h'
            ? dateTime.format('%l:%M %p').trim() : dateTime.format('%H:%M');
    }

    update() {
        this.card.clear();
        const now = GLib.DateTime.new_now_local();
        const twelve = this._interface.get_string('clock-format') === '12h';
        const time = new St.BoxLayout({style_class: 'kulan-clock-time'});
        time.add_child(new St.Label({text: twelve ? now.format('%l:%M').trim() : now.format('%H:%M'),
            style_class: 'kulan-widget-big'}));
        if (twelve) {
            time.add_child(new St.Label({text: now.format('%p'), style_class: 'kulan-clock-ampm',
                y_align: Clutter.ActorAlign.END}));
        }
        this.card.body.add_child(time);
        this.card.body.add_child(label(now.format('%A, %-d %B'), 'kulan-widget-detail'));

        if (this._cities.length === 0) {
            this.card.body.add_child(label('Add cities in Clocks to see their time here',
                'kulan-widget-muted', true));
            return;
        }
        const list = new St.BoxLayout({vertical: true, style_class: 'kulan-widget-list'});
        for (const place of this._cities.slice(0, MAX_CITIES)) {
            const there = GLib.DateTime.new_now(place.get_timezone());
            const row = new St.BoxLayout({style_class: 'kulan-widget-list-row'});
            row.add_child(label(place.get_city_name() || place.get_name(), 'kulan-widget-muted'));
            // Another day there: say which ("Fri 3:12 AM").
            const day = there.format('%Y%m%d') !== now.format('%Y%m%d') ? there.format('%a ') : '';
            row.add_child(new St.Label({text: `${day}${this._time(there)}`, style_class: 'kulan-widget-muted'}));
            list.add_child(row);
        }
        this.card.body.add_child(list);
    }

    tick() {
        // Kept current by its own minute timer.
    }

    destroy() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        this._interface.disconnect(this._interfaceId);
        if (this._worldId)
            this._world.disconnect(this._worldId);
        this.card.destroy();
    }
}

// --- System -----------------------------------------------------------------

function readText(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        return ok ? new TextDecoder().decode(bytes) : '';
    } catch {
        return '';
    }
}

function cpuTimes() {
    const line = readText('/proc/stat').split('\n')[0] ?? '';
    const parts = line.trim().split(/\s+/).slice(1).map(Number).filter(n => Number.isFinite(n));
    if (parts.length < 4)
        return null;
    const idle = parts[3] + (parts[4] ?? 0); // idle + waiting for the disk
    return {idle, total: parts.slice(0, 8).reduce((a, b) => a + b, 0)};
}

function memory() {
    const info = {};
    for (const line of readText('/proc/meminfo').split('\n')) {
        const m = line.match(/^(\w+):\s+(\d+)/);
        if (m)
            info[m[1]] = Number(m[2]) * 1024;
    }
    if (!info.MemTotal || info.MemAvailable === undefined)
        return null;
    return {used: info.MemTotal - info.MemAvailable, total: info.MemTotal};
}

function disk() {
    try {
        const fs = Gio.File.new_for_path(GLib.get_home_dir())
            .query_filesystem_info('filesystem::size,filesystem::free', null);
        const total = fs.get_attribute_uint64('filesystem::size');
        const free = fs.get_attribute_uint64('filesystem::free');
        return total > 0 ? {used: total - free, free, total} : null;
    } catch {
        return null;
    }
}

function battery() {
    const dir = '/sys/class/power_supply';
    let names = [];
    try {
        const e = Gio.File.new_for_path(dir).enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let info;
        while ((info = e.next_file(null)))
            names.push(info.get_name());
        e.close(null);
    } catch {
        return null;
    }
    names = names.sort();
    for (const name of names) {
        const base = `${dir}/${name}`;
        // The computer's own battery, not a mouse's or headphones'.
        if (readText(`${base}/type`).trim() !== 'Battery' || readText(`${base}/scope`).trim() === 'Device')
            continue;
        const level = Number(readText(`${base}/capacity`).trim());
        if (!Number.isFinite(level))
            continue;
        return {level: Math.max(0, Math.min(100, level)), status: readText(`${base}/status`).trim()};
    }
    return null;
}

function size(bytes) {
    const gb = bytes / 1024 ** 3;
    return gb >= 100 ? `${Math.round(gb)} GB` : `${gb.toFixed(1)} GB`;
}

export class SystemWidget {
    constructor() {
        this.card = new Card('System', 'computer-symbolic', () => {
            if (!launchApp('org.gnome.SystemMonitor.desktop'))
                launchApp('gnome-system-monitor.desktop');
        }, 'system');
        this._cpu = cpuTimes();
        this._cpuPercent = null;
        this.update();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, SYSTEM_EVERY_S, () => {
            this.update();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _row(name, value, fraction, warn = false) {
        const box = new St.BoxLayout({vertical: true, style_class: 'kulan-system-row'});
        const top = new St.BoxLayout();
        top.add_child(label(name, 'kulan-widget-muted'));
        top.add_child(new St.Label({text: value, style_class: 'kulan-system-value'}));
        box.add_child(top);
        const track = new St.Widget({style_class: 'kulan-system-bar', width: scaled(BAR_WIDTH)});
        const fill = new St.Widget({style_class: warn ? 'kulan-system-fill kulan-system-fill-warn' : 'kulan-system-fill',
            width: Math.max(scaled(4), Math.round(scaled(BAR_WIDTH) * Math.min(1, Math.max(0, fraction))))});
        track.add_child(fill);
        box.add_child(track);
        this.card.body.add_child(box);
    }

    update() {
        const now = cpuTimes();
        if (now && this._cpu && now.total > this._cpu.total)
            this._cpuPercent = Math.round(100 * (1 - (now.idle - this._cpu.idle) / (now.total - this._cpu.total)));
        this._cpu = now;

        this.card.clear();
        if (this._cpuPercent !== null)
            this._row('Processor', `${this._cpuPercent}%`, this._cpuPercent / 100, this._cpuPercent >= 90);
        else
            this._row('Processor', '…', 0);
        const mem = memory();
        if (mem)
            this._row('Memory', `${size(mem.used)} of ${size(mem.total)}`, mem.used / mem.total, mem.used / mem.total >= 0.9);
        const space = disk();
        if (space)
            this._row('Disk', `${size(space.free)} free`, space.used / space.total, space.free / space.total < 0.1);
        const power = battery();
        if (power) {
            const charging = power.status === 'Charging' ? ' · charging' : power.status === 'Full' ? ' · full' : '';
            this._row('Battery', `${power.level}%${charging}`, power.level / 100, power.level <= 15 && power.status !== 'Charging');
        }
    }

    tick() {
        // Kept current by its own timer.
    }

    destroy() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        this.card.destroy();
    }
}

// --- To-do ------------------------------------------------------------------

export class TodoWidget {
    constructor() {
        this._panel = new TodoPanel();
        this.card = new Card('To-do', 'checkbox-checked-symbolic', () => this._panel.open(), 'todo');
        this._monitor = null;
        try {
            GLib.mkdir_with_parents(GLib.path_get_dirname(TODO_FILE), 0o700);
            this._monitor = Gio.File.new_for_path(TODO_FILE).monitor_file(Gio.FileMonitorFlags.NONE, null);
            this._monitorId = this._monitor.connect('changed', () => this.update());
        } catch (e) {
            logError(e, 'Kulan Shell: to-do widget');
        }
        this.update();
    }

    update() {
        this.card.clear();
        const open = readTodos().filter(i => !i.done);
        if (open.length === 0) {
            this.card.body.add_child(label('Nothing to do. Click to add a task.', 'kulan-widget-muted', true));
            return;
        }
        for (const item of open.slice(0, MAX_TODOS)) {
            const row = new St.BoxLayout({style_class: 'kulan-todo-widget-row'});
            row.add_child(checkButton(false, () => setDone(item.id, true), `Mark “${item.text}” as done`));
            row.add_child(label(item.text, 'kulan-widget-line'));
            this.card.body.add_child(row);
        }
        if (open.length > MAX_TODOS)
            this.card.body.add_child(label(`${open.length - MAX_TODOS} more`, 'kulan-widget-muted'));
    }

    tick() {
        // The file monitor keeps it current.
    }

    destroy() {
        this._panel.destroy();
        if (this._monitorId)
            this._monitor.disconnect(this._monitorId);
        this._monitor?.cancel();
        this._monitor = null;
        this.card.destroy();
    }
}

// --- Now Playing ------------------------------------------------------------

export class MediaWidget {
    constructor() {
        this._player = null;
        this.card = new Card('Now Playing', 'audio-x-generic-symbolic', () => this._player?.raise(), 'media');
        this._players = new Players();
        this._players.connect('changed', () => this.update());
        this.update();
    }

    _pick() {
        const ready = this._players.all.filter(p => p.canPlay);
        return ready.find(p => p.status === 'Playing') ?? ready.find(p => p.status === 'Paused') ?? ready[0] ?? null;
    }

    _button(icon, name, sensitive, action) {
        const button = new St.Button({style_class: 'kulan-media-button', can_focus: true, reactive: sensitive,
            accessible_name: name, child: new St.Icon({icon_name: icon, icon_size: 16})});
        button.opacity = sensitive ? 255 : 90;
        button.connect('clicked', action);
        return button;
    }

    update() {
        this.card.clear();
        const player = this._player = this._pick();
        if (!player) {
            this.card.setTitle('Now Playing');
            this.card.body.add_child(label('Nothing playing', 'kulan-widget-muted'));
            return;
        }
        this.card.setTitle(player.name || 'Now Playing');
        const top = new St.BoxLayout({style_class: 'kulan-media-track'});
        const cover = new St.Icon({style_class: 'kulan-media-cover'});
        if (player.coverUrl)
            cover.gicon = new Gio.FileIcon({file: Gio.File.new_for_uri(player.coverUrl)});
        else if (player.app)
            cover.gicon = player.app.get_icon();
        else
            cover.icon_name = 'audio-x-generic-symbolic';
        top.add_child(cover);
        const words = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        words.add_child(label(player.title || 'Unknown title', 'kulan-widget-line'));
        if (player.artists.length > 0)
            words.add_child(label(player.artists.join(', '), 'kulan-widget-muted'));
        top.add_child(words);
        this.card.body.add_child(top);

        const controls = new St.BoxLayout({style_class: 'kulan-media-controls', x_align: Clutter.ActorAlign.CENTER});
        controls.add_child(this._button('media-skip-backward-symbolic', 'Previous', player.canGoPrevious, () => player.previous()));
        const playing = player.status === 'Playing';
        controls.add_child(this._button(playing ? 'media-playback-pause-symbolic' : 'media-playback-start-symbolic',
            playing ? 'Pause' : 'Play', true, () => player.playPause()));
        controls.add_child(this._button('media-skip-forward-symbolic', 'Next', player.canGoNext, () => player.next()));
        this.card.body.add_child(controls);
    }

    tick() {
        // Players report their own changes.
    }

    destroy() {
        this._players.destroy();
        this._player = null;
        this.card.destroy();
    }
}
