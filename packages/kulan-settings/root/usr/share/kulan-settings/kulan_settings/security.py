"""Security: status cards with fixes, compliance mode, the security checkup, password manager."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, launch, package_installed, plural, read_json, settings
from .updates import auto_updates_on, when_text

CHECKUP_FILE = Path("/var/lib/kulan/security-checkup.json")
RESTORE_CONFIG = Path("/etc/timeshift/timeshift.json")
COMPLIANCE = "/usr/bin/kulan-compliance"
TPM = "/usr/bin/kulan-tpm"
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
                "keepassxc", "virus", "antivirus", "compliance", "cis", "nist", "baseline", "audit", "report", "tpm",
                "security chip", "unlock", "disk password")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.screensaver = settings("org.gnome.desktop.screensaver")
        self.session = settings("org.gnome.desktop.session")
        self.dejadup = settings("org.gnome.DejaDup")

        self.status_group = self.add_group("Protection", "Green means on. Orange items have a fix.")
        self.status_rows: list[Gtk.Widget] = []

        self.tpm_group = self.add_group(
            "Start-up",
            "With TPM unlock, the encrypted disk opens by itself on this computer, and the disk password is "
            "only asked if something changed (another computer, Secure Boot turned off).")
        self.tpm_switch = Adw.SwitchRow(title="Unlock with this computer’s security chip", subtitle="Checking…")
        self.tpm_handler = self.tpm_switch.connect("notify::active", self.on_tpm_switch)
        self.tpm_group.add(self.tpm_switch)
        self.tpm_group.set_visible(Path(TPM).exists())

        self.compliance_group = self.add_group(
            "Compliance mode",
            "One security baseline for a work computer, checked against CIS Controls v8 and NIST SP 800-53. "
            "Turning it on fixes what it can and keeps the screen lock from being turned off.")
        self.compliance_switch = Adw.SwitchRow(title="Compliance mode", subtitle="Checking…")
        self.compliance_handler = self.compliance_switch.connect("notify::active", self.on_compliance_switch)
        self.compliance_list = Adw.ExpanderRow(title="Controls", subtitle="")
        self.compliance_list.add_prefix(Gtk.Image(icon_name="view-list-bullet-symbolic"))
        self.compliance_items: list[Gtk.Widget] = []
        report = Adw.ActionRow(title="Report", subtitle="A page to hand to an auditor or a client")
        save = Gtk.Button(label="Save…", valign=Gtk.Align.CENTER)
        save.connect("clicked", lambda _b: self.save_report())
        report.add_suffix(save)
        self.add_rows(self.compliance_group, self.compliance_switch, self.compliance_list, report)
        self.compliance_group.set_visible(Path(COMPLIANCE).exists())

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
        self.load_compliance()
        self.load_tpm()

    # --- TPM unlock -------------------------------------------------------------------
    def load_tpm(self) -> None:
        if not Path(TPM).exists() or is_live():
            self.tpm_group.set_visible(False)
            return
        try:
            proc = Gio.Subprocess.new([TPM, "status", "--json"],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            return

        def done(p, result) -> None:
            if self.torn_down:
                return
            try:
                _ok, stdout, _err = p.communicate_utf8_finish(result)
                data = json.loads(stdout or "")
            except (GLib.Error, ValueError):
                self.tpm_switch.set_subtitle("Couldn’t check")
                return
            on = bool(data.get("on"))
            # Held back until it's ready (kulan-tpm says "available": false);
            # still shown when it's on, so it can be turned off.
            self.tpm_group.set_visible(on or data.get("available", True) is not False)
            with self.tpm_switch.handler_block(self.tpm_handler):
                self.tpm_switch.set_active(on)
            reasons = data.get("reasons") or []
            self.tpm_switch.set_sensitive(on or (bool(data.get("possible")) and not self.window.helper.busy))
            if on:
                self.tpm_switch.set_subtitle("On: the disk opens by itself on this computer")
            elif reasons:
                self.tpm_switch.set_subtitle(GLib.markup_escape_text(reasons[0]))
            else:
                self.tpm_switch.set_subtitle("Off: the disk password is asked at every start")

        proc.communicate_utf8_async(None, None, done)

    def on_tpm_switch(self, row: Adw.SwitchRow, _pspec) -> None:
        turning_on = row.get_active()
        if not turning_on:
            self.tpm_switch.set_subtitle("Turning off…")
            self.window.helper.run(["tpm-off"], "TPM unlock off", on_done=lambda _ok: self.shown(), stdin="")
            return
        dialog = Adw.AlertDialog(heading="Disk password",
                                 body="Type the password you enter at start-up. It's used once to add the "
                                      "security chip's key, and it keeps working whenever the chip says no.")
        entry = Adw.PasswordEntryRow(title="Disk password")
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(entry)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("on", "Turn on")
        dialog.set_response_appearance("on", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("on")
        dialog.set_close_response("cancel")
        entry.connect("entry-activated", lambda _e: dialog.response("on"))

        def answered(_d, response: str) -> None:
            password = entry.get_text()
            if response != "on" or not password:
                with self.tpm_switch.handler_block(self.tpm_handler):
                    self.tpm_switch.set_active(False)
                return
            self.tpm_switch.set_subtitle("Adding the key and rebuilding the start-up image (a few minutes)…")
            self.window.helper.run(["tpm-on"], "TPM unlock on", on_done=lambda _ok: self.shown(), stdin=password)

        dialog.connect("response", answered)
        dialog.present(self.window)

    # --- compliance mode ------------------------------------------------------------
    def load_compliance(self) -> None:
        if not Path(COMPLIANCE).exists():
            return
        try:
            proc = Gio.Subprocess.new([COMPLIANCE, "status", "--json"],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            return

        def done(p, result) -> None:
            if self.torn_down:
                return
            try:
                _ok, stdout, _err = p.communicate_utf8_finish(result)
                data = json.loads(stdout or "")
            except (GLib.Error, ValueError):
                self.compliance_switch.set_subtitle("Couldn’t check")
                return
            self.render_compliance(data)

        proc.communicate_utf8_async(None, None, done)

    def render_compliance(self, data: dict) -> None:
        on = data.get("mode") == "on"
        with self.compliance_switch.handler_block(self.compliance_handler):
            self.compliance_switch.set_active(on)
        passed, total = int(data.get("passed", 0)), int(data.get("total", 0))
        verdict = "meets the baseline" if data.get("compliant") else f"{passed} of {total} controls pass"
        self.compliance_switch.set_subtitle(("On · " if on else "Off · ") + verdict)
        for row in self.compliance_items:
            self.compliance_list.remove(row)
        self.compliance_items = []
        failing = 0
        for control in data.get("controls", []):
            status = control.get("status")
            state = {"pass": GOOD, "fail": WARN}.get(status, INFO)
            failing += status == "fail"
            refs = f"CIS {control.get('cis', '')} · NIST {control.get('nist', '')}"
            row = self.card(f"{control.get('id', '')} {control.get('title', '')}",
                            f"{control.get('detail', '')} · {refs}", state)
            self.compliance_list.add_row(row)
            self.compliance_items.append(row)
        self.compliance_list.set_subtitle("All pass" if not failing else plural(failing, "control") + " to fix")

    def on_compliance_switch(self, row: Adw.SwitchRow, _pspec) -> None:
        turning_on = row.get_active()
        self.compliance_switch.set_subtitle("Applying the baseline…" if turning_on else "Turning off…")
        self.window.helper.run(["compliance-on" if turning_on else "compliance-off"],
                               "Compliance mode on" if turning_on else "Compliance mode off",
                               on_done=lambda _ok: self.shown())

    def save_report(self) -> None:
        dialog = Gtk.FileDialog(title="Save compliance report",
                                initial_name=f"compliance-{GLib.get_host_name()}.html")

        def done(d, result) -> None:
            try:
                picked = d.save_finish(result)
            except GLib.Error:
                return  # cancelled
            if picked is None or picked.get_path() is None:
                return
            ok = subprocess.run([COMPLIANCE, "report", picked.get_path()], capture_output=True,
                                timeout=60, check=False).returncode == 0
            self.toast("Report saved" if ok else "The report couldn’t be saved")

        dialog.save(self.window, None, done)

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
        self.compliance_switch.set_sensitive(not busy)
