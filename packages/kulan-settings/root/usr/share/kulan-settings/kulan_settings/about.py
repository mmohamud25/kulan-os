"""About Kulan OS: version, build, this computer, links."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, open_gnome_panel, os_release

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


class AboutPage(Page):
    title = "About Kulan OS"
    icon = "help-about-symbolic"
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
                ("Kernel", f"Linux {uname.release}")):
            row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(value), subtitle_selectable=True)
            row.add_css_class("property")
            details.add(row)

        self.org_group = self.add_group("Organisation")
        self.org_rows: list[Gtk.Widget] = []
        self.render_org()

        links = self.add_group()
        for title, action in (
                ("Check for updates", lambda: self.window.show_page("updates")),
                ("More system details", lambda: open_gnome_panel("system", "about")),
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
                         "restart (with 5 minutes' notice)"))
            rows = [head, shared]
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

    def join_org(self) -> None:
        dialog = Adw.AlertDialog(heading="Join an organisation",
                                 body="This computer will report its status (model, updates, security checks) "
                                      "every hour. Files, browsing and messages are never shared.")
        server = Adw.EntryRow(title="Server address (https://…)")
        code = Adw.PasswordEntryRow(title="One-time code")
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(server)
        box.append(code)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("join", "Join")
        dialog.set_response_appearance("join", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def answered(_d, response: str) -> None:
            if response != "join" or not server.get_text().strip() or not code.get_text().strip():
                return
            self.window.helper.run(["fleet-join", server.get_text().strip()], "Joined",
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
