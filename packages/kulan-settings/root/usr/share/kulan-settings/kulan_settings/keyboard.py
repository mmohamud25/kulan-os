"""Keyboard: typing repeat, Caps Lock behaviour, layouts and shortcuts."""
from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .common import Page, choice_row, open_gnome_panel, reset_keys, settings, switch_row

CAPS_OPTIONS = {"caps:none": "Does nothing", "caps:escape": "Escape", "ctrl:nocaps": "Ctrl",
                "caps:backspace": "Backspace"}


class KeyboardPage(Page):
    title = "Keyboard"
    icon = "input-keyboard-symbolic"
    keywords = ("keyboard", "layout", "caps lock", "repeat", "shortcuts", "typing")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.keyboard = settings("org.gnome.desktop.peripherals.keyboard")
        self.sources = settings("org.gnome.desktop.input-sources")

        typing = self.add_group("Typing")
        repeat = switch_row(self.keyboard, "repeat", "Repeat keys", "Hold a key to type it again and again")
        self.add_rows(typing, repeat)
        if self.keyboard is not None:
            delay = Adw.ActionRow(title="Repeat delay", subtitle="How long to hold before repeating")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 150, 1000, 50)
            scale.set_size_request(220, -1)
            scale.set_value(self.keyboard.get_uint("delay"))
            scale.update_property([Gtk.AccessibleProperty.LABEL], ["Repeat delay"])
            scale.connect("value-changed", lambda s: self.keyboard.set_uint("delay", int(s.get_value())))
            delay.add_suffix(scale)
            self.keyboard.bind("repeat", delay, "sensitive", Gio.SettingsBindFlags.GET)
            typing.add(delay)

        if self.sources is not None:
            typing.add(choice_row("Caps Lock key", "What the Caps Lock key does",
                                  [("", "Caps Lock"), *CAPS_OPTIONS.items()],
                                  self.get_caps, self.set_caps))

        more = self.add_group("More")
        for title, subtitle, panel in (("Keyboard layouts", "Add or switch typing languages", "keyboard"),
                                       ("Keyboard shortcuts", "View and change every shortcut", "keyboard")):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, p=panel: open_gnome_panel(p))
            more.add(row)
        self.add_reset(self.reset)

    def get_caps(self) -> str:
        options = self.sources.get_strv("xkb-options")
        return next((o for o in options if o in CAPS_OPTIONS), "")

    def set_caps(self, value: str) -> None:
        options = [o for o in self.sources.get_strv("xkb-options") if o not in CAPS_OPTIONS]
        if value:
            options.append(value)
        self.sources.set_strv("xkb-options", options)

    def reset(self) -> None:
        reset_keys(self.keyboard, ["repeat", "delay", "repeat-interval"])
        self.set_caps("")
        self.window.reload_page(self)
        self.toast("Keyboard reset to Kulan defaults")
