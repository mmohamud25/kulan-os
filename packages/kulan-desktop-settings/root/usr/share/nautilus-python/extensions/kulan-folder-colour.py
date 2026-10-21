"""Kulan OS: "Folder Colour" in Files (right-click one or more folders).

Sets the folder's custom icon to a colour version of the Kulan folder (from
the Kulan icon theme), the same way Files' own Properties > Icon does, so
it works anywhere a custom icon does. "Default" removes it.
"""
from pathlib import Path

from gi.repository import Gio, GObject, Nautilus

THEME = Path("/usr/share/icons/Kulan/scalable/places")
COLOURS = [("red", "Red"), ("orange", "Orange"), ("yellow", "Yellow"), ("green", "Green"),
           ("teal", "Teal"), ("blue", "Blue"), ("purple", "Purple"), ("grey", "Grey")]
ATTRIBUTE = "metadata::custom-icon"


class KulanFolderColour(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        folders = [f for f in files if f.is_directory() and f.get_uri_scheme() == "file"]
        if not folders or len(folders) != len(files) or not THEME.is_dir():
            return []
        top = Nautilus.MenuItem(name="KulanFolderColour::menu", label="Folder Colour")
        submenu = Nautilus.Menu()
        top.set_submenu(submenu)
        for colour, label in COLOURS:
            icon = THEME / f"folder-kulan-{colour}.svg"
            if not icon.exists():
                continue
            item = Nautilus.MenuItem(name=f"KulanFolderColour::{colour}", label=label)
            item.connect("activate", self.paint, folders, icon.as_uri())
            submenu.append_item(item)
        default = Nautilus.MenuItem(name="KulanFolderColour::default", label="Default")
        default.connect("activate", self.paint, folders, None)
        submenu.append_item(default)
        return [top]

    @staticmethod
    def paint(_item, folders, icon_uri):
        for folder in folders:
            gfile = Gio.File.new_for_uri(folder.get_uri())
            try:
                if icon_uri:
                    gfile.set_attribute_string(ATTRIBUTE, icon_uri, Gio.FileQueryInfoFlags.NONE, None)
                else:
                    gfile.set_attribute(ATTRIBUTE, Gio.FileAttributeType.INVALID, None,
                                        Gio.FileQueryInfoFlags.NONE, None)
            except Exception:  # noqa: BLE001 - read-only folder: leave it as it is
                continue
            folder.invalidate_extension_info()
