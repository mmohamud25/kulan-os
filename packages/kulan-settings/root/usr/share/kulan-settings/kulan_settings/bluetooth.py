"""Bluetooth: turn it on or off, pair new devices (btpair.py answers the
codes some devices show), and connect, disconnect or forget the devices
you've paired. Talks to BlueZ over D-Bus."""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from .btpair import Agent, nearby_devices
from .common import Page

BLUEZ = "org.bluez"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
RFKILL_NAME = "org.gnome.SettingsDaemon.Rfkill"
RFKILL_PATH = "/org/gnome/SettingsDaemon/Rfkill"

ICONS = {
    "audio-headset": "audio-headset-symbolic", "audio-headphones": "audio-headphones-symbolic",
    "audio-card": "audio-speakers-symbolic", "input-keyboard": "input-keyboard-symbolic",
    "input-mouse": "input-mouse-symbolic", "input-tablet": "input-tablet-symbolic",
    "input-gaming": "input-gaming-symbolic", "phone": "phone-symbolic", "computer": "computer-symbolic",
    "printer": "printer-symbolic", "camera-photo": "camera-photo-symbolic",
}


def device_icon(icon: str | None) -> str:
    return ICONS.get(icon or "", "bluetooth-active-symbolic")


def paired_devices(objects: dict[str, dict[str, dict]]) -> list[dict]:
    """Paired devices from BlueZ's managed objects ({path: {iface: props}}),
    connected ones first, then by name."""
    found = []
    for path, interfaces in objects.items():
        props = interfaces.get(DEVICE)
        if not props or not (props.get("Paired") or props.get("Bonded")):
            continue
        found.append({
            "path": path,
            "name": props.get("Alias") or props.get("Name") or props.get("Address") or "Device",
            "connected": bool(props.get("Connected")),
            "icon": device_icon(props.get("Icon")),
            "battery": interfaces.get("org.bluez.Battery1", {}).get("Percentage"),
        })
    return sorted(found, key=lambda d: (not d["connected"], d["name"].lower()))


class BluetoothPage(Page):
    title = "Bluetooth"
    icon = "bluetooth-active-symbolic"
    keywords = ("bluetooth", "headphones", "airpods", "speaker", "mouse", "keyboard", "pair", "add device",
                "wireless")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.manager = None
        self.rfkill = None
        self.rows: list[tuple] = []
        self.refresh_id = 0
        self.busy: set[str] = set()

        self.main = self.add_group("Bluetooth")
        self.power = Adw.SwitchRow(title="Bluetooth")
        self.power.connect("notify::active", self.on_power)
        self.main.add(self.power)

        self.devices = self.add_group("My devices")
        self.nearby = self.add_group("Nearby devices",
                                     "Put the device in pairing mode (often by holding its Bluetooth or power "
                                     "button), then click it here.")
        self.searching = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        self.searching.set_valign(Gtk.Align.CENTER)
        self.look_again = Gtk.Button(label="Look again", valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.look_again.connect("clicked", lambda _b: self.discover())
        header = Gtk.Box(spacing=6)
        header.append(self.searching)
        header.append(self.look_again)
        self.nearby.set_header_suffix(header)
        self.discovering = False
        self.discovery_timer = 0
        self.agent = None
        self.pairing: str | None = None

        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                                  RFKILL_NAME, RFKILL_PATH, RFKILL_NAME, None, self.got_rfkill)
        Gio.DBusObjectManagerClient.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.NONE,
                                                BLUEZ, "/", None, None, None, self.got_manager)
        self.queue_refresh()

    def got_rfkill(self, _source, result) -> None:
        if self.torn_down:
            return
        try:
            self.rfkill = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            self.rfkill = None
            return
        self.watch(self.rfkill, "g-properties-changed", lambda *_a: self.queue_refresh())
        self.queue_refresh()

    def got_manager(self, _source, result) -> None:
        if self.torn_down:
            return
        try:
            self.manager = Gio.DBusObjectManagerClient.new_for_bus_finish(result)
        except GLib.Error:
            self.manager = None
        if self.manager is not None:
            for signal in ("object-added", "object-removed", "interface-added", "interface-removed",
                           "interface-proxy-properties-changed", "notify::name-owner"):
                self.watch(self.manager, signal, lambda *_a: self.queue_refresh())
        self.queue_refresh()

    def teardown(self) -> None:
        if self.refresh_id:
            GLib.source_remove(self.refresh_id)
            self.refresh_id = 0
        self.stop_discovery()
        if self.agent is not None:
            self.agent.close()
            self.agent = None
        super().teardown()

    def shown(self) -> None:
        self.discover()

    # --- finding and pairing new devices ---------------------------------------------

    def discover(self) -> None:
        """Look for devices for a minute (searching drains batteries)."""
        adapter = self.adapter()
        if adapter is None or not adapter[1].get("Powered") or self.manager is None:
            return
        if not self.discovering:
            # "Already in progress" (another app is looking) is fine: the list fills either way.
            self.manager.get_connection().call(BLUEZ, adapter[0], ADAPTER, "StartDiscovery", None, None,
                                               Gio.DBusCallFlags.NONE, 10000, None,
                                               lambda c, r: self.discovery_started(c, r))
            self.discovering = True
        if self.discovery_timer:
            GLib.source_remove(self.discovery_timer)
        self.discovery_timer = GLib.timeout_add_seconds(60, self.discovery_over)
        self.queue_refresh()

    def discovery_started(self, connection, result) -> None:
        try:
            connection.call_finish(result)
        except GLib.Error as exc:
            if "InProgress" not in (Gio.DBusError.get_remote_error(exc) or "") and not self.torn_down:
                self.discovering = False
                self.toast(f"Couldn’t look for devices: {exc.message}")
        if not self.torn_down:
            self.queue_refresh()

    def discovery_over(self) -> bool:
        self.discovery_timer = 0
        self.stop_discovery()
        self.queue_refresh()
        return GLib.SOURCE_REMOVE

    def stop_discovery(self) -> None:
        if self.discovery_timer:
            GLib.source_remove(self.discovery_timer)
            self.discovery_timer = 0
        if self.discovering:
            self.discovering = False
            adapter = self.adapter()
            if adapter is not None and self.manager is not None:
                self.manager.get_connection().call(BLUEZ, adapter[0], ADAPTER, "StopDiscovery", None, None,
                                                   Gio.DBusCallFlags.NONE, 5000, None, None)

    def name_of(self, path: str) -> str:
        props = self.objects().get(path, {}).get(DEVICE, {})
        return props.get("Alias") or props.get("Name") or "the device"

    def pair(self, dev: dict) -> None:
        if self.pairing is not None:
            return
        if self.agent is None:
            try:
                self.agent = Agent(self.manager.get_connection(), self.window, self.name_of)
            except GLib.Error as exc:
                self.toast(f"Couldn’t get ready to pair: {exc.message}")
                return
        self.pairing = dev["path"]
        self.queue_refresh()

        def paired() -> None:
            self.pairing = None
            props = self.objects().get(dev["path"], {}).get(DEVICE, {})
            if props.get("Paired") or props.get("Bonded"):
                self.call(dev["path"], "org.freedesktop.DBus.Properties", "Set",
                          GLib.Variant("(ssv)", (DEVICE, "Trusted", GLib.Variant("b", True))), "Couldn’t trust it")
                self.call(dev["path"], DEVICE, "Connect", None, f"Paired, but couldn’t connect to {dev['name']}")
                self.toast(f"{dev['name']} is paired")
        self.call(dev["path"], DEVICE, "Pair", None, f"Couldn’t pair with {dev['name']}", paired)

    def nearby_row(self, dev: dict) -> Adw.ActionRow:
        row = Adw.ActionRow(title=GLib.markup_escape_text(dev["name"]), activatable=self.pairing is None)
        row.add_prefix(Gtk.Image(icon_name=device_icon(dev["icon"])))
        if self.pairing == dev["path"]:
            row.set_subtitle("Pairing…")
        else:
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r: self.pair(dev))
        return row

    # --- state -----------------------------------------------------------------------

    def objects(self) -> dict[str, dict[str, dict]]:
        if self.manager is None or self.manager.get_name_owner() is None:
            return {}
        out: dict[str, dict[str, dict]] = {}
        for obj in self.manager.get_objects():
            ifaces = {}
            for proxy in obj.get_interfaces():
                props = {}
                for name in proxy.get_cached_property_names() or []:
                    value = proxy.get_cached_property(name)
                    props[name] = value.unpack() if value is not None else None
                ifaces[proxy.get_interface_name()] = props
            out[obj.get_object_path()] = ifaces
        return out

    def adapter(self) -> tuple[str, dict] | None:
        for path, ifaces in sorted(self.objects().items()):
            if ADAPTER in ifaces:
                return path, ifaces[ADAPTER]
        return None

    def airplane(self) -> bool | None:
        """GNOME's Bluetooth airplane switch (how the top bar turns it off)."""
        if self.rfkill is None or self.rfkill.get_name_owner() is None:
            return None
        has = self.rfkill.get_cached_property("BluetoothHasAirplaneMode")
        if has is None or not has.unpack():
            return None
        value = self.rfkill.get_cached_property("BluetoothAirplaneMode")
        return bool(value.unpack()) if value is not None else None

    def hardware_off(self) -> bool:
        if self.rfkill is None:
            return False
        value = self.rfkill.get_cached_property("BluetoothHardwareAirplaneMode")
        return bool(value.unpack()) if value is not None else False

    # --- showing ---------------------------------------------------------------------

    def queue_refresh(self) -> None:
        if not self.refresh_id:
            self.refresh_id = GLib.timeout_add(200, self.refresh)

    def refresh(self) -> bool:
        self.refresh_id = 0
        for group, row in self.rows:
            group.remove(row)
        self.rows = []
        adapter = self.adapter()
        airplane = self.airplane()
        powered = bool(adapter and adapter[1].get("Powered")) and airplane is not True

        self.power.handler_block_by_func(self.on_power)
        self.power.set_active(powered)
        self.power.handler_unblock_by_func(self.on_power)
        if adapter is None:
            self.power.set_sensitive(False)
            self.power.set_subtitle("No Bluetooth adapter found" if airplane is None
                                    else "Bluetooth is off" if airplane else "No Bluetooth adapter found")
            # With airplane mode on, BlueZ hides the adapter: the switch must still turn it back on.
            self.power.set_sensitive(airplane is True and not self.hardware_off())
        elif self.hardware_off():
            self.power.set_sensitive(False)
            self.power.set_subtitle("Turned off with a switch or key on the computer")
        else:
            self.power.set_sensitive(True)
            name = adapter[1].get("Alias") or adapter[1].get("Name") or ""
            self.power.set_subtitle(f"Others see this computer as “{name}”" if powered and name else "")

        self.devices.set_visible(powered)
        self.nearby.set_visible(powered)
        if powered:
            objects = self.objects()
            devices = paired_devices(objects)
            if not devices:
                self.add_row(Adw.ActionRow(title="No devices yet",
                                           subtitle="Pair headphones, a mouse or a keyboard from Nearby devices."))
            for dev in devices:
                self.add_row(self.device_row(dev))
            if not self.discovering and self.discovery_timer == 0 and adapter[1].get("Discovering") is False \
                    and not getattr(self, "looked", False):
                self.looked = True
                GLib.idle_add(lambda: (self.discover(), False)[1])
            self.searching.set_visible(self.discovering)
            self.look_again.set_visible(not self.discovering)
            nearby = nearby_devices(objects, adapter[0])
            if not nearby:
                row = Adw.ActionRow(title="Looking for devices…" if self.discovering else "No devices found")
                self.nearby.add(row)
                self.rows.append((self.nearby, row))
            for dev in nearby[:15]:
                row = self.nearby_row(dev)
                self.nearby.add(row)
                self.rows.append((self.nearby, row))
        return GLib.SOURCE_REMOVE

    def add_row(self, row: Gtk.Widget) -> None:
        self.devices.add(row)
        self.rows.append((self.devices, row))

    def device_row(self, dev: dict) -> Adw.ActionRow:
        status = "Connected" if dev["connected"] else "Not connected"
        if dev["battery"] is not None:
            status += f" · battery {dev['battery']}%"
        if dev["path"] in self.busy:
            status = "Disconnecting…" if dev["connected"] else "Connecting…"
        row = Adw.ActionRow(title=GLib.markup_escape_text(dev["name"]), subtitle=status)
        row.add_prefix(Gtk.Image(icon_name=dev["icon"]))
        button = Gtk.Button(label="Disconnect" if dev["connected"] else "Connect", valign=Gtk.Align.CENTER,
                            sensitive=dev["path"] not in self.busy)
        button.connect("clicked", lambda _b: self.toggle(dev))
        row.add_suffix(button)
        forget = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text="Forget this device")
        forget.update_property([Gtk.AccessibleProperty.LABEL], [f"Forget {dev['name']}"])
        forget.connect("clicked", lambda _b: self.confirm_forget(dev))
        row.add_suffix(forget)
        return row

    # --- actions ---------------------------------------------------------------------

    def call(self, path: str, iface: str, method: str, args: GLib.Variant | None, failure: str,
             done=None) -> None:
        def finished(conn, result) -> None:
            try:
                conn.call_finish(result)
            except GLib.Error as exc:
                message = Gio.DBusError.get_remote_error(exc) or exc.message
                self.toast(f"{failure}: {exc.message if message.startswith('org.') else message}")
            if done:
                done()
            self.queue_refresh()

        if self.manager is None:
            return
        self.manager.get_connection().call(BLUEZ, path, iface, method, args, None,
                                                        Gio.DBusCallFlags.NONE, 30000, None, finished)

    def on_power(self, row: Adw.SwitchRow, _pspec) -> None:
        on = row.get_active()
        if self.airplane() is not None:
            # Same switch as the top bar and GNOME Settings use.
            self.rfkill.call("org.freedesktop.DBus.Properties.Set",
                             GLib.Variant("(ssv)", (RFKILL_NAME, "BluetoothAirplaneMode", GLib.Variant("b", not on))),
                             Gio.DBusCallFlags.NONE, -1, None, self.power_done)
            if not on:
                return
        adapter = self.adapter()
        if adapter is not None:
            self.call(adapter[0], "org.freedesktop.DBus.Properties", "Set",
                      GLib.Variant("(ssv)", (ADAPTER, "Powered", GLib.Variant("b", on))),
                      "Couldn’t change Bluetooth")

    def power_done(self, proxy, result) -> None:
        try:
            proxy.call_finish(result)
        except GLib.Error as exc:
            self.toast(f"Couldn’t change Bluetooth: {exc.message}")
        self.queue_refresh()

    def toggle(self, dev: dict) -> None:
        self.busy.add(dev["path"])

        def done() -> None:
            self.busy.discard(dev["path"])

        if dev["connected"]:
            self.call(dev["path"], DEVICE, "Disconnect", None, "Couldn’t disconnect", done)
        else:
            self.call(dev["path"], DEVICE, "Connect", None, f"Couldn’t connect to {dev['name']}", done)
        self.queue_refresh()

    def confirm_forget(self, dev: dict) -> None:
        dialog = Adw.AlertDialog(heading=f"Forget {dev['name']}?",
                                 body="You’ll need to pair it again to use it with this computer.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("forget", "Forget")
        dialog.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def response(_d, answer: str) -> None:
            adapter = self.adapter()
            if answer == "forget" and adapter is not None:
                self.call(adapter[0], ADAPTER, "RemoveDevice", GLib.Variant("(o)", (dev["path"],)),
                          "Couldn’t forget it")

        dialog.connect("response", response)
        dialog.present(self.window)
