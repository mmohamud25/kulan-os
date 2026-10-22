"""Pairing new Bluetooth devices in Kulan Settings: looks for nearby devices
while the page is open, and answers BlueZ's questions (codes to compare or
type) with dialogs. GNOME's own agent lives in GNOME Settings, so this one is
registered while pairing from here."""
from __future__ import annotations

import random
from typing import Callable

from gi.repository import Adw, Gio, GLib, Gtk

BLUEZ = "org.bluez"
AGENT_PATH = "/org/kulan/Settings/BluetoothAgent"
AGENT_XML = """
<node>
  <interface name="org.bluez.Agent1">
    <method name="Release"/>
    <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
    <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
    <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
    <method name="DisplayPasskey">
      <arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/>
    </method>
    <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
    <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
    <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
    <method name="Cancel"/>
  </interface>
</node>
"""


def nearby_devices(objects: dict[str, dict[str, dict]], adapter: str) -> list[dict]:
    """Devices BlueZ has found that aren't paired yet: named ones, strongest first."""
    found = []
    for path, interfaces in objects.items():
        props = interfaces.get("org.bluez.Device1")
        if not props or not path.startswith(adapter + "/") or props.get("Paired") or props.get("Bonded"):
            continue
        name = props.get("Alias") or props.get("Name")
        if not name or name.replace("-", ":") == props.get("Address"):
            continue  # nameless beacons and trackers
        found.append({"path": path, "name": name, "icon": props.get("Icon"), "rssi": props.get("RSSI") or -999})
    return sorted(found, key=lambda d: (-d["rssi"], d["name"].lower()))


class Agent:
    """BlueZ's questions while pairing, asked with dialogs on parent."""

    def __init__(self, connection: Gio.DBusConnection, parent: Gtk.Widget, name_of: Callable[[str], str]) -> None:
        self.connection = connection
        self.parent = parent
        self.name_of = name_of
        self.dialog: Adw.AlertDialog | None = None
        info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        self.registration = connection.register_object(AGENT_PATH, info.interfaces[0], self.on_call, None, None)
        connection.call_sync(BLUEZ, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent",
                             GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")), None,
                             Gio.DBusCallFlags.NONE, 5000, None)

    def close(self) -> None:
        self.close_dialog()
        try:
            self.connection.call_sync(BLUEZ, "/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent",
                                      GLib.Variant("(o)", (AGENT_PATH,)), None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass
        if self.registration:
            self.connection.unregister_object(self.registration)
            self.registration = 0

    def close_dialog(self) -> None:
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.force_close()

    def ask(self, heading: str, body: str, ok: str | None, extra: Gtk.Widget | None,
            answered: Callable[[bool], None]) -> None:
        self.close_dialog()
        dialog = Adw.AlertDialog(heading=heading, body=body)
        if extra is not None:
            dialog.set_extra_child(extra)
        dialog.add_response("cancel", "Cancel" if ok else "Close")
        if ok:
            dialog.add_response("ok", ok)
            dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def response(d, r: str) -> None:
            if self.dialog is d:
                self.dialog = None
            answered(r == "ok")
        dialog.connect("response", response)
        self.dialog = dialog
        dialog.present(self.parent)

    @staticmethod
    def reject(invocation: Gio.DBusMethodInvocation) -> None:
        invocation.return_dbus_error("org.bluez.Error.Rejected", "Cancelled on this computer")

    def on_call(self, _conn, _sender, _path, _iface, method: str, params: GLib.Variant,
                invocation: Gio.DBusMethodInvocation) -> None:
        args = params.unpack()
        name = self.name_of(args[0]) if args else ""
        if method in ("Release", "Cancel"):
            self.close_dialog()
            invocation.return_value(None)
        elif method == "RequestConfirmation":
            code = f"{args[1]:06d}"
            label = Gtk.Label(label=code, css_classes=["title-1", "numeric"])
            self.ask(f"Pair with {name}?", "Check that this code is also showing on the device.", "Pair", label,
                     lambda ok: invocation.return_value(None) if ok else self.reject(invocation))
        elif method == "RequestAuthorization":
            self.ask(f"Pair with {name}?", "It asked to pair with this computer.", "Pair", None,
                     lambda ok: invocation.return_value(None) if ok else self.reject(invocation))
        elif method == "AuthorizeService":
            invocation.return_value(None)  # a device we paired using one of its features
        elif method in ("DisplayPinCode", "DisplayPasskey"):
            code = args[1] if method == "DisplayPinCode" else f"{args[1]:06d}"
            label = Gtk.Label(label=code, css_classes=["title-1", "numeric"])
            self.ask(f"Type this code on {name}", "Then press Enter on the device.", None, label, lambda _ok: None)
            invocation.return_value(None)
        elif method in ("RequestPinCode", "RequestPasskey"):
            # A keyboard: type the suggested code on it. A headset or speaker with a
            # fixed PIN: type that PIN here (usually 0000, printed in its manual).
            code = f"{random.randint(0, 999999):06d}"
            entry = Gtk.Entry(text=code, max_length=16, input_purpose=Gtk.InputPurpose.DIGITS,
                              activates_default=True, xalign=0.5, css_classes=["title-2"])
            entry.update_property([Gtk.AccessibleProperty.LABEL], ["Pairing code"])

            def answered(ok: bool) -> None:
                pin = entry.get_text().strip()
                if not ok or not pin or (method == "RequestPasskey" and not pin.isdigit()):
                    self.reject(invocation)
                elif method == "RequestPinCode":
                    invocation.return_value(GLib.Variant("(s)", (pin,)))
                else:
                    invocation.return_value(GLib.Variant("(u)", (int(pin) % 1000000,)))
            self.ask(f"Pairing code for {name}",
                     "For a keyboard: type this code on it and press Enter, then Continue here. For headphones or "
                     "a speaker: type its own PIN here instead (often 0000).", "Continue", entry, answered)
        else:
            invocation.return_dbus_error("org.bluez.Error.Rejected", "Not supported")
