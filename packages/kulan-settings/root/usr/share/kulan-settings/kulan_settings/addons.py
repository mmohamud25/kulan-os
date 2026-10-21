"""Add-ons: optional Kulan bundles, installed and removed in one click."""
from __future__ import annotations

import platform
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .common import Page, package_installed, read_json

REGISTRY = Path("/usr/share/kulan/addons.json")


def machine_arch() -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())


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
        self.listen_busy(self.on_busy)
        self.shown()

    def addons(self) -> list[dict]:
        arch = machine_arch()
        entries = read_json(REGISTRY, [])
        return [a for a in entries if isinstance(a, dict) and a.get("id") and a.get("packages")
                and (not a.get("arch") or arch in a["arch"])]

    def shown(self) -> None:
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        self.buttons: list[Gtk.Button] = []
        addons = self.addons()
        if not addons:
            row = Adw.ActionRow(title="No add-ons available for this computer")
            self.group.add(row)
            self.rows.append(row)
            return
        for addon in addons:
            installed = all(package_installed(p) for p in addon["packages"])
            subtitle = addon.get("description", "")
            if addon.get("size") and not installed:
                subtitle += f" · {addon['size']}"
            row = Adw.ActionRow(title=GLib.markup_escape_text(addon.get("title", addon["id"])),
                                subtitle=GLib.markup_escape_text(subtitle))
            row.add_prefix(Gtk.Image(icon_name=addon.get("icon", "application-x-addon-symbolic"), pixel_size=24))
            if installed:
                row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label", "caption"]))
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
            if ok and action == "addon-install" and addon.get("note"):
                self.toast(addon["note"])
            self.shown()

        self.window.helper.run([action, addon["id"]],
                               f"{title} installed" if action == "addon-install" else f"{title} removed",
                               on_done=done)

    def on_busy(self, busy: bool) -> None:
        for button in getattr(self, "buttons", []):
            button.set_sensitive(not busy)
