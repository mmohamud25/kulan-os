"""Notifications: banners, the lock screen, and each app's own notifications."""
from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .common import Page, has_key, reset_keys, settings, switch_row

NOTIFICATIONS = "org.gnome.desktop.notifications"
APP_SCHEMA = "org.gnome.desktop.notifications.application"
APP_PATH = "/org/gnome/desktop/notifications/application/{}/"
APP_KEYS = ["enable", "enable-sound-alerts", "show-banners", "show-in-lock-screen", "details-in-lock-screen"]


def app_settings(child: str) -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(APP_SCHEMA, True) if source else None
    if schema is None:
        return None
    return Gio.Settings.new_full(schema, None, APP_PATH.format(child))


def app_info(app_settings_: Gio.Settings, child: str) -> Gio.DesktopAppInfo | None:
    """The app a notifications entry belongs to (GNOME stores its id), or None
    when that app has been removed."""
    app_id = app_settings_.get_string("application-id") if has_key(app_settings_, "application-id") else ""
    for candidate in (app_id, f"{child}.desktop", f"{child.replace('-', '.')}.desktop"):
        if candidate:
            try:
                info = Gio.DesktopAppInfo.new(candidate)
            except TypeError:
                info = None
            if info is not None:
                return info
    return None


class NotificationsPage(Page):
    title = "Notifications"
    icon = "org.gnome.Settings-notifications-symbolic|preferences-system-notifications-symbolic"
    keywords = ("notifications", "banners", "alerts", "pop-ups", "lock screen", "apps", "do not disturb", "sounds")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.notifications = settings(NOTIFICATIONS)

        general = self.add_group()
        self.add_rows(
            general,
            switch_row(self.notifications, "show-banners", "Do Not Disturb",
                       "No pop-ups or sounds; notifications wait in the list", invert=True),
            switch_row(self.notifications, "show-in-lock-screen", "Notifications on the lock screen"),
        )
        focus = Adw.ActionRow(title="Focus schedule", subtitle="Do Not Disturb at set times every day",
                              activatable=True)
        focus.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        focus.connect("activated", lambda _r: self.window.show_page("focus"))
        general.add(focus)

        apps = self.add_group("Apps", "Apps appear here after they first send a notification.")
        children = self.notifications.get_strv("application-children") if has_key(
            self.notifications, "application-children") else []
        entries = []
        for child in children:
            app = app_settings(child)
            if app is None:
                continue
            info = app_info(app, child)
            if info is None or not info.should_show():
                continue  # removed apps and background helpers
            entries.append((info.get_display_name(), info, app))
        if not entries:
            apps.add(Adw.ActionRow(title="No apps yet", css_classes=["dim-label"]))
        for name, info, app in sorted(entries, key=lambda e: e[0].lower()):
            apps.add(self.app_row(name, info, app))
        self.add_reset(self.reset)

    def app_row(self, name: str, info: Gio.DesktopAppInfo, app: Gio.Settings) -> Adw.ExpanderRow:
        row = Adw.ExpanderRow(title=name, show_enable_switch=True)
        icon = info.get_icon()
        if icon is not None:
            image = Gtk.Image(gicon=icon, pixel_size=32)
            row.add_prefix(image)
        if has_key(app, "enable"):
            app.bind("enable", row, "enable-expansion", Gio.SettingsBindFlags.DEFAULT)
            self.watch(app, "changed::enable", lambda s, _k, r=row: r.set_subtitle("" if s.get_boolean("enable") else "Off"))
            row.set_subtitle("" if app.get_boolean("enable") else "Off")
        for key, title in (("show-banners", "Pop-up banners"), ("enable-sound-alerts", "Sounds"),
                           ("show-in-lock-screen", "On the lock screen"),
                           ("details-in-lock-screen", "Show what they say on the lock screen")):
            switch = switch_row(app, key, title)
            if switch is not None:
                row.add_row(switch)
        # Keep the settings object alive as long as the row.
        row.kulan_settings = app
        return row

    def reset(self) -> None:
        reset_keys(self.notifications, ["show-banners", "show-in-lock-screen"])
        for child in self.notifications.get_strv("application-children") if has_key(
                self.notifications, "application-children") else []:
            reset_keys(app_settings(child), APP_KEYS)
        self.window.reload_page(self)
        self.toast("Notifications reset to Kulan defaults")
