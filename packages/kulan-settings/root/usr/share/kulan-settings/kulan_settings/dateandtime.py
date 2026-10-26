"""Date and time: time zone, setting the clock, and how the top bar shows it."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .common import Page, choice_row, has_key, reset_keys, settings, switch_row

INTERFACE = "org.gnome.desktop.interface"
DATETIME = "org.gnome.desktop.datetime"
CALENDAR = "org.gnome.desktop.calendar"
ZONES = Path("/usr/share/zoneinfo/zone1970.tab")


def zone_label(zone: str) -> str:
    """"America/Argentina/Buenos_Aires" -> "Buenos Aires (America, Argentina)"."""
    if zone in ("UTC", "Etc/UTC", "Etc/Universal", "Universal", "Zulu"):
        return "UTC (no time zone)"
    parts = zone.replace("_", " ").split("/")
    return parts[-1] if len(parts) == 1 else f"{parts[-1]} ({', '.join(parts[:-1])})"


def zones_from_file() -> list[str]:
    zones = set()
    try:
        for line in ZONES.read_text().splitlines():
            if line and not line.startswith("#"):
                fields = line.split("\t")
                if len(fields) >= 3:
                    zones.add(fields[2])
    except OSError:
        pass
    zones.add("UTC")
    return sorted(zones)


class DateTimePage(Page):
    title = "Date and time"
    icon = "preferences-system-time-symbolic"
    keywords = ("date", "time", "clock", "time zone", "timezone", "24-hour", "12-hour", "am pm", "seconds",
                "weekday", "week numbers", "ntp", "automatic")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.interface = settings(INTERFACE)
        self.datetime = settings(DATETIME)
        self.calendar = settings(CALENDAR)
        self.timedate = None
        try:
            self.timedate = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, "org.freedesktop.timedate1",
                "/org/freedesktop/timedate1", "org.freedesktop.timedate1", None)
        except GLib.Error:
            self.timedate = None

        clock = self.add_group("Clock")
        self.now_row = Adw.ActionRow(title="Now")
        self.now_label = Gtk.Label(css_classes=["numeric"])
        self.now_row.add_suffix(self.now_label)
        clock.add(self.now_row)
        if self.timedate is not None:
            self.ntp_row = Adw.SwitchRow(title="Set the time automatically", subtitle="From time servers on the internet",
                                         active=bool(self.prop("NTP")))
            self.ntp_row.set_sensitive(bool(self.prop("CanNTP", True)))
            self.ntp_row.connect("notify::active", lambda r, _p: self.set_ntp(r.get_active()))
            clock.add(self.ntp_row)
            self.manual_row = Adw.ActionRow(title="Date and time", subtitle="Set by hand", activatable=True)
            self.manual_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.manual_row.connect("activated", lambda _r: self.set_time_dialog())
            self.ntp_row.bind_property("active", self.manual_row, "sensitive",
                                       GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN)
            clock.add(self.manual_row)

        zone = self.add_group("Time zone")
        self.add_rows(zone, switch_row(self.datetime, "automatic-timezone", "Set the time zone automatically",
                                       "From your location, when Location services are on"))
        if self.timedate is not None:
            self.zones = self.list_zones()
            current = self.prop("Timezone") or "UTC"
            if current not in self.zones:
                self.zones.append(current)
            model = Gtk.StringList.new([zone_label(z) for z in self.zones])
            self.zone_row = Adw.ComboRow(title="Time zone", model=model, enable_search=True,
                                         expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            self.zone_row.set_selected(self.zones.index(current))
            self.zone_row.connect("notify::selected", lambda r, _p: self.set_zone(self.zones[r.get_selected()]))
            if self.datetime is not None and has_key(self.datetime, "automatic-timezone"):
                self.datetime.bind("automatic-timezone", self.zone_row, "sensitive",
                                   Gio.SettingsBindFlags.GET | Gio.SettingsBindFlags.INVERT_BOOLEAN)
            zone.add(self.zone_row)
        else:
            zone.add(Adw.ActionRow(title="Time zone", subtitle="The system’s time service isn’t running"))

        bar = self.add_group("In the top bar")
        if self.interface is not None and has_key(self.interface, "clock-format"):
            bar.add(choice_row("Time format", None, [("24h", "24-hour (14:30)"), ("12h", "12-hour (2:30 PM)")],
                               lambda: self.interface.get_string("clock-format"),
                               lambda v: self.interface.set_string("clock-format", v)))
        self.add_rows(
            bar,
            switch_row(self.interface, "clock-show-weekday", "Show the day of the week"),
            switch_row(self.interface, "clock-show-date", "Show the date"),
            switch_row(self.interface, "clock-show-seconds", "Show seconds"),
            switch_row(self.calendar, "show-weekdate", "Week numbers in the calendar"),
        )
        self.add_reset(self.reset)
        self.tick()
        self._timer = GLib.timeout_add_seconds(1, self.tick)

    def prop(self, name: str, default=None):
        if self.timedate is None:
            return default
        value = self.timedate.get_cached_property(name)
        return value.unpack() if value is not None else default

    def list_zones(self) -> list[str]:
        try:
            reply = self.timedate.call_sync("ListTimezones", None, Gio.DBusCallFlags.NONE, 5000, None)
            zones = list(reply.unpack()[0])
            if zones:
                return zones
        except GLib.Error:
            pass
        return zones_from_file()

    def tick(self) -> bool:
        if self.torn_down:
            return GLib.SOURCE_REMOVE
        now = GLib.DateTime.new_now_local()
        twelve = self.interface is not None and has_key(self.interface, "clock-format") \
            and self.interface.get_string("clock-format") == "12h"
        self.now_label.set_label(now.format("%A %-d %B %Y, " + ("%-l:%M:%S %p" if twelve else "%H:%M:%S")))
        return GLib.SOURCE_CONTINUE

    def call(self, method: str, args: GLib.Variant, done_text: str) -> None:
        """timedated asks for an administrator's password itself when needed."""
        def finish(proxy, result):
            try:
                proxy.call_finish(result)
                if not self.torn_down:
                    self.toast(done_text)
            except GLib.Error as exc:
                if not self.torn_down:
                    dismissed = "dismissed" in exc.message.lower() or "not authorized" in exc.message.lower()
                    self.toast("Not changed" if dismissed else f"Couldn’t change it: {exc.message}")
                    self.window.reload_page(self)
        self.timedate.call(method, args, Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None, finish)

    def set_ntp(self, on: bool) -> None:
        if bool(self.prop("NTP")) != on:
            self.call("SetNTP", GLib.Variant("(bb)", (on, True)),
                      "The clock is set automatically" if on else "Automatic time is off")

    def set_zone(self, zone: str) -> None:
        if zone != self.prop("Timezone"):
            self.call("SetTimezone", GLib.Variant("(sb)", (zone, True)), f"Time zone set to {zone_label(zone)}")

    def set_time_dialog(self) -> None:
        now = GLib.DateTime.new_now_local()
        dialog = Adw.AlertDialog(heading="Date and time", body="The clock is set to this, in your time zone.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        calendar = Gtk.Calendar()
        calendar.select_day(now)
        box.append(calendar)
        times = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        minute = Gtk.SpinButton.new_with_range(0, 59, 1)
        for spin, value, name in ((hour, now.get_hour(), "Hour"), (minute, now.get_minute(), "Minutes")):
            spin.set_value(value)
            spin.set_wrap(True)
            spin.set_numeric(True)
            spin.update_property([Gtk.AccessibleProperty.LABEL], [name])
        times.append(hour)
        times.append(Gtk.Label(label=":", css_classes=["title-3"]))
        times.append(minute)
        box.append(times)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("set", "Set")
        dialog.set_response_appearance("set", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response: str) -> None:
            if response != "set":
                return
            day = calendar.get_date()
            chosen = GLib.DateTime.new_local(day.get_year(), day.get_month(), day.get_day_of_month(),
                                             hour.get_value_as_int(), minute.get_value_as_int(), 0)
            if chosen is not None:
                self.call("SetTime", GLib.Variant("(xbb)", (chosen.to_unix() * 1_000_000, False, True)),
                          "Clock set")
        dialog.connect("response", answered)
        dialog.present(self.window)

    def teardown(self) -> None:
        super().teardown()
        if getattr(self, "_timer", 0):
            GLib.source_remove(self._timer)
            self._timer = 0

    def reset(self) -> None:
        reset_keys(self.interface, ["clock-format", "clock-show-weekday", "clock-show-date", "clock-show-seconds"])
        reset_keys(self.calendar, ["show-weekdate"])
        reset_keys(self.datetime, ["automatic-timezone"])
        self.window.reload_page(self)
        self.toast("Date and time display reset to Kulan defaults")
