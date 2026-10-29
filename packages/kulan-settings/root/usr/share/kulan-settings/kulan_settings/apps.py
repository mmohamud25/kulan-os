"""Apps: which app opens web links, email, music, videos, photos and more,
and what happens when you plug in a USB stick or a phone."""
from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .common import Page, plain_error, reset_keys, settings, switch_row

# (title, the type it's chosen by, the other types that follow it) — the
# same groups GNOME Settings uses.
DEFAULTS = [
    ("Web browser", "x-scheme-handler/http", ["x-scheme-handler/https", "text/html", "application/xhtml+xml"]),
    ("Email", "x-scheme-handler/mailto", []),
    ("Calendar", "text/calendar", []),
    ("Music", "audio/x-vorbis+ogg", ["audio/mpeg", "audio/x-wav", "audio/flac", "audio/mp4", "audio/ogg"]),
    ("Video", "video/x-ogm+ogg", ["video/mp4", "video/x-matroska", "video/webm", "video/quicktime",
                                  "video/x-msvideo", "video/mpeg"]),
    ("Photos", "image/jpeg", ["image/png", "image/gif", "image/webp", "image/bmp", "image/tiff", "image/heif"]),
    ("PDF documents", "application/pdf", []),
    ("Text files", "text/plain", []),
]
MEDIA = "org.gnome.desktop.media-handling"


class AppsPage(Page):
    title = "Default apps"
    icon = "view-app-grid-symbolic"
    keywords = ("apps", "default apps", "browser", "email", "mail", "music", "video", "photos", "pdf",
                "open with", "usb", "removable media", "autorun")

    def __init__(self, window) -> None:
        super().__init__(window)
        defaults = self.add_group("Open with", "The app that opens each kind of file or link.")
        for title, kind, followers in DEFAULTS:
            row = self.default_row(title, kind, followers)
            if row is not None:
                defaults.add(row)

        media = settings(MEDIA)
        removable = self.add_group("Plugging things in", "USB sticks, memory cards, phones and cameras.")
        self.add_rows(
            removable,
            switch_row(media, "automount-open", "Open a window for new drives",
                       "Show the files when you plug in a USB stick or memory card"),
            switch_row(media, "autorun-never", "Never start software from a drive",
                       "Safer. A drive can’t start a program when plugged in."),
        )
        store = Adw.ActionRow(title="Install and remove apps", subtitle="In Software", activatable=True)
        store.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        store.connect("activated", lambda _r: self.open_software())
        self.add_group().add(store)
        self.add_reset(lambda: (reset_keys(media, ["automount-open", "autorun-never"]),
                                self.window.reload_page(self), self.toast("Plugging things in reset")))

    def default_row(self, title: str, kind: str, followers: list[str]) -> Adw.ComboRow | None:
        apps = [a for a in Gio.AppInfo.get_all_for_type(kind) if a.should_show()]
        if not apps:
            return None
        apps.sort(key=lambda a: a.get_display_name().lower())
        current = Gio.AppInfo.get_default_for_type(kind, False)
        ids = [a.get_id() for a in apps]
        # Nothing chosen yet: say so, rather than show the first app as if it were.
        unset = current is None or current.get_id() not in ids
        names = (["Not chosen"] if unset else []) + [a.get_display_name() for a in apps]
        offset = 1 if unset else 0
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(names))
        row.set_selected(0 if unset else ids.index(current.get_id()))

        def chosen(r: Adw.ComboRow, _p) -> None:
            if r.get_selected() < offset:
                return
            app = apps[r.get_selected() - offset]
            try:
                app.set_as_default_for_type(kind)
                for other in followers:
                    if other in (app.get_supported_types() or []) or kind.startswith("x-scheme-handler"):
                        app.set_as_default_for_type(other)
            except Exception as exc:  # noqa: BLE001 - GLib.Error, or an app that refuses
                self.toast(f"Couldn’t change it. {plain_error(exc)}")
                return
            self.toast(f"{title}: {app.get_display_name()}")
        row.connect("notify::selected", chosen)
        return row

    def open_software(self) -> None:
        app = Gio.DesktopAppInfo.new("org.gnome.Software.desktop")
        if app is None:
            self.toast("Software isn’t installed")
            return
        app.launch([], None)

