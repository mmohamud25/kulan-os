#!/usr/bin/python3
"""Draw the Kulan app icon family (2.4) into the Kulan icon theme.

One shape for every icon: a rounded square (30 % corners) with a two-stop
gradient, a soft shadow, a light top edge and a white line symbol, as in
the 2.4 "Icons" mockup. Kulan's apps use teal and navy; GNOME's apps keep
a colour of their own in the same shape. Apps with a brand logo (Firefox,
LibreOffice, GIMP, VLC...) are left alone so they stay recognisable.

  scripts/render-app-icons.py      (writes usr/share/icons/Kulan/scalable/apps)

The symbols are simple strokes on a 24-unit grid, drawn for Kulan.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packages/kulan-desktop-settings/root/usr/share/icons/Kulan/scalable/apps"

GEAR = ("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"
        "M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1")
SYMBOLS = {
    "folder": "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "gear": GEAR,
    "sparkle": "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
    "bag": "M6 8h12l-1 12H7zM9 8a3 3 0 0 1 6 0",
    "terminal": "M5 8l4 4-4 4M12 16h7",
    "text": "M5 5h14M5 10h14M5 15h9",
    "calendar": "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
    "photo": "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4",
    "video": "M4 6h12v12H4zM16 10l5-3v10l-5-3",
    "calculator": "M6 3h12v18H6zM9 7h6M9 12h.01M12 12h.01M15 12h.01M9 16h.01M12 16h.01M15 16h.01",
    "backup": "M12 8v4l3 2M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5",
    "cloud": "M17.5 19H8a5 5 0 1 1 1.2-9.86A6 6 0 0 1 20 12a3.5 3.5 0 0 1-2.5 7z",
    "help": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01",
    "pulse": "M3 12h4l3-8 4 16 3-8h4",
    "clock": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2",
    "map": "M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14",
    "person": "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
    "document": "M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5",
    "camera": "M4 8h4l2-3h4l2 3h4v11H4zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
    "letter": "M6 19l6-15 6 15M8.5 13h7",
    "logs": "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5",
    "disk": "M4 6h16v12H4zM4 14h16M16 17h.01",
    "pie": "M12 3a9 9 0 1 0 9 9h-9zM15 2.6A9 9 0 0 1 21.4 9H15z",
    "archive": "M4 4h16v4H4zM5 8h14v12H5zM10 12h4",
    "font": "M3 18L7.5 6 12 18M4.7 14h5.6M17.5 11.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM20 11v7",
    "remote": "M3 5h18v11H3zM8 20h8M12 16v4",
    "blocks": "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    "sliders": "M4 7h10M20 17H10M18 7h2M4 17h2M16 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    "scanner": "M4 14h16v5H4zM6 14l12-8M8 16.5h.01",
    "shield": "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4",
    "fleet": "M3 5h8v6H3zM13 5h8v6h-8zM3 14h8v6H3zM13 14h8v6h-8zM7 11v3M17 11v3",
}

# icon name -> (gradient top, gradient bottom, symbol)
ICONS = {
    # Kulan's own apps: teal and navy.
    "kulan-settings": ("#5eead4", "#0d9488", "gear"),
    "org.kulan.Assistant": ("#a78bfa", "#0d9488", "sparkle"),
    "kulan-setup-assistant": ("#2dd4bf", "#0a2240", "ring"),
    "org.gnome.Nautilus": ("#2dd4bf", "#0f766e", "folder"),
    "org.gnome.Software": ("#fbbf24", "#d97706", "bag"),
    "org.gnome.DejaDup": ("#34d399", "#047857", "backup"),
    "org.gnome.Ptyxis": ("#1e3a5f", "#071428", "terminal"),
    "org.gnome.Console": ("#1e3a5f", "#071428", "terminal"),
    "org.gnome.Terminal": ("#1e3a5f", "#071428", "terminal"),
    # GNOME's apps, each with a colour of its own.
    "org.gnome.TextEditor": ("#fde68a", "#f59e0b", "text"),
    "org.gnome.Calendar": ("#f87171", "#b91c1c", "calendar"),
    "org.gnome.Loupe": ("#f472b6", "#f59e0b", "photo"),
    "org.gnome.Totem": ("#818cf8", "#4338ca", "video"),
    "org.gnome.Calculator": ("#475569", "#1e293b", "calculator"),
    "org.gnome.Weather": ("#38bdf8", "#0369a1", "cloud"),
    "org.gnome.Yelp": ("#94a3b8", "#475569", "help"),
    "org.gnome.Settings": ("#94a3b8", "#475569", "gear"),
    "org.gnome.SystemMonitor": ("#34d399", "#0f766e", "pulse"),
    "org.gnome.clocks": ("#1e3a5f", "#0a2240", "clock"),
    "org.gnome.Maps": ("#4ade80", "#15803d", "map"),
    "org.gnome.Contacts": ("#94a3b8", "#334155", "person"),
    "org.gnome.Evince": ("#f87171", "#be123c", "document"),
    "org.gnome.Papers": ("#f87171", "#be123c", "document"),
    "org.gnome.Snapshot": ("#64748b", "#1e293b", "camera"),
    "org.gnome.Characters": ("#fbbf24", "#b45309", "letter"),
    "org.gnome.Logs": ("#64748b", "#334155", "logs"),
    "org.gnome.DiskUtility": ("#94a3b8", "#475569", "disk"),
    "org.gnome.baobab": ("#2dd4bf", "#1d4ed8", "pie"),
    "org.gnome.FileRoller": ("#d6a26a", "#92400e", "archive"),
    "org.gnome.font-viewer": ("#f8fafc", "#94a3b8", "font"),
    "org.gnome.Connections": ("#60a5fa", "#1d4ed8", "remote"),
    "org.gnome.Extensions": ("#a3e635", "#4d7c0f", "blocks"),
    "org.gnome.tweaks": ("#94a3b8", "#334155", "sliders"),
    "org.gnome.SimpleScan": ("#94a3b8", "#475569", "scanner"),
    "gufw": ("#f87171", "#991b1b", "shield"),
}

# The Kulan "gathering": six dots around a centre dot, white.
RING = "".join(
    f'<circle cx="{12 + 7.5 * c:.3f}" cy="{12 + 7.5 * s:.3f}" r="1.9"/>'
    for c, s in ((0, -1), (0.866, -0.5), (0.866, 0.5), (0, 1), (-0.866, 0.5), (-0.866, -0.5))
) + '<circle cx="12" cy="12" r="2.4"/>'


def svg(name: str, top: str, bottom: str, symbol: str) -> str:
    gid = name.replace(".", "-")
    if symbol == "ring":
        mark = f'<g fill="#ffffff">{RING}</g>'
    else:
        mark = (f'<path d="{SYMBOLS[symbol]}" fill="none" stroke="#ffffff" stroke-width="2" '
                'stroke-linecap="round" stroke-linejoin="round"/>')
    # Dark symbols on very light tiles.
    if top.lower() in ("#f8fafc",):
        mark = mark.replace("#ffffff", "#0a2240")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="{gid}-bg" x1="0.33" y1="0" x2="0.67" y2="1">
      <stop offset="0" stop-color="{top}"/>
      <stop offset="1" stop-color="{bottom}"/>
    </linearGradient>
    <linearGradient id="{gid}-edge" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.30"/>
      <stop offset="0.12" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <filter id="{gid}-shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="3.5"/>
      <feOffset dy="4" result="blur"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.28"/></feComponentTransfer>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <g filter="url(#{gid}-shadow)">
    <rect x="10" y="8" width="108" height="108" rx="32" fill="url(#{gid}-bg)"/>
    <rect x="10.5" y="8.5" width="107" height="107" rx="31.5" fill="none" stroke="url(#{gid}-edge)" stroke-width="1"/>
  </g>
  <g transform="translate(64 62) scale(2.6) translate(-12 -12)">{mark}</g>
</svg>
'''


# Icons that ship inside their own app's package (not on every computer).
PACKAGE_ICONS = {
    ROOT / "packages/kulan-fleet-admin/root/usr/share/icons/hicolor/scalable/apps/org.kulan.Fleet.svg":
        ("org.kulan.Fleet", "#2dd4bf", "#0a2240", "fleet"),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    for name, (top, bottom, symbol) in ICONS.items():
        (OUT / f"{name}.svg").write_text(svg(name, top, bottom, symbol))
    for path, (name, top, bottom, symbol) in PACKAGE_ICONS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg(name, top, bottom, symbol))
    print(f"Drew {len(ICONS)} app icons into {OUT} and {len(PACKAGE_ICONS)} into their packages")


if __name__ == "__main__":
    main()
