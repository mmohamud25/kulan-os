"""Typing languages (keyboard layouts) and keyboard shortcuts, in Kulan
Settings instead of GNOME Settings."""
from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from functools import lru_cache
from pathlib import Path
from typing import Callable

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .common import KULAN_SCHEMA, has_key, settings

EVDEV = Path("/usr/share/X11/xkb/rules/evdev.xml")
WM = "org.gnome.desktop.wm.keybindings"
MUTTER = "org.gnome.mutter.keybindings"
SHELL = "org.gnome.shell.keybindings"
MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom{}/"

# (group, [(title, schema, key)]) — the shortcuts people look for.
SHORTCUTS = [
    ("Kulan", [
        ("Search apps, files and settings", KULAN_SCHEMA, "search-shortcut"),
        ("Quick Note", KULAN_SCHEMA, "quick-note-shortcut"),
        ("Clipboard history", KULAN_SCHEMA, "clipboard-history-shortcut"),
        ("Live Text: copy text from the screen", KULAN_SCHEMA, "live-text-shortcut"),
        ("Writing tools (Kulan Assistant)", KULAN_SCHEMA, "writing-tools-shortcut"),
        ("Dictation (Kulan Assistant)", KULAN_SCHEMA, "dictation-shortcut"),
    ]),
    ("Windows", [
        ("Close the window", WM, "close"),
        ("Hide the window", WM, "minimize"),
        ("Maximise or restore", WM, "toggle-maximized"),
        ("Full screen", WM, "toggle-fullscreen"),
        ("Fill the left half", MUTTER, "toggle-tiled-left"),
        ("Fill the right half", MUTTER, "toggle-tiled-right"),
        ("Switch apps", WM, "switch-applications"),
        ("Switch windows of an app", WM, "switch-group"),
        ("Move the window", WM, "begin-move"),
        ("Resize the window", WM, "begin-resize"),
    ]),
    ("Workspaces", [
        ("Workspace on the left", WM, "switch-to-workspace-left"),
        ("Workspace on the right", WM, "switch-to-workspace-right"),
        ("Move the window to the left workspace", WM, "move-to-workspace-left"),
        ("Move the window to the right workspace", WM, "move-to-workspace-right"),
    ]),
    ("Screenshots", [
        ("Screenshot of the whole screen", SHELL, "screenshot"),
        ("Screenshot of an area or a window", SHELL, "show-screenshot-ui"),
        ("Screenshot of the window", SHELL, "screenshot-window"),
        ("Record the screen", SHELL, "show-screen-recording-ui"),
    ]),
    ("System", [
        ("All windows (Activities)", SHELL, "toggle-overview"),
        ("All apps", SHELL, "toggle-application-view"),
        ("Notifications", SHELL, "toggle-message-tray"),
        ("Lock the screen", MEDIA, "screensaver"),
        ("Sign out", MEDIA, "logout"),
        ("Switch typing language", WM, "switch-input-source"),
        ("Open your Home folder", MEDIA, "home"),
        ("Open the web browser", MEDIA, "www"),
    ]),
]


# --- typing languages ---------------------------------------------------------------

@lru_cache(maxsize=1)
def xkb_layouts() -> dict[str, str]:
    """{"us": "English (US)", "us+intl": "English (US, intl., with dead keys)", ...}"""
    layouts: dict[str, str] = {}
    try:
        root = ElementTree.parse(EVDEV).getroot()
    except (ElementTree.ParseError, OSError):
        return {"us": "English (US)"}
    for layout in root.iter("layout"):
        item = layout.find("configItem")
        if item is None:
            continue
        name = (item.findtext("name") or "").strip()
        description = (item.findtext("description") or name).strip()
        if not name:
            continue
        layouts[name] = description
        for variant in layout.iter("variant"):
            vitem = variant.find("configItem")
            vname = (vitem.findtext("name") or "").strip() if vitem is not None else ""
            if vname:
                layouts[f"{name}+{vname}"] = (vitem.findtext("description") or vname).strip()
    return layouts


def source_label(kind: str, ident: str) -> str:
    if kind == "xkb":
        return xkb_layouts().get(ident, ident)
    return ident.split(":")[-1].replace("-", " ").title() + " (input method)"


class LayoutsGroup:
    """The "Typing languages" group: the layouts in order, with add, move, remove."""

    def __init__(self, page, sources: Gio.Settings) -> None:
        self.page = page
        self.sources = sources
        wm = settings(WM)
        accel = first_usable(wm.get_strv("switch-input-source")) if has_key(wm, "switch-input-source") else ""
        how = f"Switch between them with {readable(accel)}, or from the top bar." if accel \
            else "Switch between them from the top bar."
        self.group = page.add_group("Typing languages", how)
        self.rows: list[Gtk.Widget] = []
        page.watch(sources, "changed::sources", lambda *_a: self.fill())
        self.fill()

    def current(self) -> list[tuple[str, str]]:
        return [tuple(s) for s in self.sources.get_value("sources").unpack()]

    def save(self, items: list[tuple[str, str]]) -> None:
        self.sources.set_value("sources", GLib.Variant("a(ss)", items))

    def fill(self) -> None:
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        items = self.current()
        if not items:
            items = [("xkb", "us")]
        for index, (kind, ident) in enumerate(items):
            row = Adw.ActionRow(title=GLib.markup_escape_text(source_label(kind, ident)))
            for icon, name, step, usable in (("go-up-symbolic", "Move up", -1, index > 0),
                                             ("go-down-symbolic", "Move down", 1, index < len(items) - 1)):
                button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, css_classes=["flat", "circular"],
                                    sensitive=usable, tooltip_text=name)
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}: {source_label(kind, ident)}"])
                button.connect("clicked", lambda _b, i=index, d=step: self.move(i, d))
                row.add_suffix(button)
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat", "circular"], sensitive=len(items) > 1, tooltip_text="Remove")
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {source_label(kind, ident)}"])
            remove.connect("clicked", lambda _b, i=index: self.remove(i))
            row.add_suffix(remove)
            self.group.add(row)
            self.rows.append(row)
        add = Adw.ActionRow(title="Add a typing language…", activatable=True)
        add.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
        add.connect("activated", lambda _r: self.add_dialog())
        self.group.add(add)
        self.rows.append(add)

    def move(self, index: int, step: int) -> None:
        items = self.current()
        target = max(0, min(len(items) - 1, index + step))
        items.insert(target, items.pop(index))
        self.save(items)

    def remove(self, index: int) -> None:
        items = self.current()
        if len(items) > 1:
            items.pop(index)
            self.save(items)

    def add_dialog(self) -> None:
        have = {ident for kind, ident in self.current() if kind == "xkb"}
        choices = sorted(((name, label) for name, label in xkb_layouts().items()
                          if name not in have and name != "custom"), key=lambda c: c[1].lower())
        dialog = Adw.AlertDialog(heading="Add a typing language", body="Type to search, for example “Somali”.")
        row = Adw.ComboRow(title="Keyboard layout", model=Gtk.StringList.new([label for _n, label in choices]),
                           enable_search=True,
                           expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(row)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response: str) -> None:
            if response == "add" and choices:
                name, label = choices[row.get_selected()]
                self.save([*self.current(), ("xkb", name)])
                self.page.toast(f"{label} added")
        dialog.connect("response", answered)
        dialog.present(self.page.window)


# --- shortcuts ----------------------------------------------------------------------

def for_gtk(accel: str) -> str:
    """Mutter's own key names in GTK's words ("Above_Tab" is the key above Tab)."""
    return accel.replace("Above_Tab", "grave")


def parse(accel: str) -> tuple[bool, int, Gdk.ModifierType]:
    return Gtk.accelerator_parse(for_gtk(accel))


def first_usable(values: list[str]) -> str:
    """The first binding GTK can show, preferring a Super (Mac Command) one;
    skips keys like XF86Keyboard that most keyboards don't have."""
    usable = [v for v in values if parse(v)[0] and parse(v)[1]]
    mac = [v for v in usable if "<Super>" in v]
    return for_gtk((mac or usable or [""])[0])


def readable(accel: str) -> str:
    ok, key, mods = parse(accel)
    return Gtk.accelerator_get_label(key, mods) if ok and key else ""


def capture_dialog(parent: Gtk.Widget, title: str, on_done: Callable[[str | None], None],
                   default: str | None) -> None:
    """Ask for a key combination. on_done(accel), "" to turn it off, None to reset."""
    dialog = Adw.AlertDialog(heading=title, body="Press the new shortcut, or Backspace to turn it off. "
                                                 "Esc cancels.")
    shown = Gtk.ShortcutLabel(disabled_text="Waiting for keys…", halign=Gtk.Align.CENTER)
    dialog.set_extra_child(shown)
    dialog.add_response("cancel", "Cancel")
    if default is not None:
        dialog.add_response("reset", "Use the default")
    dialog.set_close_response("cancel")
    keys = Gtk.EventControllerKey()

    def pressed(_c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        mods = state & Gtk.accelerator_get_default_mod_mask()
        lower = Gdk.keyval_to_lower(keyval)
        if lower in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Alt_L,
                     Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R):
            return True  # wait for the real key
        if not mods and lower == Gdk.KEY_Escape:
            dialog.close()
            return True
        if not mods and lower == Gdk.KEY_BackSpace:
            dialog.close()
            on_done("")
            return True
        # Plain letters would stop people typing; function keys and Print are fine alone.
        alone_ok = Gdk.KEY_F1 <= lower <= Gdk.KEY_F24 or lower in (Gdk.KEY_Print, Gdk.KEY_Pause)
        if not mods and not alone_ok:
            dialog.set_body("Use it with Super, Ctrl or Alt, so it doesn’t get in the way of typing.")
            return True
        accel = Gtk.accelerator_name(lower, mods)
        shown.set_accelerator(accel)
        dialog.close()
        on_done(accel)
        return True
    keys.connect("key-pressed", pressed)
    dialog.add_controller(keys)
    dialog.connect("response", lambda _d, r: on_done(None) if r == "reset" else None)
    dialog.present(parent)
    # Let the keys reach this dialog instead of GNOME Shell (Super+A would
    # otherwise open the app grid), until it closes.
    native = parent.get_native() if parent is not None else None
    surface = native.get_surface() if native is not None else None
    if isinstance(surface, Gdk.Toplevel):
        surface.inhibit_system_shortcuts(None)
        dialog.connect("closed", lambda _d: surface.restore_system_shortcuts())


class ShortcutsDialog(Adw.PreferencesDialog):
    def __init__(self, parent: Gtk.Widget, toast: Callable[[str], None], assistant: bool) -> None:
        super().__init__(title="Keyboard shortcuts", search_enabled=True)
        self.parent = parent
        self.toast = toast
        self.schemas: dict[str, Gio.Settings | None] = {}
        self.labels: dict[tuple[str, str], Gtk.ShortcutLabel] = {}
        page = Adw.PreferencesPage(title="Shortcuts", icon_name="preferences-desktop-keyboard-shortcuts-symbolic")
        self.add(page)
        for group_title, items in SHORTCUTS:
            group = Adw.PreferencesGroup(title=group_title)
            if group_title == "Kulan":
                group.set_description("In UTM on a Mac, the Command key is the Super key.")
            added = 0
            for title, schema, key in items:
                if "Assistant" in title and not assistant:
                    continue
                gsettings = self.schema(schema)
                if not has_key(gsettings, key):
                    continue
                group.add(self.row(title, schema, key))
                added += 1
            if added:
                page.add(group)
        self.custom = Adw.PreferencesGroup(title="Your own shortcuts",
                                           description="Open an app or run a command with a key combination.")
        self.custom_rows: list[Gtk.Widget] = []
        page.add(self.custom)
        self.fill_custom()

    def schema(self, schema_id: str) -> Gio.Settings | None:
        if schema_id not in self.schemas:
            self.schemas[schema_id] = settings(schema_id)
        return self.schemas[schema_id]

    def accel_of(self, schema: str, key: str) -> str:
        return first_usable(self.schema(schema).get_strv(key))

    def row(self, title: str, schema: str, key: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, activatable=True)
        label = Gtk.ShortcutLabel(accelerator=self.accel_of(schema, key), disabled_text="Off",
                                  valign=Gtk.Align.CENTER)
        row.add_suffix(label)
        self.labels[(schema, key)] = label
        row.connect("activated", lambda _r: capture_dialog(self, title, lambda a: self.set(schema, key, title, a),
                                                           default=""))
        return row

    def owner(self, accel: str, skip: tuple[str, str]) -> tuple[str, str, str] | None:
        """Which other shortcut already uses accel (compared as keys, not text)."""
        ok, want_key, want_mods = parse(accel)
        if not ok:
            return None
        for _group, items in SHORTCUTS:
            for title, schema, key in items:
                if (schema, key) == skip or not has_key(self.schema(schema), key):
                    continue
                for value in self.schema(schema).get_strv(key):
                    ok2, k, m = parse(value)
                    if ok2 and k == want_key and m == want_mods:
                        return title, schema, key
        return None

    def set(self, schema: str, key: str, title: str, accel: str | None) -> None:
        gsettings = self.schema(schema)
        if accel is None:
            gsettings.reset(key)
        elif accel == "":
            gsettings.set_strv(key, [])
        else:
            clash = self.owner(accel, (schema, key))
            if clash is not None:
                other_title, other_schema, other_key = clash
                values = [v for v in self.schema(other_schema).get_strv(other_key)
                          if parse(v)[1:] != parse(accel)[1:]]
                self.schema(other_schema).set_strv(other_key, values)
                other_label = self.labels.get((other_schema, other_key))  # hidden rows have none
                if other_label is not None:
                    other_label.set_accelerator(self.accel_of(other_schema, other_key))
                self.toast(f"{readable(accel)} was for “{other_title}”, which is now off")
            gsettings.set_strv(key, [accel])
        self.labels[(schema, key)].set_accelerator(self.accel_of(schema, key))

    # --- your own shortcuts ---------------------------------------------------------
    def custom_paths(self) -> list[str]:
        media = self.schema(MEDIA)
        return media.get_strv("custom-keybindings") if has_key(media, "custom-keybindings") else []

    def custom_settings(self, path: str) -> Gio.Settings | None:
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(CUSTOM_SCHEMA, True) if source else None
        return Gio.Settings.new_full(schema, None, path) if schema else None

    def fill_custom(self) -> None:
        for row in self.custom_rows:
            self.custom.remove(row)
        self.custom_rows = []
        if self.schema(MEDIA) is None:
            return
        for path in self.custom_paths():
            item = self.custom_settings(path)
            if item is None:
                continue
            row = Adw.ActionRow(title=GLib.markup_escape_text(item.get_string("name") or "Shortcut"),
                                subtitle=GLib.markup_escape_text(item.get_string("command")))
            row.add_suffix(Gtk.ShortcutLabel(accelerator=item.get_string("binding"), disabled_text="Off",
                                             valign=Gtk.Align.CENTER))
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat", "circular"], tooltip_text="Remove")
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {item.get_string('name')}"])
            remove.connect("clicked", lambda _b, p=path: self.remove_custom(p))
            row.add_suffix(remove)
            self.custom.add(row)
            self.custom_rows.append(row)
        add = Adw.ActionRow(title="Add a shortcut…", activatable=True)
        add.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
        add.connect("activated", lambda _r: self.add_custom())
        self.custom.add(add)
        self.custom_rows.append(add)

    def remove_custom(self, path: str) -> None:
        item = self.custom_settings(path)
        if item is not None:
            for key in ("name", "command", "binding"):
                item.reset(key)
        self.schema(MEDIA).set_strv("custom-keybindings", [p for p in self.custom_paths() if p != path])
        self.fill_custom()

    def add_custom(self) -> None:
        dialog = Adw.AlertDialog(heading="Add a shortcut", body="For example: name “Terminal”, command “ptyxis”.")
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        name = Adw.EntryRow(title="Name")
        command = Adw.EntryRow(title="Command")
        box.append(name)
        box.append(command)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("next", "Choose keys…")
        dialog.set_response_appearance("next", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("next", False)
        check = lambda *_a: dialog.set_response_enabled("next", bool(name.get_text().strip() and command.get_text().strip()))  # noqa: E731
        name.connect("changed", check)
        command.connect("changed", check)

        def answered(_d, response: str) -> None:
            if response != "next":
                return
            title, run = name.get_text().strip(), command.get_text().strip()

            def keys(accel: str | None) -> None:
                if not accel:
                    return
                clash = self.owner(accel, ("", ""))
                if clash is not None:
                    self.toast(f"{readable(accel)} is already used for “{clash[0]}”")
                    return
                used = set(self.custom_paths())
                index = next(i for i in range(10000) if CUSTOM_PATH.format(i) not in used)
                path = CUSTOM_PATH.format(index)
                item = self.custom_settings(path)
                if item is None:
                    return
                item.set_string("name", title)
                item.set_string("command", run)
                item.set_string("binding", accel)
                self.schema(MEDIA).set_strv("custom-keybindings", [*self.custom_paths(), path])
                self.fill_custom()
            capture_dialog(self, f"Shortcut for “{title}”", keys, default=None)
        dialog.connect("response", answered)
        dialog.present(self)
