#!/bin/bash
# Upgrade test: install the previous release (its PC ISO, with that ISO's own
# Kulan Installer engine), start it, update it to this build's packages the
# way Software Update does (update-run check, then install --restore-point),
# restart, and check the updated system reaches the desktop with no failed
# services, every Kulan package at this build's version and nothing left
# half-installed.
#
# This build's packages are served to the virtual machine from this runner
# (http://10.0.2.2:8765/), signed with a key made for this test only and
# thrown away afterwards. The installed system's own update channel stays as
# it is (still online, still the previous release), so apt has to pick this
# build's newer versions over it, as it will on people's computers.
#
# Usage: ci/upgrade-test.sh <previous-release-iso> <debs-dir> [output-dir]   (runs commands with sudo)
# Needs: qemu-system-x86, ovmf, squashfs-tools, xorriso, dosfstools, fdisk,
#        apt-utils, gnupg
set -uo pipefail

ISO="${1:?usage: $0 <previous-release-iso> <debs-dir> [output-dir]}"
DEBS="$(cd "${2:?usage: $0 <previous-release-iso> <debs-dir> [output-dir]}" && pwd)"
OUT="${3:-upgrade-test}"
WAIT_S="${KULAN_UPGRADE_WAIT:-3600}" # two starts, a restore point, and the update itself
PORT=8765
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"
WORK="$(mktemp -d)"
MNT="${WORK}/root"
LOOP=""
QEMU_PID=""
HTTP_PID=""
FAILED=0
ROWS=()

# shellcheck disable=SC2317  # called by the EXIT trap
cleanup() {
	[[ -n "${QEMU_PID}" ]] && kill "${QEMU_PID}" 2>/dev/null
	[[ -n "${HTTP_PID}" ]] && kill "${HTTP_PID}" 2>/dev/null
	mountpoint -q "${MNT}" 2>/dev/null && sudo umount -l "${MNT}"
	[[ -n "${LOOP}" ]] && sudo losetup -d "${LOOP}" 2>/dev/null
	sudo rm -rf "${WORK}"
}
trap cleanup EXIT

result() { # result <pass|fail|info> <check> <detail>
	case "$1" in
		pass) echo "::notice title=Upgrade test: $2::$3"; ROWS+=("| ✅ | $2 | $3 |") ;;
		info) echo "::notice title=Upgrade test: $2::$3"; ROWS+=("| ℹ️ | $2 | $3 |") ;;
		*) echo "::error title=Upgrade test: $2::$3"; ROWS+=("| ❌ | $2 | $3 |"); FAILED=1 ;;
	esac
}

summary() {
	{
		echo "### Kulan OS upgrade test (previous release, updated through Software Update)"
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

# --- This build's packages, as a signed repository --------------------------------
shopt -s nullglob
FILES=("${DEBS}"/*.deb)
[[ ${#FILES[@]} -gt 0 ]] || fail_now "Packages" "no packages in ${DEBS}"
REPO="${WORK}/repo"
mkdir -p "${REPO}"
cp "${FILES[@]}" "${REPO}/"
export GNUPGHOME="${WORK}/gnupg"
mkdir -m 700 "${GNUPGHOME}"
gpg --batch --quiet --passphrase '' --quick-gen-key "Kulan OS upgrade test (throwaway)" ed25519 sign 1d ||
	fail_now "Test key" "couldn't make the test signing key"
gpg --batch --export > "${WORK}/upgrade-test.gpg"
(
	cd "${REPO}" &&
		apt-ftparchive packages . > Packages &&
		gzip -9nk Packages &&
		apt-ftparchive -o APT::FTPArchive::Release::Origin=kulan-upgrade-test release . > Release &&
		gpg --batch --yes --clearsign --output InRelease Release
) || fail_now "Test repository" "apt-ftparchive or signing failed"
declare -A NEW
for DEB in "${FILES[@]}"; do
	NEW["$(dpkg-deb -f "${DEB}" Package)"]="$(dpkg-deb -f "${DEB}" Version)"
done
python3 -m http.server "${PORT}" --bind 0.0.0.0 --directory "${REPO}" >"${OUT}/http.log" 2>&1 &
HTTP_PID=$!

# --- Install the previous release, with its own engine ----------------------------
xorriso -osirrox on -indev "${ISO}" -extract /live/filesystem.squashfs "${WORK}/fs.squashfs" >/dev/null 2>&1 ||
	fail_now "Previous release" "couldn't read live/filesystem.squashfs from ${ISO}"
mkdir -p "${WORK}/medium"
xorriso -osirrox on -indev "${ISO}" -extract /pool "${WORK}/medium/pool" -extract /dists "${WORK}/medium/dists" \
	>/dev/null 2>&1 || fail_now "Package pool" "the previous release's ISO has no package pool"
sudo unsquashfs -q -n -d "${WORK}/old" "${WORK}/fs.squashfs" usr/lib/kulan/installer-engine usr/lib/os-release >/dev/null ||
	fail_now "Previous installer" "couldn't take Kulan Installer's engine from the previous release"
ENGINE="${WORK}/old/usr/lib/kulan/installer-engine"
OLD_NAME="$(sed -n 's/^PRETTY_NAME=//p' "${WORK}/old/usr/lib/os-release" | tr -d '"')"

truncate -s 24G "${WORK}/disk.img"
LOOP="$(sudo losetup -fP --show "${WORK}/disk.img")" || fail_now "Disk" "no loop device"
PLAN="$(python3 - "${LOOP}" "${WORK}" <<'PY'
import json, sys
loop, work = sys.argv[1], sys.argv[2]
print(json.dumps({
    "disk": loop, "mode": "erase", "encrypt": False, "locale": "en_US.UTF-8",
    "keyboard": {"layout": "us", "variant": ""}, "timezone": "America/New_York", "hostname": "kulan-upgrade",
    "user": {"full_name": "Kulan Test", "username": "kulan", "password": "kulan-test-pw", "autologin": True},
    "source": f"{work}/fs.squashfs", "medium": f"{work}/medium", "firmware": "uefi",
    "kernel_args": "console=tty0 console=ttyS0,115200 kulan.boot-report kulan.upgrade-test",
    "efi_removable": True, "no_nvram": True, "require_pool": True,
}))
PY
)"
# shellcheck disable=SC2024  # the logs belong to this user, on purpose
sudo "${ENGINE}" install <<<"${PLAN}" >"${OUT}/engine.jsonl" 2>"${OUT}/install.log"
LAST="$(tail -n 1 "${OUT}/engine.jsonl")"
grep -q '"done": true' <<<"${LAST}" ||
	fail_now "Install previous release" "$(sed -n 's/.*"error": "\(.*\)".*/\1/p' <<<"${LAST}")"
result pass "Install previous release" "${OLD_NAME:-the previous release}, with its own Kulan Installer engine"

# --- Point it at this build, and add the test's helper ----------------------------
sudo partprobe "${LOOP}" 2>/dev/null || true
mkdir -p "${MNT}"
ROOT_PART="$(lsblk -lnpo NAME,PARTLABEL "${LOOP}" | awk '$2 == "Kulan" {print $1}' | head -n 1)"
[[ -n "${ROOT_PART}" ]] || ROOT_PART="${LOOP}p2"
sudo mount "${ROOT_PART}" "${MNT}" || fail_now "Installed system" "couldn't open the installed system"
sudo test -f "${MNT}/etc/apt/sources.list.d/kulan.sources" ||
	result fail "Update channel" "the previous release has no update channel (kulan.sources)"
sudo install -D -m 0644 "${WORK}/upgrade-test.gpg" "${MNT}/etc/apt/keyrings/kulan-upgrade-test.gpg"
sudo tee "${MNT}/etc/apt/sources.list.d/kulan-upgrade-test.sources" >/dev/null <<EOF
# This build's packages (upgrade test only).
Types: deb
URIs: http://10.0.2.2:${PORT}/
Suites: ./
Signed-By: /etc/apt/keyrings/kulan-upgrade-test.gpg
EOF
sudo tee "${MNT}/usr/local/sbin/kulan-upgrade-test" >/dev/null <<'EOF'
#!/bin/bash
# Upgrade test (CI only): first start, update as Software Update does and
# restart; second start, say what the updated system looks like.
OUT=/dev/ttyS0
STATE=/var/lib/kulan-upgrade-test
say() { printf 'KULAN-UPGRADE: %s\n' "$*" > "${OUT}"; }
desktop() {
	for PID in $(pgrep -x gnome-shell); do
		ID="$(stat -c %u "/proc/${PID}" 2>/dev/null)" || continue
		[ "${ID}" -ge 1000 ] && [ "${ID}" -lt 60000 ] && return 0
	done
	return 1
}
for _ in $(seq 1 300); do
	S="$(systemctl is-system-running 2>/dev/null)"
	desktop && [ "${S}" != starting ] && [ "${S}" != initializing ] && break
	sleep 1
done
sleep 30 # the boot report goes first
mkdir -p "${STATE}"
kulan_versions() { dpkg-query -W -f '${db:Status-Abbrev}${Package}=${Version}\n' 'kulan*' 'nautilus*' 'libnautilus*' 2>/dev/null | sed -n 's/^ii *//p' | tr '\n' ' '; }
if [ ! -e "${STATE}/updated" ]; then
	say "phase=update"
	say "before=$(kulan_versions)"
	/usr/lib/kulan/update-run check >"${STATE}/check.out" 2>"${STATE}/check.err"
	say "check=$?"
	tail -n 5 "${STATE}/check.err" | sed 's/^/KULAN-UPGRADE: checklog=/' > "${OUT}"
	say "offered=$(python3 -c 'import json; d = json.load(open("/var/lib/kulan/updates.json")); print(" ".join(sorted(u.get("name", "") for u in d.get("available", []) if isinstance(u, dict))))' 2>&1 | tr '\n' ' ')"
	START=$(date +%s)
	/usr/lib/kulan/update-run install --restore-point >"${STATE}/install.out" 2>"${STATE}/install.err"
	say "install=$? $(( $(date +%s) - START ))"
	tail -n 12 "${STATE}/install.err" | sed 's/^/KULAN-UPGRADE: installlog=/' > "${OUT}"
	# What the restore point step said (timeshift's own words), up to the skip.
	grep -B 14 "Restore point skipped" "${STATE}/install.err" | sed 's/^/KULAN-UPGRADE: restorelog=/' > "${OUT}"
	if grep -q "Restore point skipped" "${STATE}/install.err"; then say "restorepoint=skipped"; else say "restorepoint=made"; fi
	say "restart_required=$(paste -sd ' ' /run/kulan/restart-required 2>/dev/null)"
	touch "${STATE}/updated"
	sync
	say "phase=restarting"
	systemctl reboot
elif [ ! -e "${STATE}/checked" ]; then
	touch "${STATE}/checked"
	say "phase=after"
	say "after=$(kulan_versions)"
	say "audit=$(dpkg --audit 2>&1 | grep -c .)"
	say "aptcheck=$(apt-get -q check >/dev/null 2>&1 && echo ok || echo broken)"
	say "half=$(dpkg-query -W -f '${db:Status-Abbrev} ${Package}\n' | grep -vE '^(ii|rc) ' | awk '{print $2}' | tr '\n' ' ')"
	say "pending=$(apt-get -s -q -o Debug::NoLocking=1 --with-new-pkgs upgrade 2>/dev/null | awk '/^Inst /{print $2}' | grep -E '^(kulan|nautilus|libnautilus)' | tr '\n' ' ')"
	say "system=$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | tr -d '"')"
	say "done"
fi
EOF
sudo chmod 0755 "${MNT}/usr/local/sbin/kulan-upgrade-test"
sudo tee "${MNT}/etc/systemd/system/kulan-upgrade-test.service" >/dev/null <<'EOF'
[Unit]
Description=Kulan OS upgrade test (CI only)
ConditionKernelCommandLine=kulan.upgrade-test
After=multi-user.target network-online.target
Wants=network-online.target

[Service]
# Not oneshot: start-up must finish (the boot report measures it).
Type=simple
ExecStart=/usr/local/sbin/kulan-upgrade-test

[Install]
WantedBy=multi-user.target
EOF
sudo mkdir -p "${MNT}/etc/systemd/system/multi-user.target.wants"
sudo ln -sf /etc/systemd/system/kulan-upgrade-test.service \
	"${MNT}/etc/systemd/system/multi-user.target.wants/kulan-upgrade-test.service"
sudo umount "${MNT}"
sync
sudo losetup -d "${LOOP}"
LOOP=""
sudo chown "$(id -u):$(id -g)" "${WORK}/disk.img"

# --- Start, update, restart -------------------------------------------------------
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
	-netdev user,id=net0 -device virtio-net-pci,netdev=net0 -device virtio-rng-pci &
QEMU_PID=$!
while (( $(date +%s) - START < WAIT_S )); do
	grep -q "KULAN-UPGRADE: done" "${OUT}/serial.log" 2>/dev/null && break
	kill -0 "${QEMU_PID}" 2>/dev/null || break
	sleep 10
done
ELAPSED=$(( $(date +%s) - START ))
kill "${QEMU_PID}" 2>/dev/null
QEMU_PID=""

# --- What happened ----------------------------------------------------------------
LOG="$(tr -d '\r' < "${OUT}/serial.log")"
say_field() { sed -n "s/^KULAN-UPGRADE: $1=//p" <<<"${LOG}" | head -n 1; }
report() { awk -v n="$1" '/KULAN-BOOT-REPORT-BEGIN/ {c++} c == n {print} /KULAN-BOOT-REPORT-END/ && c == n {exit}' <<<"${LOG}"; }
field() { sed -n "s/^$2=//p" <<<"$1" | head -n 1; }

R1="$(report 1)"
if [[ "$(field "${R1}" desktop_user)" == kulan ]]; then
	result pass "Previous release starts" "desktop for kulan; $(field "${R1}" time)"
else
	fail_now "Previous release starts" "no desktop on the first start (serial log: $(tail -n 3 <<<"${LOG}" | tr '\n' ' ' | cut -c1-200))"
fi

CHECK="$(say_field check)"
OFFERED="$(say_field offered)"
if [[ "${CHECK}" == 0 && " ${OFFERED} " == *" kulan-base "* ]]; then
	result pass "Updates found" "Software Update's list offered this build: $(wc -w <<<"${OFFERED}") updates, including kulan-base"
elif [[ "${CHECK}" == 0 ]]; then
	result fail "Updates found" "the check worked but this build wasn't offered (offered: ${OFFERED:-nothing})"
else
	result fail "Updates found" "update-run check failed (${CHECK:-no answer}): $(sed -n 's/^KULAN-UPGRADE: checklog=//p' <<<"${LOG}" | tail -n 3 | paste -sd ' ' - | cut -c1-300)"
fi

read -r RC SECS <<<"$(say_field install)"
if [[ "${RC:-}" == 0 ]]; then
	result pass "Install updates" "update-run install --restore-point finished in ${SECS} s"
else
	result fail "Install updates" "update-run install failed (${RC:-no answer}): $(sed -n 's/^KULAN-UPGRADE: installlog=//p' <<<"${LOG}" | tail -n 4 | paste -sd ' ' - | cut -c1-400)"
fi
case "$(say_field restorepoint)" in
	made) result pass "Restore point" "taken before installing, as Software Update does" ;;
	# The previous release's own update step took (or skipped) it; this
	# build's is checked by the install test ("Restore point before updates").
	skipped) result info "Restore point" "the previous release skipped it before the update: $(sed -n 's/^KULAN-UPGRADE: restorelog=//p' <<<"${LOG}" | grep -v '^pmstatus' | tail -n 12 | paste -sd ' ' - | cut -c1-600)" ;;
	*) [[ -n "$(say_field install)" ]] && result fail "Restore point" "not reported by the updated computer" ;;
esac
RESTART="$(say_field restart_required)"
[[ -n "${RESTART}" ]] && result info "Restart needed for" "${RESTART}"

R2="$(report 2)"
FAILED_UNITS="$(sed -n 's/^failed=//p' <<<"${R2}" | paste -sd ' ' -)"
if [[ -z "${R2}" ]]; then
	result fail "After the update" "no report after restarting (${ELAPSED} s; serial log: $(tail -n 3 <<<"${LOG}" | tr '\n' ' ' | cut -c1-200))"
elif [[ "$(field "${R2}" desktop_user)" == kulan ]]; then
	result pass "After the update" "restarted to the desktop for kulan; $(field "${R2}" time)"
else
	result fail "After the update" "no desktop session after restarting (state $(field "${R2}" state))"
fi
if [[ -n "${R2}" ]]; then
	if [[ -n "${FAILED_UNITS}" ]]; then
		result fail "After the update: services" "failed: ${FAILED_UNITS}"
	else
		result pass "After the update: services" "none failed"
	fi
fi

AFTER="$(say_field after)"
if [[ -n "${AFTER}" ]]; then
	BEHIND=()
	COUNT=0
	for ITEM in ${AFTER}; do
		PKG="${ITEM%%=*}"
		VER="${ITEM#*=}"
		[[ -n "${NEW[${PKG}]:-}" ]] || continue
		COUNT=$((COUNT + 1))
		[[ "${VER}" == "${NEW[${PKG}]}" ]] || BEHIND+=("${PKG} ${VER} (want ${NEW[${PKG}]})")
	done
	if [[ ${#BEHIND[@]} -eq 0 && ${COUNT} -gt 0 ]]; then
		result pass "This build's versions" "all ${COUNT} installed Kulan packages updated (kulan-base ${NEW[kulan-base]:-?})"
	else
		result fail "This build's versions" "not updated: ${BEHIND[*]:-no Kulan packages found}"
	fi
	# Packages this build added that a computer should get with the update.
	MISSING=()
	for PKG in kulan-base kulan-desktop-settings kulan-settings kulan-update-center; do
		[[ -n "${NEW[${PKG}]:-}" && " ${AFTER} " != *" ${PKG}="* ]] && MISSING+=("${PKG}")
	done
	[[ ${#MISSING[@]} -gt 0 ]] && result fail "Core packages" "not installed after the update: ${MISSING[*]}"
	HALF="$(say_field half)"
	if [[ "$(say_field audit)" == 0 && "$(say_field aptcheck)" == ok && -z "${HALF// /}" ]]; then
		result pass "Package system" "nothing half-installed, no broken dependencies"
	else
		result fail "Package system" "dpkg --audit: $(say_field audit) lines; apt-get check: $(say_field aptcheck); not fully installed: ${HALF:-none}"
	fi
	PENDING="$(say_field pending)"
	if [[ -n "${PENDING// /}" ]]; then
		result fail "Nothing left behind" "Kulan updates still waiting after the update: ${PENDING}"
	else
		result pass "Nothing left behind" "no Kulan updates still waiting"
	fi
	result info "System" "$(say_field system) (was ${OLD_NAME:-?})"
else
	result fail "This build's versions" "the updated system didn't report its packages"
fi
grep -qE '"GET [^ ]*InRelease' "${OUT}/http.log" ||
	result fail "Test repository" "the virtual machine never asked for this build's packages"
summary
exit "${FAILED}"
