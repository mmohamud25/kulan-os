// Small files written without making the desktop wait (2.11). GNOME Shell
// draws the whole screen on one thread, so a write there that waits on the
// disk (a slow or busy disk, a network home folder) freezes everything.
//
// writeFile() and removeFile() hand the work to GIO's own threads and call
// back on the Shell's thread when it's done. Each file is still written whole
// and renamed into place (never half a file after a crash), and writes to the
// same file happen one after another in the order asked, so an older write
// never lands after a newer one: while one is on its way, only the newest
// waiting text is kept. pending() gives that newest text, so what's read back
// is always what was last written, even before it reaches the disk.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

// path -> {busy, next: {text} | {remove: true} | null, latest, waiting: [callbacks]}
const queues = new Map();
const madeDirs = new Set();

// The newest content asked for that may not be on the disk yet: a string, null
// when the file is being removed, or undefined when nothing is waiting.
export function pending(path) {
    const queue = queues.get(path);
    if (!queue)
        return undefined;
    return queue.latest.remove ? null : queue.latest.text;
}

// What's in a small file now (what was last written, even if it's still on its
// way to the disk), or null when there's none.
export function readText(path) {
    const waiting = pending(path);
    if (waiting !== undefined)
        return waiting;
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        return ok ? new TextDecoder().decode(bytes) : null;
    } catch {
        return null;
    }
}

function queueFor(path) {
    let queue = queues.get(path);
    if (!queue) {
        queue = {busy: false, next: null, latest: null, waiting: []};
        queues.set(path, queue);
    }
    return queue;
}

function ask(path, job, done) {
    const queue = queueFor(path);
    queue.next = job;
    queue.latest = job;
    if (done)
        queue.waiting.push(done);
    if (!queue.busy)
        start(path, queue);
}

// Writes text to path (made private to this user with {private: true}).
// done(error) is called on the Shell's thread: error is null once this text,
// or a newer one, is on the disk.
export function writeFile(path, text, done = null, {private: own = false, dirMode = 0o700} = {}) {
    ask(path, {text, own, dirMode}, done);
}

// Removes path (fine if it isn't there); done(error) as for writeFile.
export function removeFile(path, done = null) {
    ask(path, {remove: true}, done);
}

function start(path, queue) {
    const job = queue.next;
    const callbacks = queue.waiting;
    queue.next = null;
    queue.waiting = [];
    queue.busy = true;
    const file = Gio.File.new_for_path(path);
    const finish = error => {
        queue.busy = false;
        if (queue.next)
            start(path, queue);
        else
            queues.delete(path); // what's on the disk is the newest now
        for (const callback of callbacks) {
            try {
                callback(error);
            } catch (e) {
                logError(e, 'Kulan Shell: after saving a file');
            }
        }
    };
    if (job.remove) {
        file.delete_async(GLib.PRIORITY_DEFAULT, null, (f, result) => {
            try {
                f.delete_finish(result);
                finish(null);
            } catch (e) {
                finish(e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND) ? null : e);
            }
        });
        return;
    }
    const dir = GLib.path_get_dirname(path);
    const flags = Gio.FileCreateFlags.REPLACE_DESTINATION | (job.own ? Gio.FileCreateFlags.PRIVATE : 0);
    const bytes = new GLib.Bytes(new TextEncoder().encode(job.text));
    const attempt = again => {
        try {
            if (!madeDirs.has(dir)) {
                // Once per folder: the folder is almost always there already.
                GLib.mkdir_with_parents(dir, job.dirMode);
                madeDirs.add(dir);
            }
        } catch (e) {
            finish(e);
            return;
        }
        const written = (error, retry) => {
            if (error && again && error.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND)) {
                madeDirs.delete(dir); // the folder was removed meanwhile: make it again, once
                retry();
            } else {
                finish(error);
            }
        };
        if (bytes.get_size() === 0) {
            // An empty file (a "done once" marker): GIO can't write zero bytes from
            // an empty buffer, so the file is made and closed, still renamed into place.
            file.replace_async(null, false, flags, GLib.PRIORITY_DEFAULT, null, (f, result) => {
                let stream;
                try {
                    stream = f.replace_finish(result);
                } catch (e) {
                    written(e, () => attempt(false));
                    return;
                }
                stream.close_async(GLib.PRIORITY_DEFAULT, null, (s, closed) => {
                    try {
                        s.close_finish(closed);
                        finish(null);
                    } catch (e) {
                        finish(e);
                    }
                });
            });
            return;
        }
        file.replace_contents_bytes_async(bytes, null, false, flags, null, (f, result) => {
            try {
                f.replace_contents_finish(result);
                finish(null);
            } catch (e) {
                written(e, () => attempt(false));
            }
        });
    };
    attempt(true);
}

// The same, for async functions: resolves when written, rejects with the error.
export function writeFileAsync(path, text, options = {}) {
    return new Promise((resolve, reject) => {
        writeFile(path, text, error => (error ? reject(error) : resolve()), options);
    });
}

// A small file's text without waiting on the Shell's thread (null when there's none).
export function loadText(path) {
    const waiting = pending(path);
    if (waiting !== undefined)
        return Promise.resolve(waiting);
    return new Promise(resolve => {
        Gio.File.new_for_path(path).load_contents_async(null, (f, result) => {
            try {
                const [, bytes] = f.load_contents_finish(result);
                resolve(new TextDecoder().decode(bytes));
            } catch {
                resolve(null);
            }
        });
    });
}

// Words for why a save failed, for people.
// Why a file couldn't be saved, in plain words (the error itself goes to the log).
export function failureText(error) {
    if (error?.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NO_SPACE))
        return 'the disk is full';
    if (error)
        log(`Kulan Shell: couldn't save a file: ${error.message ?? error}`);
    return 'the disk couldn’t be written to';
}

// What to do about it.
export function failureAdvice(why) {
    return why === 'the disk is full' ? 'Free up some space and try again.' : 'Try again in a moment.';
}
