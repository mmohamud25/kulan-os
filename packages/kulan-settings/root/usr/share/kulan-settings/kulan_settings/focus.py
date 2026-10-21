"""Focus: Do Not Disturb now, or on a schedule every day."""
from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .common import KULAN_SCHEMA, Page, has_key, open_gnome_panel, reset_keys, settings, switch_row, time_row

NOTIFICATIONS = "org.gnome.desktop.notifications"


class FocusPage(Page):
    title = "Focus"
    icon = "weather-clear-night-symbolic"
    keywords = ("focus", "do not disturb", "notifications", "quiet", "schedule", "sleep", "banners")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.notifications = settings(NOTIFICATIONS)
        self.kulan = settings(KULAN_SCHEMA)

        now = self.add_group(None, "Notifications still arrive, and wait in the notification list.")
        self.add_rows(now, switch_row(self.notifications, "show-banners", "Do Not Disturb",
                                      "No pop-ups or sounds for notifications", invert=True))

        if has_key(self.kulan, "focus-schedule"):
            schedule = self.add_group("Schedule", "Turns Do Not Disturb on and off at these times every day.")
            on = switch_row(self.kulan, "focus-schedule", "Focus every day")
            start = time_row("Starts", None, lambda: self.kulan.get_double("focus-from"),
                             lambda v: self.kulan.set_double("focus-from", v))
            end = time_row("Ends", "If it ends earlier than it starts, it ends the next morning",
                           lambda: self.kulan.get_double("focus-to"),
                           lambda v: self.kulan.set_double("focus-to", v))
            for row in (start, end):
                self.kulan.bind("focus-schedule", row, "sensitive", Gio.SettingsBindFlags.GET)
            self.add_rows(schedule, on, start, end)

        apps = self.add_group()
        row = Adw.ActionRow(title="Notifications from each app", subtitle="Choose which apps may notify you",
                            activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r: open_gnome_panel("notifications"))
        apps.add(row)
        self.add_reset(self.reset)

    def reset(self) -> None:
        reset_keys(self.notifications, ["show-banners"])
        reset_keys(self.kulan, ["focus-schedule", "focus-from", "focus-to"])
        self.window.reload_page(self)
        self.toast("Focus reset to Kulan defaults")
