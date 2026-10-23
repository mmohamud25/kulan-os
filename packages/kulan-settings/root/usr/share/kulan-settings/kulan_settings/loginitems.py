"""Login Items: apps that open when you sign in (freedesktop autostart)."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page

SYSTEM_DIRS = [Path("/etc/xdg/autostart")]


def user_dir() -> Path:
    return Path(GLib.get_user_config_dir()) / "autostart"


def read_entry(path: Path) -> GLib.KeyFile | None:
    keyfile = GLib.KeyFile()
    try:
        keyfile.load_from_file(str(path), GLib.KeyFileFlags.KEEP_TRANSLATIONS)
    except GLib.Error:
        return None
    return keyfile


def get(keyfile: GLib.KeyFile, key: str, default: str = "") -> str:
    try:
        return keyfile.get_locale_string("Desktop Entry", key, None)
    except GLib.Error:
        return default


def get_bool(keyfile: GLib.KeyFile, key: str, default: bool) -> bool:
    try:
        return keyfile.get_boolean("Desktop Entry", key)
    except GLib.Error:
        return default


def shown_in_gnome(keyfile: GLib.KeyFile) -> bool:
    try:
        only = keyfile.get_string_list("Desktop Entry", "OnlyShowIn")
        if "GNOME" not in only:
            return False
    except GLib.Error:
        pass
    try:
        return "GNOME" not in keyfile.get_string_list("Desktop Entry", "NotShowIn")
    except GLib.Error:
        return True


def program(keyfile: GLib.KeyFile) -> str:
    """The program an entry runs (its first word, without the folder)."""
    try:
        argv = GLib.shell_parse_argv(get(keyfile, "Exec"))[1]
    except GLib.Error:
        return ""
    while argv and (argv[0] == "env" or "=" in argv[0]):
        argv = argv[1:]
    return Path(argv[0]).name if argv else ""


def is_session_part(keyfile: GLib.KeyFile) -> bool:
    try:
        keys = set(keyfile.get_keys("Desktop Entry")[0])
    except GLib.Error:
        return True
    return bool(keys & {"X-GNOME-Autostart-Phase", "X-GNOME-AutoRestart", "X-GNOME-HiddenUnderSystemd",
                        "X-GNOME-Provides"})


def visible_programs() -> set[str]:
    """The programs of the apps shown in the app grid."""
    names = set()
    for info in Gio.AppInfo.get_all():
        if info.should_show():
            exe = info.get_executable() or ""
            if exe:
                names.add(Path(exe).name)
    return names


def enabled(keyfile: GLib.KeyFile) -> bool:
    return not get_bool(keyfile, "Hidden", False) and get_bool(keyfile, "X-GNOME-Autostart-enabled", True)


class LoginItemsPage(Page):
    title = "Login Items"
    icon = "system-run-symbolic"
    keywords = ("startup", "login", "autostart", "open at login", "background apps")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.group = self.add_group("Open at login", "Apps that start when you sign in.")
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add an app", valign=Gtk.Align.CENTER,
                         css_classes=["flat"])
        add.update_property([Gtk.AccessibleProperty.LABEL], ["Add an app to open at login"])
        add.connect("clicked", lambda _b: self.pick_app())
        self.group.set_header_suffix(add)
        self.rows: list[Gtk.Widget] = []
        self.shown()

    # --- listing -----------------------------------------------------------------
    def entries(self) -> list[tuple[str, GLib.KeyFile, bool]]:
        """(file name, effective entry, user-added) for visible login items."""
        found: dict[str, tuple[GLib.KeyFile, bool]] = {}
        for directory in SYSTEM_DIRS:
            for path in sorted(directory.glob("*.desktop")):
                keyfile = read_entry(path)
                if keyfile is not None:
                    found[path.name] = (keyfile, False)
        for path in sorted(user_dir().glob("*.desktop")):
            keyfile = read_entry(path)
            if keyfile is not None:
                found[path.name] = (keyfile, path.name not in found)
        apps = visible_programs()
        items = []
        for name, (keyfile, user_added) in found.items():
            if get_bool(keyfile, "NoDisplay", False) or not shown_in_gnome(keyfile):
                continue  # system services, not apps people choose
            # The system's own entries only when they're an app from the app
            # grid: background services (the SSH agent, the tray watcher,
            # parts of the desktop itself) aren't login items to choose.
            if not user_added and (is_session_part(keyfile) or program(keyfile) not in apps):
                continue
            if user_added or get(keyfile, "Name"):
                items.append((name, keyfile, user_added))
        return sorted(items, key=lambda item: get(item[1], "Name", item[0]).lower())

    def shown(self) -> None:
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        items = self.entries()
        if not items:
            row = Adw.ActionRow(title="No apps open at login", subtitle="Use + to add one")
            self.group.add(row)
            self.rows.append(row)
            return
        for name, keyfile, user_added in items:
            row = Adw.SwitchRow(title=GLib.markup_escape_text(get(keyfile, "Name", name)),
                                subtitle=GLib.markup_escape_text(get(keyfile, "Comment")),
                                active=enabled(keyfile))
            image = Gtk.Image(pixel_size=32)
            icon = get(keyfile, "Icon")
            try:
                image.set_from_gicon(Gio.Icon.new_for_string(icon)) if icon else image.set_from_icon_name(
                    "application-x-executable-symbolic")
            except GLib.Error:
                image.set_from_icon_name("application-x-executable-symbolic")
            row.add_prefix(image)
            row.connect("notify::active", lambda r, _p, n=name: self.set_enabled(n, r.get_active()))
            if user_added:
                remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove",
                                    valign=Gtk.Align.CENTER, css_classes=["flat"])
                remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {get(keyfile, 'Name', name)}"])
                remove.connect("clicked", lambda _b, n=name: self.remove(n))
                row.add_suffix(remove)
            self.group.add(row)
            self.rows.append(row)

    # --- changes -------------------------------------------------------------------
    def set_enabled(self, name: str, on: bool) -> None:
        """Turn an item on or off with a copy in the user's autostart folder,
        which overrides the system one (the freedesktop rule)."""
        target = user_dir() / name
        source = target if target.exists() else next((d / name for d in SYSTEM_DIRS if (d / name).exists()), None)
        if source is None:
            return
        keyfile = read_entry(source)
        if keyfile is None:
            return
        keyfile.set_boolean("Desktop Entry", "X-GNOME-Autostart-enabled", on)
        keyfile.set_boolean("Desktop Entry", "Hidden", False)
        target.parent.mkdir(parents=True, exist_ok=True)
        keyfile.save_to_file(str(target))

    def remove(self, name: str) -> None:
        try:
            (user_dir() / name).unlink()
        except OSError:
            pass
        self.shown()
        self.toast("Removed from login items")

    def pick_app(self) -> None:
        dialog = Adw.Dialog(title="Open at login", content_width=420, content_height=520)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        search = Gtk.SearchEntry(placeholder_text="Search apps", margin_start=12, margin_end=12, margin_top=6)
        listbox = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                              margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        apps = sorted((a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id()),
                      key=lambda a: a.get_display_name().lower())
        for app in apps:
            row = Adw.ActionRow(title=GLib.markup_escape_text(app.get_display_name()), activatable=True)
            row.add_prefix(Gtk.Image(gicon=app.get_icon(), pixel_size=32))
            row.app = app
            row.connect("activated", lambda r, d=dialog: (self.add_app(r.app), d.close()))
            listbox.append(row)

        def matches(row) -> bool:
            text = search.get_text().strip().lower()
            return not text or text in row.app.get_display_name().lower()

        listbox.set_filter_func(matches)
        search.connect("search-changed", lambda _s: listbox.invalidate_filter())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(search)
        scroller = Gtk.ScrolledWindow(vexpand=True, child=listbox)
        box.append(scroller)
        view.set_content(box)
        dialog.set_child(view)
        dialog.present(self.window)

    def add_app(self, app: Gio.AppInfo) -> None:
        source = app.get_filename() if isinstance(app, Gio.DesktopAppInfo) else None
        keyfile = read_entry(Path(source)) if source else None
        if keyfile is None:
            self.toast("Couldn’t add that app")
            return
        keyfile.set_boolean("Desktop Entry", "X-GNOME-Autostart-enabled", True)
        target = user_dir() / app.get_id()
        target.parent.mkdir(parents=True, exist_ok=True)
        keyfile.save_to_file(str(target))
        self.shown()
        self.toast(f"{app.get_display_name()} will open at login")
