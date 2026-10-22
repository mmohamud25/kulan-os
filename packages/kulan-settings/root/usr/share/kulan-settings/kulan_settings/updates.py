"""Software Update (formerly Update Center): every update in one place, for
Debian, Kulan and apps (Flatpak), with progress, a restart prompt when one is
needed, what's new, history, automatic updates and restore points. GNOME
Software's own Updates page is turned off, so updates live only here.

Reads /var/lib/kulan/updates.json (kept current by apt's own hooks through
/usr/lib/kulan/update-status) and /var/log/apt/history.log. Changes run
/usr/lib/kulan/update-run through pkexec."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from . import updatelist
from .common import Page, launch, os_release, plural, read_json

STATUS_FILE = Path("/var/lib/kulan/updates.json")
HISTORY_LOG = Path("/var/log/apt/history.log")
RELEASE_NOTES = Path("/usr/share/kulan/release-notes.json")
RESTORE_CONFIG = Path("/etc/timeshift/timeshift.json")
RESTORE_USAGE = Path("/var/lib/kulan/restore-points.json")  # written by /usr/lib/kulan/restore-points
PREFS_FILE = Path(GLib.get_user_config_dir()) / "kulan" / "updates.ini"
STALE_RUNNING_S = 3 * 3600
# The Kulan OS website: each version's full notes (whats-new/#v2.4), and the
# notes as JSON, read when a new version is offered (the installed notes only
# cover the installed version). Same server as the updates themselves.
SITE = "https://os.kulangroup.com"
NOTES_JSON = f"{SITE}/release-notes.json"
WHATS_NEW_SHOWN = 3


def notes_url(version: str) -> str:
    return f"{SITE}/whats-new/#v{version}"
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
        if len(runs) >= limit:
            break
    return runs


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
                "automatic updates", "update center", "restart", "apps", "flatpak", "recovery")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.prefs = Prefs()
        self.release = os_release()
        self.status: dict = {}
        self.working = False
        self.available_rows: list[Gtk.Widget] = []
        self.history_rows: list[Gtk.Widget] = []
        self.summary: dict = {}
        self.remote_notes: dict[str, dict] = {}  # version -> {"title", "items"} from the website
        self.notes_asked: set[str] = set()

        self.page.add(self.build_status_group())
        self.available_group = self.add_group("Updates")
        self.page.add(self.build_whats_new())
        self.history_group = self.add_group("Recent updates")
        self.page.add(self.build_settings())

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

    # --- status ----------------------------------------------------------------
    def build_status_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=6)
        self.status_icon = Gtk.Image(pixel_size=72, icon_name="kulan-logo")
        self.status_title = Gtk.Label(css_classes=["title-1"], wrap=True, justify=Gtk.Justification.CENTER)
        self.status_detail = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        self.progress = Gtk.ProgressBar(show_text=True, visible=False, margin_start=40, margin_end=40)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=6)
        self.install_button = Gtk.Button(css_classes=["suggested-action", "pill"], visible=False)
        self.install_button.connect("clicked", lambda _b: self.install())
        self.restart_button = Gtk.Button(label="Restart Now", css_classes=["suggested-action", "pill"],
                                         visible=False)
        self.restart_button.connect("clicked", lambda _b: self.restart())
        self.notes_button = Gtk.Button(label="See What’s New", css_classes=["pill"], visible=False)
        self.notes_button.connect("clicked", lambda _b: self.show_release_notes())
        self.check_button = Gtk.Button(label="Check for updates", css_classes=["pill"])
        self.check_button.connect("clicked", lambda _b: self.check())
        buttons.append(self.install_button)
        buttons.append(self.restart_button)
        buttons.append(self.notes_button)
        buttons.append(self.check_button)
        self.status_subtitle = Gtk.Label(css_classes=["title-4"], wrap=True, justify=Gtk.Justification.CENTER,
                                         visible=False)
        for widget in (self.status_icon, self.status_title, self.status_subtitle, self.status_detail, self.progress,
                       buttons):
            box.append(widget)
        group.add(box)
        return group

    def reload(self) -> bool:
        self.status = read_json(STATUS_FILE, {})
        if not isinstance(self.status, dict):
            self.status = {}
        self.render_status()
        self.render_available()
        self.render_history()
        self.render_undo()
        return False

    def running(self) -> bool:
        since = self.status.get("running_since") or 0
        return self.status.get("state") == "running" and time.time() - since < STALE_RUNNING_S

    def available(self) -> list[dict]:
        items = self.status.get("available")
        return [u for u in items if isinstance(u, dict)] if isinstance(items, list) else []

    def render_status(self) -> None:
        if self.working:
            return  # the running action owns the status area
        available = self.available()
        self.summary = updatelist.summarize(available, self.release.get("VERSION_ID", ""))
        new_version = self.summary.get("release")
        security = len(self.summary.get("security", []))
        name = self.release.get("NAME", "Kulan OS")
        checked = when_text(self.status.get("checked") or 0)
        restart = self.restart_needed()
        # One main button at a time: installing comes first, restarting after.
        self.restart_button.set_visible(bool(restart) and not self.running() and not available)
        self.status_subtitle.set_visible(False)
        self.notes_button.set_visible(False)
        self.status_icon.set_pixel_size(72)
        self.check_button.set_css_classes(["pill"])
        if self.running():
            self.status_icon.set_from_icon_name("view-refresh-symbolic")
            self.status_title.set_label("Installing updates…")
            self.status_detail.set_label("Updates are being installed in the background. You can keep working.")
            self.install_button.set_visible(False)
        elif new_version:
            # A new Kulan OS version: the headline, Apple-style.
            self.status_icon.set_from_icon_name("kulan-logo")
            self.status_icon.set_pixel_size(96)
            self.status_title.set_label(f"{name} {new_version}")
            notes = self.remote_notes.get(new_version)
            self.status_subtitle.set_label(notes.get("title", "") if notes else "A new version of Kulan OS")
            self.status_subtitle.set_visible(True)
            more = self.summary["count"] - 1
            also = f"Also {plural(more, 'other update')}" + (
                f", including {plural(security, 'security update')}" if security else "") + ". " if more else ""
            self.status_detail.set_label(f"{also}You’ll be asked to restart when it’s done.")
            self.install_button.set_label("Update Now")
            self.install_button.set_visible(True)
            self.notes_button.set_visible(True)
            self.check_button.set_css_classes(["pill", "flat"])
            self.fetch_notes(new_version)
        elif available:
            count = self.summary["count"]
            self.status_icon.set_from_icon_name(
                "software-update-urgent-symbolic" if security else "software-update-available-symbolic")
            self.status_title.set_label(f"{plural(count, 'update')} available")
            extra = f"Includes {plural(security, 'security update')} · " if security else ""
            words = restart_words([str(u.get("name", "")) for u in available])
            after = f" · Restart afterwards for {words}" if words else ""
            self.status_detail.set_label(f"{extra}Last checked {checked}{after}")
            self.install_button.set_label("Install Now")
            self.install_button.set_visible(True)
        elif restart:
            self.status_icon.set_from_icon_name("system-reboot-symbolic")
            self.status_title.set_label("Restart to finish updating")
            words = restart_words(restart)
            self.status_detail.set_label(f"Updates to {words} take effect after a restart." if words
                                         else "Some updates take effect after a restart.")
            self.install_button.set_visible(False)
        else:
            self.status_icon.set_from_icon_name("kulan-logo")
            self.status_title.set_label(f"{name} is up to date")
            self.status_detail.set_label(f"Last checked {checked}")
            self.install_button.set_visible(False)

    def restart_needed(self) -> list[str]:
        items = self.status.get("restart_required")
        if not isinstance(items, list) or not Path("/run/kulan/restart-required").exists():
            return []  # the file lives in /run, so a restart clears it
        return [str(i) for i in items if isinstance(i, str)]

    def restart(self) -> None:
        # GNOME's own restart dialog, so open apps can save their work first.
        try:
            Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, "org.gnome.SessionManager",
                "/org/gnome/SessionManager", "org.gnome.SessionManager", None).call_sync(
                "Reboot", None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error as exc:
            self.toast(f"Couldn’t restart: {exc.message}")

    def render_available(self) -> None:
        for row in self.available_rows:
            self.available_group.remove(row)
        self.available_rows = []
        available = self.available()
        summary = self.summary or updatelist.summarize(available, self.release.get("VERSION_ID", ""))
        self.available_group.set_visible(bool(available) and not self.running())
        self.available_group.set_title("Also included" if summary.get("release") else "Updates")

        def add(row: Gtk.Widget) -> None:
            self.available_group.add(row)
            self.available_rows.append(row)

        for group in summary.get("security", []):
            row = Adw.ActionRow(title=GLib.markup_escape_text(group["title"]), subtitle="Security update")
            row.add_prefix(Gtk.Image(icon_name="security-high-symbolic", css_classes=["warning"]))
            add(row)
        for app in summary.get("apps", []):
            version = str(app.get("to") or "")
            row = Adw.ActionRow(title=GLib.markup_escape_text(str(app.get("name", "App"))),
                                subtitle=GLib.markup_escape_text(f"New version {version}" if version else "App update"))
            icon = str(app.get("id") or "")
            theme = Gtk.IconTheme.get_for_display(self.window.get_display())
            row.add_prefix(Gtk.Image(icon_name=icon if icon and theme.has_icon(icon) else
                                     "application-x-executable-symbolic", pixel_size=32))
            add(row)
        other = summary.get("other", [])
        if other:
            if len(other) == 1:
                row = Adw.ActionRow(title=GLib.markup_escape_text(other[0]["title"]), subtitle="Improvements and fixes")
                row.add_prefix(Gtk.Image(icon_name="software-update-available-symbolic"))
            else:
                row = Adw.ExpanderRow(title="Other improvements",
                                      subtitle=f"{plural(len(other), 'update')}: fixes and small changes")
                row.add_prefix(Gtk.Image(icon_name="software-update-available-symbolic"))
                for group in other:
                    row.add_row(Adw.ActionRow(title=GLib.markup_escape_text(group["title"])))
            add(row)
        debs = [u for u in available if u.get("kind") not in ("app", "runtime")]
        if debs:
            details = Adw.ExpanderRow(title="Technical details",
                                      subtitle=f"{plural(len(debs), 'package')}, with version numbers")
            details.add_prefix(Gtk.Image(icon_name="text-x-generic-symbolic"))
            for update in debs[:80]:
                details.add_row(Adw.ActionRow(
                    title=GLib.markup_escape_text(str(update.get("name", "?"))),
                    subtitle=GLib.markup_escape_text(f"{update.get('from') or 'new'} → {update.get('to', '?')}"),
                    css_classes=["property"]))
            add(details)

    def fetch_notes(self, version: str) -> None:
        """The new version's notes from the website, once, in the background."""
        if version in self.notes_asked:
            return
        self.notes_asked.add(version)

        def work() -> None:
            try:
                with urllib.request.urlopen(NOTES_JSON, timeout=8) as resp:  # noqa: S310 - fixed https URL
                    data = json.loads(resp.read(512 * 1024).decode("utf-8"))
                note = next((n for n in data if isinstance(n, dict) and n.get("version") == version), None)
            except (OSError, ValueError, TypeError):
                note = None
            if note and isinstance(note.get("items"), list):
                GLib.idle_add(self.notes_arrived, version, note)

        threading.Thread(target=work, daemon=True).start()

    def notes_arrived(self, version: str, note: dict) -> bool:
        if not self.torn_down:
            self.remote_notes[version] = {"title": str(note.get("title") or ""),
                                          "items": [str(i) for i in note["items"]]}
            self.render_status()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def note_row(item: str) -> Adw.ActionRow:
        """A release-notes line: its headline in bold, the details under it."""
        head, rest = updatelist.headline(item)
        row = Adw.ActionRow(title=GLib.markup_escape_text(head), subtitle=GLib.markup_escape_text(rest),
                            subtitle_lines=0, title_lines=0)
        row.add_prefix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["accent"]))
        return row

    def open_url(self, url: str) -> None:
        Gtk.UriLauncher.new(url).launch(self.window, None, None)

    def show_release_notes(self) -> None:
        version = self.summary.get("release")
        if not version:
            return
        notes = self.remote_notes.get(version)
        if not notes:
            self.open_url(notes_url(version))  # offline in-app: the website has them
            return
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(notes["title"] or f"Kulan OS {version}"))
        for item in notes["items"]:
            group.add(self.note_row(item))
        page.add(group)
        more = Adw.PreferencesGroup()
        link = Adw.ActionRow(title="Learn more on the Kulan OS website", activatable=True)
        link.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        link.connect("activated", lambda _r: self.open_url(notes_url(version)))
        more.add(link)
        page.add(more)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        dialog = Adw.Dialog(title=f"What’s New in Kulan OS {version}", content_width=640, content_height=640,
                            child=view)
        dialog.present(self.window)

    # --- what's new and history -------------------------------------------------
    def build_whats_new(self) -> Adw.PreferencesGroup:
        notes = read_json(RELEASE_NOTES, [])
        notes = notes if isinstance(notes, list) else []
        current = self.release.get("VERSION_ID", "")
        note = next((n for n in notes if isinstance(n, dict) and n.get("version") == current),
                    notes[0] if notes and isinstance(notes[0], dict) else None)
        build = self.release.get("BUILD_ID", "")
        group = Adw.PreferencesGroup(
            title=f"What’s new in {note.get('title', 'Kulan OS')}" if note else "What’s new",
            description=f"Build {build}" if build and build != "local" else "",
        )
        items = (note or {}).get("items", [])
        for item in items[:WHATS_NEW_SHOWN]:
            group.add(self.note_row(str(item)))
        if note and note.get("version"):
            more = len(items) - WHATS_NEW_SHOWN
            link = Adw.ActionRow(title="See everything that’s new" if more > 0 else "Learn more",
                                 subtitle=f"{plural(more, 'more change')} on the Kulan OS website" if more > 0
                                 else "On the Kulan OS website", activatable=True)
            link.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
            version = str(note["version"])
            link.connect("activated", lambda _r: self.open_url(notes_url(version)))
            group.add(link)
        return group

    def render_history(self) -> None:
        for row in self.history_rows:
            self.history_group.remove(row)
        self.history_rows = []
        runs = history()
        if not runs:
            row = Adw.ActionRow(title="No updates installed yet")
            self.history_group.add(row)
            self.history_rows.append(row)
            return
        for run in runs:
            how = " · installed automatically" if run["automatic"] else ""
            row = Adw.ExpanderRow(
                title=GLib.markup_escape_text(updatelist.history_title(run["packages"], run.get("kulan_series"))),
                subtitle=f"{when_text(run['time'])}{how}")
            for name in run["packages"][:40]:
                row.add_row(Adw.ActionRow(title=GLib.markup_escape_text(name)))
            self.history_group.add(row)
            self.history_rows.append(row)

    # --- settings ----------------------------------------------------------------------
    def build_settings(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Settings")
        self.auto_row = Adw.SwitchRow(
            title="Install updates automatically",
            subtitle="Debian, Kulan and app updates, including security fixes, install daily in the background",
            active=auto_updates_on())
        self.auto_handler = self.auto_row.connect("notify::active", self.on_auto_toggled)
        group.add(self.auto_row)

        restore_on = RESTORE_CONFIG.exists() and shutil.which("timeshift") is not None
        self.restore_on = restore_on
        self.restore_before_row = Adw.SwitchRow(
            title="Create a restore point before installing",
            subtitle="Lets you roll back if an update causes trouble",
            active=self.prefs.restore_before_install() and restore_on, sensitive=restore_on)
        self.restore_before_row.connect(
            "notify::active", lambda row, _p: self.prefs.set_restore_before_install(row.get_active()))
        group.add(self.restore_before_row)

        restore_row = Adw.ActionRow(
            title="Restore points",
            subtitle="Daily, keeping the last 2 days and 1 week" if restore_on
            else "Set up automatically after you install Kulan OS")
        if shutil.which("timeshift"):
            self.create_button = Gtk.Button(label="Create now", valign=Gtk.Align.CENTER, sensitive=restore_on)
            self.create_button.connect("clicked", lambda _b: self.run(
                ["restore-point"], "Creating a restore point…", "Restore point created"))
            restore_row.add_suffix(self.create_button)
            manage = Gtk.Button(label="Manage", valign=Gtk.Align.CENTER, css_classes=["flat"])
            manage.connect("clicked", lambda _b: launch("timeshift-gtk.desktop", self.toast))
            restore_row.add_suffix(manage)
        group.add(restore_row)

        # Undo: back to the restore point taken before the last update.
        self.undo_row = Adw.ActionRow(title="Undo the last update")
        self.undo_button = Gtk.Button(label="Undo…", valign=Gtk.Align.CENTER)
        self.undo_button.update_property([Gtk.AccessibleProperty.LABEL], ["Undo the last update"])
        self.undo_button.connect("clicked", lambda _b: self.confirm_undo())
        self.undo_row.add_suffix(self.undo_button)
        group.add(self.undo_row)
        self.pause_row = Adw.ActionRow(title="Automatic updates are paused",
                                       css_classes=["warning"])
        resume = Gtk.Button(label="Resume Now", valign=Gtk.Align.CENTER)
        resume.connect("clicked", lambda _b: self.window.helper.run(
            ["auto-on"], "Automatic updates on", on_done=lambda _ok: self.reload()))
        self.pause_row.add_suffix(resume)
        group.add(self.pause_row)
        return group

    def last_update_point(self) -> datetime | None:
        try:
            name = str(read_json(RESTORE_USAGE, {}).get("last_update") or "")
            return datetime.strptime(name, "%Y-%m-%d_%H-%M-%S") if name else None
        except (ValueError, AttributeError):
            return None

    def render_undo(self) -> None:
        when = self.last_update_point()
        self.undo_row.set_visible(when is not None and self.restore_on)
        if when is not None:
            self.undo_row.set_subtitle(
                f"Puts the system back to how it was before the update of {when.strftime('%-d %b, %H:%M')}. "
                "Your own files aren’t touched. Kulan OS restarts to finish.")
        until = self.status.get("paused_until") or 0
        paused = isinstance(until, (int, float)) and until > time.time()
        self.pause_row.set_visible(paused)
        if paused:
            self.pause_row.set_subtitle(
                f"After undoing an update, until {datetime.fromtimestamp(until).strftime('%-d %b, %H:%M')}, "
                "so the same updates don’t install again straight away")

    def confirm_undo(self) -> None:
        when = self.last_update_point()
        if when is None:
            return
        dialog = Adw.AlertDialog(
            heading="Undo the last update?",
            body=f"Kulan OS goes back to how it was before the update of {when.strftime('%-d %B at %H:%M')} and "
                 "restarts. Save your work first. Your documents, photos and other files aren’t touched, and "
                 "a restore point of how things are now is made first, so this can be undone too.")
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
                row.handler_block(self.auto_handler)
                row.set_active(not wanted)
                row.handler_unblock(self.auto_handler)

        self.window.helper.run(["auto-on" if wanted else "auto-off"],
                               "Automatic updates on" if wanted else "Automatic updates off", on_done=done)

    # --- actions --------------------------------------------------------------------------
    def check(self) -> None:
        self.run(["check"], "Checking for updates…", None)

    def install(self) -> None:
        args = ["install"]
        if self.restore_before_row.get_active():
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
        self.status_icon.set_from_icon_name("view-refresh-symbolic")
        self.status_title.set_label(label)
        self.status_detail.set_label("You can keep working while this runs.")

        def progress(fraction: float, text: str) -> None:
            self.progress.set_fraction(fraction)
            self.progress.set_text(text or f"{round(fraction * 100)}%")

        def done(_ok: bool) -> None:
            self.working = False
            self.progress.set_visible(False)
            self.reload()

        self.window.helper.run(args, success, on_progress=progress, on_done=done)

    def on_busy(self, busy: bool) -> None:
        for widget in (self.check_button, self.install_button, self.notes_button):
            widget.set_sensitive(not busy)
        if hasattr(self, "create_button"):
            self.create_button.set_sensitive(not busy and self.restore_on)
