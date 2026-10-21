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

    # Kulan Settings app icon: the logo's badge with a white gear.
    teeth, r_out, r_in, cx, cy = 10, 170, 138, 256, 256
    points = []
    for i in range(teeth * 2):
        for edge in (-0.32, 0.32):
            angle = (i + 0.5 + edge) * math.pi / teeth
            radius = r_out if i % 2 == 0 else r_in
            points.append(f"{cx + radius * math.cos(angle):.1f},{cy + radius * math.sin(angle):.1f}")
    settings_svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="sb" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0F766E"/>
      <stop offset="1" stop-color="#0B2A4A"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="480" height="480" rx="112" fill="url(#sb)"/>
  <circle cx="256" cy="256" r="150" fill="#F59E0B" fill-opacity="0.22"/>
  <path fill-rule="evenodd" fill="#ECFDF5" d="M{" L".join(points)} Z M256 196 a60 60 0 1 0 0.01 0 Z"/>
</svg>'''
    icons = CHROOT.parent.parent / "kulan-settings" / "root" / "usr/share/icons/hicolor"
    (icons / "scalable/apps").mkdir(parents=True, exist_ok=True)
    (icons / "scalable/apps/kulan-settings.svg").write_text(settings_svg + "\n")
    print(f"  wrote {(icons / 'scalable/apps/kulan-settings.svg').relative_to(ROOT)}")
    for px in (48, 128, 256):
        render(settings_svg, icons / f"{px}x{px}/apps/kulan-settings.png", px, px)

    # Wallpapers.
    render((ASSETS / "wallpaper-dark.svg").read_text(), CHROOT / "usr/share/backgrounds/kulan/kulan-dark.png")
    render((ASSETS / "wallpaper-light.svg").read_text(), CHROOT / "usr/share/backgrounds/kulan/kulan-light.png")

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
        # The tree and the gathered people alone (no badge, no sun), in one colour.
        variants[f"-{suffix}"] = f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <path d="M60 176 C104 96 408 96 452 176 C408 206 104 206 60 176 Z" fill="{colour}"/>
  <path d="M256 352 V266 M256 266 L196 190 M256 266 L316 190" stroke="{colour}" stroke-width="40" stroke-linecap="round" fill="none"/>
  <g fill="{colour}">
    <circle cx="104" cy="380" r="34"/><circle cx="178" cy="420" r="34"/><circle cx="256" cy="432" r="34"/>
    <circle cx="334" cy="420" r="34"/><circle cx="408" cy="380" r="34"/>
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
