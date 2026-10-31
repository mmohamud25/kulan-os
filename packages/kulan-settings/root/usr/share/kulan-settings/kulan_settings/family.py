"""Parental controls: children's accounts, what they can use, family-safe web."""
from __future__ import annotations

import grp
import pwd
import shutil

from gi.repository import Adw, GLib, Gtk

from .common import Page, launch
from .privacy import dns_mode
from .users import Accounts, add_account_dialog

ADMIN_GROUPS = ("sudo", "wheel", "admin")
PARENTAL_APP = "org.freedesktop.MalcontentControl.desktop"


def standard_accounts() -> list[tuple[str, str]]:
    """(user name, full name) of people's accounts that can't change the computer."""
    admins: set[str] = set()
    for name in ADMIN_GROUPS:
        try:
            admins.update(grp.getgrnam(name).gr_mem)
        except KeyError:
            continue
    people = []
    for entry in pwd.getpwall():
        if 1000 <= entry.pw_uid < 60000 and entry.pw_name not in admins and not entry.pw_shell.endswith(
                ("nologin", "false")):
            people.append((entry.pw_name, entry.pw_gecos.split(",")[0] or entry.pw_name))
    return sorted(people, key=lambda p: p[1].lower())


class FamilyPage(Page):
    title = "Parental controls"
    icon = "preferences-system-parental-controls-symbolic|system-users-symbolic"
    keywords = ("parental", "parents", "child", "children", "kids", "family", "screen time", "filter",
                "restrictions", "safe")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.accounts = self.add_group(
            "Children’s accounts",
            "Give each child a Standard account, then choose which apps they can use and what they can "
            "install.")
        self.account_rows: list[Gtk.Widget] = []

        web = self.add_group(
            "Family-safe web",
            "Blocks adult and harmful websites in every app, using Cloudflare for Families. Children need an "
            "administrator password to turn it off. Tor Browser and VPN apps get around it, so don’t "
            "allow them.")
        self.web_row = Adw.SwitchRow(title="Family-safe web filter",
                                     subtitle="Adult content, malware and phishing sites are blocked")
        self.web_row.set_active(dns_mode() == "family")
        self.web_handler = self.web_row.connect("notify::active", self.on_web)
        web.add(self.web_row)
        self.listen_busy(self.on_busy)
        self.shown()

    def shown(self) -> None:
        for row in self.account_rows:
            self.accounts.remove(row)
        self.account_rows = []
        have_app = shutil.which("malcontent-control") is not None
        for user, full_name in standard_accounts():
            row = Adw.ActionRow(title=GLib.markup_escape_text(full_name), subtitle=GLib.markup_escape_text(user))
            row.add_prefix(Gtk.Image(icon_name="avatar-default-symbolic"))
            if have_app:
                button = Gtk.Button(label="Choose apps", valign=Gtk.Align.CENTER)
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"Choose apps for {full_name}"])
                button.connect("clicked", lambda _b: launch(PARENTAL_APP, self.toast))
                row.add_suffix(button)
            self.accounts.add(row)
            self.account_rows.append(row)
        if not self.account_rows:
            row = Adw.ActionRow(title="No children’s accounts yet",
                                subtitle="Accounts without administrator rights appear here")
            self.accounts.add(row)
            self.account_rows.append(row)
        add = Adw.ButtonRow(title="Add a child’s account…", start_icon_name="list-add-symbolic") \
            if hasattr(Adw, "ButtonRow") else Adw.ActionRow(title="Add a child’s account…", activatable=True)
        add.connect("activated", lambda _r: add_account_dialog(self.window, Accounts(), True, self.toast,
                                                               lambda: GLib.timeout_add(400, lambda: (self.shown(), False)[1])))
        self.accounts.add(add)
        self.account_rows.append(add)
        # The switch follows the computer's setting (Privacy may have changed it).
        mode = dns_mode() == "family"
        if self.web_row.get_active() != mode:
            self.web_row.handler_block(self.web_handler)
            self.web_row.set_active(mode)
            self.web_row.handler_unblock(self.web_handler)

    def on_busy(self, busy: bool) -> None:
        self.web_row.set_sensitive(not busy)

    def on_web(self, row: Adw.SwitchRow, _pspec) -> None:
        on = row.get_active()

        def done(ok: bool) -> None:
            if not ok:
                self.shown()

        self.window.helper.run(["dns", "family" if on else "off"],
                               "Family-safe web filter on" if on else "Family-safe web filter off", on_done=done)
