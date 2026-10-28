#!/bin/bash
# Signed start-up, end to end, on a virtual computer with Secure Boot (OVMF
# with Microsoft's keys) and an emulated TPM 2.0 (swtpm):
#
#   start 1  GRUB. A one-off test step runs `kulan-signed-start on` (the key,
#            the signed start-up files, shim's request to enroll the key) and
#            restarts.
#   blue     shim's MOK manager: the test presses the keys a person would
#            (Enroll MOK, Continue, Yes, the code, Reboot).
#   start 2  GRUB still. kulan-signed-start.service sees the key enrolled and
#            puts the "Kulan OS" entry first; the test step restarts.
#   start 3  shim, systemd-boot and the signed start-up file. The service
#            removes GRUB. The test step turns TPM unlock on (now tied to the
#            signed start-up through PCR 7), takes the test key away and restarts.
#   start 4  The signed start-up file again, and the TPM opens the disk.
#
# The disk opens with a test key file in the start-up image until start 3
# (only so the test needn't type the password), as in ci/tpm-test.sh.
# kulan.boot-report reports each start (how it started, signed start-up's
# step, how the disk was opened).
#
# Usage: ci/signed-start-test.sh <iso> [output-dir]      (runs commands with sudo)
# Needs: qemu-system-x86, ovmf, swtpm, swtpm-tools, cryptsetup, squashfs-tools,
#        xorriso, dosfstools, fdisk, python3-pil
set -uo pipefail

ISO="${1:?usage: $0 <iso> [output-dir]}"
OUT="${2:-signed-start-test}"
WAIT_S="${KULAN_SIGNED_WAIT:-2400}"
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
		pass) echo "::notice title=Signed start-up test: $2::$3"; ROWS+=("| ✅ | $2 | $3 |") ;;
		info) echo "::notice title=Signed start-up test: $2::$3"; ROWS+=("| ℹ️ | $2 | $3 |") ;;
		*) echo "::error title=Signed start-up test: $2::$3"; ROWS+=("| ❌ | $2 | $3 |"); FAILED=1 ;;
	esac
}

summary() {
	{
		echo "### Kulan OS signed start-up test (Secure Boot, the key enrolled through shim, TPM unlock)"
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

# --- Install onto an encrypted disk (as ci/tpm-test.sh) ------------------------------
xorriso -osirrox on -indev "${ISO}" -extract /live/filesystem.squashfs "${WORK}/fs.squashfs" >/dev/null 2>&1 ||
	fail_now "Live filesystem" "couldn't read live/filesystem.squashfs from the ISO"
truncate -s 24G "${WORK}/disk.img"
printf 'label: gpt\n,512M,U\n,1G,L\n,,L\n' | sfdisk -q "${WORK}/disk.img" >/dev/null || fail_now "Disk" "partitioning failed"
LOOP="$(sudo losetup -fP --show "${WORK}/disk.img")" || fail_now "Disk" "no loop device"
sudo udevadm settle 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -b "${LOOP}p3" ]] && break; sleep 1; done
[[ -b "${LOOP}p3" ]] || fail_now "Disk" "the partitions of ${LOOP} didn't appear"
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

# The test key, in the start-up image (initramfs-tools' now, dracut's once
# signed start-up puts dracut in) until start 3.
sudo dd if=/dev/urandom of="${MNT}/etc/kulan-test.key" bs=64 count=1 status=none
sudo chmod 0400 "${MNT}/etc/kulan-test.key"
printf '%s' "${PASSWORD}" | sudo cryptsetup luksAddKey --key-file=- "${LOOP}p3" "${MNT}/etc/kulan-test.key" ||
	fail_now "Encryption" "couldn't add the test key"
printf 'kulan_crypt UUID=%s /etc/kulan-test.key luks,discard,initramfs\n' "${LUKS_UUID}" | sudo tee "${MNT}/etc/crypttab" >/dev/null
echo 'UMASK=0077' | sudo tee "${MNT}/etc/initramfs-tools/conf.d/kulan-test-umask" >/dev/null
sudo mkdir -p "${MNT}/etc/dracut.conf.d"
echo 'install_items+=" /etc/kulan-test.key "' | sudo tee "${MNT}/etc/dracut.conf.d/90-kulan-test-key.conf" >/dev/null
printf '%s\n' "${PASSWORD}" | sudo tee "${MNT}/root/kulan-test-password" >/dev/null
sudo chmod 0400 "${MNT}/root/kulan-test-password"

{
	in_root apt-get purge -y -q live-boot live-boot-initramfs-tools live-config live-config-systemd live-tools \
		calamares calamares-settings-debian
	in_root apt-get autoremove -y -q
} >"${OUT}/install.log" 2>&1 || result info "Live packages" "some live packages couldn't be removed (see install.log)"
sudo rm -f "${MNT}/etc/machine-id" "${MNT}/var/lib/dbus/machine-id"
in_root systemd-machine-id-setup >/dev/null 2>&1
echo kulan-signed-test | sudo tee "${MNT}/etc/hostname" >/dev/null
printf '127.0.0.1\tlocalhost\n127.0.1.1\tkulan-signed-test\n' | sudo tee "${MNT}/etc/hosts" >/dev/null
in_root useradd -m -s /bin/zsh -G sudo,adm -c "Kulan Test" kulan >>"${OUT}/install.log" 2>&1 ||
	fail_now "Account" "couldn't create the account"
echo 'kulan:kulan-test-pw' | in_root chpasswd
sudo mkdir -p "${MNT}/etc/gdm3"
printf '[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=kulan\n' | sudo tee "${MNT}/etc/gdm3/daemon.conf" >/dev/null

# The test step, at every start, after kulan-signed-start.service: what to do
# next depends on signed start-up's step. Its lines go to the serial console
# and to /boot/kulan-signed-test.log.
sudo tee "${MNT}/usr/local/sbin/kulan-signed-test-step" >/dev/null <<EOF
#!/bin/bash
exec > >(tee -a /boot/kulan-signed-test.log >/dev/ttyS0) 2>&1
say() { echo "KULAN-SIGNED-TEST \$*"; }
restart() { sync; sleep 2; systemctl reboot --check-inhibitors=no || systemctl reboot --force; }
STATUS="\$(kulan-signed-start status --json)"
PHASE="\$(python3 -c 'import json,sys; print(json.load(sys.stdin)["phase"])' <<<"\${STATUS}")"
say "phase=\${PHASE} started_signed=\$(python3 -c 'import json,sys; print(json.load(sys.stdin)["started_signed"])' <<<"\${STATUS}")"
case "\${PHASE}" in
	off)
		if [ -e /var/lib/kulan-signed-test-on ]; then say "on-again: it went back to off"; exit 0; fi
		touch /var/lib/kulan-signed-test-on
		kulan-signed-start on >/run/kulan-signed-on.out 2>/boot/kulan-signed-on.err
		RC=\$?
		say "on_exit=\${RC} code=\$(tail -n 1 /run/kulan-signed-on.out)"
		tail -n 5 /boot/kulan-signed-on.err | sed 's/^/KULAN-SIGNED-TEST on: /'
		say "esp=\$(ls /boot/efi/EFI/kulan /boot/efi/EFI/Linux 2>&1 | tr '\n' ' ')"
		say "verify=\$(sbverify --cert /var/lib/kulan/signed-start/kulan.crt /boot/efi/EFI/Linux/kulan-*.efi 2>&1 | tail -n 1)"
		say "mok_new=\$(mokutil --list-new 2>/dev/null | grep -c 'Kulan OS signed start-up')"
		restart
		;;
	switch)
		say "bootorder=\$(efibootmgr | sed -n 's/^BootOrder: //p') entry=\$(efibootmgr | grep -c 'Kulan OS')"
		restart
		;;
	on)
		if [ -e /var/lib/kulan-signed-test-tpm ]; then
			say "done"
			exit 0
		fi
		touch /var/lib/kulan-signed-test-tpm
		say "grub_left=\$(ls /boot/efi/EFI/debian 2>/dev/null | wc -l) grub_pkg=\$(dpkg-query -W -f='\\\${Status}' grub-efi-amd64-signed 2>/dev/null)"
		say "fallback=\$(cmp -s /boot/efi/EFI/BOOT/BOOTX64.EFI /usr/lib/shim/shimx64.efi.signed && echo shim || echo other)"
		head -n 1 /root/kulan-test-password | kulan-tpm on; say "tpm_on_exit=\$?"
		sed -i 's#^\(kulan_crypt [^ ]*\) /etc/kulan-test.key #\1 none #' /etc/crypttab
		cryptsetup luksRemoveKey "\$(blkid -t UUID=${LUKS_UUID} -o device)" /etc/kulan-test.key; say "key_removed=\$?"
		rm -f /etc/kulan-test.key /etc/dracut.conf.d/90-kulan-test-key.conf
		dracut --force --regenerate-all >/dev/null 2>&1 && kulan-signed-start rebuild; say "rebuilt=\$?"
		restart
		;;
	*)
		say "waiting (\${PHASE}): \$(kulan-signed-start status | tr '\n' ' ')"
		say "requested=\$(mokutil --list-new 2>&1 | grep -ciE 'fingerprint|kulan') enrolled=\$(mokutil --test-key /var/lib/kulan/signed-start/kulan.der 2>&1 | tr '\n' ' ')"
		say "esp_boot=\$(ls /boot/efi/EFI/BOOT 2>&1 | tr '\n' ' ')"
		journalctl -b -u kulan-signed-start.service -o cat --no-pager 2>/dev/null | tail -n 4 | sed 's/^/KULAN-SIGNED-TEST service: /'
		if [ "\$(cat /var/lib/kulan-signed-test-waits 2>/dev/null || echo 0)" -ge 4 ]; then say "giving up"; exit 0; fi
		echo \$(( \$(cat /var/lib/kulan-signed-test-waits 2>/dev/null || echo 0) + 1 )) > /var/lib/kulan-signed-test-waits
		restart
		;;
esac
EOF
sudo chmod 755 "${MNT}/usr/local/sbin/kulan-signed-test-step"
printf '[Unit]\nDescription=Kulan signed start-up test: the next step\nAfter=kulan-signed-start.service network-online.target\nWants=network-online.target\n\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/kulan-signed-test-step\n\n[Install]\nWantedBy=multi-user.target\n' |
	sudo tee "${MNT}/etc/systemd/system/kulan-signed-test-step.service" >/dev/null
in_root systemctl enable kulan-signed-test-step.service >>"${OUT}/install.log" 2>&1

sudo mkdir -p "${MNT}/etc/default/grub.d"
printf 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} console=tty0 console=ttyS0,115200 kulan.boot-report"\n' |
	sudo tee "${MNT}/etc/default/grub.d/zz-kulan-signed-test.cfg" >/dev/null
{
	in_root apt-get update -q &&
		in_root apt-get install -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
			grub-efi-amd64 grub-efi-amd64-signed shim-signed cryptsetup-initramfs efibootmgr &&
		echo 'KEYFILE_PATTERN="/etc/kulan-test.key"' | sudo tee -a "${MNT}/etc/cryptsetup-initramfs/conf-hook" >/dev/null &&
		echo 'CRYPTSETUP=y' | sudo tee -a "${MNT}/etc/cryptsetup-initramfs/conf-hook" >/dev/null &&
		in_root update-initramfs -u -k all &&
		in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --uefi-secure-boot --removable --no-nvram &&
		in_root update-grub
} >>"${OUT}/install.log" 2>&1 || fail_now "Boot loader" "GRUB setup failed: $(grep -E 'error|E:' "${OUT}/install.log" | tail -n 3 | tr '\n' ' ' | cut -c1-300)"
result pass "Install" "encrypted (LUKS2) install with GRUB and shim, as before signed start-up, in $(( $(date +%s) - START )) s"

sync
for M in dev/pts dev proc sys run boot/efi boot ""; do sudo umount "${MNT}/${M}" 2>/dev/null || sudo umount -l "${MNT}/${M}"; done
sudo cryptsetup close kulan_test_root
sudo losetup -d "${LOOP}"
LOOP=""
sudo chown "$(id -u):$(id -g)" "${WORK}/disk.img"

# --- Secure Boot firmware and an emulated TPM -------------------------------------
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

# The VM's keyboard and screen, through QEMU's monitor.
qmp() { # qmp <python statements using call()>
	python3 - "${WORK}/qmp.sock" "$1" <<'PY' || true
import json, socket, sys, time
sock = socket.socket(socket.AF_UNIX); sock.settimeout(20); sock.connect(sys.argv[1])
f = sock.makefile("rw")
def call(cmd, **args):
    f.write(json.dumps({"execute": cmd, "arguments": args} if args else {"execute": cmd}) + "\n"); f.flush()
    while True:
        reply = json.loads(f.readline())
        if "return" in reply or "error" in reply:
            return reply
def keys(*names, wait=1.5):
    for name in names:
        call("send-key", keys=[{"type": "qcode", "data": name}])
        time.sleep(0.3)
    time.sleep(wait)
json.loads(f.readline()); call("qmp_capabilities")
exec(sys.argv[2])
PY
}
shot() { # shot <name>: the screen, as PNG in the output folder
	qmp "call('screendump', filename='${WORK}/screen.ppm')"
	[[ -s "${WORK}/screen.ppm" ]] && python3 -c "from PIL import Image; import sys; Image.open(sys.argv[1]).save(sys.argv[2])" \
		"${WORK}/screen.ppm" "${OUT}/$1.png" 2>/dev/null
	rm -f "${WORK}/screen.ppm"
}
serial() { tr -d '\r' < "${OUT}/serial.log" 2>/dev/null; }

# The MOK manager, the way a person goes through it: any key (its 10-second
# wait), Enroll MOK, Continue, Yes, the code and Enter, Reboot.
enroll() {
	local code="$1"
	shot mok-1-wait
	qmp "keys('spc')"
	shot mok-2-menu
	qmp "keys('down', 'ret')"
	shot mok-3-enroll
	qmp "keys('down', 'ret')"
	shot mok-4-confirm
	qmp "keys('down', 'ret')"
	shot mok-5-password
	qmp "keys(*list('${code}'), 'ret', wait=3)"
	shot mok-6-done
	qmp "keys('ret')"
}

# After each restart that follows a code, watch the screen (read with
# tesseract) for shim's MOK manager, and enroll with the newest code (a skipped
# screen is asked again with a new one).
screen_text() {
	qmp "call('screendump', filename='${WORK}/watch.ppm')"
	[[ -s "${WORK}/watch.ppm" ]] || return 0
	tesseract "${WORK}/watch.ppm" - 2>/dev/null | tr '\n' ' '
	rm -f "${WORK}/watch.ppm"
}
ENROLLED=0
WATCHED=0
while (( $(date +%s) - START < WAIT_S )); do
	kill -0 "${QEMU_PID}" 2>/dev/null || break
	LOG_NOW="$(serial)"
	grep -q 'KULAN-SIGNED-TEST \(done\|giving up\)' <<<"${LOG_NOW}" && break
	CODE_NOW="$(grep -oE '(code=|code )[0-9]{8}' <<<"${LOG_NOW}" | tail -n 1 | grep -oE '[0-9]{8}')"
	RESTARTS="$(grep -c 'reboot: Restarting system' <<<"${LOG_NOW}")"
	if [[ -n "${CODE_NOW}" ]] && (( RESTARTS > WATCHED )) && (( ENROLLED < 3 )); then
		WATCHED="${RESTARTS}"
		# The next 60 seconds: the firmware, then shim (its screen waits 10 seconds).
		for i in $(seq 1 60); do
			TEXT="$(screen_text)"
			if grep -qiE 'MOK management|Press any key|Shim UEFI' <<<"${TEXT}"; then
				echo "MOK screen after ${i} s: ${TEXT:0:200}" >> "${OUT}/watch.log"
				enroll "${CODE_NOW}"
				ENROLLED=$((ENROLLED + 1))
				break
			fi
			(( i % 10 == 1 )) && echo "${i} s: ${TEXT:0:160}" >> "${OUT}/watch.log"
			sleep 0.5
		done
	fi
	sleep 2
done
ELAPSED=$(( $(date +%s) - START ))
shot last-screen

# --- Results ------------------------------------------------------------------------
# The test step's log from /boot (every line, even ones the console lost), and
# the firmware and shim lines of the console.
BOOT_LOOP="$(sudo losetup -fP --show "${WORK}/disk.img" 2>/dev/null || true)"
if [[ -n "${BOOT_LOOP}" ]]; then
	sudo mkdir -p "${WORK}/bootpart"
	if sudo mount -o ro "${BOOT_LOOP}p2" "${WORK}/bootpart" 2>/dev/null; then
		# shellcheck disable=SC2024  # the copies belong to this user, on purpose
		{ sudo cat "${WORK}/bootpart/kulan-signed-test.log"; echo "--- on.err"; sudo cat "${WORK}/bootpart/kulan-signed-on.err"; } > "${OUT}/test-step.log" 2>/dev/null || true
		sudo umount "${WORK}/bootpart"
	fi
	sudo losetup -d "${BOOT_LOOP}"
fi
serial | grep -aiE 'shim|mok|mmx64|security violation|verification|BdsDxe|systemd-boot|Failed to' | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | head -n 200 > "${OUT}/firmware.log"
LOG="$(serial)"
STEPS="$(grep -a 'KULAN-SIGNED-TEST' <<<"${LOG}" | sed 's/.*KULAN-SIGNED-TEST //')"
printf '%s\n' "${STEPS}" > "${OUT}/steps.log"
reports() { awk '/KULAN-BOOT-REPORT-BEGIN/ {r=1; buf=""} r {buf=buf $0 "\n"} r && /KULAN-BOOT-REPORT-END/ {printf "%s===\n", buf; r=0}' <<<"${LOG}"; }
reports > "${OUT}/reports.log"

if grep -q '^on_exit=0 code=[0-9]\{8\}' <<<"${STEPS}"; then
	result pass "kulan-signed-start on" "key made, start-up files signed ($(sed -n 's/^verify=//p' <<<"${STEPS}" | head -n 1)), shim asked to enroll the key"
else
	fail_now "kulan-signed-start on" "$(grep -E '^(on_exit|on:)' <<<"${STEPS}" | tail -n 6 | tr '\n' ' ' | cut -c1-500)"
fi
if (( ENROLLED )); then
	result pass "Blue screen" "shim's MOK manager came up after the restart; the code was typed (screens: mok-*.png)"
else
	result fail "Blue screen" "shim's MOK manager never showed on the console after the restart"
fi
if grep -q '^bootorder=' <<<"${STEPS}"; then
	result pass "Key enrolled" "the next start was set to go through the signed start-up files ($(grep '^bootorder=' <<<"${STEPS}" | head -n 1))"
else
	result fail "Key enrolled" "kulan-signed-start never got to the switch: $(grep '^phase=\|^waiting' <<<"${STEPS}" | tail -n 3 | tr '\n' ' ')"
fi
SIGNED="$(awk '/^loader=/ && /kulan/ && /systemd-boot/ && !/stub= / {print; exit}' "${OUT}/reports.log")"
if [[ -n "${SIGNED}" ]]; then
	result pass "Started signed" "${SIGNED:0:220}"
else
	result fail "Started signed" "no start through shim, systemd-boot and a signed start-up file: $(grep '^loader=' "${OUT}/reports.log" | tail -n 2 | tr '\n' ' ' | cut -c1-300)"
fi
if grep -q '^grub_left=0 ' <<<"${STEPS}"; then
	result pass "GRUB removed" "$(grep '^grub_left=' <<<"${STEPS}" | head -n 1); $(grep '^fallback=' <<<"${STEPS}" | head -n 1)"
else
	result fail "GRUB removed" "$(grep -E '^(grub_left|fallback)=' <<<"${STEPS}" | tr '\n' ' ')"
fi
if grep -qx 'tpm_on_exit=0' <<<"${STEPS}" && grep -qx 'key_removed=0' <<<"${STEPS}" && grep -qx 'rebuilt=0' <<<"${STEPS}"; then
	result pass "TPM unlock on" "with the signed start-up (PCR 7), test key removed, start-up files signed again"
else
	result fail "TPM unlock on" "$(grep -E '^(tpm_on_exit|key_removed|rebuilt)=' <<<"${STEPS}" | tr '\n' ' ')"
fi
LAST="$(awk 'BEGIN {RS="===\n"} NF {last=$0} END {print last}' "${OUT}/reports.log")"
if grep -q '^KULAN-SIGNED-TEST done' <<<"${LOG}" || grep -qx 'done' <<<"${STEPS}"; then
	UNLOCK="$(sed -n 's/^unlock=//p' <<<"${LAST}")"
	LOADER="$(sed -n 's/^loader=//p' <<<"${LAST}")"
	if [[ "${LOADER}" == *kulan* && "${UNLOCK}" == *TPM2* ]]; then
		result pass "Signed start, TPM unlock" "the signed start-up file started, the TPM opened the disk, the desktop came up (${ELAPSED} s for all starts)"
	elif [[ "${LOADER}" == *kulan* ]]; then
		result info "Signed start, TPM unlock" "signed start reached the desktop; the log didn't say how the disk was opened: ${UNLOCK:-nothing}"
	else
		result fail "Signed start, TPM unlock" "the last start wasn't signed: ${LOADER}"
	fi
else
	result fail "Signed start, TPM unlock" "no desktop after the last restart within ${ELAPSED} s: $(tail -n 3 <<<"${LOG}" | tr '\n' ' ' | cut -c1-200)"
fi
summary
exit "${FAILED}"
