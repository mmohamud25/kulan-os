"""Security: status cards with fixes, the security checkup, password manager."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .common import Page, launch, package_installed, plural, read_json, settings
from .updates import auto_updates_on, when_text

CHECKUP_FILE = Path("/var/lib/kulan/security-checkup.json")
RESTORE_CONFIG = Path("/etc/timeshift/timeshift.json")
SECURE_BOOT_VAR = Path("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c")

GOOD, WARN, INFO = "good", "warn", "info"


def run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def firewall_on() -> bool:
    try:
        enabled = "ENABLED=yes" in Path("/etc/ufw/ufw.conf").read_text().split()
    except OSError:
        return False
    return enabled and run(["systemctl", "is-active", "ufw"]) == "active"


def disk_encrypted() -> bool:
    source = run(["findmnt", "-no", "SOURCE", "/"])
    if not source:
        return False
    # The root device or anything below it in the device tree is a LUKS mapping.
    types = run(["lsblk", "-s", "-no", "TYPE", source]).split()
    return "crypt" in types


def secure_boot() -> str:
    """on, off, or none (BIOS or no EFI variables)."""
    if not Path("/sys/firmware/efi").exists():
        return "none"
    try:
        data = SECURE_BOOT_VAR.read_bytes()
    except OSError:
        return "off"
    return "on" if data and data[-1] == 1 else "off"


def is_live() -> bool:
    try:
        return "boot=live" in Path("/proc/cmdline").read_text().split()
    except OSError:
        return False


class SecurityPage(Page):
    title = "Security"
    icon = "security-high-symbolic"
    keywords = ("security", "firewall", "encryption", "secure boot", "lock", "checkup", "lynis", "password manager",
                "keepassxc", "virus", "antivirus")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.screensaver = settings("org.gnome.desktop.screensaver")
        self.session = settings("org.gnome.desktop.session")
        self.dejadup = settings("org.gnome.DejaDup")

        self.status_group = self.add_group("Protection", "Green means on. Orange items have a fix.")
        self.status_rows: list[Gtk.Widget] = []

        self.checkup_group = self.add_group(
            "Security checkup",
            "Looks for weak settings, using the Lynis auditing tool. Takes a minute or two.")
        self.checkup_rows: list[Gtk.Widget] = []
        self.checkup_button = Gtk.Button(label="Run checkup", valign=Gtk.Align.CENTER,
                                         css_classes=["suggested-action"])
        self.checkup_button.connect("clicked", lambda _b: self.run_checkup())
        self.checkup_group.set_header_suffix(self.checkup_button)

        passwords = self.add_group("Passwords")
        self.password_row = Adw.ActionRow(title="Password manager",
                                          subtitle="KeePassXC: an encrypted vault on this computer, with "
                                                   "autofill in Firefox")
        passwords.add(self.password_row)
        self.password_button: Gtk.Button | None = None

        self.listen_busy(self.on_busy)
        self.shown()

    # --- status cards ---------------------------------------------------------
    def card(self, title: str, subtitle: str, state: str, fix_label: str | None = None, fix=None) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(subtitle))
        icon, css = {GOOD: ("object-select-symbolic", "success"), WARN: ("dialog-warning-symbolic", "warning"),
                     INFO: ("dialog-information-symbolic", "dim-label")}[state]
        image = Gtk.Image(icon_name=icon, css_classes=[css])
        image.update_property([Gtk.AccessibleProperty.LABEL],
                              [{GOOD: "On", WARN: "Needs attention", INFO: "Information"}[state]])
        row.add_prefix(image)
        if fix_label and fix:
            button = Gtk.Button(label=fix_label, valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _b: fix())
            button.set_sensitive(not self.window.helper.busy)
            row.add_suffix(button)
        return row

    def shown(self) -> None:
        for row in self.status_rows:
            self.status_group.remove(row)
        self.status_rows = []
        live = is_live()
        cards = []

        if firewall_on():
            cards.append(self.card("Firewall", "On: other computers can’t connect to this one", GOOD))
        else:
            cards.append(self.card("Firewall", "Off: other computers on the network can try to connect", WARN,
                                   "Turn on", lambda: self.helper_run(["firewall-on"], "Firewall on")))

        if live:
            cards.append(self.card("Disk encryption", "Chosen when you install Kulan OS", INFO))
        elif disk_encrypted():
            cards.append(self.card("Disk encryption", "On: your files can’t be read if the disk is taken", GOOD))
        else:
            cards.append(self.card("Disk encryption",
                                   "Off: anyone with the disk can read your files. Encryption can only be "
                                   "chosen when installing", WARN))

        if auto_updates_on():
            cards.append(self.card("Automatic updates", "On: security fixes install every day", GOOD))
        else:
            cards.append(self.card("Automatic updates", "Off: security fixes wait until you install them", WARN,
                                   "Turn on", lambda: self.helper_run(["auto-on"], "Automatic updates on")))

        if live:
            cards.append(self.card("Restore points", "Set up automatically after you install Kulan OS", INFO))
        elif RESTORE_CONFIG.exists():
            cards.append(self.card("Restore points", "On: daily snapshots of the system", GOOD))
        else:
            cards.append(self.card("Restore points", "Not set up yet", WARN, "Set up",
                                   lambda: self.window.show_page("updates")))

        lock_on = self.screensaver is not None and self.screensaver.get_boolean("lock-enabled")
        idle = self.session.get_uint("idle-delay") if self.session is not None else 0
        if lock_on and 0 < idle <= 900:
            cards.append(self.card("Screen lock", f"On: locks after {plural(idle // 60, 'minute')} away", GOOD))
        else:
            cards.append(self.card("Screen lock", "Your screen doesn’t lock by itself", WARN, "Turn on",
                                   self.fix_lock))

        if self.dejadup is not None and (self.dejadup.get_boolean("periodic")
                                         or self.dejadup.get_string("last-backup")):
            cards.append(self.card("Backups", "On: your files are backed up", GOOD))
        elif shutil.which("deja-dup"):
            cards.append(self.card("Backups", "Not set up: restore points cover the system, not your files",
                                   WARN, "Set up", lambda: launch("org.gnome.DejaDup.desktop", self.toast)))

        boot = secure_boot()
        if boot == "on":
            cards.append(self.card("Secure Boot", "On: only trusted software can start the computer", GOOD))
        elif boot == "off":
            cards.append(self.card("Secure Boot", "Off. Turn it on in the computer’s firmware settings "
                                                  "(often F2 or Del at power-on); virtual machines usually "
                                                  "don’t offer it", INFO))
        for row in cards:
            self.status_group.add(row)
            self.status_rows.append(row)

        self.render_checkup()
        self.render_passwords()

    def fix_lock(self) -> None:
        if self.screensaver is not None:
            self.screensaver.set_boolean("lock-enabled", True)
            self.screensaver.reset("lock-delay")
        if self.session is not None:
            self.session.reset("idle-delay")
        self.toast("Screen lock on")
        self.shown()

    # --- checkup ----------------------------------------------------------------
    def render_checkup(self) -> None:
        for row in self.checkup_rows:
            self.checkup_group.remove(row)
        self.checkup_rows = []
        self.checkup_button.set_visible(shutil.which("lynis") is not None and not is_live())
        result = read_json(CHECKUP_FILE, None)
        if not isinstance(result, dict):
            row = Adw.ActionRow(title="Not run yet", subtitle="Run a checkup to see suggestions for this computer")
            self.checkup_group.add(row)
            self.checkup_rows.append(row)
            return
        warnings = [w for w in result.get("warnings", []) if isinstance(w, dict)]
        suggestions = [s for s in result.get("suggestions", []) if isinstance(s, dict)]
        score = result.get("score")
        summary = Adw.ActionRow(
            title=f"Score {score} out of 100" if isinstance(score, int) else "Checkup finished",
            subtitle=f"{plural(len(warnings), 'warning')}, {plural(len(suggestions), 'suggestion')} · "
                     f"{when_text(float(result.get('time') or 0))}")
        summary.add_prefix(Gtk.Image(icon_name="security-high-symbolic"))
        self.checkup_group.add(summary)
        self.checkup_rows.append(summary)
        for title, items, icon in (("Warnings", warnings, "dialog-warning-symbolic"),
                                   ("Suggestions", suggestions, "dialog-information-symbolic")):
            if not items:
                continue
            expander = Adw.ExpanderRow(title=title, subtitle=plural(len(items), "item"))
            expander.add_prefix(Gtk.Image(icon_name=icon))
            for item in items[:40]:
                text = str(item.get("text", "")).strip() or str(item.get("id", ""))
                details = str(item.get("details", "")).strip()
                row = Adw.ActionRow(title=GLib.markup_escape_text(text),
                                    subtitle=GLib.markup_escape_text(
                                        f"{details} · {item.get('id', '')}" if details else str(item.get("id", ""))),
                                    title_lines=3)
                expander.add_row(row)
            self.checkup_group.add(expander)
            self.checkup_rows.append(expander)

    def run_checkup(self) -> None:
        self.checkup_button.set_label("Checking…")
        self.window.helper.run(["security-checkup"], "Security checkup finished",
                               on_done=lambda _ok: (self.checkup_button.set_label("Run checkup"), self.shown()))

    # --- passwords ----------------------------------------------------------------
    def render_passwords(self) -> None:
        if self.password_button is not None:
            self.password_row.remove(self.password_button)
            self.password_button = None
        if package_installed("keepassxc"):
            self.password_button = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
            self.password_button.connect("clicked", lambda _b: launch("org.keepassxc.KeePassXC.desktop", self.toast))
        else:
            self.password_button = Gtk.Button(label="Get it", valign=Gtk.Align.CENTER)
            self.password_button.connect("clicked", lambda _b: launch("org.gnome.Software.desktop", self.toast))
        self.password_row.add_suffix(self.password_button)

    # --- helpers --------------------------------------------------------------------
    def helper_run(self, args: list[str], success: str) -> None:
        self.window.helper.run(args, success, on_done=lambda _ok: self.shown())

    def on_busy(self, busy: bool) -> None:
        self.checkup_button.set_sensitive(not busy)
