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
check "Identity" "os-release says Kulan OS" grep -q '^NAME="Kulan OS"' "${R}/usr/lib/os-release"
check "Identity is upgrade-safe" "os-release diverted from base-files" grep -qx "/usr/lib/os-release" "${R}/var/lib/dpkg/diversions"
for PKG in kulan-base kulan-desktop-settings kulan-welcome kulan-update-center kulan-apps; do
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
	result pass "Developer kit" "not in the ISO (one-click install from the Welcome app)"
fi
check "Icons" "Papirus icon theme installed" installed papirus-icon-theme
check "Font" "Inter installed" installed fonts-inter
check "Boot splash selected" "Plymouth theme = kulan" grep -qx "Theme=kulan" "${R}/etc/plymouth/plymouthd.conf"
if [[ -f "${WORK}/initrd.img" ]]; then
	check "Boot splash in initramfs" "kulan theme inside ${INITRD_PATH}" bash -c "lsinitramfs '${WORK}/initrd.img' | grep -q 'plymouth/themes/kulan/kulan.plymouth'"
	check "Keyboard layout in initramfs" "disk password typed with the chosen layout" bash -c "lsinitramfs '${WORK}/initrd.img' | grep -q 'etc/boottime.kmap.gz'"
else
	result fail "Boot splash in initramfs" "no initrd found under /live"
fi
check "Login screen logo" "Debian's vendor logos point at Kulan's" test "$(readlink "${R}/etc/alternatives/vendor-logos")" = /usr/share/kulan/vendor-logos
check "Welcome app" "/usr/bin/kulan-welcome present" has_path usr/bin/kulan-welcome
check "Installer branding" "Calamares branding: kulan" grep -qx "branding: kulan" "${R}/etc/calamares/settings.conf"
check "Installer encryption" "encryption ticked by default" grep -qE "^preCheckEncryption:[[:space:]]+true" "${R}/etc/calamares/modules/partition.conf"
check "Installer layout" "separate unencrypted /boot, so the disk is unlocked once" bash -c "grep -q '^partitionLayout:' '${R}/etc/calamares/modules/partition.conf' && grep -q 'noEncrypt: true' '${R}/etc/calamares/modules/partition.conf'"
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
