#!/bin/bash
# Open a built Kulan OS ISO and check that everything Kulan adds is really
# inside. Prints GitHub annotations (readable in the Actions UI and API) and a
# summary table; exits non-zero if any check fails.
#
# Usage: ci/verify-iso.sh <iso> <arch>
# Needs: xorriso, squashfs-tools, initramfs-tools-core (lsinitramfs).
set -uo pipefail

ISO="${1:?usage: $0 <iso> <arch>}"
ARCH="${2:?usage: $0 <iso> <arch>}"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
FAILED=0
ROWS=()

result() { # result <pass|fail|info> <check> <detail>
	local status="$1" check="$2" detail="$3"
	case "${status}" in
		pass) echo "::notice title=ISO ${ARCH}: ${check}::${detail}"; ROWS+=("| ✅ | ${check} | ${detail} |") ;;
		info) echo "::notice title=ISO ${ARCH}: ${check}::${detail}"; ROWS+=("| ℹ️ | ${check} | ${detail} |") ;;
		*) echo "::error title=ISO ${ARCH}: ${check}::${detail}"; ROWS+=("| ❌ | ${check} | ${detail} |"); FAILED=1 ;;
	esac
}

check() { # check <name> <detail> <command...>
	local name="$1" detail="$2"
	shift 2
	if "$@" >/dev/null 2>&1; then result pass "${name}" "${detail}"; else result fail "${name}" "${detail} - NOT FOUND"; fi
}

# A missing tool must not look like something missing from the ISO.
for TOOL in xorriso unsquashfs lsinitramfs python3; do
	if ! command -v "${TOOL}" >/dev/null 2>&1; then
		echo "::error title=ISO ${ARCH}: inspector setup::${TOOL} is not installed (needs xorriso, squashfs-tools, initramfs-tools-core, zstd)"
		exit 2
	fi
done

[[ -f "${ISO}" ]] || { result fail "ISO file" "${ISO} missing"; exit 1; }
result pass "ISO file" "$(basename "${ISO}") ($(du -h "${ISO}" | cut -f1))"
# Size budget: the PC ISO must fit a 2 GB download; ARM stays below 1.8 GB.
SIZE_MB=$(( $(stat -c %s "${ISO}") / 1048576 ))
LIMIT_MB=1800
[[ "${ARCH}" == amd64 ]] && LIMIT_MB=2000
if (( SIZE_MB <= LIMIT_MB )); then
	result pass "ISO size" "${SIZE_MB} MB (limit ${LIMIT_MB} MB)"
else
	result fail "ISO size" "${SIZE_MB} MB is over the ${LIMIT_MB} MB limit"
fi

# --- Pull the pieces out of the ISO -------------------------------------------
xorriso -osirrox on -indev "${ISO}" \
	-extract /live/filesystem.squashfs "${WORK}/fs.squashfs" \
	-extract /boot/grub/grub.cfg "${WORK}/grub.cfg" >/dev/null 2>&1
INITRD_PATH="$(xorriso -indev "${ISO}" -find /live -name 'initrd*' 2>/dev/null | tr -d "'" | head -1)"
if [[ -n "${INITRD_PATH}" ]]; then
	xorriso -osirrox on -indev "${ISO}" -extract "${INITRD_PATH}" "${WORK}/initrd.img" >/dev/null 2>&1
fi
[[ -f "${WORK}/fs.squashfs" ]] || { result fail "Live filesystem" "live/filesystem.squashfs missing"; exit 1; }

FILES=(
	usr/lib/os-release
	var/lib/dpkg/diversions
	var/lib/dpkg/status
	etc/plymouth/plymouthd.conf
	usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override
	etc/calamares/settings.conf
	etc/calamares/modules/partition.conf
	etc/calamares/modules/users.conf
	etc/adduser.conf
	etc/skel/.zshrc
	etc/calamares/branding/kulan/show.qml
	etc/ufw/ufw.conf
	var/lib/flatpak/repo/config
	etc/firefox/policies/policies.json
	etc/NetworkManager/conf.d/80-kulan-privacy.conf
	etc/alternatives/vendor-logos
)
unsquashfs -q -n -d "${WORK}/root" "${WORK}/fs.squashfs" "${FILES[@]}" >/dev/null 2>&1 || true
unsquashfs -l -n "${WORK}/fs.squashfs" > "${WORK}/listing" 2>/dev/null
R="${WORK}/root"

# shellcheck disable=SC2317  # called through check()
installed() { # installed <package>
	awk -v p="$1" 'BEGIN{RS=""} $0 ~ "(^|\n)Package: " p "\n" && $0 ~ /Status: install ok installed/ {f=1} END{exit !f}' \
		"${R}/var/lib/dpkg/status"
}
# shellcheck disable=SC2317  # called through check()
has_path() { grep -q "squashfs-root/$1\$" "${WORK}/listing"; }

# --- Checks -------------------------------------------------------------------
check "Boot menu" "\"Kulan OS Live\" entries in GRUB" grep -q "Kulan OS Live" "${WORK}/grub.cfg"
if [[ "${ARCH}" == arm64 ]]; then
	check "Console on screen" "boot messages, splash and prompts on the screen, not only the serial port" bash -c "grep -q 'console=tty0' '${WORK}/grub.cfg' && grep -q 'plymouth.ignore-serial-consoles' '${WORK}/grub.cfg'"
fi
check "Identity" "os-release says Kulan OS" grep -q '^NAME="Kulan OS"' "${R}/usr/lib/os-release"
check "Identity is upgrade-safe" "os-release diverted from base-files" grep -qx "/usr/lib/os-release" "${R}/var/lib/dpkg/diversions"
for PKG in kulan-base kulan-desktop-settings kulan-settings kulan-welcome kulan-update-center kulan-apps kulan-assistant kulan-assistant-engine; do
	VER="$(awk -v p="${PKG}" 'BEGIN{RS=""} $0 ~ "(^|\n)Package: " p "\n" {match($0, /\nVersion: [^\n]+/); print substr($0, RSTART+10, RLENGTH-10)}' "${R}/var/lib/dpkg/status")"
	check "Package ${PKG}" "installed ${VER:-?}" installed "${PKG}"
done
check "Desktop extensions" "dock + blur enabled" grep -q "dash-to-dock.*blur-my-shell" "${R}/usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override"
check "Kulan Shell" "kulan-shell@kulangroup.com enabled" grep -q "kulan-shell@kulangroup.com" "${R}/usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override"
check "Build number" "os-release BUILD_ID=${KULAN_BUILD:-?}" grep -qx "BUILD_ID=${KULAN_BUILD:-.*}" "${R}/usr/lib/os-release"
check "Cursor" "Bibata cursor installed" installed bibata-cursor-theme
check "Office apps" "LibreOffice Writer, Calc, Impress" bash -c "$(declare -f installed); R='${R}'; installed libreoffice-writer && installed libreoffice-calc && installed libreoffice-impress"
check "Media apps" "VLC and GIMP" bash -c "$(declare -f installed); R='${R}'; installed vlc && installed gimp"
check "Restore points" "Timeshift installed" installed timeshift
if installed kulan-devkit; then
	result fail "Developer kit" "kulan-devkit must be an optional install, but it is in the ISO"
else
	result pass "Developer kit" "not in the ISO (one-click add-on in Kulan Settings)"
fi
check "Mac-style apps" "Quick Look, Live Text OCR, window snapping, emoji, weather, desktop icons and Folder Colour" bash -c "$(declare -f installed); R='${R}'; installed gnome-sushi && installed python3-nautilus && installed tesseract-ocr-eng && installed gnome-shell-extension-tiling-assistant && installed gnome-characters && installed gnome-weather && installed gnome-shell-extension-desktop-icons-ng"
if [[ "${ARCH}" == amd64 ]]; then
	check "Laptop firmware" "Intel and AMD graphics, Wi-Fi, Bluetooth, sound and CPU microcode" bash -c "$(declare -f installed); R='${R}'; for p in firmware-intel-graphics firmware-amd-graphics firmware-iwlwifi firmware-realtek firmware-atheros firmware-mediatek firmware-sof-signed bluez-firmware intel-microcode amd64-microcode; do installed \$p || { echo \$p; exit 1; }; done"
fi
check "Compressed memory" "zram swap generator installed" installed systemd-zram-generator
check "Terminal" "Ptyxis with zsh, suggestions and highlighting; new accounts use zsh" bash -c "$(declare -f installed); R='${R}'; installed ptyxis && installed zsh && installed zsh-autosuggestions && installed zsh-syntax-highlighting && grep -qx 'DSHELL=/bin/zsh' \"\${R}/etc/adduser.conf\" && grep -qE '(shell:|userShell:)[[:space:]]+/bin/zsh' \"\${R}/etc/calamares/modules/users.conf\" && grep -q kulan.zsh \"\${R}/etc/skel/.zshrc\""
check "Boot report" "kulan-boot-report.service enabled, for the boot test" has_path etc/systemd/system/multi-user.target.wants/kulan-boot-report.service
if installed cups-browsed; then
	result fail "Lean services" "cups-browsed is in the ISO (it should be left out)"
else
	result pass "Lean services" "cups-browsed left out"
fi
check "Password manager" "KeePassXC and Metadata Cleaner" bash -c "$(declare -f installed); R='${R}'; installed keepassxc && installed metadata-cleaner"
check "Font" "Inter installed" installed fonts-inter
check "Boot splash selected" "Plymouth theme = kulan" grep -qx "Theme=kulan" "${R}/etc/plymouth/plymouthd.conf"
if [[ -f "${WORK}/initrd.img" ]]; then
	check "Boot splash in initramfs" "kulan theme inside ${INITRD_PATH}" bash -c "lsinitramfs '${WORK}/initrd.img' | grep -q 'plymouth/themes/kulan/kulan.plymouth'"
	# Debian's plymouth hook swaps the theme's watermark for Debian's logo;
	# Kulan's hook (kulan-splash) puts Kulan's back. Compare the two images.
	if command -v unmkinitramfs >/dev/null 2>&1 && unmkinitramfs "${WORK}/initrd.img" "${WORK}/initrd-x" >/dev/null 2>&1 \
		&& unsquashfs -q -n -d "${WORK}/wm" "${WORK}/fs.squashfs" usr/share/plymouth/themes/kulan/watermark.png >/dev/null 2>&1; then
		WM_INITRD="$(find "${WORK}/initrd-x" -path '*plymouth/themes/kulan/watermark.png' | head -n 1)"
		if [[ -n "${WM_INITRD}" ]] && cmp -s "${WM_INITRD}" "${WORK}/wm/usr/share/plymouth/themes/kulan/watermark.png"; then
			result pass "Splash logo" "the initramfs shows Kulan's logo, not Debian's"
		else
			result fail "Splash logo" "watermark in the initramfs isn't Kulan's (${WM_INITRD:-missing})"
		fi
	else
		result fail "Splash logo" "could not unpack the initramfs or the watermark"
	fi
	check "VM graphics early" "virtio_gpu driver inside the initramfs, so VMs show the splash" bash -c "lsinitramfs '${WORK}/initrd.img' | grep -q '/virtio-gpu.ko\\|/virtio_gpu.ko'"
	# Debian's console-setup puts the layout in the initramfs as
	# etc/console-setup/cached_*.kmap, loaded by scripts/init-top/keymap.
	lsinitramfs "${WORK}/initrd.img" > "${WORK}/initrd.list"
	if grep -qE '^etc/console-setup/cached_.*\.kmap' "${WORK}/initrd.list" && grep -qx 'scripts/init-top/keymap' "${WORK}/initrd.list"; then
		result pass "Keyboard layout in initramfs" "disk password typed with the chosen layout ($(grep -oE 'cached_[^/]*\.kmap' "${WORK}/initrd.list" | head -n 1))"
	else
		CS="no"; installed console-setup && CS="yes"
		KM="$(grep -iE 'kmap|keymap|setupcon|console-setup' "${WORK}/initrd.list" | head -n 5 | tr '\n' ' ')"
		result fail "Keyboard layout in initramfs" "no console-setup keymap; console-setup installed: ${CS}; related files: ${KM:-none}"
	fi
else
	result fail "Boot splash in initramfs" "no initrd found under /live"
fi
check "Login screen logo" "Debian's vendor logos point at Kulan's" test "$(readlink "${R}/etc/alternatives/vendor-logos")" = /usr/share/kulan/vendor-logos
check "Welcome app" "/usr/bin/kulan-welcome present" has_path usr/bin/kulan-welcome
check "Installer branding" "Calamares branding: kulan" grep -qx "branding: kulan" "${R}/etc/calamares/settings.conf"
# shellcheck disable=SC2016  # expanded by the inner bash
check "Installer slideshow" "every slide the slideshow shows is in the ISO" bash -c 'Q="$1/etc/calamares/branding/kulan/show.qml"; n=0; for f in $(grep -o "\"slide[^\"]*\.png\"" "$Q" | tr -d "\"" | sort -u); do grep -q "squashfs-root/etc/calamares/branding/kulan/$f\$" "$2" || { echo "missing $f"; exit 1; }; n=$((n+1)); done; [ "$n" -ge 5 ]' _ "${R}" "${WORK}/listing"
check "Installer in a VM" "encryption starts unticked in virtual machines (rule enabled)" has_path etc/systemd/system/multi-user.target.wants/kulan-installer-vm.service
check "Installer login" "\"Log in automatically\" starts unticked" bash -c "! grep -qE '^doAutologin:[[:space:]]+true' '${R}/etc/calamares/modules/users.conf'"
check "Installer encryption" "encryption ticked by default" grep -qE "^preCheckEncryption:[[:space:]]+true" "${R}/etc/calamares/modules/partition.conf"
check "Installer layout" "separate unencrypted /boot, so the disk is unlocked once" bash -c "grep -q '^partitionLayout:' '${R}/etc/calamares/modules/partition.conf' && grep -q 'noEncrypt: true' '${R}/etc/calamares/modules/partition.conf'"
check "Installer swap" "no swap partition or file (compressed memory instead)" bash -c "test \"\$(sed -n '/^userSwapChoices:/,/^[^ #]/p' '${R}/etc/calamares/modules/partition.conf' | grep -cE '^[[:space:]]+- ')\" = 1 && grep -qE '^[[:space:]]+- none' '${R}/etc/calamares/modules/partition.conf' && grep -qE '^initialSwapChoice:[[:space:]]+none' '${R}/etc/calamares/modules/partition.conf'"
check "Live session" "screen stays on in the live session" has_path etc/xdg/autostart/org.kulan.LiveSession.desktop
check "Firewall" "ufw enabled at boot" grep -qx "ENABLED=yes" "${R}/etc/ufw/ufw.conf"
check "Firewall app" "gufw installed" installed gufw
check "Backups" "Deja Dup installed" installed deja-dup
check "Network privacy" "Wi-Fi MAC randomization config" test -s "${R}/etc/NetworkManager/conf.d/80-kulan-privacy.conf"
check "Browser privacy" "Firefox policies valid JSON" python3 -c "import json,sys; json.load(open(sys.argv[1]))" "${R}/etc/firefox/policies/policies.json"
check "Flathub" "flathub remote configured" grep -q 'remote "flathub"' "${R}/var/lib/flatpak/repo/config"
if [[ "${ARCH}" == amd64 ]]; then
	check "Tor Browser" "torbrowser-launcher installed" installed torbrowser-launcher
fi
if has_path etc/apt/sources.list.d/kulan.sources; then
	result pass "Update channel" "kulan.sources and signing key included"
else
	result info "Update channel" "not set up yet - run scripts/setup-update-channel.sh"
fi

# --- Summary --------------------------------------------------------------------
{
	echo "### Kulan OS ${ARCH} ISO check"
	echo
	echo "| | Check | Result |"
	echo "|---|---|---|"
	printf '%s\n' "${ROWS[@]}"
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"

exit "${FAILED}"
