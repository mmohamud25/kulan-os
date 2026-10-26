"""Backups: your files, copied to a USB drive or another disk, encrypted, on a
schedule; and getting them back, all of them or one file as it was earlier.

Déjà Dup does the work (its own settings, org.gnome.DejaDup, and its
windows for the first password, progress and restoring); this page is the one
place to set it up and use it. Restore points (Software Update) cover the
system; backups cover your files.
"""
from __future__ import annotations

import datetime
import shutil

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, choice_row, has_key, launch, settings

DEJA_DUP = "org.gnome.DejaDup"
APP_ID = "org.gnome.DejaDup.desktop"
HOW_OFTEN = [("1", "Every day"), ("7", "Every week")]
KEEP = [("0", "As long as there’s room"), ("365", "At least a year"), ("182", "At least six months"),
        ("90", "At least three months")]
OTHER_FOLDER = "__folder__"


def when_text(stamp: str) -> str:
    """'Today at 10:42', 'Yesterday', '3 October' from Déjà Dup's ISO 8601 time."""
    if not stamp:
        return "Never"
    try:
        when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return "Never"
    today = datetime.datetime.now().astimezone().date()
    if when.date() == today:
        return f"Today at {when:%H:%M}"
    if when.date() == today - datetime.timedelta(days=1):
        return f"Yesterday at {when:%H:%M}"
    return f"{when.day} {when:%B %Y}" if when.year != today.year else f"{when.day} {when:%B}"


def on_system_disk(path: str) -> bool:
    """Whether a folder is on the same disk as the system or the home folder."""
    import os
    try:
        dev = os.stat(path).st_dev
        return dev in (os.stat("/").st_dev, os.stat(GLib.get_home_dir()).st_dev)
    except OSError:
        return True


def drives() -> list[tuple[str, str, str]]:
    """Disks you can back up to: (uuid, name, icon) of removable or extra
    disks that have a file system (USB drives, a second internal disk)."""
    found = []
    for volume in Gio.VolumeMonitor.get().get_volumes():
        uuid = volume.get_uuid() or volume.get_identifier(Gio.VOLUME_IDENTIFIER_KIND_UUID)
        drive = volume.get_drive()
        if not uuid or drive is None:
            continue
        mount = volume.get_mount()
        if mount is not None and mount.get_root().get_path() in ("/", "/boot", "/boot/efi", "/home"):
            continue
        if not (drive.is_removable() or drive.is_media_removable() or drive.can_eject() or mount is not None):
            continue
        icon = volume.get_icon()
        found.append((uuid, volume.get_name() or "Disk", icon.to_string() if icon else "drive-removable-media"))
    return found


class BackupsPage(Page):
    title = "Backups"
    icon = "document-save-symbolic"
    keywords = ("backup", "backups", "back up", "restore", "earlier version", "previous version", "deja dup",
                "déjà dup", "usb drive", "external drive", "time machine", "copy files", "lost file")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.deja = settings(DEJA_DUP)
        self.drive = settings(f"{DEJA_DUP}.Drive")
        self.local = settings(f"{DEJA_DUP}.Local")
        if self.deja is None or shutil.which("deja-dup") is None:
            group = self.add_group(None, "Backups need Déjà Dup, which isn’t installed. Get it in Software.")
            group.add(Adw.ActionRow(title="Restore points still protect the system",
                                    subtitle="Software Update takes one before every update"))
            return

        status = self.add_group(None, "Copies of your files on another disk, safe if this computer is lost "
                                      "or broken. Restore points cover the system.")
        self.last_row = Adw.ActionRow(title="Last backup")
        self.last_row.add_css_class("property")
        self.now_button = Gtk.Button(label="Back Up Now", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.now_button.connect("clicked", lambda _b: self.back_up_now())
        self.last_row.add_suffix(self.now_button)
        status.add(self.last_row)

        where = self.add_group("Where")
        self.where_row = Adw.ComboRow(title="Back up to", subtitle="A USB drive or another disk, kept away "
                                      "from the computer", subtitle_lines=0)
        where.add(self.where_row)
        self.fill_places()
        self.watch(Gio.VolumeMonitor.get(), "volume-added", lambda *_a: self.fill_places())
        self.watch(Gio.VolumeMonitor.get(), "volume-removed", lambda *_a: self.fill_places())
        where.add(Adw.ActionRow(title="Encrypted", subtitle="The first backup asks for a password. Keep it "
                                "safe. Without it, nobody can restore your files, not even you", subtitle_lines=0))

        schedule = self.add_group("Automatically")
        if has_key(self.deja, "periodic"):
            auto = Adw.SwitchRow(title="Back up automatically",
                                 subtitle="In the background, when the drive is connected")
            self.deja.bind("periodic", auto, "active", Gio.SettingsBindFlags.DEFAULT)
            schedule.add(auto)
            if has_key(self.deja, "periodic-period"):
                often = choice_row("How often", None, HOW_OFTEN,
                                   lambda: "1" if self.deja.get_int("periodic-period") <= 1 else "7",
                                   lambda v: self.deja.set_int("periodic-period", int(v)))
                self.deja.bind("periodic", often, "sensitive", Gio.SettingsBindFlags.GET)
                schedule.add(often)
        if has_key(self.deja, "delete-after"):
            schedule.add(choice_row("Keep old backups", "Older ones are deleted when the drive fills up",
                                    KEEP, lambda: self.keep_value(), lambda v: self.deja.set_int("delete-after", int(v))))
        what = Adw.ActionRow(title="What’s backed up", subtitle=self.what_text(), activatable=True,
                             subtitle_lines=0)
        what.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        what.connect("activated", lambda _r: self.open_deja())
        schedule.add(what)

        restore = self.add_group("Get files back")
        everything = Adw.ActionRow(title="Restore files", subtitle="Browse a backup and choose what to bring back",
                                   activatable=True)
        everything.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        everything.connect("activated", lambda _r: self.open_deja())
        restore.add(everything)
        earlier = Adw.ActionRow(title="An earlier version of a file",
                                subtitle="Choose a file, then the day to bring back", activatable=True)
        earlier.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        earlier.connect("activated", lambda _r: self.choose_earlier())
        restore.add(earlier)

        self.watch(self.deja, "changed::last-backup", lambda *_a: self.shown())
        self.shown()

    # --- state ------------------------------------------------------------------------
    def shown(self) -> None:
        if self.deja is None:
            return
        stamp = self.deja.get_string("last-backup") if has_key(self.deja, "last-backup") else ""
        text = when_text(stamp)
        if stamp and not self.deja.get_boolean("periodic"):
            text += ". Automatic backups are off"
        elif not stamp:
            text = "Never. Choose a drive below, then Back Up Now"
        self.last_row.set_subtitle(GLib.markup_escape_text(text))

    def keep_value(self) -> str:
        days = self.deja.get_int("delete-after")
        values = [int(v) for v, _l in KEEP]
        return str(min(values, key=lambda v: abs(v - days)) if days else 0)

    def what_text(self) -> str:
        include = self.deja.get_strv("include-list") if has_key(self.deja, "include-list") else []
        exclude = self.deja.get_strv("exclude-list") if has_key(self.deja, "exclude-list") else []
        if include == ["$HOME"] and set(exclude) <= {"$TRASH", "$DOWNLOAD"}:
            skipped = " and ".join(name for key, name in (("$DOWNLOAD", "Downloads"), ("$TRASH", "the bin"))
                                   if key in exclude)
            return "Your home folder" + (f", except {skipped}" if skipped else "") + ". Change in Déjà Dup"
        return f"{len(include)} folder(s), {len(exclude)} left out. Change in Déjà Dup"

    # --- where ------------------------------------------------------------------------
    def fill_places(self) -> None:
        self.places = [(uuid, name, icon) for uuid, name, icon in drives()]
        current = self.deja.get_string("backend") if has_key(self.deja, "backend") else ""
        chosen_uuid = self.drive.get_string("uuid") if self.drive is not None else ""
        if current == "drive" and chosen_uuid and chosen_uuid not in [p[0] for p in self.places]:
            # The chosen drive isn't plugged in now: still show it.
            self.places.insert(0, (chosen_uuid, self.drive.get_string("name") or "Your backup drive",
                                   self.drive.get_string("icon")))
        labels = [name for _u, name, _i in self.places]
        folder = self.local.get_string("folder") if self.local is not None else ""
        labels.append(f"Folder: {folder}" if current == "local" and folder.startswith("/") else "A folder…")
        # Nothing chosen yet: say so first, rather than seem to have picked a drive.
        chosen = (current == "drive" and chosen_uuid) or current == "local"
        self.offset = 0 if chosen else 1
        if not chosen:
            labels.insert(0, "Choose…" if self.places else "Plug in a USB drive")
        self._filling = True  # what's selected here is the current setting, not a choice
        self.where_row.set_model(Gtk.StringList.new(labels))
        if current == "drive" and chosen_uuid:
            self.where_row.set_selected([p[0] for p in self.places].index(chosen_uuid))
        elif current == "local":
            self.where_row.set_selected(len(labels) - 1)
        else:
            self.where_row.set_selected(0)
        self._filling = False
        if not getattr(self, "_place_handler", 0):
            self._place_handler = self.where_row.connect("notify::selected", self.on_place)

    def on_place(self, row: Adw.ComboRow, _pspec) -> None:
        if self._filling:
            return
        index = row.get_selected()
        offset = self.offset  # "Choose…" comes first while nothing is chosen
        if index == row.get_model().get_n_items() - 1:
            self.choose_folder()
            return
        if index - offset < 0 or index - offset >= len(self.places):
            return
        uuid, name, icon = self.places[index - offset]
        self.drive.set_string("uuid", uuid)
        self.drive.set_string("name", name)
        self.drive.set_string("icon", icon)
        self.deja.set_string("backend", "drive")
        GLib.idle_add(lambda: (self.fill_places(), False)[1])  # the "Choose…" line goes
        self.toast(f"Backups go to {name}")

    def choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Back up to a folder", modal=True)

        def done(_d, result) -> None:
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                self.fill_places()  # cancelled: back to what was chosen
                return
            path = folder.get_path() if folder else None
            if not path:
                self.fill_places()
                return
            if on_system_disk(path):
                self.toast("Choose a folder on another disk. A backup on this computer’s own disk doesn’t "
                           "protect it.")
                self.fill_places()
                return
            self.local.set_string("folder", path)
            self.deja.set_string("backend", "local")
            self.toast(f"Backups go to {path}")
            self.fill_places()
        dialog.select_folder(self.window, None, done)

    # --- actions ----------------------------------------------------------------------
    def back_up_now(self) -> None:
        backend = self.deja.get_string("backend") if has_key(self.deja, "backend") else "auto"
        if backend not in ("drive", "local", "remote", "google", "microsoft"):
            self.toast("Choose where to back up first")
            return
        try:
            Gio.Subprocess.new(["deja-dup", "--backup"], Gio.SubprocessFlags.NONE)
        except GLib.Error as exc:
            self.toast(f"Couldn’t start the backup: {exc.message}")
            return
        self.toast("Backing up. Déjà Dup shows the progress")

    def open_deja(self) -> None:
        launch(APP_ID, self.toast)

    def choose_earlier(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a file to bring back as it was", modal=True)
        dialog.set_initial_folder(Gio.File.new_for_path(GLib.get_home_dir()))

        def done(_d, result) -> None:
            try:
                chosen = dialog.open_finish(result)
            except GLib.Error:
                return
            if chosen is None or chosen.get_path() is None:
                return
            try:
                Gio.Subprocess.new(["deja-dup", "--restore", chosen.get_path()], Gio.SubprocessFlags.NONE)
            except GLib.Error as exc:
                self.toast(f"Couldn’t open Déjà Dup: {exc.message}")
        dialog.open(self.window, None, done)
