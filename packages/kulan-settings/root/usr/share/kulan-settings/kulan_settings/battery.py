"""Battery and power: charge level, power mode (power-profiles-daemon), the
percentage in the top bar, and when the screen turns off and the computer
sleeps. Charge comes from UPower; on desktops and VMs without a battery
that part is hidden."""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, choice_row, has_key, open_gnome_panel, settings, switch_row

UPOWER = "org.freedesktop.UPower"
DISPLAY_DEVICE = "/org/freedesktop/UPower/devices/DisplayDevice"
PROFILES = [("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
            ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")]
PROFILE_LABELS = {"performance": ("Performance", "Fastest, uses more power"),
                  "balanced": ("Balanced", "Standard speed and battery life"),
                  "power-saver": ("Power saver", "Slower, battery lasts longer")}

SCREEN_OFF = [(60, "1 minute"), (120, "2 minutes"), (300, "5 minutes"), (600, "10 minutes"),
              (900, "15 minutes"), (1800, "30 minutes"), (0, "Never")]
SLEEP = [(900, "15 minutes"), (1200, "20 minutes"), (1800, "30 minutes"), (2700, "45 minutes"),
         (3600, "1 hour"), (5400, "1 hour 30 minutes"), (0, "Never")]

# UPower device states
CHARGING, DISCHARGING, EMPTY, FULL, PENDING_CHARGE, PENDING_DISCHARGE = 1, 2, 3, 4, 5, 6


def duration_text(seconds: int) -> str:
    minutes = max(0, int(seconds)) // 60
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


def battery_text(percent: float, state: int, to_empty: int, to_full: int) -> str:
    level = f"{round(percent)}%"
    if state == CHARGING:
        return f"{level}, charging" + (f" · full in {duration_text(to_full)}" if to_full > 0 else "")
    if state == FULL or (state == PENDING_CHARGE and percent >= 99):
        return f"{level}, fully charged"
    if state == PENDING_CHARGE:
        return f"{level}, plugged in, not charging"
    if state == DISCHARGING:
        return f"{level}" + (f" · about {duration_text(to_empty)} left" if to_empty > 0 else "")
    return level


def nearest(value: int, choices: list[tuple[int, str]]) -> str:
    """The choice closest to a setting's value (0 = never), as a string."""
    if value <= 0:
        return "0"
    timed = [c for c, _l in choices if c > 0]
    return str(min(timed, key=lambda c: abs(c - value)))


class BatteryPage(Page):
    title = "Battery and power"
    icon = "battery-good-symbolic"
    keywords = ("battery", "power", "charge", "charging", "sleep", "suspend", "screen off", "blank",
                "power mode", "performance", "power saver", "percentage", "lid")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.interface = settings("org.gnome.desktop.interface")
        self.session = settings("org.gnome.desktop.session")
        self.power = settings("org.gnome.settings-daemon.plugins.power")
        self.battery_proxy = None
        self.profiles_proxy = None
        self.profile_buttons: dict[str, Gtk.CheckButton] = {}

        self.battery_group = self.add_group("Battery")
        self.battery_row = Adw.ActionRow(title="Battery")
        self.level = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER)
        self.level.set_size_request(160, -1)
        self.battery_row.add_suffix(self.level)
        self.battery_group.add(self.battery_row)
        self.add_rows(self.battery_group,
                      switch_row(self.interface, "show-battery-percentage", "Show percentage in the top bar"))
        self.battery_group.set_visible(False)

        self.mode_group = self.add_group("Power mode")
        self.mode_group.set_visible(False)

        idle = self.add_group("Screen and sleep")
        if has_key(self.session, "idle-delay"):
            idle.add(choice_row("Turn the screen off after", "When you aren’t using the computer",
                                [(str(v), label) for v, label in SCREEN_OFF],
                                lambda: nearest(self.session.get_uint("idle-delay"), SCREEN_OFF),
                                lambda v: self.session.set_uint("idle-delay", int(v))))
        self.add_rows(idle, switch_row(self.power, "idle-dim", "Dim the screen first",
                                       "Dims the screen a little before it turns off"))
        if has_key(self.power, "sleep-inactive-ac-type"):
            idle.add(self.sleep_row("ac", "Sleep when plugged in"))
            self.battery_sleep = self.sleep_row("battery", "Sleep on battery")
            self.battery_sleep.set_visible(False)
            idle.add(self.battery_sleep)
        else:
            self.battery_sleep = None
        if has_key(self.power, "power-button-action"):
            idle.add(choice_row("Power button", None,
                                [("interactive", "Show the power off menu"), ("suspend", "Sleep"),
                                 ("hibernate", "Hibernate"), ("nothing", "Do nothing")],
                                lambda: self.power.get_string("power-button-action"),
                                lambda v: self.power.set_string("power-button-action", v)))

        more = self.add_group()
        row = Adw.ActionRow(title="More power settings", subtitle="Wi-Fi and Bluetooth power saving",
                            activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r: open_gnome_panel("power"))
        more.add(row)

        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, UPOWER, DISPLAY_DEVICE,
                                  "org.freedesktop.UPower.Device", None, self.got_battery)
        self.connect_profiles(0)

    def sleep_row(self, source: str, title: str) -> Adw.ComboRow:
        type_key, timeout_key = f"sleep-inactive-{source}-type", f"sleep-inactive-{source}-timeout"

        def get() -> str:
            if self.power.get_string(type_key) == "nothing":
                return "0"
            return nearest(self.power.get_int(timeout_key), SLEEP)

        def set_(value: str) -> None:
            seconds = int(value)
            if seconds == 0:
                self.power.set_string(type_key, "nothing")
            else:
                self.power.set_string(type_key, "suspend")
                self.power.set_int(timeout_key, seconds)

        return choice_row(title, None, [(str(v), label) for v, label in SLEEP], get, set_)

    # --- battery ---------------------------------------------------------------------

    def got_battery(self, _source, result) -> None:
        if self.torn_down:
            return
        try:
            self.battery_proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return
        self.watch(self.battery_proxy, "g-properties-changed", lambda *_a: self.sync_battery())
        self.sync_battery()

    def prop(self, proxy, name: str, default=None):
        value = proxy.get_cached_property(name) if proxy is not None else None
        return value.unpack() if value is not None else default

    def sync_battery(self) -> None:
        proxy = self.battery_proxy
        present = bool(self.prop(proxy, "IsPresent", False)) and self.prop(proxy, "Type", 0) == 2
        self.battery_group.set_visible(present)
        if self.battery_sleep is not None:
            self.battery_sleep.set_visible(present)
        if not present:
            return
        percent = float(self.prop(proxy, "Percentage", 0.0))
        state = int(self.prop(proxy, "State", 0))
        self.battery_row.set_subtitle(battery_text(percent, state, int(self.prop(proxy, "TimeToEmpty", 0)),
                                                   int(self.prop(proxy, "TimeToFull", 0))))
        self.level.set_value(percent)

    # --- power mode ------------------------------------------------------------------

    def connect_profiles(self, index: int) -> None:
        if index >= len(PROFILES):
            return
        name, path = PROFILES[index]

        def done(_source, result) -> None:
            if self.torn_down:
                return
            try:
                proxy = Gio.DBusProxy.new_for_bus_finish(result)
            except GLib.Error:
                proxy = None
            if proxy is None or proxy.get_name_owner() is None:
                self.connect_profiles(index + 1)
                return
            self.profiles_proxy = proxy
            self.watch(proxy, "g-properties-changed", lambda *_a: self.sync_profiles())
            self.build_profiles()

        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, name, path, name, None, done)

    def build_profiles(self) -> None:
        available = [p.get("Profile") for p in self.prop(self.profiles_proxy, "Profiles", []) or []]
        group_button = None
        for profile in ("performance", "balanced", "power-saver"):
            if profile not in available:
                continue
            label, subtitle = PROFILE_LABELS[profile]
            row = Adw.ActionRow(title=label, subtitle=subtitle, activatable=True)
            button = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if group_button is not None:
                button.set_group(group_button)
            group_button = group_button or button
            button.connect("toggled", lambda b, p=profile: b.get_active() and self.set_profile(p))
            row.add_prefix(button)
            row.set_activatable_widget(button)
            self.profile_buttons[profile] = button
            self.mode_group.add(row)
        self.mode_group.set_visible(bool(self.profile_buttons))
        self.sync_profiles()

    def sync_profiles(self) -> None:
        active = self.prop(self.profiles_proxy, "ActiveProfile", "")
        button = self.profile_buttons.get(active)
        if button is not None and not button.get_active():
            button.set_active(True)
        degraded = self.prop(self.profiles_proxy, "PerformanceDegraded", "")
        self.mode_group.set_description("Performance is limited right now: the computer is too warm"
                                        if degraded == "high-operating-temperature" else
                                        "Performance is limited right now" if degraded else "")

    def set_profile(self, profile: str) -> None:
        proxy = self.profiles_proxy
        if proxy is None or self.prop(proxy, "ActiveProfile", "") == profile:
            return
        proxy.call("org.freedesktop.DBus.Properties.Set",
                   GLib.Variant("(ssv)", (proxy.get_interface_name(), "ActiveProfile", GLib.Variant("s", profile))),
                   Gio.DBusCallFlags.NONE, -1, None, self.profile_done)

    def profile_done(self, proxy, result) -> None:
        try:
            proxy.call_finish(result)
        except GLib.Error as exc:
            self.toast(f"Couldn’t change the power mode: {exc.message}")
            self.sync_profiles()
