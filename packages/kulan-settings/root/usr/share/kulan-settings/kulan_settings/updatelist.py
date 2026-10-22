"""Turn the raw list of package updates into what a person reads in Software
Update: a new Kulan OS version on its own, then security updates and apps
by name, and everything else folded away. No GTK here, so it can be tested
on its own (see --self-test in kulan-settings)."""
from __future__ import annotations

import re

# Source package (or package-name prefix) -> the name people know it by.
FRIENDLY = [
    (("firefox-esr", "firefox"), "Firefox"),
    (("thunderbird",), "Thunderbird"),
    (("chromium",), "Chromium"),
    (("libreoffice",), "LibreOffice"),
    (("openssl", "libssl3t64", "libssl3"), "Encryption (OpenSSL)"),
    (("gnutls28", "libgnutls30t64", "libgnutls30"), "Encryption (GnuTLS)"),
    (("nss", "libnss3"), "Encryption for Firefox (NSS)"),
    (("linux-signed-amd64", "linux-signed-arm64", "linux", "linux-image"), "System core (Linux kernel)"),
    (("glibc", "libc6", "libc-bin"), "System library (C library)"),
    (("systemd", "libsystemd0", "libudev1"), "System services (systemd)"),
    (("sudo",), "Administrator access (sudo)"),
    (("openssh",), "Remote sign-in (OpenSSH)"),
    (("curl", "libcurl4t64", "libcurl3t64-gnutls"), "Downloads (curl)"),
    (("wget",), "Downloads (wget)"),
    (("tzdata",), "Time zones"),
    (("gtk4", "libgtk-4-1", "libgtk-4-common"), "App toolkit (GTK 4)"),
    (("gtk+3.0", "libgtk-3-0t64", "libgtk-3-common"), "App toolkit (GTK 3)"),
    (("libadwaita-1", "libadwaita-1-0"), "App toolkit (libadwaita)"),
    (("glib2.0", "libglib2.0-0t64", "libglib2.0-bin"), "System library (GLib)"),
    (("gnome-shell",), "Desktop (GNOME Shell)"),
    (("mutter", "libmutter"), "Windows and screens (Mutter)"),
    (("gdm3",), "Sign-in screen"),
    (("nautilus",), "Files"),
    (("networkmanager", "network-manager"), "Wi-Fi and network"),
    (("wpa",), "Wi-Fi security (wpa_supplicant)"),
    (("bluez",), "Bluetooth"),
    (("pipewire", "wireplumber"), "Sound (PipeWire)"),
    (("pulseaudio",), "Sound"),
    (("mesa",), "Graphics drivers (Mesa)"),
    (("nvidia-graphics-drivers",), "NVIDIA graphics drivers"),
    (("firmware-nonfree", "firmware-free", "linux-firmware"), "Device firmware"),
    (("intel-microcode", "amd64-microcode"), "Processor firmware"),
    (("gstreamer1.0", "gst-plugins", "libgstreamer1.0-0", "gstreamer1.0-plugins"), "Video and music playback"),
    (("ffmpeg",), "Video and music playback (FFmpeg)"),
    (("vlc",), "VLC"),
    (("gimp",), "GIMP"),
    (("webkit2gtk", "libwebkit2gtk-4.1-0", "libwebkitgtk-6.0-4", "libjavascriptcoregtk"), "Web pages in apps (WebKit)"),
    (("poppler", "libpoppler"), "PDF viewing"),
    (("cups", "libcups2t64"), "Printing"),
    (("libxml2", "libxml2-16"), "Document reading (libxml2)"),
    (("expat", "libexpat1"), "Document reading (Expat)"),
    (("libtiff", "libpng1.6", "libjpeg-turbo", "libwebp", "tiff"), "Picture formats"),
    (("python3", "python3.13", "libpython3.13"), "Python"),
    (("git",), "Git"),
    (("docker.io", "containerd"), "Docker"),
    (("flatpak",), "App installer (Flatpak)"),
    (("apt",), "Package manager (apt)"),
    (("dpkg",), "Package manager (dpkg)"),
    (("ca-certificates",), "Trusted website certificates"),
    (("cryptsetup",), "Disk encryption"),
    (("grub2", "shim", "shim-signed"), "Start-up (boot loader)"),
    (("ufw",), "Firewall"),
    (("timeshift",), "Restore points"),
]
VERSION = re.compile(r"^(\d+)\.(\d+)")


def friendly_name(update: dict) -> str:
    source = str(update.get("source") or update.get("name") or "")
    name = str(update.get("name") or "")
    for keys, label in FRIENDLY:
        if source in keys or name in keys or any(source.startswith(k + "-") or name.startswith(k + "-")
                                                  for k in keys):
            return label
    summary = str(update.get("summary") or "").strip()
    if summary:
        # "GNU C Library: Shared libraries" -> "GNU C Library"; keep it short.
        short = re.split(r"\s+[-:(]\s*|\s*[,;]\s+", summary, maxsplit=1)[0].strip()
        if 3 <= len(short) <= 40:
            return short[0].upper() + short[1:]
    return source or name or "Update"


def series(version: str) -> tuple[int, int] | None:
    match = VERSION.match(version or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def version_tuple(version: str) -> tuple[int, ...]:
    """"2.4.1" -> (2, 4, 1); anything else stops at the first non-number."""
    parts = []
    for piece in (version or "").split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def is_kulan(update: dict) -> bool:
    name = str(update.get("name") or "")
    return name.startswith("kulan-") and update.get("kind") not in ("app", "runtime")


def summarize(available: list[dict], current_version: str) -> dict:
    """{"release": "2.4" or None, "security": [{"title", "names"}], "apps": [update],
    "other": [{"title", "names"}], "kulan": [names], "count": things a person would count}."""
    current = series(current_version)
    release = None
    kulan: list[str] = []
    apps: list[dict] = []
    security: dict[str, list[str]] = {}
    other: dict[str, list[str]] = {}
    for update in available:
        if update.get("kind") in ("app", "runtime"):
            if update.get("kind") == "app":
                apps.append(update)
            else:
                other.setdefault("App components", []).append(str(update.get("name") or ""))
            continue
        if is_kulan(update):
            kulan.append(str(update["name"]))
            new = series(str(update.get("to") or ""))
            if new and current and new > current and (release is None or new > series(release)):
                release = f"{new[0]}.{new[1]}"
            continue
        target = security if update.get("security") else other
        target.setdefault(friendly_name(update), []).append(str(update.get("name") or ""))
    # A security fix for something also listed under "other" belongs with security.
    for title in list(other):
        if title in security:
            security[title] += other.pop(title)
    if kulan and not release:
        other = {"Kulan OS improvements": kulan, **other}
    sec = [{"title": t, "names": n} for t, n in sorted(security.items(), key=lambda kv: kv[0].lower())]
    oth = [{"title": t, "names": n} for t, n in sorted(other.items(), key=lambda kv: (kv[0] != "Kulan OS improvements", kv[0].lower()))]
    count = (1 if release else 0) + len(sec) + len(apps) + len(oth)
    return {"release": release, "kulan": kulan, "security": sec, "apps": apps, "other": oth, "count": count}


def known_name(name: str) -> str | None:
    """The friendly name of a package, or None when it's just its own name."""
    if name.startswith("kulan-"):
        return "Kulan OS improvements"
    label = friendly_name({"name": name})
    return None if label == name else label


def history_title(packages: list[str], kulan_series: str | None = None) -> str:
    """One line for an install in the history: what changed, by name."""
    if kulan_series:
        return f"Updated to Kulan OS {kulan_series}"
    names: list[str] = []
    unknown = 0
    for name in packages:
        label = known_name(name)
        if label is None:
            unknown += 1
        elif label not in names:
            names.append(label)
    if not names:
        return f"System updates ({len(packages)})" if packages else "Updates installed"
    if len(names) > 2:
        return f"{names[0]}, {names[1]} and {len(names) - 2 + (1 if unknown else 0)} more"
    if unknown:
        return f"{', '.join(names)} and other updates"
    return " and ".join(names)


def headline(item: str) -> tuple[str, str]:
    """Split a release-notes line "Kulan Installer: Kulan OS's own installer…"
    into a short headline and the rest; lines without one stay whole."""
    head, sep, rest = item.partition(": ")
    if sep and 2 <= len(head) <= 64 and rest:
        return head, rest[0].upper() + rest[1:]
    return item, ""


def self_test() -> None:
    sample = [
        {"name": "kulan-base", "from": "2.3.1", "to": "2.4.202610221930"},
        {"name": "kulan-settings", "from": "2.3.1", "to": "2.4.202610221930"},
        {"name": "libssl3t64", "source": "openssl", "from": "3.5.1", "to": "3.5.4", "security": True},
        {"name": "openssl", "source": "openssl", "from": "3.5.1", "to": "3.5.4", "security": True},
        {"name": "firefox-esr", "from": "140.4", "to": "140.5", "security": True},
        {"name": "tzdata", "from": "2025b", "to": "2025c"},
        {"name": "libfoo1", "source": "foo", "summary": "Foo widget library - runtime", "from": "1", "to": "2"},
        {"name": "LibreOffice", "kind": "app", "to": "25.8.2", "id": "org.libreoffice.LibreOffice"},
    ]
    s = summarize(sample, "2.3")
    assert s["release"] == "2.4", s
    assert [g["title"] for g in s["security"]] == ["Encryption (OpenSSL)", "Firefox"], s["security"]
    assert s["security"][0]["names"] == ["libssl3t64", "openssl"], s
    assert [g["title"] for g in s["other"]] == ["Foo widget library", "Time zones"], s["other"]
    assert s["count"] == 1 + 2 + 1 + 2, s["count"]
    same = summarize([{"name": "kulan-base", "from": "2.4.1", "to": "2.4.2"}], "2.4")
    assert same["release"] is None and same["other"][0]["title"] == "Kulan OS improvements", same
    assert history_title(["firefox-esr", "openssl", "libssl3t64", "tzdata"]) == \
        "Firefox, Encryption (OpenSSL) and 1 more", history_title(["firefox-esr", "openssl", "libssl3t64", "tzdata"])
    assert history_title(["firefox-esr", "libfoo9"]) == "Firefox and other updates", history_title(["firefox-esr", "libfoo9"])
    assert history_title(["libfoo9", "libbar2"]) == "System updates (2)"
    assert headline("Kulan Installer: four short steps") == ("Kulan Installer", "Four short steps")
    assert headline("Round red, yellow and green window buttons") == ("Round red, yellow and green window buttons", "")
    assert history_title([], "2.4") == "Updated to Kulan OS 2.4"
    assert version_tuple("2.4.1") > version_tuple("2.4") and version_tuple("2.5") > version_tuple("2.4.9")
    assert series("2.4.1") == series("2.4.202610231200") == (2, 4)
    point = summarize([{"name": "kulan-base", "from": "2.4.202610222011", "to": "2.4.202610231200"}], "2.4")
    assert point["release"] is None and point["kulan"] == ["kulan-base"], point
    assert friendly_name({"name": "linux-image-6.12.57-amd64", "source": "linux-signed-amd64"}) == \
        "System core (Linux kernel)"
