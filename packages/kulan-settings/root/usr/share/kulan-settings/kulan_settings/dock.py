"""Dock: visibility mode, show speed, size, position and indicators (Dash to Dock)."""
from __future__ import annotations

from gi.repository import Adw, Gtk

from .common import Page, choice_row, has_key, reset_keys, settings, switch_row

DOCK_SCHEMA = "org.gnome.shell.extensions.dash-to-dock"

# Each visibility mode is a combination of Dash to Dock's own keys.
MODES = {
    "always": {"dock-fixed": True},
    "hover": {"dock-fixed": False, "autohide": True, "intellihide": False, "require-pressure-to-show": False},
    "covered": {"dock-fixed": False, "autohide": True, "intellihide": True, "require-pressure-to-show": False},
}
SPEEDS = {"instant": (0.0, 0.0625), "quick": (0.125, 0.125), "relaxed": (0.375, 0.25)}
DOCK_KEYS = ["dock-fixed", "autohide", "intellihide", "require-pressure-to-show", "show-delay", "animation-time",
             "dash-max-icon-size", "dock-position", "running-indicator-style", "show-show-apps-button",
             "show-apps-at-top"]


class DockPage(Page):
    title = "Dock"
    icon = "preferences-desktop-apps-symbolic"
    keywords = ("dock", "hide", "autohide", "hover", "icons", "size", "position", "taskbar")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.dock = settings(DOCK_SCHEMA)
        if self.dock is None:
            group = self.add_group(description="The dock isn’t installed.")
            group.add(Adw.ActionRow(title="Install gnome-shell-extension-dashtodock to use the dock"))
            return

        behaviour = self.add_group("Showing and hiding")
        behaviour.add(choice_row(
            "Dock", None,
            [("always", "Always visible"), ("hover", "Show on hover"), ("covered", "Hide when a window covers it")],
            self.get_mode, self.set_mode))
        self.speed_row = choice_row(
            "Show speed", "How quickly a hidden dock slides up",
            [("quick", "Quick"), ("instant", "Instant"), ("relaxed", "Relaxed")],
            self.get_speed, self.set_speed)
        behaviour.add(self.speed_row)

        look = self.add_group("Size and position")
        size = Adw.ActionRow(title="Icon size")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 24, 64, 4)
        scale.set_hexpand(True)
        scale.set_size_request(220, -1)
        scale.set_value(self.dock.get_int("dash-max-icon-size"))
        for mark in (32, 40, 48, 56):
            scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        scale.update_property([Gtk.AccessibleProperty.LABEL], ["Dock icon size"])
        scale.connect("value-changed", lambda s: self.dock.set_int("dash-max-icon-size", int(s.get_value())))
        size.add_suffix(scale)
        look.add(size)
        if has_key(self.dock, "dock-position"):
            look.add(choice_row("Position", None, [("BOTTOM", "Bottom"), ("LEFT", "Left"), ("RIGHT", "Right")],
                                lambda: self.dock.get_string("dock-position"),
                                lambda v: self.dock.set_string("dock-position", v)))

        extras = self.add_group("Icons")
        if has_key(self.dock, "running-indicator-style"):
            extras.add(choice_row("Open apps", "How the dock marks apps that are running",
                                  [("DOTS", "Dots"), ("DASHES", "Dashes"), ("SQUARES", "Squares"),
                                   ("DEFAULT", "GNOME default")],
                                  lambda: self.dock.get_string("running-indicator-style"),
                                  lambda v: self.dock.set_string("running-indicator-style", v)))
        self.add_rows(extras,
                      switch_row(self.dock, "show-show-apps-button", "App grid button",
                                 "The grid of dots that shows all apps"),
                      switch_row(self.dock, "show-apps-at-top", "App grid button first"))
        self.add_reset(self.reset)
        self.watch(self.dock, "changed::dock-fixed", lambda *_a: self.sync_speed())
        self.sync_speed()

    def get_mode(self) -> str:
        if self.dock.get_boolean("dock-fixed"):
            return "always"
        return "covered" if self.dock.get_boolean("intellihide") else "hover"

    def set_mode(self, mode: str) -> None:
        for key, value in MODES[mode].items():
            if has_key(self.dock, key):
                self.dock.set_boolean(key, value)

    def get_speed(self) -> str:
        delay = self.dock.get_double("show-delay") if has_key(self.dock, "show-delay") else 0.125
        return min(SPEEDS, key=lambda name: abs(SPEEDS[name][0] - delay))

    def set_speed(self, speed: str) -> None:
        delay, animation = SPEEDS[speed]
        if has_key(self.dock, "show-delay"):
            self.dock.set_double("show-delay", delay)
        if has_key(self.dock, "animation-time"):
            self.dock.set_double("animation-time", animation)

    def sync_speed(self) -> None:
        self.speed_row.set_sensitive(not self.dock.get_boolean("dock-fixed"))

    def reset(self) -> None:
        reset_keys(self.dock, DOCK_KEYS)
        self.window.reload_page(self)
        self.toast("Dock reset to Kulan defaults")
