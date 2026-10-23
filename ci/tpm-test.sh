#!/bin/bash
# TPM unlock, end to end: install a built Kulan OS ISO onto an encrypted disk
# (LUKS2 root, /boot and EFI apart, as Debian's installer lays it out), start
# it with Secure Boot on (OVMF with Microsoft's keys) and an emulated TPM 2.0
# (swtpm), turn TPM unlock on with kulan-tpm, take every other key away, and
# check the next start reaches the desktop with nobody typing a password,
# because the TPM opened the disk.
#
# First start: the disk opens with a test key file kept in the start-up image
# (only so the test can get in without typing). A one-off unit put in by this
# script then runs `kulan-tpm on` with the disk password, removes the key file
# and its key slot, rebuilds the start-up image and restarts. Second start:
# only the TPM can open the disk (or someone typing the password, and nobody
# does). kulan.boot-report reports the desktop and how the disk was opened.
#
# Usage: ci/tpm-test.sh <iso> [output-dir]      (runs commands with sudo)
# Needs: qemu-system-x86, ovmf, swtpm, swtpm-tools, cryptsetup, squashfs-tools,
#        xorriso, dosfstools, fdisk
set -uo pipefail

ISO="${1:?usage: $0 <iso> [output-dir]}"
OUT="${2:-tpm-test}"
WAIT_S="${KULAN_TPM_WAIT:-1500}"
PASSWORD="kulan-disk-test-pw"
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"
WORK="$(mktemp -d)"
MNT="${WORK}/root"
LOOP=""
QEMU_PID=""
SWTPM_PID=""
FAILED=0
ROWS=()

# shellcheck disable=SC2317  # called by the EXIT trap
cleanup() {
	[[ -n "${QEMU_PID}" ]] && kill "${QEMU_PID}" 2>/dev/null
	[[ -n "${SWTPM_PID}" ]] && kill "${SWTPM_PID}" 2>/dev/null
	for M in dev/pts dev proc sys run boot/efi boot ""; do
		mountpoint -q "${MNT}/${M}" 2>/dev/null && sudo umount -l "${MNT}/${M}"
	done
	sudo cryptsetup close kulan_test_root 2>/dev/null
	[[ -n "${LOOP}" ]] && sudo losetup -d "${LOOP}" 2>/dev/null
	sudo rm -rf "${WORK}"
}
trap cleanup EXIT

result() { # result <pass|fail|info> <check> <detail>
	case "$1" in
		pass) echo "::notice title=TPM test: $2::$3"; ROWS+=("| ✅ | $2 | $3 |") ;;
		info) echo "::notice title=TPM test: $2::$3"; ROWS+=("| ℹ️ | $2 | $3 |") ;;
		*) echo "::error title=TPM test: $2::$3"; ROWS+=("| ❌ | $2 | $3 |"); FAILED=1 ;;
	esac
}

summary() {
	{
		echo "### Kulan OS TPM unlock test (encrypted install, Secure Boot, emulated TPM)"
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

# --- Install onto an encrypted disk -----------------------------------------------
xorriso -osirrox on -indev "${ISO}" -extract /live/filesystem.squashfs "${WORK}/fs.squashfs" >/dev/null 2>&1 ||
	fail_now "Live filesystem" "couldn't read live/filesystem.squashfs from the ISO"

truncate -s 24G "${WORK}/disk.img"
printf 'label: gpt\n,512M,U\n,1G,L\n,,L\n' | sfdisk -q "${WORK}/disk.img" >/dev/null || fail_now "Disk" "partitioning failed"
LOOP="$(sudo losetup -fP --show "${WORK}/disk.img")" || fail_now "Disk" "no loop device"
sudo udevadm settle 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -b "${LOOP}p3" ]] && break; sleep 1; done
[[ -b "${LOOP}p3" ]] || fail_now "Disk" "the partitions of ${LOOP} didn't appear"
# A light key derivation, so the test machine opens the disk quickly.
printf '%s' "${PASSWORD}" | sudo cryptsetup luksFormat -q --type luks2 --pbkdf argon2id --pbkdf-memory 65536 \
	--iter-time 200 --key-file=- "${LOOP}p3" || fail_now "Encryption" "luksFormat failed"
printf '%s' "${PASSWORD}" | sudo cryptsetup open --key-file=- "${LOOP}p3" kulan_test_root ||
	fail_now "Encryption" "couldn't open the new LUKS volume"
sudo mkfs.vfat -F 32 -n EFI "${LOOP}p1" >/dev/null && sudo mkfs.ext4 -q -L boot "${LOOP}p2" &&
	sudo mkfs.ext4 -q -L kulan /dev/mapper/kulan_test_root || fail_now "Disk" "formatting failed"
mkdir -p "${MNT}"
sudo mount /dev/mapper/kulan_test_root "${MNT}" || fail_now "Disk" "couldn't mount the new root"
START=$(date +%s)
sudo unsquashfs -q -f -d "${MNT}" "${WORK}/fs.squashfs" >/dev/null || fail_now "Copy" "copying the system failed"
sudo mv "${MNT}/boot" "${MNT}/boot.live" && sudo mkdir -p "${MNT}/boot"
sudo mount "${LOOP}p2" "${MNT}/boot" || fail_now "Disk" "couldn't mount /boot"
sudo cp -a "${MNT}/boot.live/." "${MNT}/boot/" && sudo rm -rf "${MNT}/boot.live"
sudo mkdir -p "${MNT}/boot/efi"
sudo mount "${LOOP}p1" "${MNT}/boot/efi" || fail_now "Disk" "couldn't mount the EFI partition"
for M in dev dev/pts proc sys run; do sudo mount --bind "/${M}" "${MNT}/${M}"; done
sudo rm -f "${MNT}/etc/resolv.conf"
sudo cp -L /etc/resolv.conf "${MNT}/etc/resolv.conf"

ROOT_UUID="$(sudo blkid -s UUID -o value /dev/mapper/kulan_test_root)"
BOOT_UUID="$(sudo blkid -s UUID -o value "${LOOP}p2")"
EFI_UUID="$(sudo blkid -s UUID -o value "${LOOP}p1")"
LUKS_UUID="$(sudo blkid -s UUID -o value "${LOOP}p3")"
printf 'UUID=%s / ext4 errors=remount-ro 0 1\nUUID=%s /boot ext4 defaults 0 2\nUUID=%s /boot/efi vfat umask=0077 0 1\n' \
	"${ROOT_UUID}" "${BOOT_UUID}" "${EFI_UUID}" | sudo tee "${MNT}/etc/fstab" >/dev/null

# The test key: in the start-up image for the first start only.
sudo dd if=/dev/urandom of="${MNT}/etc/kulan-test.key" bs=64 count=1 status=none
sudo chmod 0400 "${MNT}/etc/kulan-test.key"
printf '%s' "${PASSWORD}" | sudo cryptsetup luksAddKey --key-file=- "${LOOP}p3" "${MNT}/etc/kulan-test.key" ||
	fail_now "Encryption" "couldn't add the test key"
# "initramfs": update-initramfs runs in a chroot here, where cryptsetup's hook
# reads the host's mounts and can't tell / is on this volume, so say it outright.
printf 'kulan_crypt UUID=%s /etc/kulan-test.key luks,discard,initramfs\n' "${LUKS_UUID}" | sudo tee "${MNT}/etc/crypttab" >/dev/null
# The key file goes into the start-up image (set once cryptsetup-initramfs,
# which owns conf-hook, is installed below).
echo 'UMASK=0077' | sudo tee "${MNT}/etc/initramfs-tools/conf.d/kulan-test-umask" >/dev/null

{
	in_root apt-get purge -y -q live-boot live-boot-initramfs-tools live-config live-config-systemd live-tools \
		calamares calamares-settings-debian
	in_root apt-get autoremove -y -q
} >"${OUT}/install.log" 2>&1 || result info "Live packages" "some live packages couldn't be removed (see install.log)"
sudo rm -f "${MNT}/etc/machine-id" "${MNT}/var/lib/dbus/machine-id"
in_root systemd-machine-id-setup >/dev/null 2>&1
echo kulan-tpm-test | sudo tee "${MNT}/etc/hostname" >/dev/null
printf '127.0.0.1\tlocalhost\n127.0.1.1\tkulan-tpm-test\n' | sudo tee "${MNT}/etc/hosts" >/dev/null
in_root useradd -m -s /bin/zsh -G sudo,adm -c "Kulan Test" kulan >>"${OUT}/install.log" 2>&1 ||
	fail_now "Account" "couldn't create the account"
echo 'kulan:kulan-test-pw' | in_root chpasswd
sudo mkdir -p "${MNT}/etc/gdm3"
printf '[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=kulan\n' | sudo tee "${MNT}/etc/gdm3/daemon.conf" >/dev/null

# The one-off first-start step: TPM unlock on, then nothing but the TPM (or a
# typed password) can open the disk. Reports to the serial console.
sudo tee "${MNT}/usr/local/sbin/kulan-tpm-test-setup" >/dev/null <<EOF
#!/bin/bash
# Also kept on /boot (not encrypted), so the test can read every line even
# when the console output is cut short or mixed with kernel messages.
exec > >(tee -a /boot/kulan-tpm-setup.log >/dev/ttyS0) 2>&1
echo KULAN-TPM-SETUP-BEGIN
# TPM unlock is held back for users in 2.4; this test keeps working on it.
export KULAN_TPM_PREVIEW=1
kulan-tpm status --json
printf '%s\n' '${PASSWORD}' | kulan-tpm on; echo "tpm_on_exit=\$?"
sed -i 's#^\(kulan_crypt [^ ]*\) /etc/kulan-test.key #\1 none #' /etc/crypttab
cryptsetup luksRemoveKey "\$(blkid -t UUID=${LUKS_UUID} -o device)" /etc/kulan-test.key; echo "key_removed=\$?"
rm -f /etc/kulan-test.key /etc/cryptsetup-initramfs/conf-hook
dracut --force --regenerate-all >/dev/null 2>&1; echo "dracut=\$?"
echo "crypttab=\$(grep kulan_crypt /etc/crypttab)"
cryptsetup luksDump "\$(blkid -t UUID=${LUKS_UUID} -o device)" | grep -E '^\s+[0-9]+: (luks2|systemd-tpm2)' | tr -s ' ' | sed 's/^/slot /'
systemctl disable kulan-tpm-test-setup.service
echo KULAN-TPM-SETUP-END
sync
sleep 1
# Past GNOME's shutdown inhibitor: when the desktop session is already up,
# a plain "systemctl reboot" from a service is refused, and the test then
# waited for a restart that never came.
systemctl reboot --check-inhibitors=no || { echo "reboot_exit=\$?"; sync; systemctl reboot --force; }
EOF
sudo chmod 755 "${MNT}/usr/local/sbin/kulan-tpm-test-setup"
printf '[Unit]\nDescription=Kulan TPM test: turn TPM unlock on, then restart\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/kulan-tpm-test-setup\n\n[Install]\nWantedBy=multi-user.target\n' |
	sudo tee "${MNT}/etc/systemd/system/kulan-tpm-test-setup.service" >/dev/null
in_root systemctl enable kulan-tpm-test-setup.service >>"${OUT}/install.log" 2>&1

sudo mkdir -p "${MNT}/etc/default/grub.d"
printf 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} console=tty0 console=ttyS0,115200 kulan.boot-report"\n' |
	sudo tee "${MNT}/etc/default/grub.d/zz-kulan-tpm-test.cfg" >/dev/null
{
	in_root apt-get update -q &&
		in_root apt-get install -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
			grub-efi-amd64 grub-efi-amd64-signed shim-signed cryptsetup-initramfs &&
		echo 'KEYFILE_PATTERN="/etc/kulan-test.key"' | sudo tee -a "${MNT}/etc/cryptsetup-initramfs/conf-hook" >/dev/null &&
		echo 'CRYPTSETUP=y' | sudo tee -a "${MNT}/etc/cryptsetup-initramfs/conf-hook" >/dev/null &&
		in_root update-initramfs -u -k all &&
		in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --uefi-secure-boot --removable --no-nvram &&
		in_root update-grub
} >>"${OUT}/install.log" 2>&1 || fail_now "Boot loader" "GRUB setup failed: $(grep -E 'error|E:' "${OUT}/install.log" | tail -n 3 | tr '\n' ' ' | cut -c1-300)"
# The start-up image must know the volume and hold the key, or the first start
# stops at an (initramfs) prompt.
INITRD="$(find "${MNT}/boot" -maxdepth 1 -name 'initrd.img-*' -printf '%f\n' | sort -V | tail -n 1)"
IN_INITRD="$(in_root lsinitramfs "/boot/${INITRD}" 2>/dev/null)"
grep -q 'cryptroot/crypttab$' <<<"${IN_INITRD}" ||
	fail_now "Start-up image" "no cryptroot/crypttab in ${INITRD:-the start-up image}; crypttab: $(sudo cat "${MNT}/etc/crypttab")"
grep -q 'cryptroot/keyfiles/' <<<"${IN_INITRD}" ||
	fail_now "Start-up image" "the test key isn't in ${INITRD}"
result pass "Install" "encrypted (LUKS2) install with /boot apart, made bootable in $(( $(date +%s) - START )) s"

sync
for M in dev/pts dev proc sys run boot/efi boot ""; do sudo umount "${MNT}/${M}" 2>/dev/null || sudo umount -l "${MNT}/${M}"; done
sudo cryptsetup close kulan_test_root
sudo losetup -d "${LOOP}"
LOOP=""
sudo chown "$(id -u):$(id -g)" "${WORK}/disk.img"

# --- Secure Boot firmware and an emulated TPM ---------------------------------------
CODE="$(ls /usr/share/OVMF/OVMF_CODE_4M.secboot.fd /usr/share/OVMF/OVMF_CODE.secboot.fd 2>/dev/null | head -n 1)"
VARS="$(ls /usr/share/OVMF/OVMF_VARS_4M.ms.fd /usr/share/OVMF/OVMF_VARS.ms.fd 2>/dev/null | head -n 1)"
[[ -n "${CODE}" && -n "${VARS}" ]] || fail_now "Firmware" "OVMF with Secure Boot and Microsoft's keys isn't installed"
cp "${VARS}" "${WORK}/vars.fd"
mkdir -p "${WORK}/tpm"
swtpm socket --tpm2 --tpmstate "dir=${WORK}/tpm" --ctrl "type=unixio,path=${WORK}/tpm/sock" \
	--log "file=${OUT}/swtpm.log,level=1" &
SWTPM_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -S "${WORK}/tpm/sock" ]] && break; sleep 1; done
[[ -S "${WORK}/tpm/sock" ]] || fail_now "TPM" "swtpm didn't start"

ACCEL=(-accel tcg)
if [[ -w /dev/kvm ]]; then
	ACCEL=(-enable-kvm -cpu host)
else
	WAIT_S=$((WAIT_S * 3))
fi
START=$(date +%s)
qemu-system-x86_64 "${ACCEL[@]}" -m 4096 -smp 2 -machine q35,smm=on \
	-global driver=cfi.pflash01,property=secure,value=on \
	-drive "if=pflash,format=raw,readonly=on,file=${CODE}" -drive "if=pflash,format=raw,file=${WORK}/vars.fd" \
	-chardev "socket,id=chrtpm,path=${WORK}/tpm/sock" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
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
# The first start reports too (before or after the setup step); the second
# start's report is the one that counts.
# Each start's report carries its boot_id; the setup step restarts the
# computer, so a second boot_id means the second start has reported. (The
# setup step's own lines can be cut short on the console as it restarts.)
boot_ids() { tr -d '\r' < "${OUT}/serial.log" 2>/dev/null | sed -n 's/^.*boot_id=\([0-9a-f-]*\).*$/\1/p' | awk '!seen[$0]++'; }
while (( $(date +%s) - START < WAIT_S )); do
	[[ "$(boot_ids | wc -l)" -ge 2 ]] && break
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

# --- Results ---------------------------------------------------------------------------
# The setup step's own log from /boot (see kulan-tpm-test-setup).
BOOT_LOOP="$(sudo losetup -fP --show "${WORK}/disk.img" 2>/dev/null || true)"
if [[ -n "${BOOT_LOOP}" ]]; then
	sudo mkdir -p "${WORK}/bootpart"
	if sudo mount -o ro "${BOOT_LOOP}p2" "${WORK}/bootpart" 2>/dev/null; then
		# shellcheck disable=SC2024  # the copy belongs to this user, on purpose
		sudo cat "${WORK}/bootpart/kulan-tpm-setup.log" > "${OUT}/setup.log" 2>/dev/null || true
		sudo umount "${WORK}/bootpart"
	fi
	sudo losetup -d "${BOOT_LOOP}"
fi
LOG="$(tr -d '\r' < "${OUT}/serial.log")"
if [[ -s "${OUT}/setup.log" ]]; then
	SETUP="$(tr -d '\r' < "${OUT}/setup.log")"
else
	SETUP="$(awk '/KULAN-TPM-SETUP-BEGIN/{f=1} f{print} /KULAN-TPM-SETUP-END/{exit}' <<<"${LOG}")"
fi
if [[ -z "${SETUP}" ]]; then
	fail_now "First start" "the setup step never ran: $(grep -aiE 'crypt|gave up|ALERT|not exist|unable|kulan_crypt' <<<"${LOG}" | tail -n 6 | tr '\n' ' ' | cut -c1-400) | last: $(tail -n 2 <<<"${LOG}" | tr '\n' ' ' | cut -c1-120)"
fi
if grep -q '"possible": true' <<<"${SETUP}"; then
	result pass "Ready for TPM unlock" "UEFI, Secure Boot on, TPM 2.0 and a LUKS2 system disk found"
else
	result fail "Ready for TPM unlock" "kulan-tpm says no: $(grep -A4 '"reasons"' <<<"${SETUP}" | tr -s ' ' | tr '\n' ' ' | cut -c1-250)"
fi
if grep -qx 'tpm_on_exit=0' <<<"${SETUP}"; then
	result pass "kulan-tpm on" "dracut installed, TPM key added and checked, start-up image rebuilt"
else
	result fail "kulan-tpm on" "$(grep -v '^slot\|^{\|^ \|^}' <<<"${SETUP}" | grep -v KULAN | grep -v '^\s*$' | tail -n 8 | tr '\n' ' ' | cut -c1-600)"
fi
SLOTS="$(sed -n 's/^slot //p' <<<"${SETUP}" | tr '\n' ' ')"
if grep -qx 'key_removed=0' <<<"${SETUP}" && grep -q 'tpm2-device=auto' <<<"${SETUP}" && grep -q ' none ' <<<"$(grep '^crypttab=' <<<"${SETUP}")"; then
	result pass "Only TPM or password left" "test key removed; crypttab: $(sed -n 's/^crypttab=//p' <<<"${SETUP}"); slots: ${SLOTS}"
else
	result fail "Only TPM or password left" "$(grep -E '^(key_removed|dracut|crypttab)=' <<<"${SETUP}" | tr '\n' ' ')"
fi
# The second start's report: the block whose boot_id is the second one seen.
SECOND_ID="$(boot_ids | sed -n 2p)"
SECOND=""
if [[ -n "${SECOND_ID}" ]]; then
	SECOND="$(awk -v id="boot_id=${SECOND_ID}" '/KULAN-BOOT-REPORT-BEGIN/{buf=""; r=1} r{buf=buf $0 "\n"} r && /KULAN-BOOT-REPORT-END/{r=0; if (index(buf, id)) {printf "%s", buf; exit}}' <<<"${LOG}")"
fi
if [[ -n "${SECOND}" ]] && grep -qx 'desktop_user=kulan' <<<"${SECOND}"; then
	result pass "Start with TPM unlock" "the encrypted disk opened by itself and the desktop came up (${ELAPSED} s for both starts)"
	UNLOCK="$(sed -n 's/^unlock=//p' <<<"${SECOND}")"
	if [[ "${UNLOCK}" == *TPM2* ]]; then
		result pass "Opened by the TPM" "${UNLOCK:0:200}"
	else
		result info "Opened by the TPM" "the system log didn't say how the disk was opened: ${UNLOCK:-nothing}"
	fi
else
	result fail "Start with TPM unlock" "no desktop after the restart within ${ELAPSED} s (waiting for a password?): $(tail -n 3 <<<"${LOG}" | tr '\n' ' ' | cut -c1-200)"
	# Everything the console showed after the first start's report: the
	# restart, the start-up image and any password prompt.
	AFTER="$(awk '/KULAN-BOOT-REPORT-END/{buf=""; next} {buf=buf $0 "\n"} END{printf "%s", buf}' <<<"${LOG}" |
		sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | tr -cd '\11\12\40-\176' | grep -v '^[[:space:]]*$' | cut -c1-200 | tail -n 60)"
	echo "::error title=TPM test: console after the restart::$(printf '%s' "${AFTER:-nothing at all on the console after the first start}" | sed 's/%/%25/g' | sed ':a;N;$!ba;s/\n/%0A/g')"
fi
summary
exit "${FAILED}"
