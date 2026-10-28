#!/bin/bash
# The ISO's own start menu, as a person sees it when starting from the USB
# stick: UEFI firmware with Secure Boot (OVMF with Microsoft's keys), the ISO
# on a USB stick, a 1920x1080 screen. Takes a picture every second while the
# menu counts down, and checks the entries can be read (Tesseract).
#
# Usage: ci/boot-menu-test.sh <iso> [output-dir]
# Needs: qemu-system-x86, ovmf, xorriso, python3-pil, tesseract-ocr.
set -uo pipefail

ISO="${1:?usage: $0 <iso> [output-dir]}"
OUT="${2:-boot-menu-test}"
LABEL="Boot menu"
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

result() { # result <pass|fail|info> <check> <detail>
	case "$1" in
		pass | info) echo "::notice title=${LABEL}: $2::$3" ;;
		*) echo "::error title=${LABEL}: $2::$3"; FAILED=1 ;;
	esac
	echo "$1: $2: $3" >>"${OUT}/results.txt"
}

# The menu's files, for reading a problem from the logs.
for F in /boot/grub/grub.cfg /boot/grub/config.cfg /boot/grub/theme.cfg /boot/grub/live-theme/theme.txt; do
	xorriso -osirrox on -indev "${ISO}" -extract "${F}" "${OUT}/$(basename "$(dirname "${F}")")-$(basename "${F}").txt" >/dev/null 2>&1
done
xorriso -indev "${ISO}" -find /boot/grub -type f 2>/dev/null | sed -n "s/^'\(.*\)'$/\1/p" >"${OUT}/grub-files.txt"

CODE="$(ls /usr/share/OVMF/OVMF_CODE_4M.secboot.fd /usr/share/OVMF/OVMF_CODE.secboot.fd 2>/dev/null | head -n 1)"
VARS="$(ls /usr/share/OVMF/OVMF_VARS_4M.ms.fd /usr/share/OVMF/OVMF_VARS.ms.fd 2>/dev/null | head -n 1)"
if [[ -z "${CODE}" || -z "${VARS}" ]]; then
	result fail "Firmware" "OVMF with Secure Boot and Microsoft's keys isn't installed"
	exit 1
fi
cp "${VARS}" "${WORK}/vars.fd"
ACCEL=(-accel tcg)
[[ -w /dev/kvm ]] && ACCEL=(-enable-kvm -cpu host)

qemu-system-x86_64 "${ACCEL[@]}" -machine q35,smm=on -m 2048 \
	-global driver=cfi.pflash01,property=secure,value=on \
	-drive "if=pflash,format=raw,readonly=on,file=${CODE}" -drive "if=pflash,format=raw,file=${WORK}/vars.fd" \
	-device qemu-xhci -drive "if=none,id=stick,format=raw,readonly=on,file=${ISO}" -device usb-storage,drive=stick,bootindex=0 \
	-device VGA,xres=1920,yres=1080,edid=on -display none \
	-qmp "unix:${WORK}/qmp.sock,server=on,wait=off" -no-reboot &
QEMU_PID=$!
sleep 1

# A picture each second for 25 seconds (firmware, then the menu's countdown).
python3 - "${WORK}/qmp.sock" "${WORK}" <<'PY' || true
import json, socket, sys, time
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(20)
for _ in range(20):
    try:
        sock.connect(sys.argv[1]); break
    except OSError:
        time.sleep(0.5)
f = sock.makefile("rw")
def call(cmd, **args):
    f.write(json.dumps({"execute": cmd, "arguments": args} if args else {"execute": cmd}) + "\n"); f.flush()
    while True:
        reply = json.loads(f.readline())
        if "return" in reply or "error" in reply:
            return reply
json.loads(f.readline())
call("qmp_capabilities")
for n in range(25):
    call("screendump", filename=f"{sys.argv[2]}/menu-{n:02d}.ppm")
    time.sleep(1)
PY
kill "${QEMU_PID}" 2>/dev/null
QEMU_PID=""

# "Kulan OS Live", allowing for Tesseract reading OS as 0S or 08.
ENTRY='Kulan [O0][S58] Live'
BEST="" BEST_TEXT=""
for PPM in "${WORK}"/menu-*.ppm; do
	[[ -s "${PPM}" ]] || continue
	PNG="${PPM%.ppm}.png"
	python3 -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" "${PPM}" "${PNG}" 2>/dev/null || continue
	TEXT="$(tesseract "${PNG}" - 2>/dev/null | tr -s '[:space:]' ' ')"
	echo "$(basename "${PNG}"): ${TEXT}" >>"${OUT}/screens.txt"
	if [[ "${TEXT}" == *"automatically"* || "${TEXT}" == *"Live"* ]] && [[ -z "${BEST}" || "${TEXT}" =~ ${ENTRY} ]]; then
		BEST="${PNG}" BEST_TEXT="${TEXT}"
	fi
done
if [[ -z "${BEST}" ]]; then
	result fail "Menu shown" "the start menu never came up (see screens.txt)"
	exit 1
fi
cp "${BEST}" "${OUT}/boot-menu.png"
result info "Menu shown" "${BEST_TEXT:0:300}"
SIZE="$(python3 -c "from PIL import Image; import sys; print('%dx%d' % Image.open(sys.argv[1]).size)" "${BEST}" 2>/dev/null)"
result info "Screen size" "${SIZE} (the computer's own size, as on a laptop's screen)"
if [[ "${BEST_TEXT}" =~ ${ENTRY} ]]; then
	result pass "Entries readable" "\"Kulan OS Live\" can be read in the menu (boot-menu.png)"
else
	result fail "Entries readable" "the menu is shown but \"Kulan OS Live\" can't be read in it (boot-menu.png)"
fi
exit "${FAILED}"
