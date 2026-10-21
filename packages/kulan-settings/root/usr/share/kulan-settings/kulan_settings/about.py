"""About Kulan OS: version, build, this computer, links."""
from __future__ import annotations

import os
import subprocess
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
    keywords = ("about", "version", "build", "system", "computer", "memory", "processor", "storage")

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

    def open_url(self, url: str | None) -> None:
        if not url:
            self.toast("No address set")
            return
        Gtk.UriLauncher.new(url).launch(self.window, None, None)
