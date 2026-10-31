"""Shared helpers for Kulan Settings pages."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from gi.repository import Adw, Gio, GLib, GObject, Gtk

OS_RELEASE = Path("/usr/lib/os-release")
HELPER = "/usr/lib/kulan/update-run"
KULAN_SCHEMA = "org.kulan.desktop"


# Lines from programs rather than from Kulan: never shown to people as they are.
RAW_LINE = (r"^(E|W|N|Err|Ign|Get|Hit|dpkg|error|warning|fatal)\b|^\S+: |Traceback|^\s+File |GDBus\.Error|"
            r"\[Errno|/usr/|/var/|\bexit (status|code)\b|^[a-z]|^\W|\.\.\.|…$|^\d|"
            r"^(Reading|Building|Setting up|Processing|Unpacking|Preparing|Selecting|Removing|Purging|"
            r"Fetched|Need to get|After this operation|The following|Done|Calculating|Installing|Created|"
            r"Removed|Synchronizing|Generating|Updating|Running|Adding|Scanning|Searching|Found|Skipping)\b")
# Common failures of programs, in plain words with a next step.
KNOWN_FAILURES = [
    ((r"Temporary failure resolving|Could not resolve|Network is unreachable|No route to host|"
      r"Failed to fetch|Unable to fetch|Can't load uri|Could not connect|Connection (timed out|refused)"),
     "Not connected to the internet. Connect to Wi-Fi or a cable, then try again"),
    (r"is not valid yet|is no longer valid|has expired",
     "This computer’s date or time is wrong. Correct it in Kulan Settings › Date and time, then try again"),
    (r"Could not get lock|is held by process|Unable to acquire the dpkg",
     "Another update is running. Try again when it’s finished, in a few minutes"),
    (r"dpkg was interrupted|dpkg --configure -a",
     "An earlier update was interrupted. Restart the computer, then try again"),
    (r"No space left on device|You don't have enough free space",
     "Not enough free space. Free up space in Kulan Settings › Storage, then try again"),
]


def failure_text(lines: list[str], said: str | None = None) -> str:
    """What to tell someone when a change failed: Kulan's own message (said: update-run's
    "say:" line; else the last line that reads like one), else a known failure in plain
    words, else a general one. Never a program's own output."""
    import re
    if said:
        return said.rstrip(".")
    for line in reversed(lines):
        line = line.strip()
        if line and not re.search(RAW_LINE, line):
            return line.rstrip(".")
    text = "\n".join(lines)
    for pattern, message in KNOWN_FAILURES:
        if re.search(pattern, text):
            return message
    return "That didn’t work. Try again, and if it keeps happening, restart the computer"


# apt's own progress words ("Preparing to configure firefox-esr (amd64)"), in plain words.
APT_STEPS = [(("preparing to configure", "configuring", "installed "), "Setting up"),
             (("preparing for removal", "removing", "removed ", "completely removed", "purg"), "Removing"),
             (("running", "trigger"), "Finishing")]


def progress_text(kind: str, package: str, text: str) -> str:
    """A progress line for people: "Downloading updates", "Installing Firefox". Kulan's own
    steps ("Creating a restore point") already are."""
    if kind == "dlstatus":
        return "Downloading"
    lower = text.strip().lower()
    if not lower.startswith(("preparing", "unpacking", "installing", "installed", "configuring", "removing",
                             "removed", "completely removed", "purg", "running", "trigger")):
        return text.strip()
    verb = next((v for keys, v in APT_STEPS if lower.startswith(keys)), "Installing")
    if verb == "Finishing":
        return "Finishing up"
    from .updatelist import friendly_name  # only needed while updating
    name = package.split(":")[0]
    return f"{verb} {friendly_name({'name': name}) if name else 'updates'}"


# A system service's own error, in plain words (the original goes to the log).
PLAIN_ERRORS = [
    (r"(?i)not ?authori[sz]ed|dismissed|permission denied|access ?denied", "Permission wasn’t given"),
    (r"(?i)timed? ?out|timeout|no reply", "It took too long to answer. Try again"),
    (r"(?i)serviceunknown|not provided by any \.service|namehasnoowner|unknownmethod|unknownobject",
     "The part of Kulan OS that does this isn’t running. Restart the computer and try again"),
    (r"(?i)no such file|not found|doesn.t exist", "It isn’t on this computer any more"),
    (r"(?i)network|resolve|unreachable|connection refused", "Can’t reach the internet. Check the connection"),
    (r"(?i)no space|disk full", "The disk is full. Free up some space in Storage"),
    (r"(?i)busy|in progress|locked", "Something else is changing this right now. Try again in a minute"),
]


def plain_error(exc) -> str:
    """What to say about an error (GLib.Error, exception or text): never a program's own words."""
    import re
    text = getattr(exc, "message", None) or str(exc)
    print(f"kulan-settings: {text}", file=sys.stderr)
    for pattern, message in PLAIN_ERRORS:
        if re.search(pattern, text):
            return message
    return "Try again, and if it keeps happening, restart the computer"


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
            toast(f"Couldn’t open it. {plain_error(exc)}")
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


def restart_computer(failed: Callable[[], None]) -> None:
    """GNOME's restart dialog (open apps can save their work first), else systemd directly.
    Never waits on a reply, so the window stays responsive; failed() if neither works."""
    def by_systemd() -> None:
        def got_bus(_src, result) -> None:
            try:
                Gio.bus_get_finish(result).call(
                    "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "Reboot",
                    GLib.Variant("(b)", (True,)), None, Gio.DBusCallFlags.NONE, 60000, None, done)
            except GLib.Error:
                failed()

        def done(bus, result) -> None:
            try:
                bus.call_finish(result)
            except GLib.Error:
                failed()
        Gio.bus_get(Gio.BusType.SYSTEM, None, got_bus)

    def by_session(bus, result) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error:
            by_systemd()

    def got_session(_src, result) -> None:
        try:
            Gio.bus_get_finish(result).call(
                "org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager", "Reboot",
                None, None, Gio.DBusCallFlags.NONE, 60000, None, by_session)
        except GLib.Error:
            by_systemd()
    Gio.bus_get(Gio.BusType.SESSION, None, got_session)


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

# update-run's changes that run as their own system service (kulan-update.service):
# they carry on if Kulan Settings closes, and the window says so when it's closed.
LONG_ACTIONS = {"install": "Updates are installing", "undo-update": "The last update is being undone",
                "repair": "An interrupted update is being finished", "install-devkit": "Software is installing",
                "install-apps": "Software is installing", "addon-install": "An add-on is installing",
                "addon-remove": "An add-on is being removed", "cleanup": "Old system files are being removed"}


class Helper:
    """Runs /usr/lib/kulan/update-run through pkexec and reports progress.

    One action at a time. Progress lines are apt's status lines
    (pmstatus/dlstatus); on_progress gets (fraction, text). While a long change
    runs (LONG_ACTIONS), closing the window asks first and can only hide it: the
    change carries on either way, and Kulan Settings closes once it's done."""

    def __init__(self, toast: Callable[[str], None]) -> None:
        self.toast = toast
        self.busy = False
        self.action: str | None = None  # update-run's action while one runs
        self.hidden = False  # the window was hidden until the change is done
        self.listeners: list[Callable[[bool], None]] = []
        self._hooked: set[int] = set()

    def window(self) -> Gtk.Window | None:
        owner = getattr(self.toast, "__self__", None)  # the window whose toast this is
        if isinstance(owner, Gtk.Window):
            return owner
        app = Gio.Application.get_default()
        return app.get_active_window() if app else None

    def hook_close(self) -> None:
        window = self.window()
        if window is not None and id(window) not in self._hooked:
            self._hooked.add(id(window))
            window.connect("close-request", self.on_close_request)

    def on_close_request(self, window: Gtk.Window) -> bool:
        """Closing while a long change runs: say it carries on, keep the window or hide it."""
        if not self.busy or self.action not in LONG_ACTIONS:
            return False  # close
        dialog = Adw.AlertDialog(heading=LONG_ACTIONS[self.action],
                                 body="Kulan Settings can close when they’re done. They carry on even if you hide "
                                      "it, sign out or close the lid, and the computer stays on until they’re "
                                      "finished.")
        dialog.add_response("keep", "Keep Open")
        dialog.add_response("hide", "Hide")
        dialog.set_default_response("keep")
        dialog.set_close_response("keep")

        def answered(_d, response: str) -> None:
            if response == "hide" and self.busy:
                self.hidden = True
                window.set_visible(False)
        dialog.connect("response", answered)
        dialog.present(window)
        return True  # stay open

    def finished_hidden(self, ok: bool, message: str | None) -> None:
        """The change is done while the window was hidden: say so, then close."""
        self.hidden = False
        app = Gio.Application.get_default()
        window = self.window()
        if app is not None:
            note = Gio.Notification.new(message or ("Done" if ok else "That didn’t work"))
            if not ok:
                note.set_body("Open Kulan Settings › Software Update to try again")
            app.send_notification("kulan-settings-change", note)
        if window is not None and not window.get_visible():
            GLib.timeout_add(500, lambda: bool(window.close()))

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
            self.toast("Kulan Settings can’t ask for your password (pkexec is missing). Add it in Terminal with "
                       "“sudo apt install pkexec”")
            if on_done:
                on_done(False)
            return
        try:
            flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            if stdin is not None:
                flags |= Gio.SubprocessFlags.STDIN_PIPE
            proc = Gio.Subprocess.new(["pkexec", HELPER, *args], flags)
        except GLib.Error as exc:
            print(f"kulan-settings: {exc.message}", file=sys.stderr)
            self.toast("Couldn’t start that change. Try again")
            if on_done:
                on_done(False)
            return
        self.action = args[0] if args else None
        if self.action in LONG_ACTIONS:
            self.hook_close()
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

        said: list[str] = []

        def progress(line: str) -> None:
            if line.startswith("say:"):  # update-run's message for the person (die)
                said.append(line[4:].strip())
                return
            parts = line.split(":", 3)
            if on_progress is None or len(parts) != 4 or parts[0] not in ("pmstatus", "dlstatus"):
                return
            try:
                fraction = max(0.0, min(1.0, float(parts[2]) / 100))
            except ValueError:
                return
            fraction = fraction * 0.3 if parts[0] == "dlstatus" else 0.3 + fraction * 0.7
            on_progress(fraction, progress_text(parts[0], parts[1], parts[3])[:80])

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
            self.action = None
            window = self.window()
            self.hidden = self.hidden and window is not None and not window.get_visible()  # not opened again since
            if self.hidden:  # nobody to see a toast: a notification, then close
                self.finished_hidden(code == 0, success if code == 0 else
                                     failure_text(errors, said[-1] if said else None))
            elif code == 0:
                if success:
                    self.toast(success)
            elif code in (126, 127):  # password dialog dismissed / not authorised
                self.toast("Cancelled")
            else:
                self.failed(failure_text(errors, said[-1] if said else None))
            if on_done:
                on_done(code == 0)

        proc.wait_async(None, exited)

    def failed(self, reason: str) -> None:
        """A short reason as a toast; a longer one in full, in a dialog (a toast cuts it off)."""
        window = Gio.Application.get_default().get_active_window() if Gio.Application.get_default() else None
        if len(reason) <= 70 or window is None:
            self.toast(reason)
            return
        dialog = Adw.AlertDialog(heading="That didn’t work")
        dialog.set_extra_child(Gtk.Label(label=reason, wrap=True, selectable=True, can_focus=False, xalign=0,
                                         max_width_chars=48, css_classes=["body"]))
        dialog.add_response("ok", "OK")
        dialog.present(window)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for listener in list(self.listeners):
            listener(busy)


class Page(Adw.Bin):
    """Base class: a titled preferences page with access to the window."""

    title = ""
    icon = "preferences-system-symbolic"
    keywords: tuple[str, ...] = ()
    show_title = True  # the large title at the top (About has its own)

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.page = Adw.PreferencesPage()
        self.set_child(self.page)
        # The page's name, large, at the top (2.4 look); the header bar above
        # stays empty, as on a Mac.
        if self.title and self.show_title:
            heading = Adw.PreferencesGroup()
            heading.add(Gtk.Label(label=self.title, xalign=0, css_classes=["kulan-page-title"],
                                  wrap=True, accessible_role=Gtk.AccessibleRole.HEADING))
            self.page.add(heading)
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
