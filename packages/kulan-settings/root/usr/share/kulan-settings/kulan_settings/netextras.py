"""The rest of Wi-Fi and network, built into Kulan Settings: enterprise and
older (WEP) Wi-Fi, hidden networks, a hotspot, VPNs, the proxy and the
connection's details. Uses NetworkManager (libnm) like the Wi-Fi page."""
from __future__ import annotations

import secrets
import shutil
import string
from typing import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from .common import has_key, settings

try:
    import gi

    gi.require_version("NM", "1.0")
    from gi.repository import NM
except (ImportError, ValueError):
    NM = None

HOTSPOT_ID = "Kulan Hotspot"
PROXY = "org.gnome.system.proxy"


def entry_row(title: str, password: bool = False, text: str = "") -> Adw.EntryRow:
    row = Adw.PasswordEntryRow(title=title) if password else Adw.EntryRow(title=title)
    row.set_text(text)
    return row


def form_dialog(parent: Gtk.Widget, heading: str, body: str, rows: list[Gtk.Widget], action: str,
                ready: Callable[[], bool], done: Callable[[], None]) -> Adw.AlertDialog:
    """A dialog with a list of rows and an action button that's enabled once
    ready() says the answers are complete."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
    for row in rows:
        box.append(row)
    dialog.set_extra_child(box)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action)
    dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_close_response("cancel")

    def check(*_a) -> None:
        dialog.set_response_enabled("go", ready())
    for row in rows:
        for signal in ("changed", "notify::selected", "notify::active"):
            try:
                row.connect(signal, check)
            except TypeError:
                continue
    check()
    dialog.connect("response", lambda _d, r: done() if r == "go" else None)
    dialog.present(parent)
    return dialog


def wifi_connection(ssid: str, hidden: bool = False) -> "NM.SimpleConnection":
    connection = NM.SimpleConnection.new()
    general = NM.SettingConnection.new()
    general.set_property(NM.SETTING_CONNECTION_ID, ssid)
    general.set_property(NM.SETTING_CONNECTION_UUID, NM.utils_uuid_generate())
    general.set_property(NM.SETTING_CONNECTION_TYPE, "802-11-wireless")
    connection.add_setting(general)
    wireless = NM.SettingWireless.new()
    wireless.set_property(NM.SETTING_WIRELESS_SSID, GLib.Bytes.new(ssid.encode("utf-8")))
    if hidden:
        wireless.set_property(NM.SETTING_WIRELESS_HIDDEN, True)
    connection.add_setting(wireless)
    return connection


def add_password(connection, kind: str, password: str) -> None:
    security = NM.SettingWirelessSecurity.new()
    if kind == "wep":
        security.set_property(NM.SETTING_WIRELESS_SECURITY_KEY_MGMT, "none")
        security.set_property(NM.SETTING_WIRELESS_SECURITY_WEP_KEY_TYPE, NM.WepKeyType.KEY
                              if len(password) in (5, 10, 13, 26) else NM.WepKeyType.PASSPHRASE)
        security.set_property(NM.SETTING_WIRELESS_SECURITY_WEP_KEY0, password)
    else:
        security.set_property(NM.SETTING_WIRELESS_SECURITY_KEY_MGMT, "sae" if kind == "sae" else "wpa-psk")
        security.set_property(NM.SETTING_WIRELESS_SECURITY_PSK, password)
    connection.add_setting(security)


def add_enterprise(connection, method: str, identity: str, password: str, anonymous: str, domain: str) -> None:
    security = NM.SettingWirelessSecurity.new()
    security.set_property(NM.SETTING_WIRELESS_SECURITY_KEY_MGMT, "wpa-eap")
    connection.add_setting(security)
    eap = NM.Setting8021x.new()
    outer, inner = {"peap": ("peap", "mschapv2"), "ttls-pap": ("ttls", "pap"),
                    "ttls-mschapv2": ("ttls", "mschapv2")}[method]
    eap.set_property(NM.SETTING_802_1X_EAP, [outer])
    eap.set_property(NM.SETTING_802_1X_PHASE2_AUTH, inner)
    eap.set_property(NM.SETTING_802_1X_IDENTITY, identity)
    eap.set_property(NM.SETTING_802_1X_PASSWORD, password)
    if anonymous:
        eap.set_property(NM.SETTING_802_1X_ANONYMOUS_IDENTITY, anonymous)
    if domain:
        # Check the server's certificate against the computer's trusted list,
        # and that it belongs to this organisation.
        eap.set_property(NM.SETTING_802_1X_SYSTEM_CA_CERTS, True)
        eap.set_property(NM.SETTING_802_1X_DOMAIN_SUFFIX_MATCH, domain)
    connection.add_setting(eap)


class NetworkExtras:
    """Adds the extra groups to the Wi-Fi page once NetworkManager is ready."""

    def __init__(self, page) -> None:
        self.page = page
        self.proxy = settings(PROXY)
        self.vpn_group = page.add_group("VPN", "Private connections to your work or a VPN service.")
        self.vpn_rows: list[Gtk.Widget] = []
        self.hotspot_group = page.add_group("Hotspot", "Share this computer’s internet over Wi-Fi.")
        self.hotspot_switch = Adw.SwitchRow(title="Hotspot")
        self.hotspot_switch.connect("notify::active", self.on_hotspot)
        self.hotspot_details = Adw.ActionRow(title="Network name and password", subtitle_selectable=True,
                                             visible=False)
        self.hotspot_group.add(self.hotspot_switch)
        self.hotspot_group.add(self.hotspot_details)
        self.proxy_group = page.add_group("Proxy")
        self.build_proxy()
        more = self.more_group = page.add_group()
        hidden = Adw.ActionRow(title="Join a hidden network…", activatable=True)
        hidden.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        hidden.connect("activated", lambda _r: self.hidden_dialog())
        more.add(hidden)
        self.syncing = False
        self.hotspot_password = ""

    # --- called by the Wi-Fi page ------------------------------------------------------
    @property
    def client(self):
        return self.page.client

    def refresh(self) -> None:
        self.fill_vpns()
        self.sync_hotspot()
        self.more_group.set_visible(self.page.wifi_device() is not None)

    def join_special(self, device, net: dict) -> None:
        """Enterprise (work, school, eduroam) and WEP networks."""
        if net["security"] == "wep":
            self.password_dialog(device, net["ssid"], "wep", net["ap"].get_path(), hidden=False)
        else:
            self.enterprise_dialog(device, net["ssid"], net["ap"].get_path(), hidden=False)

    # --- joining ---------------------------------------------------------------------
    def activate_new(self, connection, device, ap_path: str | None, name: str) -> None:
        self.client.add_and_activate_connection_async(
            connection, device, ap_path, None,
            self.page.finish_quietly("add_and_activate_connection_finish", f"Couldn’t join {name}"))
        self.page.toast(f"Joining {name}…")

    def password_dialog(self, device, ssid: str, kind: str, ap_path: str | None, hidden: bool) -> None:
        password = entry_row("Password", password=True)

        def ready() -> bool:
            text = password.get_text()
            return len(text) >= 5 if kind == "wep" else 8 <= len(text) <= 63

        def go() -> None:
            connection = wifi_connection(ssid, hidden)
            add_password(connection, kind, password.get_text())
            self.activate_new(connection, device, ap_path, ssid)
        body = "This network uses old WEP security, which is easy to break. Use it only if you must." \
            if kind == "wep" else "Enter the network’s password."
        form_dialog(self.page.window, f"Join “{ssid}”", body, [password], "Join", ready, go)

    def enterprise_dialog(self, device, ssid: str, ap_path: str | None, hidden: bool) -> None:
        methods = [("peap", "PEAP (most work and school networks)"), ("ttls-pap", "TTLS with PAP"),
                   ("ttls-mschapv2", "TTLS with MSCHAPv2")]
        method = Adw.ComboRow(title="Sign-in method", model=Gtk.StringList.new([m[1] for m in methods]))
        identity = entry_row("User name (often your email)")
        password = entry_row("Password", password=True)
        anonymous = entry_row("Anonymous identity (optional)")
        domain = entry_row("Server domain (for example, radius.example.org)")
        unchecked = Adw.SwitchRow(title="Connect without checking the server",
                                  subtitle="Unsafe. A fake network with the same name could get your password.")

        def go() -> None:
            connection = wifi_connection(ssid, hidden)
            add_enterprise(connection, methods[method.get_selected()][0], identity.get_text().strip(),
                           password.get_text(), anonymous.get_text().strip(), domain.get_text().strip())
            self.activate_new(connection, device, ap_path, ssid)
        form_dialog(self.page.window, f"Join “{ssid}”",
                    "Sign in with your work or school account. The server domain lets the computer check it’s "
                    "really your network before sending your password.",
                    [method, identity, password, anonymous, domain, unchecked], "Join",
                    lambda: bool(identity.get_text().strip() and password.get_text()
                                 and (domain.get_text().strip() or unchecked.get_active())), go)

    def hidden_dialog(self) -> None:
        device = self.page.wifi_device()
        if device is None or self.client is None:
            self.page.toast("Wi-Fi isn’t available")
            return
        kinds = [("psk", "Password (WPA2 or WPA3)"), ("open", "None"), ("enterprise", "Work or school sign-in"),
                 ("wep", "WEP (old)")]
        ssid = entry_row("Network name")
        kind = Adw.ComboRow(title="Security", model=Gtk.StringList.new([k[1] for k in kinds]))

        def go() -> None:
            name, chosen = ssid.get_text().strip(), kinds[kind.get_selected()][0]
            if chosen == "open":
                self.activate_new(wifi_connection(name, hidden=True), device, None, name)
            elif chosen == "enterprise":
                self.enterprise_dialog(device, name, None, hidden=True)
            else:
                self.password_dialog(device, name, chosen, None, hidden=True)
        form_dialog(self.page.window, "Join a hidden network", "Networks that don’t show their name.",
                    [ssid, kind], "Next", lambda: bool(ssid.get_text().strip()), go)

    # --- VPN ---------------------------------------------------------------------------
    def vpn_connections(self) -> list:
        if self.client is None:
            return []
        return sorted((c for c in self.client.get_connections() if c.get_connection_type() in ("vpn", "wireguard")),
                      key=lambda c: (c.get_id() or "").lower())

    def active_for(self, connection):
        for active in self.client.get_active_connections():
            if active.get_uuid() == connection.get_uuid():
                return active
        return None

    def fill_vpns(self) -> None:
        for row in self.vpn_rows:
            self.vpn_group.remove(row)
        self.vpn_rows = []
        for connection in self.vpn_connections():
            active = self.active_for(connection)
            row = Adw.ActionRow(title=GLib.markup_escape_text(connection.get_id() or "VPN"),
                                subtitle="WireGuard" if connection.get_connection_type() == "wireguard" else "VPN")
            switch = Gtk.Switch(active=active is not None, valign=Gtk.Align.CENTER)
            switch.update_property([Gtk.AccessibleProperty.LABEL], [connection.get_id() or "VPN"])
            switch.connect("notify::active", lambda s, _p, c=connection: self.toggle_vpn(c, s.get_active()))
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat", "circular"], tooltip_text="Remove")
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {connection.get_id()}"])
            remove.connect("clicked", lambda _b, c=connection: self.remove_vpn(c))
            row.add_suffix(remove)
            row.add_suffix(switch)
            row.set_activatable_widget(switch)
            self.vpn_group.add(row)
            self.vpn_rows.append(row)
        add = Adw.ActionRow(title="Add a VPN from a file…", subtitle="WireGuard (.conf) or OpenVPN (.ovpn)",
                            activatable=True)
        add.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
        add.connect("activated", lambda _r: self.import_vpn())
        self.vpn_group.add(add)
        self.vpn_rows.append(add)

    def toggle_vpn(self, connection, on: bool) -> None:
        active = self.active_for(connection)
        if on and active is None:
            self.client.activate_connection_async(connection, None, None, None,
                                                  self.page.finish_quietly("activate_connection_finish",
                                                                           "Couldn’t connect the VPN"))
        elif not on and active is not None:
            self.client.deactivate_connection_async(active, None,
                                                    self.page.finish_quietly("deactivate_connection_finish",
                                                                             "Couldn’t disconnect the VPN"))

    def remove_vpn(self, connection) -> None:
        dialog = Adw.AlertDialog(heading=f"Remove “{connection.get_id()}”?",
                                 body="You’d need its file again to add it back.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: r == "remove" and connection.delete_async(
            None, self.page.finish_quietly("delete_finish", "Couldn’t remove it")))
        dialog.present(self.page.window)

    def import_vpn(self) -> None:
        if shutil.which("nmcli") is None:
            self.page.toast("VPNs can’t be added on this computer: its network tools are missing")
            return
        dialog = Gtk.FileDialog(title="Choose a VPN file")
        files = Gtk.FileFilter(name="VPN files (.conf, .ovpn)")
        files.add_pattern("*.conf")
        files.add_pattern("*.ovpn")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(files)
        dialog.set_filters(filters)

        def picked(d, result) -> None:
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            path = file.get_path() if file else None
            if not path:
                return
            kind = "openvpn" if path.lower().endswith(".ovpn") else "wireguard"
            try:  # without waiting: the window keeps working while NetworkManager reads it
                proc = Gio.Subprocess.new(["nmcli", "connection", "import", "type", kind, "file", path],
                                          Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            except GLib.Error:
                self.page.toast("Couldn’t add the VPN. Try again")
                return

            def imported(p, res) -> None:
                try:
                    _ok, _out, err = p.communicate_utf8_finish(res)
                    code = p.get_exit_status() if p.get_if_exited() else 1
                except GLib.Error:
                    code, err = 1, ""
                if self.page.torn_down:
                    return
                err = (err or "").lower()
                if code == 0:
                    self.page.toast("VPN added")
                elif kind == "openvpn" and "plugin" in err:
                    self.page.toast("OpenVPN support is missing. Update Kulan OS in Kulan Settings › Software "
                                    "Update, then try again")
                elif "permission" in err or "not authorized" in err:
                    self.page.toast("Not added: an administrator’s password is needed")
                else:
                    self.page.toast("This VPN file couldn’t be read. Check it’s the file your VPN provider gave you")
                self.page.queue_refresh()
            proc.communicate_utf8_async(None, None, imported)
        dialog.open(self.page.window, None, picked)

    # --- hotspot -------------------------------------------------------------------------
    def hotspot_connection(self):
        for connection in self.client.get_connections() if self.client else []:
            if connection.get_id() == HOTSPOT_ID:
                return connection
        return None

    def sync_hotspot(self) -> None:
        device = self.page.wifi_device()
        self.hotspot_group.set_visible(device is not None)
        connection = self.hotspot_connection()
        active = self.active_for(connection) if connection is not None else None
        self.syncing = True
        self.hotspot_switch.set_active(active is not None)
        self.syncing = False
        if connection is not None and active is not None:
            wireless = connection.get_setting_wireless()
            name = (wireless.get_ssid().get_data() or b"").decode("utf-8", "replace") if wireless else ""
            self.hotspot_details.set_subtitle(f"{name} · password {self.hotspot_password}" if self.hotspot_password
                                              else f"{name} · Restart the hotspot to see a new password")
            self.hotspot_details.set_visible(True)
        else:
            self.hotspot_details.set_visible(False)

    def on_hotspot(self, row: Adw.SwitchRow, _pspec) -> None:
        if self.syncing or self.client is None:
            return
        device = self.page.wifi_device()
        if device is None:
            return
        existing = self.hotspot_connection()
        if not row.get_active():
            active = self.active_for(existing) if existing else None
            if active is not None:
                self.client.deactivate_connection_async(active, None,
                                                        self.page.finish_quietly("deactivate_connection_finish",
                                                                                 "Couldn’t stop the hotspot"))
            return
        password = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(12))
        name = f"{GLib.get_host_name()} hotspot"[:32]
        if existing is not None:
            existing.delete_async(None, None)
        connection = wifi_connection(name)
        general = connection.get_setting_connection()
        general.set_property(NM.SETTING_CONNECTION_ID, HOTSPOT_ID)
        general.set_property(NM.SETTING_CONNECTION_AUTOCONNECT, False)
        wireless = connection.get_setting_wireless()
        wireless.set_property(NM.SETTING_WIRELESS_MODE, "ap")
        add_password(connection, "psk", password)
        ipv4 = NM.SettingIP4Config.new()
        ipv4.set_property(NM.SETTING_IP_CONFIG_METHOD, "shared")
        connection.add_setting(ipv4)
        self.client.add_and_activate_connection_async(
            connection, device, None, None,
            self.page.finish_quietly("add_and_activate_connection_finish", "Couldn’t start the hotspot"))
        self.hotspot_password = password
        self.hotspot_details.set_subtitle(f"{name} · password {password}")
        self.hotspot_details.set_visible(True)
        self.page.toast("Starting hotspot. Wi-Fi leaves other networks while it’s on.")

    # --- proxy -----------------------------------------------------------------------------
    def build_proxy(self) -> None:
        if self.proxy is None or not has_key(self.proxy, "mode"):
            self.proxy_group.set_visible(False)
            return
        modes = [("none", "Off"), ("auto", "Automatic"), ("manual", "Manual")]
        mode = Adw.ComboRow(title="Proxy", model=Gtk.StringList.new([m[1] for m in modes]))
        values = [m[0] for m in modes]
        current = self.proxy.get_string("mode")
        mode.set_selected(values.index(current) if current in values else 0)
        self.proxy_group.add(mode)
        auto = entry_row("Configuration URL", text=self.proxy.get_string("autoconfig-url"))
        auto.set_show_apply_button(True)
        auto.connect("apply", lambda r: self.proxy.set_string("autoconfig-url", r.get_text().strip()))
        self.proxy_group.add(auto)
        manual_rows = []
        for scheme, label in (("http", "Web (HTTP)"), ("https", "Secure web (HTTPS)"), ("socks", "SOCKS")):
            child = settings(f"{PROXY}.{scheme}")
            if child is None:
                continue
            host = entry_row(f"{label} proxy", text=child.get_string("host"))
            host.set_show_apply_button(True)
            port = Adw.SpinRow.new_with_range(0, 65535, 1)
            port.set_title(f"{label} port")
            port.set_value(child.get_int("port"))
            host.connect("apply", lambda r, c=child: c.set_string("host", r.get_text().strip()))
            port.connect("notify::value", lambda r, _p, c=child: c.set_int("port", int(r.get_value())))
            for row in (host, port):
                self.proxy_group.add(row)
                manual_rows.append(row)
        ignore = entry_row("Don’t use the proxy for", text=", ".join(self.proxy.get_strv("ignore-hosts")))
        ignore.set_show_apply_button(True)
        ignore.connect("apply", lambda r: self.proxy.set_strv(
            "ignore-hosts", [h.strip() for h in r.get_text().split(",") if h.strip()]))
        self.proxy_group.add(ignore)
        manual_rows.append(ignore)

        def show(*_a) -> None:
            chosen = values[mode.get_selected()]
            auto.set_visible(chosen == "auto")
            for row in manual_rows:
                row.set_visible(chosen == "manual")
        mode.connect("notify::selected", lambda r, _p: (self.proxy.set_string("mode", values[r.get_selected()]),
                                                        show()))
        show()


def details_text(device) -> str:
    """Addresses, gateway and DNS of a connected device, one per line."""
    lines = []
    ip4 = device.get_ip4_config()
    if ip4 is not None:
        for address in ip4.get_addresses():
            lines.append(f"IPv4 {address.get_address()}/{address.get_prefix()}")
        if ip4.get_gateway():
            lines.append(f"Router {ip4.get_gateway()}")
        dns = ip4.get_nameservers()
        if dns:
            lines.append("DNS " + ", ".join(dns))
    ip6 = device.get_ip6_config()
    if ip6 is not None:
        for address in ip6.get_addresses():
            if not address.get_address().startswith("fe80"):
                lines.append(f"IPv6 {address.get_address()}")
    hw = device.get_hw_address() if hasattr(device, "get_hw_address") else None
    if hw:
        lines.append(f"Hardware address {hw}")
    return "\n".join(lines)
