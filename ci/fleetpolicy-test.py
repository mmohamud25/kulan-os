#!/usr/bin/python3
"""Kulan Fleet 2.8b on the computer (fleetpolicy.py), every case, without
touching this machine: a made-up /sys for USB drives, and a pretend `run`
that answers as dpkg, apt-get and flatpak would.

  python3 -I -B ci/fleetpolicy-test.py [path/to/fleetpolicy.py]
Prints "fleetpolicy: every case passed", or ::error:: lines and exits 1.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "packages/kulan-base/root/usr/share/kulan-fleet/fleetpolicy.py"
spec = importlib.util.spec_from_file_location("fleetpolicy", SRC)
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
        print(f"::error::fleetpolicy: {name} {detail}")


class Box:
    """The machine as dpkg/apt/flatpak see it, and the commands run."""

    def __init__(self, debs=(), flatpaks=(), remove_takes=None, install_fails=()):
        self.debs, self.flatpaks = set(debs), set(flatpaks)
        self.remove_takes = remove_takes or {}
        self.install_fails = set(install_fails)
        self.masked, self.calls = set(), []

    def run(self, argv, timeout=60):
        self.calls.append(argv)
        a = [x for x in argv if x not in ("env", "DEBIAN_FRONTEND=noninteractive")]
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        bad = subprocess.CompletedProcess(argv, 100, "", "E: Unable to locate package")
        if a[0] == "dpkg-query":
            return subprocess.CompletedProcess(argv, 0, "ii ", "") if a[-1] in self.debs else \
                subprocess.CompletedProcess(argv, 1, "", "no packages found")
        if a[0] == "apt-get":
            pkg = a[-1]
            if "-s" in a:
                takes = [pkg] + self.remove_takes.get(pkg, [])
                return subprocess.CompletedProcess(argv, 0, "".join(f"Remv {p} [1.0]\n" for p in takes), "")
            if "install" in a:
                if pkg in self.install_fails:
                    return bad
                self.debs.add(pkg)
                return ok
            if "remove" in a:
                self.debs.discard(pkg)
                return ok
            return ok  # update
        if a[0] == "flatpak":
            if a[1] == "info":
                return ok if a[-1] in self.flatpaks else bad
            if a[1] == "install":
                if a[-1] in self.install_fails:
                    return bad
                self.flatpaks.add(a[-1])
                return ok
            if a[1] == "uninstall":
                self.flatpaks.discard(a[-1])
                return ok
            if a[1] == "mask":
                (self.masked.discard if "--remove" in a else self.masked.add)(a[-1])
                return ok
        if a[0] == "udevadm":
            return ok
        return bad


def make_drive(sysfs: Path, name: str, vendor="0781", product="5583", serial="4C530001", cls="08", authorized="1"):
    """A USB device with one interface, as /sys/bus/usb/devices shows them
    (links into /sys/devices)."""
    dev = sysfs.parents[2] / "devices" / name
    iface = dev / f"{name}:1.0"
    iface.mkdir(parents=True)
    for k, v in (("idVendor", vendor), ("idProduct", product), ("serial", serial),
                 ("manufacturer", "SanDisk"), ("product", "Ultra Fit")):
        (dev / k).write_text(v + "\n")
    (iface / "bInterfaceClass").write_text(cls + "\n")
    (iface / "authorized").write_text(authorized + "\n")
    (sysfs / name).symlink_to(dev)
    (sysfs / iface.name).symlink_to(iface)
    return iface


with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    sysfs = t / "sys/bus/usb/devices"
    sysfs.mkdir(parents=True)
    rules, seen, pins, state = t / "60-kulan-fleet-usb.rules", t / "seen.json", t / "pins", t / "apps.json"
    stick = make_drive(sysfs, "1-1")
    other = make_drive(sysfs, "1-2", vendor="0951", product="1666", serial="ABCD1234")
    keyboard = make_drive(sysfs, "1-3", vendor="046d", product="c31c", serial="KB0001", cls="03")
    approved = [{"vendor_id": "0781", "product_id": "5583", "serial": "4C530001"}]

    # Cleaning what the server sent.
    check("drives cleaned", fp.clean_drives(approved + [{"vendor_id": "XYZ", "product_id": "1", "serial": "a"},
                                                         {"vendor_id": "0781", "product_id": "5583", "serial": 'x"; RUN+="/bin/sh'},
                                                         "junk"]) == approved)
    apps_in = [{"source": "debian", "id": "vlc"}, {"source": "flatpak", "id": "org.mozilla.firefox"},
               {"source": "debian", "id": "../../etc"}, {"source": "snap", "id": "x"}, {"source": "flatpak", "id": "nodots"}]
    check("apps cleaned", fp.clean_apps(apps_in) == apps_in[:2], str(fp.clean_apps(apps_in)))
    # A trailing "-" asks apt-get install to remove the package: never taken.
    check("a name ending in - is refused", fp.clean_apps([{"source": "debian", "id": "gdm3-"}, {"source": "debian", "id": "kulan-base-"},
                                                          {"source": "debian", "id": "g++"}]) == [{"source": "debian", "id": "g++"}],
          str(fp.clean_apps([{"source": "debian", "id": "gdm3-"}])))

    # The rule.
    check("no rule without a policy", fp.usb_rules(None, approved) == "" and fp.usb_rules("maybe", []) == "")
    block_rule = fp.usb_rules("block", approved)
    check("block: no exceptions, storage only", 'ATTR{bInterfaceClass}!="08"' in block_rule and "idVendor" not in block_rule
          and 'ATTR{authorized}="0"' in block_rule and block_rule.rstrip().endswith('LABEL="kulan_fleet_usb_end"'))
    appr_rule = fp.usb_rules("approved", approved)
    check("approved: an exception per drive", appr_rule.count('ATTRS{serial}=="4C530001"') == 1
          and appr_rule.index("4C530001") < appr_rule.index('ATTR{authorized}="0"'))

    # No policy, never had one: nothing touched, even a drive something else turned off.
    other_off = (other / "authorized").read_text()
    (other / "authorized").write_text("0\n")
    box = Box()
    check("no policy leaves drives alone", fp.apply_usb(box.run, None, [], rules, sysfs) is None
          and (other / "authorized").read_text().strip() == "0" and not rules.exists() and not box.calls)
    (other / "authorized").write_text(other_off)

    # Block.
    box = Box()
    status = fp.apply_usb(box.run, "block", [], rules, sysfs)
    check("block met", status == "met", str(status))
    check("block: sticks off, keyboard on", (stick / "authorized").read_text().strip() == "0"
          and (other / "authorized").read_text().strip() == "0" and (keyboard / "authorized").read_text().strip() == "1")
    check("block: rule written, udev reloaded", rules.read_text() == block_rule and ["udevadm", "control", "--reload"] in box.calls)
    box = Box()
    fp.apply_usb(box.run, "block", [], rules, sysfs)
    check("same policy again: udev not reloaded", not box.calls)

    # Approved only: the approved stick works again, the other doesn't.
    status = fp.apply_usb(Box().run, "approved", approved, rules, sysfs)
    check("approved met", status == "met" and (stick / "authorized").read_text().strip() == "1"
          and (other / "authorized").read_text().strip() == "0", status)

    # Drives seen: noted by the udev helper and the scan, newest first, at most 20, no duplicates.
    found = fp.usb_seen(seen, sysfs)
    check("drives seen", {(d["vendor_id"], d["serial"]) for d in found} == {("0781", "4C530001"), ("0951", "ABCD1234")}
          and all(d["name"] == "SanDisk Ultra Fit" for d in found), str(found))
    for i in range(30):
        fp.note_seen({"vendor_id": "1234", "product_id": "5678", "serial": f"SER{i:04d}", "name": "x"}, seen)
    fp.note_seen({"vendor_id": "1234", "product_id": "5678", "serial": "SER0029", "name": "x"}, seen)
    listed = json.loads(seen.read_text())
    check("seen: newest first, 20 at most, once each", len(listed) == 20 and listed[0]["serial"] == "SER0029"
          and len({d["serial"] for d in listed}) == 20)
    fp.note_seen({"vendor_id": "1234", "product_id": "5678", "serial": "bad serial!", "name": "x"}, seen)
    check("seen: odd serials not noted", "bad serial!" not in seen.read_text())
    check("seen file root only", oct(seen.stat().st_mode & 0o777) == "0o600")

    # Apps: blocked removed and pinned; required installed; Kulan OS itself never removed.
    box = Box(debs={"vlc", "steam", "kulan-base"}, flatpaks={"com.valvesoftware.Steam"},
              remove_takes={"steam": ["steam-libs", "steam-devices"]})
    blocked = [{"source": "debian", "id": "vlc"}, {"source": "debian", "id": "steam"},
               {"source": "debian", "id": "kulan-base"}, {"source": "flatpak", "id": "com.valvesoftware.Steam"}]
    required = [{"source": "debian", "id": "gimp"}, {"source": "flatpak", "id": "org.mozilla.Thunderbird"},
                {"source": "debian", "id": "nosuchapp"}]
    box.install_fails = {"nosuchapp"}
    status = fp.apply_apps(box.run, required, blocked, pins, state)
    check("blocked removed", status["app:block:debian:vlc"] == "met" and "vlc" not in box.debs)
    check("not removed when it takes others", status["app:block:debian:steam"].startswith("kept: removing it would also remove 2")
          and "steam" in box.debs, status["app:block:debian:steam"])
    check("Kulan OS never removed", status["app:block:debian:kulan-base"] == "kept: it's part of Kulan OS" and "kulan-base" in box.debs)
    check("blocked Flatpak removed and masked", status["app:block:flatpak:com.valvesoftware.Steam"] == "met"
          and box.masked == {"com.valvesoftware.Steam"} and json.loads(state.read_text()) == {"masked": ["com.valvesoftware.Steam"]})
    check("pinned off", "Package: vlc steam\n" in pins.read_text() and "Pin-Priority: -1" in pins.read_text()
          and "kulan-base" not in pins.read_text())
    check("required installed", status["app:install:debian:gimp"] == "met" and "gimp" in box.debs
          and status["app:install:flatpak:org.mozilla.Thunderbird"] == "met")
    check("install failure says why, after refreshing lists", status["app:install:debian:nosuchapp"].startswith("couldn't install")
          and any("update" in c for c in box.calls))
    check("apt non-interactive, waits for the lock", any(c[:2] == ["env", "DEBIAN_FRONTEND=noninteractive"] and "DPkg::Lock::Timeout=300" in c
                                                         for c in box.calls))
    calls = len(box.calls)
    fp.apply_apps(box.run, required[:2], blocked, pins, state)
    check("already done: nothing installed or removed again",
          not any(("install" in c or "remove" in c or "uninstall" in c) and "-s" not in c for c in box.calls[calls:]))

    # A block taken away: its mask goes, the pin goes.
    fp.apply_apps(box.run, [], [], pins, state)
    check("unblocked: mask and pin taken away", not box.masked and not pins.exists())

    # install-app once.
    box = Box()
    check("install once", fp.install(box.run, {"source": "flatpak", "id": "org.gimp.GIMP"}) == (True, "installed"))
    check("already installed", fp.install(box.run, {"source": "flatpak", "id": "org.gimp.GIMP"}) == (True, "already installed"))

    # Leaving: USB works again, pins and masks go, apps stay.
    box = Box(debs={"gimp"})
    fp.apply_usb(box.run, "block", [], rules, sysfs)
    fp.apply_apps(box.run, [], [{"source": "debian", "id": "vlc"}, {"source": "flatpak", "id": "com.example.Game"}], pins, state)
    fp.remove_all(box.run, rules, pins, state, seen, sysfs)
    check("leave: USB storage back", not rules.exists() and (stick / "authorized").read_text().strip() == "1"
          and (other / "authorized").read_text().strip() == "1")
    check("leave: pins, masks and the seen list gone", not pins.exists() and not state.exists() and not seen.exists()
          and not box.masked)
    check("leave: apps stay", "gimp" in box.debs)

if failures:
    sys.exit(1)
print("fleetpolicy: every case passed")
