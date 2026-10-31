"""Battery and power: charge level, the battery's health and charge limit,
the apps using the most power, power mode (power-profiles-daemon), the
percentage in the top bar, and when the screen turns off and the computer
sleeps. Charge comes from UPower; on desktops and VMs without a battery
that part is hidden."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import (
    Page,
    choice_row,
    has_key,
    plain_error,
    run_async,
    settings,
    switch_row,
)

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

# What the processor is busy with, per app: GNOME starts each app in its own
# cgroup under the user's app.slice (app-gnome-<id>-<pid>.scope,
# app-flatpak-<id>-<pid>.scope), with its processor time in cpu.stat.
APP_SLICE = "/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/app.slice"
POWER_SAMPLE_S = 5


def health_text(full: float, design: float, capacity: float, cycles: int) -> str:
    """The battery's health in words: how much it holds compared with when it was new."""
    pct = capacity if capacity > 0 else (full * 100 / design if design > 0 else 0)
    pct = round(min(pct, 100))
    if pct <= 0:
        text = "Not reported by this battery"
    elif pct >= 80:
        text = f"Good: holds {pct}% of what it held when new"
    elif pct >= 60:
        text = f"Worn: holds {pct}% of what it held when new, so it runs out sooner"
    else:
        text = f"Worn out: holds only {pct}% of what it held when new. A new battery would last much longer"
    if cycles > 0:
        text += f" · {cycles} charge cycles"
    return text


# Background services that can use a lot of power, by the names people know.
KNOWN_SERVICES = {"kulan-assistant-engine.service": "Ask Kulan", "localsearch-3.service": "File search",
                  "tracker-miner-fs-3.service": "File search"}


def app_id(scope: str) -> str | None:
    """The app's ID from its cgroup's name, or None for anything that isn't an app."""
    if scope in KNOWN_SERVICES:
        return KNOWN_SERVICES[scope]
    m = re.fullmatch(r"app-(?:gnome|flatpak|dbus|kulan)?-?(.+?)(?:-\d+)?\.(?:scope|service)", scope)
    if not m:
        return None
    name = m.group(1).replace("\\x2d", "-")
    return name or None


def app_cpu() -> dict[str, float]:
    """Processor time so far (seconds) of each running app, added up by app."""
    uid = os.getuid()
    totals: dict[str, float] = {}
    try:
        scopes = list(Path(APP_SLICE.format(uid=uid)).iterdir())
    except OSError:
        return totals
    for scope in scopes:
        name = app_id(scope.name)
        if name is None:
            continue
        try:
            stat = (scope / "cpu.stat").read_text()
        except OSError:
            continue
        usec = next((int(line.split()[1]) for line in stat.splitlines() if line.startswith("usage_usec ")), 0)
        totals[name] = totals.get(name, 0.0) + usec / 1e6
    return totals


def app_name(app: str) -> str:
    for desktop in (f"{app}.desktop", f"{app.lower()}.desktop"):
        try:
            info = Gio.DesktopAppInfo.new(desktop)
        except TypeError:  # not installed
            info = None
        if info is not None:
            return info.get_name()
    return app


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



LIGHTER = "/usr/lib/kulan/lighter-mode"
LIGHTER_CHOICES = [("auto", "Automatic"), ("on", "On"), ("off", "Off")]


def lighter_status_async(done, page=None) -> None:
    """Lighter mode now (lighter-mode status), without waiting: done(status, or None
    without it) on the window's thread."""
    if not Path(LIGHTER).exists():
        GLib.idle_add(lambda: (done(None), False)[1])
        return

    def finished(ok: bool, out: str, _err: str) -> None:
        try:
            done(json.loads(out) if ok else None)
        except ValueError:
            done(None)

    run_async([LIGHTER, "status"], finished, timeout=10, page=page)


def lighter_text(status: dict) -> str:
    memory = f"{status.get('memory_kib', 0) / 1024 / 1024:.1f} GB"
    what = "Fewer effects and less memory use, for computers with 4 GB of memory."
    if status.get("choice") == "auto":
        now = (f"On, because this computer has {memory} of memory." if status.get("on") else
               f"Off. This computer has {memory} of memory, enough for full effects.")
    else:
        now = "On." if status.get("on") else "Off."
    if status.get("at_boot") is not None and status.get("on") != status.get("at_boot"):
        now += " Some changes need a restart."
    return f"{what} {now}"


def can_hibernate() -> bool:
    """Hibernating needs swap on a disk (Kulan OS uses compressed memory,
    zram, which can't hold a hibernated system) at least as big as memory."""
    try:
        swaps = Path("/proc/swaps").read_text().splitlines()[1:]
        meminfo = Path("/proc/meminfo").read_text()
    except OSError:
        return False
    disk = sum(int(line.split()[2]) for line in swaps
               if len(line.split()) >= 3 and "zram" not in line.split()[0] and line.split()[2].isdigit())
    total = next((int(line.split()[1]) for line in meminfo.splitlines() if line.startswith("MemTotal:")), 0)
    return bool(total) and disk >= total

class BatteryPage(Page):
    title = "Battery and power"
    icon = "battery-good-symbolic"
    keywords = ("battery", "power", "charge", "charging", "sleep", "suspend", "screen off", "blank",
                "power mode", "performance", "power saver", "percentage", "lid", "lighter mode", "speed",
                "slow", "memory", "4 gb", "effects", "animations")

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

        # The battery itself (UPower's device for it, not the combined display device).
        self.health_proxies: list = []  # each battery powering this computer (some laptops have two)
        self.health_group = self.add_group("Battery health")
        self.health_row = Adw.ActionRow(title="Health", subtitle_lines=0)
        self.draw_row = Adw.ActionRow(title="Power in use")
        self.limit_row = Adw.SwitchRow(subtitle_lines=0, visible=False,
                                       subtitle="Best for the battery’s life when the computer is mostly plugged "
                                                "in. Turn it off for a day you need a full charge")
        self.limit_handler = self.limit_row.connect("notify::active", lambda *_a: self.set_limit())
        for row in (self.health_row, self.draw_row, self.limit_row):
            self.health_group.add(row)
        self.health_group.set_visible(False)

        # What's using the processor (and so the battery) now, by app.
        self.apps_group = self.add_group("Using the most power", "Apps busy with the processor right now")
        self.apps_group.set_visible(False)
        self.app_rows: list[Adw.ActionRow] = []
        self.last_cpu = (time.monotonic(), app_cpu())
        GLib.timeout_add_seconds(POWER_SAMPLE_S, self.sample_apps)

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
            choices = [("interactive", "Show the power off menu"), ("suspend", "Sleep"), ("nothing", "Do nothing")]
            if can_hibernate() or self.power.get_string("power-button-action") == "hibernate":
                choices.insert(2, ("hibernate", "Hibernate"))
            idle.add(choice_row("Power button", None, choices,
                                lambda: self.power.get_string("power-button-action"),
                                lambda v: self.power.set_string("power-button-action", v)))

        saving_rows = [row for row in (
            switch_row(self.power, "power-saver-profile-on-low-battery", "Power saver when the battery is low",
                       "Switches back when you plug in"),
            switch_row(self.power, "ambient-enabled", "Automatic brightness",
                       "Uses the computer’s light sensor"),
        ) if row is not None]
        if saving_rows:  # no heading with nothing under it
            self.add_rows(self.add_group("Saving power"), *saving_rows)

        # Lighter mode: its status comes from a program, so it follows when that answers.
        self.lighter_now: dict | None = None
        self.speed_group = None
        if Path(LIGHTER).exists():
            self.speed_group = self.add_group("Speed")
            self.speed_group.set_visible(False)
            lighter_status_async(self.got_lighter, page=self)

        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, UPOWER, DISPLAY_DEVICE,
                                  "org.freedesktop.UPower.Device", None, self.got_battery)
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, UPOWER,
                                  "/org/freedesktop/UPower", UPOWER, None, self.got_upower)
        self.connect_profiles(0)

    def got_lighter(self, status: dict | None) -> None:
        if status is None or self.speed_group is None:
            return
        self.lighter_now = status
        self.lighter_row = choice_row("Lighter mode", lighter_text(status), LIGHTER_CHOICES,
                                      lambda: status.get("choice", "auto"), self.set_lighter)
        self.lighter_row.set_subtitle_lines(0)
        self.speed_group.add(self.lighter_row)
        self.speed_group.set_visible(True)

    def set_lighter(self, choice: str) -> None:
        # Checked again first (it may have changed since the page opened), without waiting on it.
        def checked(status: dict | None) -> None:
            if status is None or status.get("choice") == choice:
                return
            message = {"auto": "Lighter mode is automatic", "on": "Lighter mode on",
                       "off": "Lighter mode off"}[choice]
            self.window.helper.run(["lighter", choice], message, on_done=lambda _ok: self.window.reload_page(self))

        lighter_status_async(checked, page=self)

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

    # --- battery health, charge limit, power use ---------------------------------------

    def got_upower(self, _source, result) -> None:
        if self.torn_down:
            return
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return
        proxy.call("EnumerateDevices", None, Gio.DBusCallFlags.NONE, 5000, None, self.got_devices)

    def got_devices(self, proxy, result) -> None:
        if self.torn_down:
            return
        try:
            paths = proxy.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        self.try_devices([p for p in paths if "battery" in p.lower()])

    def try_devices(self, paths: list[str]) -> None:
        """Every device that's a battery powering this computer (not a mouse's), one after another."""
        if not paths or self.torn_down:
            return

        def done(_source, result) -> None:
            if self.torn_down:
                return
            try:
                proxy = Gio.DBusProxy.new_for_bus_finish(result)
            except GLib.Error:
                proxy = None
            if proxy is not None and self.prop(proxy, "Type", 0) == 2 and self.prop(proxy, "PowerSupply", False):
                self.health_proxies.append(proxy)
                self.watch(proxy, "g-properties-changed", lambda *_a: self.sync_health())
                self.sync_health()
            self.try_devices(paths[1:])

        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, UPOWER, paths[0],
                                  "org.freedesktop.UPower.Device", None, done)

    def sync_health(self) -> None:
        batteries = [p for p in self.health_proxies if self.prop(p, "IsPresent", False)]
        self.health_group.set_visible(bool(batteries))
        if not batteries:
            return
        texts = [health_text(float(self.prop(p, "EnergyFull", 0.0)), float(self.prop(p, "EnergyFullDesign", 0.0)),
                             float(self.prop(p, "Capacity", 0.0)), int(self.prop(p, "ChargeCycles", -1)))
                 for p in batteries]
        if len(texts) == 1:
            self.health_row.set_subtitle(texts[0])
        else:
            self.health_row.set_subtitle("\n".join(f"Battery {i + 1}: {text}" for i, text in enumerate(texts)))
        draining = [p for p in batteries if int(self.prop(p, "State", 0)) in (DISCHARGING, PENDING_DISCHARGE)]
        rate = sum(float(self.prop(p, "EnergyRate", 0.0)) for p in draining)
        self.draw_row.set_visible(rate > 0)
        self.draw_row.set_subtitle(f"{rate:.1f} W from the battery. Light use is usually under 10 W")
        limited = [p for p in batteries if self.prop(p, "ChargeThresholdSupported", False)]
        self.limit_row.set_visible(bool(limited))
        if limited:
            end = int(self.prop(limited[0], "ChargeEndThreshold", 0)) or 80
            self.limit_row.set_title(f"Stop charging at {end}%")
            self.limit_row.handler_block(self.limit_handler)
            self.limit_row.set_active(all(self.prop(p, "ChargeThresholdEnabled", False) for p in limited))
            self.limit_row.handler_unblock(self.limit_handler)

    def set_limit(self) -> None:
        on = self.limit_row.get_active()
        changing = [p for p in self.health_proxies if self.prop(p, "ChargeThresholdSupported", False)
                    and on != bool(self.prop(p, "ChargeThresholdEnabled", False))]
        failed = []

        def done(source, result) -> None:
            try:
                source.call_finish(result)
            except GLib.Error:
                if not self.torn_down and not failed:
                    failed.append(True)
                    self.toast("Couldn’t change the charge limit. Try again, or restart the computer first")
                    self.sync_health()

        for proxy in changing:
            proxy.call("EnableChargeThreshold", GLib.Variant("(b)", (on,)), Gio.DBusCallFlags.NONE, 10000, None, done)

    def sample_apps(self) -> bool:
        """Every few seconds while the page is open: each app's share of the processor."""
        if self.torn_down:
            return GLib.SOURCE_REMOVE
        then, before = self.last_cpu
        now, after = time.monotonic(), app_cpu()
        self.last_cpu = (now, after)
        cores = os.cpu_count() or 1
        busy = sorted(((app, (after[app] - before.get(app, after[app])) * 100 / max(now - then, 0.1) / cores)
                       for app in after), key=lambda a: -a[1])
        busy = [(app, pct) for app, pct in busy if pct >= 1][:5]
        for row in self.app_rows:
            self.apps_group.remove(row)
        self.app_rows = []
        for app, pct in busy:
            row = Adw.ActionRow(title=GLib.markup_escape_text(app_name(app)),
                                subtitle=f"{round(pct)}% of the processor")
            self.apps_group.add(row)
            self.app_rows.append(row)
        self.apps_group.set_visible(bool(busy) and self.battery_group.get_visible())
        return GLib.SOURCE_CONTINUE

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
        self.mode_group.set_description("Performance is limited because the computer is too warm"
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
            self.toast(f"Couldn’t change the power mode. {plain_error(exc)}")
            self.sync_profiles()
