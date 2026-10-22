// Kulan Shell: reopen the apps that were open before a restart or shutdown,
// as on a Mac. Kulan Settings > Desktop and windows > "Reopen apps after a
// restart" (org.kulan.desktop reopen-apps) switches it off.
//
// The list of open apps is saved a few seconds after it changes, so apps
// closing while the computer shuts down don't empty it: confirming Restart or
// Power Off (GNOME's dialog), or the system announcing a shutdown, freezes
// the list. At the next sign-in the saved apps are started again. GNOME has no
// way to put windows back where they were; apps that remember their own state
// (Firefox's tabs, Text Editor's files) pick up where they left off.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';

import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as EndSessionDialog from 'resource:///org/gnome/shell/ui/endSessionDialog.js';

const SAVE_DELAY_S = 8;
const REOPEN_DELAY_S = 3;
const MAX_APPS = 12;
// Not reopened: the desktop's own pieces and Kulan's one-off windows.
const SKIP = new Set([
    'org.gnome.Shell.Extensions.desktop', 'org.gnome.Settings.desktop', 'org.kulan.Welcome.desktop',
    'calamares-install-debian.desktop', 'gnome-system-monitor.desktop', 'org.gnome.Software.desktop',
    'ding.desktop', 'org.gnome.Nautilus.Desktop.desktop', 'org.gnome.Shell.PortalHelper.desktop',
]);

function stateFile() {
    return GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan', 'open-apps.json']);
}

export class ReopenApps {
    constructor(settings) {
        this._settings = settings;
        this._appSystem = Shell.AppSystem.get_default();
        this._frozen = false;
        this._saveId = 0;
        this._reopenId = 0;
        this._injections = new InjectionManager();

        const self = this;
        this._injections.overrideMethod(EndSessionDialog.EndSessionDialog.prototype, '_confirm',
            original => function (...args) {
                self._freeze();
                return original.call(this, ...args);
            });
        this._logindId = 0;
        try {
            this._logindId = Gio.DBus.system.signal_subscribe('org.freedesktop.login1',
                'org.freedesktop.login1.Manager', 'PrepareForShutdown', '/org/freedesktop/login1',
                null, Gio.DBusSignalFlags.NONE, (...a) => {
                    const [starting] = a[5].deepUnpack();
                    if (starting)
                        this._freeze();
                });
        } catch (e) {
            this._logindId = 0;
        }
        this._stateId = this._appSystem.connect('app-state-changed', () => this._queueSave());

        this._reopenId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, REOPEN_DELAY_S, () => {
            this._reopenId = 0;
            this._reopen();
            return GLib.SOURCE_REMOVE;
        });
    }

    _enabled() {
        return this._settings?.settings_schema.has_key('reopen-apps')
            ? this._settings.get_boolean('reopen-apps') : true;
    }

    _freeze() {
        // Save what's open now and stop following apps as they close.
        if (this._frozen)
            return;
        this._save();
        this._frozen = true;
    }

    _queueSave() {
        if (this._frozen || this._reopenId)
            return; // while shutting down, or before the old list was used
        if (this._saveId)
            GLib.source_remove(this._saveId);
        this._saveId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, SAVE_DELAY_S, () => {
            this._saveId = 0;
            this._save();
            return GLib.SOURCE_REMOVE;
        });
    }

    _runningIds() {
        return this._appSystem.get_running()
            .filter(app => !app.is_window_backed() && app.get_id() && !SKIP.has(app.get_id()))
            .map(app => app.get_id())
            .slice(0, MAX_APPS);
    }

    _save() {
        if (this._frozen)
            return;
        try {
            const path = stateFile();
            GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
            GLib.file_set_contents(path, JSON.stringify({apps: this._runningIds(), saved: Date.now()}));
        } catch (e) {
            logError(e, 'Kulan Shell: could not save the open apps');
        }
    }

    _reopen() {
        let saved = [];
        try {
            const [, bytes] = GLib.file_get_contents(stateFile());
            saved = JSON.parse(new TextDecoder().decode(bytes)).apps ?? [];
        } catch (e) {
            saved = [];
        }
        if (this._enabled()) {
            const running = new Set(this._runningIds());
            for (const id of saved) {
                if (typeof id !== 'string' || running.has(id) || SKIP.has(id))
                    continue;
                const app = this._appSystem.lookup_app(id);
                if (!app)
                    continue;
                try {
                    app.launch(0, -1, Shell.AppLaunchGpu.DEFAULT);
                } catch (e) {
                    logError(e, `Kulan Shell: could not reopen ${id}`);
                }
            }
        }
        this._queueSave(); // from now on, follow what's open
    }

    destroy() {
        this._injections.clear();
        if (this._logindId)
            Gio.DBus.system.signal_unsubscribe(this._logindId);
        this._logindId = 0;
        if (this._stateId)
            this._appSystem.disconnect(this._stateId);
        this._stateId = 0;
        for (const id of [this._saveId, this._reopenId]) {
            if (id)
                GLib.source_remove(id);
        }
        this._saveId = this._reopenId = 0;
    }
}
