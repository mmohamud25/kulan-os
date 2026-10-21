"""Kulan OS: Tags in Files, as on a Mac.

Right-click files or folders > Tags > Red, Orange, Yellow, Green, Blue,
Purple or Grey (a tick shows the ones they already have; choosing it again
removes it). A coloured dot shows on tagged items, and a Tags column is
available in list view. Search for a colour (for example "red") in Spotlight
to find everything with that tag.
"""
import sys
from urllib.parse import unquote, urlparse

sys.path.insert(0, "/usr/share/kulan/python")

from gi.repository import GObject, Nautilus  # noqa: E402

import kulan_tags  # noqa: E402


def local_path(file_info) -> str | None:
    if file_info.get_uri_scheme() != "file":
        return None
    return unquote(urlparse(file_info.get_uri()).path) or None


class KulanTags(GObject.GObject, Nautilus.MenuProvider, Nautilus.InfoProvider, Nautilus.ColumnProvider):
    def __init__(self):
        super().__init__()
        self.seen = {}  # path -> tags already in the index, so it isn't rewritten for every file shown

    def get_file_items(self, files):
        paths = [local_path(f) for f in files]
        if not files or any(p is None for p in paths):
            return []
        current = [set(kulan_tags.read_tags(p)) for p in paths]
        top = Nautilus.MenuItem(name="KulanTags::menu", label="Tags")
        submenu = Nautilus.Menu()
        top.set_submenu(submenu)
        for tag, name in kulan_tags.TAGS:
            everywhere = all(tag in tags for tags in current)
            item = Nautilus.MenuItem(name=f"KulanTags::{tag}", label=f"✓ {name}" if everywhere else name,
                                     icon=f"emblem-kulan-tag-{tag}")
            item.connect("activate", self.toggle, files, paths, tag, not everywhere)
            submenu.append_item(item)
        if any(current):
            clear = Nautilus.MenuItem(name="KulanTags::clear", label="Remove Tags")
            clear.connect("activate", self.clear, files, paths)
            submenu.append_item(clear)
        return [top]

    @staticmethod
    def toggle(_item, files, paths, tag, add):
        for file_info, path in zip(files, paths):
            tags = kulan_tags.read_tags(path)
            if add and tag not in tags:
                tags.append(tag)
            elif not add:
                tags = [t for t in tags if t != tag]
            kulan_tags.write_tags(path, tags)
            file_info.invalidate_extension_info()

    @staticmethod
    def clear(_item, files, paths):
        for file_info, path in zip(files, paths):
            kulan_tags.write_tags(path, [])
            file_info.invalidate_extension_info()

    def update_file_info(self, file_info):
        path = local_path(file_info)
        if path is None:
            return
        tags = kulan_tags.read_tags(path)
        for tag in tags:
            file_info.add_emblem(f"emblem-kulan-tag-{tag}")
        file_info.add_string_attribute("kulan_tags", ", ".join(kulan_tags.NAMES[t] for t in tags))
        if tags and self.seen.get(path) != tags:
            # Files that were moved or copied keep their tags: find them again.
            kulan_tags.remember(path, tags)
            self.seen[path] = tags

    def get_columns(self):
        return [Nautilus.Column(name="KulanTags::column", attribute="kulan_tags", label="Tags",
                                description="Colour tags (right-click > Tags)")]
