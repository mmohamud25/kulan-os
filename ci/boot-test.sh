#!/bin/bash
# Boot a built Kulan OS ISO in a virtual machine (QEMU with KVM) the way a
# person would start the live session, and check it reaches the desktop with
# no failed services. Reads the report kulan-boot-report.service writes to
# the serial port, saves a screenshot, and prints GitHub annotations and a
# summary table. Exits non-zero if the desktop doesn't come up or a service
# failed.
#
# Usage: ci/boot-test.sh <iso> [output-dir]
# Needs: qemu-system-x86, xorriso, python3 (Pillow optional, for the PNG).
set -uo pipefail

ISO="${1:?usage: $0 <iso> [output-dir]}"
OUT="${2:-boot-test}"
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
FAILED=0
ROWS=()

result() { # result <pass|fail|info> <check> <detail>
	case "$1" in
		pass) echo "::notice title=Boot test: $2::$3"; ROWS+=("| ✅ | $2 | $3 |") ;;
		info) echo "::notice title=Boot test: $2::$3"; ROWS+=("| ℹ️ | $2 | $3 |") ;;
		*) echo "::error title=Boot test: $2::$3"; ROWS+=("| ❌ | $2 | $3 |"); FAILED=1 ;;
	esac
}

summary() {
	{
		echo "### Kulan OS boot test (live session in a virtual machine)"
		echo
		echo "| | Check | Result |"
		echo "|---|---|---|"
		printf '%s\n' "${ROWS[@]}"
	} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
}

# --- Kernel, initrd and boot options, exactly as the ISO's own menu has them ---
xorriso -osirrox on -indev "${ISO}" -extract /boot/grub/grub.cfg "${WORK}/grub.cfg" >/dev/null 2>&1
LINUX_LINE="$(grep -m1 -E '^[[:space:]]*linux[[:space:]]+/live/vmlinuz' "${WORK}/grub.cfg")"
INITRD_LINE="$(grep -m1 -E '^[[:space:]]*initrd[[:space:]]+/live/initrd' "${WORK}/grub.cfg")"
KERNEL_PATH="$(awk '{print $2}' <<<"${LINUX_LINE}")"
INITRD_PATH="$(awk '{print $2}' <<<"${INITRD_LINE}")"
BOOT_ARGS="$(awk '{ $1 = ""; $2 = ""; sub(/^[[:space:]]+/, ""); print }' <<<"${LINUX_LINE}")"
# GRUB fills in its own variables (findiso=${iso_path} is empty unless the
# ISO file itself is loop-mounted from another disk); this test starts the
# kernel directly, so drop arguments that still hold a GRUB variable.
# shellcheck disable=SC2016  # a literal ${ to match
BOOT_ARGS="$(tr ' ' '\n' <<<"${BOOT_ARGS}" | grep -v '\${' | paste -sd ' ' -)"
if [[ -z "${KERNEL_PATH}" || -z "${INITRD_PATH}" ]]; then
	result fail "Boot menu" "couldn't find the live entry in /boot/grub/grub.cfg"
	summary
	exit 1
fi
xorriso -osirrox on -indev "${ISO}" -extract "${KERNEL_PATH}" "${WORK}/vmlinuz" \
	-extract "${INITRD_PATH}" "${WORK}/initrd" >/dev/null 2>&1
# The person sees the splash; the test also gets the console on the serial
# port and asks for the boot report.
BOOT_ARGS="${BOOT_ARGS} console=tty0 console=ttyS0,115200 kulan.boot-report"
result info "Boot options" "${BOOT_ARGS}"

ACCEL=(-accel tcg)
if [[ -w /dev/kvm ]]; then
	ACCEL=(-enable-kvm -cpu host)
else
	result info "Acceleration" "no KVM on this runner: the virtual machine is emulated and much slower"
	WAIT_S=$((WAIT_S * 3))
fi

# --- Boot -----------------------------------------------------------------------
START=$(date +%s)
qemu-system-x86_64 "${ACCEL[@]}" -m 4096 -smp 2 \
	-kernel "${WORK}/vmlinuz" -initrd "${WORK}/initrd" -append "${BOOT_ARGS}" \
	-drive "file=${ISO},media=cdrom,readonly=on" \
	-vga virtio -display none \
	-serial "file:${OUT}/serial.log" \
	-qmp "unix:${WORK}/qmp.sock,server=on,wait=off" \
	-netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
	-device virtio-rng-pci -no-reboot &
QEMU_PID=$!

while (( $(date +%s) - START < WAIT_S )); do
	grep -q "KULAN-BOOT-REPORT-END" "${OUT}/serial.log" 2>/dev/null && break
	kill -0 "${QEMU_PID}" 2>/dev/null || break
	sleep 5
done
ELAPSED=$(( $(date +%s) - START ))

# Screenshot of whatever is on the screen now (desktop, or where it stopped).
python3 - "${WORK}/qmp.sock" "${OUT}/screen.ppm" <<'PY' || true
import json, socket, sys
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(20)
sock.connect(sys.argv[1])
f = sock.makefile("rw")
def call(cmd, **args):
    f.write(json.dumps({"execute": cmd, "arguments": args} if args else {"execute": cmd}) + "\n"); f.flush()
    while True:
        reply = json.loads(f.readline())
        if "return" in reply or "error" in reply:
            return reply
json.loads(f.readline())  # greeting
call("qmp_capabilities")
call("screendump", filename=sys.argv[2])
PY
if [[ -s "${OUT}/screen.ppm" ]]; then
	python3 -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" \
		"${OUT}/screen.ppm" "${OUT}/screen.png" 2>/dev/null && rm -f "${OUT}/screen.ppm"
fi

# --- Read the report --------------------------------------------------------------
REPORT="$(sed -n '/KULAN-BOOT-REPORT-BEGIN/,/KULAN-BOOT-REPORT-END/p' "${OUT}/serial.log" 2>/dev/null | tr -d '\r')"
field() { sed -n "s/^$1=//p" <<<"${REPORT}"; }

# Annotations carry multi-line text with %0A; keep them short and printable.
annotate_lines() { # annotate_lines <title> (text on stdin)
	local text
	text="$(tr -d '\r' | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | tr -cd '\11\12\40-\176' | cut -c1-180 | tail -n 40 |
		sed 's/%/%25/g' | sed ':a;N;$!ba;s/\n/%0A/g')"
	[[ -n "${text}" ]] && echo "::error title=Boot test: $1::${text}"
}

# What the screen shows, as text (Tesseract), so a failure can be read from
# the annotations alone.
screen_text() {
	[[ -s "${OUT}/screen.png" ]] && command -v tesseract >/dev/null 2>&1 || return 0
	tesseract "${OUT}/screen.png" - 2>/dev/null | grep -v '^[[:space:]]*$' | head -n 25 | annotate_lines "Screen shows"
}

if [[ -z "${REPORT}" ]]; then
	result fail "Desktop" "no boot report after ${ELAPSED} s: the live session didn't reach the desktop (see serial.log and screen.png)"
	tail -n 30 "${OUT}/serial.log" 2>/dev/null | sed 's/^/    /'
	tail -n 40 "${OUT}/serial.log" 2>/dev/null | annotate_lines "Last serial console lines"
	grep -aE "Failed|FAILED|error|panic|Timed out|Dependency failed|emergency" "${OUT}/serial.log" 2>/dev/null |
		tail -n 25 | annotate_lines "Errors on the serial console"
	screen_text
	summary
	exit 1
fi

USER_NAME="$(field desktop_user)"
[[ -n "${USER_NAME}" ]] || screen_text
if [[ -n "${USER_NAME}" ]]; then
	result pass "Desktop" "the live session's desktop is running (user ${USER_NAME}), ${ELAPSED} s after power-on"
else
	result fail "Desktop" "GNOME Shell isn't running for the live user"
fi
result info "Startup time" "$(field time)"
result info "Memory in use" "$(field memory)"

SWAP="$(field swap)"
if [[ "${SWAP}" == *zram* ]]; then
	result pass "Compressed memory" "zram swap on: ${SWAP%;}"
else
	result fail "Compressed memory" "no zram swap (${SWAP:-none})"
fi

OOMD="$(field oomd)"
if [[ "${OOMD}" == "active active" ]]; then
	result pass "Memory guard" "systemd-oomd and Kulan's notice running"
else
	result fail "Memory guard" "systemd-oomd / notice: ${OOMD:-not reported}"
fi

APPS="$(field appupdates)"
if [[ "${APPS}" == ok ]]; then
	result pass "App updates" "Flatpak lists app updates (as Software Update does)"
else
	result info "App updates" "couldn't list app updates: ${APPS:-not reported} (no network in the test machine?)"
fi

FAILED_UNITS="$(field failed | tr '\n' ' ')"
if [[ -z "${FAILED_UNITS// /}" ]]; then
	result pass "Services" "no failed services (system state: $(field state))"
else
	result fail "Services" "failed: ${FAILED_UNITS}"
fi
result info "Slowest to start" "$(field blame | head -n 5 | tr -s ' ' | paste -sd ';' -)"

summary
exit "${FAILED}"
