#!/usr/bin/env python3
"""Build os.kulangroup.com: Home, Download, Install guide, What's new and
Help, as plain HTML into the site folder next to the update channel (apt/).

    ci/build-website.py SITE_DIR

What's new is generated from the release notes Software Update uses, one
section per version (#v2.4), so the "More Details" link in Software Update
always lands on that version. Downloads link to the newest GitHub Release
(kulan-os-amd64.iso, kulan-os-arm64.iso, SHA256SUMS); a small script fills
in the sizes, and says so plainly when the newest release isn't there yet.
"""
from __future__ import annotations

import datetime
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "website"
NOTES = ROOT / "packages/kulan-update-center/root/usr/share/kulan/release-notes.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "mmohamud25/kulan-os")
RELEASES = f"https://github.com/{REPO}/releases"
# Kulan USB Maker, on every release (ci/make-release.sh).
USB_MAKER_WINDOWS = f"{RELEASES}/latest/download/Kulan-USB-Maker-Windows.exe"
USB_MAKER_MAC = f"{RELEASES}/latest/download/Kulan-USB-Maker-Mac.dmg"
DOWNLOAD = f"{RELEASES}/latest/download"
SUPPORT = "support@kulangroup.com"
SECURITY = "security@kulangroup.com"


def conf() -> dict[str, str]:
    values = {}
    for line in (ROOT / "kulan.conf").read_text().splitlines():
        m = re.match(r'^([A-Z_]+)="(.*)"$', line.strip())
        if m:
            values[m.group(1)] = m.group(2)
    return values


C = conf()
VERSION = C["KULAN_VERSION"]          # 2.8.1: release notes and anchors (#v2.8.1)
LABEL = C.get("KULAN_VERSION_LABEL") or VERSION  # 2.8a: what people see
NAME = C["KULAN_NAME"]
e = html.escape

ICONS = {
    "download": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>',
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "sparkle": '<path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    "widgets": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "screen": '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>',
}


def icon(name: str) -> str:
    return (f'<svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


NAV = [("/", "Home"), ("/install/", "Install guide"), ("/whats-new/", "What’s new"), ("/help/", "Help")]


def page(path: str, title: str, description: str, body: str, script: str = "") -> str:
    links = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if href == path else ""}>{label}</a>' for href, label in NAV)
    current = ' aria-current="page"' if path == "/download/" else ""
    full_title = f"{title} · {NAME}" if path != "/" else f"{NAME}: {title}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(full_title)}</title>
<meta name="description" content="{e(description)}">
<meta name="theme-color" content="#071428">
<meta property="og:title" content="{e(full_title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:image" content="https://os.kulangroup.com/images/desktop-settings.jpg">
<link rel="icon" href="/images/logo.svg" type="image/svg+xml">
<link rel="preload" href="/fonts/Inter-Regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="wrap top">
<a class="brand" href="/"><img src="/images/logo.svg" alt="" width="36" height="36">{e(NAME)}</a>
<nav class="nav" aria-label="Main">{links}<a class="btn btn-primary btn-small" href="/download/"{current}>Download</a></nav>
</header>
<main id="main">
{body}
</main>
<footer>
<div class="wrap">
<span>© 2026 {e(C.get("KULAN_PUBLISHER", "Kulan Group LLC"))} · Kulan means “gathering” in Somali</span>
<nav aria-label="Footer"><a href="/security/">Security</a><a href="/privacy/">Privacy</a><a href="https://github.com/{REPO}">Source code</a><a href="https://kulangroup.com">kulangroup.com</a><a href="/help/#contact">Contact</a></nav>
</div>
</footer>
{script}
</body>
</html>
"""


def headline(item: str) -> tuple[str, str]:
    """Same split as Software Update: "Headline: details"."""
    head, sep, rest = item.partition(": ")
    if sep and 2 <= len(head) <= 64 and rest:
        return head, rest[0].upper() + rest[1:]
    return item, ""


def note_items(items: list[str]) -> str:
    out = []
    for item in items:
        head, rest = headline(item)
        out.append(f"<li><span><strong>{e(head)}</strong>{' — ' + e(rest) if rest else ''}</span></li>")
    return "\n".join(out)


def home(notes: list[dict]) -> str:
    latest = notes[0]
    features = [
        ("shield", "Secure by default", "Signed updates, a firewall, full-disk encryption and a compliance mode "
                                        "checked against CIS and NIST."),
        ("sparkle", "Kulan Assistant", "Private, and it runs on your computer. Writing tools in any app, and "
                                       "Ask Kulan to change settings in plain words."),
        ("widgets", "Widgets and a clean desktop", "Weather, calendar, clock, to-do list, now playing and system "
                                                   "status, arranged the way you like."),
        ("settings", "One simple Settings app", "Wi-Fi, screens, sound, accounts, privacy and family controls, all "
                                                "in one place."),
        ("screen", "Kulan Fleet", "Manage an organisation’s computers and people from one place, with remote help."),
        ("refresh", "Updates you can undo", "New versions arrive through Software Update, with a restore point "
                                            "taken first."),
    ]
    cards = "\n".join(f'<div class="card">{icon(i)}<h3>{e(t)}</h3><p>{e(d)}</p></div>' for i, t, d in features)
    body = f"""
<section class="wrap hero">
<div>
<a class="pill" href="/whats-new/#v{e(latest['version'])}">New in {e(latest.get('label') or latest['version'])}: {e(latest.get('title', ''))} →</a>
<h1>A calm, secure desktop.</h1>
<p class="lead">{e(NAME)} is a free operating system built on Debian {e(C.get('KULAN_DEBIAN_VERSION', '13'))}. It comes with signed updates, disk encryption, a private assistant and a simple installer.</p>
<div class="actions"><a class="btn btn-primary" href="/download/">{ICONS['download']}Download {e(NAME)} {e(LABEL)}</a><a class="btn btn-ghost" href="/install/">How to install</a></div>
<p class="muted">Free · PC and ARM versions · No account needed</p>
</div>
<div class="shot"><img src="/images/desktop-settings.jpg" width="1600" height="900" alt="The {e(NAME)} desktop with Kulan Settings open on the Wi-Fi and network page"></div>
</section>
<section class="band">
<div class="wrap">
<div class="head"><h2>Everything you need, nothing you don’t</h2><p class="lead">Built for home, school and small business computers.</p></div>
<div class="grid">{cards}</div>
</div>
</section>
<section class="wrap">
<div class="cta">
<div><h2>Try it without changing anything</h2><p class="lead">{e(NAME)} runs from a USB stick, so you can look around first. Installing takes about ten minutes, and it can sit next to Windows.</p></div>
<div class="actions"><a class="btn btn-primary" href="/download/">Download</a><a class="btn btn-ghost" href="/install/">Read the install guide</a></div>
</div>
</section>"""
    return page("/", "A calm, secure desktop", f"{NAME} is a free, secure desktop built on Debian, with a private "
                                                   "assistant and a simple installer.", body)


def download() -> str:
    body = f"""
<section class="wrap stack">
<div class="head" style="margin-bottom:12px"><h1>Download {e(NAME)} {e(LABEL)}</h1><p class="lead">Free, with no account needed. Most Windows laptops and desktops need the PC version.</p></div>
<p class="note" id="dl-wait" hidden>The newest download isn’t ready yet. Check back soon.</p>
<div class="grid-2">
<div class="card dl main">
<div class="row"><h2>PC</h2><span class="tag teal">Most computers</span></div>
<p>For Intel and AMD computers, including Windows laptops and desktops and older Intel Macs. Works with Secure Boot.</p>
<a class="btn btn-primary" data-asset="kulan-os-amd64.iso" href="{DOWNLOAD}/kulan-os-amd64.iso">{ICONS['download']}Download for PC</a>
<p class="muted">kulan-os-amd64.iso · <span data-size="kulan-os-amd64.iso">about 2 GB</span></p>
</div>
<div class="card dl">
<div class="row"><h2>ARM</h2><span class="tag amber">Apple Silicon Macs</span></div>
<p>For M1 to M5 Macs using the free UTM app, and other ARM computers.</p>
<a class="btn btn-ghost" data-asset="kulan-os-arm64.iso" href="{DOWNLOAD}/kulan-os-arm64.iso">{ICONS['download']}Download for ARM</a>
<p class="muted">kulan-os-arm64.iso · <span data-size="kulan-os-arm64.iso">about 1.6 GB</span></p>
</div>
</div>
<div class="grid-2">
<div class="outline stack">
<h2 style="font-size:20px">What your computer needs</h2>
<div class="facts">
<span><strong>Memory:</strong> 2 GB minimum, 4 GB or more recommended</span>
<span><strong>Storage:</strong> 20 GB of free space</span>
<span><strong>USB stick:</strong> 8 GB or larger (it will be erased)</span>
<span><strong>Internet:</strong> not needed to install</span>
</div>
</div>
<div class="outline stack">
<h2 style="font-size:20px">Check your download is genuine</h2>
<p class="muted" style="font-size:15px">Optional. Compare the file’s fingerprint with the list in <a href="{DOWNLOAD}/SHA256SUMS">SHA256SUMS</a>, which is signed with Kulan’s <a href="/apt/kulan-archive-keyring.gpg">update key</a> (<a href="{DOWNLOAD}/SHA256SUMS.sig">signature</a>).</p>
<div class="code">sha256sum kulan-os-amd64.iso</div>
<p class="muted">On a Mac: <code>shasum -a 256 kulan-os-arm64.iso</code><br>On Windows: <code>certutil -hashfile kulan-os-amd64.iso SHA256</code></p>
</div>
</div>
<div class="outline" style="display:flex;flex-wrap:wrap;gap:20px;align-items:center;justify-content:space-between">
<p>Downloaded? The install guide takes you from USB stick to desktop.</p>
<a class="btn btn-ghost" href="/install/">Open the install guide →</a>
</div>
<p class="muted"><a href="/whats-new/">Release notes for every version</a> · <a href="{RELEASES}">All downloads</a></p>
</section>"""
    script = f"""<script>
(function () {{
  var api = "https://api.github.com/repos/{REPO}/releases/latest";
  fetch(api, {{ headers: {{ "Accept": "application/vnd.github+json" }} }}).then(function (r) {{
    if (r.status === 404) {{ throw new Error("none"); }}
    if (!r.ok) {{ return null; }}
    return r.json();
  }}).then(function (rel) {{
    if (!rel) {{ return; }}
    var have = {{}};
    (rel.assets || []).forEach(function (a) {{ have[a.name] = a.size; }});
    document.querySelectorAll("[data-size]").forEach(function (el) {{
      var size = have[el.getAttribute("data-size")];
      if (size) {{ el.textContent = (size / 1073741824).toFixed(1) + " GB"; }}
    }});
    if (!have["kulan-os-amd64.iso"]) {{ throw new Error("none"); }}
  }}).catch(function (err) {{
    if (err && err.message === "none") {{
      document.getElementById("dl-wait").hidden = false;
      document.querySelectorAll("[data-asset]").forEach(function (a) {{ a.setAttribute("aria-disabled", "true"); }});
    }}
  }});
}})();
</script>"""
    return page("/download/", "Download", f"Download {NAME} {LABEL} for PC or ARM. Free, no account needed.",
                body, script)


def install() -> str:
    flow = (
        '<div class="flow" role="img" aria-label="Kulan USB Maker: pick the USB stick, click Make, wait until it says it’s ready">'
        '<span><i><svg viewBox="0 0 24 24" fill="none" stroke="#2dd4bf" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="7" y="9" width="10" height="13" rx="2"/><path d="M9 9V3h6v6"/></svg></i>Pick the USB stick</span>'
        '<svg viewBox="0 0 24 24" fill="none" stroke="#3a4d66" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>'
        '<span><i class="go"><svg viewBox="0 0 24 24" fill="none" stroke="#04201d" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg></i>Make</span>'
        '<svg viewBox="0 0 24 24" fill="none" stroke="#3a4d66" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>'
        '<span><i><svg viewBox="0 0 24 24" fill="none" stroke="#2dd4bf" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg></i>Ready</span>'
        '</div>')
    keys = "".join(f"<div><strong>{b}</strong> {k}</div>" for b, k in (
        ("Dell", "F12"), ("HP", "F9 or Esc"), ("Lenovo", "F12"), ("Acer", "F12"), ("ASUS", "F8 or Esc"),
        ("Intel Mac", "hold Option")))
    body = f"""
<section class="wrap stack" style="padding-bottom:24px">
<h1>Install guide</h1>
<p class="lead">About 20 minutes from download to desktop. Keep your computer plugged in.</p>
<div class="chips"><a href="#pc">On a PC</a><a href="#mac">On a Mac with UTM</a></div>
</section>
<section class="wrap stack" id="pc" style="padding-top:24px">
<h2>On a PC</h2>
<div class="note">
<h3>Will this delete Windows?</h3>
<p>Only if you choose to. Trying {e(NAME)} from the USB stick changes nothing on your computer. When you install, choose <strong>Erase</strong> to have only {e(NAME)}, or <strong>Next to Windows</strong> to keep both and pick one each time you start.</p>
<p>To keep Windows, do this in Windows first:</p>
<ol>
<li><strong>Back up</strong> your important files.</li>
<li><strong>Make room:</strong> right-click Start, open Disk Management, right-click the C: drive and choose Shrink Volume. Shrink by at least 20 GB (40 GB or more is better) and leave the new space unallocated.</li>
<li><strong>Using BitLocker?</strong> Write down your recovery key first. You’ll find it at account.microsoft.com/devices/recoverykey.</li>
<li><strong>Turn off Fast Startup</strong> in Control Panel › Power Options › Choose what the power buttons do.</li>
</ol>
</div>
<div class="step"><div>
<div class="step-title"><span class="num">1</span><h3>Make a USB stick</h3></div>
<p>Get Kulan USB Maker for <a href="{USB_MAKER_WINDOWS}">Windows</a> or <a href="{USB_MAKER_MAC}">Mac</a>, plug in a USB stick of 8 GB or more, pick it and click Make. It downloads {e(NAME)}, checks it’s genuine and writes the stick, then shows the key that starts your computer from it. Everything on the stick is erased.</p>
<p class="muted">The first time you open it, Windows may say it protected your PC: choose More info, then Run anyway. On a Mac, open it once, then choose Open Anyway in System Settings › Privacy &amp; Security. Rather use your own tool? <a href="/download/">Download the .iso</a> and write it with balenaEtcher, Raspberry Pi Imager (Use custom) or, on Windows, Rufus in DD image mode.</p>
</div><figure>{flow}</figure></div>
<div class="step"><div>
<div class="step-title"><span class="num">2</span><h3>Start from the USB stick</h3></div>
<p>Plug the stick in, restart, and keep tapping the boot-menu key as the computer starts. Choose the USB stick from the list.</p>
</div><figure><div class="keys">{keys}</div></figure></div>
<div class="step"><div>
<div class="step-title"><span class="num">3</span><h3>Try {e(NAME)}</h3></div>
<p>{e(NAME)} starts from the stick. Connect to Wi-Fi, open a few apps, and check that your screen, sound and touchpad work. Nothing on your computer changes until you install.</p>
</div><figure><img src="/images/live-desktop.jpg" width="1200" height="675" alt="The {e(NAME)} live desktop, with Install {e(NAME)} first in the dock" loading="lazy"></figure></div>
<div class="step"><div>
<div class="step-title"><span class="num">4</span><h3>Install with Kulan Installer</h3></div>
<p>Click Install {e(NAME)}, the first icon in the dock. It’s one screen:</p>
<ol>
<li><strong>Where:</strong> next to Windows, using the space you made and keeping Windows and your files, or erase the whole disk.</li>
<li><strong>You:</strong> your name and a password. The password signs you in and unlocks the encrypted disk when the computer starts.</li>
<li><strong>Install.</strong> Language, keyboard and time zone are filled in for you. To change them, open More choices.</li>
</ol>
<p>Nothing on the computer changes until you click Install. Installing takes about ten minutes and doesn’t need the internet. When it’s done, the last screen shows an eight-digit code: write it down, restart, and remove the stick when the screen goes dark.</p>
<p>At that first restart, a blue screen asks once to enroll your computer’s own start-up key, so nobody can change how it starts without you knowing. Press any key, choose Enroll MOK, then Continue, then Yes, type the eight digits and choose Reboot. Then type your password to unlock the disk, and sign in. Missed the blue screen? {e(NAME)} starts as usual and asks again next time; the code is in Kulan Settings › Security.</p>
</div><figure><img src="/images/installer-disk.jpg" width="840" height="718" alt="Kulan Installer’s one screen, offering to install next to Windows" loading="lazy"></figure></div>
<p class="muted" style="font-size:15px">Want to choose partitions yourself? Open More choices in the installer, then Advanced install.</p>
</section>
<section class="band" id="mac">
<div class="wrap">
<div class="head"><h2>On a Mac with UTM</h2><p class="lead">On an Apple Silicon Mac, {e(NAME)} runs in a window using the free UTM app. Your Mac isn’t changed.</p></div>
<div class="grid">
<div class="card"><span class="small-num">1</span><p><a href="/download/">Download {e(NAME)} for ARM</a> and install UTM from mac.getutm.app or the App Store.</p></div>
<div class="card"><span class="small-num">2</span><p>In UTM choose Create a New Virtual Machine, then Virtualize, then Linux, and pick the .iso as the boot image.</p></div>
<div class="card"><span class="small-num">3</span><p>Give it 4 GB of memory or more and a 30 GB disk, then save and press Play.</p></div>
<div class="card"><span class="small-num">4</span><p>Click Install {e(NAME)} and erase the virtual disk. When it’s done, shut down, clear the CD/DVD drive in UTM’s settings, and start it again.</p></div>
</div>
</div>
</section>
<section class="wrap"><div class="outline" style="display:flex;flex-wrap:wrap;gap:20px;align-items:center;justify-content:space-between"><p>Stuck? Most problems have a quick answer.</p><a class="btn btn-ghost" href="/help/">Go to Help →</a></div></section>"""
    return page("/install/", "Install guide", f"Install {NAME} from a USB stick, next to Windows or on its own, "
                                               "or on a Mac with UTM.", body)


def whats_new(notes: list[dict]) -> str:
    parts = []
    for i, note in enumerate(notes):
        version = str(note.get("version", ""))
        items = [str(x) for x in note.get("items", [])]
        shown_version = str(note.get("label") or version)
        title = str(note.get("title") or f"{NAME} {shown_version}")
        heading = title if title.startswith(NAME) else f"{shown_version}: {title}"
        latest = ' <span class="tag teal">Latest</span>' if i == 0 else ""
        shown, rest = (items, []) if i == 0 else (items[:3], items[3:])
        more = (f'<details class="more"><summary>{len(rest)} more</summary><ul class="items">{note_items(rest)}</ul>'
                f"</details>") if rest else ""
        parts.append(f"""<article class="card release{' latest' if i == 0 else ''}" id="v{e(version)}">
<div class="row"><h2>{e(heading)}</h2>{latest}</div>
<ul class="items">{note_items(shown)}</ul>
{more}
</article>""")
    body = f"""
<section class="wrap narrow stack">
<div class="head" style="margin-bottom:12px"><h1>What’s new</h1><p class="lead">Every version of {e(NAME)}. Installed computers get each one through Software Update, without reinstalling.</p></div>
{''.join(parts)}
</section>"""
    return page("/whats-new/", "What’s new", f"Every version of {NAME} and what changed in it.", body)


def help_page() -> str:
    topics = [
        ("/install/", "Getting started", "Installing, your first sign-in and the Welcome tour."),
        ("#faq", "Wi-Fi and internet", "Connecting, work and school Wi-Fi, VPNs and hotspots."),
        ("#faq", "Updates and restore points", "Software Update, automatic updates and undoing an update."),
        ("#security", "Security and encryption", "Disk encryption, compliance mode and the firewall."),
        ("#faq", "Kulan Assistant", "Writing tools, dictation and Ask Kulan."),
        ("#faq", "Your files and backups", "Backing up, getting files back, moving from Windows."),
    ]
    cards = "".join(f'<a class="card topic" href="{h}"><span>{e(t)}</span><span>{e(d)}</span></a>'
                    for h, t, d in topics)
    faq = [
        ("Is Kulan OS really free?", "Yes. Download it, use it and share it at no cost, with no account and no ads."),
        ("Can I keep Windows?", "Yes. Trying Kulan OS from the USB stick changes nothing. To install alongside "
                                "Windows, first shrink the C: drive by at least 20 GB in Disk Management, then "
                                "choose Install next to Windows in Kulan Installer. You pick which one to start "
                                "each time you turn the computer on. The <a href=\"/install/\">install guide</a> "
                                "has every step."),
        ("How do I get updates?", "Open Kulan Settings › Software Update, or turn on Automatic Updates there. A "
                                  "restore point is taken first, so you can undo an update if anything goes wrong."),
        ("How do I connect to work or school Wi-Fi?", "Open Kulan Settings › Wi-Fi and network. It handles networks "
                                                      "that need a sign-in, like eduroam, as well as hidden "
                                                      "networks, VPNs and hotspots."),
        ("I forgot my disk encryption password", "It can’t be recovered, not even by us. That’s what keeps your "
                                                 "files safe. Without it, the disk has to be erased and Kulan OS "
                                                 "installed again."),
        ("Does Kulan Assistant send my text anywhere?", "No. It runs on your computer. Select text and press "
                                                        "Shift+Super+W for writing tools, or Shift+Super+D to dictate. "
                                                        "(Super is the Windows key, or ⌘ on a Mac.)"),
        ("How do I back up my files?", "Open Backups from the app grid and choose an external drive or network "
                                       "folder. It can back up automatically every day."),
        ("My computer won’t start from the USB stick", "Try another USB port, make the stick again with "
                                                       "Kulan USB Maker, and check the boot-menu key for your computer "
                                                       "in the <a href=\"/install/\">install guide</a>."),
    ]
    items = "".join(f"<details><summary>{e(q)}</summary><p>{a}</p></details>" for q, a in faq)
    body = f"""
<section class="wrap stack" style="padding-bottom:32px">
<h1>How can we help?</h1>
<label for="q" class="muted" style="font-size:15px">Search the common questions</label>
<input id="q" class="search" type="search" placeholder="Wi-Fi, updates, encryption…" autocomplete="off">
</section>
<section class="wrap" style="padding-top:0"><div class="grid">{cards}</div></section>
<section class="band" id="faq"><div class="wrap narrow"><div class="head"><h2>Common questions</h2></div><div class="faq">{items}</div><p class="muted" id="no-match" hidden style="margin-top:12px">No question matches that. Try other words, or email us.</p></div></section>
<section class="wrap"><div class="grid">
<div class="outline stack" id="security"><h3>Security</h3><p class="muted" style="font-size:15px">Every update is signed with Kulan’s key (<a href="/apt/kulan-archive-keyring.gpg">download the key</a>). Found a security problem? Tell us privately at <a href="mailto:{SECURITY}">{SECURITY}</a>.</p></div>
<div class="outline stack" id="privacy"><h3>Privacy</h3><p class="muted" style="font-size:15px">No ads, no tracking and no account. {e(NAME)} goes online for updates from os.kulangroup.com and for the things you use, like the weather widget and Firefox. Once a week it sends an anonymous count, which you can switch off. Kulan Assistant runs on your computer, so nothing you type in it leaves it. <a href="/privacy/">Read the privacy policy</a>.</p></div>
<div class="outline stack" id="contact"><h3>Still stuck?</h3><p class="muted" style="font-size:15px">Email <a href="mailto:{SUPPORT}">{SUPPORT}</a>. Tell us what happened and which {e(NAME)} version you have, shown in Kulan Settings › About.</p></div>
</div></section>"""
    script = """<script>
(function () {
  var q = document.getElementById("q"), none = document.getElementById("no-match");
  var items = Array.prototype.slice.call(document.querySelectorAll(".faq details"));
  q.addEventListener("input", function () {
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean), shown = 0;
    items.forEach(function (d) {
      var text = d.textContent.toLowerCase();
      var hit = words.every(function (w) { return text.indexOf(w) !== -1; });
      d.hidden = !hit; d.open = hit && words.length > 0; if (hit) { shown++; }
    });
    none.hidden = shown > 0;
    if (words.length) { document.getElementById("faq").scrollIntoView({ block: "start" }); }
  });
})();
</script>"""
    return page("/help/", "Help", f"Answers to common questions about {NAME}, and how to contact us.", body, script)


def privacy() -> str:
    sections = [
        ("The short version", f"""<p>{e(NAME)} doesn’t track you. There are no ads, no accounts and no advertising
IDs, and it never sends what you do on your computer, your files, or what you type. The only thing it sends about
itself is an anonymous weekly count, described below, which you can switch off.</p>"""),
        ("The anonymous weekly count", f"""<p>To know roughly how many computers run {e(NAME)}, and which versions
to keep supporting, each installed computer is counted once a week. It sends exactly four things:</p>
<ul><li>the {e(NAME)} version, like 2.6;</li><li>whether it’s a PC (amd64) or a Mac or other Arm computer
(arm64);</li><li>roughly how long it’s been installed: under a week, under a month, under six months, or
longer;</li><li>which week it’s counting (the same for every computer that week), so a count sent just before
midnight isn’t added to the next week as well.</li></ul>
<p>There’s no ID, name, account, serial number or hardware detail, and nothing about how you use the computer. The
server only adds one to that week’s total for that version, kind of computer and age, so no week can be linked to
another, or to a computer or a person. Computers started from the USB stick (the live session) aren’t counted.</p>
<p>The count is received by our server provider, Supabase. Like any web server, it sees the internet address a
request comes from while answering it; {e(NAME)}’s counting service doesn’t store addresses or the time of the
request, only the weekly totals.</p>
<p>It’s on unless you switch it off, in the Setup Assistant when you first start or at any time in
<strong>Kulan Settings › Privacy › Count this computer, anonymously</strong>, where <strong>What’s sent</strong> shows
the exact report. The code that sends it is <a href="https://github.com/{REPO}/blob/main/packages/kulan-update-center/root/usr/lib/kulan/countme">open
for anyone to read</a>, and so is <a href="https://github.com/{REPO}/blob/main/fleet/supabase/functions/countme/index.ts">the
code that receives it</a>.</p>"""),
        ("What else goes online", f"""<ul><li><strong>Updates</strong>: deb.debian.org, security.debian.org and
os.kulangroup.com, to check for and download updates; Flathub for app updates.</li>
<li><strong>The clock</strong>: a Debian time server.</li>
<li><strong>Encrypted DNS</strong>: Quad9, Mullvad or Cloudflare for Families, only if you turn it on.</li>
<li><strong>Organisations</strong>: if your computer is added to an organisation (Kulan Settings › About ›
Organisation), it reports its status to that organisation, and the organisation can see it. Its admins can also ask to see
your screen to help you, and nothing is shared unless you allow it. Leaving the organisation stops all of this.</li>
<li><strong>Apps</strong>: apps you use, like Firefox or the weather, connect to their own services under their
own policies.</li></ul>
<p>Kulan Assistant, Dictation and Live Text run entirely on your computer. Nothing you say, type or show
them leaves it.</p>"""),
        ("This website and downloads", f"""<p>This website has no cookies, ads or tracking scripts. It’s hosted on
GitHub Pages, and the downloads come from GitHub, which counts how many times each file is downloaded; GitHub’s
own <a href="https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement">privacy
statement</a> covers that.</p>"""),
        ("Questions", f"""<p>Email <a href="mailto:{SUPPORT}">{SUPPORT}</a>. If this policy changes, the change is
listed in <a href="/whats-new/">What’s new</a> with the release that brings it.</p>"""),
    ]
    parts = "".join(f'<section class="stack"><h2>{e(title)}</h2>{text}</section>' for title, text in sections)
    body = f"""<section class="wrap narrow stack"><h1>Privacy policy</h1><p class="lead">What {e(NAME)} sends, and
what it never does.</p><p class="muted">For {e(NAME)} {e(LABEL)} and later.</p>{parts}</section>"""
    return page("/privacy/", "Privacy policy", f"What {NAME} sends, and what it never does.", body)


def security() -> str:
    """How to report a security problem, and how Kulan OS is protected (with /.well-known/security.txt)."""
    sections = [
        ("Report a security problem", f"""<p>Found a way to get around {e(NAME)}’s protections, in the system,
Kulan Fleet, the installer, Kulan USB Maker or this website? Please tell us privately first, at
<a href="mailto:{SECURITY}">{SECURITY}</a>, before telling anyone else.</p>
<ul><li>Say what you found and how to repeat it, and which version you used (Kulan Settings › About).</li>
<li>We confirm we got it, keep you told as we fix it, and release the fix as an update. Tell us if you’d like
to be thanked by name in the release notes.</li>
<li>Please don’t look at, change or keep other people’s data, or test against computers or organisations that
aren’t yours.</li></ul>
<p>Problems in Debian’s own packages go to the <a href="https://www.debian.org/security/">Debian security
team</a>; {e(NAME)} gets their fixes the same day through its updates.</p>"""),
        (f"How {NAME} is protected", f"""<ul>
<li><strong>Signed updates</strong>: every {e(NAME)} update and release is signed with Kulan’s key
(<a href="/apt/kulan-archive-keyring.gpg">download it</a>), and installed computers only take system updates signed by it
or by Debian.</li>
<li><strong>Security updates</strong> from Debian install by themselves, with a restore point first.</li>
<li><strong>Disk encryption</strong> is on when you install, and Secure Boot is supported.</li>
<li><strong>A firewall</strong> blocks connections from outside; sharing and remote desktop are let in from your
local network only, and only when you turn them on.</li>
<li><strong>Kulan Fleet</strong> (for organisations) can only do what’s listed in Kulan Settings › About ›
Organisation; remote help needs the person at the computer to agree, every time.</li>
<li><strong>Kulan Assistant</strong> runs on your computer, and nothing you type in it leaves it.</li>
<li>The <a href="https://github.com/{REPO}">source code</a> is open for anyone to check.</li></ul>"""),
    ]
    parts = "".join(f'<section class="stack"><h2>{e(title)}</h2>{text}</section>' for title, text in sections)
    body = f"""<section class="wrap narrow stack"><h1>Security</h1><p class="lead">How to report a security
problem, and how {e(NAME)} keeps your computer safe.</p>{parts}</section>"""
    return page("/security/", "Security", f"How to report a security problem in {NAME}.", body)


def security_txt(today: datetime.date) -> str:
    """RFC 9116. Expires a year after the website was built (every release rebuilds it)."""
    expires = (today + datetime.timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")
    return (f"Contact: mailto:{SECURITY}\n"
            f"Expires: {expires}\n"
            "Preferred-Languages: en\n"
            "Canonical: https://os.kulangroup.com/.well-known/security.txt\n"
            "Policy: https://os.kulangroup.com/security/\n")


def not_found() -> str:
    body = """<section class="wrap stack"><h1>Page not found</h1><p class="lead">That page isn’t here. Try the <a href="/">home page</a> or <a href="/help/">Help</a>.</p></section>"""
    return page("/404", "Page not found", "Page not found.", body)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 64
    out = Path(argv[1])
    notes = [n for n in json.loads(NOTES.read_text()) if isinstance(n, dict) and n.get("version")]
    if not notes or str(notes[0]["version"]) != VERSION:
        print(f"error: the newest release notes ({notes[0]['version'] if notes else 'none'}) "
              f"aren't for {VERSION}", file=sys.stderr)
        return 1
    pages = {"index.html": home(notes), "download/index.html": download(), "install/index.html": install(),
             "whats-new/index.html": whats_new(notes), "help/index.html": help_page(), "privacy/index.html": privacy(), "security/index.html": security(),
             ".well-known/security.txt": security_txt(datetime.date.today()), "404.html": not_found()}
    for rel, text in pages.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    shutil.copy2(WEB / "style.css", out / "style.css")
    for folder in ("fonts", "images"):
        shutil.copytree(WEB / folder, out / folder, dirs_exist_ok=True)
    # The Kulan Fleet dashboard (for admins, signed in; 2.9a): os.kulangroup.com/fleet/,
    # so remote help works from any browser, phones too. Not for search engines.
    shutil.copytree(ROOT / "fleet" / "dashboard", out / "fleet", dirs_exist_ok=True)
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /apt/\nDisallow: /fleet/\n")
    print(f"Website: {len(pages)} pages for {NAME} {LABEL} in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
