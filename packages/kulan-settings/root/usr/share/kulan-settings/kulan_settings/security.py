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
SIGNED_START = "/usr/bin/kulan-signed-start"


def kulan_tpm_pin_min() -> int:
    return 4  # kulan-tpm's PIN_MIN
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
            "Signed start-up makes sure nobody changed how this computer starts. The security chip unlocks "
            "the encrypted disk, so you type a short PIN at start-up instead of the disk password.")
        self.signed_row = Adw.ActionRow(title="Signed start-up", subtitle="Checking…", subtitle_lines=0)
        self.signed_button = Gtk.Button(valign=Gtk.Align.CENTER, visible=False)
        self.signed_button.connect("clicked", lambda _b: self.on_signed_button())
        self.signed_row.add_suffix(self.signed_button)
        self.signed_row.set_visible(Path(SIGNED_START).exists())
        self.tpm_group.add(self.signed_row)
        self.signed_phase = "off"
        self.tpm_switch = Adw.SwitchRow(title="Unlock with this computer’s security chip", subtitle="Checking…")
        self.tpm_handler = self.tpm_switch.connect("notify::active", self.on_tpm_switch)
        self.tpm_group.add(self.tpm_switch)
        self.tpm_group.set_visible(Path(TPM).exists() or Path(SIGNED_START).exists())

        self.compliance_group = self.add_group(
            "Compliance mode",
            "A security baseline for work computers, based on CIS Controls v8 and NIST SP 800-53. "
            "Turning it on fixes what it can and keeps the screen lock on.")
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
            "Looks for weak settings. Takes a minute or two.")
        self.checkup_rows: list[Gtk.Widget] = []
        self.checkup_button = Gtk.Button(label="Run checkup", valign=Gtk.Align.CENTER,
                                         css_classes=["suggested-action"])
        self.checkup_button.connect("clicked", lambda _b: self.run_checkup())
        self.checkup_group.set_header_suffix(self.checkup_button)

        passwords = self.add_group("Passwords")
        self.password_row = Adw.ActionRow(title="Password manager",
                                          subtitle="KeePassXC, an encrypted vault with autofill in "
                                                   "Firefox")
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
            cards.append(self.card("Firewall", "Other computers can’t connect to this one", GOOD))
        else:
            cards.append(self.card("Firewall", "Off. Other computers on the network can try to connect", WARN,
                                   "Turn on", lambda: self.helper_run(["firewall-on"], "Firewall on")))

        if live:
            cards.append(self.card("Disk encryption", "Chosen when you install Kulan OS", INFO))
        elif disk_encrypted():
            cards.append(self.card("Disk encryption", "Your files can’t be read if the disk is taken", GOOD))
        else:
            cards.append(self.card("Disk encryption",
                                   "Off. Anyone with the disk can read your files. It can only be turned "
                                   "on when installing", WARN))

        if auto_updates_on():
            cards.append(self.card("Automatic updates", "Security fixes install every day", GOOD))
        else:
            cards.append(self.card("Automatic updates", "Off. Security fixes wait until you install them", WARN,
                                   "Turn on", lambda: self.helper_run(["auto-on"], "Automatic updates on")))

        if live:
            cards.append(self.card("Restore points", "Set up automatically after you install Kulan OS", INFO))
        elif RESTORE_CONFIG.exists():
            cards.append(self.card("Restore points", "The system is saved every day", GOOD))
        else:
            cards.append(self.card("Restore points", "Not set up yet", WARN, "Set up",
                                   lambda: self.window.show_page("updates")))

        lock_on = self.screensaver is not None and self.screensaver.get_boolean("lock-enabled")
        idle = self.session.get_uint("idle-delay") if self.session is not None else 0
        if lock_on and 0 < idle <= 900:
            cards.append(self.card("Screen lock", f"Locks after {plural(idle // 60, 'minute')} away", GOOD))
        else:
            cards.append(self.card("Screen lock", "Your screen doesn’t lock when you’re away", WARN, "Turn on",
                                   self.fix_lock))

        if self.dejadup is not None and (self.dejadup.get_boolean("periodic")
                                         or self.dejadup.get_string("last-backup")):
            cards.append(self.card("Backups", "Your files are backed up", GOOD))
        elif shutil.which("deja-dup"):
            cards.append(self.card("Backups", "Not set up. Restore points don’t include your files",
                                   WARN, "Set up", lambda: self.window.show_page("backups")))

        boot = secure_boot()
        if boot == "on":
            cards.append(self.card("Secure Boot", "Only trusted software can start the computer", GOOD))
        elif boot == "off":
            cards.append(self.card("Secure Boot", "Off. Turn it on in the computer’s firmware settings", INFO))
        for row in cards:
            self.status_group.add(row)
            self.status_rows.append(row)

        self.render_checkup()
        self.render_passwords()
        self.load_compliance()
        self.load_tpm()
        self.load_signed_start()

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
            if on and data.get("pin"):
                self.tpm_switch.set_subtitle("On. Type your PIN at start-up instead of the disk password")
            elif on:
                self.tpm_switch.set_subtitle("On, without a PIN. Protects the disk if it’s removed, but not the "
                                             "whole computer. Turn off and on to add a PIN")
            elif reasons:
                self.tpm_switch.set_subtitle(GLib.markup_escape_text(reasons[0]))
            else:
                self.tpm_switch.set_subtitle("Off. The disk password is needed at every start")

        proc.communicate_utf8_async(None, None, done)

    # --- signed start-up -------------------------------------------------------------
    def load_signed_start(self) -> None:
        if not Path(SIGNED_START).exists() or is_live():
            self.signed_row.set_visible(False)
            return
        try:
            proc = Gio.Subprocess.new([SIGNED_START, "status", "--json"],
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
                self.signed_row.set_subtitle("Couldn’t check")
                return
            self.render_signed_start(data)

        proc.communicate_utf8_async(None, None, done)

    def render_signed_start(self, data: dict) -> None:
        phase = data.get("phase") or "off"
        self.signed_phase = phase
        code = str(data.get("code") or "")
        problem = data.get("problem")
        reasons = data.get("reasons") or []
        texts = {
            "on": "On. This computer starts only with start-up files signed with its own key",
            "switch": "Almost done. The next start goes through the signed start-up files",
            "enroll": (f"At the next restart, a blue screen asks to enroll this computer’s key: press any key, "
                       f"choose Enroll MOK, Continue, Yes, type {code} and choose Reboot"),
        }
        text = texts.get(phase) or (reasons[0] if reasons else
                                    "Off. Someone alone with this computer could change how it starts, to record "
                                    "the disk password")
        if phase != "off" and not data.get("secure_boot"):
            text += ". Secure Boot is off in the firmware settings: turn it on for this to protect anything"
        if problem:
            text += f". {problem}"
        self.signed_row.set_subtitle(GLib.markup_escape_text(text))
        busy = self.window.helper.busy
        if phase == "off":
            self.signed_button.set_label("Turn On…")
            self.signed_button.set_visible(True)
            self.signed_button.set_sensitive(bool(data.get("possible")) and not busy)
        elif phase == "on":
            self.signed_button.set_visible(False)
        else:
            self.signed_button.set_label("Restart")
            self.signed_button.set_visible(True)
            self.signed_button.set_sensitive(not busy)

    def on_signed_button(self) -> Adw.AlertDialog | None:
        if self.signed_phase in ("enroll", "switch"):
            try:
                Gio.DBus.system.call_sync("org.freedesktop.login1", "/org/freedesktop/login1",
                                          "org.freedesktop.login1.Manager", "Reboot", GLib.Variant("(b)", (True,)),
                                          None, Gio.DBusCallFlags.NONE, 10000, None)
            except GLib.Error:
                self.toast("Couldn’t restart. Use the menu at the top right")
            return None
        dialog = Adw.AlertDialog(heading="Turn on signed start-up?", body=(
            "This computer gets its own start-up key, and its start-up files are signed with it, so nobody can "
            "change how it starts without you knowing. At the next restart, a blue screen asks once to enroll "
            "the key, with a code shown here. It takes a few minutes."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("on", "Turn On")
        dialog.set_response_appearance("on", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def answered(_d, response: str) -> None:
            if response != "on":
                return
            self.signed_row.set_subtitle("Signing the start-up files. This takes a few minutes…")
            self.signed_button.set_sensitive(False)
            self.window.helper.run(["signed-start-on"], "Signed start-up: restart to finish",
                                   on_done=lambda _ok: self.shown())
        dialog.connect("response", answered)
        dialog.present(self.window)
        return dialog

    def on_tpm_switch(self, row: Adw.SwitchRow, _pspec) -> None:
        turning_on = row.get_active()
        if not turning_on:
            self.tpm_switch.set_subtitle("Turning off…")
            self.window.helper.run(["tpm-off"], "TPM unlock off", on_done=lambda _ok: self.shown(), stdin="")
            return
        dialog = Adw.AlertDialog(heading="Turn on TPM unlock",
                                 body="Enter the disk password you use at start-up. It still works if the chip "
                                      "refuses.")
        password = Adw.PasswordEntryRow(title="Disk password")
        use_pin = Adw.SwitchRow(title="Ask for a PIN at start-up", active=True,
                                subtitle="Recommended. Without one, someone with this computer could get "
                                         "in using a USB stick")
        pin = Adw.PasswordEntryRow(title=f"New PIN, at least {kulan_tpm_pin_min()} characters")
        again = Adw.PasswordEntryRow(title="PIN again")
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for row in (password, use_pin, pin, again):
            box.append(row)
        hint = Gtk.Label(wrap=True, xalign=0, css_classes=["caption", "dim-label"], margin_top=8)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        extra.append(box)
        extra.append(hint)
        dialog.set_extra_child(extra)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("on", "Turn On")
        dialog.set_response_appearance("on", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("on")
        dialog.set_close_response("cancel")

        def problem() -> str:
            if not password.get_text():
                return "Enter the disk password."
            if use_pin.get_active():
                text = pin.get_text()
                if len(text) < kulan_tpm_pin_min():
                    return f"The PIN needs at least {kulan_tpm_pin_min()} characters."
                if text != text.strip() or not text.isprintable() or len(text) > 64:
                    return "The PIN can’t start or end with a space."
                if again.get_text() != text:
                    return "The two PINs aren’t the same."
            return ""

        def update(*_args) -> None:
            pin.set_visible(use_pin.get_active())
            again.set_visible(use_pin.get_active())
            issue = problem()
            dialog.set_response_enabled("on", not issue)
            hint.set_label(issue if (password.get_text() or pin.get_text()) else
                           "Keep the PIN safe. If you forget it, use the disk password.")

        for row in (password, pin, again):
            row.connect("changed", update)
            row.connect("entry-activated", lambda _e: not problem() and dialog.response("on"))
        use_pin.connect("notify::active", update)
        update()

        def answered(_d, response: str) -> None:
            if response != "on" or problem():
                with self.tpm_switch.handler_block(self.tpm_handler):
                    self.tpm_switch.set_active(False)
                return
            self.tpm_switch.set_subtitle("Setting up TPM unlock. This takes a few minutes…")
            if use_pin.get_active():
                self.window.helper.run(["tpm-on-pin"], "TPM unlock on, with a PIN", on_done=lambda _ok: self.shown(),
                                       stdin=f"{password.get_text()}\n{pin.get_text()}")
            else:
                self.window.helper.run(["tpm-on"], "TPM unlock on", on_done=lambda _ok: self.shown(),
                                       stdin=password.get_text())

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
