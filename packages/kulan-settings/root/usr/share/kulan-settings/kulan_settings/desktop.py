"""Desktop and windows: animations, app folders, hot corner, workspaces."""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib

from .common import KULAN_SCHEMA, Page, choice_row, has_key, reset_keys, settings, switch_row

KULAN_FOLDERS = ["Kulan-Utilities", "Kulan-System"]


class DesktopPage(Page):
    title = "Desktop and windows"
    icon = "focus-windows-symbolic"
    keywords = ("animation", "speed", "folders", "app grid", "hot corner", "workspaces", "windows")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.kulan = settings(KULAN_SCHEMA)
        self.interface = settings("org.gnome.desktop.interface")
        self.folders = settings("org.gnome.desktop.app-folders")
        self.mutter = settings("org.gnome.mutter")
        self.wm = settings("org.gnome.desktop.wm.preferences")

        motion = self.add_group("Motion")
        motion.add(choice_row("Animations", "Kulan’s default is a little quicker than GNOME’s",
                              [("normal", "Quick"), ("calm", "Calm"), ("fast", "Fast"), ("off", "Off")],
                              self.get_speed, self.set_speed))

        grid = self.add_group("App grid")
        if self.folders is not None:
            row = Adw.SwitchRow(title="Folders for Utilities and System",
                                subtitle="Everyday apps always stay out in the open",
                                active=bool(self.folders.get_strv("folder-children")))
            row.connect("notify::active", lambda r, _p: self.set_folders(r.get_active()))
            grid.add(row)

        windows = self.add_group("Windows and workspaces")
        self.add_rows(windows,
                      switch_row(self.interface, "enable-hot-corners", "Hot corner",
                                 "Move the pointer to the top-left corner to see all windows"),
                      switch_row(self.mutter, "center-new-windows", "Open new windows in the centre"),
                      switch_row(self.mutter, "dynamic-workspaces", "Add workspaces as needed",
                                 "Off: a fixed number of workspaces"))
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
            if not self.folders.get_strv("folder-children"):
                self.folders.set_strv("folder-children", KULAN_FOLDERS)
        else:
            self.folders.set_strv("folder-children", [])

    def reset(self) -> None:
        reset_keys(self.kulan, ["animation-speed"])
        reset_keys(self.interface, ["enable-animations", "enable-hot-corners"])
        reset_keys(self.folders, ["folder-children"])
        reset_keys(self.mutter, ["center-new-windows", "dynamic-workspaces"])
        reset_keys(self.wm, ["num-workspaces"])
        GLib.idle_add(lambda: self.window.reload_page(self) and False)
        self.toast("Desktop and windows reset to Kulan defaults")
