#!/usr/bin/env python3
"""Screenshots of Kulan's own apps for their Store listings.

    xvfb-run -a dbus-run-session -- python3 scripts/render-store-screenshots.py

Writes website/images/store/*.png (served at os.kulangroup.com/images/store/,
named in packages/kulan-desktop-settings/root/usr/share/swcatalog/xml/kulan-apps.xml).
Uses each app's built-in sample data: no network, no real organisation.
Needs GTK 4 and libadwaita (python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "website" / "images" / "store"
WIDTH, HEIGHT = 1100, 720


def load_fleet():
    path = ROOT / "packages/kulan-fleet-admin/root/usr/bin/kulan-fleet-admin"
    loader = importlib.machinery.SourceFileLoader("kulan_fleet_admin", str(path))
    spec = importlib.util.spec_from_loader("kulan_fleet_admin", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def snap(window: Gtk.Window, out: Path) -> None:
    paintable = Gtk.WidgetPaintable.new(window)
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    texture = window.get_native().get_renderer().render_texture(snapshot.to_node(),
                                                                Graphene.Rect().init(0, 0, width, height))
    out.parent.mkdir(parents=True, exist_ok=True)
    texture.save_to_png(str(out))
    print(f"  wrote {out.relative_to(ROOT)}")


def main() -> int:
    fleet = load_fleet()
    app = fleet.FleetApp(self_test=False)
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    shots = []

    def activate(_app) -> None:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        win = fleet.FleetWindow(app, fleet.Session(), fleet.sample())
        win.set_default_size(WIDTH, HEIGHT)
        win.present()

        def steps() -> bool:
            snap(win, OUT / "kulan-fleet-1.png")
            win.open_device(win.data["devices"][0])
            GLib.timeout_add(600, second)
            return False

        def second() -> bool:
            snap(win, OUT / "kulan-fleet-2.png")
            shots.append(True)
            app.quit()
            return False
        GLib.timeout_add(900, steps)

    app.connect("activate", activate)
    app.run([sys.argv[0]])
    return 0 if shots else 1


if __name__ == "__main__":
    sys.exit(main())
