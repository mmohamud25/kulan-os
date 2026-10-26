"""Top bar: Kulan menu, update icon, clock and battery."""
from __future__ import annotations

from .common import KULAN_SCHEMA, Page, choice_row, reset_keys, settings, switch_row

CLOCK_KEYS = ["clock-show-weekday", "clock-show-date", "clock-show-seconds", "clock-format",
              "show-battery-percentage"]


class TopBarPage(Page):
    title = "Top bar"
    icon = "focus-top-bar-symbolic"
    keywords = ("top bar", "panel", "menu", "clock", "seconds", "battery", "update icon")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.kulan = settings(KULAN_SCHEMA)
        self.interface = settings("org.gnome.desktop.interface")

        kulan = self.add_group("Kulan")
        self.add_rows(kulan,
                      switch_row(self.kulan, "kulan-menu", "Kulan menu",
                                 "The logo menu at the top left"),
                      switch_row(self.kulan, "update-indicator", "Update icon",
                                 "Shown while updates are waiting or installing"),
                      switch_row(self.kulan, "ask-button", "Ask Kulan button",
                                 "Opens Ask Kulan, also Shift+Super+Space. Needs Kulan Assistant."))

        clock = self.add_group("Clock")
        self.add_rows(clock,
                      switch_row(self.interface, "clock-show-weekday", "Day of the week"),
                      switch_row(self.interface, "clock-show-date", "Date"),
                      switch_row(self.interface, "clock-show-seconds", "Seconds"))
        if self.interface is not None:
            clock.add(choice_row("Time format", None, [("24h", "24-hour"), ("12h", "12-hour (AM/PM)")],
                                 lambda: self.interface.get_string("clock-format"),
                                 lambda v: self.interface.set_string("clock-format", v)))

        battery = self.add_group("Battery", "On laptops.")
        self.add_rows(battery, switch_row(self.interface, "show-battery-percentage", "Battery percentage"))
        self.add_reset(self.reset)

    def reset(self) -> None:
        reset_keys(self.kulan, ["kulan-menu", "update-indicator", "ask-button"])
        reset_keys(self.interface, CLOCK_KEYS)
        self.window.reload_page(self)
        self.toast("Top bar reset to Kulan defaults")
