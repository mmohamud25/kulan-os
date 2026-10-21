"""Storage: how full the disk is, and one-click ways to free up space."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, launch

APT_ARCHIVES = Path("/var/cache/apt/archives")


def folder_size(path: Path) -> int:
    """Bytes used by everything under path (not following links)."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_blocks * 512
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def unneeded_packages() -> int:
    """How many packages apt would remove as no longer needed."""
    try:
        out = subprocess.run(["apt-get", "-s", "autoremove"], capture_output=True, text=True, timeout=60,
                             check=False, env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return sum(1 for line in out.splitlines() if line.startswith("Remv "))


def disk_usage(path: str = "/") -> tuple[int, int]:
    """(used, total) bytes of the file system holding path."""
    usage = shutil.disk_usage(path)
    return usage.total - usage.free, usage.total


class StoragePage(Page):
    title = "Storage"
    icon = "drive-harddisk-symbolic"
    keywords = ("storage", "disk", "space", "free up", "clean", "bin", "trash", "cache", "full")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.data = GLib.get_user_data_dir()
        self.cache = GLib.get_user_cache_dir()

        overview = self.add_group()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6, margin_bottom=6)
        self.summary = Gtk.Label(xalign=0, css_classes=["title-3"])
        self.bar = Gtk.LevelBar(min_value=0, max_value=1)
        self.bar.add_offset_value("low", 0.80)
        self.bar.add_offset_value("high", 0.92)
        self.bar.add_offset_value("full", 1.0)
        self.bar.update_property([Gtk.AccessibleProperty.LABEL], ["Disk used"])
        self.detail = Gtk.Label(xalign=0, css_classes=["dim-label"])
        for widget in (self.summary, self.bar, self.detail):
            box.append(widget)
        overview.add(box)

        self.clean = self.add_group("Free up space", "Nothing here removes your documents, photos or apps.")
        self.bin_row = self.action_row("Bin", "Files you deleted", "Empty Bin", self.empty_bin, destructive=True)
        self.thumbs_row = self.action_row("Picture previews", "Small previews Files makes; made again when "
                                          "needed", "Clear", self.clear_thumbnails)
        self.apt_row = self.action_row("Downloaded update files", "Kept after updates install; not needed",
                                       "Remove", lambda: self.helper(["clean-cache"], "Update files removed"))
        self.old_row = self.action_row("Old system files", "Old Linux kernels and apps nothing needs any more",
                                       "Remove", lambda: self.helper(["cleanup"], "Old system files removed"))
        self.logs_row = self.action_row("System logs", "Keep only the last two weeks", "Trim",
                                        lambda: self.helper(["clean-logs"], "System logs trimmed"))
        self.buttons = [row.button for row in (self.apt_row, self.old_row, self.logs_row)]

        more = self.add_group()
        row = Adw.ActionRow(title="See what uses space", subtitle="Disk Usage Analyzer shows folders by size",
                            activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r: launch("org.gnome.baobab.desktop", self.toast))
        more.add(row)

        self.listen_busy(self.on_busy)
        self.shown()

    def action_row(self, title: str, subtitle: str, label: str, action, destructive: bool = False) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.size_label = Gtk.Label(css_classes=["dim-label", "numeric"], valign=Gtk.Align.CENTER)
        row.button = Gtk.Button(label=label, valign=Gtk.Align.CENTER,
                                css_classes=["destructive-action"] if destructive else [])
        row.button.update_property([Gtk.AccessibleProperty.LABEL], [f"{label}: {title}"])
        row.button.connect("clicked", lambda _b: action())
        row.add_suffix(row.size_label)
        row.add_suffix(row.button)
        self.clean.add(row)
        return row

    # --- measuring (in a thread: a big bin can take a moment) --------------------------
    def shown(self) -> None:
        used, total = disk_usage()
        free = total - used
        self.summary.set_label(f"{GLib.format_size(free)} free")
        self.bar.set_value(used / total if total else 0)
        self.detail.set_label(f"{GLib.format_size(used)} used of {GLib.format_size(total)}")
        for row in (self.bin_row, self.thumbs_row, self.apt_row, self.old_row):
            row.size_label.set_label("…")
        threading.Thread(target=self.measure, daemon=True).start()

    def measure(self) -> None:
        sizes = {
            "bin": folder_size(Path(self.data) / "Trash"),
            "thumbs": folder_size(Path(self.cache) / "thumbnails"),
            "apt": folder_size(APT_ARCHIVES),
            "old": unneeded_packages(),
        }
        GLib.idle_add(self.show_sizes, sizes)

    def show_sizes(self, sizes: dict) -> bool:
        for row, key in ((self.bin_row, "bin"), (self.thumbs_row, "thumbs"), (self.apt_row, "apt")):
            row.size_label.set_label(GLib.format_size(sizes[key]) if sizes[key] else "Nothing")
            row.button.set_sensitive(bool(sizes[key]) and (row is not self.apt_row or not self.window.helper.busy))
        count = sizes["old"]
        self.old_row.size_label.set_label(f"{count} package{'s' if count != 1 else ''}" if count else "Nothing")
        self.old_row.button.set_sensitive(bool(count) and not self.window.helper.busy)
        return GLib.SOURCE_REMOVE

    # --- cleaning ---------------------------------------------------------------------
    def empty_bin(self) -> None:
        dialog = Adw.AlertDialog(heading="Empty the bin?",
                                 body="Everything in the bin is deleted for good.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("empty", "Empty Bin")
        dialog.set_response_appearance("empty", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: r == "empty" and self.run_user(["gio", "trash", "--empty"],
                                                                               "Bin emptied"))
        dialog.present(self.window)

    def clear_thumbnails(self) -> None:
        shutil.rmtree(Path(self.cache) / "thumbnails", ignore_errors=True)
        self.toast("Picture previews cleared")
        self.shown()

    def run_user(self, argv: list[str], message: str) -> None:
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as exc:
            self.toast(f"Couldn’t do that: {exc.message}")
            return

        def done(p, result) -> None:
            try:
                ok = p.wait_check_finish(result)
            except GLib.Error:
                ok = False
            self.toast(message if ok else "Couldn’t do that")
            self.shown()

        proc.wait_check_async(None, done)

    def helper(self, args: list[str], message: str) -> None:
        self.window.helper.run(args, message, on_done=lambda _ok: self.shown())

    def on_busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.set_sensitive(not busy)
