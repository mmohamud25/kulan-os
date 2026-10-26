"""Sharing: remote desktop, natively (GNOME Remote Desktop, RDP), and a link
to file and media sharing in GNOME Settings.

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
never from the internet. Remote help from an organisation (Kulan Fleet) is
separate, and is asked for each time.
"""
from __future__ import annotations

import re
import secrets
import shutil
import socket
import subprocess
import threading
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .common import Page, open_gnome_panel, settings

SCHEMA_ID = "org.gnome.desktop.remote-desktop.rdp"
CREDENTIALS_SCHEMA = "org.gnome.RemoteDesktop.RdpCredentials"
PORT = 3389
FIREWALL_MARK = Path("/var/lib/kulan/remote-desktop-firewall")
SERVICE = "gnome-remote-desktop.service"
SERVICE_FILE = Path("/usr/lib/systemd/user") / SERVICE
TLS_DIR = Path(GLib.get_user_data_dir()) / "gnome-remote-desktop"
PASSWORD_LETTERS = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRTUVWXYZ2346789"


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


def firewall_on() -> bool:
    try:
        return "ENABLED=yes" in Path("/etc/ufw/ufw.conf").read_text().split()
    except OSError:
        return False


class SharingPage(Page):
    title = "Sharing"
    icon = "org.gnome.Settings-sharing-symbolic|emblem-shared-symbolic"
    keywords = ("sharing", "remote desktop", "rdp", "remote", "screen sharing", "file sharing", "media sharing",
                "vnc", "control")

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
            self.watch(self.rdp, "changed::enable", lambda *_a: self.sync())

        more = self.add_group("More sharing")
        row = Adw.ActionRow(title="File and media sharing",
                            subtitle="Share your Public folder and your music, photos and videos on this network",
                            activatable=True, subtitle_lines=0)
        row.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        row.update_property([Gtk.AccessibleProperty.LABEL], ["File and media sharing (opens GNOME Settings)"])
        row.connect("activated", lambda _r: open_gnome_panel("sharing"))
        more.add(row)
        self.sync()

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
        if self.switch is None or self.torn_down:
            return
        on = self.rdp.get_boolean("enable")
        if self.switch.get_active() != on:
            self.switch.handler_block(self.switch_handler)
            self.switch.set_active(on)
            self.switch.handler_unblock(self.switch_handler)
        self.switch.set_subtitle("On. Others can connect while you’re signed in" if on else "Off")
        creds = load_credentials() if on else None
        host = GLib.get_host_name()
        ip = local_address()
        self.set_row(self.address, f"{host}.local" + (f" or {ip}" if ip else "") if on else "")
        self.set_row(self.username, creds[0] if creds else "")
        self.set_row(self.password, creds[1] if creds else "")
        cert = Path(self.rdp.get_string("tls-cert")) if self.rdp.get_string("tls-cert") else None
        self.fingerprint.set_subtitle(fingerprint(cert) if on and cert and cert.exists() else "")
        for row in (self.control, self.address, self.username, self.password, self.fingerprint):
            row.set_visible(on)
        self.firewall.set_visible(on and firewall_on() and not FIREWALL_MARK.exists())

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
        self.rdp.set_uint("port", PORT)
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

        # The firewall rule stays (local networks only): nothing listens on it while this is off.
        self.service("disable", stopped)

    def allow_firewall(self) -> None:
        self.window.helper.run(["remote-desktop-on"], "Remote desktop allowed from your local network",
                               on_done=lambda _ok: self.sync())

    def change_password(self) -> None:
        creds = load_credentials()
        username = creds[0] if creds else GLib.get_user_name()
        if store_credentials(username, new_password()):
            self.toast("New password. Anyone connected keeps going until they disconnect")
        else:
            self.toast("Your keyring couldn’t keep the new password")
        self.sync()
