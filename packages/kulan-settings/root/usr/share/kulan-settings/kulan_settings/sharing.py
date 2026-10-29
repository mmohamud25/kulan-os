"""Sharing: remote desktop (GNOME Remote Desktop, RDP), and file and media
sharing on chosen networks (GNOME's gnome-user-share and Rygel).

Remote desktop is this person's own: while they're signed in, someone with
the username and password shown here can see the screen from a remote
desktop app (Remote Desktop on Windows, Windows App on a Mac, Remmina on
Linux), and use the mouse and keyboard if that's allowed. It's set up the way
GNOME Settings does it:
  - a TLS certificate of its own (~/.local/share/gnome-remote-desktop),
  - the username and password in the person's keyring, as GNOME Remote
    Desktop reads them (schema org.gnome.RemoteDesktop.RdpCredentials),
  - org.gnome.desktop.remote-desktop.rdp: enable, view-only, port 3389,
  - their gnome-remote-desktop service on.
Kulan's firewall blocks it until it's allowed from local networks only
(update-run remote-desktop-on, which asks an administrator's password);
never from the internet. Turning it off leaves that rule (others on the
computer may use remote desktop, and closing it needs an administrator); the
page then offers to close it. Remote help from an organisation (Kulan Fleet) is
separate, and is asked for each time.

File and media sharing work as GNOME Settings has them: on for chosen
networks only (gnome-settings-daemon's sharing plugin starts and stops them
as the computer changes network). The Public folder is shared over WebDAV
(gnome-user-share): anyone on that network may open it, and adding or
changing files needs a password Kulan makes (kept in the keyring, and as
gnome-user-share reads it in ~/.config/user-share/passwd); or the password to
open it too. Music, photos and videos go to TVs and players over UPnP/DLNA
(Rygel). Kulan OS gives them fixed ports (30301 and 30302, in drop-ins for
their services), which the firewall lets in from local networks only
(update-run sharing-on, asking an administrator's password), with the same
offer to close it again when nothing is shared.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .common import Page, settings

SCHEMA_ID = "org.gnome.desktop.remote-desktop.rdp"
CREDENTIALS_SCHEMA = "org.gnome.RemoteDesktop.RdpCredentials"
PORT = 3389
FIREWALL_MARK = Path("/var/lib/kulan/remote-desktop-firewall")
SERVICE = "gnome-remote-desktop.service"
SERVICE_FILE = Path("/usr/lib/systemd/user") / SERVICE
TLS_DIR = Path(GLib.get_user_data_dir()) / "gnome-remote-desktop"
PASSWORD_LETTERS = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRTUVWXYZ2346789"
# File and media sharing
FILES_SERVICE, MEDIA_SERVICE = "gnome-user-share-webdav", "rygel"
FILES_PORT = 30301
SHARING_SCHEMA = "org.gnome.settings-daemon.plugins.sharing.service"
FILE_SHARING_SCHEMA = "org.gnome.desktop.file-sharing"
SHARING_FIREWALL_MARK = Path("/var/lib/kulan/sharing-firewall")
FILES_PASSWD = Path(GLib.get_user_config_dir()) / "user-share" / "passwd"
FILES_REALM = "Please log in as the user guest"  # what gnome-user-share's password check uses
FILES_SECRET_SCHEMA = "org.kulan.FileSharing"


def new_password() -> str:
    """16 letters and digits that can't be mistaken for each other, in groups of four."""
    raw = "".join(secrets.choice(PASSWORD_LETTERS) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def credentials_text(username: str, password: str) -> str:
    """How GNOME Remote Desktop keeps them: a GVariant a{sv}, as text."""
    return GLib.Variant("a{sv}", {"username": GLib.Variant("s", username),
                                  "password": GLib.Variant("s", password)}).print_(True)


def parse_credentials(text: str | None) -> tuple[str, str] | None:
    if not text:
        return None
    try:
        value = GLib.Variant.parse(GLib.VariantType("a{sv}"), text, None, None).unpack()
    except GLib.Error:
        return None
    username, password = value.get("username"), value.get("password")
    return (username, password) if isinstance(username, str) and isinstance(password, str) and username else None


def secret_schema():
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None, None
    return Secret, Secret.Schema.new(CREDENTIALS_SCHEMA, Secret.SchemaFlags.NONE, {})


def load_credentials() -> tuple[str, str] | None:
    secret, schema = secret_schema()
    if schema is None:
        return None
    try:
        return parse_credentials(secret.password_lookup_sync(schema, {}, None))
    except GLib.Error:
        return None


def store_credentials(username: str, password: str) -> bool:
    secret, schema = secret_schema()
    if schema is None:
        return False
    try:
        return secret.password_store_sync(schema, {}, secret.COLLECTION_DEFAULT, "GNOME Remote Desktop RDP credentials",
                                          credentials_text(username, password), None)
    except GLib.Error:
        return False


def make_certificate(directory: Path = TLS_DIR) -> tuple[Path, Path] | None:
    """The certificate and key remote desktop apps see (made once, kept 10 years)."""
    cert, key = directory / "rdp-tls.crt", directory / "rdp-tls.key"
    if cert.exists() and key.exists():
        return cert, key
    if not shutil.which("openssl"):
        return None
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    try:
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:4096", "-sha256", "-days", "3650", "-nodes",
                            "-subj", f"/CN={GLib.get_host_name()}", "-keyout", str(key), "-out", str(cert)],
                           capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not cert.exists() or not key.exists():
        cert.unlink(missing_ok=True)
        key.unlink(missing_ok=True)
        return None
    key.chmod(0o600)
    return cert, key


def fingerprint(cert: Path) -> str:
    """The SHA-256 fingerprint a remote desktop app shows when it first connects."""
    try:
        out = subprocess.run(["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", str(cert)],
                             capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = re.search(r"=([0-9A-F:]{95})", out)
    return match.group(1) if match else ""


def local_address() -> str | None:
    """This computer's address on its network (nothing is sent: a UDP socket
    only picks the route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            address = s.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


def passwd_line(password: str) -> str:
    """The password file line for gnome-user-share's user "guest" (Apache's
    htdigest format: the MD5 of user:realm:password)."""
    digest = hashlib.md5(f"guest:{FILES_REALM}:{password}".encode()).hexdigest()
    return f"guest:{FILES_REALM}:{digest}\n"


def write_passwd(password: str, path: Path = FILES_PASSWD) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".new")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(passwd_line(password))
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def files_password() -> str | None:
    secret, _schema = secret_schema()
    if secret is None:
        return None
    schema = secret.Schema.new(FILES_SECRET_SCHEMA, secret.SchemaFlags.NONE, {})
    try:
        return secret.password_lookup_sync(schema, {}, None)
    except GLib.Error:
        return None


def store_files_password(password: str) -> bool:
    secret, _schema = secret_schema()
    if secret is None:
        return False
    schema = secret.Schema.new(FILES_SECRET_SCHEMA, secret.SchemaFlags.NONE, {})
    try:
        return secret.password_store_sync(schema, {}, secret.COLLECTION_DEFAULT, "Password for your shared Public folder",
                                          password, None) and write_passwd(password)
    except GLib.Error:
        return False


def current_network() -> tuple[str, str] | None:
    """(the NetworkManager connection's UUID, its name) for the network the
    computer is on now, or None."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        nm = "org.freedesktop.NetworkManager"
        primary = bus.call_sync(nm, "/org/freedesktop/NetworkManager", "org.freedesktop.DBus.Properties", "Get",
                                GLib.Variant("(ss)", (nm, "PrimaryConnection")), GLib.VariantType("(v)"),
                                Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        if not primary or primary == "/":
            return None
        props = bus.call_sync(nm, primary, "org.freedesktop.DBus.Properties", "GetAll",
                              GLib.Variant("(s)", (nm + ".Connection.Active",)), GLib.VariantType("(a{sv})"),
                              Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    except GLib.Error:
        return None
    uuid, name = props.get("Uuid"), props.get("Id")
    return (uuid, name or "this network") if uuid else None


def sharing_settings(service: str) -> Gio.Settings | None:
    """gnome-settings-daemon's list of networks a service is on for, or None
    when the service or the daemon's schema isn't installed."""
    if not (Path("/usr/lib/systemd/user") / f"{service}.service").exists():
        return None
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SHARING_SCHEMA, True) is None:
        return None
    return Gio.Settings.new_with_path(SHARING_SCHEMA, f"/org/gnome/settings-daemon/plugins/sharing/{service}/")


def shared_on(gsettings: Gio.Settings | None, uuid: str | None) -> bool:
    return bool(gsettings and uuid and uuid in gsettings.get_strv("enabled-connections"))


def set_shared_on(gsettings: Gio.Settings, uuid: str, on: bool) -> None:
    networks = [u for u in gsettings.get_strv("enabled-connections") if u != uuid]
    gsettings.set_strv("enabled-connections", networks + [uuid] if on else networks)


def firewall_on() -> bool:
    try:
        return "ENABLED=yes" in Path("/etc/ufw/ufw.conf").read_text().split()
    except OSError:
        return False


class SharingPage(Page):
    title = "Sharing"
    icon = "org.gnome.Settings-sharing-symbolic|emblem-shared-symbolic"
    keywords = ("sharing", "remote desktop", "rdp", "remote", "screen sharing", "file sharing", "media sharing",
                "vnc", "control", "public folder", "webdav", "dlna", "upnp", "tv", "media server")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.rdp = settings(SCHEMA_ID)
        self.busy = False
        remote = self.add_group(
            "Remote desktop",
            "While you’re signed in, someone with this username and password can see your screen from a remote "
            "desktop app: Remote Desktop on Windows, Windows App on a Mac, or Remmina on Linux.")
        self.remote = remote
        if self.rdp is None or not SERVICE_FILE.exists():
            row = Adw.ActionRow(title="Remote desktop isn’t installed on this computer",
                                subtitle="It comes with GNOME Remote Desktop", subtitle_lines=0)
            row.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
            remote.add(row)
            self.switch = None
        else:
            self.switch = Adw.SwitchRow(title="Remote desktop", subtitle="Off")
            self.switch.set_active(self.rdp.get_boolean("enable"))
            self.switch_handler = self.switch.connect("notify::active", self.on_switch)
            remote.add(self.switch)
            self.control = Adw.SwitchRow(title="Let them use the mouse and keyboard",
                                         subtitle="Off: they can only watch", subtitle_lines=0)
            self.rdp.bind("view-only", self.control, "active", Gio.SettingsBindFlags.INVERT_BOOLEAN)
            remote.add(self.control)
            self.address = self.copy_row("Address", "")
            remote.add(self.address)
            self.username = self.copy_row("Username", "")
            remote.add(self.username)
            self.password = self.copy_row("Password", "", secret=True)
            change = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="New password", valign=Gtk.Align.CENTER,
                                css_classes=["flat"])
            change.update_property([Gtk.AccessibleProperty.LABEL], ["New password"])
            change.connect("clicked", lambda _b: self.change_password())
            self.password.add_suffix(change)
            remote.add(self.password)
            self.fingerprint = Adw.ActionRow(title="Certificate fingerprint",
                                             subtitle="Check it matches the one the app shows when it first connects",
                                             subtitle_lines=0, subtitle_selectable=True)
            remote.add(self.fingerprint)
            self.firewall = Adw.ActionRow(title="The firewall blocks remote desktop",
                                          subtitle="Allow it from your local network (never from the internet). "
                                                   "This needs an administrator’s password", subtitle_lines=0)
            self.firewall.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
            allow = Gtk.Button(label="Allow", valign=Gtk.Align.CENTER)
            allow.connect("clicked", lambda _b: self.allow_firewall())
            self.firewall.add_suffix(allow)
            remote.add(self.firewall)
            self.still_open = Adw.ActionRow(title="The firewall still lets remote desktop in",
                                            subtitle="From your local network only, and nothing answers while remote "
                                                     "desktop is off. Close it if nobody here will use it again",
                                            subtitle_lines=0)
            close = Gtk.Button(label="Close", valign=Gtk.Align.CENTER)
            close.connect("clicked", lambda _b: self.close_firewall())
            self.still_open.add_suffix(close)
            remote.add(self.still_open)
            self.watch(self.rdp, "changed::enable", lambda *_a: self.sync())

        self.build_files_and_media()
        self.sync()

    # --- file and media sharing ---------------------------------------------------------
    def build_files_and_media(self) -> None:
        self.files = sharing_settings(FILES_SERVICE)
        self.media = sharing_settings(MEDIA_SERVICE)
        self.file_sharing = settings(FILE_SHARING_SCHEMA)
        self.network: tuple[str, str] | None = None
        group = self.add_group("Files and media",
                               "With computers, TVs and players on the same network. It’s on only for the networks "
                               "you turn it on for, and the firewall lets it in from local networks only.")
        self.share_group = group
        if self.files is None and self.media is None:
            row = Adw.ActionRow(title="File and media sharing aren’t installed on this computer", subtitle_lines=0)
            row.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
            group.add(row)
            self.files_switch = self.media_switch = None
            return
        self.network_row = Adw.ActionRow(title="Network", subtitle="")
        self.network_row.add_css_class("property")
        group.add(self.network_row)
        self.files_switch = self.media_switch = None
        if self.files is not None:
            self.files_switch = Adw.SwitchRow(title="Your Public folder", subtitle_lines=0)
            self.files_handler = self.files_switch.connect("notify::active", lambda r, _p: self.on_share(FILES_SERVICE, r))
            group.add(self.files_switch)
            self.files_address = self.copy_row("Address", "")
            group.add(self.files_address)
            self.files_password_row = self.copy_row("Password to add or change files", "", secret=True)
            change = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="New password", valign=Gtk.Align.CENTER,
                                css_classes=["flat"])
            change.update_property([Gtk.AccessibleProperty.LABEL], ["New password"])
            change.connect("clicked", lambda _b: self.new_files_password())
            self.files_password_row.add_suffix(change)
            group.add(self.files_password_row)
            self.files_always = Adw.SwitchRow(title="Ask for the password to open it too",
                                              subtitle="Off: anyone on the network can open it and copy files from it",
                                              subtitle_lines=0)
            self.always_handler = self.files_always.connect("notify::active", lambda r, _p: self.on_always(r))
            group.add(self.files_always)
            self.watch(self.files, "changed::enabled-connections", lambda *_a: self.sync_sharing())
        if self.media is not None:
            self.media_switch = Adw.SwitchRow(title="Music, photos and videos", subtitle_lines=0,
                                              subtitle="TVs and media players on this network can play what’s in your "
                                                       "Music, Pictures and Videos folders")
            self.media_handler = self.media_switch.connect("notify::active", lambda r, _p: self.on_share(MEDIA_SERVICE, r))
            group.add(self.media_switch)
            self.watch(self.media, "changed::enabled-connections", lambda *_a: self.sync_sharing())
        self.share_firewall = Adw.ActionRow(title="The firewall blocks sharing",
                                            subtitle="Allow it from local networks (never from the internet). This "
                                                     "needs an administrator’s password", subtitle_lines=0)
        self.share_firewall.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
        allow = Gtk.Button(label="Allow", valign=Gtk.Align.CENTER)
        allow.connect("clicked", lambda _b: self.allow_sharing_firewall())
        self.share_firewall.add_suffix(allow)
        group.add(self.share_firewall)
        self.share_still_open = Adw.ActionRow(title="The firewall still lets sharing in",
                                              subtitle="From local networks only, and nothing answers while sharing is "
                                                       "off. Close it if nobody here will share again", subtitle_lines=0)
        close = Gtk.Button(label="Close", valign=Gtk.Align.CENTER)
        close.connect("clicked", lambda _b: self.close_sharing_firewall())
        self.share_still_open.add_suffix(close)
        group.add(self.share_still_open)

    def sync_sharing(self) -> None:
        if self.torn_down or (self.files_switch is None and self.media_switch is None):
            return
        self.network = current_network()
        uuid = self.network[0] if self.network else None
        self.network_row.set_subtitle(GLib.markup_escape_text(self.network[1]) if self.network else
                                      "Not connected. Connect to a network to share on it")
        anywhere = False
        for switch, gsettings, handler in ((self.files_switch, self.files, getattr(self, "files_handler", 0)),
                                           (self.media_switch, self.media, getattr(self, "media_handler", 0))):
            if switch is None:
                continue
            on = shared_on(gsettings, uuid)
            anywhere = anywhere or bool(gsettings.get_strv("enabled-connections"))
            if switch.get_active() != on:
                switch.handler_block(handler)
                switch.set_active(on)
                switch.handler_unblock(handler)
            switch.set_sensitive(uuid is not None)
        if self.files_switch is not None:
            on = self.files_switch.get_active()
            self.files_switch.set_subtitle(
                "On. Others on this network open it at the address below: on a Mac, Finder › Go › Connect to "
                "Server; on Windows, Map network drive; in Kulan OS, Files › Other Locations (dav:// instead of "
                "http://). Adding or changing files needs the password" if on else
                "Others on this network can open your Public folder")
            for row in (self.files_address, self.files_password_row, self.files_always):
                row.set_visible(on)
            if on:
                ip = local_address()
                self.set_row(self.files_address, f"http://{GLib.get_host_name()}.local:{FILES_PORT}"
                             + (f" or http://{ip}:{FILES_PORT}" if ip else ""))
                self.set_row(self.files_password_row, files_password() or "")
                always = self.file_sharing is not None and self.file_sharing.get_string("require-password") == "always"
                if self.files_always.get_active() != always:
                    self.files_always.handler_block(self.always_handler)
                    self.files_always.set_active(always)
                    self.files_always.handler_unblock(self.always_handler)
        shared_here = any(s is not None and s.get_active() for s in (self.files_switch, self.media_switch))
        self.share_firewall.set_visible(shared_here and firewall_on() and not SHARING_FIREWALL_MARK.exists())
        self.share_still_open.set_visible(not anywhere and SHARING_FIREWALL_MARK.exists())

    def on_share(self, service: str, row: Adw.SwitchRow) -> None:
        gsettings = self.files if service == FILES_SERVICE else self.media
        if not self.network or gsettings is None:
            self.sync_sharing()
            return
        on = row.get_active()
        if on and service == FILES_SERVICE:
            # Never shared writable without a password: adding or changing files always needs one.
            if not files_password() and not store_files_password(new_password()):
                self.toast("Your keyring couldn’t keep the sharing password")
                self.sync_sharing()
                return
            if self.file_sharing is not None and self.file_sharing.get_string("require-password") not in ("on_write", "always"):
                self.file_sharing.set_string("require-password", "on_write")
            write_passwd(files_password() or "")
        set_shared_on(gsettings, self.network[0], on)
        self.sync_sharing()
        if on and firewall_on() and not SHARING_FIREWALL_MARK.exists():
            self.allow_sharing_firewall()

    def on_always(self, row: Adw.SwitchRow) -> None:
        if self.file_sharing is not None:
            self.file_sharing.set_string("require-password", "always" if row.get_active() else "on_write")

    def new_files_password(self) -> None:
        if store_files_password(new_password()):
            self.toast("New password for your Public folder")
        else:
            self.toast("Your keyring couldn’t keep the new password")
        self.sync_sharing()

    def allow_sharing_firewall(self) -> None:
        if getattr(self, "allowing", False):
            return  # (both switches turned on at once: asked once)
        self.allowing = True

        def done(_ok) -> None:
            self.allowing = False
            self.sync_sharing()
        self.window.helper.run(["sharing-on"], "Sharing allowed from your local network", on_done=done)

    def close_sharing_firewall(self) -> None:
        self.window.helper.run(["sharing-off"], "Sharing is no longer let through the firewall",
                               on_done=lambda _ok: self.sync_sharing())

    def copy_row(self, title: str, value: str, secret: bool = False) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=value, subtitle_selectable=not secret)
        row.add_css_class("property")
        row.secret = secret
        row.value = value
        if secret:
            show = Gtk.ToggleButton(icon_name="view-reveal-symbolic", tooltip_text="Show", valign=Gtk.Align.CENTER,
                                    css_classes=["flat"])
            show.update_property([Gtk.AccessibleProperty.LABEL], [f"Show the {title.lower()}"])
            show.connect("toggled", lambda b: self.set_row(row, row.value, shown=b.get_active()))
            row.add_suffix(show)
            row.reveal = show
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy", valign=Gtk.Align.CENTER,
                          css_classes=["flat"])
        copy.update_property([Gtk.AccessibleProperty.LABEL], [f"Copy the {title.lower()}"])
        copy.connect("clicked", lambda _b: self.copy(row))
        row.add_suffix(copy)
        return row

    def set_row(self, row: Adw.ActionRow, value: str, shown: bool | None = None) -> None:
        row.value = value
        if row.secret and not (row.reveal.get_active() if shown is None else shown):
            row.set_subtitle("•" * 12 if value else "")
        else:
            row.set_subtitle(GLib.markup_escape_text(value))

    def copy(self, row: Adw.ActionRow) -> None:
        if not row.value:
            return
        Gdk.Display.get_default().get_clipboard().set(row.value)
        self.toast(f"{row.get_title()} copied")

    def shown(self) -> None:
        self.sync()

    def sync(self) -> None:
        self.sync_sharing()
        if self.switch is None or self.torn_down:
            return
        on = self.rdp.get_boolean("enable")
        if self.switch.get_active() != on:
            self.switch.handler_block(self.switch_handler)
            self.switch.set_active(on)
            self.switch.handler_unblock(self.switch_handler)
        self.switch.set_subtitle("On. Others can connect while you’re signed in" if on else "Off")
        for row in (self.control, self.address, self.username, self.password, self.fingerprint):
            row.set_visible(on)
        self.firewall.set_visible(on and firewall_on() and not FIREWALL_MARK.exists())
        self.still_open.set_visible(not on and FIREWALL_MARK.exists())
        if not on:
            return
        cert_path = self.rdp.get_string("tls-cert")
        self.sync_turn = getattr(self, "sync_turn", 0) + 1
        turn = self.sync_turn

        def look() -> None:
            # The keyring and openssl can take a moment (the keyring may even ask to be
            # unlocked): never on the window's own thread.
            creds = load_credentials()
            cert = Path(cert_path) if cert_path else None
            print_ = fingerprint(cert) if cert and cert.exists() else ""
            ip = local_address()
            GLib.idle_add(lambda: (self.show_details(turn, creds, print_, ip), False)[1])

        threading.Thread(target=look, daemon=True).start()

    def show_details(self, turn: int, creds: tuple[str, str] | None, print_: str, ip: str | None) -> None:
        if self.torn_down or turn != self.sync_turn:
            return
        host = GLib.get_host_name()
        self.set_row(self.address, f"{host}.local" + (f" or {ip}" if ip else ""))
        self.set_row(self.username, creds[0] if creds else "")
        self.set_row(self.password, creds[1] if creds else "")
        self.fingerprint.set_subtitle(print_)

    def on_switch(self, row: Adw.SwitchRow, _pspec) -> None:
        if self.busy:
            return
        if row.get_active():
            self.turn_on()
        else:
            self.turn_off()

    def service(self, action: str, done=None) -> None:
        """systemctl --user enable/disable --now gnome-remote-desktop, in the background."""
        argv = ["systemctl", "--user", action, "--now", SERVICE]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as exc:
            if done:
                done(False, exc.message)
            return

        def finished(p: Gio.Subprocess, result: Gio.AsyncResult) -> None:
            try:
                _ok, _out, err = p.communicate_utf8_finish(result)
            except GLib.Error as exc:
                err = exc.message
            if done and not self.torn_down:
                done(p.get_successful(), (err or "").strip())

        proc.communicate_utf8_async(None, None, finished)

    def turn_on(self) -> None:
        self.busy = True
        self.switch.set_subtitle("Turning on…")

        def prepare() -> None:
            # A new key takes a moment to make: never on the window's own thread.
            made = make_certificate()
            creds = load_credentials()
            if creds is None:
                creds = (GLib.get_user_name(), new_password())
                if not store_credentials(*creds):
                    creds = None
            GLib.idle_add(lambda: (self.prepared(made, creds), False)[1])

        threading.Thread(target=prepare, daemon=True).start()

    def prepared(self, made: tuple[Path, Path] | None, creds: tuple[str, str] | None) -> None:
        if self.torn_down:
            return
        if made is None or creds is None:
            self.busy = False
            self.switch.set_active(False)
            self.toast("Remote desktop couldn’t be set up: " + ("no certificate could be made" if made is None
                                                                 else "your keyring couldn’t keep the password"))
            self.sync()
            return
        self.rdp.set_string("tls-cert", str(made[0]))
        self.rdp.set_string("tls-key", str(made[1]))
        self.rdp.set_value("port", GLib.Variant("q", PORT))
        if self.rdp.props.settings_schema.has_key("negotiate-port"):
            self.rdp.set_boolean("negotiate-port", False)  # always 3389, the port the firewall allows
        self.rdp.set_boolean("enable", True)

        def started(ok: bool, err: str) -> None:
            self.busy = False
            if not ok:
                self.rdp.set_boolean("enable", False)
                self.toast(f"Remote desktop didn’t start: {err.splitlines()[-1] if err else 'try again'}")
            self.sync()
            if ok and firewall_on() and not FIREWALL_MARK.exists():
                self.allow_firewall()

        self.service("enable", started)

    def turn_off(self) -> None:
        self.busy = True
        self.rdp.set_boolean("enable", False)

        def stopped(_ok: bool, _err: str) -> None:
            self.busy = False
            self.sync()

        # The firewall rule stays until someone closes it (it needs an administrator, and others on this
        # computer may still use remote desktop); the page offers to close it while it's off.
        self.service("disable", stopped)

    def allow_firewall(self) -> None:
        self.window.helper.run(["remote-desktop-on"], "Remote desktop allowed from your local network",
                               on_done=lambda _ok: self.sync())

    def close_firewall(self) -> None:
        self.window.helper.run(["remote-desktop-off"], "Remote desktop is no longer let through the firewall",
                               on_done=lambda _ok: self.sync())

    def change_password(self) -> None:
        creds = load_credentials()
        username = creds[0] if creds else GLib.get_user_name()
        if store_credentials(username, new_password()):
            self.toast("New password. Anyone connected keeps going until they disconnect")
        else:
            self.toast("Your keyring couldn’t keep the new password")
        self.sync()
