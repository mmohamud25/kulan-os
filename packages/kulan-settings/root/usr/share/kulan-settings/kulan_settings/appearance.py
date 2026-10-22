"""Appearance: light/dark/automatic, accent colour, wallpaper, window buttons."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .common import KULAN_SCHEMA, Page, choice_row, has_key, open_gnome_panel, reset_keys, settings

KULAN_WALLPAPER_LIGHT = "file:///usr/share/backgrounds/kulan/kulan-light.png"
KULAN_WALLPAPER_DARK = "file:///usr/share/backgrounds/kulan/kulan-dark.png"
# Kulan's own wallpapers: (title, subtitle, light picture, dark picture). The
# first is the default.
KULAN_WALLPAPERS = [
    ("Kulan teal waves", "Light by day, starry night in dark mode",
     KULAN_WALLPAPER_LIGHT, KULAN_WALLPAPER_DARK),
    ("Kulan classic waves", "The original Kulan OS 1.0 waves, light and dark",
     "file:///usr/share/backgrounds/kulan/kulan-waves-light.png",
     "file:///usr/share/backgrounds/kulan/kulan-waves-dark.png"),
]

# (GNOME accent-color value, label, colour used for the swatch)
ACCENTS = [
    ("teal", "Teal", "#2190a4"),
    ("blue", "Blue", "#3584e4"),
    ("green", "Green", "#3a944a"),
    ("yellow", "Yellow", "#c88800"),
    ("orange", "Orange", "#ed5b00"),
    ("red", "Red", "#e62d42"),
    ("pink", "Pink", "#d56199"),
    ("purple", "Purple", "#9141ac"),
    ("slate", "Slate", "#6f8396"),
]

# Window-button styles: the stylesheet each user's gtk.css imports, per GTK
# version. "symbols" is Kulan's default; "gnome" imports nothing.
GTK_DIR = "/usr/share/kulan/gtk"
BUTTON_STYLES = {
    "symbols": {"gtk-4.0": f"{GTK_DIR}/gtk-4.0.css", "gtk-3.0": f"{GTK_DIR}/gtk-3.0.css"},
    "dots": {"gtk-4.0": f"{GTK_DIR}/dots/gtk-4.0.css", "gtk-3.0": f"{GTK_DIR}/dots/gtk-3.0.css"},
}
GTK_COMMENT = "/* Kulan OS window buttons. Delete this line to use GTK's own. */"

LAYOUT_LEFT = "close,minimize,maximize:appmenu"
LAYOUT_RIGHT = "appmenu:minimize,maximize,close"


# Each gtk.css imports a copy kept next to it, so apps from the Kulan Store
# (Flatpak), which can read ~/.config/gtk-*.0 but not /usr/share, get the
# buttons too. Kulan Shell refreshes the copy at every sign-in.
GTK_COPY = "kulan-window-buttons.css"
GTK_IMPORT = f'@import url("{GTK_COPY}");'


def _import_line(css: str) -> str:
    """The import older versions wrote (straight from /usr/share)."""
    return f'@import url("file://{css}");'


def _kulan_lines() -> set[str]:
    return ({_import_line(css) for files in BUTTON_STYLES.values() for css in files.values()}
            | {GTK_COMMENT, GTK_IMPORT})


def button_style() -> str:
    """The style the user's GTK 4 gtk.css uses: symbols, dots or gnome."""
    folder = Path(GLib.get_user_config_dir()) / "gtk-4.0"
    try:
        text = (folder / "gtk.css").read_text()
    except OSError:
        return "gnome"
    for style, files in BUTTON_STYLES.items():
        if _import_line(files["gtk-4.0"]) in text:
            return style
    if GTK_IMPORT in text:
        try:
            first = (folder / GTK_COPY).read_text().split("\n", 1)[0]
        except OSError:
            return "symbols"
        name = first.removeprefix("/* kulan-style: ").removesuffix(" */").strip()
        return name if name in BUTTON_STYLES else "symbols"
    return "gnome"


def set_button_style(style: str) -> None:
    """Point the user's gtk.css files at the chosen style, keeping anything
    else written there. Apps pick it up when they are opened again."""
    for folder_name in ("gtk-4.0", "gtk-3.0"):
        folder = Path(GLib.get_user_config_dir()) / folder_name
        path = folder / "gtk.css"
        try:
            current = path.read_text()
        except OSError:
            current = ""
        lines = [x for x in current.splitlines() if x.strip() not in _kulan_lines()]
        css = BUTTON_STYLES.get(style, {}).get(folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        if css:
            try:
                source = Path(css).read_text()
            except OSError:
                source = None
            if source is not None:
                (folder / GTK_COPY).write_text(f"/* kulan-style: {style} */\n{source}")
                lines = [GTK_COMMENT, GTK_IMPORT, *lines]
        text = "\n".join(lines).strip("\n")
        if text:
            path.write_text(text + "\n")
        elif path.exists():
            path.unlink()


_swatch_style_added = False


def add_swatch_style() -> None:
    """Colours for the accent swatches, added to the display once."""
    global _swatch_style_added
    if _swatch_style_added:
        return
    provider = Gtk.CssProvider()
    css = "".join(f".kulan-swatch-{v} {{ background: {c}; border-radius: 999px; min-width: 22px; "
                  f"min-height: 22px; padding: 0; }}" for v, _l, c in ACCENTS)
    css += (".kulan-swatch:checked { box-shadow: 0 0 0 2px @window_bg_color, 0 0 0 4px currentColor; }"
            ".kulan-swatch image { color: white; }")
    provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _swatch_style_added = True


class AppearancePage(Page):
    title = "Appearance"
    icon = "applications-graphics-symbolic"
    keywords = ("dark", "light", "theme", "accent", "colour", "color", "wallpaper", "background", "buttons")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.interface = settings("org.gnome.desktop.interface")
        self.background = settings("org.gnome.desktop.background")
        self.wm = settings("org.gnome.desktop.wm.preferences")
        self.kulan = settings(KULAN_SCHEMA)

        style = self.add_group("Style")
        style.add(choice_row("Appearance", "Automatic follows the sunset: dark at night, light by day",
                             [("light", "Light"), ("dark", "Dark"), ("auto", "Automatic")],
                             self.get_style, self.set_style))
        if has_key(self.interface, "accent-color"):
            style.add(self.accent_row())

        wallpaper = self.add_group("Wallpaper")
        # One row per Kulan wallpaper: a check on the one in use, Use on the others.
        self.kulan_wallpaper_rows = []
        for index, (title, subtitle, _light, _dark) in enumerate(KULAN_WALLPAPERS):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            check = Gtk.Image(icon_name="object-select-symbolic", css_classes=["accent"])
            use = Gtk.Button(label="Use", valign=Gtk.Align.CENTER)
            use.update_property([Gtk.AccessibleProperty.LABEL], [f"Use {title}"])
            use.connect("clicked", self.on_use_wallpaper, index)
            row.add_suffix(check)
            row.add_suffix(use)
            self.kulan_wallpaper_rows.append((row, check, use))
        own = Adw.ActionRow(title="Your own picture", subtitle="Used in both light and dark mode")
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda _b: self.choose_picture())
        own.add_suffix(choose)
        more = Adw.ActionRow(title="More wallpapers", subtitle="GNOME’s wallpapers and colour backgrounds",
                             activatable=True)
        more.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        more.connect("activated", lambda _r: open_gnome_panel("background"))
        self.add_rows(wallpaper, *(r[0] for r in self.kulan_wallpaper_rows), own, more)

        windows = self.add_group("Window buttons", "Changes apply to apps as you open them.")
        windows.add(choice_row("Style", None,
                               [("symbols", "Coloured with symbols"), ("dots", "Mac dots"),
                                ("gnome", "GNOME standard")],
                               button_style, set_button_style))
        if has_key(self.wm, "button-layout"):
            windows.add(choice_row("Position", None, [("left", "Left, like a Mac"), ("right", "Right")],
                                   lambda: "right" if self.wm.get_string("button-layout").startswith("appmenu")
                                   else "left",
                                   lambda v: self.wm.set_string("button-layout",
                                                                LAYOUT_LEFT if v == "left" else LAYOUT_RIGHT)))
        self.add_reset(self.reset)
        if self.background is not None:
            self.watch(self.background, "changed", lambda *_a: self.shown())
        self.shown()

    # --- light / dark / automatic -------------------------------------------------
    def get_style(self) -> str:
        if self.kulan is not None and self.kulan.get_boolean("automatic-style"):
            return "auto"
        if self.interface is not None and self.interface.get_string("color-scheme") == "prefer-dark":
            return "dark"
        return "light"

    def set_style(self, value: str) -> None:
        if self.kulan is not None:
            self.kulan.set_boolean("automatic-style", value == "auto")
        if value != "auto" and self.interface is not None:
            self.interface.set_string("color-scheme", "prefer-dark" if value == "dark" else "default")

    # --- accent ----------------------------------------------------------------------
    def accent_row(self) -> Adw.ActionRow:
        row = Adw.ActionRow(title="Accent colour")
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        add_swatch_style()
        current = self.interface.get_string("accent-color")
        first = None
        self.swatches = {}
        for value, label, _colour in ACCENTS:
            button = Gtk.ToggleButton(tooltip_text=label, css_classes=["kulan-swatch", f"kulan-swatch-{value}"],
                                      valign=Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.LABEL], [f"{label} accent"])
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.set_active(value == current)
            button.connect("toggled", self.on_accent, value)
            self.swatches[value] = button
            box.append(button)
        row.add_suffix(box)
        return row

    def on_accent(self, button: Gtk.ToggleButton, value: str) -> None:
        if button.get_active():
            self.interface.set_string("accent-color", value)

    # --- wallpaper -----------------------------------------------------------------
    def kulan_wallpaper_active(self, index: int = 0) -> bool:
        if self.background is None:
            return False
        _title, _subtitle, light, dark = KULAN_WALLPAPERS[index]
        return (self.background.get_string("picture-uri") == light
                and self.background.get_string("picture-uri-dark") == dark)

    def on_use_wallpaper(self, _button: Gtk.Button, index: int) -> None:
        self.use_kulan_wallpaper(index)

    def use_kulan_wallpaper(self, index: int = 0) -> None:
        if self.background is None:
            return
        title, _subtitle, light, dark = KULAN_WALLPAPERS[index]
        for key in ("picture-uri", "picture-uri-dark", "picture-options", "primary-color"):
            self.background.reset(key)
        if not self.kulan_wallpaper_active(index):  # not the default, or a site-wide default
            self.background.set_string("picture-uri", light)
            self.background.set_string("picture-uri-dark", dark)
        self.toast(f"{title} wallpaper on")

    def choose_picture(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a wallpaper")
        images = Gtk.FileFilter(name="Pictures")
        images.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pictures:
            dialog.set_initial_folder(Gio.File.new_for_path(pictures))

        def done(d, result) -> None:
            try:
                picked = d.open_finish(result)
            except GLib.Error:
                return  # cancelled
            if picked is not None and self.background is not None:
                uri = picked.get_uri()
                self.background.set_string("picture-uri", uri)
                self.background.set_string("picture-uri-dark", uri)
                self.background.set_string("picture-options", "zoom")
                self.toast("Wallpaper changed")

        dialog.open(self.window, None, done)

    # --- refresh and reset --------------------------------------------------------------
    def shown(self) -> None:
        for index, (_row, check, use) in enumerate(self.kulan_wallpaper_rows):
            active = self.kulan_wallpaper_active(index)
            check.set_visible(active)
            use.set_visible(not active)

    def reset(self) -> None:
        reset_keys(self.interface, ["color-scheme", "accent-color"])
        reset_keys(self.kulan, ["automatic-style"])
        reset_keys(self.wm, ["button-layout"])
        reset_keys(self.background, ["picture-uri", "picture-uri-dark", "picture-options", "primary-color"])
        set_button_style("symbols")
        self.window.reload_page(self)
        self.toast("Appearance reset to Kulan defaults")
