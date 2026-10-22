"""Shared helpers for Kulan Settings pages."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from gi.repository import Adw, Gio, GLib, GObject, Gtk

OS_RELEASE = Path("/usr/lib/os-release")
HELPER = "/usr/lib/kulan/update-run"
KULAN_SCHEMA = "org.kulan.desktop"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def os_release() -> dict[str, str]:
    info: dict[str, str] = {}
    try:
        for line in OS_RELEASE.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep:
                parts = shlex.split(value)
                info[key] = parts[0] if parts else ""
    except (OSError, ValueError):
        pass
    return info


def plural(count: int, word: str, words: str | None = None) -> str:
    return f"{count} {word if count == 1 else (words or word + 's')}"


def package_installed(name: str) -> bool:
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Status}", name],
                             capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.strip() == "install ok installed"


def settings(schema_id: str) -> Gio.Settings | None:
    """A Gio.Settings for schema_id, or None when the schema isn't installed
    (for example Dash to Dock removed): pages then hide those rows."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema_id, True) is None:
        return None
    return Gio.Settings.new(schema_id)


def has_key(gsettings: Gio.Settings | None, key: str) -> bool:
    return gsettings is not None and gsettings.props.settings_schema.has_key(key)


def launch(desktop_id: str, toast: Callable[[str], None] | None = None) -> bool:
    info = Gio.DesktopAppInfo.new(desktop_id)
    if info is None:
        if toast:
            toast("That app isn’t installed")
        return False
    try:
        info.launch([], None)
        return True
    except GLib.Error as exc:
        if toast:
            toast(f"Couldn’t open it: {exc.message}")
        return False


def open_gnome_panel(panel: str, *params: str) -> None:
    """Open a page of GNOME Settings (Wi-Fi, displays, users...)."""
    try:
        Gio.Subprocess.new(["gnome-control-center", panel, *params], Gio.SubprocessFlags.NONE)
    except GLib.Error:
        pass


# --- rows bound to GSettings ------------------------------------------------------

def switch_row(gsettings: Gio.Settings | None, key: str, title: str, subtitle: str | None = None,
               invert: bool = False) -> Adw.SwitchRow | None:
    if not has_key(gsettings, key):
        return None
    row = Adw.SwitchRow(title=title, subtitle=subtitle or "")
    flags = Gio.SettingsBindFlags.DEFAULT
    if invert:
        flags |= Gio.SettingsBindFlags.INVERT_BOOLEAN
    gsettings.bind(key, row, "active", flags)
    return row


def choice_row(title: str, subtitle: str | None, choices: list[tuple[str, str]],
               get: Callable[[], str], set_: Callable[[str], None]) -> Adw.ComboRow:
    """A drop-down row. choices = [(value, label)]; get/set work in values."""
    labels = Gtk.StringList.new([label for _v, label in choices])
    row = Adw.ComboRow(title=title, subtitle=subtitle or "", model=labels)
    values = [value for value, _l in choices]
    current = get()
    row.set_selected(values.index(current) if current in values else 0)
    row.connect("notify::selected", lambda r, _p: set_(values[r.get_selected()]))
    return row


def enum_row(gsettings: Gio.Settings | None, key: str, title: str, subtitle: str | None,
             choices: list[tuple[str, str]]) -> Adw.ComboRow | None:
    if not has_key(gsettings, key):
        return None
    return choice_row(title, subtitle, choices, lambda: gsettings.get_string(key),
                      lambda v: gsettings.set_string(key, v))


def time_row(title: str, subtitle: str | None, get: Callable[[], float],
             set_: Callable[[float], None]) -> Adw.ActionRow:
    """A row with an HH:MM picker for a time of day stored as hours (22.5 = 22:30)."""
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    value = max(0.0, min(get(), 23.99))
    hour = int(value)
    minute = min(round((value - hour) * 60 / 5) * 5, 55)

    def spin(upper: int, step: int, current: int, label: str) -> Gtk.SpinButton:
        button = Gtk.SpinButton.new_with_range(0, upper, step)
        button.set_value(current)
        button.set_wrap(True)
        button.set_numeric(True)
        button.set_width_chars(2)
        button.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}: {label}"])

        def show(b: Gtk.SpinButton) -> bool:
            b.set_text(f"{b.get_value_as_int():02d}")
            return True

        button.connect("output", show)
        return button

    hours = spin(23, 1, hour, "hour")
    minutes = spin(55, 5, minute, "minutes")
    for button in (hours, minutes):
        button.connect("value-changed", lambda _b: set_(hours.get_value_as_int() + minutes.get_value_as_int() / 60))
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    box.append(hours)
    box.append(Gtk.Label(label=":", css_classes=["title-3"]))
    box.append(minutes)
    row.add_suffix(box)
    return row


def reset_button(on_reset: Callable[[], None]) -> Gtk.Button:
    button = Gtk.Button(label="Reset to Kulan defaults", halign=Gtk.Align.CENTER,
                        margin_top=12, css_classes=["pill"])
    button.connect("clicked", lambda _b: on_reset())
    return button


def reset_keys(gsettings: Gio.Settings | None, keys: list[str]) -> None:
    if gsettings is None:
        return
    for key in keys:
        if has_key(gsettings, key):
            gsettings.reset(key)


# --- privileged helper ---------------------------------------------------------------

class Helper:
    """Runs /usr/lib/kulan/update-run through pkexec and reports progress.

    One action at a time. Progress lines are apt's status lines
    (pmstatus/dlstatus); on_progress gets (fraction, text)."""

    def __init__(self, toast: Callable[[str], None]) -> None:
        self.toast = toast
        self.busy = False
        self.listeners: list[Callable[[bool], None]] = []

    def run(self, args: list[str], success: str | None = None,
            on_progress: Callable[[float, str], None] | None = None,
            on_done: Callable[[bool], None] | None = None, stdin: str | None = None) -> None:
        """stdin: text handed to the helper on standard input (a password),
        never on the command line, where other users could see it."""
        if self.busy:
            self.toast("Another change is still running")
            if on_done:
                on_done(False)
            return
        if shutil.which("pkexec") is None:
            self.toast("pkexec isn’t installed, so this can’t be changed from here")
            if on_done:
                on_done(False)
            return
        try:
            flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            if stdin is not None:
                flags |= Gio.SubprocessFlags.STDIN_PIPE
            proc = Gio.Subprocess.new(["pkexec", HELPER, *args], flags)
        except GLib.Error as exc:
            self.toast(f"Couldn’t start: {exc.message}")
            if on_done:
                on_done(False)
            return
        self.set_busy(True)
        if stdin is not None:
            pipe = proc.get_stdin_pipe()
            try:
                pipe.write_all((stdin + "\n").encode(), None)
            except GLib.Error:
                pass
            pipe.close(None)
        errors: list[str] = []

        def reader(stream: Gio.DataInputStream, handle: Callable[[str], None]) -> None:
            def got(s, result) -> None:
                try:
                    line, _n = s.read_line_finish_utf8(result)
                except GLib.Error:
                    line = None
                if line is None:
                    return
                handle(line)
                s.read_line_async(GLib.PRIORITY_DEFAULT, None, got)
            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, got)

        def progress(line: str) -> None:
            parts = line.split(":", 3)
            if on_progress is None or len(parts) != 4 or parts[0] not in ("pmstatus", "dlstatus"):
                return
            try:
                fraction = max(0.0, min(1.0, float(parts[2]) / 100))
            except ValueError:
                return
            fraction = fraction * 0.3 if parts[0] == "dlstatus" else 0.3 + fraction * 0.7
            on_progress(fraction, parts[3][:80])

        def error(line: str) -> None:
            if line.strip():
                errors.append(line.strip())
                del errors[:-20]

        reader(Gio.DataInputStream.new(proc.get_stdout_pipe()), progress)
        reader(Gio.DataInputStream.new(proc.get_stderr_pipe()), error)

        def exited(process, result) -> None:
            try:
                process.wait_finish(result)
                code = process.get_exit_status() if process.get_if_exited() else 1
            except GLib.Error:
                code = 1
            self.set_busy(False)
            if code == 0:
                if success:
                    self.toast(success)
            elif code in (126, 127):  # password dialog dismissed / not authorised
                self.toast("Cancelled")
            else:
                reason = next((e for e in reversed(errors) if not e.startswith(("W:", "N:"))),
                              "Something went wrong")
                self.toast(f"Couldn’t finish: {reason[:120]}")
            if on_done:
                on_done(code == 0)

        proc.wait_async(None, exited)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for listener in list(self.listeners):
            listener(busy)


class Page(Adw.Bin):
    """Base class: a titled preferences page with access to the window."""

    title = ""
    icon = "preferences-system-symbolic"
    keywords: tuple[str, ...] = ()

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.page = Adw.PreferencesPage()
        self.set_child(self.page)
        self._watches: list[tuple[GObject.Object, int]] = []
        self._monitors: list[Gio.FileMonitor] = []

    def watch(self, obj: GObject.Object | None, signal: str, callback) -> None:
        """Connect to something that outlives the page (settings, the helper's
        file monitor); teardown() disconnects it when the page is rebuilt."""
        if obj is not None:
            self._watches.append((obj, obj.connect(signal, callback)))

    def listen_busy(self, callback: Callable[[bool], None]) -> None:
        self.window.helper.listeners.append(callback)

    def monitor_file(self, path: str, callback: Callable[[], None]) -> None:
        monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        monitor.connect("changed", lambda *_a: callback())
        self._monitors.append(monitor)

    torn_down = False

    def teardown(self) -> None:
        """Disconnect everything watch(), listen_busy() and monitor_file() set up.
        Replies that arrive later (D-Bus, NetworkManager) check torn_down."""
        self.torn_down = True
        for obj, handler in self._watches:
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self._watches.clear()
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors.clear()
        listeners = self.window.helper.listeners
        listeners[:] = [f for f in listeners if getattr(f, "__self__", None) is not self]

    def toast(self, text: str) -> None:
        self.window.toast(text)

    def add_group(self, title: str | None = None, description: str | None = None) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title or "", description=description or "")
        self.page.add(group)
        return group

    @staticmethod
    def add_rows(group: Adw.PreferencesGroup, *rows) -> None:
        for row in rows:
            if row is not None:
                group.add(row)

    def add_reset(self, on_reset: Callable[[], None]) -> None:
        group = Adw.PreferencesGroup()
        group.add(reset_button(on_reset))
        self.page.add(group)

    def shown(self) -> None:
        """Called each time the page is opened; refresh live state here."""
