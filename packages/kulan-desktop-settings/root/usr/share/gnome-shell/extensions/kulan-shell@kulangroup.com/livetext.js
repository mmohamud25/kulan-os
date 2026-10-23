// Live Text: drag over anything on screen (a picture, a video, an app that
// won't let you select) and its text is copied. Recognised on this computer
// by Tesseract; nothing leaves it.

import GdkPixbuf from 'gi://GdkPixbuf';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {SelectArea} from 'resource:///org/gnome/shell/ui/screenshot.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');
Gio._promisify(Gio.OutputStream.prototype, 'close_async');

const TITLE = 'Live Text';
const MIN_SIZE = 6;
// Tesseract reads best at about 300 dpi; small text on a 1x screen is
// enlarged first.
const UPSCALE_BELOW = 2;
const TIMEOUT_S = 60;

export class LiveText {
    // assistant: Kulan Assistant (assistant.js), for Translate and Explain.
    constructor(assistant = null) {
        this._assistant = assistant;
        this._busy = false;
        this._cancellable = null;
    }

    // onText: where the text goes (Ask Kulan); without it, it's copied.
    async start(onText = null) {
        if (this._busy)
            return;
        const tesseract = GLib.find_program_in_path('tesseract');
        if (!tesseract) {
            Main.notify(TITLE, 'Text recognition isn’t installed. In the Terminal: sudo apt install tesseract-ocr');
            return;
        }
        this._busy = true;
        this._cancellable = new Gio.Cancellable();
        const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), `kulan-live-text-${GLib.random_int()}.png`]);
        const file = Gio.File.new_for_path(path);
        try {
            const area = await new SelectArea().selectAsync();
            if (!area || area.width < MIN_SIZE || area.height < MIN_SIZE)
                return; // Esc, or just a click

            // Let the selection outline disappear before the capture.
            await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
                resolve();
                return GLib.SOURCE_REMOVE;
            }));
            const stream = file.replace(null, false,
                Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            await new Shell.Screenshot().screenshot_area(area.x, area.y, area.width, area.height, stream);
            await stream.close_async(GLib.PRIORITY_DEFAULT, null);

            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            if (scale < UPSCALE_BELOW) {
                const pixbuf = GdkPixbuf.Pixbuf.new_from_file(path);
                pixbuf.scale_simple(pixbuf.get_width() * 2, pixbuf.get_height() * 2,
                    GdkPixbuf.InterpType.BILINEAR).savev(path, 'png', [], []);
            }

            const text = await this._recognise(tesseract, path);
            if (!text) {
                Main.notify(TITLE, 'No text found there. Try a larger area.');
                return;
            }
            if (onText) {
                onText(text);
                return;
            }
            St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
            const preview = text.length > 120 ? `${text.slice(0, 117)}…` : text;
            this._notifyCopied(text, preview);
        } catch (e) {
            if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)) {
                logError(e, 'Kulan Shell: Live Text');
                Main.notify(TITLE, 'Couldn’t read the text there.');
            }
        } finally {
            try {
                file.delete(null);
            } catch {
                // never written
            }
            this._cancellable = null;
            this._busy = false;
        }
    }

    _notifyCopied(text, preview) {
        const body = `Copied. Paste it anywhere.\n${preview}`;
        if (!this._assistant?.enabled('live-text-actions')) {
            Main.notify(TITLE, body);
            return;
        }
        const source = MessageTray.getSystemSource();
        const notification = new MessageTray.Notification({source, title: TITLE, body});
        notification.addAction('Translate', () => this._assistant?.show('translate', text));
        notification.addAction('Explain', () => this._assistant?.show('explain', text));
        source.addNotification(notification);
    }

    // The languages to read: this computer's language first when its
    // recognition data is installed, then English and Arabic when they are
    // (Kulan OS ships both), at most three (each one slows recognition).
    _languages() {
        const installed = new Set();
        for (const dir of ['/usr/share/tesseract-ocr/5/tessdata', '/usr/share/tesseract-ocr/4.00/tessdata',
            '/usr/share/tessdata']) {
            try {
                const listing = Gio.File.new_for_path(dir).enumerate_children('standard::name',
                    Gio.FileQueryInfoFlags.NONE, null);
                let info;
                while ((info = listing.next_file(null)) !== null) {
                    const name = info.get_name();
                    if (name.endsWith('.traineddata') && name !== 'osd.traineddata')
                        installed.add(name.slice(0, -'.traineddata'.length));
                }
            } catch {
                // not there
            }
        }
        const codes = {ar: 'ara', fr: 'fra', es: 'spa', de: 'deu', it: 'ita', pt: 'por', nl: 'nld', sv: 'swe',
            tr: 'tur', ru: 'rus', zh: 'chi_sim', ja: 'jpn', ko: 'kor', hi: 'hin', ur: 'urd', sw: 'swa', am: 'amh',
            fa: 'fas', id: 'ind', pl: 'pol', uk: 'ukr', so: 'som'};
        const lang = (GLib.get_language_names()[0] ?? 'en').slice(0, 2);
        const wanted = [codes[lang], 'eng', 'ara'].filter(code => code && installed.has(code));
        const unique = [...new Set(wanted)].slice(0, 3);
        return unique.length > 0 ? unique.join('+') : 'eng';
    }

    async _recognise(tesseract, path) {
        const proc = Gio.Subprocess.new([tesseract, path, 'stdout', '-l', this._languages()],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        const cancellable = this._cancellable;
        let timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, TIMEOUT_S, () => {
            timer = 0;
            proc.force_exit();
            return GLib.SOURCE_REMOVE;
        });
        try {
            const [out] = await proc.communicate_utf8_async(null, cancellable);
            if (!proc.get_successful())
                return '';
            // Tidy Tesseract's output: no form feeds, no trailing spaces,
            // at most one blank line between paragraphs.
            return (out ?? '').replace(/\f/g, '').replace(/[ \t]+$/gm, '')
                .replace(/\n{3,}/g, '\n\n').trim();
        } finally {
            if (timer)
                GLib.source_remove(timer);
        }
    }

    destroy() {
        this._cancellable?.cancel();
    }
}
