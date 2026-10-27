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
	etc/calamares/branding/kulan/branding.desc
	etc/calamares/branding/kulan/stylesheet.qss
	etc/xdg/autostart/calamares-desktop-icon.desktop
	etc/ufw/ufw.conf
	var/lib/flatpak/repo/config
	etc/firefox/policies/policies.json
	etc/NetworkManager/conf.d/80-kulan-privacy.conf
	etc/alternatives/vendor-logos
	etc/sysctl.d/60-kulan-hardening.conf
	etc/xdg/gnome-mimeapps.list
	etc/calamares/modules/fstab.conf
	var/lib/flatpak/overrides/global
	etc/passwd
	usr/bin/kulan-installer
	usr/lib/kulan/installer-engine
	usr/share/applications/calamares-install-debian.desktop
	usr/share/kulan/xdg/applications/libreoffice-writer.desktop
	usr/share/kulan/xdg/applications/libreoffice-calc.desktop
	usr/share/kulan/xdg/applications/libreoffice-impress.desktop
	usr/share/kulan/xdg/applications/gimp.desktop
	usr/share/kulan/xdg/applications/vlc.desktop
	usr/share/kulan/xdg/applications/org.gnome.Ptyxis.desktop
	usr/share/kulan/xdg/applications/org.gnome.baobab.desktop
	usr/share/kulan/xdg/applications/gufw.desktop
	usr/share/kulan/xdg/applications/org.gnome.Software.desktop
	etc/pam.d/gdm-password
	etc/pam.d/gdm-autologin
	etc/pam.d/login
	etc/pam.d/common-auth
	etc/group
)
unsquashfs -q -n -d "${WORK}/root" "${WORK}/fs.squashfs" "${FILES[@]}" >/dev/null 2>&1 || true
unsquashfs -l -n "${WORK}/fs.squashfs" > "${WORK}/listing" 2>/dev/null
R="${WORK}/root"
# The package list, for the vulnerability scan (ci/vuln-scan.sh).
if [[ -n "${KULAN_SAVE_STATUS:-}" && -f "${R}/var/lib/dpkg/status" ]]; then
	cp "${R}/var/lib/dpkg/status" "${KULAN_SAVE_STATUS}"
fi

# Where the size goes: the live system (compressed), the offline-install pool,
# and the biggest installed packages (uncompressed), to keep the ISO small.
POOL_KB="$(xorriso -indev "${ISO}" -du /pool 2>/dev/null | awk 'END { print $1 }' | tr -dc 0-9)"
result info "Size breakdown" "live system $(( $(stat -c %s "${WORK}/fs.squashfs") / 1048576 )) MB, offline-install pool $(( ${POOL_KB:-0} / 1024 )) MB; biggest installed: $(awk 'BEGIN{RS=""} {p=""; s=0; n=split($0, l, "\n"); for (i=1;i<=n;i++) { if (l[i] ~ /^Package: /) p=substr(l[i],10); if (l[i] ~ /^Installed-Size: /) s=substr(l[i],17) } print s, p }' "${R}/var/lib/dpkg/status" 2>/dev/null | sort -rn | head -n 15 | awk '{ printf "%s%s %d MB", (NR>1?", ":""), $2, $1/1024 }')"

[[ -f "${R}/var/lib/dpkg/status" ]] && result info "Why the biggest are there" "$(python3 "$(dirname "$0")/why-installed.py" "${R}/var/lib/dpkg/status" 15)"
[[ -f "${R}/var/lib/dpkg/status" ]] && result info "Download weight" "what the biggest packages add to the ISO (compressed): $(python3 "$(dirname "$0")/package-weight.py" "${WORK}/fs.squashfs" "${R}/var/lib/dpkg/status" 20)"

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
check "Account picture" "new accounts start with Kulan's picture, not Debian's logo" bash -c "grep -qx /etc/skel/.face '${R}/var/lib/dpkg/diversions' && grep -q 'squashfs-root/usr/share/kulan/faces/kulan.png\$' '${WORK}/listing'"
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
# One line per app, so a failure says which name is missing.
for SPEC in "libreoffice-writer.desktop:Writer" "libreoffice-calc.desktop:Calc" "libreoffice-impress.desktop:Impress" \
	"gimp.desktop:GIMP" "vlc.desktop:VLC" "org.gnome.Ptyxis.desktop:Terminal" "org.gnome.baobab.desktop:Disk Usage" \
	"gufw.desktop:Firewall" "org.gnome.Software.desktop:Store"; do
	ID="${SPEC%%:*}"
	SHORT="${SPEC#*:}"
	check "Short app name: ${SHORT}" "${ID} shows as ${SHORT} in the app grid (original: $(has_path "usr/share/applications/${ID}" && echo present || echo missing))" \
		grep -qx "Name=${SHORT}" "${R}/usr/share/kulan/xdg/applications/${ID}"
done
check "Media apps" "VLC and GIMP" bash -c "$(declare -f installed); R='${R}'; installed vlc && installed gimp"
check "Restore points" "Timeshift installed" installed timeshift
if installed kulan-devkit; then
	result fail "Developer kit" "kulan-devkit must be an optional install, but it is in the ISO"
else
	result pass "Developer kit" "not in the ISO (one-click add-on in Kulan Settings)"
fi
check "Sound" "PipeWire with PulseAudio and ALSA support and WirePlumber" bash -c "$(declare -f installed); R='${R}'; installed pipewire && installed pipewire-pulse && installed pipewire-alsa && installed wireplumber"
check "Alert sound" "GNOME's Click alert is where Kulan's first sign-in links it" grep -q "squashfs-root/usr/share/sounds/gnome/default/alerts/click.ogg$" "${WORK}/listing"
check "VM tools" "UTM/QEMU and VMware guest tools (clipboard, screen size)" bash -c "$(declare -f installed); R='${R}'; installed spice-vdagent && installed qemu-guest-agent && installed open-vm-tools-desktop"
check "Mac-style apps" "Quick Look, Live Text OCR, window snapping, emoji, weather, desktop icons and Folder Colour" bash -c "$(declare -f installed); R='${R}'; installed gnome-sushi && installed python3-nautilus && installed tesseract-ocr-eng && installed gnome-shell-extension-tiling-assistant && installed gnome-characters && installed gnome-weather && installed gnome-shell-extension-desktop-icons-ng"
if [[ "${ARCH}" == amd64 ]]; then
	check "Laptop firmware" "Intel and AMD graphics, Wi-Fi, Bluetooth, sound and CPU microcode" bash -c "$(declare -f installed); R='${R}'; for p in firmware-intel-graphics firmware-amd-graphics firmware-iwlwifi firmware-realtek firmware-atheros firmware-mediatek firmware-sof-signed bluez-firmware intel-microcode amd64-microcode; do installed \$p || { echo \$p; exit 1; }; done"
fi
check "Compressed memory" "zram swap generator installed" installed systemd-zram-generator
check "Terminal" "Ptyxis with zsh, suggestions, highlighting and the power tools; new accounts use zsh" bash -c "$(declare -f installed); R='${R}'; installed ptyxis && installed zsh && installed zsh-autosuggestions && installed zsh-syntax-highlighting && installed fzf && installed ripgrep && installed fd-find && installed bat && installed eza && installed zoxide && installed btop && grep -qx 'DSHELL=/bin/zsh' \"\${R}/etc/adduser.conf\" && grep -qE '(shell:|userShell:)[[:space:]]+/bin/zsh' \"\${R}/etc/calamares/modules/users.conf\" && grep -q kulan.zsh \"\${R}/etc/skel/.zshrc\""
check "Restart in ARM VMs" "kexec-tools, fast-restart generator, and the kexec hardening moved to kulan-kexec-guard (enabled)" bash -c "$(declare -f installed has_path); R='${R}'; WORK='${WORK}'; installed kexec-tools && has_path usr/lib/systemd/system-generators/kulan-fast-restart && has_path etc/systemd/system/sysinit.target.wants/kulan-kexec-guard.service && has_path etc/systemd/system/multi-user.target.wants/kulan-fast-restart.service && ! grep -q '^kernel.kexec_load_disabled' \"\${R}/etc/sysctl.d/60-kulan-hardening.conf\" && grep -q '^kernel.kptr_restrict' \"\${R}/etc/sysctl.d/60-kulan-hardening.conf\""
check "Disk password" "a mistyped disk password asks again (installer writes tries=0 to crypttab)" grep -qE '^crypttabOptions:[[:space:]]*[^#[:space:]]*tries=0' "${R}/etc/calamares/modules/fstab.conf"
check "Store apps' look" "Flatpak apps may read the GTK settings folders, for Kulan's window buttons" bash -c "grep -q 'xdg-config/gtk-4.0:ro' '${R}/var/lib/flatpak/overrides/global' && grep -q 'xdg-config/gtk-3.0:ro' '${R}/var/lib/flatpak/overrides/global' && grep -q 'QT_QPA_PLATFORM=xcb;wayland' '${R}/var/lib/flatpak/overrides/global'"
check "Boot report" "kulan-boot-report.service enabled, for the boot test" has_path etc/systemd/system/multi-user.target.wants/kulan-boot-report.service
if installed cups-browsed; then
	result fail "Lean services" "cups-browsed is in the ISO (it should be left out)"
else
	result pass "Lean services" "cups-browsed left out"
fi
check "Password manager" "KeePassXC and Metadata Cleaner" bash -c "$(declare -f installed); R='${R}'; installed keepassxc && installed metadata-cleaner"
check "Font" "Inter installed" installed fonts-inter
check "Default apps" "photos open in Image Viewer, PDFs in Document Viewer, text in Text Editor (every default app is in the ISO)" bash -c 'f="$1/etc/xdg/gnome-mimeapps.list"; grep -qx "image/jpeg=org.gnome.Loupe.desktop" "$f" && grep -qx "application/pdf=org.gnome.Evince.desktop" "$f" && grep -qx "text/plain=org.gnome.TextEditor.desktop" "$f" || exit 1; for app in $(sed -n "s/^[^#][^=]*=//p" "$f" | sort -u); do grep -q "squashfs-root/usr/share/applications/${app}\$" "$2" || { echo "missing ${app}"; exit 1; }; done' _ "${R}" "${WORK}/listing"
check "Smaller download" "unused speech data, the all-language bundle, VLC translations, GNOME's wallpapers and other-language help left out; Kulan's wallpapers and English kept" bash -c "$(declare -f installed); R='${R}'; ! installed pocketsphinx-en-us && ! installed locales-all && ! installed vlc-l10n && ! grep -qE 'squashfs-root/usr/share/backgrounds/gnome/.+' '${WORK}/listing' && ! grep -qE 'squashfs-root/usr/share/help/(de|fr|es)/' '${WORK}/listing' && grep -q 'squashfs-root/usr/share/backgrounds/kulan/kulan-dark.png\$' '${WORK}/listing' && grep -q 'squashfs-root/usr/lib/locale/locale-archive\$' '${WORK}/listing'"
check "Clock sync" "systemd-timesyncd installed, so the time is set from the internet" installed systemd-timesyncd
check "Lighter mode" "enabled at start-up; the file search service it slows down (localsearch-3) is in the image" bash -c "grep -q 'squashfs-root/etc/systemd/system/multi-user.target.wants/kulan-lighter-mode.service\$' '${WORK}/listing' && grep -q 'squashfs-root/usr/lib/systemd/user/localsearch-3.service\$' '${WORK}/listing'"
check "Phone link is off" "GSConnect isn't in the image: it's an add-on, installed from Kulan Settings › Phone (in the image, Kulan's defaults would turn it on for everyone)" bash -c "$(declare -f installed); R='${R}'; ! installed gnome-shell-extension-gsconnect"
check "Anonymous count" "weekly count timer enabled, with its own user" bash -c "grep -q 'squashfs-root/etc/systemd/system/timers.target.wants/kulan-countme.timer\$' '${WORK}/listing' && grep -q '^kulan-countme:' '${R}/etc/passwd'"
check "Firmware updates" "fwupd installed (BIOS, dock and device firmware in Software Update)" installed fwupd
check "Live Text languages" "English and Arabic text recognition" bash -c "$(declare -f installed); R='${R}'; installed tesseract-ocr-eng && installed tesseract-ocr-ara"
check "Cursor files" "Bibata Modern Classic cursors present, the unused variants' left out" bash -c "grep -q 'squashfs-root/usr/share/icons/Bibata-Modern-Classic/cursors/left_ptr\$' '${WORK}/listing' && ! grep -E 'squashfs-root/usr/share/icons/Bibata-[^/]+/cursors/' '${WORK}/listing' | grep -qv '/Bibata-Modern-Classic/'"
check "Boot splash selected" "Plymouth theme = kulan" grep -qx "Theme=kulan" "${R}/etc/plymouth/plymouthd.conf"
if [[ -f "${WORK}/initrd.img" ]]; then
	check "Boot splash in initramfs" "kulan theme inside ${INITRD_PATH}" bash -c "lsinitramfs '${WORK}/initrd.img' | grep -q 'plymouth/themes/kulan/kulan.plymouth'"
	check "Animated splash" "the gathering animation (script, images) and Plymouth's script module in the initramfs" bash -c "L=\"\$(lsinitramfs '${WORK}/initrd.img')\"; echo \"\$L\" | grep -q 'plymouth/themes/kulan/kulan.script' && echo \"\$L\" | grep -q 'plymouth/themes/kulan/dot-amber.png' && echo \"\$L\" | grep -q 'plymouth/script.so'"
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
check "kulan command" "/usr/bin/kulan present (info, update, doctor, ask)" has_path usr/bin/kulan
kulan_installer() {
	test -x "${R}/usr/bin/kulan-installer" && test -x "${R}/usr/lib/kulan/installer-engine" &&
		grep -qF "favorite-apps=['org.kulan.Installer.desktop'" "${R}/usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override" &&
		grep -qx 'Name=Advanced install' "${R}/usr/share/applications/calamares-install-debian.desktop"
}
check "Kulan Installer" "Install Kulan OS (Kulan Installer) first in the dock; Calamares is Advanced install" kulan_installer
POOL="$(xorriso -indev "${ISO}" -find /pool -name '*.deb' 2>/dev/null | tr -d "'")"
if [[ "${ARCH}" == amd64 ]]; then POOL_NEED=(grub-efi-amd64_ grub-efi-amd64-signed_ shim-signed_ grub-pc_ cryptsetup-initramfs_); else POOL_NEED=(grub-efi-arm64_ cryptsetup-initramfs_); fi
POOL_MISSING=()
for P in "${POOL_NEED[@]}"; do grep -q "/${P}" <<<"${POOL}" || POOL_MISSING+=("${P%_}"); done
if [[ ${#POOL_MISSING[@]} -eq 0 ]] && xorriso -indev "${ISO}" -find /dists -name Packages\* 2>/dev/null | grep -q Packages; then
	result pass "Offline install" "boot loader and encryption packages in the ISO's package pool ($(grep -c . <<<"${POOL}") packages)"
else
	result fail "Offline install" "missing from the ISO's package pool: ${POOL_MISSING[*]:-the index (dists)}"
fi
check "Installer branding" "Calamares branding: kulan" grep -qx "branding: kulan" "${R}/etc/calamares/settings.conf"
check "Installer look" "Kulan stylesheet and sidebar colours" bash -c "grep -q '#0D9488' '${R}/etc/calamares/branding/kulan/stylesheet.qss' && grep -qE 'SidebarBackground:[[:space:]]+\"#0A2240\"' '${R}/etc/calamares/branding/kulan/branding.desc' && grep -qE 'SidebarTextCurrent:[[:space:]]+\"#FFFFFF\"' '${R}/etc/calamares/branding/kulan/branding.desc'"
check "Installer desktop icon" "none on the live desktop (the dock has Install Kulan OS)" bash -c "! test -f '${R}/etc/xdg/autostart/calamares-desktop-icon.desktop' || grep -qx 'Hidden=true' '${R}/etc/xdg/autostart/calamares-desktop-icon.desktop'"
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
check "Graphics helper" "Kulan's renderer helper and the EGL and OpenGL ES libraries it uses are there, for Kulan Settings › About's 3D acceleration line" bash -c "$(declare -f has_path); WORK='${WORK}'; has_path usr/lib/kulan/graphics-renderer && grep -q 'squashfs-root/usr/lib/[^/]*/libEGL.so.1\$' '${WORK}/listing' && grep -q 'squashfs-root/usr/lib/[^/]*/libGLESv2.so.2\$' '${WORK}/listing'"
check "Joins from an organisation's stick" "kulan-fleet-autojoin.timer is enabled, and does nothing without a stick's join code" bash -c "R='${R}'; test -e \"\${R}/etc/systemd/system/timers.target.wants/kulan-fleet-autojoin.timer\" && grep -q '^ConditionPathExists=/var/lib/kulan/fleet-join.json' \"\${R}/usr/lib/systemd/system/kulan-fleet-autojoin.service\" && test ! -e \"\${R}/var/lib/kulan/fleet-join.json\""
check "Lost mode stops sign-in" "GDM's sign-in (password and automatic) and the console's honour /etc/nologin (pam_nologin), which Kulan Fleet's lost mode uses" bash -c "for f in gdm-password gdm-autologin login; do grep -Eq '^auth[[:space:]]+(requisite|required)[[:space:]]+pam_nologin.so' '${R}/etc/pam.d/'\$f || { echo \"no pam_nologin in \$f\"; exit 1; }; done"
check "Two-step sign-in ready" "Kulan Fleet's two-step profile is in common-auth, after the password, and only asks members of kulan-two-step (nobody on a new install)" bash -c "f='${R}/etc/pam.d/common-auth'; grep -q 'pam_succeed_if.so quiet user ingroup kulan-two-step' \"\$f\" || { echo 'not in common-auth:'; grep -v '^#' \"\$f\" | grep . | head -n 12; ls '${R}/usr/share/pam-configs/'; exit 1; }; grep -Eq '^kulan-two-step:x:[0-9]+:$' '${R}/etc/group' || { echo \"group: \$(grep kulan-two-step '${R}/etc/group' || echo missing)\"; exit 1; }; grep -q 'squashfs-root/usr/lib/[a-z0-9_]*-linux-gnu/security/pam_oath.so\$' '${WORK}/listing' || { echo 'no pam_oath.so'; exit 1; }; awk '/pam_unix.so/ {u=NR} /ingroup kulan-two-step/ {k=NR} END {exit !(u && k > u)}' \"\$f\" || { echo 'not after pam_unix'; exit 1; }"
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
