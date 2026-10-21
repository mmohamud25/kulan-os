"""Kulan Settings: the window, sidebar and command line."""
from __future__ import annotations

import sys

from gi.repository import Adw, Gio, GLib, Gtk

from .about import AboutPage
from .addons import AddonsPage
from .appearance import AppearancePage
from .assistant import AssistantPage
from .common import Helper, open_gnome_panel
from .desktop import DesktopPage
from .display import DisplayPage
from .dock import DockPage
from .focus import FocusPage
from .keyboard import KeyboardPage
from .loginitems import LoginItemsPage
from .privacy import PrivacyPage
from .security import SecurityPage
from .storage import StoragePage
from .topbar import TopBarPage
from .updates import UpdatesPage

APP_ID = "org.kulan.Settings"

# Sidebar order: (id, page class). The id is what `kulan-settings <id>` opens.
PAGES = [
    ("appearance", AppearancePage),
    ("dock", DockPage),
    ("top-bar", TopBarPage),
    ("desktop", DesktopPage),
    ("display", DisplayPage),
    ("keyboard", KeyboardPage),
    ("focus", FocusPage),
    ("assistant", AssistantPage),
    ("login-items", LoginItemsPage),
    ("storage", StoragePage),
    ("security", SecurityPage),
    ("privacy", PrivacyPage),
    ("updates", UpdatesPage),
    ("add-ons", AddonsPage),
    ("about", AboutPage),
]

# Pages that live in GNOME Settings, listed so everything is findable from here.
GNOME_PANELS = [
    ("Wi-Fi", "network-wireless-symbolic", ("wifi",)),
    ("Network", "network-wired-symbolic", ("network",)),
    ("Bluetooth", "bluetooth-active-symbolic", ("bluetooth",)),
    ("Sound", "audio-speakers-symbolic", ("sound",)),
    ("Displays", "video-display-symbolic", ("display",)),
    ("Power", "battery-full-symbolic", ("power",)),
    ("Mouse and touchpad", "input-mouse-symbolic", ("mouse",)),
    ("Printers", "printer-symbolic", ("printers",)),
    ("Apps", "view-app-grid-symbolic", ("applications",)),
    ("Users", "system-users-symbolic", ("system", "users")),
    ("Date and time", "preferences-system-time-symbolic", ("system", "datetime")),
]


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Kulan Settings")
        self.set_default_size(980, 720)
        self.set_size_request(360, 480)
        self.helper = Helper(self.toast)
        self.pages: dict[str, Gtk.Widget] = {}
        self.current: str | None = None

        # Sidebar
        self.sidebar_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.sidebar_list.connect("row-activated", self.on_row)
        self.rows: dict[str, Gtk.ListBoxRow] = {}
        for page_id, cls in PAGES:
            row = self.sidebar_row(cls.title, cls.icon)
            row.page_id = page_id
            row.keywords = " ".join((cls.title, *cls.keywords)).lower()
            self.rows[page_id] = row
            self.sidebar_list.append(row)
        heading = Gtk.ListBoxRow(activatable=False, selectable=False)
        heading.set_child(Gtk.Label(label="More in GNOME Settings", xalign=0, css_classes=["heading", "dim-label"],
                                    margin_top=12, margin_start=6))
        heading.page_id = None
        heading.keywords = ""
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
        self.search.connect("search-changed", lambda _s: self.sidebar_list.invalidate_filter())
        self.sidebar_list.set_filter_func(self.filter_row)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.append(self.search)
        sidebar_box.append(Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                              child=self.sidebar_list))
        sidebar_view = Adw.ToolbarView()
        sidebar_view.add_top_bar(Adw.HeaderBar())
        sidebar_view.set_content(sidebar_box)
        sidebar = Adw.NavigationPage(title="Kulan Settings", child=sidebar_view)

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
    def sidebar_row(title: str, icon: str, external: bool = False) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
        box.append(Gtk.Image(icon_name=icon))
        box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
        if external:
            box.append(Gtk.Image(icon_name="adw-external-link-symbolic", css_classes=["dim-label"]))
        row = Gtk.ListBoxRow(child=box)
        row.update_property([Gtk.AccessibleProperty.LABEL],
                            [f"{title} (opens GNOME Settings)" if external else title])
        return row

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
        self.content_title.set_title(cls.title)
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


class KulanSettings(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("check", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Check for updates (opens Updates and recovery)", None)
        self.add_main_option("self-test", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open every page, then quit (for automated tests)", None)
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
        window.destroy()
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        print(f"{len(PAGES) - len(failures)}/{len(PAGES)} pages built")
        return 1 if failures else 0


def main(argv: list[str]) -> int:
    return KulanSettings().run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
