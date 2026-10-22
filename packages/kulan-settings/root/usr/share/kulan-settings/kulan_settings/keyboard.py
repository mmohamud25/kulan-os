"""Keyboard: typing repeat, Caps Lock, Mac-style shortcuts, clipboard history."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, Gtk

from .common import (
    KULAN_SCHEMA,
    Page,
    choice_row,
    has_key,
    reset_keys,
    settings,
    switch_row,
)
from .shortcuts import LayoutsGroup, ShortcutsDialog, first_usable

CAPS_OPTIONS = {"caps:none": "Does nothing", "caps:escape": "Escape", "ctrl:nocaps": "Ctrl",
                "caps:backspace": "Backspace"}

# (title, schema, key) of the shortcuts worth knowing; the label shows the
# key's current first binding, so changes made in GNOME Settings show here.
SHORTCUTS = [
    ("Search apps, files and settings", KULAN_SCHEMA, "search-shortcut"),
    ("Screenshot of the whole screen", "org.gnome.shell.keybindings", "screenshot"),
    ("Screenshot of an area or a window", "org.gnome.shell.keybindings", "show-screenshot-ui"),
    ("Record the screen", "org.gnome.shell.keybindings", "show-screen-recording-ui"),
    ("Live Text: copy text from the screen", KULAN_SCHEMA, "live-text-shortcut"),
    ("Clipboard history", KULAN_SCHEMA, "clipboard-history-shortcut"),
    ("Quick Note", KULAN_SCHEMA, "quick-note-shortcut"),
    ("Writing tools for the selected text (Kulan Assistant)", KULAN_SCHEMA, "writing-tools-shortcut"),
    ("Dictation: start and stop (Kulan Assistant)", KULAN_SCHEMA, "dictation-shortcut"),
    ("Switch typing language", "org.gnome.desktop.wm.keybindings", "switch-input-source"),
]
KULAN_SHORTCUT_KEYS = ["search-shortcut", "search-style", "quick-note-shortcut", "live-text-shortcut", "clipboard-history-shortcut", "writing-tools-shortcut",
                       "dictation-shortcut", "clipboard-history"]
ASSISTANT = Path("/usr/bin/kulan-assistant")


def first_binding(gsettings, key: str) -> str:
    """The first real key combination (skips keys like XF86Keyboard, and
    prefers the Mac-style Super one)."""
    return first_usable(gsettings.get_strv(key) if has_key(gsettings, key) else [])


class KeyboardPage(Page):
    title = "Keyboard"
    icon = "input-keyboard-symbolic"
    keywords = ("keyboard", "layout", "layouts", "typing language", "input source", "caps lock", "repeat",
                "shortcuts", "typing", "custom shortcut")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.keyboard = settings("org.gnome.desktop.peripherals.keyboard")
        self.sources = settings("org.gnome.desktop.input-sources")

        typing = self.add_group("Typing")
        repeat = switch_row(self.keyboard, "repeat", "Repeat keys", "Hold a key to type it again and again")
        self.add_rows(typing, repeat)
        if self.keyboard is not None:
            delay = Adw.ActionRow(title="Repeat delay", subtitle="How long to hold before repeating")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 150, 1000, 50)
            scale.set_size_request(220, -1)
            scale.set_value(self.keyboard.get_uint("delay"))
            scale.update_property([Gtk.AccessibleProperty.LABEL], ["Repeat delay"])
            scale.connect("value-changed", lambda s: self.keyboard.set_uint("delay", int(s.get_value())))
            delay.add_suffix(scale)
            self.keyboard.bind("repeat", delay, "sensitive", Gio.SettingsBindFlags.GET)
            typing.add(delay)

        if self.sources is not None:
            typing.add(choice_row("Caps Lock key", "What the Caps Lock key does",
                                  [("", "Caps Lock"), *CAPS_OPTIONS.items()],
                                  self.get_caps, self.set_caps))

        if self.sources is not None and has_key(self.sources, "sources"):
            self.layouts = LayoutsGroup(self, self.sources)

        self.kulan = settings(KULAN_SCHEMA)
        shortcuts = self.add_group("Shortcuts", "In UTM on a Mac, the Command key is the Super key.")
        for title, schema, key in SHORTCUTS:
            gsettings = self.kulan if schema == KULAN_SCHEMA else settings(schema)
            accel = first_binding(gsettings, key)
            if not accel or (key in ("writing-tools-shortcut", "dictation-shortcut") and not ASSISTANT.exists()):
                continue
            row = Adw.ActionRow(title=title)
            row.add_suffix(Gtk.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER))
            shortcuts.add(row)
        emoji = Adw.ActionRow(title="Emoji and symbols")
        emoji.add_suffix(Gtk.ShortcutLabel(accelerator="<Control><Super>space", valign=Gtk.Align.CENTER))
        quick_look = Adw.ActionRow(title="Quick Look", subtitle="Select a file in Files and press Space")
        quick_look.add_suffix(Gtk.ShortcutLabel(accelerator="space", valign=Gtk.Align.CENTER))
        self.add_rows(shortcuts, emoji, quick_look)
        if has_key(self.kulan, "search-style"):
            shortcuts.add(choice_row("The search shortcut opens", None,
                                     [("spotlight", "A search bar over your windows"),
                                      ("activities", "Activities, with all windows")],
                                     lambda: self.kulan.get_string("search-style"),
                                     lambda v: self.kulan.set_string("search-style", v)))

        clipboard = self.add_group("Clipboard")
        self.add_rows(clipboard,
                      switch_row(self.kulan, "clipboard-history", "Clipboard history",
                                 "Remembers the last 25 things you copied, in memory only, until you lock the "
                                 "screen. Passwords from KeePassXC are skipped, but passwords copied from a "
                                 "browser are remembered too"))

        more = self.add_group()
        row = Adw.ActionRow(title="Keyboard shortcuts", subtitle="View and change every shortcut, or add your own",
                            activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r: ShortcutsDialog(self.window, self.toast, ASSISTANT.exists())
                    .present(self.window))
        more.add(row)
        self.add_reset(self.reset)

    def get_caps(self) -> str:
        options = self.sources.get_strv("xkb-options")
        return next((o for o in options if o in CAPS_OPTIONS), "")

    def set_caps(self, value: str) -> None:
        options = [o for o in self.sources.get_strv("xkb-options") if o not in CAPS_OPTIONS]
        if value:
            options.append(value)
        self.sources.set_strv("xkb-options", options)

    def reset(self) -> None:
        reset_keys(self.keyboard, ["repeat", "delay", "repeat-interval"])
        reset_keys(self.kulan, KULAN_SHORTCUT_KEYS)
        self.set_caps("")
        self.window.reload_page(self)
        self.toast("Keyboard reset to Kulan defaults")
