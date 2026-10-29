"""Kulan Fleet 2.8b on this computer: USB storage and apps, as the
organisation's policy says (kulan-fleet report runs this as root).

USB ("usb" in the policy):
  "block"     USB storage (sticks, external drives, card readers) doesn't
              work; keyboards, mice, phones, chargers and the rest do.
  "approved"  only the drives the organisation approved (by maker, model
              and serial number) work.
A udev rule turns off the storage part of any other drive as it's plugged
in; drives already plugged in are re-checked at once. The drives seen are
noted (maker, model, serial and name, nothing about what's on them) and
sent with the next report, so an admin can approve one.

Apps ("required_apps", "blocked_apps": [{source: debian|flatpak, id}]):
  required    installed from Kulan's and Debian's sources, or Flathub;
  blocked     removed, and kept off (an apt pin, a Flatpak mask).
A Debian package is never removed if that would take other packages with
it, or if it's part of Kulan OS: it's reported instead. Each app's state
goes in the report ("app:install:debian:vlc": "met" or why not).

Leaving the organisation takes all of it away (the apps stay as they are).
Commands go through `run` (kulan-fleet's), so the tests can watch them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

RULES = Path("/etc/udev/rules.d/60-kulan-fleet-usb.rules")
SEEN = Path("/var/lib/kulan/fleet-usb-seen.json")
SYSFS = Path("/sys/bus/usb/devices")
PINS = Path("/etc/apt/preferences.d/60-kulan-fleet-blocked")
APPS_STATE = Path("/var/lib/kulan/fleet-apps.json")  # Flatpak masks Fleet added, to take off later
SEEN_HELPER = "/usr/lib/kulan/fleet-usb-seen"
HEX4 = re.compile(r"^[0-9a-f]{4}$")
SERIAL = re.compile(r"^[A-Za-z0-9._-]{4,64}$")
# 2-63 characters, never ending in "-" (apt-get install would remove the package instead).
DEBIAN = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,61}[a-z0-9+.]$")
FLATPAK = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
# Never removed here, whatever a policy says (the server refuses them too).
PROTECTED = re.compile(r"^(kulan|linux-|systemd|gnome-shell|gnome-session|gdm3|mutter|apt|dpkg|libc6|bash|coreutils|"
                       r"sudo|login|passwd|network-manager|flatpak|gnome-software|packagekit|firmware-|grub|shim|"
                       r"plymouth|xdg-|dbus|polkitd|udev|init)")
MAX_SEEN = 20
APT_ENV = ["env", "DEBIAN_FRONTEND=noninteractive"]
APT_OPTS = ["-o", "DPkg::Lock::Timeout=300", "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".kulan-new")
    tmp.unlink(missing_ok=True)
    # Made with its final mode (never readable by others for a moment), and never through a link.
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode), "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)  # the umask may have taken bits away
    os.replace(tmp, path)


# --- USB ------------------------------------------------------------------------------------
def clean_drives(raw) -> list[dict]:
    out = []
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict):
            continue
        v, p, s = d.get("vendor_id"), d.get("product_id"), d.get("serial")
        if isinstance(v, str) and HEX4.match(v) and isinstance(p, str) and HEX4.match(p) \
                and isinstance(s, str) and SERIAL.match(s):
            out.append({"vendor_id": v, "product_id": p, "serial": s})
    return out[:500]


def usb_rules(mode: str | None, drives: list[dict]) -> str:
    """The udev rule for a USB policy ("" for none). Only USB mass-storage
    interfaces (class 08) are touched; approved drives skip the rule."""
    if mode not in ("block", "approved"):
        return ""
    lines = ["# Kulan Fleet: USB storage as the organisation's policy says. Written by kulan-fleet;",
             "# it goes when the computer leaves the organisation.",
             'ACTION!="add", GOTO="kulan_fleet_usb_end"',
             'SUBSYSTEM!="usb", GOTO="kulan_fleet_usb_end"',
             'ENV{DEVTYPE}!="usb_interface", GOTO="kulan_fleet_usb_end"',
             'ATTR{bInterfaceClass}!="08", GOTO="kulan_fleet_usb_end"']
    if mode == "approved":
        for d in drives:
            lines.append(f'ATTRS{{idVendor}}=="{d["vendor_id"]}", ATTRS{{idProduct}}=="{d["product_id"]}", '
                         f'ATTRS{{serial}}=="{d["serial"]}", GOTO="kulan_fleet_usb_end"')
    lines += [f'RUN+="{SEEN_HELPER}"', 'ATTR{authorized}="0"', 'LABEL="kulan_fleet_usb_end"', ""]
    return "\n".join(lines)


def _attr(path: Path, name: str) -> str:
    try:
        return (path / name).read_text().strip()
    except OSError:
        return ""


def storage_interfaces(sysfs: Path = SYSFS) -> list[tuple[Path, dict]]:
    """USB mass-storage interfaces plugged in now, with their drive's details."""
    found = []
    try:
        entries = sorted(sysfs.iterdir())
    except OSError:
        return found
    for iface in entries:
        if ":" not in iface.name or _attr(iface, "bInterfaceClass") != "08":
            continue
        dev = iface.resolve().parent
        drive = {"vendor_id": _attr(dev, "idVendor").lower(), "product_id": _attr(dev, "idProduct").lower(),
                 "serial": _attr(dev, "serial"),
                 "name": " ".join(x for x in (_attr(dev, "manufacturer"), _attr(dev, "product")) if x)[:80]}
        found.append((iface, drive))
    return found


def allowed(drive: dict, mode: str | None, drives: list[dict]) -> bool:
    if mode == "block":
        return False
    if mode == "approved":
        return any(drive["vendor_id"] == d["vendor_id"] and drive["product_id"] == d["product_id"]
                   and drive["serial"] == d["serial"] for d in drives)
    return True


def note_seen(drive: dict, seen_path: Path = SEEN) -> None:
    if not (HEX4.match(drive.get("vendor_id", "")) and HEX4.match(drive.get("product_id", ""))
            and SERIAL.match(drive.get("serial", ""))):
        return
    seen = [d for d in _read_json(seen_path, []) if isinstance(d, dict)]
    seen = [d for d in seen if (d.get("vendor_id"), d.get("product_id"), d.get("serial")) !=
            (drive["vendor_id"], drive["product_id"], drive["serial"])]
    seen.insert(0, {k: drive.get(k, "") for k in ("vendor_id", "product_id", "serial", "name")})
    _write(seen_path, json.dumps(seen[:MAX_SEEN]), 0o600)


def usb_seen(seen_path: Path = SEEN, sysfs: Path = SYSFS) -> list[dict]:
    """Drives seen lately (noted by the udev rule) and plugged in now."""
    for _iface, drive in storage_interfaces(sysfs):
        note_seen(drive, seen_path)
    return [d for d in _read_json(seen_path, []) if isinstance(d, dict)][:MAX_SEEN]


def apply_usb(run, mode: str | None, drives: list[dict], rules: Path = RULES, sysfs: Path = SYSFS) -> str | None:
    """Write (or remove) the rule, then make the drives plugged in now
    follow it. Returns the policy status ("met" / why not), None if no
    USB policy."""
    text = usb_rules(mode, drives)
    try:
        current = rules.read_text()
    except OSError:
        current = ""
    if not text and not current:
        return None  # no USB policy, now or before: drives are left to whatever else decides
    if text != current:
        if text:
            _write(rules, text)
        else:
            rules.unlink(missing_ok=True)
        run(["udevadm", "control", "--reload"])
    problems = []
    for iface, drive in storage_interfaces(sysfs):
        want = "1" if allowed(drive, mode, drives) else "0"
        if _attr(iface, "authorized") != want:
            try:
                (iface / "authorized").write_text(want)
            except OSError as e:
                problems.append(f"{drive['name'] or iface.name}: {e.strerror or e}")
    if not mode:
        return None
    return "met" if not problems and rules.exists() else "not met: " + ("; ".join(problems) or "rule not written")[:200]


# --- apps -----------------------------------------------------------------------------------
def clean_apps(raw) -> list[dict]:
    out = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        source, app_id = a.get("source"), a.get("id")
        if source == "debian" and isinstance(app_id, str) and DEBIAN.match(app_id):
            out.append({"source": source, "id": app_id})
        elif source == "flatpak" and isinstance(app_id, str) and len(app_id) <= 255 and FLATPAK.match(app_id):
            out.append({"source": source, "id": app_id})
    return out[:200]


def deb_installed(run, pkg: str) -> bool:
    r = run(["dpkg-query", "-W", "-f", "${db:Status-Abbrev}", pkg])
    return r.returncode == 0 and r.stdout.startswith("ii")


def flatpak_installed(run, app_id: str) -> bool:
    return run(["flatpak", "info", "--system", app_id]).returncode == 0


def _last(r) -> str:
    return ((r.stderr or r.stdout or "").strip().splitlines() or ["error"])[-1][:160]


def install(run, app: dict) -> tuple[bool, str]:
    if app["source"] == "debian":
        if deb_installed(run, app["id"]):
            return True, "already installed"
        r = run([*APT_ENV, "apt-get", "install", "-y", "-q", "--no-install-recommends", *APT_OPTS, app["id"]], timeout=1800)
        if r.returncode != 0:
            run([*APT_ENV, "apt-get", "update", "-q"], timeout=600)  # an old list is the usual reason
            r = run([*APT_ENV, "apt-get", "install", "-y", "-q", "--no-install-recommends", *APT_OPTS, app["id"]], timeout=1800)
        return (True, "installed") if r.returncode == 0 else (False, f"couldn't install: {_last(r)}")
    if flatpak_installed(run, app["id"]):
        return True, "already installed"
    r = run(["flatpak", "install", "--system", "-y", "--noninteractive", "flathub", app["id"]], timeout=1800)
    return (True, "installed") if r.returncode == 0 else (False, f"couldn't install: {_last(r)}")


def remove(run, app: dict) -> tuple[bool, str]:
    if app["source"] == "debian":
        pkg = app["id"]
        if PROTECTED.match(pkg):
            return False, "kept: it's part of Kulan OS"
        if not deb_installed(run, pkg):
            return True, "not installed"
        sim = run(["apt-get", "-s", "remove", pkg])
        removing = [l.split()[1] for l in sim.stdout.splitlines() if l.startswith("Remv ")]
        if sim.returncode != 0 or removing != [pkg]:
            others = [p for p in removing if p != pkg]
            return False, (f"kept: removing it would also remove {len(others)} other package(s) ({', '.join(others[:3])})"
                           if others else f"kept: {_last(sim)}")
        r = run([*APT_ENV, "apt-get", "remove", "-y", "-q", *APT_OPTS, pkg], timeout=900)
        return (True, "removed") if r.returncode == 0 else (False, f"couldn't remove: {_last(r)}")
    if not flatpak_installed(run, app["id"]):
        return True, "not installed"
    r = run(["flatpak", "uninstall", "--system", "-y", "--noninteractive", app["id"]], timeout=900)
    return (True, "removed") if r.returncode == 0 else (False, f"couldn't remove: {_last(r)}")


def pins_text(blocked: list[dict]) -> str:
    debs = [a["id"] for a in blocked if a["source"] == "debian" and not PROTECTED.match(a["id"])]
    if not debs:
        return ""
    return ("# Kulan Fleet: apps the organisation's policy blocks. Written by kulan-fleet;\n"
            "# it goes when the computer leaves the organisation.\n"
            f"Package: {' '.join(debs)}\nPin: release *\nPin-Priority: -1\n")


def apply_apps(run, required: list[dict], blocked: list[dict], pins: Path = PINS,
               state_path: Path = APPS_STATE) -> dict[str, str]:
    """Blocked apps first (removed, held off), then required ones installed."""
    status: dict[str, str] = {}
    text = pins_text(blocked)
    if text:
        _write(pins, text)
    else:
        pins.unlink(missing_ok=True)
    state = _read_json(state_path, {})
    masked = set(state.get("masked", [])) if isinstance(state, dict) else set()
    want_masked = {a["id"] for a in blocked if a["source"] == "flatpak"}
    for app_id in sorted(want_masked - masked):
        if run(["flatpak", "mask", "--system", app_id]).returncode == 0:
            masked.add(app_id)
    for app_id in sorted(masked - want_masked):
        run(["flatpak", "mask", "--system", "--remove", app_id])
        masked.discard(app_id)
    if masked or state_path.exists():
        _write(state_path, json.dumps({"masked": sorted(masked)}), 0o600)
    for app in blocked:
        ok, detail = remove(run, app)
        status[f"app:block:{app['source']}:{app['id']}"] = "met" if ok else detail
    for app in required:
        ok, detail = install(run, app)
        status[f"app:install:{app['source']}:{app['id']}"] = "met" if ok else detail
    return status


def remove_all(run, rules: Path = RULES, pins: Path = PINS, state_path: Path = APPS_STATE,
               seen_path: Path = SEEN, sysfs: Path = SYSFS) -> None:
    """Leaving the organisation: USB storage works again, the pins and masks
    go; apps stay as they are."""
    apply_usb(run, None, [], rules, sysfs)
    pins.unlink(missing_ok=True)
    state = _read_json(state_path, {})
    for app_id in state.get("masked", []) if isinstance(state, dict) else []:
        run(["flatpak", "mask", "--system", "--remove", app_id])
    state_path.unlink(missing_ok=True)
    seen_path.unlink(missing_ok=True)
