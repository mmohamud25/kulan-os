// Dictation (Kulan Assistant): press Shift+Super+D, speak, press it again.
// The speech is turned into text on this computer (whisper.cpp) and typed
// where the cursor was. The recording lives in the private runtime folder
// only until it has been read, then it is deleted.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');
Gio._promisify(Gio.Subprocess.prototype, 'wait_async');

const TITLE = 'Dictation';
const MAX_SECONDS = 120;

export class Dictation {
    // assistant: Kulan Assistant (assistant.js), for its settings and pasting.
    constructor(assistant) {
        this._assistant = assistant;
        this._recorder = null;
        this._window = null;
        this._file = null;
        this._indicator = null;
        this._timer = 0;
        this._busy = false;
    }

    async toggle() {
        if (this._recorder) {
            await this._stop(true);
            return;
        }
        if (this._busy)
            return;
        if (!this._assistant?.enabled('dictation')) {
            Main.notify(TITLE, this._assistant?.installed
                ? 'Dictation is off. Turn it on in Kulan Settings > Assistant.'
                : 'Dictation needs Kulan Assistant. Install it from Kulan Settings > Add-ons.');
            return;
        }
        const recorder = GLib.find_program_in_path('pw-record');
        if (!recorder) {
            Main.notify(TITLE, 'Recording isn’t available (PipeWire’s pw-record is missing).');
            return;
        }
        const dir = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'kulan-assistant']);
        GLib.mkdir_with_parents(dir, 0o700);
        this._file = GLib.build_filenamev([dir, `dictation-${GLib.random_int()}.wav`]);
        this._window = global.display.focus_window;
        try {
            this._recorder = Gio.Subprocess.new(
                [recorder, '--rate', '16000', '--channels', '1', '--format', 's16', this._file],
                Gio.SubprocessFlags.STDERR_SILENCE);
        } catch (e) {
            logError(e, 'Kulan Shell: dictation');
            Main.notify(TITLE, 'Couldn’t start recording.');
            this._recorder = null;
            return;
        }
        this._showIndicator();
        // If the recorder stops by itself (no microphone, sound system not
        // running), say so instead of listening forever.
        const recording = this._recorder;
        recording.wait_async(null).then(() => {
            if (this._recorder === recording)
                this._failed('Couldn’t record from the microphone. Check it in Settings > Sound.');
        }).catch(() => {});
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, MAX_SECONDS, () => {
            this._timer = 0;
            this._stop(true);
            return GLib.SOURCE_REMOVE;
        });
    }

    _showIndicator() {
        this._indicator = new PanelMenu.Button(0.0, 'Dictation', true);
        const box = new St.BoxLayout({style_class: 'kulan-dictation'});
        box.add_child(new St.Icon({icon_name: 'audio-input-microphone-symbolic', style_class: 'system-status-icon'}));
        this._label = new St.Label({text: 'Listening… press Shift+Super+D to finish',
            y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._label);
        this._indicator.add_child(box);
        this._indicator.connect('button-press-event', () => {
            this._stop(true);
            return true;
        });
        Main.panel.addToStatusArea('kulan-dictation', this._indicator, 0, 'center');
    }

    _hideIndicator() {
        this._indicator?.destroy();
        this._indicator = null;
        this._label = null;
    }

    _failed(message) {
        this._recorder = null;
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        this._hideIndicator();
        this._removeFile(this._file);
        this._window = null;
        Main.notify(TITLE, message);
    }

    _removeFile(file) {
        try {
            if (file)
                Gio.File.new_for_path(file).delete(null);
        } catch {
            // never written
        }
    }

    async _stop(type) {
        if (!this._recorder)
            return;
        const recorder = this._recorder;
        this._recorder = null;
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        this._busy = true;
        const file = this._file;
        try {
            recorder.send_signal(2); // SIGINT: pw-record finishes the file
            await recorder.wait_async(null);
            if (!type)
                return;
            const info = Gio.File.new_for_path(file).query_info('standard::size', Gio.FileQueryInfoFlags.NONE, null);
            if (info.get_size() < 16000) { // under half a second of sound
                Main.notify(TITLE, 'Nothing was recorded. Check the microphone in Settings > Sound.');
                return;
            }
            this._label?.set_text('Writing what you said…');
            const proc = Gio.Subprocess.new(['kulan-assistant', 'dictate', file],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            const [out, err] = await proc.communicate_utf8_async(null, null);
            const text = (out ?? '').trim();
            if (!proc.get_successful()) {
                Main.notify(TITLE, (err ?? '').replace(/^kulan-assistant: /, '').trim() ||
                    'Couldn’t turn the recording into text.');
                return;
            }
            if (!text) {
                Main.notify(TITLE, 'No speech heard. Check the microphone in Settings > Sound.');
                return;
            }
            this._hideIndicator();
            await this._assistant._paste(this._window, `${text} `);
        } catch (e) {
            logError(e, 'Kulan Shell: dictation');
            Main.notify(TITLE, 'Couldn’t finish the dictation.');
        } finally {
            this._hideIndicator();
            this._removeFile(file);
            this._window = null;
            this._busy = false;
        }
    }

    destroy() {
        if (this._recorder)
            this._stop(false);
        this._hideIndicator();
    }
}
