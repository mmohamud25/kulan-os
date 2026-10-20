// Kulan Shell: the Kulan OS look (stylesheet-dark.css / stylesheet-light.css,
// loaded by GNOME Shell on top of its own theme) and the Update Center
// indicator in the top bar.
//
// The indicator reads /var/lib/kulan/updates.json, which the
// kulan-update-center package keeps current from apt's own hooks. It only
// appears while updates are available or being installed.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const STATUS_FILE = '/var/lib/kulan/updates.json';
const UPDATE_CENTER = 'org.kulan.Updates.desktop';
const RELOAD_DELAY_MS = 400;
const FALLBACK_RELOAD_S = 600;
// An install that was interrupted (power loss) can leave "running" behind;
// the boot-time refresh clears it, and the indicator ignores it after 3 hours.
const STALE_RUNNING_S = 3 * 3600;

// GNOME writes its own folder list into a user's settings at first login,
// which hides the Kulan folders (system defaults) for users who existed
// before Kulan OS 2.0. Switch them over once, but only if their folders are
// still GNOME's untouched defaults; customised folders are left alone.
const GNOME_DEFAULT_FOLDERS = [
    ['System', 'Utilities', 'YaST', 'Pardus'],
    ['Utilities', 'YaST', 'Pardus'],
];

function migrateAppFolders() {
    const marker = GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan', 'app-folders-v2']);
    if (GLib.file_test(marker, GLib.FileTest.EXISTS))
        return;
    try {
        const settings = new Gio.Settings({schema_id: 'org.gnome.desktop.app-folders'});
        const userValue = settings.get_user_value('folder-children')?.deepUnpack();
        if (Array.isArray(userValue) &&
            GNOME_DEFAULT_FOLDERS.some(d => d.length === userValue.length && d.every((v, i) => v === userValue[i])))
            settings.reset('folder-children');
        GLib.mkdir_with_parents(GLib.path_get_dirname(marker), 0o700);
        GLib.file_set_contents(marker, '');
    } catch (e) {
        logError(e, 'Kulan Shell: could not set up app folders');
    }
}

function readStatus() {
    try {
        const [ok, bytes] = Gio.File.new_for_path(STATUS_FILE).load_contents(null);
        if (!ok)
            return null;
        const status = JSON.parse(new TextDecoder().decode(bytes));
        return typeof status === 'object' && status !== null ? status : null;
    } catch {
        return null;
    }
}

function plural(count, word) {
    return `${count} ${word}${count === 1 ? '' : 's'}`;
}

const UpdateIndicator = GObject.registerClass(
class KulanUpdateIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.5, 'Kulan updates');
        this.add_style_class_name('kulan-update-indicator');

        const box = new St.BoxLayout({style_class: 'panel-status-indicators-box'});
        this._icon = new St.Icon({
            icon_name: 'software-update-available-symbolic',
            style_class: 'system-status-icon',
        });
        this._count = new St.Label({
            style_class: 'kulan-update-count',
            y_align: Clutter.ActorAlign.CENTER,
        });
        box.add_child(this._icon);
        box.add_child(this._count);
        this.add_child(box);

        this._title = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._title.label.add_style_class_name('kulan-update-title');
        this._detail = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._detail.label.add_style_class_name('kulan-update-detail');
        this.menu.addMenuItem(this._title);
        this.menu.addMenuItem(this._detail);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Open Update Center', () => this._openUpdateCenter());

        this.visible = false;
    }

    _openUpdateCenter() {
        const app = Gio.DesktopAppInfo.new(UPDATE_CENTER);
        if (!app) {
            Main.notify('Update Center isn’t installed', 'Install the kulan-update-center package.');
            return;
        }
        try {
            app.launch([], global.create_app_launch_context(0, -1));
        } catch (e) {
            logError(e, 'Kulan Shell: could not open Update Center');
        }
    }

    update(status) {
        const available = Array.isArray(status?.available) ? status.available.length : 0;
        const security = Number.isInteger(status?.security) ? status.security : 0;
        const since = Number(status?.running_since) || 0;
        const running = status?.state === 'running' && Date.now() / 1000 - since < STALE_RUNNING_S;

        this.visible = running || available > 0;
        this._count.text = available > 0 && !running ? String(available) : '';

        if (running) {
            this._icon.icon_name = 'emblem-synchronizing-symbolic';
            this._title.label.text = 'Installing updates…';
            this._detail.label.text = 'You can keep working while this runs.';
        } else {
            this._icon.icon_name = security > 0
                ? 'software-update-urgent-symbolic'
                : 'software-update-available-symbolic';
            this._title.label.text = `${plural(available, 'update')} available`;
            this._detail.label.text = security > 0
                ? `${plural(security, 'security fix')} included`
                : 'Open Update Center to install them now.';
        }
    }
});

export default class KulanShellExtension extends Extension {
    enable() {
        if (!Main.sessionMode.isLocked)
            migrateAppFolders();

        this._indicator = new UpdateIndicator();
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'right');

        this._lastRun = undefined;
        this._monitor = Gio.File.new_for_path(STATUS_FILE)
            .monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, null);
        this._monitorId = this._monitor.connect('changed', () => this._queueReload());
        this._fallbackId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, FALLBACK_RELOAD_S, () => {
            this._reload();
            return GLib.SOURCE_CONTINUE;
        });
        this._sessionId = Main.sessionMode.connect('updated', () => this._syncSession());

        this._reload();
        this._syncSession();
    }

    disable() {
        if (this._sessionId) {
            Main.sessionMode.disconnect(this._sessionId);
            this._sessionId = 0;
        }
        if (this._reloadId) {
            GLib.source_remove(this._reloadId);
            this._reloadId = 0;
        }
        if (this._fallbackId) {
            GLib.source_remove(this._fallbackId);
            this._fallbackId = 0;
        }
        if (this._monitor) {
            this._monitor.disconnect(this._monitorId);
            this._monitor.cancel();
            this._monitor = null;
        }
        this._indicator?.destroy();
        this._indicator = null;
    }

    // On the lock screen only the look stays; the indicator is hidden.
    _syncSession() {
        if (this._indicator)
            this._indicator.container.visible = !Main.sessionMode.isLocked;
    }

    // apt writes the status file a few times in a row; reload once it settles.
    _queueReload() {
        if (this._reloadId)
            GLib.source_remove(this._reloadId);
        this._reloadId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, RELOAD_DELAY_MS, () => {
            this._reloadId = 0;
            this._reload();
            return GLib.SOURCE_REMOVE;
        });
    }

    _reload() {
        const status = readStatus();
        this._indicator?.update(status);

        // Tell the user once when an update run has finished.
        const lastRun = status?.last_run;
        const runTime = typeof lastRun?.time === 'number' ? lastRun.time : null;
        if (this._lastRun !== undefined && runTime !== null && runTime !== this._lastRun &&
            status?.state !== 'running' && Number(lastRun.count) > 0) {
            Main.notify('Kulan OS was updated',
                `${plural(Number(lastRun.count), 'update')} installed. See what’s new in Update Center.`);
        }
        this._lastRun = runTime;
    }
}
