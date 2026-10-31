"""Kulan Settings: the window, sidebar and command line."""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import time

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .about import AboutPage
from .accessibility import AccessibilityPage
from .accounts import AccountsPage
from .addons import AddonsPage
from .apps import AppsPage
from .appearance import AppearancePage
from .assistant import AssistantPage
from .backups import BackupsPage
from .battery import BatteryPage
from .bluetooth import BluetoothPage
from .common import Helper, open_gnome_panel
from .dateandtime import DateTimePage
from .desktop import DesktopPage
from .display import DisplayPage
from .family import FamilyPage
from .dock import DockPage
from .focus import FocusPage
from .help import HelpPage
from .keyboard import KeyboardPage
from .loginitems import LoginItemsPage
from .mouse import MousePage
from .notifications import NotificationsPage
from .phone import PhonePage
from .prayer import PrayerPage
from .printers import PrintersPage
from .privacy import PrivacyPage
from .region import RegionPage
from .security import SecurityPage
from .sharing import SharingPage
from .sound import SoundPage
from .storage import StoragePage
from .topbar import TopBarPage
from .updates import UpdatesPage
from .users import UsersPage
from .wifi import WifiPage

APP_ID = "org.kulan.Settings"

# Sidebar order: (id, page class). The id is what `kulan-settings <id>` opens.
# Related pages sit together, as in a Mac's System Settings; GROUP_STARTS
# marks the first page of each group, which gets a little space above it.
PAGES = [
    # Connections
    ("wifi", WifiPage),
    ("bluetooth", BluetoothPage),
    ("phone", PhonePage),
    ("printers", PrintersPage),
    ("sharing", SharingPage),
    # Sound and attention
    ("sound", SoundPage),
    ("focus", FocusPage),
    ("notifications", NotificationsPage),
    ("users", UsersPage),
    ("accounts", AccountsPage),
    ("parental-controls", FamilyPage),
    # How it looks
    ("appearance", AppearancePage),
    ("desktop", DesktopPage),
    ("dock", DockPage),
    ("top-bar", TopBarPage),
    ("display", DisplayPage),
    # Power and space
    ("battery", BatteryPage),
    ("storage", StoragePage),
    ("login-items", LoginItemsPage),
    ("apps", AppsPage),
    # Protection
    ("security", SecurityPage),
    ("privacy", PrivacyPage),
    ("backups", BackupsPage),
    # Input and helpers
    ("keyboard", KeyboardPage),
    ("mouse", MousePage),
    ("accessibility", AccessibilityPage),
    ("assistant", AssistantPage),
    ("prayer-times", PrayerPage),
    # This computer
    ("date-time", DateTimePage),
    ("region", RegionPage),
    ("updates", UpdatesPage),
    ("add-ons", AddonsPage),
    ("help", HelpPage),
    ("about", AboutPage),
]
GROUP_STARTS = {"sound", "appearance", "battery", "security", "keyboard", "date-time"}

# Each page's tile colour in the sidebar (2.4 look): a white symbol on a
# rounded square, like a Mac's System Settings.
TILE_COLOURS = {
    "wifi": "#0d9488", "bluetooth": "#3b82f6", "phone": "#16a34a", "printers": "#475569", "sharing": "#0ea5e9",
    "sound": "#ef4444", "focus": "#8b5cf6", "notifications": "#f43f5e", "users": "#0ea5e9", "accounts": "#2563eb", "parental-controls": "#f97316",
    "appearance": "#475569", "desktop": "#f59e0b", "dock": "#0ea5e9", "top-bar": "#64748b", "display": "#6366f1",
    "battery": "#10b981", "storage": "#64748b", "login-items": "#94a3b8", "apps": "#8b5cf6",
    "security": "#0f766e", "privacy": "#2563eb", "backups": "#0891b2",
    "keyboard": "#64748b", "mouse": "#64748b", "accessibility": "#2563eb", "assistant": "#7c3aed", "prayer-times": "#d97706",
    "date-time": "#0ea5e9", "region": "#0d9488", "updates": "#64748b", "add-ons": "#ec4899", "help": "#0d9488", "about": "#334155",
}
EXTERNAL_TILE = "#94a3b8"

STYLE = """
.kulan-tile { min-width: 26px; min-height: 26px; border-radius: 8px; color: #ffffff;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.22); }
.kulan-tile > image { -gtk-icon-size: 15px; }
.navigation-sidebar > row { border-radius: 9px; }
.navigation-sidebar > row:selected { background-color: @accent_bg_color; color: @accent_fg_color; }
.navigation-sidebar > row:selected .dim-label { color: @accent_fg_color; }
.kulan-page-title { font-family: Manrope, Inter, sans-serif; font-weight: 800; font-size: 26px; }
list.boxed-list { border-radius: 14px; }
list.boxed-list > row:first-child { border-top-left-radius: 14px; border-top-right-radius: 14px; }
list.boxed-list > row:last-child { border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }
preferencespage > scrolledwindow > viewport > clamp > box { border-spacing: 30px; }
.kulan-update-done { background-color: alpha(@window_fg_color, 0.55); color: @window_bg_color;
  border-radius: 999px; padding: 18px; -gtk-icon-size: 40px; }
""" + "".join(f".kulan-tile.tile-{pid} {{ background-color: {colour}; }}\n" for pid, colour in TILE_COLOURS.items()) \
    + f".kulan-tile.tile-external {{ background-color: {EXTERNAL_TILE}; }}\n"


# Pages that live in GNOME Settings, listed so everything is findable from here
# (Online accounts has its own page now: accounts.py).
GNOME_PANELS: list[tuple[str, str, tuple[str, ...]]] = []


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Kulan Settings")
        self.set_default_size(980, 720)
        self.set_size_request(360, 480)
        self.helper = Helper(self.toast)
        style = Gtk.CssProvider()
        style.load_from_string(STYLE)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), style,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.pages: dict[str, Gtk.Widget] = {}
        self.current: str | None = None

        # Sidebar
        self.sidebar_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.sidebar_list.connect("row-activated", self.on_row)
        self.rows: dict[str, Gtk.ListBoxRow] = {}
        for page_id, cls in PAGES:
            row = self.sidebar_row(cls.title, cls.icon, page_id=page_id)
            row.page_id = page_id
            row.keywords = " ".join((cls.title, *cls.keywords)).lower()
            self.rows[page_id] = row
            self.sidebar_list.append(row)
        heading = Gtk.ListBoxRow(activatable=False, selectable=False)
        heading.set_child(Gtk.Label(label="More in the Settings app", xalign=0, css_classes=["heading", "dim-label"],
                                    margin_top=12, margin_start=6))
        heading.page_id = None
        heading.keywords = ""
        if GNOME_PANELS:
            self.sidebar_list.append(heading)
        self.heading = heading
        for title, icon, panel in GNOME_PANELS:
            row = self.sidebar_row(title, icon, external=True)
            row.page_id = None
            row.panel = panel
            row.keywords = title.lower()
            self.sidebar_list.append(row)

        self.search = Gtk.SearchEntry(placeholder_text="Search settings", margin_start=12, margin_end=12,
                                      margin_bottom=6)
        self.search.connect("search-changed", lambda _s: (self.sidebar_list.invalidate_filter(),
                                                          self.sidebar_list.invalidate_headers()))
        self.sidebar_list.set_filter_func(self.filter_row)
        self.sidebar_list.set_header_func(self.group_gap)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.append(self.search)
        sidebar_box.append(Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                              child=self.sidebar_list))
        sidebar_view = Adw.ToolbarView()
        # "Settings" here: next to the window buttons the full name gets cut
        # off ("Kulan Setti…"); the window itself is still "Kulan Settings".
        sidebar_view.add_top_bar(Adw.HeaderBar())
        sidebar_view.set_content(sidebar_box)
        sidebar = Adw.NavigationPage(title="Settings", child=sidebar_view)

        # Content
        self.content_title = Adw.WindowTitle()
        content_header = Adw.HeaderBar(title_widget=self.content_title)
        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)
        content_view = Adw.ToolbarView()
        content_view.add_top_bar(content_header)
        content_view.set_content(self.toasts)
        self.content = Adw.NavigationPage(title="Settings", child=content_view)

        self.split = Adw.NavigationSplitView(sidebar=sidebar, content=self.content, min_sidebar_width=240,
                                             max_sidebar_width=300)
        self.set_content(self.split)

        # Narrow windows (phone width): sidebar and page stack instead.
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)

        search_action = Gio.SimpleAction.new("search", None)
        search_action.connect("activate", lambda *_a: self.search.grab_focus())
        self.add_action(search_action)

    @staticmethod
    def pick_icon(names: str) -> str:
        """The first of "a|b|c" the icon theme has (GNOME renames panel icons)."""
        candidates = names.split("|")
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            for name in candidates:
                if theme.has_icon(name):
                    return name
        return candidates[-1] if display is None else "preferences-system-symbolic"

    @staticmethod
    def sidebar_row(title: str, icon: str, external: bool = False, page_id: str | None = None) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=10, margin_top=5, margin_bottom=5, margin_start=4, margin_end=6)
        tile = Gtk.Box(css_classes=["kulan-tile", f"tile-{page_id}" if page_id else "tile-external"],
                       valign=Gtk.Align.CENTER, halign=Gtk.Align.START, hexpand=False)
        tile.append(Gtk.Image(icon_name=SettingsWindow.pick_icon(icon), halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, hexpand=True, vexpand=True))
        tile.set_size_request(26, 26)
        box.append(tile)
        box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
        if external:
            box.append(Gtk.Image(icon_name="adw-external-link-symbolic", css_classes=["dim-label"]))
        row = Gtk.ListBoxRow(child=box)
        row.update_property([Gtk.AccessibleProperty.LABEL],
                            [f"{title} (opens the Settings app)" if external else title])
        return row

    def group_gap(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        """A little space above the first page of each group (none while searching)."""
        gap = (before is not None and getattr(row, "page_id", None) in GROUP_STARTS
               and not self.search.get_text().strip())
        if not gap:
            if row.get_header() is not None:
                row.set_header(None)
        elif row.get_header() is None:
            row.set_header(Gtk.Box(height_request=12, can_target=False))

    def filter_row(self, row: Gtk.ListBoxRow) -> bool:
        text = self.search.get_text().strip().lower()
        if not text:
            return True
        if row is self.heading:
            return False
        return all(word in row.keywords for word in text.split())

    def on_row(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if getattr(row, "page_id", None):
            self.show_page(row.page_id)
        elif getattr(row, "panel", None):
            open_gnome_panel(*row.panel)
            if self.current:  # keep the Kulan page selected
                self.sidebar_list.select_row(self.rows[self.current])

    def show_page(self, page_id: str) -> None:
        cls = dict(PAGES).get(page_id)
        if cls is None:
            page_id, cls = PAGES[0]
        if page_id not in self.pages:
            page = cls(self)
            self.pages[page_id] = page
            self.stack.add_named(page, page_id)
        else:
            self.pages[page_id].shown()
        self.stack.set_visible_child_name(page_id)
        self.content_title.set_title("")  # the page shows its own large title
        self.content.set_title(cls.title)
        self.current = page_id
        self.sidebar_list.select_row(self.rows[page_id])
        self.split.set_show_content(True)

    def reload_page(self, page) -> None:
        """Rebuild a page after a reset, so every row shows the new values."""
        page_id = next((pid for pid, p in self.pages.items() if p is page), None)
        if page_id is None:
            return
        page.teardown()
        self.stack.remove(page)
        del self.pages[page_id]
        self.show_page(page_id)

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))


# Programs the pages ask (self-test: made slow, to show none is waited for on the window's thread).
SLOW_PROGRAMS = ("lscpu", "lspci", "gnome-shell", "systemctl", "findmnt", "lsblk", "dpkg-query", "flatpak",
                 "locale", "openssl")
SLOW_S = 3


class KulanSettings(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("check", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open Software Update and check for updates", None)
        self.add_main_option("self-test", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open every page, then quit", None)
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("window.close", ["<Control>w", "<Control>q"])

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict().end().unpack()
        args = command_line.get_arguments()[1:]
        page_id = args[0] if args else None
        window = self.props.active_window or SettingsWindow(self)
        if options.get("self-test"):
            return self.self_test(window)
        if options.get("check"):
            page_id = "updates"
        window.show_page(page_id or window.current or PAGES[0][0])
        window.present()
        if options.get("check"):
            GLib.idle_add(lambda: window.pages["updates"].check() and False)
        return 0

    def self_test(self, window: SettingsWindow) -> int:
        failures = []
        # Mistakes in replies that arrive later (threads, D-Bus) count as failures too.
        late_errors: list[str] = []
        excepthook = sys.excepthook

        def caught(kind, value, tb) -> None:
            late_errors.append(f"{kind.__name__}: {value}")
            excepthook(kind, value, tb)
        sys.excepthook = caught
        # Nothing slow on the window's thread (2.11): with the programs pages ask
        # made slow, every page still opens at once and fills in afterwards.
        slow = tempfile.mkdtemp(prefix="kulan-slow-")
        for name in SLOW_PROGRAMS:
            fake = os.path.join(slow, name)
            with open(fake, "w") as f:
                f.write(f"#!/bin/sh\nsleep {SLOW_S}\n")
            os.chmod(fake, 0o755)
        path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{slow}:{path}"
        started = time.monotonic()
        try:
            for page_id, _cls in PAGES:
                try:
                    window.show_page(page_id)
                except Exception:  # noqa: BLE001,S110 - reported by the pass below
                    pass
        finally:
            os.environ["PATH"] = path
        took = time.monotonic() - started
        if took > SLOW_S * 2:
            failures.append(f"opening every page took {took:.1f} s with slow programs: something waits for "
                            "them on the window's thread")
        for page_id, _cls in PAGES:
            try:
                window.show_page(page_id)
                listeners = len(window.helper.listeners)
                # Rebuilding a page (what Reset does) must not leave the old one listening.
                window.reload_page(window.pages[page_id])
                window.reload_page(window.pages[page_id])
                if len(window.helper.listeners) != listeners:
                    failures.append(f"{page_id}: old page still listening after a rebuild")
            except Exception as exc:  # noqa: BLE001 - report every page, then fail
                failures.append(f"{page_id}: {exc!r}")
        try:  # Software Update's plain-language grouping of updates
            from . import updatelist
            updatelist.self_test()
        except AssertionError as exc:
            failures.append(f"updates: grouping gave {exc}")
        try:  # Get help: screenshots made small enough to send, and the person's own requests
            from . import help as get_help
            get_help.self_test(window)
        except AssertionError as exc:
            failures.append(f"help: {exc}")
        try:  # Security: signed start-up in each of its steps
            window.show_page("security")
            page = window.pages["security"]
            page.render_signed_start({"phase": "enroll", "code": "48207165", "secure_boot": True})
            assert "48207165" in page.signed_row.get_subtitle() and page.signed_button.get_label() == "Restart"
            page.render_signed_start({"phase": "off", "possible": False,
                                      "reasons": ["Turn TPM unlock off first, and on again afterwards"]})
            assert "TPM" in page.signed_row.get_subtitle() and not page.signed_button.get_sensitive()
            page.render_signed_start({"phase": "on", "secure_boot": False})
            assert "Secure Boot is off" in page.signed_row.get_subtitle() and not page.signed_button.get_visible()
        except (AssertionError, KeyError) as exc:
            failures.append(f"security: signed start-up {exc!r}")
        try:  # Battery: health in words, and apps named from their cgroups
            from . import battery
            assert battery.health_text(51.48, 50.5, 100, 494).startswith("Good: holds 100%")
            assert "494 charge cycles" in battery.health_text(51.48, 50.5, 100, 494)
            assert battery.health_text(35, 50, 0, -1).startswith("Worn: holds 70%")
            assert battery.health_text(20, 50, 0, -1).startswith("Worn out")
            assert battery.app_id("app-gnome-firefox\\x2desr-12345.scope") == "firefox-esr"
            assert battery.app_id("app-flatpak-com.spotify.Client-3456.scope") == "com.spotify.Client"
            assert battery.app_id("kulan-assistant-engine.service") == "Ask Kulan"
            assert battery.app_id("dbus.service") is None
            window.show_page("battery")
            window.pages["battery"].sample_apps()
        except (AssertionError, KeyError) as exc:
            failures.append(f"battery: {exc!r}")
        try:  # Messages in plain words, never a program's own output
            from . import bluetooth, common, users
            ft = common.failure_text
            assert ft([], "Not enough free space.") == "Not enough free space"
            assert ft(["E: Temporary failure resolving 'deb.debian.org'"]).startswith("Not connected to the internet")
            assert ft(["E: Could not get lock /var/lib/dpkg/lock-frontend"]).startswith("Another update is running")
            assert ft(["Traceback (most recent call last):"]).startswith("That didn’t work")
            assert common.plain_error("GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: x").startswith(
                "The part of Kulan OS")
            assert common.plain_error("Not authorized").startswith("Permission wasn’t given")
            assert common.plain_error("xyz 0x3f").startswith("Try again")
            assert common.progress_text("pmstatus", "firefox-esr:amd64", "Unpacking firefox-esr (amd64)") == \
                "Installing Firefox"
            assert common.progress_text("pmstatus", "restore-point", "Creating a restore point") == \
                "Creating a restore point"
            assert common.progress_text("dlstatus", "x", "Retrieving file 3 of 9") == "Downloading"
            assert bluetooth.bluez_reason("org.bluez.Error.AuthenticationFailed", "").startswith("The code didn’t")
            assert users.user_name_from("Bin Li", {"bin"}) == "binl"
            assert users.user_name_from("Amina Yusuf", {"amina"}) not in ("amina", "")
        except (AssertionError, AttributeError) as exc:
            failures.append(f"messages: {exc!r}")
        try:  # Restart to finish updating, Mac-style (2.12): asked as soon as an install that needs one ends
            from . import updates
            window.show_page("updates")
            page = window.pages["updates"]
            note = os.path.join(GLib.get_user_runtime_dir(), updates.RESTART_ASKED_NOTE)
            with contextlib.suppress(OSError):
                os.unlink(note)
            run, needed = window.helper.run, page.restart_needed
            window.helper.run = lambda *_a, on_done=None, **_k: on_done and on_done(True)
            try:
                page.restart_needed = lambda: []
                page.install()
                assert not os.path.exists(note), "asked to restart though none is needed"
                page.restart_needed = lambda: ["Linux"]
                page.install()
                assert os.path.exists(note), "not asked to restart after an install that needs one"
                os.unlink(note)
                page.run(["check"], "Checking…", None)
                assert not os.path.exists(note), "asked to restart after a check"
            finally:
                window.helper.run, page.restart_needed = run, needed
        except (AssertionError, KeyError, OSError) as exc:
            failures.append(f"updates: restart {exc!r}")
        try:  # Connected the first time (2.11): hotspot code, models for the memory, accounts in words
            from . import accounts, assistant, netextras
            assert netextras.wifi_qr_text("Home;Net", "p:w\\1") == "WIFI:T:WPA;S:Home\\;Net;P:p\\:w\\\\1;;"
            writing = [{"id": "s", "memory_gb": 3}, {"id": "m", "memory_gb": 4}, {"id": "l", "memory_gb": 7}]
            assert assistant.recommended(writing, 2) is None, "a model recommended that can't run"
            assert assistant.recommended(writing, 4) == "s" and assistant.recommended(writing, 16) == "l"
            assert assistant.recommended(writing, 0) == "s" and not assistant.can_run(writing[2], 6)
            assert accounts.account_subtitle("ms_graph", "", True).startswith("Microsoft 365 · Signed out")
            assert accounts.provider_label("google") == "Google"
        except (AssertionError, AttributeError) as exc:
            failures.append(f"connected: {exc!r}")
        # Let the replies arrive (the slow programs answer after SLOW_S seconds).
        context = GLib.MainContext.default()
        deadline = time.monotonic() + SLOW_S + 2
        while time.monotonic() < deadline:
            if not context.iteration(False):
                time.sleep(0.02)
        sys.excepthook = excepthook
        shutil.rmtree(slow, ignore_errors=True)
        failures += [f"later: {error}" for error in late_errors]
        window.destroy()
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        print(f"{len(PAGES) - len(failures)}/{len(PAGES)} pages built")
        return 1 if failures else 0


def main(argv: list[str]) -> int:
    return KulanSettings().run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
