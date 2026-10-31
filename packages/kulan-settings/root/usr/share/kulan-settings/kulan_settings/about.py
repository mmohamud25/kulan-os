"""About Kulan OS: version, build, this computer, links."""
from __future__ import annotations

import base64
import getpass
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .common import Page, failure_text, in_thread, os_release

KULAN_CONF = Path("/usr/share/kulan/kulan.conf")


def kulan_conf() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in KULAN_CONF.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().startswith("KULAN_"):
                values[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return values


def processor() -> str:
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5, check=False,
                             env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    fields: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.strip(), value.strip())
    name = fields.get("Model name", "")
    vendor = fields.get("Vendor ID", "")
    if name in ("", "-"):
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text()
            name = next((line.split(":", 1)[1].strip() for line in cpuinfo.splitlines()
                         if line.lower().startswith(("model name", "hardware"))), "")
        except OSError:
            name = ""
    if not name:
        name = "Apple silicon" if vendor == "Apple" else (vendor or os.uname().machine)
    cores = os.cpu_count() or 0
    return f"{name} × {cores}" if cores else name


def memory() -> str:
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        if total > 0:
            return f"{total / 1024 ** 3:.1f} GB"
    except (OSError, ValueError):
        pass
    return "Unknown"


def storage() -> str:
    try:
        info = Gio.File.new_for_path("/").query_filesystem_info("filesystem::size,filesystem::free", None)
        size = info.get_attribute_uint64("filesystem::size")
        free = info.get_attribute_uint64("filesystem::free")
        return f"{GLib.format_size(free)} free of {GLib.format_size(size)}"
    except GLib.Error:
        return "Unknown"


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return ""


def hardware_model() -> str:
    """"LENOVO ThinkPad X1 Carbon", from the firmware's own tables."""
    vendor = read_text("/sys/class/dmi/id/sys_vendor")
    product = read_text("/sys/class/dmi/id/product_version") if read_text(
        "/sys/class/dmi/id/sys_vendor").upper() == "LENOVO" else ""
    product = product or read_text("/sys/class/dmi/id/product_name")
    if not product:
        product = read_text("/proc/device-tree/model").rstrip("\x00")
    junk = ("", "to be filled by o.e.m.", "system product name", "default string", "none")
    if product.lower() in junk:
        return ""
    return product if not vendor or vendor.lower() in junk or product.lower().startswith(vendor.lower()) \
        else f"{vendor} {product}"


def graphics() -> str:
    """The graphics chips (lspci), e.g. "Intel UHD Graphics 620"."""
    try:
        out = subprocess.run(["lspci", "-mm"], capture_output=True, text=True, timeout=5, check=False,
                             env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    names = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in re.findall(r'"[^"]*"', line)]
        if len(parts) >= 3 and parts[0].startswith(("VGA", "3D", "Display")):
            vendor = "AMD" if "AMD" in parts[1] else re.sub(r"\s*\[.*?\]|,? Inc\.?|Corporation|Corp\.", "",
                                                            parts[1]).strip()
            model = re.sub(r".*\[(.*)\].*", r"\1", parts[2]) if "[" in parts[2] else parts[2]
            names.append(f"{vendor} {model}".strip())
    return " · ".join(dict.fromkeys(names))


# Kulan's own (EGL, off-screen), then GNOME Settings' (older GNOME versions).
RENDERER_HELPERS = ("/usr/lib/kulan/graphics-renderer", "/usr/libexec/gnome-control-center-print-renderer")
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "software rasterizer", "lavapipe")


def acceleration(helper: str | None = None) -> tuple[bool | None, str]:
    """Whether the screen is drawn by the graphics chip (True) or by the
    processor (False), and the renderer's name; (None, "") if unknown."""
    name = ""
    for path in ([helper] if helper else RENDERER_HELPERS):
        try:
            out = subprocess.run([path], capture_output=True, text=True, timeout=10, check=False,
                                 env={**os.environ, "LC_ALL": "C"}).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        name = out.splitlines()[0].strip() if out else ""
        if name:
            break
    if not name:
        return None, ""
    return not any(s in name.lower() for s in SOFTWARE_RENDERERS), name


def acceleration_text(on: bool | None, name: str) -> str:
    if on is None:
        return ""
    if on:
        return f"On ({name})"
    return (f"Off, using {name}. Animations can be slow. In a virtual machine, turn on 3D acceleration "
            "in its display settings. Otherwise a graphics driver may be missing.")


def firmware() -> str:
    version = read_text("/sys/class/dmi/id/bios_version")
    date = read_text("/sys/class/dmi/id/bios_date")
    kind = "UEFI" if os.path.isdir("/sys/firmware/efi") else "BIOS"
    return f"{kind} {version}" + (f" ({date})" if date else "") if version else kind


def desktop_version() -> str:
    try:
        out = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True, timeout=5,
                             check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    session = os.environ.get("XDG_SESSION_TYPE", "")
    session = {"wayland": "Wayland", "x11": "X11"}.get(session, session)
    return ", ".join(x for x in (out, session) if x)


SLOW_DETAILS: dict[str, str] | None = None  # found once per run; the hardware doesn't change


def slow_details() -> dict[str, str]:
    """The details that need a program to find out. On a worker thread, never the window's."""
    global SLOW_DETAILS
    if SLOW_DETAILS is None:
        SLOW_DETAILS = {"Processor": processor(), "Graphics": graphics(),
                        "3D acceleration": acceleration_text(*acceleration()), "Desktop": desktop_version()}
    return SLOW_DETAILS


class AboutPage(Page):
    title = "About Kulan OS"
    icon = "help-about-symbolic"
    show_title = False  # the logo and "Kulan OS" are the heading
    keywords = ("about", "version", "build", "system", "computer", "memory", "processor", "storage",
                "organisation", "organization", "managed", "fleet", "company", "work")

    def __init__(self, window) -> None:
        super().__init__(window)
        release = os_release()
        conf = kulan_conf()

        hero = self.add_group()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12)
        box.append(Gtk.Image(icon_name=release.get("LOGO", "kulan-logo"), pixel_size=112))
        box.append(Gtk.Label(label=release.get("NAME", "Kulan OS"), css_classes=["title-1"]))
        # As people see it (2.8a), not the plain number (2.8.1) apt uses.
        version = release.get("VERSION", "").split(" (")[0].strip() or release.get("VERSION_ID", "")
        build = release.get("BUILD_ID", "")
        line = f"Version {version}" + (f" · Build {build}" if build and build != "local" else "")
        box.append(Gtk.Label(label=line, css_classes=["dim-label"]))
        hero.add(box)

        details = self.add_group("This computer")
        uname = os.uname()
        # What needs a program to find out (lscpu, lspci, the renderer, gnome-shell)
        # fills in from a thread, so About opens at once, also from the top bar.
        slow = SLOW_DETAILS or {}
        self.slow_rows: dict[str, Adw.ActionRow] = {}
        for title, value in (
                ("Computer name", GLib.get_host_name()),
                ("Processor", slow.get("Processor", None)),
                ("Memory", memory()),
                ("Storage", storage()),
                ("Based on", f"Debian {release.get('VERSION', '').split('Debian ')[-1].rstrip(')') or 'GNU/Linux'}"),
                ("Kernel", f"Linux {uname.release} ({uname.machine})"),
                ("Hardware model", hardware_model()),
                ("Graphics", slow.get("Graphics", None)),
                ("3D acceleration", slow.get("3D acceleration", None)),
                ("Firmware", firmware()),
                ("Desktop", slow.get("Desktop", None))):
            if value == "":
                continue
            row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(value or "…"),
                                subtitle_selectable=True)
            row.add_css_class("property")
            details.add(row)
            if value is None:
                self.slow_rows[title] = row
        if self.slow_rows:
            in_thread(slow_details, self.got_slow_details, page=self)

        self.org_group = self.add_group("Organisation")
        self.org_rows: list[Gtk.Widget] = []
        self.render_org()

        links = self.add_group()
        for title, action in (
                ("Check for updates", lambda: self.window.show_page("updates")),
                ("Kulan OS website", lambda: self.open_url(conf.get("KULAN_HOME_URL"))),
                ("Get help", lambda: self.window.show_page("help"))):
            row = Adw.ActionRow(title=title, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, a=action: a())
            links.add(row)

    def got_slow_details(self, values: dict[str, str] | None) -> None:
        for title, row in self.slow_rows.items():
            value = (values or {}).get(title, "")
            if value:
                row.set_subtitle(GLib.markup_escape_text(value))
            else:
                row.set_visible(False)

    # --- fleet management -------------------------------------------------------------
    def render_org(self) -> None:
        for row in self.org_rows:
            self.org_group.remove(row)
        self.org_rows = []
        fleet = Path("/usr/bin/kulan-fleet")
        self.org_group.set_visible(fleet.exists())
        if not fleet.exists():
            return
        try:
            data = json.loads(Path("/var/lib/kulan/fleet-public.json").read_text())
        except (OSError, ValueError):
            data = {}
        if data.get("joined"):
            last = data.get("last_report")
            when = time.strftime("%d %b %Y, %H:%M", time.localtime(last)) if last else "not yet"
            head = Adw.ActionRow(title=GLib.markup_escape_text(f"Managed by {data.get('organisation', '')}"),
                                 subtitle=GLib.markup_escape_text(
                                     ("Waiting for the organisation's Owner to approve this computer · "
                                      if data.get("pending") else "")
                                     + f"Reports to {data.get('server', '')} every hour · last report {when}"
                                     + (f" · last problem: {data['last_error']}" if data.get("last_error") else "")),
                                 subtitle_lines=0)
            head.add_prefix(Gtk.Image(icon_name="network-workgroup-symbolic"))
            leave = Gtk.Button(label="Leave…", valign=Gtk.Align.CENTER)
            leave.connect("clicked", lambda _b: self.leave_org())
            head.add_suffix(leave)
            shared = Adw.ExpanderRow(title="What is shared",
                                     subtitle="Never files, browsing, messages or passwords")
            for item in data.get("shared", []):
                shared.add_row(Adw.ActionRow(title=GLib.markup_escape_text(str(item))))
            shared.add_row(Adw.ActionRow(
                title="What the organisation can ask this computer to do",
                subtitle="Report now, install updates, turn on compliance mode, lock the screen, "
                         "lock it if it’s lost"
                         + (", erase it" if data.get("allow_erase") else "")
                         + ", and restart or shut down with 5 minutes’ notice. It can run Kulan’s fixes"
                         + (" and its own scripts" if self.scripts_allowed() else "")
                         + ", show you messages, add Wi-Fi networks and printers, and ask to see your screen, "
                           "which only happens if you allow it. It manages the accounts it created. Other "
                           "accounts on this computer are never touched",
                subtitle_lines=0))
            rows = [head, shared]
            policy = self.fleet_policy()
            if policy:
                rows.append(policy)
            mine = self.my_two_step()
            if mine is not None:
                rows.append(self.two_step_row(mine))
            work = self.work_email_row()
            if work is not None:
                rows.append(work)
            erase = Adw.SwitchRow(title="Allow erasing this computer if it’s lost or stolen",
                                  subtitle="Your organisation can then remotely make the disk unreadable, after "
                                           "a 20-second notice on the screen. Needs an encrypted "
                                           "disk", subtitle_lines=0,
                                  active=bool(data.get("allow_erase")))
            erase.connect("notify::active", self.on_allow_erase)
            self.erase_row = erase
            # Only an encrypted disk can be erased from afar (its keys are destroyed).
            self.when_encrypted(lambda on, row=erase: self.erase_needs_encryption(row, on))
            rows.append(erase)
        else:
            # Why joining from an organisation's USB stick stopped (kulan-fleet-autojoin).
            try:
                problem = Path("/var/lib/kulan/fleet-join-problem").read_text().strip()
            except OSError:
                problem = ""
            join = Adw.ActionRow(title="Join an organisation", subtitle_lines=0,
                                 subtitle=problem or "For work or school computers. Your IT team gives you a "
                                                     "join code", activatable=True)
            join.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            join.connect("activated", lambda _r: self.join_org())
            rows = [join]
        for row in rows:
            self.org_group.add(row)
            self.org_rows.append(row)

    @staticmethod
    def scripts_allowed() -> bool:
        """Whether the organisation's Owner allows its own scripts on this computer."""
        try:
            return json.loads(Path("/var/lib/kulan/fleet-policy.json").read_text()).get("policy", {}).get("scripts") is True
        except (OSError, ValueError, AttributeError):
            return False

    @staticmethod
    def fleet_policy() -> Adw.ExpanderRow | None:
        """What the organisation's policy asks of this computer, and whether it's met."""
        try:
            data = json.loads(Path("/var/lib/kulan/fleet-policy.json").read_text())
        except (OSError, ValueError):
            return None
        policy, status = data.get("policy") or {}, data.get("status") or {}
        if not policy:
            return None
        words = {"auto_updates": "Automatic updates on", "firewall": "Firewall on",
                 "require_encryption": "Disk encryption"}
        # (title, status, what to say when it isn't met)
        items = [(words[k], status.get(k), "Not met. Kulan OS must be installed again with "
                  "encryption on" if k == "require_encryption" else "Not met yet") for k in words if policy.get(k)]
        if policy.get("screen_lock_minutes"):
            items.append((f"Screen locks after {policy['screen_lock_minutes']} minutes",
                          status.get("screen_lock_minutes"), "Not met yet"))
        if policy.get("usb") == "block":
            items.append(("USB drives don’t work", status.get("usb"),
                          "Not met yet. Keyboards, mice and other USB devices still work"))
        elif policy.get("usb") == "approved":
            items.append(("Only USB drives your organisation approved work", status.get("usb"),
                          "Not met yet. Keyboards, mice and other USB devices still work"))
        for kind, verb in (("required_apps", "installed"), ("blocked_apps", "not allowed")):
            for app in policy.get(kind) or []:
                if not isinstance(app, dict) or not app.get("id"):
                    continue
                key = f"app:{'install' if kind == 'required_apps' else 'block'}:{app.get('source')}:{app['id']}"
                state = status.get(key)
                items.append((f"{app['id']} {verb}", state,
                              (state[:1].upper() + state[1:]) if isinstance(state, str) and state else "Not met yet"))
        if not items:  # only what isn't shown here, scripts say
            return None
        missing = sum(1 for _t, s, _w in items if s != "met")
        row = Adw.ExpanderRow(title="Your organisation’s policy",
                              subtitle="All met" if not missing else
                              f"{missing} not met. Your organisation is told")
        for title, state, why in items:
            item = Adw.ActionRow(title=title, subtitle="Met" if state == "met" else why, subtitle_lines=0)
            item.add_prefix(Gtk.Image(icon_name="object-select-symbolic" if state == "met" else
                                      "dialog-warning-symbolic"))
            row.add_row(item)
        return row

    # --- Work email (Kulan Fleet 2.8e) ------------------------------------------------------
    WORKMAIL = Path("/usr/share/kulan-fleet/workmail.py")

    @classmethod
    def workmail(cls):
        """kulan-fleet's work email module (in kulan-base), or None."""
        try:
            spec = importlib.util.spec_from_file_location("kulan_fleet_workmail", cls.WORKMAIL)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except (OSError, ImportError, AttributeError, SyntaxError):
            return None

    def work_email_row(self) -> Adw.ActionRow | None:
        """The work mailbox the organisation made for this account, and whether it's connected."""
        wm = self.workmail()
        mail = wm.mine(getpass.getuser()) if wm else None
        if not mail:
            return None
        done = wm.connected(mail, Path.home())
        row = Adw.ActionRow(title="Work email", subtitle_lines=0, subtitle=GLib.markup_escape_text(
            f"{mail['address']} · Connected to mail, calendar, contacts and Google Drive" if done else
            f"{mail['address']} · Not connected. Click Connect and sign in as {mail['address']}"))
        row.add_prefix(Gtk.Image(icon_name="mail-unread-symbolic" if done else "dialog-information-symbolic"))
        if done:
            command = wm.open_command(mail) if hasattr(wm, "open_command") else ["geary"]
            button = Gtk.Button(label="Open mail" if command == ["geary"] else "Accounts", valign=Gtk.Align.CENTER)
        else:
            command = wm.connect_command(mail)
            button = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        button.connect("clicked", lambda _b: self.run_app(command))
        row.add_suffix(button)
        return row

    def run_app(self, argv: list[str]) -> None:
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            print(f"kulan-settings: {argv[0]}: {e}", file=sys.stderr)
            self.window.toast("That app isn’t installed")

    # --- Two-step sign-in (Kulan Fleet 2.8d) ------------------------------------------------
    TWO_STEP_PUBLIC = Path("/var/lib/kulan/fleet-two-step-public.json")
    TWO_STEP_HELPER = "/usr/lib/kulan/fleet-two-step"
    TWO_STEP_ORIGIN = "pam://kulan-fleet"

    @classmethod
    def my_two_step(cls) -> dict | None:
        """This account's two-step sign-in, if the organisation manages it (else None)."""
        try:
            mine = json.loads(cls.TWO_STEP_PUBLIC.read_text()).get(getpass.getuser())
        except (OSError, ValueError, AttributeError):
            return None
        return mine if isinstance(mine, dict) else None

    def two_step_row(self, mine: dict) -> Adw.ActionRow:
        factors = mine.get("factors") or []
        subtitle = (f"On · {', '.join(f.get('label') or ('Security key' if f.get('kind') == 'key' else 'Phone code') for f in factors)}"
                    if factors else "Your organisation asks you to set it up" if mine.get("required") else
                    "Off. Add a security key or phone code to protect your account")
        row = Adw.ActionRow(title="Two-step sign-in", subtitle=GLib.markup_escape_text(subtitle), activatable=True,
                            subtitle_lines=0)
        row.add_prefix(Gtk.Image(icon_name="security-high-symbolic" if factors else "dialog-warning-symbolic"
                                 if mine.get("required") else "security-medium-symbolic"))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r: self.open_two_step())
        return row

    def two_step_call(self, args: list[str], stdin: str, done) -> None:
        """The helper, as this person (pkexec asks their own password), in the background."""
        def work() -> None:
            try:
                r = subprocess.run(["pkexec", self.TWO_STEP_HELPER, *args], input=stdin, capture_output=True, text=True,
                                   timeout=300, check=False)
                ok, msg = r.returncode == 0, failure_text(r.stderr.strip().splitlines())
                if r.returncode in (126, 127):
                    msg = "Not changed. The password wasn't entered."
            except (OSError, subprocess.TimeoutExpired):
                ok, msg = False, "That took too long. Try again"
            GLib.idle_add(lambda: (done(ok, msg), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def open_two_step(self) -> Adw.AlertDialog:
        mine = self.my_two_step() or {}
        dialog = Adw.AlertDialog(heading="Two-step sign-in",
                                 body="After your password, touch a security key or enter a code from your phone. "
                                      "It works on every organisation computer you use. Changes that need "
                                      "an administrator’s password still ask only for your password." + (" Your organisation asks you to set it up."
                                                                    if mine.get("required") and not mine.get("factors") else ""))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for f in mine.get("factors") or []:
            row = Adw.ActionRow(title=GLib.markup_escape_text(f.get("label") or "Second step"),
                                subtitle="Security key" if f.get("kind") == "key" else "Phone code")
            row.add_prefix(Gtk.Image(icon_name="auth-smartcard-symbolic" if f.get("kind") == "key" else "phone-symbolic"))
            remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove", valign=Gtk.Align.CENTER,
                                css_classes=["flat"])

            def removed(_b, fid=f.get("id")):
                dialog.force_close()
                self.two_step_call(["remove", fid], "", lambda ok, msg: (self.window.toast(
                    "Removed" if ok else msg or "Not removed"), self.render_org()))
            remove.connect("clicked", removed)
            row.add_suffix(remove)
            box.append(row)
        for title, subtitle, icon, opener in (
                ("Add a security key", "A USB or NFC key, such as a YubiKey. Touch it when you sign in",
                 "auth-smartcard-symbolic", self.add_two_step_key),
                ("Add a phone code", "A 6-digit code from an authenticator app, such as Google Authenticator "
                 "or Aegis", "phone-symbolic", self.add_two_step_code)):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True, subtitle_lines=0)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.connect("activated", lambda _r, f=opener: (dialog.force_close(), f()))
            box.append(row)
        dialog.set_extra_child(box)
        dialog.add_response("close", "Close")
        dialog.present(self.window)
        return dialog

    def add_two_step_key(self) -> Adw.AlertDialog:
        dialog = Adw.AlertDialog(heading="Add a security key",
                                 body="Plug in your security key, then press Start and touch it when it blinks.")
        name = Adw.EntryRow(title="Name", text="Security key")
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(name)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("start", "Start")
        dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response):
            if response != "start":
                return
            self.window.toast("Touch your security key now")

            def work() -> None:
                try:
                    r = subprocess.run(["pamu2fcfg", "-n", "-o", self.TWO_STEP_ORIGIN, "-i", self.TWO_STEP_ORIGIN],
                                       capture_output=True, text=True, timeout=60, check=False)
                    found = r.returncode == 0 and r.stdout.strip()
                    cred, err = (r.stdout.strip(), "") if found else (
                        "", "No security key answered. Plug it in and touch it when it blinks")
                except (OSError, subprocess.TimeoutExpired):
                    cred, err = "", "The key wasn’t touched in time. Try again, and touch it when it blinks"

                def after() -> bool:
                    if not cred:
                        self.window.toast(err)
                        return False
                    self.two_step_call(["add", "key", name.get_text().strip() or "Security key"], cred + "\n",
                                       lambda ok, msg: (self.window.toast("Security key added. Touch it when you sign in."
                                                                          if ok else msg or "Not added"), self.render_org()))
                    return False
                GLib.idle_add(after)
            threading.Thread(target=work, daemon=True).start()
        dialog.connect("response", answered)
        dialog.present(self.window)
        return dialog

    @staticmethod
    def otpauth_uri(secret: bytes, user: str, issuer: str = "Kulan OS") -> str:
        b32 = base64.b32encode(secret).decode().rstrip("=")
        return (f"otpauth://totp/{urllib.parse.quote(issuer)}:{urllib.parse.quote(user)}?secret={b32}"
                f"&issuer={urllib.parse.quote(issuer)}&algorithm=SHA1&digits=6&period=30")

    def add_two_step_code(self) -> Adw.AlertDialog:
        secret = secrets.token_bytes(20)
        user = getpass.getuser()
        uri = self.otpauth_uri(secret, user)
        dialog = Adw.AlertDialog(heading="Add a phone code",
                                 body="Scan this with an authenticator app, or type the key below. "
                                      "Then enter the 6-digit code it shows.")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        try:
            png = subprocess.run(["qrencode", "-t", "PNG", "-s", "6", "-m", "2", "-o", "-", uri],
                                 capture_output=True, timeout=10, check=True).stdout
            picture = Gtk.Picture.new_for_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)))
            picture.set_size_request(220, 220)
            picture.set_can_shrink(True)
            outer.append(picture)
        except (OSError, subprocess.SubprocessError, GLib.Error):
            pass  # the key below still works
        key = base64.b32encode(secret).decode().rstrip("=")
        outer.append(Gtk.Label(label=" ".join(key[i:i + 4] for i in range(0, len(key), 4)), selectable=True, wrap=True,
                               css_classes=["monospace", "dim-label"]))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        name = Adw.EntryRow(title="Name", text="Phone")
        code = Adw.EntryRow(title="The 6-digit code", input_purpose=Gtk.InputPurpose.DIGITS)
        box.append(name)
        box.append(code)
        outer.append(box)
        dialog.set_extra_child(outer)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Check and save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("save", False)
        code.connect("changed", lambda e: dialog.set_response_enabled("save", bool(re.fullmatch(r"\d{6}", e.get_text().strip()))))

        def answered(_d, response):
            if response != "save":
                return
            self.two_step_call(["add", "code", name.get_text().strip() or "Phone"], f"{secret.hex()}\n{code.get_text().strip()}\n",
                               lambda ok, msg: (self.window.toast("Phone code added. Enter it after your password when "
                                                                  "you sign in." if ok else msg or "Not added"),
                                                self.render_org()))
        dialog.connect("response", answered)
        dialog.present(self.window)
        return dialog

    # --- Joining, and erasing from afar ------------------------------------------------------
    JOINING = Path("/usr/share/kulan-fleet/joining.py")

    @classmethod
    def joining(cls):
        """kulan-fleet's joining module (Kulan's server, and reading a code or link), or None."""
        try:
            spec = importlib.util.spec_from_file_location("kulan_fleet_joining", cls.JOINING)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except (OSError, ImportError, AttributeError, SyntaxError):
            return None

    @staticmethod
    def when_encrypted(callback) -> None:
        """callback(True/False): whether this computer's disk is encrypted, found in the
        background (the page never waits for the disk tools)."""
        def work() -> None:
            try:
                out = subprocess.run(["lsblk", "--noheadings", "--output", "TYPE"], capture_output=True, text=True,
                                     timeout=10, check=False).stdout
                on = "crypt" in out.split()
            except (OSError, subprocess.TimeoutExpired):
                on = True  # unknown: kulan-fleet checks again, and says why if it can't
            GLib.idle_add(lambda: (callback(on), False)[1])
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def erase_needs_encryption(row: Adw.SwitchRow, encrypted: bool) -> None:
        if encrypted or row.get_active():  # already allowed: it can still be turned off
            return
        row.set_sensitive(False)
        row.set_subtitle("Not possible on this computer: its disk isn’t encrypted. Erasing from afar works only "
                         "on a computer installed with encryption on")

    def on_allow_erase(self, row: Adw.SwitchRow, _pspec) -> None:
        on = row.get_active()

        def done(ok: bool) -> None:
            if not ok:
                self.render_org()
        self.window.helper.run(["fleet-allow-erase", "on" if on else "off"],
                               "Your organisation can erase this computer if it’s lost" if on else
                               "Your organisation can’t erase this computer", on_done=done)

    def join_org(self) -> Adw.AlertDialog:
        joining = self.joining()
        dialog = Adw.AlertDialog(heading="Join an organisation",
                                 body="Enter the join code from your IT team. This computer will then report its "
                                      "model, updates and security checks every hour. Files, browsing and "
                                      "messages are never shared.")
        code = Adw.EntryRow(title="Join code")
        code.update_property([Gtk.AccessibleProperty.DESCRIPTION], ["A join code, or a join link"])
        # Kulan's own server is filled in; another organisation's server is under here.
        other = Adw.ExpanderRow(title="Use a different server",
                                subtitle="Only if your IT team runs its own Kulan Fleet server")
        server = Adw.EntryRow(title="Server address (https://…)")
        other.add_row(server)
        if joining is None:  # an older kulan-base: the address has to be typed
            other.set_expanded(True)
            other.set_enable_expansion(True)
        erase = Adw.SwitchRow(title="Allow erasing it if it’s lost or stolen",
                              subtitle="Needs an encrypted disk. You can change this later",
                              subtitle_lines=0, active=False)
        self.when_encrypted(lambda on: self.erase_needs_encryption(erase, on))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(code)
        box.append(other)
        box.append(erase)
        problem = Gtk.Label(wrap=True, xalign=0, css_classes=["error", "caption"], visible=False, margin_top=8)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(box)
        outer.append(problem)
        dialog.set_extra_child(outer)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("join", "Join")
        dialog.set_response_appearance("join", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("join", False)
        answer: dict[str, str] = {}

        def read(*_a) -> None:
            """Join once there's something usable: a code (or a link with one)."""
            answer.clear()
            text, other_server = code.get_text().strip(), server.get_text().strip()
            reason = ""
            if not text:
                pass
            elif joining is not None:
                try:
                    answer["server"], answer["code"] = joining.parse(text, other_server)
                except ValueError as exc:
                    reason = str(exc)
            elif other_server:
                answer["server"], answer["code"] = other_server, text
            else:
                reason = "Enter the server address your IT team gave you"
            problem.set_label(reason)
            problem.set_visible(bool(reason))
            dialog.set_response_enabled("join", bool(answer))
        code.connect("changed", read)
        server.connect("changed", read)

        def answered(_d, response: str) -> None:
            if response != "join" or not answer:
                return
            self.window.helper.run(["fleet-join", answer["server"], *(["--allow-erase"] if erase.get_active() else [])],
                                   "Joined",
                                   on_done=lambda _ok: self.render_org(), stdin=answer["code"])

        dialog.connect("response", answered)
        dialog.present(self.window)
        code.grab_focus()
        return dialog

    def leave_org(self) -> None:
        dialog = Adw.AlertDialog(heading="Leave the organisation?",
                                 body="This computer stops reporting and forgets the organisation. Your "
                                      "organisation may expect it to stay joined.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("leave", "Leave")
        dialog.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: r == "leave" and self.window.helper.run(
            ["fleet-leave"], "Left the organisation", on_done=lambda _ok: self.render_org()))
        dialog.present(self.window)

    def open_url(self, url: str | None) -> None:
        if not url:
            self.toast("No address set")
            return
        Gtk.UriLauncher.new(url).launch(self.window, None, None)
