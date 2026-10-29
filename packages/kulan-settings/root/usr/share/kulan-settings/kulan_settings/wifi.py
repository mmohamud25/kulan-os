"""Wi-Fi and network: turn Wi-Fi on or off, see nearby networks and join one,
and see the wired connection. Uses NetworkManager (libnm). Enterprise and
hidden networks, VPN, hotspot and proxy are in netextras.py."""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, plain_error
from .netextras import NetworkExtras, details_text

try:
    import gi

    gi.require_version("NM", "1.0")
    from gi.repository import NM
except (ImportError, ValueError):  # libnm's introspection data isn't installed
    NM = None

NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"


def ssid_text(ssid) -> str:
    """An access point's SSID (GLib.Bytes) as text; '' for hidden networks."""
    if ssid is None:
        return ""
    data = ssid.get_data() if hasattr(ssid, "get_data") else bytes(ssid)
    if not data:
        return ""
    return NM.utils_ssid_to_utf8(data) if NM is not None else data.decode("utf-8", "replace")


# NM's 80211ApSecurityFlags / 80211ApFlags (their GI names start with a digit,
# so Python can't reach them as attributes); values from nm-dbus-interface.h.
KEY_MGMT_PSK = 0x100
KEY_MGMT_802_1X = 0x200
KEY_MGMT_SAE = 0x400
KEY_MGMT_OWE = 0x800
KEY_MGMT_EAP_SUITE_B_192 = 0x2000
AP_FLAGS_PRIVACY = 0x1


def security_kind(flags: int, wpa: int, rsn: int) -> str:
    """'open', 'psk' (a password), 'sae' (WPA3 password), 'wep' or 'enterprise'."""
    key_mgmt = int(wpa) | int(rsn)
    if key_mgmt & (KEY_MGMT_802_1X | KEY_MGMT_EAP_SUITE_B_192):
        return "enterprise"
    if key_mgmt & KEY_MGMT_SAE and not key_mgmt & KEY_MGMT_PSK:
        return "sae"
    if key_mgmt & KEY_MGMT_PSK:
        return "psk"
    if key_mgmt & KEY_MGMT_OWE:
        return "open"  # encrypted without a password
    if int(flags) & AP_FLAGS_PRIVACY:
        return "wep"
    return "open"


def strongest_per_network(points: list[dict]) -> list[dict]:
    """One entry per network name (the strongest access point), named networks
    only, the connected one first, then by signal."""
    best: dict[str, dict] = {}
    for point in points:
        name = point["ssid"]
        if not name:
            continue
        if name not in best or point["active"] or (not best[name]["active"] and point["strength"] > best[name]["strength"]):
            best[name] = point
    return sorted(best.values(), key=lambda p: (not p["active"], -p["strength"], p["ssid"].lower()))


def signal_icon(strength: int) -> str:
    level = "excellent" if strength > 75 else "good" if strength > 50 else "ok" if strength > 25 else "weak"
    return f"network-wireless-signal-{level}-symbolic"


class WifiPage(Page):
    title = "Wi-Fi and network"
    icon = "network-wireless-symbolic"
    keywords = ("wifi", "wi-fi", "wireless", "network", "internet", "ethernet", "wired", "password", "hotspot",
                "vpn", "wireguard", "openvpn", "proxy", "hidden network", "eduroam", "enterprise", "ip address", "dns")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.client = None
        self.rows: list[Gtk.Widget] = []
        self.refresh_id = 0

        self.device_watches: list[tuple] = []
        if NM is None:
            self.unavailable("Wi-Fi settings need the gir1.2-nm-1.0 package.")
            return
        self.wifi_group = self.add_group("Wi-Fi")
        self.wifi_switch = Adw.SwitchRow(title="Wi-Fi")
        self.wifi_switch.connect("notify::active", self.on_switch)
        self.wifi_group.add(self.wifi_switch)
        self.status_row = Adw.ActionRow(title="Not connected")
        self.status_details = Gtk.Button(label="Details", valign=Gtk.Align.CENTER, css_classes=["flat"],
                                         visible=False)
        self.status_details.connect("clicked", lambda _b: self.wifi_device() and self.show_details(self.wifi_device()))
        self.status_row.add_suffix(self.status_details)
        self.wifi_group.add(self.status_row)

        self.networks = self.add_group("Networks nearby")
        scan = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Look for networks", css_classes=["flat"])
        scan.update_property([Gtk.AccessibleProperty.LABEL], ["Look for networks"])
        scan.connect("clicked", lambda _b: self.scan())
        self.networks.set_header_suffix(scan)

        self.wired_group = self.add_group("Wired")

        self.extras = None
        self.wifi_group.set_visible(False)
        self.networks.set_visible(False)
        self.wired_group.set_visible(False)
        NM.Client.new_async(None, self.got_client)

    def got_client(self, _source, result) -> None:
        if self.torn_down:
            return
        try:
            self.client = NM.Client.new_finish(result)
        except GLib.Error as exc:
            print(f"kulan-settings: NetworkManager: {exc.message}")
            self.unavailable("NetworkManager isn’t running.")
            return
        if not self.client.get_nm_running():
            self.unavailable("NetworkManager isn’t running.")
            return
        self.extras = NetworkExtras(self)
        self.watch(self.client, "connection-added", lambda *_a: self.queue_refresh())
        self.watch(self.client, "connection-removed", lambda *_a: self.queue_refresh())
        self.watch(self.client, "active-connection-added", lambda *_a: self.queue_refresh())
        self.watch(self.client, "active-connection-removed", lambda *_a: self.queue_refresh())
        self.watch(self.client, "notify::wireless-enabled", lambda *_a: self.queue_refresh())
        self.watch(self.client, "notify::wireless-hardware-enabled", lambda *_a: self.queue_refresh())
        self.watch(self.client, "notify::primary-connection", lambda *_a: self.queue_refresh())
        self.watch(self.client, "device-added", lambda *_a: self.rewatch_devices())
        self.watch(self.client, "device-removed", lambda *_a: self.rewatch_devices())
        self.rewatch_devices()
        self.scan()

    def unavailable(self, why: str) -> None:
        for name in ("wifi_group", "networks", "wired_group"):
            if hasattr(self, name):
                getattr(self, name).set_visible(False)
        self.add_group("Wi-Fi", why)

    # --- devices ---------------------------------------------------------------------

    def wifi_device(self):
        for device in self.client.get_devices() if self.client else []:
            if isinstance(device, NM.DeviceWifi) and device.get_managed():
                return device
        return None

    def wired_devices(self) -> list:
        return [d for d in (self.client.get_devices() if self.client else [])
                if isinstance(d, NM.DeviceEthernet) and d.get_managed()]

    def rewatch_devices(self) -> None:
        for obj, handler in self.device_watches:
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self.device_watches = []
        for device in self.client.get_devices():
            for signal in ("state-changed", "notify::active-access-point", "access-point-added",
                           "access-point-removed"):
                if signal.startswith("access-point") and not isinstance(device, NM.DeviceWifi):
                    continue
                if signal == "notify::active-access-point" and not isinstance(device, NM.DeviceWifi):
                    continue
                self.device_watches.append((device, device.connect(signal, lambda *_a: self.queue_refresh())))
        self.queue_refresh()

    def teardown(self) -> None:
        for obj, handler in getattr(self, "device_watches", []):
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self.device_watches = []
        if self.refresh_id:
            GLib.source_remove(self.refresh_id)
            self.refresh_id = 0
        super().teardown()

    def shown(self) -> None:
        if self.client is not None:
            self.scan()

    def scan(self) -> None:
        device = self.wifi_device()
        if device is not None and self.client.wireless_get_enabled():
            device.request_scan_async(None, self.scan_done)

    @staticmethod
    def scan_done(device, result) -> None:
        try:
            device.request_scan_finish(result)
        except GLib.Error:
            pass  # scanning too often, or Wi-Fi off: the list still updates

    # --- showing ---------------------------------------------------------------------

    def queue_refresh(self) -> None:
        if self.client is not None and not self.refresh_id:
            self.refresh_id = GLib.timeout_add(250, self.refresh)

    def refresh(self) -> bool:
        self.refresh_id = 0
        for group, row in self.rows:
            group.remove(row)
        self.rows = []
        device = self.wifi_device()
        enabled = self.client.wireless_get_enabled()
        hardware = self.client.wireless_hardware_get_enabled()

        self.wifi_group.set_visible(True)
        self.wifi_switch.handler_block_by_func(self.on_switch)
        self.wifi_switch.set_active(enabled and hardware and device is not None)
        self.wifi_switch.handler_unblock_by_func(self.on_switch)
        self.wifi_switch.set_sensitive(device is not None and hardware)
        if device is None:
            self.wifi_switch.set_subtitle("No Wi-Fi adapter found")
        elif not hardware:
            self.wifi_switch.set_subtitle("Turned off with a switch or key on the computer")
        else:
            self.wifi_switch.set_subtitle("")

        active_ap = device.get_active_access_point() if device is not None else None
        self.status_details.set_visible(False)
        if active_ap is not None:
            self.status_row.set_title(f"Connected to {ssid_text(active_ap.get_ssid()) or 'a hidden network'}")
            self.status_row.set_subtitle(self.address_text(device))
            self.status_details.set_visible(True)
        elif device is not None and device.get_state() in (NM.DeviceState.PREPARE, NM.DeviceState.CONFIG,
                                                             NM.DeviceState.NEED_AUTH, NM.DeviceState.IP_CONFIG,
                                                             NM.DeviceState.IP_CHECK):
            self.status_row.set_title("Connecting…")
            self.status_row.set_subtitle("")
        else:
            self.status_row.set_title("Not connected" if device is not None else "Wi-Fi isn’t available")
            self.status_row.set_subtitle("")
        self.status_row.set_visible(device is not None)

        show_list = device is not None and enabled and hardware
        self.networks.set_visible(show_list)
        if show_list:
            points = []
            for ap in device.get_access_points():
                points.append({
                    "ap": ap, "ssid": ssid_text(ap.get_ssid()), "strength": ap.get_strength(),
                    "security": security_kind(ap.get_flags(), ap.get_wpa_flags(), ap.get_rsn_flags()),
                    "active": active_ap is not None and ap.get_path() == active_ap.get_path(),
                })
            nets = strongest_per_network(points)
            if not nets:
                self.add_row(self.networks, Adw.ActionRow(title="Looking for networks…"))
            for net in nets[:20]:
                self.add_row(self.networks, self.network_row(device, net))

        wired = self.wired_devices()
        self.wired_group.set_visible(bool(wired))
        for dev in wired:
            state = dev.get_state()
            if state == NM.DeviceState.ACTIVATED:
                title, sub = "Connected", self.address_text(dev)
            elif state == NM.DeviceState.UNAVAILABLE:
                title, sub = "Cable unplugged", ""
            elif NM.DeviceState.PREPARE <= state < NM.DeviceState.ACTIVATED:
                title, sub = "Connecting…", ""
            else:
                title, sub = "Not connected", ""
            row = Adw.ActionRow(title=title, subtitle=sub)
            row.add_prefix(Gtk.Image(icon_name="network-wired-symbolic"))
            if len(wired) > 1:
                row.set_title(f"{dev.get_iface()}: {title}")
            if state == NM.DeviceState.ACTIVATED:
                self.add_details_button(row, dev)
            self.add_row(self.wired_group, row)
        if self.extras is not None:
            self.extras.refresh()
        return GLib.SOURCE_REMOVE

    def add_details_button(self, row: Adw.ActionRow, device) -> None:
        button = Gtk.Button(label="Details", valign=Gtk.Align.CENTER, css_classes=["flat"])
        button.connect("clicked", lambda _b: self.show_details(device))
        row.add_suffix(button)

    def show_details(self, device) -> None:
        dialog = Adw.AlertDialog(heading="Connection details", body=details_text(device) or "No details yet")
        dialog.set_body_use_markup(False)
        dialog.add_response("close", "Close")
        dialog.present(self.window)

    def add_row(self, group: Adw.PreferencesGroup, row: Gtk.Widget) -> None:
        group.add(row)
        self.rows.append((group, row))

    @staticmethod
    def address_text(device) -> str:
        config = device.get_ip4_config()
        if config is None:
            return ""
        addresses = config.get_addresses()
        return f"Address {addresses[0].get_address()}" if addresses else ""

    def network_row(self, device, net: dict) -> Adw.ActionRow:
        row = Adw.ActionRow(title=GLib.markup_escape_text(net["ssid"]), activatable=not net["active"])
        row.add_prefix(Gtk.Image(icon_name=signal_icon(net["strength"])))
        if net["security"] != "open":
            lock = Gtk.Image(icon_name="channel-secure-symbolic", tooltip_text="Needs a password")
            row.add_suffix(lock)
        if net["active"]:
            row.set_subtitle("Connected")
            button = Gtk.Button(label="Disconnect", valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _b: self.disconnect(device))
            row.add_suffix(button)
        else:
            row.connect("activated", lambda _r: self.join(device, net))
        return row

    # --- actions ---------------------------------------------------------------------

    def on_switch(self, row: Adw.SwitchRow, _pspec) -> None:
        self.client.dbus_set_property(NM_PATH, NM_IFACE, "WirelessEnabled", GLib.Variant("b", row.get_active()),
                                      -1, None, self.set_done, None)

    def set_done(self, client, result, _data) -> None:
        try:
            client.dbus_set_property_finish(result)
        except GLib.Error as exc:
            self.toast(f"Couldn’t change Wi-Fi. {plain_error(exc)}")
            self.queue_refresh()

    def disconnect(self, device) -> None:
        device.disconnect_async(None, self.finish_quietly("disconnect_finish", "Couldn’t disconnect"))

    def finish_quietly(self, method: str, failure: str):
        def done(obj, result, *_data) -> None:
            try:
                getattr(obj, method)(result)
            except GLib.Error as exc:
                if not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                    self.toast(f"{failure}. {plain_error(exc)}")
            self.queue_refresh()
        return done

    def saved_connection(self, device, ssid: str):
        """A saved Wi-Fi connection for this network name, if there is one."""
        for conn in device.filter_connections(self.client.get_connections()):
            setting = conn.get_setting_wireless()
            if setting is not None and ssid_text(setting.get_ssid()) == ssid:
                return conn
        return None

    def join(self, device, net: dict) -> None:
        saved = self.saved_connection(device, net["ssid"])
        if saved is not None:
            self.client.activate_connection_async(saved, device, net["ap"].get_path(), None,
                                                  self.finish_quietly("activate_connection_finish",
                                                                      "Couldn’t connect"))
            return
        if net["security"] in ("enterprise", "wep"):
            self.extras.join_special(device, net)
            return
        if net["security"] == "open":
            self.connect_new(device, net, None)
            return
        self.ask_password(device, net)

    def ask_password(self, device, net: dict) -> None:
        dialog = Adw.AlertDialog(heading=f"Join “{net['ssid']}”",
                                 body="Enter the network’s password.")
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Wi-Fi password"])
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("join", "Join")
        dialog.set_response_appearance("join", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("join")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("join", False)

        def changed(_e) -> None:
            length = len(entry.get_text())
            dialog.set_response_enabled("join", 8 <= length <= 63)

        entry.connect("changed", changed)

        def response(_d, answer: str) -> None:
            if answer == "join":
                self.connect_new(device, net, entry.get_text())

        dialog.connect("response", response)
        dialog.present(self.window)
        entry.grab_focus()

    def connect_new(self, device, net: dict, password: str | None) -> None:
        connection = NM.SimpleConnection.new()
        wireless = NM.SettingWireless.new()
        wireless.set_property(NM.SETTING_WIRELESS_SSID, GLib.Bytes.new(net["ssid"].encode("utf-8")))
        connection.add_setting(wireless)
        if password is not None:
            security = NM.SettingWirelessSecurity.new()
            security.set_property(NM.SETTING_WIRELESS_SECURITY_KEY_MGMT,
                                  "sae" if net["security"] == "sae" else "wpa-psk")
            security.set_property(NM.SETTING_WIRELESS_SECURITY_PSK, password)
            connection.add_setting(security)
        self.client.add_and_activate_connection_async(connection, device, net["ap"].get_path(), None,
                                                      self.finish_quietly("add_and_activate_connection_finish",
                                                                          "Couldn’t join the network"))
        self.toast(f"Joining {net['ssid']}…")
