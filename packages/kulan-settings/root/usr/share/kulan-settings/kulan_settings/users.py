"""Users: your account (picture, name, password, automatic login) and the
other people who use this computer. Changes go through AccountsService, which
asks for an administrator's password itself when one is needed."""
from __future__ import annotations

import os
import pty
import re
import select
import shutil
import subprocess
import time
from typing import Callable

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .common import Page

ACCOUNTS = "org.freedesktop.Accounts"
ACCOUNTS_PATH = "/org/freedesktop/Accounts"
USER_IFACE = "org.freedesktop.Accounts.User"
STANDARD, ADMINISTRATOR = 0, 1
NAME_RULE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def bus_proxy(path: str, iface: str) -> Gio.DBusProxy | None:
    try:
        return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                                              ACCOUNTS, path, iface, None)
    except GLib.Error:
        return None


def prop(proxy: Gio.DBusProxy, name: str, default=None):
    value = proxy.get_cached_property(name)
    return value.unpack() if value is not None else default


def user_name_from(full_name: str) -> str:
    """"Amina Yusuf" -> "amina"; a suggestion people can change."""
    first = re.sub(r"[^a-z0-9]", "", (full_name.strip().split() or [""])[0].lower())
    if first and not first[0].isalpha():
        first = "u" + first
    return first[:32]


def hash_password(password: str) -> str | None:
    """A SHA-512 crypt hash (what /etc/shadow uses) for AccountsService."""
    if shutil.which("openssl") is None:
        return None
    try:
        out = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password, capture_output=True,
                             text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out.startswith("$6$") else None


def change_own_password(current: str, new: str) -> str | None:
    """Run passwd for the person signed in (it checks the current password),
    answering its questions through a pseudo-terminal. Returns None when it
    worked, else what went wrong in plain words."""
    if shutil.which("passwd") is None:
        return "passwd isn’t installed"
    leader, follower = pty.openpty()
    env = dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="")  # its questions in English, to read them
    try:
        proc = subprocess.Popen(["passwd"], stdin=follower, stdout=follower, stderr=follower,
                                start_new_session=True, env=env, close_fds=True)
    except OSError as exc:
        os.close(leader)
        os.close(follower)
        return f"passwd couldn’t start: {exc.strerror}"
    os.close(follower)
    answers = [current, new, new]
    output = ""
    asked = 0
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline and proc.poll() is None:
            ready, _w, _x = select.select([leader], [], [], 0.3)
            if not ready:
                continue
            try:
                chunk = os.read(leader, 1024).decode(errors="replace")
            except OSError:
                break
            output += chunk
            # Answer each question once: only text after the last answer counts.
            if output[asked:].rstrip().lower().endswith("password:") and answers:
                os.write(leader, (answers.pop(0) + "\n").encode())
                asked = len(output)
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
    finally:
        os.close(leader)
    if code == 0:
        return None
    lower = output.lower()
    if "authentication token manipulation" in lower or "authentication failure" in lower:
        return "The current password isn’t right"
    for line in reversed(output.splitlines()):
        text = line.strip()
        if text.lower().startswith(("bad password", "password unchanged", "passwd:", "the password", "you must")):
            return text.removeprefix("passwd: ").removeprefix("BAD PASSWORD: ")
    return "The password wasn’t changed"


class Accounts:
    """The accounts AccountsService knows: people, not system accounts."""

    def __init__(self) -> None:
        self.manager = bus_proxy(ACCOUNTS_PATH, ACCOUNTS)

    @property
    def available(self) -> bool:
        return self.manager is not None and self.manager.get_name_owner() is not None

    def users(self) -> list[Gio.DBusProxy]:
        if not self.available:
            return []
        try:
            paths = self.manager.call_sync("ListCachedUsers", None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error:
            return []
        users = [p for p in (bus_proxy(path, USER_IFACE) for path in paths) if p is not None]
        return [u for u in users if not prop(u, "SystemAccount", False)]

    def me(self) -> Gio.DBusProxy | None:
        if not self.available:
            return None
        try:
            path = self.manager.call_sync("FindUserById", GLib.Variant("(x)", (os.getuid(),)),
                                          Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error:
            return None
        return bus_proxy(path, USER_IFACE)


def call(proxy: Gio.DBusProxy, method: str, args: GLib.Variant | None,
         done: Callable[[str | None, GLib.Variant | None], None]) -> None:
    """Call AccountsService, letting it ask for a password; done(error, reply)."""
    def finish(p, result):
        try:
            done(None, p.call_finish(result))
        except GLib.Error as exc:
            message = exc.message
            if "dismissed" in message.lower() or "not authorized" in message.lower():
                message = "Not changed: an administrator’s password is needed"
            done(message, None)
    proxy.call(method, args, Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None, finish)


def add_account_dialog(parent: Gtk.Widget, accounts: Accounts, standard_only: bool,
                       toast: Callable[[str], None], on_added: Callable[[], None]) -> None:
    """Ask for a new account's details, then create it."""
    dialog = Adw.AlertDialog(heading="Add a child’s account" if standard_only else "Add an account",
                             body="They can sign in with this name and password.")
    box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
    full = Adw.EntryRow(title="Full name")
    user = Adw.EntryRow(title="User name (for signing in)")
    password = Adw.PasswordEntryRow(title="Password")
    again = Adw.PasswordEntryRow(title="Password again")
    admin = Adw.SwitchRow(title="Administrator", subtitle="Can install apps and change the whole computer")
    edited = {"user": False}

    def full_changed(_e) -> None:
        if not edited["user"]:
            user.handler_block(user_handler)
            user.set_text(user_name_from(full.get_text()))
            user.handler_unblock(user_handler)
        check()

    def user_changed(_e) -> None:
        edited["user"] = True
        check()

    def check() -> None:
        name = user.get_text()
        ok = (full.get_text().strip() != "" and NAME_RULE.match(name) is not None
              and len(password.get_text()) >= 1 and password.get_text() == again.get_text())
        dialog.set_response_enabled("add", ok)

    full.connect("changed", full_changed)
    user_handler = user.connect("changed", user_changed)
    password.connect("changed", lambda _e: check())
    again.connect("changed", lambda _e: check())
    for row in (full, user, password, again):
        box.append(row)
    if not standard_only:
        box.append(admin)
    dialog.set_extra_child(box)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("add", "Add")
    dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_response_enabled("add", False)

    def answered(_d, response: str) -> None:
        if response != "add":
            return
        crypted = hash_password(password.get_text())
        if crypted is None:
            toast("Couldn’t prepare the password (openssl is missing)")
            return
        kind = ADMINISTRATOR if admin.get_active() and not standard_only else STANDARD

        def created(error: str | None, reply: GLib.Variant | None) -> None:
            if error:
                toast(f"The account wasn’t added: {error}")
                return
            new_user = bus_proxy(reply.unpack()[0], USER_IFACE)
            if new_user is None:
                toast("The account was added, but its password couldn’t be set")
                return

            def password_set(err: str | None, _r) -> None:
                toast(f"The password wasn’t set: {err}" if err else f"{full.get_text().strip()} can sign in now")
                on_added()
            call(new_user, "SetPassword", GLib.Variant("(ss)", (crypted, "")), password_set)

        call(accounts.manager, "CreateUser",
             GLib.Variant("(ssi)", (user.get_text(), full.get_text().strip(), kind)), created)

    dialog.connect("response", answered)
    dialog.present(parent)


class UsersPage(Page):
    title = "Users"
    icon = "avatar-default-symbolic|system-users-symbolic"
    keywords = ("users", "accounts", "account", "password", "picture", "avatar", "photo", "name",
                "administrator", "admin", "automatic login", "add user", "remove user", "people")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.accounts = Accounts()
        if not self.accounts.available:
            group = self.add_group()
            group.add(Adw.ActionRow(title="Accounts can’t be changed right now",
                                    subtitle="The accounts service (AccountsService) isn’t running"))
            return
        self.me = self.accounts.me()
        self.mine = self.add_group("Your account")
        self.others = self.add_group("Other people", "Each person gets their own files, settings and password.")
        self.rows: list[Gtk.Widget] = []
        self.fill()

    def fill(self) -> None:
        for group, row in self.rows:
            group.remove(row)
        self.rows = []
        me = self.me = self.accounts.me()
        if me is not None:
            self.fill_mine(me)
        my_uid = os.getuid()
        others = [u for u in self.accounts.users() if prop(u, "Uid", 0) != my_uid]
        for user in sorted(others, key=lambda u: (prop(u, "RealName", "") or prop(u, "UserName", "")).lower()):
            self.add(self.others, self.other_row(user))
        if not others:
            self.add(self.others, Adw.ActionRow(title="Only you use this computer"))
        add = Adw.ButtonRow(title="Add an account…", start_icon_name="list-add-symbolic") \
            if hasattr(Adw, "ButtonRow") else Adw.ActionRow(title="Add an account…", activatable=True)
        add.connect("activated", lambda _r: add_account_dialog(self.window, self.accounts, False, self.toast,
                                                               self.refresh))
        self.add(self.others, add)

    def add(self, group: Adw.PreferencesGroup, row: Gtk.Widget) -> None:
        group.add(row)
        self.rows.append((group, row))

    def refresh(self) -> None:
        if not self.torn_down:
            GLib.timeout_add(400, lambda: (self.fill(), False)[1])

    def avatar(self, user: Gio.DBusProxy, size: int) -> Adw.Avatar:
        name = prop(user, "RealName", "") or prop(user, "UserName", "")
        avatar = Adw.Avatar(size=size, text=name, show_initials=True)
        icon = prop(user, "IconFile", "")
        if icon and os.path.isfile(icon):
            try:
                avatar.set_custom_image(Gdk.Texture.new_from_filename(icon))
            except GLib.Error:
                pass
        return avatar

    def fill_mine(self, me: Gio.DBusProxy) -> None:
        head = Adw.ActionRow(title=GLib.markup_escape_text(prop(me, "RealName", "") or prop(me, "UserName", "")),
                             subtitle=("Administrator" if prop(me, "AccountType", 0) == ADMINISTRATOR
                                       else "Standard account") + f" · {prop(me, 'UserName', '')}")
        head.add_prefix(self.avatar(me, 56))
        picture = Gtk.Button(label="Change picture…", valign=Gtk.Align.CENTER)
        picture.connect("clicked", lambda _b: self.choose_picture(me))
        head.add_suffix(picture)
        self.add(self.mine, head)

        name = Adw.EntryRow(title="Full name", text=prop(me, "RealName", ""), show_apply_button=True)
        name.connect("apply", lambda r: call(me, "SetRealName", GLib.Variant("(s)", (r.get_text().strip(),)),
                                             lambda e, _r: (self.toast(e or "Name changed"), self.refresh())))
        self.add(self.mine, name)

        password = Adw.ActionRow(title="Password", subtitle="Change the password you sign in with", activatable=True)
        password.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        password.connect("activated", lambda _r: self.password_dialog())
        self.add(self.mine, password)

        auto = Adw.SwitchRow(title="Sign in automatically",
                             subtitle="No password at start-up (the screen lock still asks for it)",
                             active=bool(prop(me, "AutomaticLogin", False)))
        auto.connect("notify::active", lambda r, _p: self.set_flag(me, "SetAutomaticLogin", r.get_active()))
        self.add(self.mine, auto)

    def other_row(self, user: Gio.DBusProxy) -> Adw.ExpanderRow:
        real = prop(user, "RealName", "") or prop(user, "UserName", "")
        admin = prop(user, "AccountType", 0) == ADMINISTRATOR
        row = Adw.ExpanderRow(title=GLib.markup_escape_text(real),
                              subtitle=("Administrator" if admin else "Standard account") + f" · {prop(user, 'UserName', '')}")
        row.add_prefix(self.avatar(user, 36))
        kind = Adw.SwitchRow(title="Administrator", subtitle="Can install apps and change the whole computer",
                             active=admin)
        kind.connect("notify::active", lambda r, _p: call(
            user, "SetAccountType", GLib.Variant("(i)", (ADMINISTRATOR if r.get_active() else STANDARD,)),
            lambda e, _r: (self.toast(e or "Account type changed"), self.refresh())))
        row.add_row(kind)
        auto = Adw.SwitchRow(title="Sign in automatically", active=bool(prop(user, "AutomaticLogin", False)))
        auto.connect("notify::active", lambda r, _p: self.set_flag(user, "SetAutomaticLogin", r.get_active()))
        row.add_row(auto)
        reset = Adw.ActionRow(title="New password at next sign-in",
                              subtitle="They choose a new password the next time they sign in", activatable=True)
        reset.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        reset.connect("activated", lambda _r: call(user, "SetPasswordMode", GLib.Variant("(i)", (1,)),
                                                   lambda e, _r: self.toast(e or f"{real} sets a new password next time")))
        row.add_row(reset)
        remove = Adw.ActionRow(title="Remove this account…", activatable=True, css_classes=["error"])
        remove.connect("activated", lambda _r: self.remove_dialog(user, real))
        row.add_row(remove)
        return row

    def set_flag(self, user: Gio.DBusProxy, method: str, value: bool) -> None:
        call(user, method, GLib.Variant("(b)", (value,)), lambda e, _r: (self.toast(e) if e else None, self.refresh()))

    def choose_picture(self, me: Gio.DBusProxy) -> None:
        dialog = Gtk.FileDialog(title="Choose a picture")
        images = Gtk.FileFilter(name="Pictures")
        images.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)

        def chosen(d, result) -> None:
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            if file is None or file.get_path() is None:
                return
            if os.path.getsize(file.get_path()) > 10 * 1024 * 1024:
                self.toast("That picture is too large (over 10 MB)")
                return
            call(me, "SetIconFile", GLib.Variant("(s)", (file.get_path(),)),
                 lambda e, _r: (self.toast(e or "Picture changed"), self.refresh()))
        dialog.open(self.window, None, chosen)

    def password_dialog(self) -> None:
        dialog = Adw.AlertDialog(heading="Change your password",
                                 body="Use at least 8 characters. A short sentence is easy to remember.")
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        current = Adw.PasswordEntryRow(title="Current password")
        new = Adw.PasswordEntryRow(title="New password")
        again = Adw.PasswordEntryRow(title="New password again")
        for row in (current, new, again):
            box.append(row)

        def check(_e=None) -> None:
            dialog.set_response_enabled("change", bool(current.get_text()) and len(new.get_text()) >= 8
                                        and new.get_text() == again.get_text())
        for row in (current, new, again):
            row.connect("changed", check)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("change", "Change")
        dialog.set_response_appearance("change", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("change", False)

        def answered(_d, response: str) -> None:
            if response != "change":
                return
            old, fresh = current.get_text(), new.get_text()

            def work() -> None:
                problem = change_own_password(old, fresh)
                GLib.idle_add(lambda: (self.toast(problem or "Password changed"), False)[1])
            GLib.Thread.new("kulan-passwd", work)
        dialog.connect("response", answered)
        dialog.present(self.window)

    def remove_dialog(self, user: Gio.DBusProxy, real: str) -> None:
        dialog = Adw.AlertDialog(heading=f"Remove {real}’s account?",
                                 body="They won’t be able to sign in any more.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("keep", "Remove, keep their files")
        dialog.add_response("delete", "Remove with their files")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_d, response: str) -> None:
            if response not in ("keep", "delete"):
                return
            call(self.accounts.manager, "DeleteUser",
                 GLib.Variant("(xb)", (prop(user, "Uid", -1), response == "delete")),
                 lambda e, _r: (self.toast(e or f"{real}’s account was removed"), self.refresh()))
        dialog.connect("response", answered)
        dialog.present(self.window)

