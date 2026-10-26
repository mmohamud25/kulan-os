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
        this._transcriber = null;
        this._cancelled = false;
    }

    get isRecording() {
        return this._recorder !== null;
    }

    // onText: where the words go (Ask Kulan); without it, they're typed into
    // the window that was in front. onEnd: called when it's over, either way.
    async toggle(onText = null, onEnd = null) {
        if (this._recorder) {
            await this._stop(true);
            return;
        }
        if (this._busy)
            return;
        if (!this._assistant?.installed) {
            Main.notify(TITLE, 'Dictation needs Kulan Assistant. Turn it on in Kulan Settings › Add-ons.');
            return;
        }
        if (!this._assistant.enabled('dictation')) {
            this._assistant.notifyFix('Dictation is off. Turn it on in Kulan Settings › Assistant.', 'assistant');
            return;
        }
        // Before recording, not after you've spoken.
        if (!this._assistant.hasModel('dictation')) {
            this._assistant.notifyFix('Dictation needs a speech model first. Download one for free in ' +
                'Kulan Settings › Assistant.', 'assistant');
            return;
        }
        const recorder = GLib.find_program_in_path('pw-record');
        if (!recorder) {
            Main.notify(TITLE, 'Recording isn’t available on this computer.');
            return;
        }
        const dir = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'kulan-assistant']);
        GLib.mkdir_with_parents(dir, 0o700);
        this._file = GLib.build_filenamev([dir, `dictation-${GLib.random_int()}.wav`]);
        this._window = global.display.focus_window;
        // Only now, after every reason not to record: an earlier dictation
        // still writing out its words keeps its own destination.
        this._onText = onText;
        this._onEnd = onEnd;
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
                this._failed('Couldn’t record from the microphone. Check it in Kulan Settings › Sound.');
        }).catch(() => {});
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, MAX_SECONDS, () => {
            this._timer = 0;
            this._stop(true);
            return GLib.SOURCE_REMOVE;
        });
    }

    // The shortcut as set in Kulan Settings > Keyboard, written the way people read it.
    _shortcut() {
        try {
            const accel = new Gio.Settings({schema_id: 'org.kulan.desktop'}).get_strv('dictation-shortcut')[0];
            if (accel) {
                return accel.replace(/<Super>/g, 'Super+').replace(/<Shift>/g, 'Shift+')
                    .replace(/<(Control|Primary)>/g, 'Ctrl+').replace(/<Alt>/g, 'Alt+')
                    .replace(/\+([a-z])$/, (_m, c) => `+${c.toUpperCase()}`);
            }
        } catch {
            // older settings without the key
        }
        return 'the Dictation shortcut';
    }

    _showIndicator() {
        this._indicator = new PanelMenu.Button(0.0, 'Dictation', true);
        const box = new St.BoxLayout({style_class: 'kulan-dictation'});
        box.add_child(new St.Icon({icon_name: 'audio-input-microphone-symbolic', style_class: 'system-status-icon'}));
        this._label = new St.Label({text: `Listening… press ${this._shortcut()} to finish`,
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
        this._onText = null;
        this._ended();
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
                Main.notify(TITLE, 'Nothing was recorded. Check the microphone in Kulan Settings › Sound.');
                return;
            }
            this._label?.set_text('Writing what you said…');
            this._cancelled = false;
            const proc = Gio.Subprocess.new(['kulan-assistant', 'dictate', file],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            this._transcriber = proc;
            const [out, err] = await proc.communicate_utf8_async(null, null);
            this._transcriber = null;
            // Locked or turned off meanwhile: the words go nowhere.
            if (this._cancelled || Main.sessionMode.isLocked)
                return;
            const text = (out ?? '').trim();
            if (!proc.get_successful()) {
                Main.notify(TITLE, (err ?? '').replace(/^kulan-assistant: /, '').trim() ||
                    'Couldn’t turn the recording into text.');
                return;
            }
            if (!text) {
                Main.notify(TITLE, 'No speech heard. Check the microphone in Kulan Settings › Sound.');
                return;
            }
            this._hideIndicator();
            if (this._onText)
                this._onText(text);
            else
                await this._assistant._paste(this._window, `${text} `);
        } catch (e) {
            logError(e, 'Kulan Shell: dictation');
            Main.notify(TITLE, 'Couldn’t finish the dictation.');
        } finally {
            this._hideIndicator();
            this._removeFile(file);
            this._window = null;
            this._onText = null;
            this._busy = false;
            this._ended();
        }
    }

    _ended() {
        const onEnd = this._onEnd;
        this._onEnd = null;
        onEnd?.();
    }

    destroy() {
        if (this._recorder)
            this._stop(false);
        if (this._transcriber) {
            this._cancelled = true;
            this._transcriber.force_exit();
        }
        this._hideIndicator();
    }
}
