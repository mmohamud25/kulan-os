#!/bin/bash
# Install a built Kulan OS ISO onto a virtual disk, start the installed system
# in a virtual machine (QEMU with KVM, UEFI), let it restart once, and check
# both starts reach the desktop with no failed services.
#
# The installation is done the way the installer does it: the live system's
# files copied to an ext4 partition, live-only packages removed, a user
# account, the boot loader (GRUB with shim) on an EFI partition. The test
# account signs in by itself; the kernel options ask for the boot report and
# for one restart (kulan.restart-test).
#
# Usage: ci/install-test.sh <iso> [output-dir]      (runs commands with sudo)
# Needs: qemu-system-x86, ovmf, squashfs-tools, xorriso, dosfstools, fdisk
set -uo pipefail

ISO="${1:?usage: $0 <iso> [output-dir]}"
OUT="${2:-install-test}"
WAIT_S="${KULAN_INSTALL_WAIT:-900}"
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
xorriso -osirrox on -indev "${ISO}" -extract /live/filesystem.squashfs "${WORK}/fs.squashfs" >/dev/null 2>&1 ||
	fail_now "Live filesystem" "couldn't read live/filesystem.squashfs from the ISO"

truncate -s 16G "${WORK}/disk.img"
printf 'label: gpt\n,512M,U\n,,L\n' | sfdisk -q "${WORK}/disk.img" >/dev/null || fail_now "Disk" "partitioning failed"
LOOP="$(sudo losetup -fP --show "${WORK}/disk.img")" || fail_now "Disk" "no loop device"
sudo mkfs.vfat -F 32 -n EFI "${LOOP}p1" >/dev/null && sudo mkfs.ext4 -q -L kulan "${LOOP}p2" ||
	fail_now "Disk" "formatting failed"
mkdir -p "${MNT}"
sudo mount "${LOOP}p2" "${MNT}"
START=$(date +%s)
sudo unsquashfs -q -f -d "${MNT}" "${WORK}/fs.squashfs" >/dev/null || fail_now "Copy" "copying the system failed"
sudo mkdir -p "${MNT}/boot/efi"
sudo mount "${LOOP}p1" "${MNT}/boot/efi"
for M in dev dev/pts proc sys run; do sudo mount --bind "/${M}" "${MNT}/${M}"; done
sudo rm -f "${MNT}/etc/resolv.conf"
sudo cp -L /etc/resolv.conf "${MNT}/etc/resolv.conf"

ROOT_UUID="$(sudo blkid -s UUID -o value "${LOOP}p2")"
EFI_UUID="$(sudo blkid -s UUID -o value "${LOOP}p1")"
printf 'UUID=%s / ext4 errors=remount-ro 0 1\nUUID=%s /boot/efi vfat umask=0077 0 1\n' "${ROOT_UUID}" "${EFI_UUID}" |
	sudo tee "${MNT}/etc/fstab" >/dev/null

# What the installer does after copying: remove the live session's own
# packages, a new machine ID, a host name and a person's account.
{
	in_root apt-get purge -y -q live-boot live-boot-initramfs-tools live-config live-config-systemd live-tools \
		calamares calamares-settings-debian
	in_root apt-get autoremove -y -q
} >"${OUT}/install.log" 2>&1 || result info "Live packages" "some live packages couldn't be removed (see install.log)"
sudo rm -f "${MNT}/etc/machine-id" "${MNT}/var/lib/dbus/machine-id"
in_root systemd-machine-id-setup >/dev/null 2>&1
echo kulan-test | sudo tee "${MNT}/etc/hostname" >/dev/null
printf '127.0.0.1\tlocalhost\n127.0.1.1\tkulan-test\n' | sudo tee "${MNT}/etc/hosts" >/dev/null
in_root useradd -m -s /bin/zsh -G sudo,adm -c "Kulan Test" kulan >>"${OUT}/install.log" 2>&1 ||
	fail_now "Account" "couldn't create the account"
echo 'kulan:kulan-test-pw' | in_root chpasswd
# Signs in by itself, so the test sees a real session.
sudo mkdir -p "${MNT}/etc/gdm3"
printf '[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=kulan\n' | sudo tee "${MNT}/etc/gdm3/daemon.conf" >/dev/null

# Boot loader, as the installer sets it up for UEFI (shim, signed GRUB).
printf 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} console=tty0 console=ttyS0,115200 kulan.boot-report kulan.restart-test"\n' |
	sudo tee "${MNT}/etc/default/grub.d/zz-kulan-install-test.cfg" >/dev/null
{
	in_root apt-get update -q &&
		in_root apt-get install -y -q grub-efi-amd64 grub-efi-amd64-signed shim-signed &&
		in_root update-initramfs -u -k all &&
		in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --uefi-secure-boot --removable --no-nvram &&
		in_root update-grub
} >>"${OUT}/install.log" 2>&1 || fail_now "Boot loader" "GRUB setup failed: $(grep -E 'error|E:' "${OUT}/install.log" | tail -n 3 | tr '\n' ' ' | cut -c1-300)"
result pass "Install" "copied, cleaned up and made bootable in $(( $(date +%s) - START )) s"

sync
for M in dev/pts dev proc sys run boot/efi ""; do sudo umount "${MNT}/${M}" 2>/dev/null || sudo umount -l "${MNT}/${M}"; done
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

reports() { grep -c "KULAN-BOOT-REPORT-END" "${OUT}/serial.log" 2>/dev/null || echo 0; }
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
	if [[ -n "${FAILED_UNITS}" ]]; then
		result fail "${NAME}: services" "failed: ${FAILED_UNITS}"
	else
		result pass "${NAME}: services" "none failed"
	fi
done
BOOTS="$(field "$(report 2)" boots)"
if [[ "${COUNT}" -ge 2 && "${BOOTS:-0}" -ge 2 ]]; then
	result pass "Restart" "the installed system restarted and came back to the desktop (${ELAPSED} s for both starts)"
elif [[ "${COUNT}" -ge 2 ]]; then
	result fail "Restart" "two reports but the journal shows ${BOOTS:-?} start(s)"
fi
summary
exit "${FAILED}"
