"""Night Light and display: warm colours at night, text size, display settings."""
from __future__ import annotations

from gi.repository import Adw, Gtk

from .common import Page, choice_row, has_key, open_gnome_panel, reset_keys, settings, switch_row

COLOR_SCHEMA = "org.gnome.settings-daemon.plugins.color"
TEXT_SIZES = [("1.0", "Default"), ("1.1", "Larger"), ("1.25", "Large"), ("1.5", "Largest")]


class DisplayPage(Page):
    title = "Night Light and display"
    icon = "night-light-symbolic"
    keywords = ("night light", "warm", "blue light", "text size", "font size", "display", "resolution", "scale")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.color = settings(COLOR_SCHEMA)
        self.interface = settings("org.gnome.desktop.interface")

        night = self.add_group("Night Light", "Warmer colours in the evening are easier on the eyes.")
        enabled = switch_row(self.color, "night-light-enabled", "Night Light")
        self.add_rows(night, enabled)
        if self.color is not None:
            schedule = choice_row("Schedule", None, [("sunset", "Sunset to sunrise"), ("manual", "Set times")],
                                  lambda: "sunset" if self.color.get_boolean("night-light-schedule-automatic")
                                  else "manual",
                                  lambda v: self.color.set_boolean("night-light-schedule-automatic", v == "sunset"))
            night.add(schedule)
            self.times = Adw.ActionRow(title="Times")
            self.from_spin = self.hour_spin("night-light-schedule-from", "Night Light starts")
            self.to_spin = self.hour_spin("night-light-schedule-to", "Night Light ends")
            box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            box.append(Gtk.Label(label="From"))
            box.append(self.from_spin)
            box.append(Gtk.Label(label="to"))
            box.append(self.to_spin)
            self.times.add_suffix(box)
            night.add(self.times)
            if has_key(self.color, "night-light-temperature"):
                warmth = Adw.ActionRow(title="Warmth")
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1700, 4700, 100)
                scale.set_inverted(True)  # right = warmer
                scale.set_size_request(220, -1)
                scale.set_value(self.color.get_uint("night-light-temperature"))
                scale.update_property([Gtk.AccessibleProperty.LABEL], ["Night Light warmth"])
                scale.connect("value-changed",
                              lambda s: self.color.set_uint("night-light-temperature", int(s.get_value())))
                warmth.add_suffix(scale)
                night.add(warmth)
            self.watch(self.color, "changed", lambda *_a: self.sync())
            self.sync()

        display = self.add_group("Display")
        if has_key(self.interface, "text-scaling-factor"):
            display.add(choice_row("Text size", None, TEXT_SIZES, self.get_text_size,
                                   lambda v: self.interface.set_double("text-scaling-factor", float(v))))
        more = Adw.ActionRow(title="Resolution, scale and screens", activatable=True)
        more.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        more.connect("activated", lambda _r: open_gnome_panel("display"))
        display.add(more)
        self.add_reset(self.reset)

    def hour_spin(self, key: str, label: str) -> Gtk.SpinButton:
        spin = Gtk.SpinButton.new_with_range(0, 23.5, 0.5)
        spin.set_digits(1)
        spin.set_value(self.color.get_double(key))
        spin.set_valign(Gtk.Align.CENTER)
        spin.update_property([Gtk.AccessibleProperty.LABEL], [f"{label} (hour)"])
        spin.connect("value-changed", lambda s: self.color.set_double(key, s.get_value()))
        return spin

    def sync(self) -> None:
        on = self.color.get_boolean("night-light-enabled")
        self.times.set_visible(not self.color.get_boolean("night-light-schedule-automatic"))
        self.times.set_sensitive(on)

    def get_text_size(self) -> str:
        value = self.interface.get_double("text-scaling-factor")
        return min((v for v, _l in TEXT_SIZES), key=lambda v: abs(float(v) - value))

    def reset(self) -> None:
        reset_keys(self.color, ["night-light-enabled", "night-light-schedule-automatic",
                                "night-light-schedule-from", "night-light-schedule-to", "night-light-temperature"])
        reset_keys(self.interface, ["text-scaling-factor"])
        self.window.reload_page(self)
        self.toast("Night Light and display reset to Kulan defaults")
