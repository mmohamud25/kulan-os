"""Software Update (formerly Update Center): every update in one place, for
Debian, Kulan and apps (Flatpak). Minimal, like a phone's: Automatic Updates
and Update History on top, then one centred status (up to date, a new Kulan
OS version, other updates, installing, restart) with one action and More
Details (the website for a Kulan OS version, a short sheet for the rest).
GNOME Software's own Updates page is turned off, so updates live only here.

Reads /var/lib/kulan/updates.json (kept current by apt's own hooks through
/usr/lib/kulan/update-status) and /var/log/apt/history.log. Changes run
/usr/lib/kulan/update-run through pkexec."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from . import updatelist
from .common import Page, launch, os_release, plural, read_json, restart_computer

STATUS_FILE = Path("/var/lib/kulan/updates.json")
HISTORY_LOG = Path("/var/log/apt/history.log")
RESTORE_CONFIG = Path("/etc/timeshift/timeshift.json")
RESTORE_USAGE = Path("/var/lib/kulan/restore-points.json")  # written by /usr/lib/kulan/restore-points
PREFS_FILE = Path(GLib.get_user_config_dir()) / "kulan" / "updates.ini"
STALE_RUNNING_S = 3 * 3600
# The Kulan OS website: each version's full notes (whats-new/#v2.4), and the
# notes as JSON, read when a new version is offered (the installed notes only
# cover the installed version). Same server as the updates themselves.
SITE = "https://os.kulangroup.com"
NOTES_JSON = f"{SITE}/release-notes.json"


def notes_url(version: str) -> str:
    return f"{SITE}/whats-new/#v{version}"


def shown_version(release: dict) -> str:
    """The version as people see it: os-release's VERSION without "(based on
    Debian 13)", so 2.8a for 2.8.1; VERSION_ID if there's no VERSION."""
    return release.get("VERSION", "").split(" (")[0].strip() or release.get("VERSION_ID", "")
# What a restart finishes, in plain words (package name prefix -> words).
RESTART_WORDS = [
    (("linux-image", "linux-signed"), "the Linux kernel"),
    (("libc6",), "core system libraries"),
    (("systemd", "libsystemd0", "dbus"), "system services"),
    (("gnome-shell", "mutter", "libmutter", "gdm3"), "the desktop"),
    (("nvidia", "libnvidia"), "graphics drivers"),
    (("firmware-", "intel-microcode", "amd64-microcode"), "firmware"),
]


def restart_words(packages: list[str]) -> str:
    words: list[str] = []
    for name in packages:
        for prefixes, text in RESTART_WORDS:
            if name.startswith(prefixes) and text not in words:
                words.append(text)
    if not words:
        return ""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def when_text(stamp: float) -> str:
    if not stamp:
        return "never"
    delta = time.time() - stamp
    if delta < 90:
        return "just now"
    if delta < 3600:
        return plural(int(delta // 60), "minute") + " ago"
    if delta < 86400:
        return plural(int(delta // 3600), "hour") + " ago"
    return datetime.fromtimestamp(stamp).strftime("%-d %b %Y, %H:%M")


def history(limit: int = 8) -> list[dict]:
    """Recent apt runs that installed or upgraded packages."""
    try:
        entries = HISTORY_LOG.read_text(encoding="utf-8", errors="replace").strip().split("\n\n")
    except OSError:
        return []
    runs = []
    for entry in reversed(entries):
        fields = {}
        for line in entry.splitlines():
            key, sep, value = line.partition(": ")
            if sep:
                fields[key] = value
        changed = [fields[k] for k in ("Upgrade", "Install") if k in fields]
        if not changed:
            continue
        names = [p.split(":")[0].split(" ")[0] for chunk in changed for p in chunk.split("), ") if p.strip()]
        # "kulan-base:all (2.3.2026…, 2.4.2026…)": a new Kulan OS version.
        kulan_series = None
        for chunk in changed:
            for part in chunk.split("), "):
                if part.startswith("kulan-base:") and "(" in part:
                    versions = [v.strip(" )") for v in part.split("(", 1)[1].split(",")]
                    old = updatelist.series(versions[0]) if len(versions) > 1 else None
                    new = updatelist.series(versions[-1])
                    if new and (old is None or new > old):
                        kulan_series = f"{new[0]}.{new[1]}"
        try:
            stamp = datetime.strptime(" ".join(fields.get("End-Date", fields.get("Start-Date", "")).split()),
                                      "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            stamp = 0
        runs.append({"time": stamp, "packages": names, "kulan_series": kulan_series,
                     "automatic": "unattended" in fields.get("Commandline", "")})
        if len(runs) >= limit * 6:
            break
    return merge_runs(runs)[:limit]


MERGE_GAP = 15 * 60


def merge_runs(runs: list[dict]) -> list[dict]:
    """One update often takes several apt runs a few minutes apart (automatic
    updates install in steps); they show as one entry. Runs are newest first."""
    merged: list[dict] = []
    for run in runs:
        last = merged[-1] if merged else None
        if (last and run["automatic"] == last["automatic"] and run["time"] and last["earliest"]
                and 0 <= last["earliest"] - run["time"] <= MERGE_GAP):
            last["packages"] = last["packages"] + [p for p in run["packages"] if p not in last["packages"]]
            last["kulan_series"] = last["kulan_series"] or run["kulan_series"]
            last["earliest"] = run["time"]
            continue
        merged.append({**run, "earliest": run["time"]})
    for run in merged:
        del run["earliest"]
    return merged


def auto_updates_on() -> bool:
    try:
        out = subprocess.run(["apt-config", "dump", "APT::Periodic::Unattended-Upgrade"],
                             capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return 'Unattended-Upgrade "1"' in out


class Prefs:
    """Per-user preferences (restore point before installing)."""

    def __init__(self) -> None:
        self.keyfile = GLib.KeyFile()
        try:
            self.keyfile.load_from_file(str(PREFS_FILE), GLib.KeyFileFlags.NONE)
        except GLib.Error:
            pass

    def restore_before_install(self) -> bool:
        try:
            return self.keyfile.get_boolean("updates", "restore-point-before-install")
        except GLib.Error:
            return True

    def set_restore_before_install(self, value: bool) -> None:
        self.keyfile.set_boolean("updates", "restore-point-before-install", value)
        try:
            PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.keyfile.save_to_file(str(PREFS_FILE))
        except (OSError, GLib.Error):
            pass


class UpdatesPage(Page):
    title = "Software Update"
    icon = "software-update-available-symbolic"
    keywords = ("update", "updates", "upgrade", "security", "restore", "restore point", "timeshift", "what's new",
                "automatic updates", "update center", "restart", "apps", "flatpak", "recovery", "history")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.prefs = Prefs()
        self.release = os_release()
        self.status: dict = {}
        self.summary: dict = {}
        self.working = False
        self.restore_on = RESTORE_CONFIG.exists() and shutil.which("timeshift") is not None
        self.remote_notes: list[dict] = []  # every version's notes, from the website (newest first)
        self.notes_fetched = False
        self.dialog_rows: dict[str, Gtk.Widget] = {}  # rows of the open Automatic Updates sheet

        self.page.add(self.build_top_group())
        self.page.add(self.build_status_group())

        self.listen_busy(self.on_busy)
        self.reload_id = 0
        self.monitor_file(str(STATUS_FILE), self.queue_reload)
        self.reload()

    def queue_reload(self) -> None:
        """apt writes the status file several times in a row: reload once it settles."""
        if self.reload_id:
            GLib.source_remove(self.reload_id)
        self.reload_id = GLib.timeout_add(400, self.reload_settled)

    def reload_settled(self) -> bool:
        self.reload_id = 0
        self.reload()
        return GLib.SOURCE_REMOVE

    def teardown(self) -> None:
        if self.reload_id:
            GLib.source_remove(self.reload_id)
            self.reload_id = 0
        super().teardown()

    def shown(self) -> None:
        self.reload()

    # --- layout ------------------------------------------------------------------
    @staticmethod
    def nav_row(title: str) -> tuple[Adw.ActionRow, Gtk.Label]:
        row = Adw.ActionRow(title=title, activatable=True)
        value = Gtk.Label(css_classes=["dim-label"])
        row.add_suffix(value)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        return row, value

    def build_top_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        self.auto_nav, self.auto_value = self.nav_row("Automatic Updates")
        self.auto_nav.connect("activated", lambda _r: self.show_settings())
        group.add(self.auto_nav)
        self.history_nav, self.history_value = self.nav_row("Update History")
        self.history_nav.connect("activated", lambda _r: self.show_history())
        group.add(self.history_nav)
        return group

    def build_status_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=72, margin_bottom=24,
                      halign=Gtk.Align.CENTER)
        self.status_icon = Gtk.Image(pixel_size=64, icon_name="kulan-logo", margin_bottom=8, halign=Gtk.Align.CENTER)
        self.status_title = Gtk.Label(css_classes=["title-2"], wrap=True, justify=Gtk.Justification.CENTER)
        self.status_subtitle = Gtk.Label(css_classes=["title-4", "dim-label"], wrap=True,
                                         justify=Gtk.Justification.CENTER)
        self.status_detail = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER,
                                       max_width_chars=48, visible=False)
        self.progress = Gtk.ProgressBar(show_text=True, visible=False, margin_top=8, width_request=320)
        self.action_button = Gtk.Button(css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER,
                                        margin_top=12, visible=False)
        self.action_button.connect("clicked", lambda _b: self.on_action())
        self.details_button = Gtk.Button(label="More Details", css_classes=["flat", "accent"],
                                         halign=Gtk.Align.CENTER)
        self.details_button.connect("clicked", lambda _b: self.more_details())
        self.check_button = Gtk.Button(css_classes=["flat", "dim-label", "caption"], halign=Gtk.Align.CENTER,
                                       margin_top=18)
        self.check_button.connect("clicked", lambda _b: self.check())
        for widget in (self.status_icon, self.status_title, self.status_subtitle, self.status_detail,
                       self.progress, self.action_button, self.details_button, self.check_button):
            box.append(widget)
        group.add(box)
        return group

    # --- state ---------------------------------------------------------------------
    def reload(self) -> bool:
        self.status = read_json(STATUS_FILE, {})
        if not isinstance(self.status, dict):
            self.status = {}
        self.render_status()
        self.render_top()
        self.render_undo()
        return False

    def running(self) -> bool:
        since = self.status.get("running_since") or 0
        return self.status.get("state") == "running" and time.time() - since < STALE_RUNNING_S

    def available(self) -> list[dict]:
        items = self.status.get("available")
        return [u for u in items if isinstance(u, dict)] if isinstance(items, list) else []

    def paused(self) -> bool:
        until = self.status.get("paused_until") or 0
        return isinstance(until, (int, float)) and until > time.time()

    def render_top(self) -> None:
        self.auto_value.set_label("Paused" if self.paused() else ("On" if auto_updates_on() else "Off"))
        runs = history(limit=1)
        self.history_value.set_label(when_text(runs[0]["time"]) if runs and runs[0]["time"] else "")

    def render_status(self) -> None:
        if self.working:
            return  # the running action owns the status area
        # Read again each time: automatic updates can install a new Kulan OS
        # version while this page is open, and it must say so.
        self.release = os_release()
        available = self.available()
        self.summary = updatelist.summarize(available, self.release.get("VERSION_ID", ""))
        new_version = self.release_offered()
        name = self.release.get("NAME", "Kulan OS")
        current = shown_version(self.release)
        restart = self.restart_needed()
        self.action = ""
        self.status_icon.set_pixel_size(64)
        self.status_detail.set_visible(False)
        self.details_button.set_visible(True)
        self.check_button.set_visible(True)
        self.check_button.set_label(f"Last checked {when_text(self.status.get('checked') or 0)} · Check Now")
        if self.running():
            self.status_icon.set_from_icon_name("view-refresh-symbolic")
            self.status_title.set_label("Installing updates…")
            self.status_subtitle.set_label("You can keep working.")
            self.details_button.set_visible(False)
            self.check_button.set_visible(False)
        elif new_version:
            # A new Kulan OS version: the headline, with its name from the website.
            self.status_icon.set_from_icon_name("kulan-logo")
            self.status_icon.set_pixel_size(88)
            self.status_title.set_label(f"{name} {(self.note_for(new_version) or {}).get('label') or new_version}")
            notes = self.note_for(new_version)
            self.status_subtitle.set_label(notes["title"] if notes and notes.get("title") else "A new version")
            self.action = "install"
        elif available:
            count = self.summary["count"]
            security = self.summary.get("security", [])
            self.status_icon.set_from_icon_name(
                "software-update-urgent-symbolic" if security else "software-update-available-symbolic")
            self.status_title.set_label(f"{plural(count, 'update')} available")
            self.status_subtitle.set_label(self.short_list() or "Improvements and fixes")
            self.action = "install"
        elif restart:
            self.status_icon.set_from_icon_name("system-reboot-symbolic")
            self.status_title.set_label("Restart to finish updating")
            words = restart_words(restart)
            self.status_subtitle.set_label(f"For {words}" if words else f"{name} {current}")
            self.details_button.set_visible(False)
            self.action = "restart"
        else:
            self.status_icon.set_from_icon_name("object-select-symbolic")
            self.status_icon.set_pixel_size(40)
            self.status_icon.set_css_classes(["kulan-update-done"])
            self.status_title.set_label(f"{name} is up to date")
            self.status_subtitle.set_label(f"{name} {current}".strip())
        if self.action != "" or self.running():
            self.status_icon.set_css_classes([])
        labels = {"install": "Update Now" if new_version else "Install Now", "restart": "Restart Now"}
        self.action_button.set_label(labels.get(self.action, ""))
        self.action_button.set_visible(bool(self.action) and not self.running())
        self.action_button.set_sensitive(not self.window.helper.busy)

    def short_list(self) -> str:
        """"Includes security fixes for Firefox and OpenSSL", "LibreOffice and 2 more"."""
        security = [g["title"] for g in self.summary.get("security", [])]
        others = [str(a.get("name", "")) for a in self.summary.get("apps", [])] + \
            [g["title"] for g in self.summary.get("other", [])]

        def join(names: list[str]) -> str:
            if len(names) <= 2:
                return " and ".join(names)
            return f"{names[0]}, {names[1]} and {len(names) - 2} more"
        if security:
            return f"Includes security fixes for {join(security)}"
        return join(others)

    def restart_needed(self) -> list[str]:
        items = self.status.get("restart_required")
        if not isinstance(items, list) or not Path("/run/kulan/restart-required").exists():
            return []  # the file lives in /run, so a restart clears it
        return [str(i) for i in items if isinstance(i, str)]

    def on_action(self) -> None:
        if self.action == "install":
            self.install()
        elif self.action == "restart":
            self.restart()

    def restart(self) -> None:
        restart_computer(lambda: self.toast("Couldn’t restart. Use the menu at the top right: the power button, "
                                            "then Restart"))

    # --- More Details ------------------------------------------------------------------
    def open_url(self, url: str) -> None:
        Gtk.UriLauncher.new(url).launch(self.window, None, None)

    def more_details(self) -> None:
        """A Kulan OS version: its page on the website. Other updates: a short sheet."""
        new_version = self.release_offered()
        if new_version:
            self.open_url(notes_url(new_version))
        elif self.available():
            self.show_update_list()
        else:
            self.open_url(notes_url(self.release.get("VERSION_ID", "")))

    def sheet(self, title: str, page: Adw.PreferencesPage, height: int = 560) -> Adw.Dialog:
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        dialog = Adw.Dialog(title=title, content_width=560, content_height=height, child=view)
        dialog.present(self.window)
        return dialog

    def show_update_list(self) -> None:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        for item in self.summary.get("security", []):
            row = Adw.ActionRow(title=GLib.markup_escape_text(item["title"]), subtitle="Security update")
            row.add_prefix(Gtk.Image(icon_name="security-high-symbolic"))
            group.add(row)
        theme = Gtk.IconTheme.get_for_display(self.window.get_display())
        for app in self.summary.get("apps", []):
            icon = str(app.get("id") or "")
            row = Adw.ActionRow(title=GLib.markup_escape_text(str(app.get("name", "App"))),
                                subtitle=GLib.markup_escape_text(f"Version {app.get('to')}" if app.get("to")
                                                                 else "App update"))
            row.add_prefix(Gtk.Image(icon_name=icon if icon and theme.has_icon(icon) else
                                     "application-x-executable-symbolic", pixel_size=32))
            group.add(row)
        for item in self.summary.get("other", []):
            row = Adw.ActionRow(title=GLib.markup_escape_text(item["title"]), subtitle="Improvements and fixes")
            row.add_prefix(Gtk.Image(icon_name="software-update-available-symbolic"))
            group.add(row)
        page.add(group)
        debs = [u for u in self.available() if u.get("kind") not in ("app", "runtime")]
        if debs:
            tech = Adw.PreferencesGroup()
            details = Adw.ExpanderRow(title="Technical details", subtitle=plural(len(debs), "package"))
            for update in debs[:80]:
                details.add_row(Adw.ActionRow(
                    title=GLib.markup_escape_text(str(update.get("name", "?"))),
                    subtitle=GLib.markup_escape_text(f"{update.get('from') or 'new'} → {update.get('to', '?')}"),
                    css_classes=["property"]))
            tech.add(details)
            page.add(tech)
        self.sheet("Updates", page)

    def release_offered(self) -> str | None:
        """The Kulan OS version these updates bring, if they bring one: a new
        major.minor from the packages themselves, or a point release (2.4.1)
        named by the website's notes when Kulan's packages are updating."""
        if self.summary.get("release"):
            self.fetch_notes()
            return self.summary["release"]
        if not self.summary.get("kulan"):
            return None
        self.fetch_notes()
        current = updatelist.version_tuple(self.release.get("VERSION_ID", ""))
        newest = str(self.remote_notes[0].get("version", "")) if self.remote_notes else ""
        same_series = updatelist.series(newest) == updatelist.series(self.release.get("VERSION_ID", ""))
        if newest and same_series and updatelist.version_tuple(newest) > current:
            return newest
        return None

    def note_for(self, version: str) -> dict | None:
        return next((n for n in self.remote_notes if str(n.get("version")) == version), None)

    def fetch_notes(self) -> None:
        """Every version's notes from the website, once, in the background."""
        if self.notes_fetched:
            return
        self.notes_fetched = True

        def work() -> None:
            import urllib.request  # only when a Kulan update is offered (keeps opening Settings quick)
            try:
                with urllib.request.urlopen(NOTES_JSON, timeout=8) as resp:  # noqa: S310 - fixed https URL
                    data = json.loads(resp.read(512 * 1024).decode("utf-8"))
            except (OSError, ValueError):
                return
            if isinstance(data, list):
                notes = [{"version": str(n.get("version")), "title": str(n.get("title") or ""),
                          "label": str(n.get("label") or "")}
                         for n in data if isinstance(n, dict) and n.get("version")]
                GLib.idle_add(self.notes_arrived, notes)

        threading.Thread(target=work, daemon=True).start()

    def notes_arrived(self, notes: list[dict]) -> bool:
        if not self.torn_down:
            self.remote_notes = notes
            self.render_status()
        return GLib.SOURCE_REMOVE

    # --- Update History ---------------------------------------------------------------
    def show_history(self) -> None:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        runs = history()
        if not runs:
            group.add(Adw.ActionRow(title="No updates installed yet"))
        for run in runs:
            how = " · automatically" if run["automatic"] else ""
            row = Adw.ExpanderRow(
                title=GLib.markup_escape_text(updatelist.history_title(run["packages"], run.get("kulan_series"))),
                subtitle=f"{when_text(run['time'])}{how}")
            for name in run["packages"][:40]:
                row.add_row(Adw.ActionRow(title=GLib.markup_escape_text(name), css_classes=["property"]))
            group.add(row)
        page.add(group)
        notes = Adw.PreferencesGroup()
        link = Adw.ActionRow(title=f"What’s new in {self.release.get('NAME', 'Kulan OS')} "
                                   f"{shown_version(self.release)}".strip(), activatable=True)
        link.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        link.connect("activated", lambda _r: self.open_url(notes_url(self.release.get("VERSION_ID", ""))))
        notes.add(link)
        page.add(notes)
        self.sheet("Update History", page)

    # --- Automatic Updates ----------------------------------------------------------
    def show_settings(self) -> None:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        auto = Adw.SwitchRow(
            title="Install updates automatically",
            subtitle="Security fixes, Kulan and app updates install in the background every day",
            active=auto_updates_on())
        handler = auto.connect("notify::active", self.on_auto_toggled)
        auto.handler = handler
        group.add(auto)
        before = Adw.SwitchRow(
            title="Create a restore point first",
            subtitle="So an update can be undone if it causes trouble",
            active=self.prefs.restore_before_install() and self.restore_on, sensitive=self.restore_on)
        before.connect("notify::active", lambda row, _p: self.prefs.set_restore_before_install(row.get_active()))
        group.add(before)
        page.add(group)

        restore = Adw.PreferencesGroup(title="Restore points")
        points = Adw.ActionRow(
            title="Restore points",
            subtitle="Daily, keeping the last 2 days and 1 week" if self.restore_on
            else "Set up automatically after you install Kulan OS")
        if shutil.which("timeshift"):
            create = Gtk.Button(label="Create Now", valign=Gtk.Align.CENTER, sensitive=self.restore_on)
            create.connect("clicked", lambda _b: self.run(
                ["restore-point"], "Creating a restore point…", "Restore point created"))
            points.add_suffix(create)
            manage = Gtk.Button(label="Manage", valign=Gtk.Align.CENTER, css_classes=["flat"])
            manage.connect("clicked", lambda _b: launch("timeshift-gtk.desktop", self.toast))
            points.add_suffix(manage)
        restore.add(points)
        undo = Adw.ActionRow(title="Undo the last update")
        undo_button = Gtk.Button(label="Undo…", valign=Gtk.Align.CENTER)
        undo_button.update_property([Gtk.AccessibleProperty.LABEL], ["Undo the last update"])
        undo_button.connect("clicked", lambda _b: self.confirm_undo())
        undo.add_suffix(undo_button)
        restore.add(undo)
        pause = Adw.ActionRow(title="Automatic updates are paused", css_classes=["warning"])
        resume = Gtk.Button(label="Resume Now", valign=Gtk.Align.CENTER)
        resume.connect("clicked", lambda _b: self.window.helper.run(
            ["auto-on"], "Automatic updates on", on_done=lambda _ok: self.reload()))
        pause.add_suffix(resume)
        restore.add(pause)
        page.add(restore)

        self.dialog_rows = {"undo": undo, "pause": pause}
        self.render_undo()
        dialog = self.sheet("Automatic Updates", page, height=520)
        dialog.connect("closed", lambda _d: self.dialog_rows.clear())

    def last_update_point(self) -> datetime | None:
        try:
            name = str(read_json(RESTORE_USAGE, {}).get("last_update") or "")
            return datetime.strptime(name, "%Y-%m-%d_%H-%M-%S") if name else None
        except (ValueError, AttributeError):
            return None

    def render_undo(self) -> None:
        undo, pause = self.dialog_rows.get("undo"), self.dialog_rows.get("pause")
        if undo is None or pause is None:
            return
        when = self.last_update_point()
        undo.set_visible(when is not None and self.restore_on)
        if when is not None:
            undo.set_subtitle(
                f"Go back to before the update of {when.strftime('%-d %b, %H:%M')}. "
                "Your files aren’t touched. Kulan OS restarts to finish.")
        paused = self.paused()
        pause.set_visible(paused)
        if paused:
            until = self.status.get("paused_until") or 0
            pause.set_subtitle(
                f"Until {datetime.fromtimestamp(until).strftime('%-d %b, %H:%M')}, "
                "so the undone updates don’t install again")

    def updates_since(self, when: datetime) -> bool:
        """Whether updates were installed after that restore point's update
        (then undoing goes back further than the last update)."""
        last = (self.status.get("last_run") or {}).get("time") or 0
        # The update the restore point was made for ends a while after it.
        return bool(last) and last > when.timestamp() + 6 * 3600

    def confirm_undo(self) -> None:
        when = self.last_update_point()
        if when is None:
            return
        later = (" Updates installed since then, including security fixes, are undone too. They install "
                 "again in three days, or when you turn automatic updates back on.") if self.updates_since(when) else ""
        dialog = Adw.AlertDialog(
            heading="Undo the last update?",
            body=f"Kulan OS goes back to before the update of {when.strftime('%-d %B at %H:%M')} and "
                 f"restarts.{later} Save your work first. Your files aren’t touched, and a restore point "
                 "is made first so you can undo this.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("undo", "Undo and Restart")
        dialog.set_response_appearance("undo", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", lambda _d, r: r == "undo" and self.run(
            ["undo-update"], "Undoing the last update…", None))
        dialog.present(self.window)

    def on_auto_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        wanted = row.get_active()

        def done(ok: bool) -> None:
            if not ok:  # put the switch back if the change didn't happen
                row.handler_block(row.handler)
                row.set_active(not wanted)
                row.handler_unblock(row.handler)
            self.render_top()

        self.window.helper.run(["auto-on" if wanted else "auto-off"],
                               "Automatic updates on" if wanted else "Automatic updates off", on_done=done)

    # --- actions --------------------------------------------------------------------------
    def check(self) -> None:
        self.run(["check"], "Checking for updates…", None)

    def install(self) -> None:
        args = ["install"]
        if self.restore_on and self.prefs.restore_before_install():
            args.append("--restore-point")
        self.run(args, "Installing updates…", "Updates installed")

    def run(self, args: list[str], label: str, success: str | None) -> None:
        if self.window.helper.busy:
            self.toast("Another change is still running")
            return
        self.working = True
        self.progress.set_fraction(0)
        self.progress.set_text(label)
        self.progress.set_visible(True)
        self.status_icon.set_css_classes([])
        self.status_icon.set_from_icon_name("view-refresh-symbolic")
        self.status_title.set_label(label)
        self.status_subtitle.set_label("You can keep working.")
        for widget in (self.action_button, self.details_button, self.check_button):
            widget.set_visible(False)

        def progress(fraction: float, text: str) -> None:
            self.progress.set_fraction(fraction)
            self.progress.set_text(text or f"{round(fraction * 100)}%")

        def done(_ok: bool) -> None:
            self.working = False
            self.progress.set_visible(False)
            self.reload()

        self.window.helper.run(args, success, on_progress=progress, on_done=done)

    def on_busy(self, busy: bool) -> None:
        for widget in (self.check_button, self.action_button):
            widget.set_sensitive(not busy)
