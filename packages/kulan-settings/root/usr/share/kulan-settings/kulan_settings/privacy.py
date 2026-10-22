"""Privacy: computer name, encrypted DNS and ad blocking, camera, microphone,
location, history, and what Kulan OS connects to."""
from __future__ import annotations

import re
import secrets
import socket
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import (
    Page,
    choice_row,
    has_key,
    launch,
    reset_keys,
    settings,
    switch_row,
)
from .permissions import PermissionsDialog

DNS_CONF = Path("/etc/systemd/resolved.conf.d/60-kulan-dns.conf")
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

DNS_CHOICES = [
    ("off", "Your network’s"),
    ("quad9", "Encrypted (Quad9)"),
    ("adblock", "Encrypted, blocks ads and trackers"),
    ("family", "Encrypted, family-safe (blocks adult and harmful sites)"),
]

CONNECTIONS = [
    ("Browser blockers", ("Firefox and Chromium download uBlock Origin from their own add-on stores, "
                          "and keep it up to date")),
    ("Software updates", ("deb.debian.org, security.debian.org and the Kulan OS update server, to check "
                          "for and download updates")),
    ("Clock", "A Debian time server, to keep the clock right"),
    ("Encrypted DNS", "Quad9, Mullvad or Cloudflare for Families, only if you turn it on"),
    ("Nothing else", ("No usage data, crash reports or advertising IDs are sent. Apps you install may "
                      "connect to their own services")),
]


def dns_mode() -> str:
    try:
        text = DNS_CONF.read_text()
    except OSError:
        return "off"
    if "mullvad" in text:
        return "adblock"
    return "family" if "cloudflare-dns" in text else "quad9"


def current_name() -> str:
    """The computer's name now (GLib caches it for the life of the app)."""
    return socket.gethostname()


def private_name() -> str:
    return f"kulan-{secrets.token_hex(3)}"


NM_NAME = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"


class PrivacyPage(Page):
    title = "Privacy"
    icon = "preferences-system-privacy-symbolic"
    keywords = ("privacy", "dns", "ads", "adblock", "tracking", "trackers", "camera", "microphone", "location",
                "history", "recent files", "computer name", "hostname", "connectivity")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.privacy = settings("org.gnome.desktop.privacy")
        self.location = settings("org.gnome.system.location")

        network = self.add_group("On the network")
        self.name_row = Adw.ActionRow(title="Computer name",
                                      subtitle="Other devices on the network can see it")
        self.name_row.add_css_class("property")
        rename = Gtk.Button(label="Make private", valign=Gtk.Align.CENTER,
                            tooltip_text="Use a name that doesn’t include yours, such as kulan-3fa9c2")
        rename.connect("clicked", lambda _b: self.set_name(private_name()))
        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                          tooltip_text="Choose a name")
        edit.update_property([Gtk.AccessibleProperty.LABEL], ["Choose a name"])
        edit.connect("clicked", lambda _b: self.ask_name())
        self.name_buttons = [rename, edit]
        self.name_row.add_suffix(rename)
        self.name_row.add_suffix(edit)
        network.add(self.name_row)
        self.dns_row = choice_row("Name lookups (DNS)",
                                  "Encrypted hides the sites you visit from the network whenever the network "
                                  "allows it (some hotel and office Wi-Fi doesn't). The ad-blocking choice works "
                                  "in every app and browser",
                                  DNS_CHOICES, dns_mode, self.set_dns)
        network.add(self.dns_row)

        devices = self.add_group("Camera and microphone",
                                 "Off blocks every app. Video calls need them on.")
        self.add_rows(devices,
                      self.inverted_switch("disable-camera", "Camera"),
                      self.inverted_switch("disable-microphone", "Microphone"),
                      switch_row(self.location, "enabled", "Location services",
                                 "Lets apps such as Maps and Weather find where you are"))
        permissions = Adw.ActionRow(title="App permissions", subtitle="Which apps may use the camera, files "
                                                                      "and more", activatable=True)
        permissions.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        permissions.connect("activated", lambda _r: PermissionsDialog(self.toast).present(self.window))
        devices.add(permissions)

        history = self.add_group("History")
        self.add_rows(history,
                      switch_row(self.privacy, "remember-recent-files", "Remember recent files",
                                 "Shown in Files and when opening documents"),
                      switch_row(self.privacy, "remove-old-temp-files", "Empty temporary files automatically"),
                      switch_row(self.privacy, "remove-old-trash-files", "Empty the bin after 30 days"))
        clear = Adw.ActionRow(title="Clear recent files now")
        clear_button = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        clear_button.connect("clicked", lambda _b: self.clear_recent())
        clear.add_suffix(clear_button)
        history.add(clear)
        self.add_rows(history,
                      switch_row(self.privacy, "report-technical-problems", "Send problem reports",
                                 "Off in Kulan OS: nothing is sent"))
        metadata = Adw.ActionRow(title="Remove hidden details from files",
                                 subtitle="Photos and documents can carry your location, name and device. "
                                          "Metadata Cleaner removes them before you share",
                                 activatable=True)
        metadata.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        metadata.connect("activated", lambda _r: self.open_app("fr.romainvigier.MetadataCleaner.desktop"))
        history.add(metadata)

        connections = self.add_group("What Kulan OS connects to")
        for title, text in CONNECTIONS:
            row = Adw.ActionRow(title=title, subtitle=text, subtitle_lines=0)
            connections.add(row)
        self.check_row = Adw.SwitchRow(title="Connectivity check",
                                       subtitle="Loads a small test page now and then to spot Wi-Fi sign-in pages "
                                                "(hotels, airports). Off: you open those pages yourself")
        self.check_row.set_visible(False)
        connections.add(self.check_row)
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, NM_NAME, NM_PATH, NM_NAME,
                                  None, self.got_nm)

        self.add_reset(self.reset)
        self.listen_busy(self.on_busy)
        self.shown()

    def inverted_switch(self, key: str, title: str) -> Adw.SwitchRow | None:
        """GNOME's keys are 'disable-…'; the switch shows 'allowed'."""
        if not has_key(self.privacy, key):
            return None
        row = Adw.SwitchRow(title=title, active=not self.privacy.get_boolean(key))
        row.connect("notify::active", lambda r, _p: self.privacy.set_boolean(key, not r.get_active()))
        self.watch(self.privacy, f"changed::{key}",
                             lambda s, k: row.get_active() == s.get_boolean(k) and row.set_active(not s.get_boolean(k)))
        return row

    # --- computer name --------------------------------------------------------------
    def shown(self) -> None:
        self.name_row.set_subtitle(GLib.markup_escape_text(current_name()))

    def ask_name(self) -> None:
        dialog = Adw.AlertDialog(heading="Computer name",
                                 body="Letters, numbers and dashes. Avoid your own name: other devices on "
                                      "the network can see it.")
        entry = Gtk.Entry(text=current_name(), activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def validate(*_a) -> None:
            dialog.set_response_enabled("rename", bool(HOSTNAME_RE.match(entry.get_text().strip().lower())))

        entry.connect("changed", validate)
        validate()
        dialog.connect("response", lambda _d, r: r == "rename" and self.set_name(entry.get_text().strip().lower()))
        dialog.present(self.window)

    def set_name(self, name: str) -> None:
        if not HOSTNAME_RE.match(name):
            self.toast("Use letters, numbers and dashes only")
            return
        self.window.helper.run(["set-hostname", name], f"Computer name is now {name}",
                               on_done=lambda _ok: self.shown())

    # --- DNS -------------------------------------------------------------------------
    def set_dns(self, mode: str) -> None:
        if mode == dns_mode():
            return
        message = {"off": "Using your network’s DNS", "quad9": "Encrypted DNS on",
                   "adblock": "Encrypted DNS with ad blocking on",
                   "family": "Family-safe DNS on"}[mode]
        self.window.helper.run(["dns", mode], message, on_done=lambda _ok: self.window.reload_page(self))

    # --- history ---------------------------------------------------------------------
    def clear_recent(self) -> None:
        manager = Gtk.RecentManager.get_default()
        try:
            manager.purge_items()
        except GLib.Error:
            pass
        self.toast("Recent files cleared")

    def open_app(self, desktop_id: str) -> None:
        if not launch(desktop_id):
            self.toast("Metadata Cleaner isn’t installed. Get it from Software")

    def on_busy(self, busy: bool) -> None:
        for button in self.name_buttons:
            button.set_sensitive(not busy)
        self.dns_row.set_sensitive(not busy)

    def got_nm(self, _source, result) -> None:
        try:
            self.nm = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return
        if self.torn_down or self.nm.get_name_owner() is None:
            return
        available = self.nm.get_cached_property("ConnectivityCheckAvailable")
        enabled = self.nm.get_cached_property("ConnectivityCheckEnabled")
        if available is None or enabled is None or not available.unpack():
            return  # turned off in NetworkManager's own settings
        self.check_row.set_active(enabled.unpack())
        self.check_row.set_visible(True)
        self.check_row.connect("notify::active", self.on_check)

    def on_check(self, row: Adw.SwitchRow, _pspec) -> None:
        on = row.get_active()

        def done(_conn, result) -> None:
            try:
                _conn.call_finish(result)
            except GLib.Error as exc:
                if not self.torn_down:
                    self.toast("Not changed" if "not authorized" in exc.message.lower()
                               or "dismissed" in exc.message.lower() else exc.message)
                    row.handler_block_by_func(self.on_check)
                    row.set_active(not on)
                    row.handler_unblock_by_func(self.on_check)
                return
            if not self.torn_down:
                self.toast("Connectivity check on" if on else "Connectivity check off")
        self.nm.get_connection().call(NM_NAME, NM_PATH, "org.freedesktop.DBus.Properties", "Set",
                                      GLib.Variant("(ssv)", (NM_NAME, "ConnectivityCheckEnabled",
                                                             GLib.Variant("b", on))),
                                      None, Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 60000, None, done)

    def reset(self) -> None:
        reset_keys(self.privacy, ["disable-camera", "disable-microphone", "remember-recent-files",
                                  "remove-old-temp-files", "remove-old-trash-files", "report-technical-problems"])
        reset_keys(self.location, ["enabled"])
        self.window.reload_page(self)
        self.toast("Privacy reset to Kulan defaults (name and DNS unchanged)")
