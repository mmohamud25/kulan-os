// Spotlight-style search (Cmd+Space): a search bar in the middle of the
// screen, over the windows, instead of the full Activities overview. It asks
// the same search sources Activities uses, so it finds apps, files (Files'
// own search), Kulan Settings pages, GNOME Settings pages, sums (Calculator),
// characters, Kulan Assistant answers and whatever else is installed, in the
// order chosen in GNOME Settings > Search. Arrow keys move, Enter opens,
// Escape clears and then closes.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as RemoteSearch from 'resource:///org/gnome/shell/ui/remoteSearch.js';
import * as SystemActions from 'resource:///org/gnome/shell/misc/systemActions.js';
import {AppSearchProvider} from 'resource:///org/gnome/shell/ui/appDisplay.js';

const DEBOUNCE_MS = 120;
const ICON_SIZE = 32;
const MAX_APPS = 5;
const MAX_PER_SOURCE = 3;
const PANEL_WIDTH = 680;
const SUPER = Clutter.ModifierType.SUPER_MASK | Clutter.ModifierType.MOD4_MASK;

// Kulan Assistant's "Ask Kulan" search result: its id is this, then the words.
const ASK_PREFIX = 'kulan-ask:';

export class Spotlight {
    constructor(askKulan = null) {
        this._askKulan = askKulan;
        this._backdrop = null;
        this._grab = null;
        this._timer = 0;
        this._cancellable = null;
        this._generation = 0;
        this._sections = [];
        this._rows = [];
        this._pending = 0;
        this._selected = 0;
        this._userMoved = false;
        this._activatePending = false;
        this._terms = [];
        this._providers = null;
        this._searchSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.search-providers'});
        this._searchSettingsId = this._searchSettings.connect('changed', () => {
            this._providers = null; // reloaded at the next search
        });
    }

    get isOpen() {
        return this._backdrop !== null;
    }

    toggle() {
        if (this.isOpen)
            this.close();
        else
            this.open();
    }

    _loadProviders() {
        const providers = [new AppSearchProvider()];
        try {
            providers.push(...RemoteSearch.loadRemoteSearchProviders(this._searchSettings));
        } catch (e) {
            logError(e, 'Kulan Shell: search sources');
        }
        return providers;
    }

    open() {
        if (this.isOpen || Main.sessionMode.isLocked)
            return;
        if (Main.overview.visible)
            Main.overview.hide();
        this._providers ??= this._loadProviders();

        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const width = Math.min(PANEL_WIDTH, monitor.width - 32);

        this._backdrop = new St.Widget({
            reactive: true,
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height: monitor.height,
            style_class: 'kulan-spotlight-backdrop',
        });
        this._panel = new St.BoxLayout({
            vertical: true,
            style_class: 'kulan-spotlight',
            width,
            x: Math.round((monitor.width - width) / 2),
            y: Math.round(monitor.height * 0.18),
        });
        this._entry = new St.Entry({
            hint_text: 'Search apps, files and settings',
            can_focus: true,
            x_expand: true,
            style_class: 'kulan-spotlight-entry',
            primary_icon: new St.Icon({icon_name: 'edit-find-symbolic', style_class: 'kulan-spotlight-entry-icon'}),
        });
        this._entry.clutter_text.connect('text-changed', () => this._queueSearch());
        this._entry.clutter_text.connect('key-press-event', (_actor, event) => this._onKey(event));
        this._entry.clutter_text.connect('activate', () => this._activateSelected());

        this._results = new St.BoxLayout({vertical: true, style_class: 'kulan-spotlight-results'});
        this._scroll = new St.ScrollView({
            style_class: 'kulan-spotlight-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            overlay_scrollbars: true,
            visible: false,
        });
        this._scroll.child = this._results;

        this._panel.add_child(this._entry);
        this._panel.add_child(this._scroll);
        this._backdrop.add_child(this._panel);
        this._backdrop.connect('button-press-event', (_actor, event) => {
            const [x, y] = event.get_coords();
            const [px, py] = this._panel.get_transformed_position();
            const [pw, ph] = this._panel.get_transformed_size();
            if (x < px || x > px + pw || y < py || y > py + ph)
                this.close();
            return Clutter.EVENT_PROPAGATE;
        });

        Main.layoutManager.modalDialogGroup.add_child(this._backdrop);
        this._grab = Main.pushModal(this._backdrop, {actionMode: Shell.ActionMode.SYSTEM_MODAL});
        if ((this._grab.get_seat_state() & Clutter.GrabState.KEYBOARD) === 0) {
            this.close(); // something else holds the keyboard
            return;
        }
        this._entry.grab_key_focus();
        this._panel.opacity = 0;
        this._panel.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    close() {
        if (!this._backdrop)
            return;
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        this._cancellable?.cancel();
        this._cancellable = null;
        this._generation++;
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        this._backdrop.destroy();
        this._backdrop = null;
        this._panel = this._entry = this._results = this._scroll = null;
        this._sections = [];
        this._rows = [];
    }

    destroy() {
        this.close();
        if (this._searchSettingsId) {
            this._searchSettings.disconnect(this._searchSettingsId);
            this._searchSettingsId = 0;
        }
        this._providers = null;
    }

    // --- searching -------------------------------------------------------------

    _queueSearch() {
        if (this._timer)
            GLib.source_remove(this._timer);
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, DEBOUNCE_MS, () => {
            this._timer = 0;
            this._search();
            return GLib.SOURCE_REMOVE;
        });
    }

    _search() {
        this._cancellable?.cancel();
        const generation = ++this._generation;
        const text = this._entry?.get_text().trim() ?? '';
        this._userMoved = false;
        this._activatePending = false;
        this._selected = 0;
        if (!text) {
            this._sections = [];
            this._render();
            return;
        }
        const terms = text.split(/\s+/);
        this._terms = terms;
        const cancellable = new Gio.Cancellable();
        this._cancellable = cancellable;
        this._sections = this._providers.map(() => null);
        this._pending = this._providers.length;
        this._render();

        // Each source answers in its own time; results show as they come, in
        // the sources' order, so a slow one never holds up the others.
        this._providers.forEach(async (provider, index) => {
            try {
                let ids = await provider.getInitialResultSet(terms, cancellable);
                if (generation !== this._generation || !Array.isArray(ids) || ids.length === 0)
                    return;
                const max = provider.id === 'applications' ? MAX_APPS : MAX_PER_SOURCE;
                ids = (provider.filterResults ? provider.filterResults(ids, max) : ids).slice(0, max);
                const metas = await provider.getResultMetas(ids, cancellable);
                if (generation !== this._generation || !Array.isArray(metas))
                    return;
                const usable = metas.filter(m => m && m.id && m.name);
                if (usable.length === 0)
                    return;
                this._sections[index] = {provider, metas: usable};
                this._render();
            } catch (e) {
                if (!(e instanceof GLib.Error && e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)))
                    logError(e, `Kulan Shell: search source ${provider.id}`);
            } finally {
                // Every source has answered (or failed): "No results" can show.
                if (generation === this._generation) {
                    this._pending = Math.max(0, (this._pending ?? 1) - 1);
                    if (this._pending === 0 && this._rows.length === 0)
                        this._render();
                }
            }
        });
    }

    _sourceName(provider) {
        if (provider.id === 'applications')
            return 'Applications';
        // Kulan's tag search answers for Files: say what it is.
        if (provider.proxy?.g_object_path === '/org/kulan/Tags/SearchProvider')
            return 'Tags';
        return provider.appInfo?.get_name() ?? '';
    }

    _render() {
        if (!this._results)
            return;
        this._results.destroy_all_children();
        this._rows = [];
        for (const section of this._sections) {
            if (!section)
                continue;
            this._results.add_child(new St.Label({
                text: this._sourceName(section.provider),
                style_class: 'kulan-spotlight-header',
            }));
            for (const meta of section.metas)
                this._addRow(section.provider, meta);
        }
        const text = this._entry?.get_text().trim() ?? '';
        if (this._rows.length === 0 && text) {
            // Still looking, or nothing found: say so instead of an empty panel.
            const message = this._pending > 0 ? 'Searching…'
                : `No results for “${text}”. Try other words, or ask Kulan Assistant with Shift+Super+Space.`;
            const label = new St.Label({text: message, style_class: 'kulan-spotlight-empty'});
            label.clutter_text.line_wrap = true;
            this._results.add_child(label);
        }
        this._scroll.visible = this._rows.length > 0 || Boolean(text);
        if (!this._userMoved)
            this._selected = 0;
        this._select(Math.min(this._selected, this._rows.length - 1), false);
        if (this._activatePending && this._rows.length > 0) {
            this._activatePending = false;
            const [first] = this._rows;
            this._activate(first.provider, first.meta);
        }
    }

    _addRow(provider, meta) {
        const button = new St.Button({
            style_class: 'kulan-spotlight-row',
            x_expand: true,
            can_focus: false,
            reactive: true,
        });
        const box = new St.BoxLayout({style_class: 'kulan-spotlight-row-box', x_expand: true});
        let icon = null;
        try {
            icon = meta.createIcon?.(ICON_SIZE) ?? null;
        } catch (e) {
            icon = null;
        }
        box.add_child(icon ?? new St.Icon({icon_name: 'system-search-symbolic', icon_size: ICON_SIZE}));
        const labels = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        const name = new St.Label({text: String(meta.name), style_class: 'kulan-spotlight-name'});
        name.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        labels.add_child(name);
        const description = String(meta.description ?? '').split('\n')[0];
        // "Kulan Settings" under every Kulan Settings result says nothing the
        // heading above doesn't.
        if (description && description !== this._sourceName(provider)) {
            const detail = new St.Label({text: description, style_class: 'kulan-spotlight-description'});
            detail.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            labels.add_child(detail);
        }
        box.add_child(labels);
        button.set_child(box);

        const index = this._rows.length;
        button.connect('clicked', () => this._activate(provider, meta));
        button.connect('notify::hover', () => {
            if (button.hover) {
                this._userMoved = true;
                this._select(index, false);
            }
        });
        this._results.add_child(button);
        this._rows.push({button, provider, meta});
    }

    _select(index, scroll = true) {
        this._rows[this._selected]?.button.remove_style_pseudo_class('selected');
        if (index < 0 || this._rows.length === 0) {
            this._selected = 0;
            return;
        }
        this._selected = index;
        const {button} = this._rows[index];
        button.add_style_pseudo_class('selected');
        if (scroll) {
            const adjustment = this._scroll.vadjustment;
            const box = button.get_allocation_box();
            if (box.y1 < adjustment.value)
                adjustment.value = box.y1;
            else if (box.y2 > adjustment.value + adjustment.page_size)
                adjustment.value = box.y2 - adjustment.page_size;
        }
    }

    _move(step) {
        if (this._rows.length === 0)
            return;
        this._userMoved = true;
        const next = (this._selected + step + this._rows.length) % this._rows.length;
        this._select(next);
    }

    _onKey(event) {
        const symbol = event.get_key_symbol();
        const state = event.get_state();
        if (symbol === Clutter.KEY_Escape) {
            if (this._entry.get_text())
                this._entry.set_text('');
            else
                this.close();
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_space && (state & SUPER)) {
            this.close(); // the shortcut again closes it, as on a Mac
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_Down || symbol === Clutter.KEY_Tab) {
            this._move(1);
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_Up || symbol === Clutter.KEY_ISO_Left_Tab) {
            this._move(-1);
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _activateSelected() {
        // Typed and pressed Enter at once: search now and open the first
        // result that comes back.
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
            this._search();
            this._activatePending = true;
            return;
        }
        const row = this._rows[this._selected];
        if (row)
            this._activate(row.provider, row.meta);
    }

    _activate(provider, meta) {
        const terms = this._terms;
        this.close();
        try {
            if (this._askKulan && String(meta.id).startsWith(ASK_PREFIX)) {
                this._askKulan(String(meta.id).slice(ASK_PREFIX.length));
            } else if (provider.id === 'applications') {
                if (meta.id.endsWith('.desktop'))
                    Shell.AppSystem.get_default().lookup_app(meta.id)?.activate();
                else
                    SystemActions.getDefault().activateAction(meta.id);
            } else {
                provider.activateResult(meta.id, terms);
            }
        } catch (e) {
            logError(e, 'Kulan Shell: opening a search result');
        }
    }
}
