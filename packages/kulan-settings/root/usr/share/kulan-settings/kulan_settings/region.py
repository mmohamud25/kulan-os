"""Language and region: the language you use, how dates and numbers look,
and the language of the login screen."""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, settings
from .users import Accounts, call, prop

LOCALE_DATA = Path("/usr/share/i18n/locales")
SUPPORTED = Path("/usr/share/i18n/SUPPORTED")
TITLE = re.compile(r'^title\s+"(.*)"', re.M)
# What the root helper accepts (update-run locale-add); offer nothing else.
HELPER_NAME = re.compile(r"^[a-z]{2,3}_[A-Z]{2}\.UTF-8(@[a-z]+)?$")
WORDY = re.compile(r"^(.*?)\s+(?:language\s+)?locale\s+for\s+(?:the\s+)?(.*?)\.?$", re.I)


def normalise(name: str) -> str:
    """en_US.utf8 -> en_US.UTF-8 (the spelling settings use)."""
    base, _dot, rest = name.partition(".")
    charset, at, modifier = rest.partition("@")
    if charset.lower().replace("-", "") == "utf8":
        charset = "UTF-8"
    return base + (f".{charset}" if charset else "") + (f"@{modifier}" if at else "")


@lru_cache(maxsize=None)
def locale_label(name: str) -> str:
    """"en_US.UTF-8" -> "English (USA)", from glibc's own description."""
    base = name.split(".")[0]
    modifier = name.partition("@")[2]
    try:
        text = (LOCALE_DATA / (base + (f"@{modifier}" if modifier else ""))).read_text(errors="replace")
        match = TITLE.search(text)
    except OSError:
        match = None
    if not match:
        return name
    title = match.group(1).strip()
    wordy = WORDY.match(title)
    return f"{wordy.group(1)} ({wordy.group(2)})" if wordy else title


def installed_locales() -> list[str]:
    """UTF-8 languages this computer can use now (generated with locale-gen)."""
    try:
        out = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ["en_US.UTF-8"]
    names = {normalise(n) for n in out.split() if ".utf8" in n.lower() or ".utf-8" in n.lower()}
    names.discard("C.UTF-8")
    return sorted(names, key=lambda n: locale_label(n).lower()) or ["en_US.UTF-8"]


def supported_locales() -> list[str]:
    """Every UTF-8 language Debian can add (/usr/share/i18n/SUPPORTED)."""
    try:
        lines = SUPPORTED.read_text().splitlines()
    except OSError:
        return []
    # Only names that say UTF-8 ("so_SO.UTF-8"): the helper adds exactly
    # these, and they show up again in `locale -a` under the same name.
    names = [line.split()[0] for line in lines
             if len(line.split()) == 2 and line.split()[1] == "UTF-8" and HELPER_NAME.match(line.split()[0])]
    return sorted(set(names), key=lambda n: locale_label(n).lower())


class RegionPage(Page):
    title = "Language and region"
    icon = "org.gnome.Settings-region-symbolic|preferences-desktop-locale-symbolic"
    keywords = ("language", "region", "locale", "formats", "dates", "numbers", "currency", "translation",
                "somali", "arabic", "english", "login screen")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.locale = settings("org.gnome.system.locale")
        self.accounts = Accounts()
        self.me = self.accounts.me()
        self.localed = None
        try:
            self.localed = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                                                          "org.freedesktop.locale1", "/org/freedesktop/locale1",
                                                          "org.freedesktop.locale1", None)
        except GLib.Error:
            self.localed = None
        self.locales = installed_locales()

        mine = self.add_group("Your language", "Apps use it from the next time you sign in.")
        current = normalise((prop(self.me, "Language", "") if self.me else "") or os.environ.get("LANG", "en_US.UTF-8"))
        if current not in self.locales:
            self.locales.insert(0, current)
        self.language_row = self.locale_combo("Language", self.locales, current)
        self.language_row.connect("notify::selected", self.on_language)
        mine.add(self.language_row)
        self.sign_out_row = Adw.ActionRow(title="Sign out to finish changing the language", visible=False)
        sign_out = Gtk.Button(label="Sign out", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        sign_out.connect("clicked", lambda _b: self.sign_out())
        self.sign_out_row.add_suffix(sign_out)
        mine.add(self.sign_out_row)
        add = Adw.ActionRow(title="Add a language…", subtitle="Only languages added to this computer are listed",
                            activatable=True)
        add.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
        add.connect("activated", lambda _r: self.add_language_dialog())
        mine.add(add)

        if self.locale is not None:
            formats = self.add_group("Formats", "Dates, times, numbers and money.")
            region = self.locale.get_string("region")
            choices = ["", *self.locales]
            if region and normalise(region) not in choices:
                choices.append(normalise(region))
            row = self.locale_combo("Formats", choices, normalise(region) if region else "",
                                    same_label="Same as language")
            row.connect("notify::selected", lambda r, _p: self.locale.set_string("region", choices[r.get_selected()]))
            formats.add(row)
            self.example = Adw.ActionRow(title="Example", subtitle=self.format_example(region or current))
            formats.add(self.example)
            self.watch(self.locale, "changed::region",
                       lambda s, _k: self.example.set_subtitle(self.format_example(s.get_string("region") or current)))

        if self.localed is not None:
            system = self.add_group("Login screen", "The language of the login screen and of new accounts.")
            lang = self.system_language()
            row = Adw.ActionRow(title="Login screen language", subtitle=locale_label(lang) if lang else "Not set")
            use = Gtk.Button(label="Use my language", valign=Gtk.Align.CENTER)
            use.connect("clicked", lambda _b: self.set_system_language(row))
            row.add_suffix(use)
            system.add(row)

    def locale_combo(self, title: str, names: list[str], current: str, same_label: str = "") -> Adw.ComboRow:
        labels = [same_label if not n else locale_label(n) for n in names]
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels), enable_search=True,
                           expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        row.set_selected(names.index(current) if current in names else 0)
        return row

    @staticmethod
    def format_example(name: str) -> str:
        """Today's date and a number, as that region writes them."""
        env = dict(os.environ, LC_ALL=normalise(name))
        try:
            out = subprocess.run(["date", "+%x · %X"], capture_output=True, text=True, timeout=5, env=env,
                                 check=False).stdout.strip()
            number = subprocess.run(["printf", "%'.2f", "1234567.89"], capture_output=True, text=True, timeout=5,
                                    env=env, check=False).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return f"{out} · {number}" if out else number

    def on_language(self, row: Adw.ComboRow, _pspec) -> None:
        name = self.locales[row.get_selected()]
        if self.me is None:
            self.toast("Your language can’t be changed right now")
            return

        def done(error: str | None, _r) -> None:
            if self.torn_down:
                return
            if error:
                self.toast(error)
                return
            self.sign_out_row.set_visible(True)
        call(self.me, "SetLanguage", GLib.Variant("(s)", (name,)), done)

    def system_language(self) -> str:
        values = self.localed.get_cached_property("Locale")
        for item in values.unpack() if values is not None else []:
            if item.startswith("LANG="):
                return normalise(item[5:])
        return ""

    def set_system_language(self, row: Adw.ActionRow) -> None:
        name = self.locales[self.language_row.get_selected()]

        def finish(proxy, result) -> None:
            try:
                proxy.call_finish(result)
            except GLib.Error as exc:
                if not self.torn_down:
                    self.toast("Not changed" if "dismissed" in exc.message.lower() or "not authorized"
                               in exc.message.lower() else exc.message)
                return
            if not self.torn_down:
                row.set_subtitle(locale_label(name))
                self.toast(f"The login screen uses {locale_label(name)}")
        self.localed.call("SetLocale", GLib.Variant("(asb)", ([f"LANG={name}"], True)),
                          Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None, finish)

    def add_language_dialog(self) -> None:
        names = [n for n in supported_locales() if n not in self.locales]
        if not names:
            self.toast("Every language is already added")
            return
        dialog = Adw.AlertDialog(heading="Add a language", body="Adding a language takes a moment.")
        row = Adw.ComboRow(title="Language", model=Gtk.StringList.new([locale_label(n) for n in names]),
                           enable_search=True, expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(row)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response: str) -> None:
            if response != "add":
                return
            name = names[row.get_selected()]
            self.window.helper.run(["locale-add", name], f"{locale_label(name)} added",
                                   on_done=lambda ok: ok and not self.torn_down and self.window.reload_page(self))
        dialog.connect("response", answered)
        dialog.present(self.window)

    def sign_out(self) -> None:
        try:
            Gio.DBus.session.call("org.gnome.SessionManager", "/org/gnome/SessionManager",
                                  "org.gnome.SessionManager", "Logout", GLib.Variant("(u)", (0,)), None,
                                  Gio.DBusCallFlags.NONE, -1, None, None)
        except GLib.Error as exc:
            self.toast(f"Couldn’t sign out: {exc.message}")
