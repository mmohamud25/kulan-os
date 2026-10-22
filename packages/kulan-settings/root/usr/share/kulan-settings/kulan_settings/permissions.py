"""App permissions: which sandboxed apps (Flatpak) may use the camera, the
microphone, your location, take screenshots, or run in the background. The
answers live in the desktop portal's permission store, the same place GNOME
Settings reads; apps appear after they first ask."""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

STORE_NAME = "org.freedesktop.impl.portal.PermissionStore"
STORE_PATH = "/org/freedesktop/impl/portal/PermissionStore"

# (table, id, title, what it lets an app do)
KINDS = [
    ("devices", "camera", "Camera", "Take photos and video"),
    ("devices", "microphone", "Microphone", "Record sound"),
    ("location", "location", "Location", "Find where you are"),
    ("screenshot", "screenshot", "Screenshots", "Take pictures of the screen without asking each time"),
    ("background", "background", "Running in the background", "Keep working after their windows close"),
]


def store() -> Gio.DBusProxy | None:
    try:
        return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, STORE_NAME,
                                              STORE_PATH, STORE_NAME, None)
    except GLib.Error:
        return None


def lookup(proxy: Gio.DBusProxy, table: str, ident: str) -> dict[str, list[str]]:
    try:
        reply = proxy.call_sync("Lookup", GLib.Variant("(ss)", (table, ident)), Gio.DBusCallFlags.NONE, 3000, None)
    except GLib.Error:
        return {}  # nothing stored yet
    return dict(reply.unpack()[0])


def allowed(values: list[str], table: str) -> bool:
    # Location stores "EXACT"/"CITY"/"NONE"; the others "yes"/"no".
    if table == "location":
        return bool(values) and values[0] not in ("NONE", "no")
    return bool(values) and values[0] == "yes"


def app_name(app_id: str) -> tuple[str, Gio.Icon | None]:
    info = Gio.DesktopAppInfo.new(f"{app_id}.desktop") if app_id else None
    if info is None:
        return app_id or "Apps that aren’t sandboxed", None
    return info.get_display_name(), info.get_icon()


class PermissionsDialog(Adw.PreferencesDialog):
    def __init__(self, toast) -> None:
        super().__init__(title="App permissions", search_enabled=False)
        self.toast = toast
        self.proxy = store()
        page = Adw.PreferencesPage()
        self.add(page)
        if (self.proxy is None or self.proxy.get_name_owner() is None) and not self.wake():
            group = Adw.PreferencesGroup()
            group.add(Adw.ActionRow(title="Permissions aren’t available",
                                    subtitle="The desktop portal (xdg-desktop-portal) isn’t running"))
            page.add(group)
            return
        for table, ident, title, what in KINDS:
            entries = lookup(self.proxy, table, ident)
            group = Adw.PreferencesGroup(title=title, description=what)
            apps = sorted(((app_id, values) for app_id, values in entries.items() if app_id),
                          key=lambda item: app_name(item[0])[0].lower())
            if not apps:
                group.add(Adw.ActionRow(title="No apps have asked yet", css_classes=["dim-label"]))
            for app_id, values in apps:
                name, icon = app_name(app_id)
                row = Adw.SwitchRow(title=GLib.markup_escape_text(name), active=allowed(values, table))
                if icon is not None:
                    row.add_prefix(Gtk.Image(gicon=icon, pixel_size=32))
                row.connect("notify::active", lambda r, _p, t=table, i=ident, a=app_id: self.set(t, i, a, r))
                group.add(row)
            page.add(group)

    def wake(self) -> bool:
        """The store starts on first use; poke the portal so it's running."""
        try:
            Gio.DBus.session.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                       "StartServiceByName", GLib.Variant("(su)", (STORE_NAME, 0)), None,
                                       Gio.DBusCallFlags.NONE, 5000, None)
            self.proxy = store()
            return self.proxy is not None and self.proxy.get_name_owner() is not None
        except GLib.Error:
            return False

    def set(self, table: str, ident: str, app_id: str, row: Adw.SwitchRow) -> None:
        on = row.get_active()
        value = ("EXACT" if on else "NONE") if table == "location" else ("yes" if on else "no")
        values = [value]
        if table == "location":
            # Keep the timestamp GNOME stores next to the answer, if there was one.
            old = lookup(self.proxy, table, ident).get(app_id, [])
            values = [value, *old[1:]]

        def done(proxy, result) -> None:
            try:
                proxy.call_finish(result)
            except GLib.Error as exc:
                self.toast(f"Couldn’t change it: {exc.message}")
        self.proxy.call("SetPermission", GLib.Variant("(sbssas)", (table, True, ident, app_id, values)),
                        Gio.DBusCallFlags.NONE, 5000, None, done)
