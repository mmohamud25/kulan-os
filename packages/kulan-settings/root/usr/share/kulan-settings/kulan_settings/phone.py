"""Phone: link an Android phone (or an iPhone, with fewer features) to share
files, see its notifications, and read and send texts from the computer.

GSConnect does it (a GNOME Shell extension speaking KDE Connect's protocol;
the phone runs the KDE Connect app). It isn't in the image: it's the Phone
Link add-on (about 5 MB), which this page installs when asked. It's off until
switched on here or installed,
because it listens on the network: switching it on enables the extension for
this account and opens KDE Connect's ports in the firewall (update-run
phone-link-on); off closes them again. Only phones on the same network can
find the computer, and only a phone you pair (and accept on the phone) can
do anything.
"""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, settings

UUID = "gsconnect@andyholmes.github.io"
EXTENSION_DIRS = [Path("/usr/share/gnome-shell/extensions") / UUID,
                  Path(GLib.get_user_data_dir()) / "gnome-shell/extensions" / UUID]
BUS_NAME = "org.gnome.Shell.Extensions.GSConnect"
OBJECT_PATH = "/org/gnome/Shell/Extensions/GSConnect"
DEVICE_IFACE = "org.gnome.Shell.Extensions.GSConnect.Device"


def installed() -> bool:
    return any(d.is_dir() for d in EXTENSION_DIRS)


class PhonePage(Page):
    title = "Phone"
    icon = "phone-symbolic"
    keywords = ("phone", "android", "iphone", "kde connect", "gsconnect", "texts", "sms", "messages",
                "notifications from phone", "send files to phone", "mobile", "pair phone")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.shell = settings("org.gnome.shell")
        self.manager = None
        if self.shell is None:
            self.add_group(None, "Phone link needs GNOME Shell.")
            return
        if not installed():
            self.build_install()
            return

        intro = self.add_group(None, "Share files and links, see your phone’s notifications here, and read and "
                                     "send texts from the computer. Works best with Android; on iPhone, files "
                                     "and the clipboard.")
        self.switch = Adw.SwitchRow(title="Phone link",
                                    subtitle="Lets phones on the same home or office network find this "
                                             "computer. Only a phone you pair can connect", subtitle_lines=0,
                                    active=self.enabled())
        self.switch.connect("notify::active", self.on_switch)
        intro.add(self.switch)

        self.steps = self.add_group("Set up", None)
        for number, text in enumerate((
                "Install KDE Connect on your phone: Google Play or F-Droid on Android, the App Store on iPhone.",
                "Connect the phone to the same Wi-Fi as this computer.",
                "Open KDE Connect on the phone. It appears under Phones below: choose Pair, then accept on the "
                "phone."), start=1):
            row = Adw.ActionRow(title=text, title_lines=0)
            row.add_prefix(Gtk.Label(label=str(number), css_classes=["dim-label", "numeric"], width_chars=2))
            self.steps.add(row)

        self.devices = self.add_group("Phones")
        self.device_rows: list[Gtk.Widget] = []
        self.empty_row = Adw.ActionRow(title="Looking for phones…",
                                       subtitle="Open KDE Connect on your phone, on the same Wi-Fi")
        self.devices.add(self.empty_row)

        more = self.add_group()
        tips = Adw.ActionRow(title="Send a file to your phone",
                             subtitle="In Files, right-click it and choose Send to Mobile Device", subtitle_lines=0)
        more.add(tips)
        prefs = Adw.ActionRow(title="More phone settings",
                              subtitle="What the phone may do: notifications, texts, files, media controls",
                              activatable=True)
        prefs.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        prefs.connect("activated", lambda _r: self.open_prefs())
        more.add(prefs)

        self.watch(self.shell, "changed::enabled-extensions", lambda *_a: self.sync_switch())
        self.listen_busy(self.on_busy)
        self.sync_visible()
        self.connect_devices()

    def build_install(self) -> None:
        """Not installed yet: say what it does and install the add-on on request
        (it opens KDE Connect's ports to local networks only, like the switch)."""
        self.switch = None
        group = self.add_group(None, "Share files and links, see your phone’s notifications here, and read and "
                                     "send texts from the computer. Works best with Android; on iPhone, files "
                                     "and the clipboard.")
        row = Adw.ActionRow(title="Phone link",
                            subtitle="Not installed (about 5 MB). Installing turns it on: phones on the same home "
                                     "or office network can find this computer, and only a phone you pair can "
                                     "connect", subtitle_lines=0)
        self.install_button = Gtk.Button(label="Install", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.install_button.update_property([Gtk.AccessibleProperty.LABEL], ["Install Phone link"])
        self.install_button.connect("clicked", lambda _b: self.install())
        self.install_button.set_sensitive(not self.window.helper.busy)
        row.add_suffix(self.install_button)
        group.add(row)
        self.listen_busy(self.on_install_busy)

    def on_install_busy(self, busy: bool) -> None:
        self.install_button.set_sensitive(not busy)

    def install(self) -> None:
        self.install_button.set_label("Installing…")

        def done(ok: bool) -> None:
            if not ok:
                self.install_button.set_label("Install")
                return
            from .addons import set_extension
            set_extension(UUID, True)
            dialog = Adw.AlertDialog(heading="Phone link is installed",
                                     body="Log out and back in to finish. Then install KDE Connect on your phone "
                                          "and pair it here.")
            dialog.add_response("ok", "OK")
            dialog.present(self.window)
            GLib.idle_add(lambda: self.window.reload_page(self) and False)

        self.window.helper.run(["addon-install", "phone-link"], "Phone link installed", on_done=done)

    def on_busy(self, busy: bool) -> None:
        self.switch.set_sensitive(not busy)

    # --- on and off -------------------------------------------------------------------
    def enabled(self) -> bool:
        return UUID in self.shell.get_strv("enabled-extensions") and \
            UUID not in self.shell.get_strv("disabled-extensions")

    def set_enabled(self, on: bool) -> None:
        enabled = [u for u in self.shell.get_strv("enabled-extensions") if u != UUID]
        disabled = [u for u in self.shell.get_strv("disabled-extensions") if u != UUID]
        self.shell.set_strv("enabled-extensions", enabled + [UUID] if on else enabled)
        self.shell.set_strv("disabled-extensions", disabled)

    def sync_switch(self) -> None:
        on = self.enabled()
        if self.switch.get_active() != on:
            self.switch.handler_block_by_func(self.on_switch)
            self.switch.set_active(on)
            self.switch.handler_unblock_by_func(self.on_switch)
        self.sync_visible()

    def sync_visible(self) -> None:
        on = self.enabled()
        self.steps.set_visible(on)
        self.devices.set_visible(on)

    def on_switch(self, row: Adw.SwitchRow, _pspec) -> None:
        on = row.get_active()
        if on == self.enabled():
            return
        if not on:
            # Close the ports first; only then stop the extension, so a
            # cancelled password prompt never leaves ports open with the
            # switch showing off.
            def closed(ok: bool) -> None:
                if ok:
                    self.set_enabled(False)
                self.sync_switch()
            self.window.helper.run(["phone-link-off"], "Phone link off", on_done=closed)
            return

        def done(ok: bool) -> None:
            if ok:
                self.set_enabled(True)
                GLib.timeout_add_seconds(3, lambda: (self.connect_devices(), False)[1])
            self.sync_switch()
        self.window.helper.run(["phone-link-on"], "Phone link on: open KDE Connect on your phone", on_done=done)

    def open_prefs(self) -> None:
        if not self.enabled():
            self.toast("Turn Phone link on first")
            return
        try:
            Gio.Subprocess.new(["gnome-extensions", "prefs", UUID], Gio.SubprocessFlags.NONE)
        except GLib.Error as exc:
            self.toast(f"Couldn’t open them: {exc.message}")

    # --- phones -----------------------------------------------------------------------
    def connect_devices(self) -> None:
        if self.manager is not None or self.torn_down:
            return
        # By name: PyGObject's positional form of this constructor shifts the
        # callback into the wrong argument (it's then never called).
        Gio.DBusObjectManagerClient.new_for_bus(
            bus_type=Gio.BusType.SESSION, flags=Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START,
            name=BUS_NAME, object_path=OBJECT_PATH, get_proxy_type_func=None, cancellable=None,
            callback=self.got_manager)

    def got_manager(self, _source, result) -> None:
        try:
            manager = Gio.DBusObjectManagerClient.new_for_bus_finish(result)
        except GLib.Error:
            return
        if self.torn_down:
            return
        self.manager = manager
        for signal in ("object-added", "object-removed", "interface-proxy-properties-changed", "notify::name-owner"):
            self.watch(manager, signal, lambda *_a: self.fill_devices())
        self.fill_devices()

    def fill_devices(self) -> None:
        if self.torn_down:
            return
        for row in self.device_rows:
            self.devices.remove(row)
        self.device_rows = []
        phones = []
        if self.manager is not None and self.manager.get_name_owner():
            for obj in self.manager.get_objects():
                proxy = obj.get_interface(DEVICE_IFACE)
                if proxy is not None:
                    phones.append(proxy)
        self.empty_row.set_visible(not phones)
        for proxy in sorted(phones, key=lambda p: self.prop(p, "Name", "")):
            self.add_device(proxy)

    @staticmethod
    def prop(proxy, name: str, default=None):
        value = proxy.get_cached_property(name)
        return value.unpack() if value is not None else default

    def add_device(self, proxy) -> None:
        name = self.prop(proxy, "Name", "Phone")
        paired, connected = self.prop(proxy, "Paired", False), self.prop(proxy, "Connected", False)
        if paired and connected:
            state = "Connected"
        elif paired:
            state = "Paired, not connected now: open KDE Connect on the phone, on the same Wi-Fi"
        else:
            state = "Not paired yet"
        row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=state, subtitle_lines=0)
        row.add_prefix(Gtk.Image(icon_name=self.prop(proxy, "IconName", "") or "phone-symbolic"))
        label, action = ("Unpair", "unpair") if paired else ("Pair", "pair")
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER,
                            css_classes=[] if paired else ["suggested-action"])
        button.connect("clicked", lambda _b: self.device_action(proxy, action, name))
        row.add_suffix(button)
        self.devices.add(row)
        self.device_rows.append(row)

    def device_action(self, proxy, action: str, name: str) -> None:
        def done(conn, result) -> None:
            try:
                conn.call_finish(result)
            except GLib.Error as exc:
                if not self.torn_down:
                    self.toast(f"Couldn’t {action}: {exc.message}")
                return
            if not self.torn_down:
                self.toast(f"Accept the request on {name}" if action == "pair" else f"{name} unpaired")
        proxy.get_connection().call(BUS_NAME, proxy.get_object_path(), "org.gtk.Actions", "Activate",
                                    GLib.Variant("(sava{sv})", (action, [], {})), None,
                                    Gio.DBusCallFlags.NONE, 10000, None, done)
