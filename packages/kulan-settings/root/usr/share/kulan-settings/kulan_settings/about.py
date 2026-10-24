"""About Kulan OS: version, build, this computer, links."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, os_release

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


RENDERER_HELPER = "/usr/libexec/gnome-control-center-print-renderer"  # GNOME Settings' own
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "software rasterizer", "lavapipe")


def acceleration(helper: str = RENDERER_HELPER) -> tuple[bool | None, str]:
    """Whether the screen is drawn by the graphics chip (True) or by the
    processor (False), and the renderer's name; (None, "") if unknown."""
    try:
        out = subprocess.run([helper], capture_output=True, text=True, timeout=10, check=False,
                             env={**os.environ, "LC_ALL": "C"}).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    name = out.splitlines()[0].strip() if out else ""
    if not name:
        return None, ""
    return not any(s in name.lower() for s in SOFTWARE_RENDERERS), name


def acceleration_text(on: bool | None, name: str) -> str:
    if on is None:
        return ""
    if on:
        return f"On ({name})"
    return (f"Off: everything on the screen is drawn by the processor ({name}), so animations can be slow. "
            "In a virtual machine, turn on 3D or GPU acceleration in its display settings; on a computer, a "
            "graphics driver may be missing.")


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
        version = release.get("VERSION_ID", "")
        build = release.get("BUILD_ID", "")
        line = f"Version {version}" + (f" · Build {build}" if build and build != "local" else "")
        box.append(Gtk.Label(label=line, css_classes=["dim-label"]))
        hero.add(box)

        details = self.add_group("This computer")
        uname = os.uname()
        for title, value in (
                ("Computer name", GLib.get_host_name()),
                ("Processor", processor()),
                ("Memory", memory()),
                ("Storage", storage()),
                ("Based on", f"Debian {release.get('VERSION', '').split('Debian ')[-1].rstrip(')') or 'GNU/Linux'}"),
                ("Kernel", f"Linux {uname.release} ({uname.machine})"),
                ("Hardware model", hardware_model()),
                ("Graphics", graphics()),
                ("3D acceleration", acceleration_text(*acceleration())),
                ("Firmware", firmware()),
                ("Desktop", desktop_version())):
            if not value:
                continue
            row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(value), subtitle_selectable=True)
            row.add_css_class("property")
            details.add(row)

        self.org_group = self.add_group("Organisation")
        self.org_rows: list[Gtk.Widget] = []
        self.render_org()

        links = self.add_group()
        for title, action in (
                ("Check for updates", lambda: self.window.show_page("updates")),
                ("Kulan OS website", lambda: self.open_url(conf.get("KULAN_HOME_URL"))),
                ("Get help", lambda: self.open_url(conf.get("KULAN_SUPPORT_URL")))):
            row = Adw.ActionRow(title=title, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, a=action: a())
            links.add(row)

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
                                     f"Reports to {data.get('server', '')} every hour · last report {when}"
                                     + (f" · last problem: {data['last_error']}" if data.get("last_error") else "")))
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
                subtitle="Report now, install updates, turn compliance mode on, lock the screen, "
                         "restart (with 5 minutes' notice)" + (", and erase it if it’s lost"
                                                               if data.get("allow_erase") else ""),
                subtitle_lines=0))
            rows = [head, shared]
            policy = self.fleet_policy()
            if policy:
                rows.append(policy)
            erase = Adw.SwitchRow(title="Allow erasing this computer if it’s lost or stolen",
                                  subtitle="Your organisation can then make everything on its disk unreadable from "
                                           "afar, after a 20-second notice on the screen. Only works when the "
                                           "disk is encrypted", subtitle_lines=0,
                                  active=bool(data.get("allow_erase")))
            erase.connect("notify::active", self.on_allow_erase)
            self.erase_row = erase
            rows.append(erase)
        else:
            join = Adw.ActionRow(title="Join an organisation",
                                 subtitle="For work or school computers: your IT team gives you a server address "
                                          "and a one-time code", activatable=True)
            join.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            join.connect("activated", lambda _r: self.join_org())
            rows = [join]
        for row in rows:
            self.org_group.add(row)
            self.org_rows.append(row)

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
        items = [(words[k], status.get(k)) for k in words if policy.get(k)]
        if policy.get("screen_lock_minutes"):
            items.append((f"Screen locks after {policy['screen_lock_minutes']} minutes",
                          status.get("screen_lock_minutes")))
        missing = sum(1 for _t, s in items if s != "met")
        row = Adw.ExpanderRow(title="Your organisation’s policy",
                              subtitle="All met" if not missing else
                              f"{missing} not met: your organisation is told")
        for title, state in items:
            item = Adw.ActionRow(title=title, subtitle="Met" if state == "met" else
                                 "Not met. For disk encryption, Kulan OS has to be installed again with "
                                 "encryption on" if title == "Disk encryption" else "Not met yet",
                                 subtitle_lines=0)
            item.add_prefix(Gtk.Image(icon_name="object-select-symbolic" if state == "met" else
                                      "dialog-warning-symbolic"))
            row.add_row(item)
        return row

    def on_allow_erase(self, row: Adw.SwitchRow, _pspec) -> None:
        on = row.get_active()

        def done(ok: bool) -> None:
            if not ok:
                self.render_org()
        self.window.helper.run(["fleet-allow-erase", "on" if on else "off"],
                               "Your organisation can erase this computer if it’s lost" if on else
                               "Your organisation can’t erase this computer", on_done=done)

    def join_org(self) -> None:
        dialog = Adw.AlertDialog(heading="Join an organisation",
                                 body="This computer will report its status (model, updates, security checks) "
                                      "every hour. Files, browsing and messages are never shared.")
        server = Adw.EntryRow(title="Server address (https://…)")
        code = Adw.PasswordEntryRow(title="One-time code")
        erase = Adw.SwitchRow(title="Allow erasing it if it’s lost or stolen",
                              subtitle="Only works when the disk is encrypted. You can change this later",
                              subtitle_lines=0, active=False)
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(server)
        box.append(code)
        box.append(erase)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("join", "Join")
        dialog.set_response_appearance("join", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def answered(_d, response: str) -> None:
            if response != "join" or not server.get_text().strip() or not code.get_text().strip():
                return
            self.window.helper.run(["fleet-join", server.get_text().strip(), *(["--allow-erase"] if erase.get_active() else [])],
                                   "Joined",
                                   on_done=lambda _ok: self.render_org(), stdin=code.get_text().strip())

        dialog.connect("response", answered)
        dialog.present(self.window)

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
