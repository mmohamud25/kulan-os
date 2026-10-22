#!/usr/bin/python3
"""Check the Kulan look for GTK 4 apps with Debian's GTK and libadwaita.

  check-gtk-look.py /usr/share/kulan/gtk/look/gtk-4.0.css   (under xvfb-run)

Loads the stylesheet as a user's gtk.css would be (user priority), fails on
any CSS error GTK reports, then reads back what the window colour works out
to in light and dark mode: navy (blue well above red, dark) in dark mode and
a near-white mist in light mode. The relative-colour formulas only work with
GTK 4.16+, so this is the real test of them.
"""
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402


def settle() -> None:
    ctx = GLib.MainContext.default()
    for _ in range(200):
        ctx.iteration(False)


def main() -> int:
    Adw.init()
    display = Gdk.Display.get_default()
    errors: list[str] = []
    look = Gtk.CssProvider()
    look.connect("parsing-error", lambda _p, section, error: errors.append(f"{section.to_string()}: {error.message}"))
    look.load_from_path(sys.argv[1])
    Gtk.StyleContext.add_provider_for_display(display, look, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    # A label coloured with the window and sidebar colours, to read them back.
    probe = Gtk.CssProvider()
    probe.load_from_string("label.kulan-window { color: var(--window-bg-color); }"
                           "label.kulan-sidebar { color: var(--sidebar-bg-color); }"
                           "label.kulan-view { color: var(--view-bg-color); }")
    Gtk.StyleContext.add_provider_for_display(display, probe, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    window = Gtk.Window()
    box = Gtk.Box()
    labels = {name: Gtk.Label(label="x", css_classes=[f"kulan-{name}"]) for name in ("window", "sidebar", "view")}
    for label in labels.values():
        box.append(label)
    window.set_child(box)
    window.present()

    manager = Adw.StyleManager.get_default()
    seen: dict[str, dict[str, tuple[int, int, int]]] = {}
    for scheme, variant in ((Adw.ColorScheme.FORCE_LIGHT, "light"), (Adw.ColorScheme.FORCE_DARK, "dark")):
        manager.set_color_scheme(scheme)
        settle()
        seen[variant] = {}
        for name, label in labels.items():
            c = label.get_color()
            seen[variant][name] = (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
    window.destroy()

    def hexed(rgb: tuple[int, int, int]) -> str:
        return "#%02x%02x%02x" % rgb

    print("light:", ", ".join(f"{k} {hexed(v)}" for k, v in seen["light"].items()))
    print("dark:", ", ".join(f"{k} {hexed(v)}" for k, v in seen["dark"].items()))
    problems = list(errors)
    for name, (r, g, b) in seen["dark"].items():
        if not (b > r + 20 and b < 90):
            problems.append(f"dark {name} isn't Kulan navy: {hexed((r, g, b))}")
    for name, (r, g, b) in seen["light"].items():
        if min(r, g, b) < 220 or b < r:
            problems.append(f"light {name} isn't Kulan mist: {hexed((r, g, b))}")
    if seen["light"]["view"] != (255, 255, 255):
        problems.append(f"light view should stay white: {hexed(seen['light']['view'])}")
    for p in problems:
        print("FAIL", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
