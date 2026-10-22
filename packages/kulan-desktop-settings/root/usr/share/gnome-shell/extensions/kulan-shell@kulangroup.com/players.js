// Music and video players on the session bus (MPRIS), for the Now Playing
// widget. Kept separate from GNOME Shell's own media code, whose module
// changed between GNOME versions.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';

import * as Signals from 'resource:///org/gnome/shell/misc/signals.js';

const PREFIX = 'org.mpris.MediaPlayer2.';
const PATH = '/org/mpris/MediaPlayer2';

function unpack(variant) {
    try {
        return variant?.deepUnpack() ?? null;
    } catch {
        return null;
    }
}

class Player extends Signals.EventEmitter {
    constructor(busName) {
        super();
        this.busName = busName;
        this.app = null;
        this.identity = '';
        this._root = null;
        this._player = null;
        this._cancel = new Gio.Cancellable();
        this._connect();
    }

    async _connect() {
        try {
            const make = iface => new Promise((resolve, reject) => {
                Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, null,
                    this.busName, PATH, iface, this._cancel, (_o, res) => {
                        try {
                            resolve(Gio.DBusProxy.new_for_bus_finish(res));
                        } catch (e) {
                            reject(e);
                        }
                    });
            });
            const [root, player] = await Promise.all([
                make('org.mpris.MediaPlayer2'), make('org.mpris.MediaPlayer2.Player')]);
            if (this._cancel.is_cancelled())
                return; // destroyed while connecting
            [this._root, this._player] = [root, player];
        } catch (e) {
            if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                logError(e, `Kulan Shell: media player ${this.busName}`);
            return;
        }
        this.identity = unpack(this._root.get_cached_property('Identity')) ?? '';
        const entry = unpack(this._root.get_cached_property('DesktopEntry'));
        this.app = entry ? Shell.AppSystem.get_default().lookup_app(`${entry}.desktop`) : null;
        this._changedId = this._player.connect('g-properties-changed', () => this.emit('changed'));
        this.emit('changed');
    }

    _get(name) {
        return unpack(this._player?.get_cached_property(name));
    }

    get ready() {
        return this._player !== null && this._player.get_name_owner() !== null;
    }

    get canPlay() {
        return this.ready && this._get('CanPlay') !== false;
    }

    get status() {
        return this._get('PlaybackStatus') ?? 'Stopped';
    }

    get canGoNext() {
        return !!this._get('CanGoNext');
    }

    get canGoPrevious() {
        return !!this._get('CanGoPrevious');
    }

    get metadata() {
        const data = this._get('Metadata') ?? {};
        const out = {};
        for (const [key, value] of Object.entries(data))
            out[key] = value instanceof GLib.Variant ? unpack(value) : value;
        return out;
    }

    get title() {
        const title = this.metadata['xesam:title'];
        return typeof title === 'string' ? title : '';
    }

    get artists() {
        const artists = this.metadata['xesam:artist'];
        return Array.isArray(artists) ? artists.filter(a => typeof a === 'string') : [];
    }

    get coverUrl() {
        const url = this.metadata['mpris:artUrl'];
        return typeof url === 'string' && /^(file|https?):\/\//.test(url) ? url : '';
    }

    get name() {
        return this.app?.get_name() ?? this.identity;
    }

    _call(proxy, method) {
        proxy?.call(method, null, Gio.DBusCallFlags.NONE, -1, null, (p, res) => {
            try {
                p.call_finish(res);
            } catch (e) {
                logError(e, `Kulan Shell: media ${method}`);
            }
        });
    }

    playPause() {
        this._call(this._player, 'PlayPause');
    }

    next() {
        this._call(this._player, 'Next');
    }

    previous() {
        this._call(this._player, 'Previous');
    }

    raise() {
        if (this.app)
            this.app.activate();
        else
            this._call(this._root, 'Raise');
    }

    destroy() {
        this._cancel.cancel();
        if (this._changedId)
            this._player.disconnect(this._changedId);
        this._player = this._root = null;
        this.disconnectAll();
    }
}

// Every player on the bus, kept current as players start and quit.
// Emits 'changed' for any change.
export class Players extends Signals.EventEmitter {
    constructor() {
        super();
        this._players = new Map();
        this._cancel = new Gio.Cancellable();
        this._bus = Gio.DBus.session;
        this._watchId = this._bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
            'NameOwnerChanged', '/org/freedesktop/DBus', null, Gio.DBusSignalFlags.NONE,
            (_c, _s, _p, _i, _n, params) => {
                const [name, , owner] = params.deepUnpack();
                if (!name.startsWith(PREFIX))
                    return;
                if (owner)
                    this._add(name);
                else
                    this._remove(name);
            });
        this._bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ListNames',
            null, new GLib.VariantType('(as)'), Gio.DBusCallFlags.NONE, -1, this._cancel, (bus, res) => {
                try {
                    const [names] = bus.call_finish(res).deepUnpack();
                    if (this._cancel.is_cancelled())
                        return;
                    for (const name of names.filter(n => n.startsWith(PREFIX)))
                        this._add(name);
                } catch (e) {
                    if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        logError(e, 'Kulan Shell: listing media players');
                }
            });
    }

    get all() {
        return [...this._players.values()];
    }

    _add(name) {
        if (this._cancel.is_cancelled() || this._players.has(name))
            return;
        const player = new Player(name);
        player.connect('changed', () => this.emit('changed'));
        this._players.set(name, player);
    }

    _remove(name) {
        const player = this._players.get(name);
        if (!player)
            return;
        this._players.delete(name);
        player.destroy();
        this.emit('changed');
    }

    destroy() {
        this._cancel.cancel();
        if (this._watchId) {
            this._bus.signal_unsubscribe(this._watchId);
            this._watchId = 0;
        }
        for (const player of this._players.values())
            player.destroy();
        this._players.clear();
        this.disconnectAll();
    }
}
