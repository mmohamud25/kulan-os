// Kulan Shell: windows open fully on screen, never under the dock or the top bar.
//
// An app may open a window bigger than the screen, or partly off it: Chrome,
// for example, opens at the size it had last time, which may have been on a
// bigger screen or before the dock took its place. Its bottom then runs under
// the dock and off the screen. When a window first shows, Kulan moves it (and
// makes it smaller if needed) to fit the space apps may use: the screen less
// the top bar and the dock. Only when it opens: a window someone drags under
// the dock afterwards stays where they put it. Not maximised or full-screen
// windows (they already fit), nor dialogs, menus and the desktop icons.

import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import {fitRect} from './fitrect.js';

// Apps often set their size just after their first picture (Chrome restores
// its bounds then): checked at the first picture and once more after this.
const SETTLE_MS = 600;

export class WindowFit {
    constructor() {
        this._watched = new Map(); // window -> {actor, firstFrameId, unmanagedId, timeoutId}
        this._createdId = global.display.connect('window-created', (_d, win) => this._watch(win));
    }

    _watch(win) {
        if (!win || this._watched.has(win) || win.get_window_type() !== Meta.WindowType.NORMAL ||
            win.is_skip_taskbar() || win.get_transient_for())
            return;
        const entry = {actor: null, firstFrameId: 0, unmanagedId: 0, timeoutId: 0};
        this._watched.set(win, entry);
        entry.unmanagedId = win.connect('unmanaged', () => this._forget(win));
        const actor = win.get_compositor_private();
        if (actor) {
            entry.actor = actor;
            entry.firstFrameId = actor.connect('first-frame', () => this._firstFrame(win));
        } else {
            this._firstFrame(win);
        }
    }

    _firstFrame(win) {
        const entry = this._watched.get(win);
        if (!entry)
            return;
        if (entry.actor && entry.firstFrameId) {
            entry.actor.disconnect(entry.firstFrameId);
            entry.firstFrameId = 0;
        }
        this._fit(win);
        entry.timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, SETTLE_MS, () => {
            entry.timeoutId = 0;
            this._fit(win);
            this._forget(win);
            return GLib.SOURCE_REMOVE;
        });
    }

    _fit(win) {
        try {
            // GNOME 49 has is_maximized(); GNOME 46 to 48 get_maximized().
            const maximized = win.is_maximized ? win.is_maximized() : win.get_maximized() !== 0;
            if (win.is_fullscreen() || win.minimized || maximized)
                return;
            const area = win.get_work_area_current_monitor();
            const rect = win.get_frame_rect();
            if (!win.allows_resize() && (rect.width > area.width || rect.height > area.height))
                return;
            const fit = fitRect(rect, area);
            if (fit)
                win.move_resize_frame(true, fit.x, fit.y, fit.width, fit.height);
        } catch (e) {
            // The window went while being checked: nothing to fit.
        }
    }

    _forget(win) {
        const entry = this._watched.get(win);
        if (!entry)
            return;
        this._watched.delete(win);
        if (entry.timeoutId)
            GLib.source_remove(entry.timeoutId);
        if (entry.actor && entry.firstFrameId)
            entry.actor.disconnect(entry.firstFrameId);
        if (entry.unmanagedId)
            win.disconnect(entry.unmanagedId);
    }

    destroy() {
        if (this._createdId)
            global.display.disconnect(this._createdId);
        this._createdId = 0;
        for (const win of [...this._watched.keys()])
            this._forget(win);
    }
}
