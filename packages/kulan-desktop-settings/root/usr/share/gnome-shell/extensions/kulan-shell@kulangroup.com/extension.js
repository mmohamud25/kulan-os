// Kulan Shell: the Kulan OS look (stylesheet-dark.css / stylesheet-light.css,
// loaded by GNOME Shell on top of its own theme), the Kulan menu at the top
// left, the update indicator in the top bar, slightly quicker animations,
// automatic light/dark at sunset, a Focus schedule (Do Not Disturb at set
// hours), Live Text, clipboard history, and (once per user) the Kulan window
// buttons for apps. Its options (org.kulan.desktop) are set in Kulan Settings.
//
// The indicator reads /var/lib/kulan/updates.json, which the
// kulan-update-center package keeps current from apt's own hooks. It only
// appears while updates are available or being installed.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as SystemActions from 'resource:///org/gnome/shell/misc/systemActions.js';
import {UnlockDialog} from 'resource:///org/gnome/shell/ui/unlockDialog.js';

import {Assistant} from './assistant.js';
import {ClipboardHistory} from './clipboard.js';
import {Dictation} from './dictation.js';
import {LiveText} from './livetext.js';
import {PrayerTimes} from './prayer.js';
import {Spotlight} from './spotlight.js';
import {AskKulan} from './askkulan.js';
import {QuickNote} from './quicknote.js';
import {DesktopWidgets} from './widgets.js';
import {VmDisplayKeeper} from './vmdisplay.js';
import {ReopenApps} from './reopen.js';
import {LightEffects, ANIMATION_FACTOR} from './lighteffects.js';

const APP_GRID_ICON_SIZE = 72;
const STATUS_FILE = '/var/lib/kulan/updates.json';
const KULAN_SCHEMA = 'org.kulan.desktop';
const RELOAD_DELAY_MS = 400;
const FALLBACK_RELOAD_S = 600;
// An install that was interrupted (power loss) can leave "running" behind;
// the boot-time refresh clears it, and the indicator ignores it after 3 hours.
const STALE_RUNNING_S = 3 * 3600;
// Animation duration, as a fraction of GNOME's own, per Kulan Settings choice.
const ANIMATION_SPEEDS = {calm: 1.0, normal: 0.8, fast: 0.6};
// Without a known location, "automatic" is dark from 19:00 to 07:00.
const FALLBACK_SUNSET = 19;
const FALLBACK_SUNRISE = 7;
const STYLE_CHECK_S = 60;
// Kulan shortcuts (org.kulan.desktop keys) and what they do.
const SHORTCUTS = {
    'live-text-shortcut': ext => ext._liveText?.start(),
    'writing-tools-shortcut': ext => ext._assistant?.writingTools(),
    'dictation-shortcut': ext => ext._dictation?.toggle(),
    'quick-note-shortcut': ext => ext._quickNote?.toggle(),
    'ask-shortcut': ext => ext._askKulan?.toggle(),
    'clipboard-history-shortcut': ext => {
        if (ext._clipboard)
            ext._clipboard.show();
        else
            Main.notify('Clipboard history is off', 'Turn it on in Kulan Settings › Keyboard.');
    },
};

// GNOME writes its own folder list into a user's settings at first login,
// which hides the Kulan folders (system defaults) for users who existed
// before Kulan OS 2.0. Switch them over once, but only if their folders are
// still GNOME's untouched defaults; customised folders are left alone.
//
// Kulan OS 2.1 keeps everyday apps loose in the grid, with only Utilities and
// System as folders; a user still on the 2.0 list is moved over the same way,
// including a 2.0 list GNOME trimmed (it drops a folder once its last app is
// dragged out). A list with any folder of the user's own is left alone.
const KULAN_2_0_FOLDERS = ['Kulan-Office', 'Kulan-Internet', 'Kulan-Media', 'Kulan-Developer',
    'Kulan-Utilities', 'Kulan-System'];
const OLD_DEFAULT_FOLDERS = [
    ['System', 'Utilities', 'YaST', 'Pardus'],
    ['Utilities', 'YaST', 'Pardus'],
    ['Kulan-Office', 'Kulan-Internet', 'Kulan-Media', 'Kulan-Developer', 'Kulan-Utilities', 'Kulan-System'],
];

function migrateAppFolders() {
    const marker = GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan', 'app-folders-v4']);
    if (GLib.file_test(marker, GLib.FileTest.EXISTS))
        return;
    try {
        const settings = new Gio.Settings({schema_id: 'org.gnome.desktop.app-folders'});
        const userValue = settings.get_user_value('folder-children')?.deepUnpack();
        const isOldDefault = Array.isArray(userValue) &&
            OLD_DEFAULT_FOLDERS.some(d => d.length === userValue.length && d.every((v, i) => v === userValue[i]));
        const isTrimmed2_0 = Array.isArray(userValue) && userValue.length > 0 &&
            userValue.some(v => v !== 'Kulan-Utilities' && v !== 'Kulan-System') &&
            userValue.every(v => KULAN_2_0_FOLDERS.includes(v));
        if (isOldDefault || isTrimmed2_0)
            settings.reset('folder-children');
        GLib.mkdir_with_parents(GLib.path_get_dirname(marker), 0o700);
        GLib.file_set_contents(marker, '');
    } catch (e) {
        logError(e, 'Kulan Shell: could not set up app folders');
    }
}

// GNOME keeps every app where it was last placed, so apps that are removed or
// hidden leave holes, and a grid can end up with a half-empty first page and
// apps spread over three pages. Once (Kulan OS 2.3), a grid with such gaps is
// sorted A to Z again, which fills every page; a tidy grid is left alone.
// Kulan Settings > Desktop and windows has "Tidy" to do it any time.
function isGappy(layout) {
    if (!Array.isArray(layout) || layout.length < 2)
        return false;
    const counts = layout.map(page => Object.keys(page ?? {}).length);
    const fullest = Math.max(...counts);
    return counts.slice(0, -1).some(n => n < fullest / 2);
}

function tidyAppGrid() {
    const marker = GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan', 'app-grid-tidy-v1']);
    if (GLib.file_test(marker, GLib.FileTest.EXISTS))
        return;
    try {
        const settings = new Gio.Settings({schema_id: 'org.gnome.shell'});
        if (isGappy(settings.get_value('app-picker-layout').recursiveUnpack()))
            settings.reset('app-picker-layout');
        GLib.mkdir_with_parents(GLib.path_get_dirname(marker), 0o700);
        GLib.file_set_contents(marker, '');
    } catch (e) {
        logError(e, 'Kulan Shell: could not tidy the app grid');
    }
}

// Kulan window buttons (round, macOS-style) for GTK 4 and GTK 3 apps. GTK has
// no system-wide stylesheet, so each user's gtk.css imports Kulan's. Added
// once: a user who removes the line keeps it removed.
// Kulan's window buttons for GTK apps. Each user's gtk.css imports a copy
// kept next to it (kulan-window-buttons.css), not the file in /usr/share:
// apps from the Kulan Store (Flatpak) can read ~/.config/gtk-*.0 (Kulan
// allows that for every Flatpak) but not /usr/share. The copy is refreshed at
// every sign-in, so updates to the style reach everyone.
const GTK_DIRS = ['gtk-4.0', 'gtk-3.0'];
const GTK_STYLE_FILES = {
    symbols: {'gtk-4.0': '/usr/share/kulan/gtk/gtk-4.0.css', 'gtk-3.0': '/usr/share/kulan/gtk/gtk-3.0.css'},
    dots: {'gtk-4.0': '/usr/share/kulan/gtk/dots/gtk-4.0.css', 'gtk-3.0': '/usr/share/kulan/gtk/dots/gtk-3.0.css'},
};
const GTK_COPY = 'kulan-window-buttons.css';
const GTK_IMPORT = `@import url("${GTK_COPY}");`;
const GTK_COMMENT = "/* Kulan OS window buttons. Delete this line to use GTK's own. */";

function readText(path) {
    try {
        const [, bytes] = GLib.file_get_contents(path);
        return new TextDecoder().decode(bytes);
    } catch (e) {
        return null;
    }
}

function installGtkStyle() {
    const dataDir = GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan']);
    const firstTime = !GLib.file_test(GLib.build_filenamev([dataDir, 'gtk-style-v1']), GLib.FileTest.EXISTS) &&
        !GLib.file_test(GLib.build_filenamev([dataDir, 'gtk-style-v2']), GLib.FileTest.EXISTS);
    try {
        for (const dir of GTK_DIRS) {
            const folder = GLib.build_filenamev([GLib.get_user_config_dir(), dir]);
            const path = GLib.build_filenamev([folder, 'gtk.css']);
            const copy = GLib.build_filenamev([folder, GTK_COPY]);
            const current = readText(path) ?? '';
            // Which style this person uses: an old import of /usr/share, the
            // name in the copy's first line, or Kulan's default the first time.
            let style = null;
            for (const [name, files] of Object.entries(GTK_STYLE_FILES)) {
                if (current.includes(`@import url("file://${files[dir]}");`))
                    style = name;
            }
            if (!style && current.includes(GTK_IMPORT))
                style = (readText(copy) ?? '').match(/^\/\* kulan-style: (\w+) \*\//)?.[1] ?? 'symbols';
            if (!style && firstTime)
                style = 'symbols';
            if (!style || !GTK_STYLE_FILES[style])
                continue; // switched to GTK's own buttons
            const source = readText(GTK_STYLE_FILES[style][dir]);
            if (source === null)
                continue;
            GLib.mkdir_with_parents(folder, 0o755);
            const fresh = `/* kulan-style: ${style} */\n${source}`;
            if (readText(copy) !== fresh)
                GLib.file_set_contents(copy, fresh);
            const kept = current.split('\n').filter(l => !l.trim().startsWith('@import url("file:///usr/share/kulan/gtk/') &&
                l.trim() !== GTK_IMPORT && l.trim() !== GTK_COMMENT);
            const text = [GTK_COMMENT, GTK_IMPORT, ...kept].join('\n').replace(/\n+$/, '') + '\n';
            if (text !== current)
                GLib.file_set_contents(path, text);
        }
        GLib.mkdir_with_parents(dataDir, 0o700);
        GLib.file_set_contents(GLib.build_filenamev([dataDir, 'gtk-style-v2']), '');
    } catch (e) {
        logError(e, 'Kulan Shell: could not set up the window buttons');
    }
}

// The Kulan look (2.4) for GTK 4 / libadwaita apps: Kulan's navy and mist on
// GNOME's surfaces, rounder windows, Manrope titles. Like the window buttons,
// each user's gtk.css imports a copy kept next to it (Flatpak apps can read
// that), refreshed at every sign-in. The import line is added once: someone
// who deletes it keeps GNOME's own look.
const GTK_LOOK_SOURCE = '/usr/share/kulan/gtk/look/gtk-4.0.css';
const GTK_LOOK_COPY = 'kulan-look.css';
const GTK_LOOK_IMPORT = `@import url("${GTK_LOOK_COPY}");`;
const GTK_LOOK_COMMENT = "/* Kulan OS look. Delete this line to use GNOME's own colours. */";

function installGtkLook() {
    const dataDir = GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan']);
    const marker = GLib.build_filenamev([dataDir, 'gtk-look-v1']);
    try {
        const source = readText(GTK_LOOK_SOURCE);
        if (source === null)
            return;
        const folder = GLib.build_filenamev([GLib.get_user_config_dir(), 'gtk-4.0']);
        const path = GLib.build_filenamev([folder, 'gtk.css']);
        const copy = GLib.build_filenamev([folder, GTK_LOOK_COPY]);
        const current = readText(path) ?? '';
        const firstTime = !GLib.file_test(marker, GLib.FileTest.EXISTS);
        if (!firstTime && !current.includes(GTK_LOOK_IMPORT))
            return; // turned off by the user
        GLib.mkdir_with_parents(folder, 0o755);
        if (readText(copy) !== source)
            GLib.file_set_contents(copy, source);
        if (!current.includes(GTK_LOOK_IMPORT)) {
            // After the window buttons' import (imports must come first).
            const lines = current.split('\n');
            let at = 0;
            lines.forEach((l, i) => {
                if (l.trim().startsWith('@import') || l.trim() === GTK_COMMENT)
                    at = i + 1;
            });
            lines.splice(at, 0, GTK_LOOK_COMMENT, GTK_LOOK_IMPORT);
            GLib.file_set_contents(path, lines.join('\n').replace(/\n+$/, '') + '\n');
        }
        GLib.mkdir_with_parents(dataDir, 0o700);
        GLib.file_set_contents(marker, '');
    } catch (e) {
        logError(e, 'Kulan Shell: could not set up the Kulan look for apps');
    }
}

function launchApp(desktopId) {
    const app = Gio.DesktopAppInfo.new(desktopId);
    if (!app)
        return false;
    try {
        app.launch([], global.create_app_launch_context(0, -1));
        return true;
    } catch (e) {
        logError(e, `Kulan Shell: could not open ${desktopId}`);
        return false;
    }
}

function runCommand(argv) {
    try {
        Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
        return true;
    } catch (e) {
        logError(e, `Kulan Shell: could not run ${argv[0]}`);
        return false;
    }
}

// Open a page of Kulan Settings; GNOME Settings if Kulan Settings is missing.
function openKulanSettings(page) {
    if (GLib.find_program_in_path('kulan-settings'))
        return runCommand(page ? ['kulan-settings', page] : ['kulan-settings']);
    if (page === 'about')
        return runCommand(['gnome-control-center', 'system', 'about']);
    return launchApp('org.gnome.Settings.desktop');
}

// The Kulan options, or null when the schema isn't installed yet.
function kulanSettings() {
    const source = Gio.SettingsSchemaSource.get_default();
    if (!source?.lookup(KULAN_SCHEMA, true))
        return null;
    return new Gio.Settings({schema_id: KULAN_SCHEMA});
}

// The Kulan menu: the logo at the top left, like the Apple menu.
const KulanMenu = GObject.registerClass(
class KulanMenu extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'Kulan menu');
        this.add_style_class_name('kulan-menu-button');
        this.add_child(new St.Icon({
            icon_name: 'kulan-logo-symbolic',
            style_class: 'system-status-icon kulan-menu-icon',
        }));

        const actions = SystemActions.getDefault();
        this.menu.addAction('About Kulan OS', () => openKulanSettings('about'));
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Kulan Settings…', () => openKulanSettings(null));
        this.menu.addAction('Software Update…', () => openKulanSettings('updates'));
        this.menu.addAction('Kulan Store…', () => launchApp('org.gnome.Software.desktop'));
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Lock Screen', () => actions.activateLockScreen());
        this.menu.addAction('Log Out…', () => actions.activateLogout());
        this.menu.addAction('Restart…', () => actions.activateRestart());
        this.menu.addAction('Shut Down…', () => actions.activatePowerOff());
        this.menu.actor.add_style_class_name('kulan-menu');
    }
});

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
        this._restartItem = this.menu.addAction('Restart…', () => this._restart());
        this.menu.addAction('Open Software Update', () => this._openUpdateCenter());

        this.visible = false;
        this.allowed = true;
    }

    _openUpdateCenter() {
        openKulanSettings('updates');
    }

    _restart() {
        // GNOME's own restart dialog, which lets apps save their work first.
        SystemActions.getDefault().activateRestart();
    }

    update(status) {
        // Counted the way Software Update counts them ("count", grouped by what
        // people know), so both say the same number; raw counts from an
        // older status file.
        const raw = Array.isArray(status?.available) ? status.available.length : 0;
        const available = Number.isInteger(status?.count) ? status.count : raw;
        const security = Number.isInteger(status?.security_count) ? status.security_count
            : Number.isInteger(status?.security) ? status.security : 0;
        const since = Number(status?.running_since) || 0;
        const running = status?.state === 'running' && Date.now() / 1000 - since < STALE_RUNNING_S;

        const restart = Array.isArray(status?.restart_required) && status.restart_required.length > 0;

        this.visible = this.allowed && (running || available > 0 || restart);
        this._count.text = available > 0 && !running ? String(available) : '';
        this._restartItem.visible = restart && !running;

        if (running) {
            this._icon.icon_name = 'view-refresh-symbolic';
            this._title.label.text = 'Installing updates…';
            this._detail.label.text = 'You can keep working while this runs.';
        } else if (restart && available === 0) {
            this._icon.icon_name = 'system-reboot-symbolic';
            this._title.label.text = 'Restart to finish updating';
            this._detail.label.text = 'Some updates take effect after a restart.';
        } else {
            this._icon.icon_name = security > 0
                ? 'software-update-urgent-symbolic'
                : 'software-update-available-symbolic';
            this._title.label.text = `${plural(available, 'update')} available`;
            this._detail.label.text = security > 0
                ? `${plural(security, 'security fix')} included`
                : 'Open Software Update to install them now.';
        }
    }
});

export default class KulanShellExtension extends Extension {
    enable() {
        if (!Main.sessionMode.isLocked) {
            migrateAppFolders();
            tidyAppGrid();
            installGtkStyle();
            installGtkLook();
        }

        this._oldSlowDown = St.Settings.get().slow_down_factor;

        // Lock screen, Mac-style: the date above the time.
        this._injections = new InjectionManager();
        this._injections.overrideMethod(UnlockDialog.prototype, '_init', original => function (...args) {
            original.call(this, ...args);
            const clock = this._clock;
            if (clock?._date && clock._time && clock._date.get_parent() === clock)
                clock.set_child_below_sibling(clock._date, clock._time);
        });

        this._menu = new KulanMenu();
        Main.panel.addToStatusArea(`${this.uuid}-menu`, this._menu, 0, 'left');

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
        this._sessionId = Main.sessionMode.connect('updated', () => {
            this._widgets?.sync(); // no widgets on the lock screen
            this._syncSession();
            this._syncStyle(); // the time may have passed sunset while locked
            if (Main.sessionMode.isLocked) {
                this._clipboard?.clear(); // nothing copied survives a lock
                this._dictation?.destroy(); // and nothing is recorded while locked
                this._spotlight?.close();
                this._askKulan?.close();
                this._quickNote?.close();
            }
        });

        this._settings = kulanSettings();
        this._settingsId = this._settings?.connect('changed', () => this._applySettings()) ?? 0;
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        // Choosing light or dark anywhere else (Quick Settings, GNOME
        // Settings) means the user wants that now: stop switching for them.
        this._styleSet = null;
        this._schemeId = this._interface.connect('changed::color-scheme', () => {
            const value = this._interface.get_string('color-scheme');
            if (this._styleSet !== null && value !== this._styleSet &&
                this._settings?.get_boolean('automatic-style'))
                this._settings.set_boolean('automatic-style', false);
            this._styleSet = null;
            this._syncGtkTheme();
        });
        this._accentId = this._interface.settings_schema.has_key('accent-color')
            ? this._interface.connect('changed::accent-color', () => this._syncGtkTheme()) : 0;
        this._syncGtkTheme();
        this._styleTimerId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, STYLE_CHECK_S, () => {
            this._syncStyle();
            this._syncFocus();
            return GLib.SOURCE_CONTINUE;
        });
        this._notifications = new Gio.Settings({schema_id: 'org.gnome.desktop.notifications'});
        this._assistant = new Assistant();
        this._liveText = new LiveText(this._assistant);
        this._dictation = new Dictation(this._assistant);
        this._prayer = this._settings?.settings_schema.has_key('prayer-times') ? new PrayerTimes(this._settings) : null;
        this._askKulan = new AskKulan({dictation: this._dictation, liveText: this._liveText});
        // "Ask Kulan" in the search bar opens the Ask Kulan panel with the words.
        this._spotlight = new Spotlight(text => this._askKulan?.open(text, true));
        // A calmer app grid: 72-pixel icons instead of GNOME's 96.
        this._appGridLayout = Main.overview._overview?.controls?._appDisplay?._grid?.layoutManager ?? null;
        if (this._appGridLayout && this._appGridLayout.fixedIconSize === -1)
            this._appGridLayout.fixedIconSize = APP_GRID_ICON_SIZE;
        else
            this._appGridLayout = null;
        this._vmDisplay = new VmDisplayKeeper();
        this._lightEffects = new LightEffects(() => this._applySettings());
        this._reopenApps = Main.sessionMode.isLocked ? null : new ReopenApps(this._settings);
        this._quickNote = new QuickNote();
        this._widgets = this._settings?.settings_schema.has_key('desktop-widgets')
            ? new DesktopWidgets(this._settings, () => this._quickNote?.open()) : null;
        this._clipboardId = this._settings?.connect('changed::clipboard-history', () => this._syncClipboard()) ?? 0;
        this._syncClipboard();
        this._addShortcuts();
        this._colorCancel = new Gio.Cancellable();
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, null,
            'org.gnome.SettingsDaemon.Color', '/org/gnome/SettingsDaemon/Color',
            'org.gnome.SettingsDaemon.Color', this._colorCancel, (_o, result) => {
                let proxy = null;
                try {
                    proxy = Gio.DBusProxy.new_for_bus_finish(result);
                } catch {
                    return; // cancelled by disable(), or no colour daemon
                }
                this._color = proxy;
                this._syncStyle();
            });

        this._reload();
        this._applySettings();
    }

    _applySettings() {
        const speed = this._settings?.get_string('animation-speed') ?? 'normal';
        const factor = ANIMATION_SPEEDS[speed] ?? ANIMATION_SPEEDS.normal;
        // No graphics acceleration (every frame is drawn by the processor) or
        // lighter mode: fewer frames (shorter animations) feel smoother.
        St.Settings.get().slow_down_factor = this._lightEffects?.software ? factor * ANIMATION_FACTOR : factor;
        if (this._indicator) {
            this._indicator.allowed = this._settings?.get_boolean('update-indicator') ?? true;
            this._indicator.update(readStatus());
        }
        this._syncAskButton();
        this._syncSession();
        this._syncStyle();
        this._syncFocus();
    }

    // Ask Kulan's button in the top bar (Kulan Settings › Top bar), when
    // Kulan Assistant is installed.
    _syncAskButton() {
        const schema = this._settings?.settings_schema;
        const want = this._askKulan && (!schema?.has_key('ask-button') || this._settings.get_boolean('ask-button')) &&
            GLib.file_test('/usr/share/dbus-1/services/org.kulan.Assistant.Ask.service', GLib.FileTest.EXISTS);
        if (want && !this._askButton) {
            this._askButton = new PanelMenu.Button(0.0, 'Ask Kulan', true);
            this._askButton.add_child(new St.Icon({
                gicon: Gio.ThemedIcon.new_from_names(['org.kulan.Assistant-symbolic', 'kulan-logo-symbolic']),
                style_class: 'system-status-icon',
            }));
            this._askButton.connect('button-press-event', () => {
                this._askKulan?.toggle();
                return Clutter.EVENT_STOP;
            });
            Main.panel.addToStatusArea(`${this.uuid}-ask`, this._askButton, 1, 'right');
            this._askButton.container.visible = !Main.sessionMode.isLocked;
        } else if (!want && this._askButton) {
            this._askButton.destroy();
            this._askButton = null;
        }
    }

    // Kulan's own shortcuts, read from org.kulan.desktop (an older schema
    // without a key simply leaves that shortcut out).
    _addShortcuts() {
        this._shortcuts = [];
        if (!this._settings)
            return;
        for (const [key, action] of Object.entries(SHORTCUTS)) {
            if (!this._settings.settings_schema.has_key(key))
                continue;
            Main.wm.addKeybinding(key, this._settings, Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW, () => {
                    if (Main.overview.visible)
                        Main.overview.hide();
                    action(this);
                });
            this._shortcuts.push(key);
        }
        // Cmd+Space: Spotlight search, or Activities if chosen in Kulan
        // Settings > Keyboard.
        if (this._settings.settings_schema.has_key('search-shortcut')) {
            Main.wm.addKeybinding('search-shortcut', this._settings, Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW, () => {
                    if (this._settings?.get_string('search-style') === 'activities')
                        Main.overview.toggle();
                    else
                        this._spotlight?.toggle();
                });
            this._shortcuts.push('search-shortcut');
        }
    }

    _syncClipboard() {
        const on = this._settings?.get_boolean('clipboard-history') ?? false;
        if (on && !this._clipboard) {
            this._clipboard = new ClipboardHistory();
        } else if (!on && this._clipboard) {
            this._clipboard.destroy();
            this._clipboard = null;
        }
    }

    // Focus: Do Not Disturb between two times of day. Only the switch-over
    // moments change it, so turning Do Not Disturb on or off by hand in
    // between is kept. "focus-active" remembers (across logins and restarts)
    // that the schedule turned it on, so it is always turned off again.
    _syncFocus() {
        const kulan = this._settings;
        if (!kulan?.settings_schema.has_key('focus-active') || !this._notifications)
            return;
        const active = kulan.get_boolean('focus-active');
        let inside = false;
        if (kulan.get_boolean('focus-schedule')) {
            const from = kulan.get_double('focus-from');
            const to = kulan.get_double('focus-to');
            const now = GLib.DateTime.new_now_local();
            const hour = now.get_hour() + now.get_minute() / 60;
            inside = from > to ? hour >= from || hour < to : hour >= from && hour < to;
        }
        if (inside && !active) {
            kulan.set_boolean('focus-active', true);
            this._notifications.set_boolean('show-banners', false);
        } else if (!inside && active) {
            kulan.set_boolean('focus-active', false);
            this._notifications.set_boolean('show-banners', true);
        }
    }

    // "Automatic" appearance: dark from sunset to sunrise. GNOME's colour
    // daemon knows today's times when it knows the location.
    _syncStyle() {
        if (!this._settings?.get_boolean('automatic-style') || Main.sessionMode.isLocked)
            return;
        const hours = name => {
            const value = this._color?.get_cached_property(name)?.unpack();
            return typeof value === 'number' && value > 0 ? value : null;
        };
        const sunrise = hours('Sunrise') ?? FALLBACK_SUNRISE;
        const sunset = hours('Sunset') ?? FALLBACK_SUNSET;
        const now = GLib.DateTime.new_now_local();
        const hour = now.get_hour() + now.get_minute() / 60;
        const dark = sunset > sunrise ? hour >= sunset || hour < sunrise : hour >= sunset && hour < sunrise;
        const wanted = dark ? 'prefer-dark' : 'default';
        if (this._interface.get_string('color-scheme') !== wanted) {
            this._styleSet = wanted;
            this._interface.set_string('color-scheme', wanted);
        }
    }

    // Older (GTK 3) apps use the "Kulan-<accent>" themes (adw-gtk3 with the
    // accent): keep them on the accent and light or dark chosen for the rest
    // of the system. A theme someone picked themselves is left alone.
    _syncGtkTheme() {
        const current = this._interface?.get_string('gtk-theme') ?? '';
        if (!current.startsWith('Kulan-'))
            return;
        const accents = ['blue', 'teal', 'green', 'yellow', 'orange', 'red', 'pink', 'purple', 'slate'];
        const accent = this._interface.settings_schema.has_key('accent-color')
            ? this._interface.get_string('accent-color') : 'teal';
        const dark = this._interface.get_string('color-scheme') === 'prefer-dark';
        const wanted = `Kulan-${accents.includes(accent) ? accent : 'teal'}${dark ? '-dark' : ''}`;
        if (current !== wanted)
            this._interface.set_string('gtk-theme', wanted);
    }

    disable() {
        this._injections?.clear();
        this._injections = null;
        for (const key of this._shortcuts ?? [])
            Main.wm.removeKeybinding(key);
        this._shortcuts = [];
        if (this._clipboardId) {
            this._settings.disconnect(this._clipboardId);
            this._clipboardId = 0;
        }
        this._clipboard?.destroy();
        this._clipboard = null;
        this._liveText?.destroy();
        this._liveText = null;
        this._dictation?.destroy();
        this._dictation = null;
        this._prayer?.destroy();
        this._prayer = null;
        this._spotlight?.destroy();
        this._askButton?.destroy();
        this._askButton = null;
        this._askKulan?.destroy();
        this._askKulan = null;
        if (this._appGridLayout)
            this._appGridLayout.fixedIconSize = -1;
        this._appGridLayout = null;
        this._vmDisplay?.destroy();
        this._lightEffects?.destroy();
        this._lightEffects = null;
        this._reopenApps?.destroy();
        this._reopenApps = null;
        this._vmDisplay = null;
        this._spotlight = null;
        this._widgets?.destroy();
        this._widgets = null;
        this._quickNote?.destroy();
        this._quickNote = null;
        this._assistant?.destroy();
        this._assistant = null;
        this._notifications = null;
        if (this._settingsId) {
            this._settings.disconnect(this._settingsId);
            this._settingsId = 0;
        }
        this._settings = null;
        this._colorCancel?.cancel();
        this._colorCancel = null;
        this._color = null;
        if (this._accentId) {
            this._interface.disconnect(this._accentId);
            this._accentId = 0;
        }
        if (this._schemeId) {
            this._interface.disconnect(this._schemeId);
            this._schemeId = 0;
        }
        this._interface = null;
        if (this._styleTimerId) {
            GLib.source_remove(this._styleTimerId);
            this._styleTimerId = 0;
        }
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
        this._menu?.destroy();
        this._menu = null;
        if (this._oldSlowDown !== undefined) {
            St.Settings.get().slow_down_factor = this._oldSlowDown;
            this._oldSlowDown = undefined;
        }
    }

    // On the lock screen only the look stays; the menu and indicator hide.
    _syncSession() {
        const unlocked = !Main.sessionMode.isLocked;
        if (this._indicator)
            this._indicator.container.visible = unlocked;
        if (this._menu)
            this._menu.container.visible = unlocked && (this._settings?.get_boolean('kulan-menu') ?? true);
        if (this._askButton)
            this._askButton.container.visible = unlocked;
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
                `${plural(Number(lastRun.count), 'update')} installed. See what’s new in Kulan Settings.`);
        }
        this._lastRun = runTime;
    }
}
