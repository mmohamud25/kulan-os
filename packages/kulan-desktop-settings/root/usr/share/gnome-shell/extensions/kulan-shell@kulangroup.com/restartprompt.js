// Restart to finish updating, Mac-style (Kulan OS 2.12).
//
// When updates that need a restart have installed:
//  - installed from Kulan Settings' Software Update: Kulan Settings itself
//    shows GNOME's restart countdown (60 seconds; an app with unsaved work
//    stops it) and leaves a note in the runtime folder, so nothing more here
//    straight away;
//  - otherwise (automatic updates, an IT admin's Install updates, the
//    terminal): a notice with Restart Now and Later. Later (or no answer)
//    asks again 4 hours later.
//  - Either way, at night (1 to 5 am), once nobody has used the computer for
//    30 minutes, it restarts by itself, as a Mac's "Try Tonight" does: only
//    when no app says it has unsaved work (GNOME's "inhibit logout"), no
//    update is running, it's on mains power, and nobody else is signed in
//    (logind refuses then).

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as SystemActions from 'resource:///org/gnome/shell/misc/systemActions.js';

const TITLE = 'Restart to finish updating';
const REMIND_S = 4 * 3600;
const NIGHT_CHECK_S = 600;
const NIGHT_FROM = 1;
const NIGHT_UNTIL = 5; // hours, local time: from 1:00 up to 4:59
const QUIET_MS = 30 * 60 * 1000;
// Kulan Settings' note that it showed the countdown itself (kulan_settings/updates.py).
const ASKED_NOTE = 'kulan-restart-asked';
const ASKED_FRESH_S = 15 * 60;
const INHIBIT_LOGOUT = 1;

export function restartNeeded(status) {
    const since = Number(status?.running_since) || 0;
    const running = status?.state === 'running' && Date.now() / 1000 - since < 6 * 3600;
    return !running && Array.isArray(status?.restart_required) && status.restart_required.length > 0;
}

// Whether a time (a Date) is in the night window.
export function isNight(date) {
    const hour = date.getHours();
    return hour >= NIGHT_FROM && hour < NIGHT_UNTIL;
}

export class RestartPrompt {
    constructor() {
        this._needed = false;
        this._notification = null;
        this._remindId = 0;
        this._nightId = 0;
        this._restarting = false;
    }

    update(status) {
        const needed = restartNeeded(status);
        if (needed && !this._needed) {
            if (this._askedBySettings())
                this._remindLater();
            else
                this._notify(status);
            this._watchNight();
        } else if (!needed && this._needed) {
            this._stop();
        }
        this._needed = needed;
    }

    _askedBySettings() {
        try {
            const file = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_runtime_dir(), ASKED_NOTE]));
            const info = file.query_info('time::modified', Gio.FileQueryInfoFlags.NONE, null);
            return Date.now() / 1000 - info.get_modification_date_time().to_unix() < ASKED_FRESH_S;
        } catch {
            return false;
        }
    }

    _notify(status) {
        this._clearNotification();
        const count = Number(status?.last_run?.count) || 0;
        const body = (count > 0 ? `${count} update${count === 1 ? '' : 's'} installed. ` : '') +
            'Restart to finish. Apps with unsaved work ask you first.';
        try {
            const source = MessageTray.getSystemSource();
            const notification = new MessageTray.Notification({
                source, title: TITLE, body, iconName: 'system-reboot-symbolic',
                urgency: MessageTray.Urgency.HIGH,
            });
            notification.addAction('Restart Now', () => this.restartNow());
            notification.addAction('Later', () => this._remindLater());
            notification.connect('destroy', () => {
                if (this._notification === notification)
                    this._notification = null;
            });
            this._notification = notification;
            source.addNotification(notification);
        } catch (e) {
            logError(e, 'Kulan Shell: restart notice');
        }
        // Dismissed, or not answered: asked again later all the same.
        this._remindLater();
    }

    // GNOME's own restart window: a 60-second countdown, stopped by any app
    // with unsaved work, with Cancel.
    restartNow() {
        try {
            SystemActions.getDefault().activateRestart();
        } catch (e) {
            logError(e, 'Kulan Shell: restart');
        }
    }

    _remindLater() {
        if (this._remindId)
            GLib.source_remove(this._remindId);
        this._remindId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, REMIND_S, () => {
            this._remindId = 0;
            if (this._needed)
                this._notify(null);
            return GLib.SOURCE_REMOVE;
        });
    }

    _watchNight() {
        if (this._nightId)
            return;
        this._nightId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, NIGHT_CHECK_S, () => {
            if (this._needed && !this._restarting && isNight(new Date()))
                this._tryQuietRestart().catch(e => logError(e, 'Kulan Shell: quiet restart'));
            return GLib.SOURCE_CONTINUE;
        });
    }

    async _tryQuietRestart() {
        let idle = 0;
        try {
            idle = global.backend.get_core_idle_monitor().get_idletime();
        } catch {
            return;
        }
        if (idle < QUIET_MS)
            return;
        // An app with unsaved work (or anything else that asked not to be
        // signed out) keeps the computer as it is.
        const [inhibited] = (await call(Gio.DBus.session, 'org.gnome.SessionManager', '/org/gnome/SessionManager',
            'org.gnome.SessionManager', 'IsInhibited', new GLib.Variant('(u)', [INHIBIT_LOGOUT]), '(b)')).deepUnpack();
        if (inhibited)
            return;
        const [onBattery] = (await call(Gio.DBus.system, 'org.freedesktop.UPower', '/org/freedesktop/UPower',
            'org.freedesktop.DBus.Properties', 'Get', new GLib.Variant('(ss)', ['org.freedesktop.UPower', 'OnBattery']),
            '(v)').catch(() => new GLib.Variant('(v)', [new GLib.Variant('b', false)]))).deepUnpack();
        if (onBattery.deepUnpack())
            return;
        if (!this._needed || !isNight(new Date()))
            return;
        this._restarting = true;
        log('Kulan Shell: restarting to finish updating (night, quiet for 30 minutes, nothing unsaved)');
        try {
            // Not interactive: with someone else signed in, logind refuses
            // (it would ask for a password nobody is here to type).
            await call(Gio.DBus.system, 'org.freedesktop.login1', '/org/freedesktop/login1',
                'org.freedesktop.login1.Manager', 'Reboot', new GLib.Variant('(b)', [false]), null);
        } catch (e) {
            this._restarting = false;
            if (!this._refusedOnce)
                log(`Kulan Shell: no restart tonight: ${e.message}`);
            this._refusedOnce = true;
        }
    }

    _clearNotification() {
        const old = this._notification;
        this._notification = null;
        old?.destroy();
    }

    _stop() {
        this._clearNotification();
        if (this._remindId) {
            GLib.source_remove(this._remindId);
            this._remindId = 0;
        }
        if (this._nightId) {
            GLib.source_remove(this._nightId);
            this._nightId = 0;
        }
    }

    destroy() {
        this._stop();
        this._needed = false;
    }
}

function call(bus, name, path, iface, method, args, replyType) {
    return new Promise((resolve, reject) => {
        bus.call(name, path, iface, method, args, replyType ? new GLib.VariantType(replyType) : null,
            Gio.DBusCallFlags.NONE, 10000, null, (conn, result) => {
                try {
                    resolve(conn.call_finish(result));
                } catch (e) {
                    reject(e);
                }
            });
    });
}
