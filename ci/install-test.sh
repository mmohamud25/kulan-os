#!/bin/bash
# Install a built Kulan OS ISO onto a virtual disk, start the installed system
# in a virtual machine (QEMU with KVM, UEFI), let it restart once, and check
# both starts reach the desktop with no failed services.
#
# The installation is done by Kulan Installer's engine, as the app does it
# (erase the disk, copy, remove the live packages, account, GRUB with shim from
# the ISO's own package pool, no internet). The test account signs in by
# itself; the kernel options ask for the boot report and for one restart
# (kulan.restart-test).
#
# Usage: ci/install-test.sh <iso> [output-dir]      (runs commands with sudo)
# Needs: qemu-system-x86, ovmf, squashfs-tools, xorriso, dosfstools, fdisk
set -uo pipefail

ISO="${1:?usage: $0 <iso> [output-dir]}"
OUT="${2:-install-test}"
WAIT_S="${KULAN_INSTALL_WAIT:-1800}" # includes taking a first restore point
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"
WORK="$(mktemp -d)"
MNT="${WORK}/root"
LOOP=""
QEMU_PID=""
FAILED=0
ROWS=()

# shellcheck disable=SC2317  # called by the EXIT trap
cleanup() {
	[[ -n "${QEMU_PID}" ]] && kill "${QEMU_PID}" 2>/dev/null
	for M in dev/pts dev proc sys run boot/efi ""; do
		mountpoint -q "${MNT}/${M}" 2>/dev/null && sudo umount -l "${MNT}/${M}"
	done
	[[ -n "${LOOP}" ]] && sudo losetup -d "${LOOP}" 2>/dev/null
	sudo rm -rf "${WORK}"
}
trap cleanup EXIT

result() { # result <pass|fail|info> <check> <detail>
	case "$1" in
		pass) echo "::notice title=Install test: $2::$3"; ROWS+=("| ✅ | $2 | $3 |") ;;
		info) echo "::notice title=Install test: $2::$3"; ROWS+=("| ℹ️ | $2 | $3 |") ;;
		*) echo "::error title=Install test: $2::$3"; ROWS+=("| ❌ | $2 | $3 |"); FAILED=1 ;;
	esac
}

summary() {
	{
		echo "### Kulan OS install test (installed system, started and restarted)"
		echo
		echo "| | Check | Result |"
		echo "|---|---|---|"
		printf '%s\n' "${ROWS[@]}"
	} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
}

fail_now() {
	result fail "$1" "$2"
	summary
	exit 1
}

in_root() { sudo chroot "${MNT}" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin DEBIAN_FRONTEND=noninteractive "$@"; }

# --- Install ----------------------------------------------------------------------
# With Kulan Installer's own engine (the same file the ISO carries), as the
# app runs it: erase the disk, copy the system, remove the live session's
# packages, make the account, and install GRUB from the ISO's package pool.
ENGINE="$(cd "$(dirname "$0")/.." && pwd)/packages/kulan-installer/root/usr/lib/kulan/installer-engine"
[[ -x "${ENGINE}" ]] || fail_now "Installer" "installer-engine not found"
xorriso -osirrox on -indev "${ISO}" -extract /live/filesystem.squashfs "${WORK}/fs.squashfs" >/dev/null 2>&1 ||
	fail_now "Live filesystem" "couldn't read live/filesystem.squashfs from the ISO"
mkdir -p "${WORK}/medium"
xorriso -osirrox on -indev "${ISO}" -extract /pool "${WORK}/medium/pool" -extract /dists "${WORK}/medium/dists" \
	>/dev/null 2>&1 || fail_now "Package pool" "the ISO has no package pool (/pool, /dists) for the boot loader"

truncate -s 24G "${WORK}/disk.img"
LOOP="$(sudo losetup -fP --show "${WORK}/disk.img")" || fail_now "Disk" "no loop device"
START=$(date +%s)
PLAN="$(python3 - "${LOOP}" "${WORK}" <<'PY'
import json, sys
loop, work = sys.argv[1], sys.argv[2]
print(json.dumps({
    "disk": loop, "mode": "erase", "encrypt": False, "locale": "en_US.UTF-8",
    "keyboard": {"layout": "us", "variant": ""}, "timezone": "America/New_York", "hostname": "kulan-test",
    "user": {"full_name": "Kulan Test", "username": "kulan", "password": "kulan-test-pw", "autologin": True},
    "source": f"{work}/fs.squashfs", "medium": f"{work}/medium", "firmware": "uefi",
    "kernel_args": "console=tty0 console=ttyS0,115200 kulan.boot-report kulan.restart-test",
    "efi_removable": True, "no_nvram": True, "require_pool": True,
}))
PY
)"
# shellcheck disable=SC2024  # the logs belong to this user, on purpose
sudo "${ENGINE}" install <<<"${PLAN}" >"${OUT}/engine.jsonl" 2>"${OUT}/install.log"
LAST="$(tail -n 1 "${OUT}/engine.jsonl")"
if ! grep -q '"done": true' <<<"${LAST}"; then
	fail_now "Install" "Kulan Installer's engine: $(sed -n 's/.*"error": "\(.*\)".*/\1/p' <<<"${LAST}") | $(grep -E 'error|E:|failed' "${OUT}/install.log" | tail -n 3 | tr '\n' ' ' | cut -c1-300)"
fi
STEPS="$(grep -c '"progress"' "${OUT}/engine.jsonl")"
if grep -q "kulan-installer-media.list" "${OUT}/install.log"; then
	result pass "Offline boot loader" "GRUB and shim came from the ISO's package pool"
else
	result fail "Offline boot loader" "the package pool wasn't used"
fi
result pass "Install" "Kulan Installer's engine: erased, copied, set up and made bootable in $(( $(date +%s) - START )) s (${STEPS} progress updates)"

# The installed system, as the engine left it.
sudo partprobe "${LOOP}" 2>/dev/null || true
mkdir -p "${MNT}"
ROOT_PART="$(lsblk -lnpo NAME,PARTLABEL "${LOOP}" | awk '$2 == "Kulan" {print $1}' | head -n 1)"
[[ -n "${ROOT_PART}" ]] || ROOT_PART="${LOOP}p2"
if sudo mount -o ro "${ROOT_PART}" "${MNT}"; then
	if sudo test -e "${MNT}/usr/lib/kulan/installer-engine" || sudo test -e "${MNT}/usr/bin/calamares"; then
		result fail "Live tools removed" "Kulan Installer or Calamares is still on the installed system"
	else
		result pass "Live tools removed" "no installer and no live-boot on the installed system"
	fi
	if sudo grep -q "^kulan:" "${MNT}/etc/passwd" && sudo grep -qx "kulan-test" "${MNT}/etc/hostname" &&
		[[ "$(sudo readlink "${MNT}/etc/localtime")" == */America/New_York ]]; then
		result pass "Settings" "account, computer name and time zone as chosen"
	else
		result fail "Settings" "account, computer name or time zone missing"
	fi
	sudo umount "${MNT}"
fi

sync
sudo losetup -d "${LOOP}"
LOOP=""
sudo chown "$(id -u):$(id -g)" "${WORK}/disk.img"

# --- Start, restart, start again --------------------------------------------------
CODE="$(ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd 2>/dev/null | head -n 1)"
VARS="$(ls /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd 2>/dev/null | head -n 1)"
[[ -n "${CODE}" && -n "${VARS}" ]] || fail_now "Firmware" "OVMF isn't installed"
cp "${VARS}" "${WORK}/vars.fd"
ACCEL=(-accel tcg)
if [[ -w /dev/kvm ]]; then
	ACCEL=(-enable-kvm -cpu host)
else
	WAIT_S=$((WAIT_S * 3))
fi
START=$(date +%s)
qemu-system-x86_64 "${ACCEL[@]}" -m 4096 -smp 2 -machine q35 \
	-drive "if=pflash,format=raw,readonly=on,file=${CODE}" -drive "if=pflash,format=raw,file=${WORK}/vars.fd" \
	-drive "file=${WORK}/disk.img,format=raw,if=virtio" \
	-vga virtio -display none -serial "file:${OUT}/serial.log" \
	-qmp "unix:${WORK}/qmp.sock,server=on,wait=off" \
	-netdev user,id=net0 -device virtio-net-pci,netdev=net0 -device virtio-rng-pci &
QEMU_PID=$!

reports() {
	local n
	n="$(grep -c "KULAN-BOOT-REPORT-END" "${OUT}/serial.log" 2>/dev/null)"
	echo "${n:-0}"
}
while (( $(date +%s) - START < WAIT_S )); do
	[[ "$(reports)" -ge 2 ]] && break
	kill -0 "${QEMU_PID}" 2>/dev/null || break
	sleep 5
done
ELAPSED=$(( $(date +%s) - START ))
python3 - "${WORK}/qmp.sock" "${OUT}/screen.ppm" <<'PY' || true
import json, socket, sys
sock = socket.socket(socket.AF_UNIX); sock.settimeout(20); sock.connect(sys.argv[1])
f = sock.makefile("rw")
def call(cmd, **args):
    f.write(json.dumps({"execute": cmd, "arguments": args} if args else {"execute": cmd}) + "\n"); f.flush()
    while True:
        reply = json.loads(f.readline())
        if "return" in reply or "error" in reply:
            return reply
json.loads(f.readline()); call("qmp_capabilities"); call("screendump", filename=sys.argv[2])
PY
[[ -s "${OUT}/screen.ppm" ]] && python3 -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" \
	"${OUT}/screen.ppm" "${OUT}/screen.png" 2>/dev/null && rm -f "${OUT}/screen.ppm"

# --- Read both reports ------------------------------------------------------------
report() { # report <n>: the n-th report's lines
	tr -d '\r' < "${OUT}/serial.log" | awk -v n="$1" '/KULAN-BOOT-REPORT-BEGIN/ {c++} c == n {print} /KULAN-BOOT-REPORT-END/ && c == n {exit}'
}
field() { sed -n "s/^$2=//p" <<<"$1" | head -n 1; }
COUNT="$(reports)"
for N in 1 2; do
	NAME="First start"
	[[ "${N}" == 2 ]] && NAME="After restart"
	R="$(report "${N}")"
	if [[ -z "${R}" ]]; then
		result fail "${NAME}" "no report after ${ELAPSED} s (serial log: $(tail -n 3 "${OUT}/serial.log" 2>/dev/null | tr -d '\r' | tr '\n' ' ' | cut -c1-200))"
		continue
	fi
	USER_="$(field "${R}" desktop_user)"
	FAILED_UNITS="$(sed -n 's/^failed=//p' <<<"${R}" | paste -sd ' ' -)"
	if [[ "${USER_}" == kulan ]]; then
		result pass "${NAME}" "desktop for kulan; $(field "${R}" time)"
	else
		result fail "${NAME}" "no desktop session (state $(field "${R}" state))"
	fi
	result info "${NAME}: slowest" "$(sed -n 's/^blame=//p' <<<"${R}" | head -n 5 | tr -s ' ' | paste -sd ';' -)"
	result info "${NAME}: login screen waited on" "$(sed -n 's/^chain=//p' <<<"${R}" | head -n 6 | tr -s ' ' | paste -sd ';' -)"
	if [[ -n "${FAILED_UNITS}" ]]; then
		result fail "${NAME}: services" "failed: ${FAILED_UNITS}"
	else
		result pass "${NAME}: services" "none failed"
	fi
done
RP="$(field "$(report 1)" restorepoint)"
if [[ "${RP}" =~ ^success\ ([1-9][0-9]*)$ ]]; then
	result pass "Restore point" "the sandboxed restore-points service took one (${BASH_REMATCH[1]} on the disk)"
elif [[ -n "${RP}" ]]; then
	result fail "Restore point" "the restore-points service didn't take one: ${RP}"
else
	result info "Restore point" "not reported"
fi
UP="$(field "$(report 1)" updatepoint)"
if [[ "${UP}" =~ ^0\ 1\  ]] || [[ "${UP}" == "0 1" ]]; then
	result pass "Restore point before updates" "taken the way Software Update takes it before installing updates"
elif [[ -n "${UP}" ]]; then
	result fail "Restore point before updates" "not taken: ${UP}"
else
	result info "Restore point before updates" "not reported"
fi
SPACE="$(field "$(report 1)" spacecheck)"
if [[ "${SPACE}" == ok ]]; then
	result pass "Free-space check" "Software Update's check before installing reads apt's plan without errors"
elif [[ -n "${SPACE}" ]]; then
	result fail "Free-space check" "${SPACE}"
fi
ID1="$(field "$(report 1)" boot_id)"
ID2="$(field "$(report 2)" boot_id)"
if [[ "${COUNT}" -ge 2 && -n "${ID1}" && -n "${ID2}" && "${ID1}" != "${ID2}" ]]; then
	result pass "Restart" "the installed system restarted and came back to the desktop (${ELAPSED} s for both starts)"
elif [[ "${COUNT}" -ge 2 ]]; then
	result fail "Restart" "two reports but not from two different starts (${ID1:-?} / ${ID2:-?})"
fi
summary
exit "${FAILED}"
