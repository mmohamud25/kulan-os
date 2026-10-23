#!/bin/bash
# Take the website's screenshots from a built Kulan OS ISO: boot the live
# session in a virtual machine at 1920x1080, then capture the desktop, Kulan
# Installer (its demo mode: welcome, where to install, summary) and Kulan
# Settings. Apps are opened through the QEMU guest agent
# as the live session's user.
#
# Usage: ci/screenshots.sh <iso> [output-dir]
# Needs: qemu-system-x86, xorriso, python3 with Pillow. Best with KVM.
# Never fails the build: missing shots are reported as warnings.
set -uo pipefail

ISO="${1:?usage: $0 <iso> [output-dir]}"
OUT="${2:-screenshots}"
WAIT_S="${KULAN_BOOT_WAIT:-600}"
mkdir -p "${OUT}"
WORK="$(mktemp -d)"
QEMU_PID=""
# shellcheck disable=SC2317  # called by the EXIT trap
cleanup() {
	[[ -n "${QEMU_PID}" ]] && kill "${QEMU_PID}" 2>/dev/null
	rm -rf "${WORK}"
}
trap cleanup EXIT

xorriso -osirrox on -indev "${ISO}" -extract /boot/grub/grub.cfg "${WORK}/grub.cfg" >/dev/null 2>&1
LINUX_LINE="$(grep -m1 -E '^[[:space:]]*linux[[:space:]]+/live/vmlinuz' "${WORK}/grub.cfg")"
INITRD_LINE="$(grep -m1 -E '^[[:space:]]*initrd[[:space:]]+/live/initrd' "${WORK}/grub.cfg")"
KERNEL_PATH="$(awk '{print $2}' <<<"${LINUX_LINE}")"
INITRD_PATH="$(awk '{print $2}' <<<"${INITRD_LINE}")"
BOOT_ARGS="$(awk '{ $1 = ""; $2 = ""; sub(/^[[:space:]]+/, ""); print }' <<<"${LINUX_LINE}")"
# shellcheck disable=SC2016  # a literal ${ to match
BOOT_ARGS="$(tr ' ' '\n' <<<"${BOOT_ARGS}" | grep -v '\${' | paste -sd ' ' -)"
[[ -n "${KERNEL_PATH}" && -n "${INITRD_PATH}" ]] || { echo "::warning title=Screenshots::no live entry in the ISO's boot menu"; exit 0; }
xorriso -osirrox on -indev "${ISO}" -extract "${KERNEL_PATH}" "${WORK}/vmlinuz" \
	-extract "${INITRD_PATH}" "${WORK}/initrd" >/dev/null 2>&1
BOOT_ARGS="${BOOT_ARGS} console=tty0 console=ttyS0,115200 kulan.boot-report"

ACCEL=(-accel tcg)
if [[ -w /dev/kvm ]]; then
	ACCEL=(-enable-kvm -cpu host)
else
	WAIT_S=$((WAIT_S * 3))
fi

START=$(date +%s)
qemu-system-x86_64 "${ACCEL[@]}" -m 4096 -smp 2 \
	-kernel "${WORK}/vmlinuz" -initrd "${WORK}/initrd" -append "${BOOT_ARGS}" \
	-drive "file=${ISO},media=cdrom,readonly=on" \
	-device virtio-vga,xres=1920,yres=1080 -display none \
	-serial "file:${WORK}/serial.log" \
	-qmp "unix:${WORK}/qmp.sock,server=on,wait=off" \
	-chardev "socket,path=${WORK}/qga.sock,server=on,wait=off,id=qga0" \
	-device virtio-serial -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0 \
	-netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
	-device virtio-rng-pci -no-reboot &
QEMU_PID=$!

while (( $(date +%s) - START < WAIT_S )); do
	grep -q "KULAN-BOOT-REPORT-END" "${WORK}/serial.log" 2>/dev/null && break
	kill -0 "${QEMU_PID}" 2>/dev/null || break
	sleep 5
done
if ! grep -q "KULAN-BOOT-REPORT-END" "${WORK}/serial.log" 2>/dev/null; then
	echo "::warning title=Screenshots::the live desktop didn't come up; no screenshots"
	exit 0
fi

python3 - "${WORK}/qmp.sock" "${WORK}/qga.sock" "${OUT}" <<'PY'
import base64, json, os, socket, sys, time
from PIL import Image

qmp_path, qga_path, out = sys.argv[1:4]

def connect(path):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(30)
    s.connect(path)
    return s.makefile("rw")

qmp = connect(qmp_path)
json.loads(qmp.readline())

def qmp_call(cmd, **args):
    qmp.write(json.dumps({"execute": cmd, "arguments": args}) + "\n"); qmp.flush()
    while True:
        reply = json.loads(qmp.readline())
        if "return" in reply or "error" in reply:
            return reply

qmp_call("qmp_capabilities")

qga = None
def qga_call(cmd, **args):
    qga.write(json.dumps({"execute": cmd, "arguments": args}) + "\n"); qga.flush()
    while True:
        line = qga.readline()
        if not line:
            raise RuntimeError("guest agent closed")
        reply = json.loads(line)
        if "return" in reply or "error" in reply:
            return reply

# The agent starts with the live system; sync until it answers.
for _ in range(30):
    try:
        qga = connect(qga_path)
        token = int(time.time())
        if qga_call("guest-sync", id=token).get("return") == token:
            break
    except Exception:
        time.sleep(4)
else:
    print("::warning title=Screenshots::the guest agent didn't answer; desktop only")
    qga = None

def run(script, wait=True):
    """Run a shell script in the guest as root; return its output."""
    if qga is None:
        return ""
    r = qga_call("guest-exec", path="/bin/sh", arg=["-c", script], **{"capture-output": True})
    pid = r.get("return", {}).get("pid")
    if pid is None or not wait:
        return ""
    for _ in range(60):
        s = qga_call("guest-exec-status", pid=pid).get("return", {})
        if s.get("exited"):
            return base64.b64decode(s.get("out-data", "")).decode(errors="replace")
        time.sleep(1)
    return ""

# Start an app in the live user's session, detached.
SESSION = ('U=$(id -nu 1000); runuser -u "$U" -- env XDG_RUNTIME_DIR=/run/user/1000 '
           'WAYLAND_DISPLAY=wayland-0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus '
           'setsid %s >/dev/null 2>&1 </dev/null &')

def shot(name):
    ppm = os.path.join(out, name + ".ppm")
    qmp_call("screendump", filename=os.path.abspath(ppm))
    time.sleep(1)
    if os.path.getsize(ppm) if os.path.exists(ppm) else 0:
        Image.open(ppm).save(os.path.join(out, name + ".png"), optimize=True)
        os.remove(ppm)
        print(f"Saved {name}.png")
    else:
        print(f"::warning title=Screenshots::{name} wasn't captured")

def close_apps():
    # Close only the apps this script and the live session opened. The [/]
    # keeps each pattern from matching the shell running these very commands
    # (its own command line contains them), which pkill would kill first.
    run("pkill -x kulan-welcome; pkill -f '[/]usr/bin/kulan-installer'; pkill -f '[/]usr/bin/kulan-settings'; sleep 2")

time.sleep(25)  # let the welcome app, widgets and weather settle
close_apps()
time.sleep(3)
shot("desktop")

# The installer in demo mode: a sample Windows disk, never installs.
for name, cmd in (("installer", "/usr/bin/kulan-installer --demo"),
                  ("installer-disk", "/usr/bin/kulan-installer --demo disk"),
                  ("installer-summary", "/usr/bin/kulan-installer --demo summary"),
                  ("settings", "/usr/bin/kulan-settings")):
    if qga is None:
        break
    run(SESSION % cmd, wait=False)
    time.sleep(12)
    shot(name)
    close_apps()
    time.sleep(3)

# Every Kulan Settings page as it really looks in the live session (with
# its network, sound, display and account services), for reviewing the
# insides of the app; saved as settings-<page>.png.
PAGES = ("wifi bluetooth printers sound focus notifications users parental-controls appearance desktop dock "
         "top-bar display battery storage login-items apps security privacy keyboard mouse accessibility "
         "assistant prayer-times date-time region updates add-ons about").split()
if qga is not None:
    for page in PAGES:
        run(SESSION % f"/usr/bin/kulan-settings {page}", wait=False)
        time.sleep(6)
        shot(f"settings-{page}")
        run("pkill -f '[/]usr/bin/kulan-settings'; sleep 1")
        time.sleep(1)

# Ask Kulan, opened the way the Activities search result opens it (the
# shortcut needs a real keyboard): empty, then with a question. The live
# system has no model yet, so the answer says how to get one.
ASK = ("gdbus call --session --dest org.gnome.Shell --object-path /org/kulan/Shell/Ask "
       "--method org.kulan.Shell.Ask.Open %s")
if qga is not None:
    run(SESSION % (ASK % "''"), wait=False)
    time.sleep(4)
    shot("ask-kulan")
    run(SESSION % (ASK % "'Why is my computer slow?'"), wait=False)
    time.sleep(8)
    shot("ask-kulan-answer")
    run(SESSION % ASK.replace("Open %s", "Close"), wait=False)
    time.sleep(2)

# --- Speed: how long each everyday app takes to show its window ----------------
# Opened twice: the first time (nothing in memory yet) and again. "Shown" is
# the first screen with a new window on it, compared with the screen before,
# ignoring the top bar (its clock changes).
from PIL import ImageChops

def grab():
    ppm = os.path.join(out, "_speed.ppm")
    qmp_call("screendump", filename=os.path.abspath(ppm))
    img = Image.open(ppm).convert("L")
    w, h = img.size
    return img.crop((0, int(h * 0.06), w, int(h * 0.92))).resize((320, 172))

def changed(before, now) -> bool:
    diff = ImageChops.difference(before, now).point(lambda v: 255 if v > 40 else 0)
    return diff.histogram()[255] > 320 * 172 * 0.04

def change(before, now) -> float:
    diff = ImageChops.difference(before, now).point(lambda v: 255 if v > 40 else 0)
    return diff.histogram()[255] / (320 * 172)

def time_open(cmd: str, label: str, limit: float = 40.0):
    before = grab()
    start = time.monotonic()
    run(SESSION % cmd, wait=False)
    most = 0.0
    while time.monotonic() - start < limit:
        now = grab()
        most = max(most, change(before, now))
        if most > 0.04:
            return round(time.monotonic() - start, 2)
        time.sleep(0.05)
    # Not seen: keep what the screen showed, and what was running, to find out why.
    shot("speed-" + label.lower().replace(" ", "-"))
    print(f"::warning title=Speed: {label}::no new window within {limit:.0f} s (largest change {most:.3f}); "
          f"running: {run('ps -eo comm | sort -u | tr \"\\n\" \" \"')[:400]}")
    return None

# (label, command, which processes to stop: pkill/pgrep arguments). Firefox's
# main process is named plain "firefox-esr" (only its helpers have the full
# path), so it's matched by name.
APPS = (("Files", "nautilus --new-window", "-x nautilus"),
        ("Terminal", "ptyxis --new-window", "-x ptyxis"),
        ("Kulan Settings", "/usr/bin/kulan-settings", "-f '[/]usr/bin/kulan-settings'"),
        ("Firefox", "firefox-esr --new-window about:blank", "-x firefox-esr"))

def stop_app(label: str, which: str):
    # Ask it to quit; if it's still there after a few seconds, make it. A window
    # left open would make the next opening look like hardly any change.
    left = run(f"pkill {which}; for i in 1 2 3 4 5 6; do sleep 1; pgrep {which} >/dev/null || break; done; "
               f"if pgrep {which} >/dev/null; then pkill -9 {which}; sleep 1; echo forced; fi")
    if "forced" in (left or ""):
        print(f"::notice title=Speed: {label}::didn't quit when asked; stopped it")

speed = {}
if qga is not None:
    for label, cmd, which in APPS:
        times = []
        for _ in range(2):
            times.append(time_open(cmd, label))
            time.sleep(3)
            stop_app(label, which)
            time.sleep(2)
        speed[label] = {"first": times[0], "again": times[1]}
        print(f"Speed: {label} first {times[0]} s, again {times[1]} s")
with open(os.path.join(out, "speed.json"), "w") as fh:
    json.dump(speed, fh, indent=2)
try:
    os.remove(os.path.join(out, "_speed.ppm"))
except OSError:
    pass
PY
# How long the live session took to start (systemd's own count, from the
# boot report on the serial console).
STARTUP="$(tr -d '\r' < "${WORK}/serial.log" | sed -n 's/^time=Startup finished in .* = \([0-9.]*\)s.*$/\1/p' | head -n 1)"
python3 - "${OUT}/speed.json" "${STARTUP:-}" <<'PY'
import json, sys
path, startup = sys.argv[1], sys.argv[2]
data = json.load(open(path)) if startup is not None else {}
if startup:
    data["Start-up"] = {"seconds": float(startup)}
json.dump(data, open(path, "w"), indent=2)
PY
ls -l "${OUT}"
