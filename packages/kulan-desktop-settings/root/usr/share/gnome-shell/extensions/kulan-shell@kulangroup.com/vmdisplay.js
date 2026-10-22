// Kulan Shell: keep the virtual screen switched on in virtual machines.
//
// When the screen goes to sleep, GNOME switches the display output off. On a
// real monitor that saves power; in a VM window (UTM, VMware, VirtualBox,
// GNOME Boxes) it only replaces the picture with "Display output is not
// active", which looks like a crash. In VMs, Kulan turns the output straight
// back on: the screen still goes black and locks as usual, and a key press or
// mouse move brings the lock screen back.
//
// Mutter has no setting for this; its DisplayConfig D-Bus property
// PowerSaveMode (0 = on) is what gnome-settings-daemon itself sets.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const BUS_NAME = 'org.gnome.Mutter.DisplayConfig';
const OBJECT_PATH = '/org/gnome/Mutter/DisplayConfig';
const POWER_SAVE_ON = 0;

export class VmDisplayKeeper {
    constructor() {
        this._cancellable = new Gio.Cancellable();
        this._monitorManager = null;
        this._handler = 0;
        this._detectVm();
    }

    _detectVm() {
        let proc;
        try {
            proc = Gio.Subprocess.new(['systemd-detect-virt', '--vm'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        } catch (e) {
            return;
        }
        proc.communicate_utf8_async(null, this._cancellable, (p, result) => {
            let out = '';
            try {
                [, out] = p.communicate_utf8_finish(result);
            } catch (e) {
                return; // cancelled by destroy(), or the tool failed
            }
            const virt = (out ?? '').trim();
            if (virt && virt !== 'none')
                this._start();
        });
    }

    _start() {
        this._monitorManager = global.backend.get_monitor_manager();
        this._handler = this._monitorManager.connect('power-save-mode-changed',
            () => this._check());
    }

    _check() {
        Gio.DBus.session.call(BUS_NAME, OBJECT_PATH, 'org.freedesktop.DBus.Properties', 'Get',
            new GLib.Variant('(ss)', [BUS_NAME, 'PowerSaveMode']), null,
            Gio.DBusCallFlags.NONE, -1, this._cancellable, (conn, result) => {
                let mode;
                try {
                    mode = conn.call_finish(result).deepUnpack()[0].deepUnpack();
                } catch (e) {
                    return;
                }
                // -1 means DPMS isn't supported; 1-3 are the sleep modes.
                if (mode > POWER_SAVE_ON)
                    this._turnOn();
            });
    }

    _turnOn() {
        Gio.DBus.session.call(BUS_NAME, OBJECT_PATH, 'org.freedesktop.DBus.Properties', 'Set',
            new GLib.Variant('(ssv)', [BUS_NAME, 'PowerSaveMode', new GLib.Variant('i', POWER_SAVE_ON)]), null,
            Gio.DBusCallFlags.NONE, -1, this._cancellable, (conn, result) => {
                try {
                    conn.call_finish(result);
                } catch (e) {
                    if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        log(`Kulan Shell: couldn't keep the VM screen on: ${e.message}`);
                }
            });
    }

    destroy() {
        this._cancellable.cancel();
        if (this._monitorManager && this._handler)
            this._monitorManager.disconnect(this._handler);
        this._monitorManager = null;
        this._handler = 0;
    }
}
