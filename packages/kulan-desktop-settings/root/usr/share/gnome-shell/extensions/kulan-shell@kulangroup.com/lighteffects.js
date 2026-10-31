// Kulan Shell: lighter effects when the computer has no graphics acceleration,
// or in lighter mode (computers with little memory; /usr/lib/kulan/lighter-mode
// writes /run/kulan/lighter-mode, and Kulan Settings › Battery and power
// switches it).
//
// Without a working graphics driver (virtual machines without 3D, some old
// PCs) the processor draws everything itself (Mesa's llvmpipe), and the live
// blur behind menus and app folders makes the whole desktop stutter. There,
// Kulan Shell turns those live blurs off (the top bar and dock keep their
// blur, which is drawn once); extension.js also makes animations a quarter
// shorter there (ANIMATION_FACTOR), on top of the chosen animation speed.
//
// What it changed is written to ~/.local/share/kulan/light-effects.json, so
// the user's own choices come back as they were once acceleration works.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {readText, removeFile, writeFile} from './files.js';

const BLUR_UUID = 'blur-my-shell@aunetx';
const BLUR_SCHEMA = 'org.gnome.shell.extensions.blur-my-shell';
// Blurred live, every frame: the expensive ones.
const LIVE_BLURS = ['popup', 'appfolder', 'window-list', 'coverflow-alt-tab'];
export const ANIMATION_FACTOR = 0.75;
const LIGHTER_FLAG = '/run/kulan/lighter-mode';

export function lighterMode() {
    return GLib.file_test(LIGHTER_FLAG, GLib.FileTest.EXISTS);
}

function stateFile() {
    return GLib.build_filenamev([GLib.get_user_data_dir(), 'kulan', 'light-effects.json']);
}

function readState() {
    try {
        return JSON.parse(readText(stateFile()) ?? 'null');
    } catch (e) {
        return null;
    }
}

export function hardwareAccelerated() {
    try {
        return global.backend.is_rendering_hardware_accelerated();
    } catch (e) {
        return true; // can't tell: leave everything as it is
    }
}

export class LightEffects {
    constructor(onChange = null) {
        this._source = null;
        this._accelerated = hardwareAccelerated();
        this._onChange = onChange;
        this.software = !this._accelerated || lighterMode();
        // Lighter mode switched in Kulan Settings: follow it straight away.
        this._monitor = Gio.File.new_for_path(LIGHTER_FLAG).monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._monitorId = this._monitor.connect('changed', () => {
            const light = !this._accelerated || lighterMode();
            if (light === this.software)
                return;
            this.software = light;
            this._sync();
            this._onChange?.();
        });
        // Blur my Shell may be enabled after Kulan Shell; check again then.
        this._stateId = Main.extensionManager.connect('extension-state-changed', (_m, ext) => {
            if (ext?.uuid === BLUR_UUID)
                this._sync();
        });
        this._sync();
    }

    _blurSettings(part) {
        if (!this._source) {
            const ext = Main.extensionManager.lookup(BLUR_UUID);
            const dir = ext?.dir?.get_child('schemas');
            const fallback = Gio.SettingsSchemaSource.get_default();
            try {
                this._source = dir?.query_exists(null)
                    ? Gio.SettingsSchemaSource.new_from_directory(dir.get_path(), fallback, false) : fallback;
            } catch (e) {
                this._source = fallback;
            }
        }
        const schema = this._source?.lookup(`${BLUR_SCHEMA}.${part}`, true);
        if (!schema || !schema.has_key('blur'))
            return null;
        return new Gio.Settings({settings_schema: schema});
    }

    _sync() {
        const saved = readState();
        if (this.software && !saved) {
            // Turn the live blurs off, remembering how they were.
            const before = {};
            for (const part of LIVE_BLURS) {
                const settings = this._blurSettings(part);
                if (!settings)
                    continue;
                before[part] = settings.get_boolean('blur');
                if (before[part])
                    settings.set_boolean('blur', false);
            }
            if (Object.keys(before).length === 0)
                return; // Blur my Shell isn't there (yet)
            // Without making the desktop wait (files.js); readState() sees it at once.
            writeFile(stateFile(), JSON.stringify({blur: before}), error => {
                if (error)
                    logError(error, 'Kulan Shell: could not save the effects state');
            });
        } else if (!this.software && saved) {
            // Acceleration works now: put back what was there before.
            for (const [part, value] of Object.entries(saved.blur ?? {})) {
                const settings = this._blurSettings(part);
                if (settings && value === true)
                    settings.set_boolean('blur', true);
            }
            removeFile(stateFile(), error => {
                if (error)
                    logError(error, 'Kulan Shell: could not forget the effects state');
            });
        }
    }

    destroy() {
        if (this._monitorId)
            this._monitor.disconnect(this._monitorId);
        this._monitor?.cancel();
        this._monitor = null;
        this._monitorId = 0;
        if (this._stateId)
            Main.extensionManager.disconnect(this._stateId);
        this._stateId = 0;
    }
}
