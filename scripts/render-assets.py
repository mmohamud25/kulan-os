#!/usr/bin/env python3
"""Render Kulan OS branding PNGs from the SVG sources in assets/.

The generated PNGs are committed to the repo, so the ISO build itself never
needs this script. Re-run it after editing anything in assets/ or after
changing the product name:

    python3 scripts/render-assets.py

Requires: rsvg-convert (Debian/Ubuntu: librsvg2-bin; macOS: brew install librsvg),
Pillow (pip install pillow) and a sans font (Inter or Noto Sans recommended).
"""
from __future__ import annotations

import math
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
# Images ship in the kulan-desktop-settings package.
CHROOT = ROOT / "packages" / "kulan-desktop-settings" / "root"
FONT = "Inter, Noto Sans, DejaVu Sans, sans-serif"


def read_conf() -> dict[str, str]:
    """Parse KEY="value" lines from kulan.conf (the single source of truth)."""
    conf: dict[str, str] = {}
    for line in (ROOT / "kulan.conf").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            parts = shlex.split(value)
            conf[key.strip()] = parts[0] if parts else ""
    return conf


def logo_group(x: float, y: float, size: float) -> str:
    """Return the logo's drawing as an SVG <g>, placed at (x, y) with given size."""
    svg = (ASSETS / "logo.svg").read_text()
    body = svg[svg.index(">", svg.index("<svg")) + 1 : svg.rindex("</svg>")]
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    # Make gradient ids unique so several logos can share one document.
    body = body.replace('id="badge"', 'id="lgBadge"').replace("url(#badge)", "url(#lgBadge)")
    scale = size / 512
    return f'<g transform="translate({x},{y}) scale({scale})">{body}</g>'


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(
    svg: str,
    out: Path,
    width: int | None = None,
    height: int | None = None,
    opaque: bool = False,
) -> None:
    """Render SVG to PNG. opaque=True writes plain RGB (no alpha channel),
    which bootloaders (GRUB, ISOLINUX) handle most reliably."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as tmp:
        tmp.write(svg)
        src = tmp.name
    cmd = ["rsvg-convert", "-o", str(out)]
    if width:
        cmd += ["-w", str(width)]
    if height:
        cmd += ["-h", str(height)]
    subprocess.run(cmd + [src], check=True)
    Path(src).unlink()
    if opaque:
        from PIL import Image

        with Image.open(out) as img:
            img.convert("RGB").save(out, optimize=True)
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"  wrote {shown}")


def main() -> int:
    if not shutil.which("rsvg-convert"):
        print("error: rsvg-convert not found (install librsvg2-bin)", file=sys.stderr)
        return 1
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow not found (pip install pillow)", file=sys.stderr)
        return 1
    conf = read_conf()
    name = esc(conf["KULAN_NAME"])
    version = esc(conf["KULAN_VERSION"])
    logo_svg = (ASSETS / "logo.svg").read_text()

    print("Rendering Kulan OS assets...")

    # Logo: scalable icon (used by os-release LOGO=) + PNG sizes.
    for dest in (
        CHROOT / "usr/share/icons/hicolor/scalable/apps/kulan-logo.svg",
        CHROOT / "usr/share/kulan/logo.svg",
    ):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(logo_svg)
        print(f"  wrote {dest.relative_to(ROOT)}")
    for px in (48, 128, 256):
        render(logo_svg, CHROOT / f"usr/share/icons/hicolor/{px}x{px}/apps/kulan-logo.png", px, px)
    render(logo_svg, CHROOT / "usr/share/kulan/calamares/logo.png", 256, 256)

    # Kulan app icons, in GNOME's Adwaita style: a rounded tile with a darker
    # edge underneath (the "depth" of Adwaita app icons) and a light symbol.
    def adwaita_tile(top: str, bottom: str, edge: str, symbol: str) -> str:
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="face" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{top}"/>
      <stop offset="1" stop-color="{bottom}"/>
    </linearGradient>
    <linearGradient id="shine" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.18"/>
      <stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <rect x="40" y="64" width="432" height="416" rx="88" fill="{edge}"/>
  <rect x="40" y="40" width="432" height="416" rx="88" fill="url(#face)"/>
  <rect x="40" y="40" width="432" height="416" rx="88" fill="url(#shine)"/>
  {symbol}
</svg>'''

    def write_icon(package: str, icon: str, svg: str) -> None:
        icons = CHROOT.parent.parent / package / "root" / "usr/share/icons/hicolor"
        (icons / "scalable/apps").mkdir(parents=True, exist_ok=True)
        (icons / f"scalable/apps/{icon}.svg").write_text(svg + "\n")
        print(f"  wrote {(icons / f'scalable/apps/{icon}.svg').relative_to(ROOT)}")
        for px in (48, 128, 256):
            render(svg, icons / f"{px}x{px}/apps/{icon}.png", px, px)

    # Kulan Settings: a light gear on Kulan teal.
    teeth, r_out, r_in, cx, cy = 10, 150, 122, 256, 248
    points = []
    for i in range(teeth * 2):
        for edge in (-0.32, 0.32):
            angle = (i + 0.5 + edge) * math.pi / teeth
            radius = r_out if i % 2 == 0 else r_in
            points.append(f"{cx + radius * math.cos(angle):.1f},{cy + radius * math.sin(angle):.1f}")
    gear = (f'<path fill-rule="evenodd" fill="#0B3B4A" fill-opacity="0.25" transform="translate(0 8)" '
            f'd="M{" L".join(points)} Z M256 196 a52 52 0 1 0 0.01 0 Z"/>'
            f'<path fill-rule="evenodd" fill="#F2FBF9" d="M{" L".join(points)} Z M256 196 a52 52 0 1 0 0.01 0 Z"/>')
    write_icon("kulan-settings", "kulan-settings", adwaita_tile("#2BB3A3", "#12807A", "#0B5B5A", gear))

    # Setup Assistant: a large sparkle and two small ones, on Kulan blue.
    def sparkle(x: float, y: float, r: float) -> str:
        k = r * 0.22
        return (f'M{x} {y - r} C{x + k} {y - k} {x + k} {y - k} {x + r} {y} '
                f'C{x + k} {y + k} {x + k} {y + k} {x} {y + r} '
                f'C{x - k} {y + k} {x - k} {y + k} {x - r} {y} '
                f'C{x - k} {y - k} {x - k} {y - k} {x} {y - r} Z')
    stars = (f'<path fill="#06203A" fill-opacity="0.25" transform="translate(0 8)" '
             f'd="{sparkle(236, 262, 130)} {sparkle(366, 152, 54)} {sparkle(368, 352, 34)}"/>'
             f'<path fill="#FFF7E6" d="{sparkle(236, 262, 130)}"/>'
             f'<path fill="#FBBF24" d="{sparkle(366, 152, 54)} {sparkle(368, 352, 34)}"/>')
    write_icon("kulan-welcome", "kulan-setup-assistant", adwaita_tile("#3A7BD5", "#1E4E9C", "#173A75", stars))

    # Kulan folders: an icon theme with just the folder icons, inheriting
    # Adwaita for everything else. Teal folders, a white symbol on the special
    # ones, and colour versions for the Folder Colour menu in Files.
    folder_colours = {
        # name: (back, front top, front bottom, edge)
        "teal": ("#13857C", "#3CC3B2", "#1A9C8F", "#0E6F67"),
        "blue": ("#2F6FC4", "#6AA6EE", "#4A8BE0", "#285FA8"),
        "purple": ("#6E3FA8", "#A97BDE", "#8A57C8", "#5C3490"),
        "red": ("#B7343F", "#EE7680", "#DC4C58", "#9C2B35"),
        "orange": ("#C65A12", "#F7A15A", "#EC7D2C", "#A84B0E"),
        "yellow": ("#B88A06", "#F6D364", "#E8B71E", "#9B7405"),
        "green": ("#2F8A3E", "#6FCB7E", "#45AE55", "#277334"),
        "grey": ("#6F7A86", "#B9C2CC", "#949FAB", "#5F6974"),
    }
    stroke = 'stroke="#FFFFFF" stroke-opacity="0.92" fill="none" stroke-width="22" stroke-linecap="round" stroke-linejoin="round"'
    fill = 'fill="#FFFFFF" fill-opacity="0.92"'
    folder_symbols = {
        "folder": "",
        "folder-documents": (f'<path {fill} d="M206 228 h76 l42 42 v112 q0 10 -10 10 h-108 q-10 0 -10 -10 v-144 q0 -10 10 -10 Z"/>'
                             '<path fill="#000000" fill-opacity="0.12" d="M282 228 v32 q0 10 10 10 h32 Z"/>'),
        "folder-pictures": (f'<rect {stroke} x="188" y="236" width="136" height="150" rx="16"/>'
                            f'<path {fill} d="M200 372 l40 -52 l30 34 l18 -20 l32 38 Z"/><circle {fill} cx="292" cy="276" r="14"/>'),
        "folder-download": f'<path {stroke} d="M256 228 v116 M210 300 l46 46 l46 -46 M196 388 h120"/>',
        "folder-music": (f'<path {stroke} d="M236 360 v-118 l84 -18 v118"/><circle {fill} cx="216" cy="362" r="26"/>'
                         f'<circle {fill} cx="300" cy="342" r="26"/>'),
        "folder-videos": (f'<rect {stroke} x="180" y="248" width="152" height="124" rx="18"/>'
                          f'<path {fill} d="M240 284 v52 l46 -26 Z"/>'),
        "folder-templates": (f'<path {stroke} stroke-dasharray="30 24" d="M206 236 h100 q12 0 12 12 v124 q0 12 -12 12 h-100 q-12 0 -12 -12 v-124 q0 -12 12 -12 Z"/>'
                             f'<path {stroke} d="M228 290 h56 M228 330 h40"/>'),
        "folder-publicshare": (f'<circle {fill} cx="256" cy="262" r="30"/>'
                               f'<path {fill} d="M196 382 q0 -64 60 -64 q60 0 60 64 Z"/>'),
        "folder-remote": (f'<circle {stroke} cx="256" cy="310" r="66"/>'
                          f'<path {stroke} d="M190 310 h132 M256 244 q-34 66 0 132 M256 244 q34 66 0 132"/>'),
        "user-desktop": (f'<rect {stroke} x="182" y="240" width="148" height="100" rx="14"/>'
                         f'<path {stroke} d="M226 382 h60 M256 342 v38"/>'),
        "user-home": f'<path {stroke} d="M188 306 l68 -62 l68 62 M206 292 v86 h100 v-86 M240 378 v-46 h32 v46"/>',
    }

    def kulan_folder(colour: str, symbol: str = "", open_: bool = False) -> str:
        back, top, bottom, edge = folder_colours[colour]
        front_y = 186 if open_ else 168  # drag-and-drop target: the front tips down a little
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="front" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{top}"/>
      <stop offset="1" stop-color="{bottom}"/>
    </linearGradient>
  </defs>
  <path fill="{back}" d="M56 128 q0 -28 28 -28 h118 q14 0 24 10 l26 26 h176 q28 0 28 28 v244 q0 28 -28 28 H84 q-28 0 -28 -28 Z"/>
  <rect x="56" y="{front_y + 8}" width="400" height="{428 - front_y}" rx="30" fill="{edge}"/>
  <rect x="56" y="{front_y}" width="400" height="{428 - front_y}" rx="30" fill="url(#front)"/>
  <rect x="56" y="{front_y}" width="400" height="6" rx="3" fill="#FFFFFF" fill-opacity="0.35"/>
  <g transform="translate(0 12)">{symbol}</g>
</svg>'''

    theme = CHROOT / "usr/share/icons/Kulan"
    places = theme / "scalable/places"
    mimes = theme / "scalable/mimetypes"
    places.mkdir(parents=True, exist_ok=True)
    mimes.mkdir(parents=True, exist_ok=True)
    for icon_name, symbol in folder_symbols.items():
        (places / f"{icon_name}.svg").write_text(kulan_folder("teal", symbol) + "\n")
    (places / "folder-drag-accept.svg").write_text(kulan_folder("teal", open_=True) + "\n")
    (mimes / "inode-directory.svg").write_text(kulan_folder("teal") + "\n")
    for colour in folder_colours:
        (places / f"folder-kulan-{colour}.svg").write_text(kulan_folder(colour) + "\n")
    # Tag dots for Files (Tags on right-click), shown as emblems on files.
    emblems = theme / "scalable/emblems"
    emblems.mkdir(parents=True, exist_ok=True)
    tag_colours = {"red": "#FF3B30", "orange": "#FF9500", "yellow": "#FFCC00", "green": "#34C759",
                   "blue": "#007AFF", "purple": "#AF52DE", "grey": "#8E8E93"}
    for colour, fill in tag_colours.items():
        (emblems / f"emblem-kulan-tag-{colour}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
            f'<circle cx="8" cy="8" r="6.5" fill="{fill}" stroke="#FFFFFF" stroke-width="1.5"/></svg>\n')
    (theme / "index.theme").write_text(
        "[Icon Theme]\n"
        "Name=Kulan\n"
        "Comment=Kulan OS folders, with GNOME's Adwaita icons for everything else\n"
        "Inherits=Adwaita,hicolor\n"
        "Directories=scalable/places,scalable/mimetypes,scalable/emblems\n"
        "\n"
        "[scalable/places]\n"
        "Context=Places\n"
        "Size=128\n"
        "MinSize=8\n"
        "MaxSize=512\n"
        "Type=Scalable\n"
        "\n"
        "[scalable/mimetypes]\n"
        "Context=MimeTypes\n"
        "Size=128\n"
        "MinSize=8\n"
        "MaxSize=512\n"
        "Type=Scalable\n"
        "\n"
        "[scalable/emblems]\n"
        "Context=Emblems\n"
        "Size=16\n"
        "MinSize=8\n"
        "MaxSize=512\n"
        "Type=Scalable\n"
    )
    print(f"  wrote {theme.relative_to(ROOT)} ({len(list(places.iterdir())) + 1} icons)")

    # Wallpapers.
    render((ASSETS / "wallpaper-dark.svg").read_text(), CHROOT / "usr/share/backgrounds/kulan/kulan-dark.png")
    render((ASSETS / "wallpaper-light.svg").read_text(), CHROOT / "usr/share/backgrounds/kulan/kulan-light.png")
    # The 1.0 waves, kept as a second choice in Settings > Appearance.
    render((ASSETS / "wallpaper-waves-dark.svg").read_text(), CHROOT / "usr/share/backgrounds/kulan/kulan-waves-dark.png")
    render((ASSETS / "wallpaper-waves-light.svg").read_text(), CHROOT / "usr/share/backgrounds/kulan/kulan-waves-light.png")

    # Login-screen wordmark (GDM shows it under the user list; light text on dark).
    render(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="440" height="96" viewBox="0 0 440 96">
  {logo_group(0, 8, 80)}
  <text x="100" y="64" font-family="{FONT}" font-size="46" font-weight="600" fill="#FFFFFF">{name}</text>
</svg>''',
        CHROOT / "usr/share/kulan/login-wordmark.png",
    )

    # Debian's "vendor-logos": the logo Debian's login screen (greeter defaults)
    # and other Debian software show. kulan-desktop-settings registers this
    # directory as the vendor-logos alternative, so they show Kulan instead.
    vendor = CHROOT / "usr/share/kulan/vendor-logos"
    wordmark_svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="440" height="96" viewBox="0 0 440 96">
  {logo_group(0, 8, 80)}
  <text x="100" y="64" font-family="{FONT}" font-size="46" font-weight="600" fill="#FFFFFF">{name}</text>
</svg>'''
    vendor.mkdir(parents=True, exist_ok=True)
    (vendor / "logo.svg").write_text(logo_svg)
    for stem in ("logo-text", "logo-text-version"):
        (vendor / f"{stem}.svg").write_text(wordmark_svg + "\n")
    for px in (64, 128, 256):
        render(logo_svg, vendor / f"logo-{px}.png", px, px)
        for stem in ("logo-text", "logo-text-version"):
            render(wordmark_svg, vendor / f"{stem}-{px}.png", height=px)
    # Emblems (the alternative's slave links): full colour, white, and
    # symbolic (GNOME recolours #bebebe symbolic icons to match the theme).
    emblems = CHROOT / "usr/share/kulan/vendor-emblems"
    emblems.mkdir(parents=True, exist_ok=True)
    flat = {
        "white": "#FFFFFF",
        "symbolic": "#BEBEBE",
    }
    variants = {"": logo_svg}
    for suffix, colour in flat.items():
        # The gathering alone (no badge), in one colour: six people around
        # one centre. Cropped close, so it still reads at top-bar size.
        variants[f"-{suffix}"] = f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="56 56 400 400">
  <g fill="{colour}">
    <circle cx="256" cy="106" r="46"/><circle cx="386" cy="181" r="46"/><circle cx="386" cy="331" r="46"/>
    <circle cx="256" cy="406" r="46"/><circle cx="126" cy="331" r="46"/><circle cx="126" cy="181" r="46"/>
    <circle cx="256" cy="256" r="58"/>
  </g>
</svg>'''
    # The Kulan menu's top-bar icon (GNOME recolours "-symbolic" icons).
    symbolic = CHROOT / "usr/share/icons/hicolor/scalable/apps/kulan-logo-symbolic.svg"
    symbolic.write_text(variants["-symbolic"] + "\n")
    print(f"  wrote {symbolic.relative_to(ROOT)}")
    for suffix, svg in variants.items():
        (emblems / f"emblem-vendor{suffix}.svg").write_text(svg if svg.endswith("\n") else svg + "\n")
        for px in (64, 128, 256):
            render(svg, emblems / f"emblem-vendor{suffix}-{px}.png", px, px)

    # Boot-menu background (GRUB + ISOLINUX need 640x480). Keep the centre clear
    # for the menu text; branding sits in the bottom-right corner.
    render(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
  <defs>
    <linearGradient id="b" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#050E1A"/><stop offset="1" stop-color="#0B3354"/>
    </linearGradient>
    <radialGradient id="g" cx="0.15" cy="0.95" r="0.7">
      <stop offset="0" stop-color="#14B8A6" stop-opacity="0.35"/>
      <stop offset="1" stop-color="#14B8A6" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="640" height="480" fill="url(#b)"/>
  <rect width="640" height="480" fill="url(#g)"/>
  {logo_group(472, 404, 44)}
  <text x="524" y="434" font-family="{FONT}" font-size="20" font-weight="600" fill="#FFFFFF">{name}</text>
</svg>''',
        CHROOT / "usr/share/kulan/boot-splash.png",
        opaque=True,
    )

    # Boot splash logo (Plymouth draws it on its own dark background).
    render(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="360" height="200" viewBox="0 0 360 200">
  {logo_group(124, 0, 112)}
  <text x="180" y="176" text-anchor="middle" font-family="{FONT}" font-size="34" font-weight="600" fill="#FFFFFF">{name}</text>
</svg>''',
        CHROOT / "usr/share/kulan/plymouth/watermark.png",
    )

    # Installed system's boot menu background (shown only if you hold Esc).
    render(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
  <defs>
    <linearGradient id="b" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#040B16"/><stop offset="1" stop-color="#081A33"/>
    </linearGradient>
  </defs>
  <rect width="1920" height="1080" fill="url(#b)"/>
  {logo_group(1736, 976, 56)}
  <text x="1720" y="1014" text-anchor="end" font-family="{FONT}" font-size="26" font-weight="600" fill="#FFFFFF">{name}</text>
</svg>''',
        CHROOT / "usr/share/kulan/grub-background.png",
        opaque=True,
    )

    # Installer welcome image (457x300 matches Debian's original slot).
    render(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="457" height="300" viewBox="0 0 457 300">
  {logo_group(168, 20, 120)}
  <text x="228.5" y="190" text-anchor="middle" font-family="{FONT}" font-size="40" font-weight="700" fill="#0B2A4A">{name}</text>
  <text x="228.5" y="226" text-anchor="middle" font-family="{FONT}" font-size="18" fill="#3B5873">Version {version} · based on Debian</text>
</svg>''',
        CHROOT / "usr/share/kulan/calamares/welcome.png",
    )

    # Installer slideshow image (800x480).
    render(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="480" viewBox="0 0 800 480">
  <defs>
    <linearGradient id="b" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0A2240"/><stop offset="1" stop-color="#0E4A6E"/>
    </linearGradient>
  </defs>
  <rect width="800" height="480" rx="24" fill="url(#b)"/>
  <path d="M -20 380 C 180 300, 360 450, 560 360 S 760 300, 820 330" fill="none" stroke="#2DD4BF" stroke-opacity="0.5" stroke-width="4"/>
  <path d="M -20 410 C 200 330, 380 480, 580 390 S 760 330, 820 360" fill="none" stroke="#60A5FA" stroke-opacity="0.35" stroke-width="2"/>
  {logo_group(330, 70, 140)}
  <text x="400" y="270" text-anchor="middle" font-family="{FONT}" font-size="44" font-weight="700" fill="#FFFFFF">{name}</text>
  <text x="400" y="310" text-anchor="middle" font-family="{FONT}" font-size="20" fill="#BFDBFE">Secure, simple, and ready for everyday work.</text>
</svg>''',
        CHROOT / "usr/share/kulan/calamares/slide1.png",
    )
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
