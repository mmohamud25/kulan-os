"""Add-ons: optional Kulan bundles, installed and removed in one click."""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .common import Page, in_thread, read_json, settings

REGISTRY = Path("/usr/share/kulan/addons.json")


def machine_arch() -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())


def installed_packages(names: list[str]) -> set[str]:
    """Which of these Debian packages are installed: one dpkg-query for all of them."""
    if not names:
        return set()
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Package}\t${Status}\n", "--", *names],
                             capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    found = set()
    for line in out.splitlines():
        name, _sep, status = line.partition("\t")
        if status.strip() == "install ok installed":
            found.add(name.split(":")[0])
    return found


def installed_flatpaks() -> set[str]:
    """The apps and runtimes installed system-wide with Flatpak: one flatpak list."""
    if shutil.which("flatpak") is None:
        return set()
    try:
        out = subprocess.run(["flatpak", "list", "--system", "--columns=application"],
                             capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def installed_addons(addons: list[dict]) -> tuple[set[str], set[str]]:
    """(installed, built in): the IDs of the add-ons whose packages and Flatpak apps
    are all installed, and of the bundles whose apps came with Kulan OS ("includes":
    installed from the ISO, so the bundle's own package isn't there). A built-in one
    shows as installed and is never removed from here, so the built-in apps can't go
    with it. Runs programs: on a worker thread, never the window's."""
    packages = installed_packages(sorted({p for a in addons for p in (a.get("packages") or [])
                                          + (a.get("includes") or [])}))
    flatpaks = installed_flatpaks() if any(a.get("flatpak") for a in addons) else set()
    own = {a["id"] for a in addons
           if all(p in packages for p in a.get("packages") or [])
           and all(f in flatpaks for f in a.get("flatpak") or [])}
    builtin = {a["id"] for a in addons
               if a.get("includes") and a["id"] not in own and all(p in packages for p in a["includes"])}
    return own | builtin, builtin


def set_extension(uuid: str, on: bool) -> None:
    """Turn an add-on's GNOME extension on or off for this user.

    Kulan's defaults already list it once it is installed; only a user who has
    their own list of extensions (they turned one on or off themselves) needs
    it added there, and the list is left alone otherwise."""
    shell = settings("org.gnome.shell")
    if shell is None or not uuid or shell.get_user_value("enabled-extensions") is None:
        return
    enabled = [u for u in shell.get_strv("enabled-extensions") if u != uuid]
    disabled = [u for u in shell.get_strv("disabled-extensions") if u != uuid]
    shell.set_strv("enabled-extensions", [*enabled, uuid] if on else enabled)
    shell.set_strv("disabled-extensions", disabled)


class AddonsPage(Page):
    title = "Add-ons"
    icon = "application-x-addon-symbolic"
    keywords = ("add-ons", "addons", "install", "developer", "office", "extras", "bundles")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.group = self.add_group(
            "Optional add-ons",
            "Extra bundles for Kulan OS. Installing or removing one asks for your password.")
        self.rows: list[Gtk.Widget] = []
        self.buttons: list[Gtk.Button] = []
        self.asked = 0  # which refresh is the latest
        self.builtin: set[str] = set()  # bundles whose apps came with Kulan OS
        self.listen_busy(self.on_busy)
        self.shown()

    def addons(self) -> list[dict]:
        arch = machine_arch()
        entries = read_json(REGISTRY, [])
        return [a for a in entries if isinstance(a, dict) and a.get("id")
                and (a.get("packages") or a.get("flatpak"))
                and (not a.get("arch") or arch in a["arch"])]

    def shown(self) -> None:
        addons = self.addons()
        if not addons:
            self.render(addons, set())
            return
        if not self.rows:
            self.set_rows([Adw.ActionRow(title="Checking…")])
        self.asked += 1
        asked = self.asked

        def done(found: tuple[set[str], set[str]] | None) -> None:
            if asked == self.asked:  # only the latest answer, if several overlap
                installed, self.builtin = found or (set(), set())
                self.render(addons, installed)

        in_thread(lambda: installed_addons(addons), done, page=self)

    def set_rows(self, rows: list[Gtk.Widget]) -> None:
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        for row in rows:
            self.group.add(row)
            self.rows.append(row)

    def render(self, addons: list[dict], installed_ids: set[str]) -> None:
        self.set_rows([])
        self.buttons: list[Gtk.Button] = []
        if not addons:
            self.set_rows([Adw.ActionRow(title="No add-ons available for this computer")])
            return
        for addon in addons:
            installed = addon["id"] in installed_ids
            subtitle = addon.get("description", "")
            if addon.get("size") and not installed:
                subtitle += f" · {addon['size']}"
            row = Adw.ActionRow(title=GLib.markup_escape_text(addon.get("title", addon["id"])),
                                subtitle=GLib.markup_escape_text(subtitle))
            row.add_prefix(Gtk.Image(icon_name=addon.get("icon", "application-x-addon-symbolic"), pixel_size=24))
            if installed:
                row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label", "caption"]))
                if addon.get("settings-page"):
                    setup = Gtk.Button(label="Set up", valign=Gtk.Align.CENTER)
                    setup.update_property([Gtk.AccessibleProperty.LABEL],
                                          [f"Set up {addon.get('title', addon['id'])}"])
                    setup.connect("clicked", lambda _b, page=addon["settings-page"]: self.window.show_page(page))
                    row.add_suffix(setup)
            button = Gtk.Button(label="Remove" if installed else "Install", valign=Gtk.Align.CENTER,
                                css_classes=[] if installed else ["suggested-action"])
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [f"{'Remove' if installed else 'Install'} {addon.get('title', addon['id'])}"])
            button.connect("clicked", self.on_clicked, addon, installed)
            button.set_sensitive(not self.window.helper.busy)
            row.add_suffix(button)
            self.buttons.append(button)
            self.group.add(row)
            self.rows.append(row)

    def on_clicked(self, button: Gtk.Button, addon: dict, installed: bool) -> None:
        title = addon.get("title", addon["id"])
        if installed and addon["id"] in self.builtin:
            dialog = Adw.AlertDialog(heading=f"{title} come with Kulan OS",
                                     body="They’re part of this computer, so they aren’t removed here. To remove "
                                          "one of them, find it in Store and choose Uninstall.")
            dialog.add_response("ok", "OK")
            dialog.present(self.window)
            return
        if installed:
            dialog = Adw.AlertDialog(heading=f"Remove {title}?",
                                     body="The apps in this add-on, and anything only they needed, are removed. "
                                          "Your files are kept.")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("remove", "Remove")
            dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda _d, r: r == "remove" and self.run(button, addon, "addon-remove"))
            dialog.present(self.window)
        else:
            self.run(button, addon, "addon-install")

    def run(self, button: Gtk.Button, addon: dict, action: str) -> None:
        title = addon.get("title", addon["id"])
        button.set_label("Installing…" if action == "addon-install" else "Removing…")

        def done(ok: bool) -> None:
            if ok and addon.get("extension"):
                set_extension(addon["extension"], action == "addon-install")
            if ok and action == "addon-install" and addon.get("note"):
                self.next_steps(title, addon["note"])
            self.shown()

        self.window.helper.run([action, addon["id"]],
                               f"{title} installed" if action == "addon-install" else f"{title} removed",
                               on_done=done)

    def next_steps(self, title: str, note: str) -> None:
        """What to do next, in full (a toast cuts long text off). The text can be
        selected, so commands in it can be copied."""
        dialog = Adw.AlertDialog(heading=f"{title} is installed")
        # Not focusable: a focused selectable label selects all its text.
        text = Gtk.Label(label=note, wrap=True, selectable=True, can_focus=False, xalign=0,
                         max_width_chars=48, css_classes=["body"])
        dialog.set_extra_child(text)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.present(self.window)

    def on_busy(self, busy: bool) -> None:
        for button in getattr(self, "buttons", []):
            button.set_sensitive(not busy)
