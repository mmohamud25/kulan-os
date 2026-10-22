"""More wallpapers: every wallpaper installed for GNOME (Kulan's, GNOME's and
any an app added), as small pictures to click."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Callable

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

LISTS = [Path("/usr/share/gnome-background-properties"), Path("/usr/local/share/gnome-background-properties"),
         Path(GLib.get_user_data_dir()) / "gnome-background-properties"]
THUMB_W, THUMB_H = 200, 125
PICTURE = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".jxl")


def installed_wallpapers() -> list[dict]:
    """[{name, light, dark, options}] from GNOME's wallpaper lists; only
    pictures that exist (lists can name removed files)."""
    found: dict[str, dict] = {}
    for folder in LISTS:
        try:
            files = sorted(folder.glob("*.xml"))
        except OSError:
            continue
        for file in files:
            try:
                root = ElementTree.parse(file).getroot()
            except (ElementTree.ParseError, OSError):
                continue
            for item in root.iter("wallpaper"):
                if item.get("deleted") == "true":
                    continue
                light = (item.findtext("filename") or "").strip()
                dark = (item.findtext("filename-dark") or "").strip() or light
                if not light.lower().endswith(PICTURE) or not os.path.isfile(light):
                    continue  # slideshows (.xml) and colour-only entries
                if not os.path.isfile(dark):
                    dark = light
                found.setdefault(light, {"name": (item.findtext("name") or Path(light).stem).strip(),
                                         "light": light, "dark": dark,
                                         "options": (item.findtext("options") or "zoom").strip()})
    return list(found.values())


class WallpaperDialog(Adw.Dialog):
    def __init__(self, background: Gio.Settings, toast: Callable[[str], None], dark: bool) -> None:
        super().__init__(title="Wallpapers", content_width=720, content_height=560)
        self.background = background
        self.toast = toast
        self.dark = dark
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=3,
                                min_children_per_line=2, row_spacing=12, column_spacing=12, margin_top=12,
                                margin_bottom=18, margin_start=18, margin_end=18, valign=Gtk.Align.START)
        self.flow.connect("child-activated", self.on_pick)
        view.set_content(Gtk.ScrolledWindow(child=self.flow, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True))
        self.set_child(view)
        self.items = installed_wallpapers()
        if not self.items:
            self.flow.append(Gtk.Label(label="No other wallpapers are installed", css_classes=["dim-label"]))
            return
        self.loader = 0
        current = background.get_string("picture-uri-dark" if dark else "picture-uri")
        self.pictures: list[Gtk.Picture] = []
        for item in self.items:
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, width_request=THUMB_W, height_request=THUMB_H,
                                  css_classes=["card"], overflow=Gtk.Overflow.HIDDEN)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.append(picture)
            name = Gtk.Label(label=item["name"], ellipsize=Pango.EllipsizeMode.END, css_classes=["caption"])
            if Gio.File.new_for_path(item["dark" if dark else "light"]).get_uri() == current:
                name.set_label(f"✓ {item['name']}")
                name.add_css_class("accent")
            box.append(name)
            child = Gtk.FlowBoxChild(child=box)
            child.update_property([Gtk.AccessibleProperty.LABEL], [f"Use {item['name']}"])
            self.flow.append(child)
            self.pictures.append(picture)
        # Thumbnails load one at a time when the app is idle, so the dialog
        # opens at once even with large pictures.
        self.next = 0
        self.loader = GLib.idle_add(self.load_next)
        self.connect("closed", self.stop_loading)

    def stop_loading(self, *_a) -> None:
        if self.loader:
            GLib.source_remove(self.loader)
            self.loader = 0

    def load_next(self) -> bool:
        if self.next >= len(self.items):
            self.loader = 0
            return GLib.SOURCE_REMOVE
        item = self.items[self.next]
        path = item["dark" if self.dark else "light"]
        try:
            scale = max(1, self.get_scale_factor())
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB_W * 2 * scale, -1, True)
            self.pictures[self.next].set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        except GLib.Error:
            pass
        self.next += 1
        return GLib.SOURCE_CONTINUE

    def on_pick(self, _flow, child: Gtk.FlowBoxChild) -> None:
        index = child.get_index()
        if index >= len(self.items):
            return
        item = self.items[index]
        self.background.set_string("picture-uri", Gio.File.new_for_path(item["light"]).get_uri())
        self.background.set_string("picture-uri-dark", Gio.File.new_for_path(item["dark"]).get_uri())
        options = item["options"] if item["options"] in ("none", "wallpaper", "centered", "scaled", "stretched",
                                                         "zoom", "spanned") else "zoom"
        self.background.set_string("picture-options", options)
        self.toast(f"Wallpaper: {item['name']}")
        self.close()
