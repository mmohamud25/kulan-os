"""Desktop and windows: files on the desktop, animations, app folders, hot corner, workspaces."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .common import (
    KULAN_SCHEMA,
    Page,
    choice_row,
    has_key,
    reset_keys,
    settings,
    switch_row,
)

KULAN_FOLDERS = ["Kulan-Utilities", "Kulan-System"]
DESKTOP_ICONS = "ding@rastersoft.com"
DESKTOP_ICONS_DIR = Path("/usr/share/gnome-shell/extensions") / DESKTOP_ICONS


def desktop_icons_on(shell) -> bool:
    if DESKTOP_ICONS in shell.get_strv("disabled-extensions"):
        return False
    return DESKTOP_ICONS in shell.get_strv("enabled-extensions")


def set_desktop_icons(shell, on: bool) -> None:
    """Turn the desktop icons extension on or off for this user. Off goes in
    the disabled list (which wins), so Kulan's default list stays as it is."""
    disabled = [u for u in shell.get_strv("disabled-extensions") if u != DESKTOP_ICONS]
    shell.set_strv("disabled-extensions", disabled if on else [*disabled, DESKTOP_ICONS])
    # Kulan's defaults list it; only someone with their own list of
    # extensions needs it added there.
    if on and shell.get_user_value("enabled-extensions") is not None \
            and DESKTOP_ICONS not in shell.get_strv("enabled-extensions"):
        shell.set_strv("enabled-extensions", [*shell.get_strv("enabled-extensions"), DESKTOP_ICONS])
# "No folders" can't be an empty list: GNOME Shell writes its own default
# folders back over an empty list at the next login. A folder with no apps
# is never shown, so this one stands for "folders off".
NO_FOLDERS = ["Kulan-NoFolders"]

# Desktop widgets, in the order they're shown: (id, title, subtitle).
WIDGETS = [
    ("calendar", "Calendar", "Today’s date and what’s next in Calendar"),
    ("weather", "Weather", "For the place chosen in the Weather app"),
    ("prayer", "Prayer Times", "The next prayer, while Prayer Times is on"),
    ("notes", "Note", "Your Quick Note (Cmd+Shift+N)"),
    ("clock", "Clock", "The time here and in the cities added in Clocks"),
    ("system", "System", "Processor, memory, disk space and battery"),
    ("todo", "To-do", "A short task list you tick off on the desktop"),
    ("media", "Now Playing", "Play, pause and skip whatever music or video is playing"),
]
WIDGET_INFO = {w: (t, s) for w, t, s in WIDGETS}


def widget_order(current: list[str], widget_id: str, on: bool) -> list[str]:
    """The shown widgets after switching one on (added at the end) or off,
    keeping the order the person chose. Unknown ids are dropped."""
    kept = [w for w in current if w in WIDGET_INFO and w != widget_id]
    return [*kept, widget_id] if on else kept


def widget_move(current: list[str], widget_id: str, step: int) -> list[str]:
    """Move one shown widget up (-1) or down (+1)."""
    order = [w for w in current if w in WIDGET_INFO]
    if widget_id not in order:
        return order
    i = order.index(widget_id)
    j = max(0, min(len(order) - 1, i + step))
    order.insert(j, order.pop(i))
    return order


def folders_on(folders) -> bool:
    children = folders.get_strv("folder-children")
    return bool(children) and children != NO_FOLDERS


class DesktopPage(Page):
    title = "Desktop and windows"
    icon = "focus-windows-symbolic"
    keywords = ("widgets", "clock", "world clock", "to-do", "todo", "system monitor", "now playing", "music", "animation", "speed", "folders", "app grid", "hot corner", "workspaces", "windows", "reopen", "restore apps", "restart", "resume")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.kulan = settings(KULAN_SCHEMA)
        self.interface = settings("org.gnome.desktop.interface")
        self.folders = settings("org.gnome.desktop.app-folders")
        self.mutter = settings("org.gnome.mutter")
        self.wm = settings("org.gnome.desktop.wm.preferences")
        self.shell = settings("org.gnome.shell")
        self.ding = settings("org.gnome.shell.extensions.ding")

        if self.shell is not None and DESKTOP_ICONS_DIR.is_dir():
            desktop = self.add_group("Desktop")
            icons = Adw.SwitchRow(title="Show files on the desktop",
                                  subtitle="Folders and files in your Desktop folder",
                                  active=desktop_icons_on(self.shell))
            icons.connect("notify::active", lambda r, _p: set_desktop_icons(self.shell, r.get_active()))
            desktop.add(icons)
            if has_key(self.ding, "icon-size"):
                size = choice_row("Icon size", None, [("small", "Small"), ("standard", "Standard"),
                                                      ("large", "Large")],
                                  lambda: self.ding.get_string("icon-size"),
                                  lambda v: self.ding.set_string("icon-size", v))
                icons.bind_property("active", size, "sensitive", GObject.BindingFlags.SYNC_CREATE)
                desktop.add(size)
            # The desktop icons' own settings window uses technical names;
            # these are the choices people need, in plain words.
            extra = [
                switch_row(self.ding, "show-trash", "Show the Bin"),
                switch_row(self.ding, "show-volumes", "Show drives you plug in",
                           "USB sticks, memory cards and external disks"),
                switch_row(self.ding, "show-home", "Show your Home folder"),
            ]
            if has_key(self.ding, "start-corner"):
                extra.append(choice_row("New items appear", None,
                                        [("top-left", "Top left"), ("top-right", "Top right"),
                                         ("bottom-left", "Bottom left"), ("bottom-right", "Bottom right")],
                                        lambda: self.ding.get_string("start-corner"),
                                        lambda v: self.ding.set_string("start-corner", v)))
            for row in extra:
                if row is not None:
                    icons.bind_property("active", row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
                    desktop.add(row)

        if has_key(self.kulan, "desktop-widgets"):
            self.widgets_group = self.add_group(
                "Widgets", "Cards at the top left of the desktop, in this order. Click one to open its app.")
            self.widget_rows = []
            self.build_widget_rows()

        if has_key(self.kulan, "reopen-apps"):
            session = self.add_group("Restarting")
            self.add_rows(session, switch_row(self.kulan, "reopen-apps", "Reopen apps after a restart",
                                              "Opens the apps you had open before a restart or "
                                              "shut down"))

        motion = self.add_group("Motion")
        motion.add(choice_row("Animations", "Kulan’s default is a little quicker than GNOME’s",
                              [("normal", "Quick"), ("calm", "Calm"), ("fast", "Fast"), ("off", "Off")],
                              self.get_speed, self.set_speed))

        grid = self.add_group("App grid")
        if self.folders is not None:
            row = Adw.SwitchRow(title="Folders for Utilities and System",
                                subtitle="Everyday apps always stay out in the open",
                                active=folders_on(self.folders))
            row.connect("notify::active", lambda r, _p: self.set_folders(r.get_active()))
            grid.add(row)
        if has_key(self.shell, "app-picker-layout"):
            tidy = Adw.ActionRow(title="Tidy the app grid",
                                 subtitle="Sort apps from A to Z and close the gaps between pages")
            button = Gtk.Button(label="Tidy", valign=Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.LABEL], ["Tidy the app grid"])
            button.connect("clicked", lambda _b: self.tidy_grid())
            tidy.add_suffix(button)
            grid.add(tidy)

        windows = self.add_group("Windows and workspaces")
        self.add_rows(windows,
                      switch_row(self.interface, "enable-hot-corners", "Hot corner",
                                 "Move the pointer to the top-left corner to see all windows"),
                      switch_row(self.mutter, "center-new-windows", "Open new windows in the centre"),
                      switch_row(self.mutter, "dynamic-workspaces", "Add workspaces as needed",
                                 "When off, the number of workspaces is fixed"))
        if has_key(self.wm, "num-workspaces") and has_key(self.mutter, "dynamic-workspaces"):
            count = Adw.SpinRow.new_with_range(1, 12, 1)
            count.set_title("Number of workspaces")
            count.set_value(self.wm.get_int("num-workspaces"))
            count.connect("notify::value", lambda r, _p: self.wm.set_int("num-workspaces", int(r.get_value())))
            self.mutter.bind("dynamic-workspaces", count, "sensitive",
                             Gio.SettingsBindFlags.GET | Gio.SettingsBindFlags.INVERT_BOOLEAN)
            windows.add(count)
        self.add_reset(self.reset)

    def get_speed(self) -> str:
        if self.interface is not None and not self.interface.get_boolean("enable-animations"):
            return "off"
        return self.kulan.get_string("animation-speed") if self.kulan is not None else "normal"

    def set_speed(self, value: str) -> None:
        if self.interface is not None:
            self.interface.set_boolean("enable-animations", value != "off")
        if value != "off" and self.kulan is not None:
            self.kulan.set_string("animation-speed", value)

    def set_folders(self, on: bool) -> None:
        if on:
            self.folders.reset("folder-children")
            if not folders_on(self.folders):
                self.folders.set_strv("folder-children", KULAN_FOLDERS)
        else:
            self.folders.set_strv("folder-children", NO_FOLDERS)

    def build_widget_rows(self, focus: str | None = None) -> None:
        """Shown widgets first, in their order (with Move up/down), then the rest."""
        for row in self.widget_rows:
            self.widgets_group.remove(row)
        self.widget_rows = []
        shown = [w for w in self.kulan.get_strv("desktop-widgets") if w in WIDGET_INFO]
        hidden = [w for w, _t, _s in WIDGETS if w not in shown]
        for index, widget_id in enumerate([*shown, *hidden]):
            title, subtitle = WIDGET_INFO[widget_id]
            # Move buttons, then the switch (an action row, so the switch
            # stays at the far end like every other switch).
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            switch = Gtk.Switch(active=widget_id in shown, valign=Gtk.Align.CENTER)
            switch.update_property([Gtk.AccessibleProperty.LABEL], [title])
            if widget_id in shown:
                for icon, name, step, usable in (("go-up-symbolic", "Move up", -1, index > 0),
                                                 ("go-down-symbolic", "Move down", 1, index < len(shown) - 1)):
                    button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, sensitive=usable,
                                        css_classes=["flat", "circular"], tooltip_text=name)
                    button.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}: {title}"])
                    button.connect("clicked", lambda _b, w=widget_id, d=step: self.move_widget(w, d))
                    row.add_suffix(button)
            row.add_suffix(switch)
            row.set_activatable_widget(switch)
            switch.connect("notify::active", lambda sw, _p, w=widget_id: self.set_widget(w, sw.get_active()))
            self.widgets_group.add(row)
            self.widget_rows.append(row)
            if widget_id == focus:
                GLib.idle_add(lambda sw=switch: sw.grab_focus() and False)

    def set_widget(self, widget_id: str, on: bool) -> None:
        current = self.kulan.get_strv("desktop-widgets")
        wanted = widget_order(current, widget_id, on)
        if wanted != current:
            self.kulan.set_strv("desktop-widgets", wanted)
        # Rebuilt after this click is handled, so the switch isn't removed
        # while it's still sending its signal.
        GLib.idle_add(lambda: self.build_widget_rows(widget_id) and False)

    def move_widget(self, widget_id: str, step: int) -> None:
        current = self.kulan.get_strv("desktop-widgets")
        wanted = widget_move(current, widget_id, step)
        if wanted != current:
            self.kulan.set_strv("desktop-widgets", wanted)
            GLib.idle_add(lambda: self.build_widget_rows(widget_id) and False)

    def tidy_grid(self) -> None:
        # GNOME keeps each app where it was dropped; with no saved layout it
        # sorts alphabetically and fills every page.
        self.shell.reset("app-picker-layout")
        self.toast("App grid sorted from A to Z")

    def reset(self) -> None:
        reset_keys(self.kulan, ["animation-speed", "desktop-widgets", "reopen-apps"])
        reset_keys(self.interface, ["enable-animations", "enable-hot-corners"])
        reset_keys(self.folders, ["folder-children"])
        reset_keys(self.mutter, ["center-new-windows", "dynamic-workspaces"])
        reset_keys(self.wm, ["num-workspaces"])
        reset_keys(self.ding, ["icon-size", "show-trash", "show-volumes", "show-home", "start-corner"])
        if self.shell is not None and DESKTOP_ICONS_DIR.is_dir():
            set_desktop_icons(self.shell, True)
        GLib.idle_add(lambda: self.window.reload_page(self) and False)
        self.toast("Desktop and windows reset to Kulan defaults")
