#!/bin/bash
# Install the Kulan packages into a clean Debian 13 system (with their real
# Debian dependencies), check they behave, then remove them and check Debian
# is restored. Runs inside a debian:trixie container on GitHub Actions; gates
# publishing to the update channel and building ISOs.
#
# Usage: ci/test-packages.sh   (expects dist/debs/*.deb)
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
FAILED=0
ROWS=()

result() { # result <pass|fail|skip> <check> <detail>
	if [[ "$1" == pass ]]; then
		echo "::notice title=Packages: $2::$3"
		ROWS+=("| ✅ | $2 | $3 |")
	elif [[ "$1" == skip ]]; then
		echo "::notice title=Packages: $2 (skipped)::$3"
		ROWS+=("| ⏭️ | $2 | $3 |")
	else
		echo "::error title=Packages: $2::$3"
		ROWS+=("| ❌ | $2 | $3 |")
		FAILED=1
	fi
}

check() { # check <name> <detail> <command...>
	local name="$1" detail="$2"
	shift 2
	if "$@" >/tmp/check.out 2>&1; then
		result pass "${name}" "${detail}"
	else
		result fail "${name}" "${detail} - $(tail -n 6 /tmp/check.out | tr '\n' ' ' | cut -c1-600)"
	fi
}

# shellcheck disable=SC2317  # used through bash -c "$(declare -f gs)"
gs() { GSETTINGS_BACKEND=memory gsettings get "$@"; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -q >/dev/null
# Checks run as root; byte-code caches written into /usr would be left
# behind on purge and hide what "Removal cleans up" should catch.
export PYTHONDONTWRITEBYTECODE=1
# Parse Python files without writing caches (py_compile always writes them).
# shellcheck disable=SC2317  # used through bash -c "$(declare -f pycheck)"
pycheck() { python3 -c 'import sys; [compile(open(f, encoding="utf-8").read(), f, "exec") for f in sys.argv[1:]]' "$@"; }

shopt -s nullglob
# The core Kulan packages. kulan-devkit (optional) and kulan-apps (LibreOffice,
# VLC, GIMP: large) are checked by simulating their install below.
DEBS=()
for DEB in dist/debs/kulan-*.deb; do
	case "${DEB}" in
		*/kulan-devkit_* | */kulan-apps_* | */kulan-fleet-admin_* | */kulan-installer_*) ;;
		*) DEBS+=("${DEB}") ;;
	esac
done
DEVKIT=(dist/debs/kulan-devkit_*.deb)
APPS=(dist/debs/kulan-apps_*.deb)
# Kulan Fleet (the administrators' app): published, never installed by default.
FLEET_ADMIN=(dist/debs/kulan-fleet-admin_*.deb)
# Kulan Installer: only in the live session (checked separately below).
INSTALLER=(dist/debs/kulan-installer_*.deb)
[[ ${#DEBS[@]} -eq 6 && ${#DEVKIT[@]} -eq 1 && ${#APPS[@]} -eq 1 && ${#FLEET_ADMIN[@]} -eq 1 && ${#INSTALLER[@]} -eq 1 ]] || {
	echo "::error::expected 6 core packages, kulan-devkit, kulan-apps, kulan-fleet-admin and kulan-installer in dist/debs, found ${#DEBS[@]}, ${#DEVKIT[@]}, ${#APPS[@]}, ${#FLEET_ADMIN[@]} and ${#INSTALLER[@]}"
	exit 1
}
# Kulan Assistant's engine (ci/build-engine.sh), when CI built it for PCs.
ENGINE=(dist/engine/kulan-assistant-engine_*_amd64.deb)
# Kulan Files (ci/build-nautilus.sh), when CI built it for PCs.
FILES=(dist/files/*.deb)
[[ -e "${FILES[0]:-}" ]] || FILES=()
VERSION="$(cat dist/debs/VERSION)"

# --- Start from the released version, as existing users do -----------------------
# The published packages go in first, so the install below is a real upgrade:
# files moving between packages, Breaks/Replaces and maintainer scripts.
CHANNEL="$(sed -n 's/^KULAN_APT_URL="\(.*\)"$/\1/p' kulan.conf)"
# The bare debian:trixie image has no CA certificates, so apt can't use
# HTTPS sources yet (installed systems have them).
apt-get install -y -q ca-certificates >/dev/null 2>&1
UPGRADE_FROM=""
install -m 0644 packages/kulan-archive-keyring.gpg /usr/share/keyrings/kulan-ci-released.gpg
printf 'Types: deb\nURIs: %s\nSuites: ./\nSigned-By: /usr/share/keyrings/kulan-ci-released.gpg\n' "${CHANNEL}" \
	>/etc/apt/sources.list.d/kulan-ci-released.sources
if apt-get update -q >/tmp/released.log 2>&1 &&
	apt-get install -y -q kulan-base kulan-desktop-settings kulan-update-center kulan-welcome gdm3 desktop-base \
		>>/tmp/released.log 2>&1; then
	UPGRADE_FROM="$(dpkg-query -W -f '${Version}' kulan-base)"
	result pass "Released version" "kulan-base ${UPGRADE_FROM} installed from the update channel first"
else
	result fail "Released version" "couldn't install from ${CHANNEL}: $(grep -E '^(E|W|Err|Ign):' /tmp/released.log | head -n 4 | tr '\n' ' ' | cut -c1-400)"
	echo "--- apt output (released version) ---"
	cat /tmp/released.log
	apt-cache policy kulan-base
fi
# The released kulan-base brings its own kulan.sources for the same channel.
rm -f /etc/apt/sources.list.d/kulan-ci-released.sources /usr/share/keyrings/kulan-ci-released.gpg
apt-get update -q >/dev/null 2>&1

# --- Install, as the ISO build does (recommends included) -----------------------
# htop and gnome-tour are installed too, so the "Clutter hidden" check has
# real launchers to hide; gdm3 and desktop-base so the login screen's settings
# and Debian's vendor logos can be checked; xvfb, xauth and dbus-x11 so Kulan
# Settings can open every page on a virtual screen; appstream for the store
# pages.
if apt-get install -y -q "${DEBS[@]/#/./}" "${ENGINE[@]/#/./}" "${FILES[@]/#/./}" htop gnome-tour gdm3 desktop-base xvfb xauth dbus-x11 appstream \
	>/tmp/install.log 2>&1; then
	result pass "Install" "kulan-base, kulan-desktop-settings, kulan-settings, kulan-welcome, kulan-update-center, kulan-assistant ${VERSION}${ENGINE:+ and the assistant engine} with Debian dependencies"
else
	result fail "Install" "apt failed: $(grep -E '^E:|error' /tmp/install.log | tail -n 3 | tr '\n' ' ')"
	tail -n 40 /tmp/install.log
	exit 1
fi

if [[ -n "${UPGRADE_FROM}" ]]; then
	# shellcheck disable=SC2016  # expanded by the inner bash
	check "Upgrade from ${UPGRADE_FROM}" "every package at ${VERSION}, nothing half-configured, moved files owned by kulan-settings" bash -c '
		for p in kulan-base kulan-desktop-settings kulan-update-center kulan-welcome kulan-settings; do
			[[ "$(dpkg-query -W -f "\${Version}" "$p")" == "$1" ]] || { echo "$p not upgraded"; exit 1; }
		done
		[[ -z "$(dpkg --audit)" ]] || { dpkg --audit | head -n 3; exit 1; }
		[[ "$(dpkg -S /usr/bin/kulan-updates | cut -d: -f1)" == kulan-settings ]]' _ "${VERSION}"
fi

# --- Identity ---------------------------------------------------------------------
check "Identity" "os-release says Kulan OS" grep -q '^NAME="Kulan OS"' /usr/lib/os-release
check "Version shown" "os-release's VERSION_ID is kulan.conf's number (2.8.1) and what people see (PRETTY_NAME, VERSION, Kulan Settings) its label (2.8a)" python3 -I -B - <<'PYVER'
import re, sys
conf = dict(re.findall(r'^([A-Z_]+)="(.*)"$', open("kulan.conf").read(), re.M))
rel = dict(re.findall(r'^([A-Z_]+)="?([^"\n]*)"?$', open("/usr/lib/os-release").read(), re.M))
label = conf.get("KULAN_VERSION_LABEL") or conf["KULAN_VERSION"]
assert rel["VERSION_ID"] == conf["KULAN_VERSION"], rel
assert rel["PRETTY_NAME"] == f"Kulan OS {label}" and rel["VERSION"].startswith(f"{label} (based on Debian"), rel
sys.path.insert(0, "/usr/share/kulan-settings")
sys.dont_write_bytecode = True
src = open("/usr/share/kulan-settings/kulan_settings/updates.py").read()
ns = {}
exec(re.search(r"def shown_version.*?\n\n", src + "\n\n", re.S).group(0), ns)
assert ns["shown_version"](rel) == label, ns["shown_version"](rel)
print("version shown as", label)
PYVER
check "Identity is upgrade-safe" "os-release diverted by kulan-base" bash -c 'dpkg-divert --list /usr/lib/os-release | grep -q "by kulan-base$"'
check "Debian's copy kept" "os-release.debian is Debian's" grep -q '^ID=debian' /usr/lib/os-release.debian
check "Build number" "os-release has BUILD_ID" grep -q '^BUILD_ID=' /usr/lib/os-release
check "Symlink" "/etc/os-release points at the Kulan file" test "$(readlink /etc/os-release)" = ../usr/lib/os-release

# --- Desktop (real Debian extensions and GNOME 48 schemas) ---------------------------
check "Extensions (strict)" "dock and blur found under their real Debian IDs" env KULAN_STRICT=1 /usr/lib/kulan/desktop-setup
check "Dock settings icon" "the dock opens Kulan Settings; GNOME Settings is in the System folder, Kulan Settings isn't" bash -c "grep -q \"org.kulan.Settings.desktop'\]\" /usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override && grep -q \"'org.gnome.Settings.desktop'\" /etc/dconf/db/local.d/10-kulan-app-folders && ! grep -q org.kulan.Settings /etc/dconf/db/local.d/10-kulan-app-folders"
check "Apps installed later stay in sight" "2.10: Kulan's folders list their apps by name (no categories), so a new app is never tucked into a folder" bash -c 'f=/etc/dconf/db/local.d/10-kulan-app-folders; [ "$(grep -c "^categories=@as \[\]$" "$f")" = 2 ] && ! grep -q "^categories=\[" "$f" && ! grep -q "org.gnome.Nautilus\|org.kulan.Assistant\|org.gnome.Software" "$f"'
check "No terminal tools in the app grid" "htop, btop, Vim and Lynis are hidden (run from the terminal; Lynis is Settings › Security)" bash -c 'for a in htop btop vim lynis; do grep -qx "Hidden=true" /usr/share/kulan/xdg/applications/$a.desktop || exit 1; done' 
check "Extensions enabled" "dash-to-dock and blur-my-shell in enabled-extensions" bash -c "$(declare -f gs); gs org.gnome.shell enabled-extensions | grep -q dash-to-dock && gs org.gnome.shell enabled-extensions | grep -q blur-my-shell"
check "Dock defaults applied" "dash-to-dock reads Kulan's settings (bottom, translucent)" bash -c "$(declare -f gs); test \"\$(gs org.gnome.shell.extensions.dash-to-dock dock-position)\" = \"'BOTTOM'\" && test \"\$(gs org.gnome.shell.extensions.dash-to-dock transparency-mode)\" = \"'FIXED'\""
check "Kulan Shell enabled" "kulan-shell@kulangroup.com in enabled-extensions" bash -c "$(declare -f gs); gs org.gnome.shell enabled-extensions | grep -q kulan-shell@kulangroup.com"
check "Kulan Shell files" "GNOME 48 listed; stylesheets, Kulan menu and window-button styles present" python3 - <<'PY'
import json, os
d = "/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com"
meta = json.load(open(f"{d}/metadata.json"))
assert "48" in meta["shell-version"], meta["shell-version"]
for f in ("extension.js", "stylesheet-dark.css", "stylesheet-light.css"):
    assert os.path.getsize(f"{d}/{f}") > 0, f
js = open(f"{d}/extension.js").read()
for needle in ("KulanMenu", "kulan-logo-symbolic", "installGtkStyle", "activateRestart", "activatePowerOff"):
    assert needle in js, needle
for f in ("/usr/share/icons/hicolor/scalable/apps/kulan-logo-symbolic.svg",
          "/usr/share/kulan/gtk/gtk-4.0.css", "/usr/share/kulan/gtk/gtk-3.0.css",
          "/usr/share/kulan/gtk/dots/gtk-4.0.css", "/usr/share/kulan/gtk/dots/gtk-3.0.css",
          "/usr/share/kulan/gtk/look/gtk-4.0.css"):
    assert os.path.getsize(f) > 0, f
PY
check "Cursor" "Bibata-Modern-Classic set and installed" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.interface cursor-theme)\" = \"'Bibata-Modern-Classic'\" && test -d /usr/share/icons/Bibata-Modern-Classic"
# shellcheck disable=SC2016  # expanded by the inner bash
check "App folders" "only Utilities and System are folders for new users" bash -c 'test "$(HOME=$(mktemp -d) XDG_CONFIG_HOME="" dconf read /org/gnome/desktop/app-folders/folder-children)" = "['"'"'Kulan-Utilities'"'"', '"'"'Kulan-System'"'"']"'
check "Calmer dock" "34 px icons, quick animations" bash -c "$(declare -f gs); test \"\$(gs org.gnome.shell.extensions.dash-to-dock dash-max-icon-size)\" = 34 && test \"\$(gs org.gnome.shell.extensions.dash-to-dock animation-time)\" = 0.125"
check "Wallpaper" "Kulan light and dark wallpapers set and present" bash -c "$(declare -f gs); for k in picture-uri picture-uri-dark; do f=\$(gs org.gnome.desktop.background \$k | sed \"s|^'file://||; s|'\$||\"); test -s \"\$f\" && case \"\$f\" in /usr/share/backgrounds/kulan/*) ;; *) echo \"\$k=\$f\"; exit 1;; esac; done"
check "Wallpaper choices" "Kulan teal and classic waves, light and dark, in GNOME's list" bash -c 'for f in kulan-light kulan-dark kulan-waves-light kulan-waves-dark; do test -s /usr/share/backgrounds/kulan/$f.png || { echo "missing $f"; exit 1; }; grep -q "/usr/share/backgrounds/kulan/$f.png" /usr/share/gnome-background-properties/kulan.xml || { echo "$f not listed"; exit 1; }; done'
check "Accent color" "GNOME 48 accent = teal" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.interface accent-color)\" = \"'teal'\""
check "Fonts and icons" "Inter + Kulan icons" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.interface font-name)\" = \"'Inter 11'\" && test \"\$(gs org.gnome.desktop.interface icon-theme)\" = \"'Kulan'\" && test -f /usr/share/icons/Kulan/index.theme"
check "Privacy defaults" "old files cleared after 30 days, no usage reports" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.privacy old-files-age)\" = 'uint32 30' && test \"\$(gs org.gnome.desktop.privacy send-software-usage-stats)\" = false"
check "One welcome tour" "GNOME's own welcome popup suppressed" bash -c "$(declare -f gs); test \"\$(gs org.gnome.shell welcome-dialog-last-shown-version)\" = \"'999'\""
check "Clutter hidden" "Tour, Htop, KDE System Settings, Input Method and Tor launcher settings hidden from the app grid" python3 - <<'PY'
import os, subprocess
code = "from gi.repository import Gio; print(sorted(a.get_id() for a in Gio.AppInfo.get_all() if a.should_show()))"
env = dict(os.environ, XDG_DATA_DIRS="/usr/share/kulan/xdg:/usr/local/share:/usr/share")
shown = subprocess.run(["python3", "-c", code], env=env, capture_output=True, text=True, check=True).stdout
hidden = ["org.gnome.Tour.desktop", "htop.desktop", "systemsettings.desktop", "org.kde.systemsettings.desktop",
          "kdesystemsettings.desktop", "im-config.desktop", "torbrowser-settings.desktop"]
leaked = [h for h in hidden if f"'{h}'" in shown]
assert open("/usr/lib/environment.d/60-kulan-os.conf").read().count("/usr/share/kulan/xdg") == 1
raise SystemExit(f"still shown: {leaked}" if leaked else 0)
PY
# Prints, on one line, what GDM would read when the logo is wrong.
# shellcheck disable=SC2317,SC2012  # called through check()
login_logo_ok() {
	local v f info
	v="$(DCONF_PROFILE=gdm dconf read /org/gnome/login-screen/logo)"
	[[ "${v}" == "'/usr/share/kulan/login-wordmark.png'" ]] && return 0
	info="logo=${v:-unset};"
	for f in /etc/dconf/profile/gdm /usr/share/dconf/profile/gdm; do
		[[ -f "${f}" ]] && info+=" ${f}: $(tr '\n' ' ' < "${f}");"
	done
	echo "${info} dbs: $(ls /etc/dconf/db | tr '\n' ' ') gdm: $(ls /usr/share/gdm | tr '\n' ' ')"
	return 1
}
check "Login screen logo (Kulan)" "GDM setting points at the Kulan logo" login_logo_ok
# shellcheck disable=SC2016  # expanded by the inner bash
check "Login screen logo (Debian)" "Debian's greeter logo now resolves to Kulan's" bash -c 'f="$(sed -n "s/^logo=.\(.*\).$/\1/p" /etc/gdm3/greeter.dconf-defaults | head -n 1)"; echo "greeter logo: ${f:-none} -> $(readlink -f "${f:-/nonexistent}")"; case "$(readlink -f "${f:-/nonexistent}")" in /usr/share/kulan/vendor-logos/*) test -s "$f" ;; *) exit 1 ;; esac'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Vendor emblems" "Debian's vendor emblem links point at Kulan's" bash -c 'for f in /usr/share/icons/vendor/*/emblems/emblem-vendor*; do case "$(readlink -f "$f")" in /usr/share/kulan/vendor-emblems/*) ;; *) echo "$f"; exit 1 ;; esac; done; test "$(ls /usr/share/icons/vendor/*/emblems/ | grep -c emblem-vendor)" -eq 12'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Live session" "does nothing on installed systems; autostart entry valid" bash -c 'desktop-file-validate /etc/xdg/autostart/org.kulan.LiveSession.desktop && test -z "$(GSETTINGS_BACKEND=memory /usr/lib/kulan/live-session 2>&1)"'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Live session settings" "every screen-sleep key exists in GNOME 48" bash -c 'out="$(sed "s/^grep -qw .boot=live.*/:/" /usr/lib/kulan/live-session | GSETTINGS_BACKEND=memory sh 2>&1)"; [ -z "$out" ] || { echo "$out"; exit 1; }'
check "Disk password layout" "initramfs loads the chosen keyboard layout" grep -qx "KEYMAP=y" /etc/initramfs-tools/conf.d/kulan-keymap.conf
check "Boot splash" "Plymouth theme kulan built and selected" bash -c 'test -f /usr/share/plymouth/themes/kulan/kulan.plymouth && grep -qx "Theme=kulan" /etc/plymouth/plymouthd.conf'

# --- Security and updates ---------------------------------------------------------
check "Boot speed" "network-wait startup step switched off once" test -e /var/lib/kulan/network-wait-disabled
check "Firewall" "ufw enabled on first install" grep -qx 'ENABLED=yes' /etc/ufw/ufw.conf
check "Auto updates" "Debian security + Kulan origins allowed" bash -c 'apt-config dump | grep -q "origin=Kulan OS" && apt-config dump | grep -q "label=Debian-Security"'
check "Firefox privacy" "policies.json valid; Firefox uses the system (encrypted) DNS" python3 -c 'import json; p = json.load(open("/etc/firefox/policies/policies.json"))["policies"]; assert p["DNSOverHTTPS"] == {"Enabled": False, "Locked": True}'
check "Kulan look for apps" "Kulan navy (dark) and mist (light) on GNOME's surfaces with Debian's GTK 4; Manrope installed" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- python3 ci/check-gtk-look.py /usr/share/kulan/gtk/look/gtk-4.0.css 2>&1)"; rc=$?; echo "$out" | grep -E "^(light|dark|FAIL)" | head -n 6; test $rc = 0 && fc-list | grep -qi manrope'
check "Kulan look for older apps" "the GTK 3 accent themes carry Kulan's navy and mist" bash -c 'grep -q "window_bg_color #09182d" /usr/share/themes/Kulan-teal-dark/gtk-3.0/gtk.css && grep -q "sidebar_bg_color #e6ebf1" /usr/share/themes/Kulan-teal/gtk-3.0/gtk.css'
check "Kulan app icons" "the 2.4 icon family is in the Kulan icon theme and GTK finds it" bash -c 'grep -q "^Directories=scalable/apps," /usr/share/icons/Kulan/index.theme && test "$(ls /usr/share/icons/Kulan/scalable/apps/*.svg | wc -l)" -ge 30 && HOME=$(mktemp -d) timeout 60 xvfb-run -a python3 -c "import gi; gi.require_version(\"Gtk\", \"4.0\"); from gi.repository import Gtk; Gtk.init(); t = Gtk.IconTheme(theme_name=\"Kulan\"); p = t.lookup_icon(\"org.gnome.Calculator\", None, 64, 1, Gtk.TextDirection.NONE, 0).get_file().get_path(); print(p); assert p.startswith(\"/usr/share/icons/Kulan/\")"'
check "Browser window buttons" "2.12: Chrome-type browsers take Kulan's look once (GTK theme), at sign-in and when one is installed" bash -c 'test -L /etc/systemd/user/default.target.wants/kulan-browser-look.path && test -L /etc/systemd/user/default.target.wants/kulan-browser-look.service && out="$(timeout 60 python3 -I -B ci/browser-look-test.py /usr/lib/kulan/browser-look 2>&1)"; echo "$out" | grep -qx "browser-look: every case passed" || { echo "$out" | tail -n 4; exit 1; }'
check "Firefox window buttons" "Kulan's round buttons loaded by Firefox's administrator configuration" bash -c 'grep -q "general.config.filename\", \"kulan.cfg\"" /usr/lib/firefox-esr/defaults/pref/kulan-autoconfig.js && grep -q "file:///usr/share/kulan/firefox/titlebar.css" /usr/lib/firefox-esr/kulan.cfg && grep -q "titlebar-close" /usr/share/kulan/firefox/titlebar.css && test "$(stat -c %U /usr/lib/firefox-esr/kulan.cfg)" = root'
if [[ -f /etc/apt/sources.list.d/kulan.sources ]]; then
	check "Update channel" "kulan.sources + key present" test -s /usr/share/keyrings/kulan-archive-keyring.gpg
	# An upgrade must move computers to this build's address (kulan.conf), even
	# when the released version pointed somewhere else.
	check "Update channel address" "kulan.sources uses ${CHANNEL}" grep -qx "URIs: ${CHANNEL}" /etc/apt/sources.list.d/kulan.sources
	# Only Kulan's own packages from the channel (a stolen key can't replace Debian's), and only from its own site.
	CHANNEL_HOST="$(sed -E 's#^https://([^/:]+).*#\1#' <<<"${CHANNEL}")"
	check "Update channel pin" "only Kulan's own packages from ${CHANNEL_HOST}; anything else from it never" bash -c "
		! apt-cache policy 2>&1 | grep -q '^[EW]:' &&
		grep -q 'Pin: origin \"${CHANNEL_HOST}\"' /etc/apt/preferences.d/50-kulan-channel &&
		apt-cache policy kulan-base | grep -Eq '^ +(\*\*\* )?[0-9][^ ]* 500$' &&
		apt-cache policy kulan-base | grep -q 'https://${CHANNEL_HOST}/' &&
		grep -q 'origin=Kulan OS,site=${CHANNEL_HOST}' /etc/apt/apt.conf.d/52kulan-auto-upgrades"
fi

# --- Update Center ------------------------------------------------------------------
check "Update status" "status file written by the package" python3 -c 'import json; d = json.load(open("/var/lib/kulan/updates.json")); assert d["state"] == "idle" and isinstance(d["available"], list), d'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Update hooks" "apt keeps the status current (3 hooks)" bash -c 'test "$(apt-config dump | grep -c /usr/lib/kulan/update-status)" -eq 3'
check "Update status refresh" "refresh runs and stays idle" bash -c '/usr/lib/kulan/update-status refresh && grep -q "\"state\": \"idle\"" /var/lib/kulan/updates.json'
check "Auto-update switch" "update-run auto-off then auto-on" bash -c '/usr/lib/kulan/update-run auto-off && apt-config dump APT::Periodic::Unattended-Upgrade | grep -q "\"0\"" && /usr/lib/kulan/update-run auto-on && apt-config dump APT::Periodic::Unattended-Upgrade | grep -q "\"1\""'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Interrupted update finished" "update-run install first finishes a package an earlier update left half-installed" bash -c 'd=$(mktemp -d); cd "$d" && apt-get download -q hello >/dev/null 2>&1 && dpkg --unpack hello_*.deb >/dev/null 2>&1 && dpkg --audit | grep -q hello && out="$(/usr/lib/kulan/update-run install 2>&1)"; echo "$out" | grep -i "interrupted" | head -n 2; ok=0; dpkg-query -W -f="\${Status}" hello 2>/dev/null | grep -qx "install ok installed" && test -z "$(dpkg --audit)" && ok=1; dpkg --purge hello >/dev/null 2>&1; [ "$ok" = 1 ]'
check "Restore point before automatic updates" "taken before automatic updates take apt's lock (apt-daily-upgrade.service), not in apt's hook; without restore points set up it does nothing" bash -c '! apt-config dump | grep -q "restore-points before-auto-update" && grep -q "restore-points before-auto-update" /usr/lib/systemd/system/apt-daily-upgrade.service.d/60-kulan-restore-point.conf && rm -f /var/lib/kulan/auto-update-restore-point && /usr/lib/kulan/restore-points before-auto-update && test ! -e /var/lib/kulan/auto-update-restore-point'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Updates can't leave it broken" "installs run as their own service and survive their caller; waits for automatic updates; interrupted and running states; the start-up menu after a failed start-up, with Undo the last update; signed start-up's boot counting; undo needs power; timers and time limits" bash -c 'out="$(bash ci/update-recovery-test.sh / 2>&1)"; echo "$out" | grep -qx "update recovery: every case passed" || { echo "$out" | grep -v "^ok:" | tail -n 6; exit 1; }'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Start-up repair and menu services" "an interrupted update is finished at start-up (only then), start-ups are noted, the start-up menu's undo is ready" bash -c 'for u in kulan-update-repair.service kulan-boot-ok.service kulan-undo-at-start.service; do systemctl is-enabled "$u" >/dev/null || { echo "$u not enabled"; exit 1; }; done; /usr/lib/kulan/update-run needs-repair && { echo "says repair is needed on a healthy system"; exit 1; }; test -x /etc/grub.d/01_kulan-boot-check && test -x /etc/grub.d/12_kulan-undo'
check "Restart after automatic updates" "a new kernel from automatic updates shows as restart needed" bash -c 'mkdir -p /run && echo linux-image-test > /run/reboot-required.pkgs && touch /run/reboot-required && /usr/lib/kulan/update-status refresh && python3 -c "import json; d = json.load(open(\"/var/lib/kulan/updates.json\")); assert \"linux-image-test\" in d[\"restart_required\"], d[\"restart_required\"]"; ok=$?; rm -f /run/reboot-required /run/reboot-required.pkgs; /usr/lib/kulan/update-status refresh; exit $ok'
check "Wi-Fi password words" "GNOME Shell's network password box says it in Kulan's words in English (the texts it replaces are GNOME Shell's own)" python3 -I - <<'PY'
import gettext
ours = gettext.GNUTranslations(open("/usr/share/locale/en/LC_MESSAGES/gnome-shell.mo", "rb"))
gnome = gettext.GNUTranslations(open("/usr/share/locale/en_GB/LC_MESSAGES/gnome-shell.mo", "rb"))
for msgid in [k for k in ours._catalog if k]:
    assert msgid in gnome._catalog, f"not one of GNOME Shell's texts: {msgid!r}"
used = gettext.translation("gnome-shell", "/usr/share/locale", languages=["en_US"])
assert used.gettext("Authentication required") == "Password needed"
PY
check "A failing setup step can't block updates" "when one of Kulan's desktop setup steps fails, kulan-desktop-settings still configures (a warning, not a failure)" bash -c 'h=/usr/lib/kulan/boot-splash-setup; cp -p "$h" /tmp/splash.bak && printf "#!/bin/sh\necho simulated failure >&2\nexit 1\n" > "$h"; out="$(/var/lib/dpkg/info/kulan-desktop-settings.postinst configure 2>&1)"; rc=$?; cp -p /tmp/splash.bak "$h"; /var/lib/dpkg/info/kulan-desktop-settings.postinst configure >/dev/null 2>&1; echo "$out" | grep "^W: kulan-desktop-settings" | head -n 2; echo "postinst exit: $rc"; [ "$rc" -eq 0 ] && echo "$out" | grep -q "boot-splash-setup.* runs again at the next update"'
check "Firmware updates" "device firmware from fwupd shows in Software Update" bash -c 'printf "{\"Devices\": [{\"Name\": \"Test Dock\", \"DeviceId\": \"abc\", \"Version\": \"1.0\", \"Releases\": [{\"Version\": \"1.1\", \"Summary\": \"Fixes\"}]}]}" > /var/lib/kulan/firmware-updates.json && /usr/lib/kulan/update-status refresh && python3 -c "import json; d = json.load(open(\"/var/lib/kulan/updates.json\")); f = [u for u in d[\"available\"] if u.get(\"kind\") == \"firmware\"]; assert f and f[0][\"name\"] == \"Test Dock\" and f[0][\"to\"] == \"1.1\", f"; ok=$?; rm -f /var/lib/kulan/firmware-updates.json; /usr/lib/kulan/update-status refresh; exit $ok'
check "Anonymous count" "countme's own tests: exactly version, PC or Mac, and rough age; once a week; off means nothing is sent" /usr/lib/kulan/countme self-test
check "Anonymous count on by default" "weekly count timer enabled, its own user, no off file" bash -c 'test -L /etc/systemd/system/timers.target.wants/kulan-countme.timer && getent passwd kulan-countme >/dev/null && test ! -e /etc/kulan/no-countme && grep -qx "ConditionPathExists=!/etc/kulan/no-countme" /usr/lib/systemd/system/kulan-countme.service'
check "What the count sends" "only version, PC or Mac, age and the week (this Monday); the version matches Kulan OS" python3 -c '
import json, subprocess
d = json.loads(subprocess.run(["/usr/lib/kulan/countme", "show"], capture_output=True, text=True, check=True).stdout)
vid = [l.split("=", 1)[1].strip().strip("\"") for l in open("/usr/lib/os-release") if l.startswith("VERSION_ID=")][0]
assert d["on"] is True and set(d["sends"]) == {"version", "arch", "age", "week"}, d
import datetime
today = datetime.datetime.now(datetime.timezone.utc).date()
assert d["sends"]["week"] == (today - datetime.timedelta(days=today.weekday())).isoformat(), d
assert d["sends"]["version"] == ".".join(vid.split(".")[:2]) and d["sends"]["arch"] in ("amd64", "arm64") and d["sends"]["age"] == 1, d
assert d["to"].startswith("https://"), d'
check "Count off switch" "update-run countme-off stops the count; countme-on brings it back" bash -c '/usr/lib/kulan/update-run countme-off && test -e /etc/kulan/no-countme && /usr/lib/kulan/countme send | grep -q "switched off" && /usr/lib/kulan/update-run countme-on && test ! -e /etc/kulan/no-countme'
check "Count reaches the server" "a real HTTP POST with only the four fields (to a local test server); a server from before 2.7 gets the three it knows" python3 -I -B - <<'PY2'
import http.server, importlib.machinery, importlib.util, json, threading
got = []
old_server = [False]
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        got.append((self.headers.get("Content-Type"), body, self.headers.get("Cookie")))
        self.send_response(400 if old_server[0] and "week" in body else 204); self.end_headers()
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
loader = importlib.machinery.SourceFileLoader("countme", "/usr/lib/kulan/countme")
spec = importlib.util.spec_from_loader("countme", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
url = f"http://127.0.0.1:{srv.server_port}/countme/v1"
body = {"version": "2.7", "arch": "amd64", "age": 2, "week": "2026-10-26"}
m.send(dict(body), url=url)
assert got == [("application/json", body, None)], got
got.clear(); old_server[0] = True
m.send(dict(body), url=url)
assert [g[1] for g in got] == [body, {"version": "2.7", "arch": "amd64", "age": 2}], got
srv.shutdown()
PY2
check "Lighter mode" "on, off and automatic are remembered and applied: shell flag, file search priority and zram size only when on" bash -c '
	L=/usr/lib/kulan/lighter-mode
	systemctl is-enabled kulan-lighter-mode.service >/dev/null 2>&1 || test -L /etc/systemd/system/multi-user.target.wants/kulan-lighter-mode.service || { echo "service not enabled"; exit 1; }
	/usr/lib/kulan/update-run lighter on || exit 1
	test "$(cat /etc/kulan/lighter-mode)" = on && test -e /run/kulan/lighter-mode && grep -qx "Nice=19" /run/systemd/user/localsearch-3.service.d/60-kulan-lighter.conf && grep -q "min(ram, 8192)" /etc/systemd/zram-generator.conf.d/60-kulan-lighter.conf || { echo "on not applied"; exit 1; }
	$L status | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"on\"] and d[\"choice\"] == \"on\" and d[\"active_now\"], d" || exit 1
	/usr/lib/kulan/update-run lighter off || exit 1
	test ! -e /run/kulan/lighter-mode && test ! -e /etc/systemd/zram-generator.conf.d/60-kulan-lighter.conf && test ! -e /run/systemd/user/localsearch-3.service.d/60-kulan-lighter.conf || { echo "off not applied"; exit 1; }
	/usr/lib/kulan/update-run lighter auto || exit 1
	test ! -e /etc/kulan/lighter-mode || { echo "auto kept a choice"; exit 1; }
	$L status | python3 -c "import json,sys; d=json.load(sys.stdin); m=int(open(\"/proc/meminfo\").read().split()[1]); assert d[\"choice\"] == \"auto\" and d[\"on\"] == (m < 5*1024*1024), d"
	! /usr/lib/kulan/update-run lighter sometimes 2>/dev/null'
check "Lighter mode for the assistant" "the model unloads after 2 minutes in lighter mode, 10 otherwise" python3 -c '
import sys; sys.path.insert(0, "/usr/share/kulan-assistant")
from pathlib import Path; import tempfile
from kulan_assistant import engine
engine.LIGHTER_FLAG = Path(tempfile.mkdtemp(), "flag")
assert engine.idle_minutes() == 10
engine.LIGHTER_FLAG.write_text("on")
assert engine.idle_minutes() == 2'
check "Backups page matches Déjà Dup" "every Déjà Dup setting and option Kulan Settings › Backups uses exists in Debian 13's Déjà Dup" bash -c '
	command -v deja-dup >/dev/null || apt-get install -y -q --no-install-recommends deja-dup >/dev/null 2>&1 || { echo "deja-dup not installable"; exit 1; }
	g() { GSETTINGS_BACKEND=memory gsettings list-keys "$1" | grep -qx "$2" || { echo "missing $1 $2"; exit 1; }; }
	for k in backend periodic periodic-period delete-after include-list exclude-list last-backup; do g org.gnome.DejaDup "$k"; done
	for k in uuid name icon folder; do g org.gnome.DejaDup.Drive "$k"; done
	g org.gnome.DejaDup.Local folder
	range="$(GSETTINGS_BACKEND=memory gsettings range org.gnome.DejaDup backend)"
	grep -qx "'"'"'drive'"'"'" <<<"$range" && grep -qx "'"'"'local'"'"'" <<<"$range" || { echo "backends: $range" | tr "\n" " "; exit 1; }
	help="$(deja-dup --help 2>&1)"; grep -q -- "--restore" <<<"$help" && grep -q -- "--backup" <<<"$help" || { echo "deja-dup options changed: $help"; exit 1; }'
check "Phone link firewall" "phone-link-on opens KDE Connect's ports (1714-1764) to local networks only; phone-link-off closes them" bash -c '
	# The test container may not change the firewall (no iptables rights),
	# so a stand-in ufw records exactly what update-run asks of it.
	real=/usr/sbin/ufw; log=/tmp/ufw-calls.log; rm -f "$log"
	[ -e "$real" ] && mv "$real" "$real.kulan-test"
	printf "#!/bin/sh\necho \"\$*\" >> %s\n" "$log" > "$real"; chmod +x "$real"
	/usr/lib/kulan/update-run phone-link-on >/dev/null 2>&1; on=$?
	cp "$log" /tmp/ufw-on.log; rm -f "$log"
	/usr/lib/kulan/update-run phone-link-off >/dev/null 2>&1; off=$?
	rm -f "$real"; [ -e "$real.kulan-test" ] && mv "$real.kulan-test" "$real"
	[ "$on" = 0 ] && [ "$off" = 0 ] || { echo "exit codes on=$on off=$off"; exit 1; }
	[ "$(grep -c "^allow proto .* from .* to any port 1714:1764 comment Kulan Phone link$" /tmp/ufw-on.log)" = 10 ] || { cat /tmp/ufw-on.log; exit 1; }
	grep -q "from 192.168.0.0/16" /tmp/ufw-on.log && ! grep -v " from " /tmp/ufw-on.log | grep -q . || { echo "a rule without a source network"; exit 1; }
	[ "$(grep -c "^--force delete allow proto .* from .* to any port 1714:1764$" "$log")" = 10 ] || { cat "$log"; exit 1; }'
check "Remote desktop firewall" "remote-desktop-on allows RDP (3389) from local networks only, and marks it for Sharing; off closes it" bash -c '
	real=/usr/sbin/ufw; log=/tmp/ufw-calls.log; rm -f "$log"
	[ -e "$real" ] && mv "$real" "$real.kulan-test"
	printf "#!/bin/sh\necho \"\$*\" >> %s\n" "$log" > "$real"; chmod +x "$real"
	/usr/lib/kulan/update-run remote-desktop-on >/dev/null 2>&1; on=$?; [ -e /var/lib/kulan/remote-desktop-firewall ]; marked=$?
	cp "$log" /tmp/ufw-on.log; rm -f "$log"
	/usr/lib/kulan/update-run remote-desktop-off >/dev/null 2>&1; off=$?
	rm -f "$real"; [ -e "$real.kulan-test" ] && mv "$real.kulan-test" "$real"
	[ "$on" = 0 ] && [ "$off" = 0 ] && [ "$marked" = 0 ] && [ ! -e /var/lib/kulan/remote-desktop-firewall ] || { echo "on=$on off=$off marked=$marked"; exit 1; }
	[ "$(grep -c "^allow proto tcp from .* to any port 3389 comment Kulan remote desktop$" /tmp/ufw-on.log)" = 5 ] || { cat /tmp/ufw-on.log; exit 1; }
	! grep -v " from " /tmp/ufw-on.log | grep -q . || { echo "a rule without a source network"; exit 1; }
	[ "$(grep -c "^--force delete allow proto tcp from .* to any port 3389$" "$log")" = 5 ] || { cat "$log"; exit 1; }'
check "Sharing firewall" "sharing-on allows file and media sharing (30301, 30302) from local networks only, and marks it for Sharing; off closes it" bash -c '
	real=/usr/sbin/ufw; log=/tmp/ufw-calls.log; rm -f "$log"
	[ -e "$real" ] && mv "$real" "$real.kulan-test"
	printf "#!/bin/sh\necho \"\$*\" >> %s\n" "$log" > "$real"; chmod +x "$real"
	/usr/lib/kulan/update-run sharing-on >/dev/null 2>&1; on=$?; [ -e /var/lib/kulan/sharing-firewall ]; marked=$?
	cp "$log" /tmp/ufw-on.log; rm -f "$log"
	/usr/lib/kulan/update-run sharing-off >/dev/null 2>&1; off=$?
	rm -f "$real"; [ -e "$real.kulan-test" ] && mv "$real.kulan-test" "$real"
	[ "$on" = 0 ] && [ "$off" = 0 ] && [ "$marked" = 0 ] && [ ! -e /var/lib/kulan/sharing-firewall ] || { echo "on=$on off=$off marked=$marked"; exit 1; }
	[ "$(grep -c "^allow proto tcp from .* to any port 30301,30302 comment Kulan file and media sharing$" /tmp/ufw-on.log)" = 5 ] || { cat /tmp/ufw-on.log; exit 1; }
	! grep -v " from " /tmp/ufw-on.log | grep -q . || { echo "a rule without a source network"; exit 1; }
	[ "$(grep -c "^--force delete allow proto tcp from .* to any port 30301,30302$" "$log")" = 5 ] || { cat "$log"; exit 1; }'
check "Sharing on fixed ports" "file sharing (gnome-user-share) and media sharing (Rygel) are given the ports the firewall allows" bash -c 'grep -qx "Environment=GNOME_USER_SHARE_PORT=30301" /usr/lib/systemd/user/gnome-user-share-webdav.service.d/50-kulan-port.conf && grep -qx "Environment=RYGEL_PORT=30302" /usr/lib/systemd/user/rygel.service.d/50-kulan-port.conf'
check "File sharing works" "Apache as gnome-user-share starts it, on port 30301: anyone reads the Public folder, adding files needs Kulan's password, and with the password asked for, reading does too" bash -c 'command -v curl >/dev/null || apt-get install -y -q --no-install-recommends curl >/dev/null 2>&1; out="$(timeout 120 bash ci/file-sharing-test.sh 2>&1)"; echo "$out" | grep -qx "file sharing test passed" || { echo "$out" | tail -n 6; exit 1; }'
check "Remote desktop with GNOME 48" "Sharing's settings, username and password (read back by GNOME Remote Desktop's grdctl) and certificate" bash -c 'command -v gnome-keyring-daemon >/dev/null || apt-get install -y -q --no-install-recommends gnome-keyring >/dev/null 2>&1; command -v grdctl >/dev/null || { echo "gnome-remote-desktop not installed"; exit 1; }; out="$(HOME=$(mktemp -d) timeout 120 dbus-run-session -- bash ci/sharing-test.sh 2>&1)"; echo "$out" | grep -qx "sharing self-test passed" || { echo "$out" | grep -v "fd limit" | tail -n 4; exit 1; }'
check "Phone link works with GNOME 48" "Debian's GSConnect (the Phone link add-on) is made for GNOME 48" bash -c '
	apt-get install -y -q --no-install-recommends gnome-shell-extension-gsconnect >/dev/null 2>&1 || { echo "not installable"; exit 1; }
	python3 -c "import json, sys; m = json.load(open(\"/usr/share/gnome-shell/extensions/gsconnect@andyholmes.github.io/metadata.json\")); v = [str(x) for x in m.get(\"shell-version\", [])]; sys.exit(0 if \"48\" in v else \"made for GNOME \" + \", \".join(v))"; ok=$?
	apt-get purge -y -q gnome-shell-extension-gsconnect >/dev/null 2>&1; rm -rf /usr/share/gnome-shell/extensions/gsconnect@andyholmes.github.io; exit $ok'
check "Free-space check before updates" "reads apt's plan (apt answers no and exits 1) into one number, and stops when the disk is too full" bash -c '
	space() { bash -c "set -euo pipefail; APT_LOCK=(-o DPkg::Lock::Timeout=60); die() { echo \"die: \$*\"; exit 3; }
		eval \"\$(sed -n \"/^check_space() {/,/^}/p\" /usr/lib/kulan/update-run)\"
		$1
		check_space install $2 && echo ok"; }
	for pkg in hello cowsay sl figlet; do dpkg -s "$pkg" >/dev/null 2>&1 || break; done
	apt-get -o Debug::NoLocking=1 --assume-no install "$pkg" 2>/dev/null | grep -q "^Need to get" || { echo "apt plans no download for $pkg"; exit 1; }
	out="$(space "" "$pkg" 2>&1)"; [ "$(tail -n 1 <<<"$out")" = ok ] && ! grep -q "syntax error" <<<"$out" || { echo "normal: $out" | tr "\n" " "; exit 1; }
	out="$(space "df() { printf \"Avail\\n1000\\n\"; }" "$pkg" 2>&1)"; grep -q "^die: Not enough space for these updates" <<<"$out" || { echo "full disk: $out" | tr "\n" " "; exit 1; }'
check "Password policy" "polkit action runs only update-run" bash -c 'command -v pkexec >/dev/null && grep -q "<annotate key=\"org.freedesktop.policykit.exec.path\">/usr/lib/kulan/update-run</annotate>" /usr/share/polkit-1/actions/org.kulan.updates.policy'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Restore points stay small" "apps from Flathub, assistant models and Docker are left out, on new and existing set-ups" bash -c '
	D=$(mktemp -d); mkdir -p "$D/bin"; printf "#!/bin/sh\nexit 0\n" > "$D/bin/timeshift"; chmod +x "$D/bin/timeshift"
	sed -e "s|^export PATH=|export PATH=$D/bin:|" -e "s|^CONFIG=.*|CONFIG=$D/ts.json|" /usr/lib/kulan/restore-points > "$D/rp"
	echo "{\"exclude\": [\"/root/**\", \"/var/lib/flatpak\"], \"schedule_daily\": \"true\"}" > "$D/ts.json"
	KULAN_RESTORE_FORCE=1 bash "$D/rp" setup && cp "$D/ts.json" "$D/once.json" && KULAN_RESTORE_FORCE=1 bash "$D/rp" setup && cmp -s "$D/ts.json" "$D/once.json" || exit 1
	python3 -c "import json, sys; d = json.load(open(sys.argv[1])); e = d[\"exclude\"]; assert e[:2] == [\"/root/**\", \"/var/lib/flatpak\"] and e.count(\"/var/lib/flatpak\") == 1 and \"/var/lib/kulan-assistant/models\" in e and \"/var/lib/docker\" in e and d[\"schedule_daily\"] == \"true\", d" "$D/ts.json"'
check "Restore points timer" "kulan-restore-points.timer enabled" test -L /etc/systemd/system/timers.target.wants/kulan-restore-points.timer
check "Updates in one place" "GNOME Software's Updates page off; app updates timer on; app updates listed in Software Update" bash -c "$(declare -f gs); test -L /etc/systemd/system/timers.target.wants/kulan-app-updates.timer && grep -qx 'allow-updates=false' /usr/share/glib-2.0/schemas/91_kulan-os-software.gschema.override && { ! test -f /usr/share/glib-2.0/schemas/org.gnome.software.gschema.xml || test \"\$(gs org.gnome.software allow-updates)\" = false; }"
check "App updates in the status" "Flatpak updates appear with Debian's" python3 -I -B - <<'PY'
import importlib.machinery, importlib.util, os, tempfile
loader = importlib.machinery.SourceFileLoader("us", "/usr/lib/kulan/update-status")
spec = importlib.util.spec_from_loader("us", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
with tempfile.NamedTemporaryFile("w", delete=False) as fh:
    fh.write("app/org.example.App/x86_64/stable\tExample\t2.0\nnot a ref\n")
m.APP_UPDATES = fh.name
got = m.app_updates()
os.unlink(fh.name)
assert got == [{"name": "Example", "from": "", "to": "2.0", "security": False, "kind": "app",
                "ref": "app/org.example.App/x86_64/stable", "id": "org.example.App"}], got
# Debian updates carry their source package and summary (Software Update groups
# libssl3t64 with openssl and names them in plain words).
debs = [{"name": "dpkg", "from": "1", "to": "2", "security": False}, {"name": "no-such-package", "from": "1", "to": "2", "security": False}]
m.add_sources(debs)
assert debs[0].get("source") == "dpkg" and debs[0].get("summary"), debs
assert "source" not in debs[1], debs
PY
check "Status refresh after start-up" "a timer a minute after start-up, not in the way of the login screen" bash -c 'test -L /etc/systemd/system/timers.target.wants/kulan-update-status.timer && ! test -e /etc/systemd/system/multi-user.target.wants/kulan-update-status.service'
check "Restart in ARM VMs" "fast restart only in ARM QEMU VMs; kexec hardening kept elsewhere" bash -c '
	systemctl is-enabled kulan-kexec-guard.service || { echo "guard service not enabled"; exit 1; }
	systemctl is-enabled kulan-fast-restart.service || { echo "fast-restart service not enabled"; exit 1; }
	test -x /usr/lib/systemd/system-generators/kulan-fast-restart || { echo "generator missing"; exit 1; }
	if grep -q "^kernel.kexec_load_disabled" /etc/sysctl.d/60-kulan-hardening.conf; then echo "sysctl file still sets kexec_load_disabled"; exit 1; fi
	[ "$(uname -m)" = aarch64 ] && exit 0
	if /usr/lib/kulan/fast-restart check; then echo "fast restart claims this $(uname -m) machine"; exit 1; fi
	D=$(mktemp -d)
	/usr/lib/systemd/system-generators/kulan-fast-restart "$D" || { echo "generator failed"; exit 1; }
	if test -e "$D/systemd-reboot.service.d/50-kulan-fast-restart.conf"; then echo "generator set up kexec on $(uname -m)"; exit 1; fi
	echo ok'
check "DNS through systemd-resolved" "the link to resolved's stub put back when it's a plain file, left alone otherwise" bash -c '
	systemctl is-enabled kulan-resolv-link.service >/dev/null || { echo "service not enabled"; exit 1; }
	D=$(mktemp -d)
	echo "nameserver 192.0.2.1" >"$D/resolv.conf"
	KULAN_RESOLV_ETC="$D" /usr/lib/kulan/resolv-link 2>/dev/null
	[ "$(readlink "$D/resolv.conf")" = ../run/systemd/resolve/stub-resolv.conf ] || { ls -l "$D"; exit 1; }
	rm "$D/resolv.conf"; ln -s ../run/systemd/resolve/resolv.conf "$D/resolv.conf"
	KULAN_RESOLV_ETC="$D" /usr/lib/kulan/resolv-link 2>/dev/null
	[ "$(readlink "$D/resolv.conf")" = ../run/systemd/resolve/resolv.conf ] || { echo "changed a link that was already resolved'"'"'s"; exit 1; }
	[ "$(ls -A "$D" | wc -l)" = 1 ] || { ls -A "$D"; exit 1; }
	echo ok'
check "Firmware service" "the sign-in screen doesn't wait for fwupd; the rest of its unit is Debian's" bash -c '
	G=/usr/lib/systemd/system-generators/kulan-fwupd-later
	test -x "$G" || { echo "generator missing"; exit 1; }
	D=$(mktemp -d)
	printf "[Unit]\nDescription=t\nBefore=a.service display-manager.service\n\n[Service]\nExecStart=/bin/true\n" >"$D/fwupd.service"
	KULAN_FWUPD_UNIT="$D/fwupd.service" "$G" "$D/out" || { echo "generator failed"; exit 1; }
	grep -qx "Before=a.service" "$D/out/fwupd.service" && grep -qx "ExecStart=/bin/true" "$D/out/fwupd.service" || { cat "$D/out/fwupd.service"; exit 1; }
	if [ -e /usr/lib/systemd/system/fwupd.service ]; then
		"$G" "$D/real" || exit 1
		if grep -q "^Before=.*display-manager" "$D/real/fwupd.service" 2>/dev/null; then echo "still before the sign-in screen"; exit 1; fi
		grep -q "^ExecStart=" "$D/real/fwupd.service" || { echo "no copy of Debian'"'"'s unit"; exit 1; }
	fi
	echo ok'
check "Memory guard" "systemd-oomd and Kulan's notice enabled; user apps closed under memory pressure" bash -c 'systemctl is-enabled systemd-oomd.service && systemctl is-enabled kulan-oom-notice.service && grep -qx "ManagedOOMMemoryPressure=kill" /usr/lib/systemd/system/user@.service.d/50-kulan-oomd.conf'
check "Memory notice" "names the app that was closed" python3 -I -B - <<'PY'
import importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader("oom", "/usr/lib/kulan/oom-notice")
spec = importlib.util.spec_from_loader("oom", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
sent = []
m.notify_user = lambda uid, title, body: sent.append((uid, title))
m.pwd.getpwuid = lambda uid: type("P", (), {"pw_dir": "/nonexistent", "pw_name": "x"})()
base = "/user.slice/user-1000.slice/user@1000.service/app.slice/"
m.handle("Killed " + base + "app-gnome-org.gnome.Nautilus-17.scope due to memory pressure for x", {})
m.handle("Killed /system.slice/foo.service due to memory pressure", {})
assert sent and sent[0][0] == 1000 and ("Files" in sent[0][1] or "Nautilus" in sent[0][1]), sent
assert len(sent) == 1, sent
# The id comes from a unit name its user chose: never a path, never a pipe that would hang root.
import os, tempfile
home = tempfile.mkdtemp(); os.makedirs(home + "/.local/share/applications")
os.mkfifo(home + "/.local/share/applications/hang.desktop")
assert m.desktop_name("../../../etc/passwd", home) is None and m.desktop_name("hang", home) is None
print(sent[0][1])
PY
check "Widgets and Quick Note" "Kulan Shell has them; four widgets on by default; Cmd+Shift+N" bash -c "$(declare -f gs); d=/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com; test -s \$d/widgets.js && test -s \$d/quicknote.js && test -s \$d/spotlight.js && test \"\$(gs org.kulan.desktop desktop-widgets)\" = \"['calendar', 'weather', 'prayer', 'notes']\" && gs org.kulan.desktop quick-note-shortcut | grep -q '<Shift><Super>n'"
check "More widgets" "Clock, System, To-do and Now Playing present; every module the extension imports is shipped" python3 - <<'PY'
import os, re
d = "/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com"
js = {f: open(os.path.join(d, f)).read() for f in os.listdir(d) if f.endswith(".js")}
missing = [(f, m) for f, text in js.items() for m in re.findall(r"from '\./([\w-]+\.js)'", text) if m not in js]
assert not missing, f"imports with no file: {missing}"
for name in ("ClockWidget", "SystemWidget", "TodoWidget", "MediaWidget"):
    assert f"export class {name}" in js["morewidgets.js"], name
for widget_id in ("'clock'", "'system'", "'todo'", "'media'"):
    assert widget_id in js["widgets.js"], widget_id
for css in ("stylesheet-dark.css", "stylesheet-light.css"):
    assert ".kulan-todo-check" in open(os.path.join(d, css)).read(), css
PY
# shellcheck disable=SC2016  # expanded by the inner bash
check "Undo last update" "restores the newest pre-update point after saving the current one, pauses automatic updates, restarts" bash -c '
	set -e; D=$(mktemp -d); mkdir -p "$D/bin" "$D/snaps/2026-01-01_00-00-00" "$D/snaps/2026-01-03_00-00-00" "$D/snaps/2026-01-02_00-00-00"
	printf "#!/bin/sh\necho \"\$0 \$*\" >> \"\$D/calls\"\n" | sed "s|\\\$D|$D|" > "$D/bin/timeshift"; cp "$D/bin/timeshift" "$D/bin/systemctl"; chmod +x "$D/bin/"*
	for n in 2026-01-01_00-00-00 2026-01-03_00-00-00; do printf "{\"tags\":\"O\",\"comments\":\"Before installing updates\"}" > "$D/snaps/$n/info.json"; done
	printf "{\"tags\":\"D\",\"comments\":\"\"}" > "$D/snaps/2026-01-02_00-00-00/info.json"
	sed -e "s|^export PATH=|export PATH=$D/bin:|" -e "s|^CONFIG=.*|CONFIG=$D/ts.json|" -e "s/^MIN_FREE_PERCENT=.*/MIN_FREE_PERCENT=0/" -e "s/^MIN_FREE_GIB=.*/MIN_FREE_GIB=0/" /usr/lib/kulan/restore-points > "$D/rp"; echo "{}" > "$D/ts.json"
	KULAN_RESTORE_FORCE=1 KULAN_RESTORE_SNAPSHOTS="$D/snaps" KULAN_RESTORE_USAGE="$D/u.json" KULAN_PAUSE_FILE="$D/pause" KULAN_POWER_SUPPLIES="$D/no-power-supplies" bash "$D/rp" undo-update >/dev/null
	grep -q -- "--create --scripted --comments Before undoing an update" "$D/calls"
	grep -q -- "--restore --snapshot 2026-01-03_00-00-00 --scripted --yes --skip-grub" "$D/calls"
	grep -q "systemctl reboot" "$D/calls"
	grep -q "Unattended-Upgrade \"0\"" "$D/pause" && grep -qE "Until: [0-9]+" "$D/pause"'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Short app names" "long names cut off in the app grid get short ones (GIMP, Writer); removed with the app" bash -c '
	src=/usr/share/applications/gimp.desktop; dst=/usr/share/kulan/xdg/applications/gimp.desktop; [ ! -e "$src" ] || exit 0
	grep -q "interest-noawait /usr/share/applications/libreoffice-writer.desktop" /var/lib/dpkg/info/kulan-desktop-settings.triggers || exit 1
	printf "[Desktop Entry]\nType=Application\nName=GNU Image Manipulation Program\nName[de]=GNU Bildbearbeitung\nExec=gimp\nIcon=gimp\nActions=new;\n\n[Desktop Action new]\nName=New Window\nExec=gimp\n" > "$src"
	/usr/lib/kulan/desktop-setup >/dev/null 2>&1; ok=1
	grep -qx "Name=GIMP" "$dst" && grep -qx "X-GNOME-FullName=GNU Image Manipulation Program" "$dst" && grep -qx "Name=New Window" "$dst" && grep -qx "Name\[de\]=GNU Bildbearbeitung" "$dst" && desktop-file-validate "$dst" && ok=0
	rm -f "$src"; /usr/lib/kulan/desktop-setup >/dev/null 2>&1; [ ! -e "$dst" ] || ok=1; exit $ok'
check "Fingerprint sign-in" "fprintd and its sign-in module: password prompts take a finger once one is added (they don't wait without one)" bash -c 'command -v fprintd-list >/dev/null || test -x /usr/libexec/fprintd || { echo "fprintd missing"; exit 1; }; grep -q "pam_fprintd.so" /etc/pam.d/common-auth || { echo "pam_fprintd not in common-auth"; exit 1; }; test -f /etc/pam.d/gdm-fingerprint || dpkg -s gdm3 >/dev/null 2>&1 && test -f /etc/pam.d/gdm-fingerprint || ! dpkg -s gdm3 >/dev/null 2>&1; test -s /usr/share/kulan-settings/kulan_settings/fingerprint.py'
check "One Settings app" "GNOME Settings hidden from the grid and search while Kulan Settings is there; its pages still open; the gear opens Kulan Settings" bash -c 'f=/usr/share/kulan/xdg/applications/org.gnome.Settings.desktop; if [ -f /usr/share/applications/org.gnome.Settings.desktop ]; then grep -qx "NoDisplay=true" "$f" && [ "$(sed -n "/^\[Desktop Entry\]/,/^\[/p" "$f" | grep -c "^NoDisplay=")" -eq 1 ] && grep -q "^Exec=" "$f" && ! grep -q "^Hidden=true" "$f" && desktop-file-validate "$f"; fi; grep -q "_redirectSettingsGear" /usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com/extension.js'
check "Store" "GNOME Software shown as Store with Kulan's icon; Kulan picks and Kulan's own apps in the catalogue; Kulan counted as official" bash -c 'f=/usr/share/kulan/xdg/applications/org.gnome.Software.desktop; if [ -f /usr/share/applications/org.gnome.Software.desktop ]; then grep -qx "Name=Store" "$f" && grep -qx "Icon=kulan-store" "$f" && [ "$(grep -c "^Name" "$f")" -eq 1 ] && desktop-file-validate "$f"; fi; test -s /usr/share/icons/hicolor/scalable/apps/kulan-store.svg && grep -q "GnomeSoftware::popular" /usr/share/swcatalog/xml/kulan-picks.xml && grep -q "<pkgname>kulan-fleet-admin</pkgname>" /usr/share/swcatalog/xml/kulan-apps.xml && test -s /usr/share/swcatalog/icons/kulan-apps/64x64/org.kulan.Fleet.png && grep -qx "official-repos=\[.debian-\*., .kulan-\*.\]" /usr/share/glib-2.0/schemas/91_kulan-os-software.gschema.override && [ "$(dpkg-divert --listpackage /usr/share/swcatalog/xml/org.gnome.Software.Curated.xml)" = kulan-desktop-settings ]'
check "Tags in Files" "Tags menu, dots and search are installed; tag words understood" bash -c "$(declare -f pycheck); pycheck /usr/share/nautilus-python/extensions/kulan-tags.py /usr/share/kulan/python/kulan_tags.py /usr/lib/kulan/tags-search-provider && test -s /usr/share/icons/Kulan/scalable/emblems/emblem-kulan-tag-red.svg && grep -qx 'DesktopId=org.gnome.Nautilus.desktop' /usr/share/gnome-shell/search-providers/org.kulan.Tags.search-provider.ini && python3 -c 'import sys; sys.path.insert(0, \"/usr/share/kulan/python\"); import kulan_tags as k; assert k.tag_for_words([\"tag\", \"Gray\"]) == \"grey\" and k.tag_for_words([\"red\", \"car\"]) is None and k._clean([\"Blue\", \"x\", \"red\"]) == [\"red\", \"blue\"]'"
check "Tag folders" "each tag in use gets a folder of links for Files' sidebar; real files there are kept" python3 -I -B - <<'PY'
import os, sys, tempfile
home = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = os.path.join(home, "data")
sys.path.insert(0, "/usr/share/kulan/python")
import kulan_tags as k
a, b = os.path.join(home, "a.txt"), os.path.join(home, "b.txt")
for p in (a, b):
    open(p, "w").write("x")
k.remember(a, ["red"]); k.remember(b, ["red", "blue"])
red = k.TAG_FOLDERS / "Red"
assert sorted(n for n in os.listdir(red) if not n.startswith(".")) == ["a.txt", "b.txt"] and os.readlink(red / "a.txt") == a
k.remember(b, [])
assert not (k.TAG_FOLDERS / "Blue").exists() and [n for n in os.listdir(red) if not n.startswith(".")] == ["a.txt"]
PY
if [[ ${#FILES[@]} -gt 0 ]]; then
	NAUT="$(dpkg-query -W -f '${Version}' nautilus 2>/dev/null)"
	check "Kulan Files" "Nautilus ${NAUT} with the status bar, preview pane and sidebar sections" bash -c "[[ '${NAUT}' == *+kulan* ]] && grep -aq kulan-status-bar /usr/bin/nautilus && grep -aq 'NautilusKulanPreview' /usr/bin/nautilus && grep -aq kulan-sidebar-heading /usr/bin/nautilus && grep -aq win.toggle-preview /usr/bin/nautilus"
	GUS="$(dpkg-query -W -f '${Version}' gnome-user-share 2>/dev/null)"
	check "File sharing's port" "gnome-user-share ${GUS} takes its port from GNOME_USER_SHARE_PORT" bash -c "[[ '${GUS}' == *+kulan* ]] && grep -rlaqs GNOME_USER_SHARE_PORT \$(dpkg -L gnome-user-share | grep -E \"/(libexec|bin)/\") && grep -c 'AuthName \"Please log in as the user guest\"' /usr/share/gnome-user-share/dav_user_2.4.conf | grep -qx 2"
else
	result skip "Kulan Files" "no Kulan Files package for PCs in this run"
fi
check "Calm file indexing" "file search indexing runs only when the computer is idle" grep -qx "CPUWeight=idle" /usr/lib/systemd/user/localsearch-3.service.d/50-kulan-idle.conf
# shellcheck disable=SC2016  # expanded by the inner bash
check "Restore point limits" "newest 2 update-time points kept, own ones never deleted, size recorded" bash -c '
	set -e; D=$(mktemp -d); mkdir -p "$D/bin" "$D/snaps"
	printf "#!/bin/sh\ncase \"\$1\" in --delete) rm -rf \"\$KULAN_RESTORE_SNAPSHOTS/\$3\";; esac\n" > "$D/bin/timeshift"; chmod +x "$D/bin/timeshift"
	sed -e "s|^export PATH=|export PATH=$D/bin:|" -e "s|^CONFIG=.*|CONFIG=$D/ts.json|" /usr/lib/kulan/restore-points > "$D/rp"; echo "{}" > "$D/ts.json"
	mk() { mkdir -p "$D/snaps/$1"; printf "{\"tags\":\"%s\",\"comments\":\"%s\"}" "$2" "$3" > "$D/snaps/$1/info.json"; }
	mk 2026-01-01_00-00-00 O "Before installing updates"; mk 2026-01-02_00-00-00 O "Created in Update Center"
	mk 2026-01-03_00-00-00 O "Before installing updates"; mk 2026-01-04_00-00-00 "D" ""; mk 2026-01-05_00-00-00 O "Before installing updates"
	KULAN_RESTORE_FORCE=1 KULAN_RESTORE_SNAPSHOTS="$D/snaps" KULAN_RESTORE_USAGE="$D/usage.json" bash "$D/rp" tidy
	test "$(ls "$D/snaps" | tr "\n" " ")" = "2026-01-02_00-00-00 2026-01-03_00-00-00 2026-01-04_00-00-00 2026-01-05_00-00-00 " || { ls "$D/snaps"; exit 1; }
	python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d[\"count\"]==4 and d[\"own\"]==1 and d[\"bytes\"]>0, d" "$D/usage.json"'
check "Release notes" "notes exist for this version" python3 -c 'import json, shlex; v = [shlex.split(l.split("=",1)[1])[0] for l in open("/usr/lib/os-release") if l.startswith("VERSION_ID=")][0]; assert any(n["version"] == v for n in json.load(open("/usr/share/kulan/release-notes.json"))), v'
check "Kulan Settings compiles" "every Python file parses" bash -c "$(declare -f pycheck); pycheck /usr/bin/kulan-settings /usr/lib/kulan/update-status /usr/share/kulan-settings/kulan_settings/*.py"
# shellcheck disable=SC2016  # expanded by the inner bash
check "3D acceleration in About" "drawn by the graphics chip, or by the processor (a virtual machine without 3D), read like GNOME Settings does" python3 -I -B - <<'PYACC'
import os, stat, sys, tempfile
sys.path.insert(0, "/usr/share/kulan-settings")
import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from kulan_settings import about
d = tempfile.mkdtemp()
def helper(text):
    p = os.path.join(d, f"r{len(os.listdir(d))}")
    with open(p, "w") as f:
        f.write(f"#!/bin/sh\necho '{text}'\n")
    os.chmod(p, 0o755)
    return p
on, name = about.acceleration(helper("llvmpipe (LLVM 19.1.7, 256 bits)"))
assert on is False and name.startswith("llvmpipe"), (on, name)
assert about.acceleration_text(on, name).startswith("Off, using llvmpipe")
on, name = about.acceleration(helper("Mesa Intel(R) UHD Graphics 620 (KBL GT2)"))
assert on is True and about.acceleration_text(on, name) == "On (Mesa Intel(R) UHD Graphics 620 (KBL GT2))"
assert about.acceleration(helper("virgl (Apple M5)"))[0] is True, "a virtual machine with 3D on"
assert about.acceleration(os.path.join(d, "missing")) == (None, "") and about.acceleration_text(None, "") == ""
assert about.acceleration(helper("")) == (None, "")
# Kulan's own helper runs without a screen: a renderer name, or nothing (exit 1); never a crash.
import subprocess
r = subprocess.run(["/usr/lib/kulan/graphics-renderer"], capture_output=True, text=True, timeout=60)
assert (r.returncode == 0 and r.stdout.strip()) or (r.returncode == 1 and not r.stdout.strip()), (r.returncode, r.stdout, r.stderr[-300:])
assert "Traceback" not in r.stderr, r.stderr[-300:]
print("renderer here:", r.stdout.strip() or "unknown")
print("acceleration check passed")
PYACC
check "Kulan Settings opens" "every page builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-settings --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback|Error:" | head -n 3; echo "$out" | grep -qxE "(2[1-9]|[3-9][0-9])/\1 pages built" && ! echo "$out" | grep -q Traceback'
# Saved passwords after automatic sign-in (2.6.1): against a real GNOME Keyring on a private bus.
cat >/tmp/keyring-test.sh <<'KEYRING'
#!/bin/bash
export HOME="$(mktemp -d)" XDG_RUNTIME_DIR="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1
unset XDG_DATA_HOME XDG_STATE_HOME
eval "$(printf 'sign-in-pw' | gnome-keyring-daemon --unlock --components=secrets 2>/dev/null)" || exit 1
cd /usr/share/kulan-settings && python3 -I -B -c 'import sys; sys.path.insert(0, "."); from kulan_settings import keyring; keyring.self_test()' || exit 1
# The sign-in job does nothing (and succeeds) when Settings didn't ask for it.
/usr/lib/kulan/keyring-merge && test ! -e "$HOME/.local/state/kulan/keyring-merge"
KEYRING
check "Saved passwords fix" "moving them into the login keyring, removing the keyring password, wrong passwords refused (real GNOME Keyring); the sign-in job does nothing unless asked" bash -c 'command -v gnome-keyring-daemon >/dev/null || apt-get install -y -q --no-install-recommends gnome-keyring >/dev/null 2>&1 || { echo "gnome-keyring not installable"; exit 1; }; desktop-file-validate /etc/xdg/autostart/org.kulan.KeyringMerge.desktop && test -x /usr/lib/kulan/keyring-merge && timeout 120 dbus-run-session -- bash /tmp/keyring-test.sh >/tmp/keyring.out 2>&1; rc=$?; grep -v "fd limit" /tmp/keyring.out | tail -n 4; test "$rc" = 0 && grep -qx "keyring self-test passed" /tmp/keyring.out'
check "Update Center moved" "kulan-updates opens Kulan Settings; old shortcut hidden" bash -c 'grep -q "kulan-settings updates" /usr/bin/kulan-updates && grep -qx "NoDisplay=true" /usr/share/applications/org.kulan.Updates.desktop && dpkg -S /usr/bin/kulan-updates | grep -q "^kulan-settings:"'
check "Store pages" "Kulan apps have valid AppStream pages" appstreamcli validate --no-net /usr/share/metainfo/org.kulan.Settings.metainfo.xml /usr/share/metainfo/org.kulan.Welcome.metainfo.xml
check "Kulan options" "org.kulan.desktop schema installed with Kulan defaults" bash -c "$(declare -f gs); test \"\$(gs org.kulan.desktop animation-speed)\" = \"'normal'\" && test \"\$(gs org.kulan.desktop kulan-menu)\" = true && test \"\$(gs org.kulan.desktop automatic-style)\" = false"
check "Sound theme" "Kulan chimes installed and chosen" bash -c "$(declare -f gs); test -f /usr/share/sounds/kulan/index.theme && test -s /usr/share/sounds/kulan/stereo/message-new-instant.oga && test \"\$(gs org.gnome.desktop.sound theme-name)\" = \"'kulan'\""
check "Quiet sign-in" "2.10: GNOME's USB protection (needs USBGuard) off, and no media keys GNOME can't bind (hibernate, repeat)" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.privacy usb-protection)\" = false && test \"\$(gs org.gnome.settings-daemon.plugins.media-keys hibernate-static)\" = '@as []' && test \"\$(gs org.gnome.settings-daemon.plugins.media-keys playback-repeat-static)\" = '@as []'"
check "Mac shortcuts" "Super+Space search, Super+Shift+3/4/5 screenshots, Kulan Live Text and clipboard keys" bash -c "$(declare -f gs); gs org.kulan.desktop search-shortcut | grep -q \"'<Super>space'\" && test \"\$(gs org.kulan.desktop search-style)\" = \"'spotlight'\" && ! gs org.gnome.shell.keybindings toggle-overview | grep -q \"'<Super>space'\" && gs org.gnome.shell.keybindings screenshot | grep -q '<Shift><Super>3' && gs org.gnome.shell.keybindings show-screenshot-ui | grep -q '<Shift><Super>4' && gs org.kulan.desktop live-text-shortcut | grep -q '<Shift><Super>t' && test \"\$(gs org.kulan.desktop clipboard-history)\" = false && test \"\$(gs org.gnome.desktop.interface icon-theme)\" = \"'Kulan'\" && test -f /usr/share/icons/Kulan/index.theme"
# shellcheck disable=SC2016  # expanded by the inner bash
check "Helper refuses bad input" "computer name, DNS and add-on values are checked before anything runs as root" bash -c 'for args in "set-hostname Bad_Name" "set-hostname -x" "set-hostname a.b"; do out="$(/usr/lib/kulan/update-run $args 2>&1)"; echo "$out" | grep -q "letters, numbers and dashes" || { echo "not refused by the check: $args"; exit 1; }; done; for args in "dns bogus" "dns" "addon-install ../x" "addon-remove nope" "nonsense"; do if /usr/lib/kulan/update-run $args >/dev/null 2>&1; then echo "accepted: $args"; exit 1; fi; done'
check "Dock default" "always visible" bash -c "$(declare -f gs); test \"\$(gs org.gnome.shell.extensions.dash-to-dock dock-fixed)\" = true"
# shellcheck disable=SC2016  # expanded by the inner bash
check "Pointer in VMs" "GNOME draws the pointer itself in a virtual machine (generator installed and runs)" bash -c 'g=/usr/lib/systemd/user-environment-generators/60-kulan-vm-cursor; test -x "$g" && out="$("$g")" && { [ -z "$out" ] || [ "$out" = MUTTER_DEBUG_DISABLE_HW_CURSORS=1 ]; }'
check "Files defaults" "64 px icons, folders first, readable dates, Delete permanently, Bin in the dock" bash -c "$(declare -f gs); test \"\$(gs org.gnome.nautilus.preferences default-folder-viewer)\" = \"'icon-view'\" && test \"\$(gs org.gnome.nautilus.icon-view default-zoom-level)\" = \"'small-plus'\" && test \"\$(gs org.gnome.nautilus.preferences date-time-format)\" = \"'simple'\" && test \"\$(gs org.gnome.nautilus.preferences show-delete-permanently)\" = true && test \"\$(gs org.gtk.gtk4.Settings.FileChooser sort-directories-first)\" = true && test \"\$(gs org.gnome.shell.extensions.dash-to-dock show-trash)\" = true"
check "Folder Colour" "the Files extension parses and every colour has its icon" bash -c "$(declare -f pycheck); pycheck /usr/share/nautilus-python/extensions/kulan-folder-colour.py && for c in red orange yellow green teal blue purple grey; do test -s /usr/share/icons/Kulan/scalable/places/folder-kulan-\$c.svg || exit 1; done"
check "Templates" "New Document templates are valid OpenDocument files" python3 - <<'PY'
import zipfile
from pathlib import Path
d = Path("/usr/share/kulan/templates")
names = sorted(p.name for p in d.iterdir())
assert names == ["Document.odt", "Markdown Note.md", "Presentation.odp", "Spreadsheet.ods", "Text File.txt"], names
for p, kind in ((d / "Document.odt", "text"), (d / "Spreadsheet.ods", "spreadsheet"), (d / "Presentation.odp", "presentation")):
    with zipfile.ZipFile(p) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED, p
        assert z.read("mimetype").decode() == f"application/vnd.oasis.opendocument.{kind}", p
        assert z.testzip() is None, p
        for member in ("content.xml", "styles.xml", "META-INF/manifest.xml"):
            __import__("xml.etree.ElementTree").etree.ElementTree.fromstring(z.read(member))
PY
check "Older apps' theme" "adw-gtk3 and the 18 Kulan accent themes installed, Kulan-teal chosen, Qt apps follow GTK" python3 - <<'PY'
import os, re, subprocess
T = "/usr/share/themes"
for base in ("adw-gtk3", "adw-gtk3-dark"):
    for f in ("gtk-3.0/gtk.css", "gtk-4.0/gtk.css", "index.theme", "LICENSE"):
        assert os.path.getsize(f"{T}/{base}/{f}") > 0, (base, f)
names = [n for n in os.listdir(T) if n.startswith("Kulan-")]
assert len(names) == 18, names
for n in names:
    for gtk in ("gtk-3.0", "gtk-4.0"):
        css = open(f"{T}/{n}/{gtk}/gtk.css").read()
        target = re.search(r'@import url\("([^"]+)"\)', css).group(1)
        assert os.path.exists(os.path.normpath(f"{T}/{n}/{gtk}/{target}")), (n, target)
out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"], capture_output=True, text=True,
                     env={"GSETTINGS_BACKEND": "memory", "PATH": "/usr/bin:/bin"}).stdout.strip()
assert out == "'Kulan-teal'", out
assert "QT_QPA_PLATFORMTHEME=gtk3" in open("/usr/lib/environment.d/60-kulan-os.conf").read()
assert "QT_QPA_PLATFORM=xcb;wayland" in open("/usr/lib/environment.d/60-kulan-os.conf").read()
PY
check "Terminal colours" "Kulan palette for Ptyxis (light and dark, 16 colours each) chosen by default" python3 - <<'PY'
import configparser, re, subprocess
c = configparser.ConfigParser(); c.optionxform = str
c.read("/usr/share/kulan/ptyxis/Kulan.palette")
assert c["Palette"]["Name"] == "Kulan"
for mode in ("Light", "Dark"):
    keys = ["Foreground", "Background", "Cursor"] + [f"Color{i}" for i in range(16)]
    for k in keys:
        assert re.fullmatch(r"#[0-9a-f]{6}", c[mode][k]), (mode, k)
out = subprocess.run(["gsettings", "get", "org.gnome.Ptyxis.Profile:/org/gnome/Ptyxis/Profiles/test/", "palette"],
                     capture_output=True, text=True, env={"GSETTINGS_BACKEND": "memory", "PATH": "/usr/bin:/bin"})
assert out.stdout.strip() == "'Kulan'", out.stdout + out.stderr
PY
check "Shell defaults" "zsh and bash load Kulan's prompt and helpers without errors; bash gets them once" bash <<'SH'
set -u
h=$(mktemp -d); export HOME=$h TERM=xterm-256color XDG_CONFIG_HOME= XDG_STATE_HOME= XDG_DATA_HOME=
cp /etc/skel/.zshrc "$h/"
err="$(zsh -i -c 'print -P -- "$PROMPT"; whence -w _zsh_autosuggest_start _zsh_highlight; [[ -o autocd ]] || exit 3' 2>&1 >"$h/zsh.out")" || { echo "zsh failed: $err"; exit 1; }
[ -z "$err" ] || { echo "zsh printed: $err"; exit 1; }
grep -q "_zsh_highlight: function" "$h/zsh.out" || { cat "$h/zsh.out"; exit 1; }
cp /etc/skel/.bashrc "$h/.bashrc"
sh /usr/lib/kulan/user-setup && rm -f "$h/.local/state/kulan/user-setup" && sh /usr/lib/kulan/user-setup
[ "$(grep -c /usr/share/kulan/shell/kulan.bash "$h/.bashrc")" -eq 1 ] || { echo "bashrc line count wrong"; exit 1; }
out="$(bash -i -c '__kulan_prompt; printf "%s" "$PS1"' 2>"$h/bash.err")"
grep -v -e "job control" -e "terminal process group" "$h/bash.err" | grep . && exit 1
case "$out" in *'\u@\h'*) ;; *) echo "bash prompt: $out"; exit 1 ;; esac
SH
check "Terminal power tools" "fzf, ripgrep, fd, bat, eza, zoxide and btop installed; zsh uses them; a missing command names its package" bash <<'SH'
set -u
for c in fzf rg fdfind batcat eza zoxide btop; do command -v "$c" >/dev/null || { echo "missing $c"; exit 1; }; done
h=$(mktemp -d); export HOME=$h TERM=xterm-256color
cp /etc/skel/.zshrc "$h/"
out="$(zsh -i -c 'alias ls; alias fd; alias bat; (( $+functions[__zoxide_z] )) && print zoxide-on; bindkey "^[k"; cowsay hi' 2>&1)"
for want in "ls='eza" "fd=fdfind" "bat=batcat" "zoxide-on" "_kulan_ask_last" "sudo apt install cowsay"; do
	grep -qF -- "$want" <<<"$out" || { echo "no \"$want\" in: $out"; exit 1; }
done
SH
# shellcheck disable=SC2016  # expanded by the inner bash
check "Apps ready after sign-in" "kulan-prewarm timer on for every account; skips the live session and low memory" bash -c 'test -L /etc/systemd/user/timers.target.wants/kulan-prewarm.timer && sh -n /usr/lib/kulan/prewarm && grep -q "run/live/medium" /usr/lib/kulan/prewarm && grep -qx "IOSchedulingClass=idle" /usr/lib/systemd/user/kulan-prewarm.service'
# shellcheck disable=SC2016  # expanded by the inner python
check "One search" "Kulan Settings' search finds its pages (Printers, Sharing and Get help too) and Online accounts (now Kulan's own page); GNOME Settings' own source off; Ask Kulan result" bash -c 'python3 - <<"PY" && gsettings get org.gnome.desktop.search-providers disabled | grep -q "org.gnome.Settings.desktop"
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("sp", "/usr/lib/kulan/settings-search-provider")
spec = importlib.util.spec_from_loader("sp", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
pages = m.load_pages()
assert m.search(pages, ["printer"]) == ["printers"], m.search(pages, ["printer"])
assert m.search(pages, ["sharing"]) == ["sharing"], m.search(pages, ["sharing"])
assert m.search(pages, ["remote", "desktop"])[0] == "sharing", m.search(pages, ["remote", "desktop"])
assert m.search(pages, ["get", "help"]) == ["help"], m.search(pages, ["get", "help"])
assert m.search(pages, ["online", "accounts"]) == ["accounts"], m.search(pages, ["online", "accounts"])
assert "appearance" in m.search(pages, ["dark", "mode"]) and "wifi" in m.search(pages, ["wi-fi"])
sys.path.insert(0, "/usr/share/kulan-assistant")
from kulan_assistant import search
meta = search.Provider.meta(search.ASK_PREFIX + "make the text bigger")
assert meta["name"].unpack() == "Ask Kulan" and meta["description"].unpack() == "make the text bigger"
PY'
# shellcheck disable=SC2016  # expanded by the inner python
check "Printers page" "reads CUPS's printer list, default and waiting jobs; finds network and USB printers" python3 -c 'import sys; sys.path.insert(0, "/usr/share/kulan-settings"); import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from kulan_settings import printers as p; got = p.parse_printers("printer A disabled since x -\n\tUnplugged\n\tDescription: Office\nprinter B is idle.  enabled since x\n\tDescription: Home\n", "system default destination: B", "B-3 me 1 x\n"); assert [(x["description"], x["state"], x["reason"], x["default"], x["jobs"]) for x in got] == [("Office", "disabled", "Unplugged", False, 0), ("Home", "idle", "", True, 1)], got; f = p.parse_found("Device: uri = usb://Brother/HL?serial=1\n        info = Brother HL\nDevice: uri = file:/dev/null\n", set()); assert f == [{"uri": "usb://Brother/HL?serial=1", "name": "Brother HL", "where": "Plugged in (USB)"}], f'
check "Update History groups an update" "apt runs of one update, minutes apart, show as one entry" python3 -I -B -c 'import sys; sys.path.insert(0, "/usr/share/kulan-settings"); import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from kulan_settings import updates as u; t = 1_800_000_000; runs = [{"time": t, "packages": ["kulan-base"], "kulan_series": None, "automatic": False}, {"time": t - 300, "packages": ["kulan-base", "firefox-esr"], "kulan_series": "2.6", "automatic": True}, {"time": t - 360, "packages": ["libc6"], "kulan_series": None, "automatic": True}, {"time": t - 400, "packages": ["kulan-settings"], "kulan_series": None, "automatic": True}, {"time": t - 86400, "packages": ["vlc"], "kulan_series": None, "automatic": True}]; m = u.merge_runs(runs); assert [(r["time"], r["kulan_series"], r["packages"]) for r in m] == [(t, None, ["kulan-base"]), (t - 300, "2.6", ["kulan-base", "firefox-esr", "libc6", "kulan-settings"]), (t - 86400, None, ["vlc"])], m; print("merge ok")'
check "kulan command" "help, info, doctor and settings work; mistakes get a usage message" bash -c 'kulan help | grep -q "kulan doctor" && kulan info | grep -q "^  Memory" && { kulan doctor; rc=$?; [ "$rc" -le 1 ]; } && { kulan settings dark-mode maybe 2>/dev/null; [ $? -eq 64 ]; } && { kulan nonsense 2>/dev/null; [ $? -eq 64 ]; } && { kulan why 2>/dev/null; [ $? -eq 1 ]; }'
check "Terminal questions" "kulan ask and kulan why use the Terminal instructions (Debian, apt, nothing run)" python3 -c 'import sys; sys.path.insert(0, "/usr/share/kulan-assistant"); from kulan_assistant import tasks; a = tasks.messages_for("answer", "How do I list files?"); w = tasks.messages_for("terminal-error", "Command: lss\nExit code: 127"); assert "Debian 13" in a[0]["content"] and "Debian 13" in w[0]["content"] and "writing helper" not in a[0]["content"], a'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Projects and templates" "first sign-in makes Projects (in the sidebar) and the templates, once" bash -c 'h=$(mktemp -d) && desktop-file-validate /etc/xdg/autostart/org.kulan.UserSetup.desktop && HOME=$h XDG_CONFIG_HOME= XDG_STATE_HOME= sh /usr/lib/kulan/user-setup && test -d $h/Projects && grep -qx "file://$h/Projects" $h/.config/gtk-3.0/bookmarks && t=$(HOME=$h xdg-user-dir TEMPLATES) && test -f "$t/Document.odt" && rm "$t/Document.odt" && HOME=$h XDG_CONFIG_HOME= XDG_STATE_HOME= sh /usr/lib/kulan/user-setup && test ! -e "$t/Document.odt" && test "$(wc -l < $h/.config/gtk-3.0/bookmarks)" -eq 1 && test "$(readlink $h/.local/share/org.gnome.Ptyxis/palettes/Kulan.palette)" = /usr/share/kulan/ptyxis/Kulan.palette'
check "Add-on list" "every add-on names its apps; the helper refuses anything else" python3 - <<'PY'
import json, re, subprocess
addons = json.load(open("/usr/share/kulan/addons.json"))
ids = [a["id"] for a in addons]
assert len(ids) == len(set(ids)), ids
assert {"apps", "chromium", "devkit", "assistant", "learning-pack", "steam", "phone-link", "fleet-admin"} <= set(ids), ids
assert next(a for a in addons if a["id"] == "steam").get("arch") == ["amd64"]
for a in addons:
    assert a.get("title") and a.get("description") and (a.get("packages") or a.get("flatpak")), a
    for port in a.get("firewall", []):
        assert re.fullmatch(r"[0-9]{1,5}(:[0-9]{1,5})?/(tcp|udp)", port), port
for bad in ("nope", "x;y", "../etc", ""):
    r = subprocess.run(["/usr/lib/kulan/update-run", "addon-install", bad], capture_output=True, text=True)
    assert r.returncode != 0, bad
PY
# Every Debian package an add-on installs, and the security and privacy apps
# the ISO adds in 2.2, must exist in Debian 13 (main).
# shellcheck disable=SC2016  # expanded by the inner bash
check "Package names" "add-on and security packages exist in Debian 13" bash -c 'missing=""; for p in $(python3 -c "import json; print(\" \".join(p for a in json.load(open(\"/usr/share/kulan/addons.json\")) for p in a.get(\"packages\", []) + a.get(\"includes\", []) if not p.startswith(\"kulan-\")))") keepassxc metadata-cleaner network-manager-openvpn-gnome apparmor-profiles-extra lynis gnome-sushi gnome-characters gnome-weather gnome-clocks baobab gnome-shell-extension-tiling-assistant tesseract-ocr tesseract-ocr-eng power-profiles-daemon gir1.2-dbusmenu-glib-0.4 systemd-zram-generator gnome-shell-extension-desktop-icons-ng python3-nautilus poppler-utils tracker-miner-fs gir1.2-tsparql-3.0 ptyxis zsh zsh-autosuggestions zsh-syntax-highlighting fzf ripgrep fd-find bat eza zoxide btop qt5-gtk-platformtheme qt6-gtk-platformtheme geary simple-scan gir1.2-goa-1.0 fprintd libpam-fprintd; do apt-cache show "$p" >/dev/null 2>&1 || missing="$missing $p"; done; [ -z "$missing" ] || { echo "missing:$missing"; exit 1; }'
check "Developer kit" "kulan-devkit installs cleanly (simulated)" apt-get -s -q install "./${DEVKIT[0]}"
check "Office and media apps" "kulan-apps installs cleanly (simulated)" apt-get -s -q install "./${APPS[0]}"
# shellcheck disable=SC2016  # ${Depends} and ${Status} are dpkg-query fields
check "Account picture" "new accounts start with Kulan's picture (Debian's kept aside); an account still on Debian's logo gets Kulan's, written as its owner, never through a link" bash -c 'cmp -s /etc/skel/.face /usr/share/kulan/faces/kulan.png && dpkg-divert --list /etc/skel/.face | grep -q "by kulan-desktop-settings" || exit 1; [ -f /etc/skel/.face.debian ] || exit 0; mkdir -p /home/facetest /home/facelink && cp /etc/skel/.face.debian /home/facetest/.face && chown nobody /home/facetest/.face && cp /etc/skel/.face.debian /root/face-target && ln -s /root/face-target /home/facelink/.face && /var/lib/dpkg/info/kulan-desktop-settings.postinst configure >/dev/null 2>&1; ok=1; cmp -s /home/facetest/.face /usr/share/kulan/faces/kulan.png && cmp -s /root/face-target /etc/skel/.face.debian && test "$(stat -c %U /home/facetest/.face)" = nobody && ok=0; rm -rf /home/facetest /home/facelink /root/face-target; exit $ok'
check "Cursor files" "only Bibata Modern Classic's cursors are kept (the dpkg rule and postinst)" bash -c 'test -e /usr/share/icons/Bibata-Modern-Classic/cursors/left_ptr && test -f /etc/dpkg/dpkg.cfg.d/kulan-lean && test -z "$(find /usr/share/icons/Bibata-* -path "*/cursors/*" ! -path "/usr/share/icons/Bibata-Modern-Classic/*" \( -type f -o -type l \) 2>/dev/null | head -n 1)" && apt-get install -y -q --reinstall bibata-cursor-theme >/dev/null 2>&1 && test -e /usr/share/icons/Bibata-Modern-Classic/cursors/left_ptr && test -z "$(find /usr/share/icons/Bibata-* -path "*/cursors/*" ! -path "/usr/share/icons/Bibata-Modern-Classic/*" \( -type f -o -type l \) 2>/dev/null | head -n 1)"'
check "Upgrade path" "kulan-desktop-settings pulls in kulan-update-center, kulan-settings and Timeshift" bash -c 'dpkg-query -W -f="\${Depends}" kulan-desktop-settings | grep -q kulan-update-center && dpkg-query -W -f="\${Depends}" kulan-desktop-settings | grep -q kulan-settings && dpkg-query -W -f="\${Status}" timeshift | grep -q "ok installed"'

# --- Welcome app against Debian's real GTK and libadwaita ------------------------------
check "Icons exist" "every icon Kulan's apps and shell use is in Kulan, Adwaita, hicolor or libadwaita" python3 - <<'PY'
import pathlib, re
sources = [*pathlib.Path("/usr/share/kulan-settings").rglob("*.py"), pathlib.Path("/usr/bin/kulan-welcome"),
           pathlib.Path("/usr/share/kulan/addons.json"),
           *pathlib.Path("/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com").glob("*.js")]
names = set()
for f in sources:
    t = f.read_text()
    names |= set(re.findall(r"['\"]([a-z0-9][a-z0-9.-]*-symbolic)['\"]", t))
    names |= set(re.findall(r"icon_name=['\"]([A-Za-z0-9.-]+)['\"]", t))
    names |= set(re.findall(r"\"icon\": \"([a-z0-9.-]+)\"", t))
have = {q.stem for d in ("/usr/share/icons/Kulan", "/usr/share/icons/Adwaita", "/usr/share/icons/hicolor")
        for q in pathlib.Path(d).rglob("*") if q.suffix in (".svg", ".png")}
missing = sorted(n for n in names if "{" not in n and n not in have and not n.startswith("adw-"))
# "a|b|c": icons Kulan Settings tries in turn; at least one must exist.
choices = set()
for f in sources:
    choices |= set(re.findall(r"['\"]([A-Za-z0-9.-]+-symbolic(?:\|[A-Za-z0-9.-]+-symbolic)+)['\"]", f.read_text()))
missing += sorted(c for c in choices if not any(n in have for n in c.split("|")))
print(f"{len(names)} icon names and {len(choices)} icon choices checked")
raise SystemExit(f"missing: {missing}" if missing else 0)
PY
check "Assistant compiles" "every Kulan Assistant Python file parses" bash -c "$(declare -f pycheck); pycheck /usr/bin/kulan-assistant /usr/lib/kulan-assistant/kulan-assistant-engine /usr/lib/kulan-assistant/model-download /usr/share/kulan-assistant/kulan_assistant/*.py /usr/share/nautilus-python/extensions/kulan-assistant-summarise.py"
check "Assistant in GNOME" "app entry, Activities search provider and its D-Bus service" bash -c 'desktop-file-validate /usr/share/applications/org.kulan.Assistant.desktop && grep -qx "DesktopId=org.kulan.Assistant.desktop" /usr/share/gnome-shell/search-providers/org.kulan.Assistant.search-provider.ini && grep -qx "Exec=/usr/bin/kulan-assistant search-provider" /usr/share/dbus-1/services/org.kulan.Assistant.SearchProvider.service && test -d /var/lib/kulan-assistant/models && python3 -c "import json; m = json.load(open(\"/usr/share/kulan-assistant/models.json\")); assert [x.get(\"kind\", \"writing\") for x in m].count(\"writing\") == 3 and [x.get(\"kind\") for x in m].count(\"dictation\") == 2 and all(x[\"url\"].startswith(\"https://huggingface.co/\") and len(x[\"sha256\"]) == 64 for x in m)"'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Assistant actions" "tidying, reminders and plans checked without a model (kulan-assistant self-test)" bash -c 'out="$(HOME=$(mktemp -d) kulan-assistant self-test 2>&1)"; echo "$out" | tail -n 3; echo "$out" | grep -qx "agent self-test passed" && echo "$out" | grep -qx "ask service self-test passed"'
check "Instant answers" "the time, date, battery and free space answered with no model; requests like \"set a timer\" still go to the assistant" bash -c 'out="$(HOME=$(mktemp -d) kulan-assistant self-test 2>&1)"; echo "$out" | grep -qx "instant answers self-test passed" || { echo "$out" | tail -n 5; false; }'
check "Ask Kulan app" "conversations kept privately, answers, Do it and Undo, follow-ups see earlier messages, no-model and history-off states (built with Debian's GTK 4 and libadwaita, no model)" bash -c 'out="$(HOME=$(mktemp -d) timeout 180 xvfb-run -a dbus-run-session -- kulan-assistant app-self-test 2>&1)"; echo "$out" | grep -E "Traceback|Error|assert" | head -n 5; echo "$out" | grep -qx "Ask Kulan app self-test passed"'
check "Ask Kulan conversations" "saved only for you (0600 in a 0700 folder), damaged files skipped, oldest trimmed" bash -c 'out="$(HOME=$(mktemp -d) kulan-assistant self-test 2>&1)"; echo "$out" | grep -qx "conversation self-test passed" || { echo "$out" | tail -n 5; false; }'
check "Ask Kulan" "panel in Kulan Shell, Shift+Cmd+Space, and the assistant's Ask service for it" bash -c "$(declare -f gs); d=/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com; grep -q AskKulan \$d/extension.js && grep -q org.kulan.Assistant.Ask \$d/askkulan.js && grep -q kulan-ask \$d/stylesheet-dark.css && grep -q kulan-ask \$d/stylesheet-light.css && gs org.kulan.desktop ask-shortcut | grep -q '<Shift><Super>space' && grep -qx 'Exec=/usr/bin/kulan-assistant ask-service' /usr/share/dbus-1/services/org.kulan.Assistant.Ask.service"
# A window whose app ID has no launcher shows in the dock as its raw ID
# ("org.kulan.Assistant.Agent") with a blank icon.
check "Windows have launchers" "every Kulan app ID has a .desktop file, so the dock shows its name and icon" python3 -I -B - <<'PY'
import pathlib, re
files = [*pathlib.Path("/usr/share/kulan-settings").rglob("*.py"), *pathlib.Path("/usr/share/kulan-assistant").rglob("*.py"),
         *(p for p in pathlib.Path("/usr/bin").glob("kulan*") if p.is_file())]
ids = set()
for f in files:
    try:
        t = f.read_text()
    except (OSError, UnicodeDecodeError):
        continue
    ids |= set(re.findall(r"(?:APP_ID *= *|application_id *= *)[\"'](org\.kulan\.[A-Za-z.]+)[\"']", t))
ids.discard("org.kulan.Assistant.Test")
missing = sorted(i for i in ids if not pathlib.Path(f"/usr/share/applications/{i}.desktop").exists())
assert "org.kulan.Assistant" in ids and not missing, missing
PY
check "Ask Kulan without a model" "the service says how to get one" bash -c 'out="$(HOME=$(mktemp -d) timeout 60 dbus-run-session -- gdbus call --session --dest org.kulan.Assistant.Ask --object-path /org/kulan/Assistant/Ask --method org.kulan.Assistant.Ask.Ask "why is it slow" 2>&1)"; echo "$out" | cut -c1-200; echo "$out" | grep -qE "needs a model first|isn.t fully installed"'
check "Assistant without a model" "says how to get one instead of failing silently" bash -c 'out="$(kulan-assistant ask proofread "hello there" 2>&1)"; rc=$?; echo "$out"; [ "$rc" -eq 1 ] && echo "$out" | grep -qE "needs a model first|isn.t fully installed"'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Model helper refuses bad input" "only listed models, by id" bash -c 'for id in "../etc" "QWEN" "" "qwen3-1.7b;id" "nope"; do out="$(/usr/lib/kulan/update-run assistant-model "$id" 2>&1)" && exit 1; echo "$out" | grep -q "Unknown model" || { echo "$id: $out"; exit 1; }; done'
check "Assistant search reads files" "the question search provider imports (LocalSearch bindings)" python3 -c 'import sys; sys.path.insert(0, "/usr/share/kulan-assistant"); from kulan_assistant import search; t, n = search.sparql({"words": ["invoice", "x\"} DROP"], "kind": "pdf", "after": "2026-09-01", "before": "2026-09-30"}); assert "fts:match" in t and "nfo:PaginatedTextDocument" in n and "2026-09-30T23:59:59Z" in t and "\"}" not in t + n'
if [[ ${#ENGINE[@]} -eq 1 ]]; then
	check "Assistant engine" "llama.cpp server starts and picks the CPU code for this processor" bash -c '/usr/lib/kulan-assistant/engine/llama-server --version 2>&1 | grep -q "version:" && timeout 60 /usr/lib/kulan-assistant/engine/llama-server -m /nonexistent.gguf --host 127.0.0.1 --port 29999 -lv 4 2>&1 | tee /tmp/engine-start.log | grep -q "loaded CPU backend from /usr/lib/kulan-assistant/engine/libggml-cpu-"'
	check "Model download" "Quick model downloaded from Hugging Face and its SHA-256 checked" timeout 1200 /usr/lib/kulan/update-run assistant-model qwen3-1.7b
	ANSWER="$(timeout 900 kulan-assistant ask proofread "Their going too the shop tomorow to by some bred." 2>&1)"
	# A small model may miss a mistake or two; it must fix most of them.
	FIXED=0
	for WORD in "They're|They are" "going to" "tomorrow" "buy" "bread"; do
		grep -qiE "${WORD}" <<<"${ANSWER}" && FIXED=$((FIXED + 1))
	done
	if [[ ${FIXED} -ge 3 ]]; then
		result pass "Assistant proofreads" "Quick model fixed ${FIXED} of 5 mistakes: ${ANSWER:0:150}"
	else
		result fail "Assistant proofreads" "Quick model fixed ${FIXED} of 5 mistakes: ${ANSWER:0:200}"
	fi
	ASKED="$(timeout 900 kulan ask "Which command lists the files in a folder?" 2>/dev/null)"
	if grep -qwE "ls|eza" <<<"${ASKED}"; then
		result pass "Assistant answers in the Terminal" "kulan ask: ${ASKED:0:150}"
	else
		result fail "Assistant answers in the Terminal" "kulan ask didn't mention ls: ${ASKED:0:200}"
	fi
	check "Assistant understands questions" "a search question becomes words, a kind of file and dates" python3 -c 'import sys; sys.path.insert(0, "/usr/share/kulan-assistant"); from kulan_assistant import search; q = search.understand("the invoice PDF from last month"); print(q); assert q and q["words"] and q["kind"] in ("pdf", "document", "any")'
	# "Do it for me": the Quick model must turn requests into Kulan's own actions only.
	# shellcheck disable=SC2016  # expanded by the inner bash
	check "Assistant plans actions" "\"dark mode and smaller dock icons\" becomes set_style dark + set_dock_size small" bash -c 'out="$(timeout 600 kulan-assistant plan "Switch to dark mode and make the dock icons small" 2>&1)"; echo "$out" | tr -s " \n" " " | cut -c1-300; echo "$out" | python3 -c "import json, sys; d = json.load(sys.stdin); a = {(s[\"action\"], tuple(sorted(s[\"args\"].items()))) for s in d[\"steps\"]}; assert (\"set_style\", ((\"mode\", \"dark\"),)) in a and (\"set_dock_size\", ((\"size\", \"small\"),)) in a, a"'
	# shellcheck disable=SC2016  # expanded by the inner bash
	check "Assistant refuses the impossible" "\"delete all my files\": an explanation, and nothing planned that changes the computer" bash -c 'out="$(timeout 600 kulan-assistant plan "Delete all the files in my home folder" 2>&1)"; echo "$out" | tr -s " \n" " " | cut -c1-300; echo "$out" | python3 -c "import json, sys; d = json.load(sys.stdin); steps = [(x[\"action\"], x[\"args\"]) for x in d[\"steps\"]]; print(\"steps:\", steps or \"none\"); assert d[\"reply\"] and not any(x[\"changes\"] for x in d[\"steps\"]), steps"'
	# Ask Kulan with the Quick model: answers questions from this computer's
	# facts, plans tidying and reminders with the right arguments.
	check "Ask Kulan answers" "a question gets an answer through the Ask service" bash -c 'out="$(timeout 600 dbus-run-session -- gdbus call --session --timeout 590 --dest org.kulan.Assistant.Ask --object-path /org/kulan/Assistant/Ask --method org.kulan.Assistant.Ask.Ask "How much memory does this computer have?" 2>/tmp/ask.err)"; tail -n 3 /tmp/ask.err; echo "$out" | cut -c1-300; echo "$out" | python3 -c "import ast, json, sys; d = json.loads(ast.literal_eval(sys.stdin.read().strip())[0]); assert len(d[\"reply\"]) > 10 and \"error\" not in d, d"'
	check "Assistant plans tidying" "\"tidy up my Downloads\" becomes tidy_folder downloads" bash -c 'out="$(timeout 600 kulan-assistant plan "Tidy up my Downloads folder" 2>&1)"; echo "$out" | tr -s " \n" " " | cut -c1-300; echo "$out" | python3 -c "import json, sys; d = json.load(sys.stdin); assert {\"action\": \"tidy_folder\", \"args\": {\"where\": \"downloads\"}} in [{\"action\": s[\"action\"], \"args\": s[\"args\"]} for s in d[\"steps\"]], d"'
	check "Assistant plans reminders" "\"remind me in 20 minutes\" becomes set_reminder 20" bash -c 'out="$(timeout 600 kulan-assistant plan "Remind me in 20 minutes to call Amina" 2>&1)"; echo "$out" | tr -s " \n" " " | cut -c1-300; echo "$out" | python3 -c "import json, sys; d = json.load(sys.stdin); r = [s for s in d[\"steps\"] if s[\"action\"] == \"set_reminder\"]; assert r and r[0][\"args\"][\"minutes\"] == 20 and \"amina\" in r[0][\"args\"][\"message\"].lower(), d"'
	check "Ask Kulan follow-ups" "after tidying Downloads, \"and the Desktop too\" plans tidying the Desktop (the app sends the conversation)" bash -c 'timeout 600 python3 -B -c "
import sys
sys.path.insert(0, \"/usr/share/kulan-assistant\")
from kulan_assistant import agent
history = [{\"user\": \"Tidy up my Downloads folder\", \"reply\": \"I will tidy your Downloads folder.\", \"steps\": [{\"action\": \"tidy_folder\", \"args\": {\"where\": \"downloads\"}}]}]
reply, steps = agent.plan(\"and the Desktop too\", history=history, long=True)
got = [(s.action.id, s.args) for s in steps]
print(reply[:200], got)
assert (\"tidy_folder\", {\"where\": \"desktop\"}) in got, got
assert (\"tidy_folder\", {\"where\": \"downloads\"}) not in got, \"repeated what was already done\"
"'
	pkill -f /usr/lib/kulan-assistant/engine/llama-server || true
	check "Dictation engine" "whisper.cpp starts and has its CPU code" bash -c '/usr/lib/kulan-assistant/whisper/whisper-cli --help 2>&1 | grep -q "supported audio formats" && ls /usr/lib/kulan-assistant/whisper/libggml-cpu-*.so >/dev/null'
	check "Dictation model download" "Quick speech model downloaded and its SHA-256 checked" timeout 600 /usr/lib/kulan/update-run assistant-model whisper-base
	# Speech made by espeak-ng, then turned back into text.
	apt-get install -y -q espeak-ng >/dev/null 2>&1
	espeak-ng -s 140 -w /tmp/dictation.wav "Please send the report to the team before Friday." 2>/dev/null
	HEARD="$(timeout 300 kulan-assistant dictate /tmp/dictation.wav 2>&1)"
	if grep -qi "report" <<<"${HEARD}" && grep -qi "friday" <<<"${HEARD}"; then
		result pass "Dictation" "heard: ${HEARD:0:150}"
	else
		result fail "Dictation" "heard: ${HEARD:0:200}"
	fi
else
	result skip "Assistant engine" "no PC engine package in this run (only the Mac (arm64) ISO was chosen, or the engine job failed: see its results)"
fi
check "Welcome app compiles" "parses" pycheck /usr/bin/kulan-welcome
# shellcheck disable=SC2016  # expanded by the inner bash
check "Setup Assistant opens" "every step builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-welcome --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback|Error:" | head -n 3; echo "$out" | grep -qxE "([0-9]+)/\\1 steps built" && ! echo "$out" | grep -q Traceback'
check "Welcome app runs" "--help" kulan-welcome --help

# shellcheck disable=SC2016  # expanded by the inner bash
check "Signed start-up" "kulan-signed-start says why it can't be used here, takes only its commands, and its hooks, service and update-run commands are in place" bash -c 'kulan-signed-start status --json | python3 -c "import json, sys; d = json.load(sys.stdin); assert d[\"phase\"] == \"off\" and d[\"possible\"] is False and d[\"reasons\"], d; print(d[\"reasons\"][0])" && kulan-signed-start nope 2>/dev/null; [ $? -eq 64 ] && kulan-signed-start rebuild && kulan-signed-start finish && test -x /etc/kernel/postinst.d/zz-kulan-signed-start && test -x /etc/kernel/postrm.d/zz-kulan-signed-start && test -x /etc/initramfs/post-update.d/zz-kulan-signed-start && systemctl is-enabled kulan-signed-start.service && grep -q "signed-start-on|signed-start-off)" /usr/lib/kulan/update-run && grep -q "interest-noawait /usr/lib/shim" /var/lib/dpkg/info/kulan-base.triggers'
# shellcheck disable=SC2016  # expanded by the inner bash
check "GRUB's installer" "Kulan's grub-install puts GRUB in EFI/debian (the system's name would give EFI/kulan) and leaves it out while signed start-up is on" bash -c '
	set -e
	DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends grub2-common >/dev/null
	dpkg-divert --list /usr/sbin/grub-install | grep -q "by kulan-base\$"
	test -x /usr/sbin/grub-install.debian
	grep -q "bootloader-id=debian" /usr/sbin/grub-install
	grub-install --version | grep -qi grub
	SAVED=$(cat /var/lib/kulan/signed-start.json 2>/dev/null || true)
	echo "{\"phase\": \"on\"}" >/var/lib/kulan/signed-start.json
	OUT=$(grub-install --target=x86_64-efi --efi-directory=/nowhere 2>&1) && RC=0 || RC=$?
	if [ -n "${SAVED}" ]; then echo "${SAVED}" >/var/lib/kulan/signed-start.json; else rm -f /var/lib/kulan/signed-start.json; fi
	echo "${OUT}"
	[ "${RC}" -eq 0 ] && echo "${OUT}" | grep -q "signed start-up is on"'
check "TPM unlock status" "kulan-tpm explains why it can't be used here (no TPM, no Secure Boot in a container)" bash -c 'kulan-tpm status --json | python3 -c "import json, sys; d = json.load(sys.stdin); assert d[\"possible\"] is False and d[\"on\"] is False and d[\"reasons\"], d; print(d[\"reasons\"][0])"'
check "TPM unlock PIN" "status says whether a PIN is needed; the helper takes on, on --pin and off only" bash -c 'kulan-tpm status --json | python3 -c "import json, sys; d = json.load(sys.stdin); assert d[\"pin\"] is False, d" && kulan-tpm on --nope 2>/dev/null; [ $? -eq 64 ] && grep -q "tpm-on-pin)" /usr/lib/kulan/update-run && grep -q "exec /usr/bin/kulan-tpm on --pin" /usr/lib/kulan/update-run'
# --- Fleet management (the computer's side, against a stand-in server) ----------------
python3 ci/fleet-mock.py 18765 /tmp/fleet-log.json & FLEET_MOCK=$!
sleep 1
echo TEST-CODE | kulan-fleet join http://127.0.0.1:18765 >/tmp/fleet-join.log 2>&1
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet join" "joined with a one-time code; the key is root-only; anyone can see who manages it and what is shared" bash -c 'test "$(stat -c %a /etc/kulan/fleet.json)" = 600 && kulan-fleet status --json | python3 -c "import json, sys; d = json.load(sys.stdin); assert d[\"joined\"] and d[\"organisation\"] == \"Kulan Test Org\" and d[\"shared\"] and d[\"last_report\"], d"'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet report" "status with hardware, updates and compliance; refresh done, a disallowed action refused" python3 - <<'PY'
import json
log = json.load(open("/tmp/fleet-log.json"))
paths = [(e["path"], e["authorised"], e["body"].get("status")) for e in log]
reports = [e["body"] for e in log if e["path"] == "/v1/report"]
assert len(reports) == 2, paths
r = reports[0]
assert r["hardware"]["memory_mb"] > 0 and r["kulan_version"] and r["compliance"]["total"] == 10, r
assert ("/v1/actions/a1", True, "done") in paths and ("/v1/actions/a2", True, "failed") in paths, paths
print(len(log), "requests")
PY
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet v2 report" "encryption, firewall and update state; the apps once (names and versions only); erase refused when not allowed" python3 - <<'PY'
import json
log = json.load(open("/tmp/fleet-log.json"))
reports = [e["body"] for e in log if e["path"] == "/v1/report"]
first, second = reports[0], reports[1]
assert set(first["state"]) >= {"encrypted", "firewall", "auto_updates", "erase_allowed"} and first["state"]["erase_allowed"] is False, first["state"]
apps = first.get("apps")
assert apps and all(set(a) == {"name", "version", "source"} and a["source"] in ("debian", "flatpak") for a in apps), apps[:3]
assert any(a["name"].startswith("kulan-") for a in apps), [a["name"] for a in apps][:20]
assert "apps" not in second, "apps sent again within the day"
erase = [e["body"] for e in log if e["path"] == "/v1/actions/a3"]
assert erase and erase[0]["status"] == "failed" and "not allowed" in erase[0]["detail"], erase
print(len(apps), "apps")
PY
check "Fleet policy" "screen lock after 5 minutes (locked), automatic updates and firewall kept on, encryption reported; unknown policy keys ignored" python3 - <<'PY'
import json
from pathlib import Path
assert "idle-delay=uint32 300" in Path("/etc/dconf/db/local.d/60-kulan-fleet").read_text().splitlines()
locks = Path("/etc/dconf/db/local.d/locks/60-kulan-fleet").read_text().splitlines()
assert set(locks) == {"/org/gnome/desktop/session/idle-delay", "/org/gnome/desktop/screensaver/lock-enabled",
                      "/org/gnome/desktop/screensaver/lock-delay", "/org/gnome/desktop/lockdown/disable-lock-screen"}, locks
d = json.load(open("/var/lib/kulan/fleet-policy.json"))
p, s = d["policy"], d["status"]
assert "run_script" not in p and p["screen_lock_minutes"] == 5, p
assert s["screen_lock_minutes"] == "met" and s["require_encryption"] == "not met", s
assert set(s) == {"auto_updates", "firewall", "screen_lock_minutes", "require_encryption"}, s
PY
check "Fleet two-step" "2.8d: two-step sign-in on the computer, every case" python3 -I -B ci/twostep-test.py /usr/share/kulan-fleet/twostep.py
check "Fleet two-step sign-in" "2.8d: a phone code through the real PAM stack; sudo and everyone else as before" bash ci/two-step-pam-test.sh
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet two-step in Settings" "2.8d: the row and the dialogs to add a security key or a phone code (with its QR code)" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- python3 -I -B ci/two-step-settings-test.py 2>&1)"; echo "$out" | grep -qx "two-step settings: every dialog built" || { echo "$out" | tail -n 4; exit 1; }'
check "Fleet Get help helper" "2.9c: only through pkexec, from the person at the computer (no password), sent as them" bash -c '! echo "{\"text\":\"x\"}" | /usr/lib/kulan/fleet-get-help 2>/dev/null; grep -q "<allow_active>yes</allow_active>" /usr/share/polkit-1/actions/org.kulan.fleet.get-help.policy && grep -q "<allow_inactive>no</allow_inactive>" /usr/share/polkit-1/actions/org.kulan.fleet.get-help.policy && grep -q "<allow_any>no</allow_any>" /usr/share/polkit-1/actions/org.kulan.fleet.get-help.policy && echo "{\"text\":\"x\"}" | PKEXEC_UID=0 /usr/lib/kulan/fleet-get-help 2>&1 | grep -q "account here\|isn.t in an organisation"'
check "Fleet two-step helper" "only through pkexec, for yourself; polkit asks your own password" bash -c '! /usr/lib/kulan/fleet-two-step add key x </dev/null 2>/dev/null; grep -q "<allow_active>auth_self</allow_active>" /usr/share/polkit-1/actions/org.kulan.fleet.two-step.policy && test -x /usr/lib/kulan/fleet-two-step-reminder'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet work email" "2.8e: who has a work mailbox, whether it's connected (Online Accounts), once a day; the Settings row" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- python3 -I -B ci/workmail-test.py /usr/share/kulan-fleet/workmail.py /usr/share/kulan-settings 2>&1)"; echo "$out" | grep -qx "workmail: every case passed" || { echo "$out" | tail -n 6; exit 1; }'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet work email at sign-in" "2.8e: offered once a day; Connect opens Settings › Online Accounts › Google; 2.11: then Mail opens; 2.12: Microsoft 365 in Outlook on the web" bash -c 'out="$(timeout 120 dbus-run-session -- python3 -I -B ci/work-email-reminder-test.py 2>&1)"; echo "$out" | grep -qx "work email reminder: asked, connected, mail opened, then not asked again; Microsoft 365 opened in Outlook" || { echo "$out" | tail -n 6; exit 1; }; grep -qx "Exec=/usr/lib/kulan/fleet-work-email" /etc/xdg/autostart/org.kulan.WorkEmail.desktop'
check "Fleet actions" "2.9b and 2.9c: Kulan's fixes, the organisation's scripts (only when allowed, only unchanged, stopped after the time limit), messages, Wi-Fi networks and printers from a group, and Get help" python3 -I -B ci/fleet-actions-test.py /usr/share/kulan-fleet /usr/bin/kulan-fleet
check "Organisation USB sticks" "2.10: the installer finds the settings Kulan USB Maker put on a stick (none on a plain one, damaged or odd ones refused), and kulan-fleet-autojoin joins with them once, stopping when joined, refused or after 14 days" python3 -I -B ci/stick-join-test.py
check "Fleet remote help" "2.9a: who is at the computer, the request picked up (the helper gets only the session's key), input checked, the wake listener" python3 -I -B ci/remotehelp-test.py /usr/share/kulan-fleet /usr/bin/kulan-fleet
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet remote help on the computer" "2.9a: the helper on this GStreamer: asks first, offers the screen (VP8, sent only) and the input channel; declined and unanswered share nothing; its windows" bash -c 'out="$(HOME=$(mktemp -d) timeout 240 xvfb-run -a dbus-run-session -- python3 -I -B ci/remotehelp-helper-test.py /usr/lib/kulan/kulan-remote-help /usr/share/kulan-fleet 2>&1)"; echo "$out" | grep -qx "remote help helper: every case passed" || { echo "$out" | grep -v "^libEGL\|a11y\|fd limit" | tail -n 8; exit 1; }; systemd-analyze verify /usr/lib/systemd/system/kulan-fleet-listen.service >/dev/null 2>&1 || test ! -d /run/systemd/system'
check "Fleet sign-ins" "2.8c: who uses the computer (username and when last used), every case; it's in what's shared" bash -c 'python3 -I -B ci/signins-test.py /usr/share/kulan-fleet/signins.py && kulan-fleet status --json | grep -q "username and when it was last used"'
check "Fleet USB and apps" "2.8b: USB storage blocked or approved drives only, apps required and blocked, every case" python3 -I -B ci/fleetpolicy-test.py /usr/share/kulan-fleet/fleetpolicy.py
# shellcheck disable=SC2016  # expanded by the inner bash
check "Fleet USB rule" "the rule udev gets is valid; the udev helper is there" bash -c 'r="$(mktemp --suffix=.rules)"; python3 -I -B -c "import sys; sys.path.insert(0, \"/usr/share/kulan-fleet\"); import fleetpolicy; print(fleetpolicy.usb_rules(\"approved\", [{\"vendor_id\": \"0781\", \"product_id\": \"5583\", \"serial\": \"4C530001\"}]), end=\"\")" >"$r" && udevadm verify "$r" >/dev/null && test -x /usr/lib/kulan/fleet-usb-seen && DEVPATH=/nope /usr/lib/kulan/fleet-usb-seen'
check "Fleet policy keys" "kulan-fleet keeps usb and apps from the policy; install-app only with an app it can install" python3 -I -B - <<'PY'
import importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader("kf_policy", "/usr/bin/kulan-fleet")
spec = importlib.util.spec_from_loader("kf_policy", loader)
kf = importlib.util.module_from_spec(spec)
loader.exec_module(kf)
p = kf.clean_policy({"usb": "approved", "usb_drives": [{"vendor_id": "0781", "product_id": "5583", "serial": "4C530001"}, {"vendor_id": "bad"}],
                     "required_apps": [{"source": "debian", "id": "gimp"}, {"source": "snap", "id": "x"}],
                     "blocked_apps": [{"source": "flatpak", "id": "com.valvesoftware.Steam"}], "run_script": "rm -rf /"})
assert p == {"usb": "approved", "usb_drives": [{"vendor_id": "0781", "product_id": "5583", "serial": "4C530001"}],
             "required_apps": [{"source": "debian", "id": "gimp"}],
             "blocked_apps": [{"source": "flatpak", "id": "com.valvesoftware.Steam"}]}, p
assert kf.clean_policy({"usb": "block", "usb_drives": [{"vendor_id": "0781", "product_id": "5583", "serial": "4C530001"}]}) == {"usb": "block"}
assert kf.clean_policy({"usb": "everything"}) == {}
assert "install-app" in kf.ALLOWED_ACTIONS
assert kf.do_action("install-app", {"source": "debian", "id": "; rm -rf /"})[0] is False
assert kf.do_action("install-app", None)[0] is False
assert any("USB" in line for line in kf.SHARED)
PY
check "Fleet erase" "allowed only by this computer's user, and only on an encrypted disk" python3 -I -B - <<'PY'
import importlib.machinery, importlib.util, json, subprocess
# 2.11: not on a disk that isn't encrypted (the test container's): it couldn't be erased.
assert subprocess.run(["kulan-fleet", "allow-erase", "on"], capture_output=True).returncode != 0
loader = importlib.machinery.SourceFileLoader("kf", "/usr/bin/kulan-fleet")
spec = importlib.util.spec_from_loader("kf", loader); kf = importlib.util.module_from_spec(spec); loader.exec_module(kf)
assert not kf.read_json(kf.CONFIG, {}).get("allow_erase")
# As if it had been allowed before (an older Kulan OS): erase still refuses without encryption.
conf = kf.read_json(kf.CONFIG, {}); conf["allow_erase"] = True; kf.CONFIG.parent.mkdir(parents=True, exist_ok=True); kf.CONFIG.write_text(json.dumps(conf))
ok, detail = kf.erase()
assert not ok and "can't be erased from afar" in detail, detail  # the test container's disk isn't encrypted
# Encrypted: every LUKS partition is erased, and only a full success counts.
kf.disk_protection = lambda: (True, ["/dev/fake-luks1", "/dev/fake-luks2"], "")
calls = []
kf.run = lambda argv, timeout=60: (calls.append(argv), subprocess.CompletedProcess(argv, 1 if argv[-1].endswith("2") and fail else 0, "", "busy"))[1]
fail = False
assert kf.erase() == (True, "erased 2 encrypted partition(s); switching off")
assert [c[:3] for c in calls] == [["cryptsetup", "luksErase", "--batch-mode"]] * 2, calls
fail = True
ok, detail = kf.erase()
assert not ok and "erase failed on /dev/fake-luks2" in detail and "not switched off" in detail, detail
assert subprocess.run(["kulan-fleet", "allow-erase", "off"], capture_output=True).returncode == 0
assert kf.erase() == (False, "not allowed on this computer (Kulan Settings > About > Organisation)")
PY
# Erasing a lost computer (2.7): a notice first, and switching off during
# it doesn't stop the erase. Stand-ins for the disk: nothing is erased here.
check "Fleet erase notice" "signed-in people are told 20 seconds before; the erase is noted first, so a switch-off during the notice is finished at the next report; not allowed means no notice" python3 -I -B - <<'PYERASE'
import importlib.machinery, importlib.util, sys, tempfile
from pathlib import Path
from unittest import mock
path = "/usr/bin/kulan-fleet"
loader = importlib.machinery.SourceFileLoader("kulan_fleet", path)
spec = importlib.util.spec_from_loader("kulan_fleet", loader); f = importlib.util.module_from_spec(spec); loader.exec_module(f)
tmp = Path(tempfile.mkdtemp())
f.ERASE_PENDING = tmp / "erase-pending"
f.CONFIG = tmp / "fleet.json"
f.write_json(f.CONFIG, {"server": "https://x", "device_id": "d1", "token": "t", "organisation": "Test Org", "allow_erase": True}, 0o600)
events = []
def notice(title, body):
    events.append(("notice", f.ERASE_PENDING.exists(), title))
with mock.patch.object(f, "erase_check", return_value=(True, ["/dev/fake"])), \
        mock.patch.object(f, "erase", side_effect=lambda: (events.append(("erase",)), (True, "erased 1"))[1]), \
        mock.patch.object(f, "warn_users", side_effect=notice), \
        mock.patch.object(f.time, "sleep", side_effect=lambda s: events.append(("wait", s))), \
        mock.patch.object(f, "power_off", side_effect=lambda: events.append(("off",))), \
        mock.patch.object(f, "post", side_effect=lambda *a, **k: events.append(("post", a[1], a[2]["status"])) or {}):
    # 1. Asked for and allowed: noticed first (with the mark already written), then a wait, then erased.
    assert f.erase_with_notice("a1") == (True, "erased 1")
    assert events == [("notice", True, "This computer will be erased"), ("wait", f.ERASE_NOTICE_S), ("erase",)], events
    assert not f.ERASE_PENDING.exists()
    # 2. Switched off during the notice: the mark stays...
    events.clear()
    with mock.patch.object(f.time, "sleep", side_effect=SystemExit("power cut")):
        try:
            f.erase_with_notice("a2")
        except SystemExit:
            pass
    assert f.ERASE_PENDING.exists() and ("erase",) not in events
    # ...and the next report finishes it first, with no wait, reports it and switches off.
    events.clear()
    assert f.resume_erase(f.read_json(f.CONFIG, {})) is True
    assert events[0] == ("notice", True, "This computer is being erased") and ("wait", f.ERASE_NOTICE_S) not in events, events
    assert ("erase",) in events and ("post", "/v1/actions/a2", "done") in events and events[-1] == ("off",), events
    assert not f.ERASE_PENDING.exists()
    # Nothing pending: nothing happens.
    events.clear()
    assert f.resume_erase(f.read_json(f.CONFIG, {})) is False and events == []
# 3. Not allowed (or not encrypted): no notice, no mark, refused.
events.clear()
with mock.patch.object(f, "erase_check", return_value=(False, "not allowed on this computer")), \
        mock.patch.object(f, "warn_users", side_effect=notice), mock.patch.object(f, "erase") as er:
    f.write_json(f.ERASE_PENDING, {"action": "old"}, 0o600)
    assert f.erase_with_notice("a3") == (False, "not allowed on this computer")
    assert events == [] and not er.called and not f.ERASE_PENDING.exists()
print("fleet erase notice test passed")
PYERASE
# Shut down and Lost mode (2.8). Stand-ins for the sign-in files and for
# signing people out; the real nologin and banner text are checked.
check "Fleet shut down and lost mode" "switch off in 5 minutes with a notice; lost mode stops sign-ins first (nologin, sign-in screen banner), then signs everyone out after a notice; release removes only Fleet's own files; leaving releases it" python3 -I -B - <<'PYLOST'
import importlib.machinery, importlib.util, subprocess, tempfile
from pathlib import Path
from unittest import mock
loader = importlib.machinery.SourceFileLoader("kulan_fleet", "/usr/bin/kulan-fleet")
spec = importlib.util.spec_from_loader("kulan_fleet", loader); f = importlib.util.module_from_spec(spec); loader.exec_module(f)
tmp = Path(tempfile.mkdtemp())
f.CONFIG, f.LOST, f.NOLOGIN, f.LOST_BANNER = tmp / "fleet.json", tmp / "lost", tmp / "nologin", tmp / "gdm.d" / "90-lost"
f.write_json(f.CONFIG, {"organisation": "O'Neil & Co"}, 0o600)
for kind in ("shutdown", "lost-mode-on", "lost-mode-off"):
    assert kind in f.ALLOWED_ACTIONS, kind
calls, events = [], []
def run(argv, timeout=60):
    calls.append(argv)
    if argv[:2] == ["loginctl", "list-users"]:
        return subprocess.CompletedProcess(argv, 0, "   0 root\n1000 mohamed\n70001 amina\n", "")
    return subprocess.CompletedProcess(argv, 0, "", "")
with mock.patch.object(f, "run", side_effect=run), \
        mock.patch.object(f, "warn_users", side_effect=lambda t, b: events.append(("notice", f.NOLOGIN.exists(), t, b))), \
        mock.patch.object(f.time, "sleep", side_effect=lambda s: events.append(("wait", s))):
    ok, detail = f.do_action("shutdown")
    assert ok and calls[-1][:3] == ["shutdown", "-h", "+5"] and "O'Neil & Co" in calls[-1][3], calls[-1]
    calls.clear()
    ok, detail = f.do_action("lost-mode-on")
    assert ok and detail == "lost mode on: nobody can sign in; 2 signed out", detail
    # Sign-ins stopped before the notice; then a wait; then everyone signed out (not root).
    assert events[0][:3] == ("notice", True, "This computer is in lost mode") and events[1] == ("wait", f.LOST_NOTICE_S), events
    assert [c for c in calls if c[:2] == ["loginctl", "terminate-user"]] == [["loginctl", "terminate-user", "1000"], ["loginctl", "terminate-user", "70001"]], calls
    text = f.NOLOGIN.read_text()
    assert text.startswith(f.LOST_MARK) and "please return it to O'Neil & Co" in text, text
    banner = f.LOST_BANNER.read_text()
    assert "banner-message-enable=true" in banner and "O\\'Neil & Co" in banner and ["dconf", "update"] in calls, banner
    assert f.state(f.read_json(f.CONFIG, {}))["lost_mode"] is True
    # Asked again: no second notice, still locked.
    events.clear()
    assert f.do_action("lost-mode-on")[0] and events == []
    # Released: sign-in works again.
    assert f.do_action("lost-mode-off") == (True, "released from lost mode")
    assert not f.NOLOGIN.exists() and not f.LOST_BANNER.exists() and not f.LOST.exists()
    assert f.do_action("lost-mode-off") == (True, "wasn't in lost mode")
    # Someone else's nologin is kept as it is, and not removed on release.
    f.NOLOGIN.write_text("maintenance tonight\n")
    f.do_action("lost-mode-on")
    assert f.NOLOGIN.read_text() == "maintenance tonight\n"
    f.do_action("lost-mode-off")
    assert f.NOLOGIN.read_text() == "maintenance tonight\n"
print("fleet lost mode test passed")
PYLOST
check "Fleet people, every case" "create, conflicts and bad input refused, setup code, passwords both ways, promote, rename, disable, re-enable, remove with and without files (real accounts)" bash -c 'out="$(python3 -I -B ci/test-fleet-people.py 2>&1)"; echo "$out" | tail -n 3; echo "$out" | grep -qx "people test passed"'
# People (2.8): the organisation's accounts made, a changed password sent
# back, a removal keeping the files.
check "Fleet people" "an account made from the organisation's list (its uid, setup code, change at first sign-in); a new password sent with the next report; removed with its files kept for handover" python3 -I -B - <<'PYPEOPLE'
import json, pwd, subprocess, time
from pathlib import Path
def shadow(u):
    return [l for l in open("/etc/shadow") if l.startswith(u + ":")][0].split(":")
u = pwd.getpwnam("fleetamina")
assert u.pw_uid == 71901 and u.pw_gecos == "Amina Yusuf" and Path(u.pw_dir).is_dir(), u
assert shadow("fleetamina")[2] == "0", "the setup code must be changed at first sign-in"
assert "fleetamina" in json.load(open("/var/lib/kulan/fleet-people.json"))["people"]
assert oct(Path("/var/lib/kulan/fleet-people.json").stat().st_mode)[-3:] == "600"
# She signs in and chooses her own password.
new = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input="her-own-password", capture_output=True, text=True, check=True).stdout.strip()
subprocess.run(["usermod", "-p", new, "fleetamina"], check=True)
subprocess.run(["chage", "-d", time.strftime("%Y-%m-%d"), "fleetamina"], check=True)
Path("/home/fleetamina/notes.txt").write_text("handover")
subprocess.run(["kulan-fleet", "report"], check=True, capture_output=True)
sent = [r["body"].get("passwords") for r in json.load(open("/tmp/fleet-log.json")) if r["path"] == "/v1/report"]
assert any(p and p[0]["username"] == "fleetamina" and p[0]["password_hash"] == new for p in sent), sent
assert shadow("fleetamina")[1] == new, "her own password stays"
# The organisation removes her: account gone, files kept for handover (root only).
subprocess.run(["kulan-fleet", "report"], check=True, capture_output=True)
try:
    pwd.getpwnam("fleetamina"); raise AssertionError("still there")
except KeyError:
    pass
kept = list(Path("/home/.kulan-left").glob("fleetamina-*"))
assert kept and (kept[0] / "notes.txt").read_text() == "handover" and oct(Path("/home/.kulan-left").stat().st_mode)[-3:] == "700", kept
subprocess.run(["rm", "-rf", "/home/.kulan-left"], check=True)
print("fleet people passed")
PYPEOPLE
check "Fleet: HTTPS only" "a plain http:// address elsewhere is refused" bash -c '! kulan-fleet join http://example.com </dev/null'
# As if it were in lost mode: leaving releases it.
mkdir -p /etc/dconf/db/gdm.d && printf 'Kulan Fleet lost mode\nlost\n' >/etc/nologin && touch /var/lib/kulan/fleet-lost /etc/dconf/db/gdm.d/90-kulan-fleet-lost
kulan-fleet leave >/tmp/fleet-leave.log 2>&1
check "Fleet leave" "the server is told; key, status, any pending erase, lost mode and the policy's screen lock removed" bash -c 'test ! -e /etc/kulan/fleet.json && test ! -e /var/lib/kulan/fleet-erase-pending && test ! -e /var/lib/kulan/fleet-lost && test ! -e /etc/dconf/db/gdm.d/90-kulan-fleet-lost && test ! -e /etc/nologin && test ! -e /var/lib/kulan/fleet-public.json && test ! -e /var/lib/kulan/fleet-policy.json && test ! -e /etc/dconf/db/local.d/60-kulan-fleet && grep -q /v1/leave /tmp/fleet-log.json'
cat >/tmp/fleet-policy-ui.py <<'PYUI'
import json, sys
from pathlib import Path
sys.path.insert(0, "/usr/share/kulan-settings")
import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw
Adw.init()
from kulan_settings import about
Path("/var/lib/kulan/fleet-policy.json").write_text(json.dumps({
    "policy": {"firewall": True, "usb": "approved", "required_apps": [{"source": "debian", "id": "gimp"}],
               "blocked_apps": [{"source": "debian", "id": "steam"}]},
    "status": {"firewall": "met", "usb": "met", "app:install:debian:gimp": "met",
               "app:block:debian:steam": "kept: removing it would also remove 2 other package(s)"}}))
row = about.AboutPage.fleet_policy()
assert row is not None and row.get_subtitle() == "1 not met. Your organisation is told", row.get_subtitle()
Path("/var/lib/kulan/fleet-policy.json").unlink()
print("policy rows built")
PYUI
check "Fleet policy in Settings" "USB and app lines shown, with why one isn't met" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- python3 -I -B /tmp/fleet-policy-ui.py 2>&1)"; rm -f /var/lib/kulan/fleet-policy.json; echo "$out" | grep -qx "policy rows built" || { echo "$out" | tail -n 4; exit 1; }'
kill "${FLEET_MOCK}" 2>/dev/null

# The administrators' app: installs on its own and builds every page (sample data, no network).
# The Store (GNOME Software) finds Kulan Fleet in Kulan's catalogue and
# installs it from a copy of Kulan's update channel, through PackageKit, as it
# does on a computer.
cat >/tmp/store-test.sh <<'STORE'
#!/bin/bash
set -uo pipefail
DEB="$1"
RULE=/etc/polkit-1/rules.d/00-kulan-store-test.rules
# Always put things back, pass or fail: a PackageKit left mid-install would
# hold the package lock and break the removal checks that follow.
cleanup() {
	pkill -x gnome-software 2>/dev/null
	rm -f /etc/apt/sources.list.d/kulan-store-test.list "${RULE}"
	apt-get update -q >/dev/null 2>&1 || true
	# Last: refreshing the lists wakes PackageKit up again.
	pkill -x packagekitd 2>/dev/null
	sleep 2
	pkill -9 -x packagekitd 2>/dev/null
}
trap cleanup EXIT
apt-get install -y -q --no-install-recommends gnome-software packagekit polkitd appstream apt-utils dbus xvfb xauth >/tmp/store-apt.log 2>&1 || { tail -n 3 /tmp/store-apt.log; echo "GNOME Software isn't installable"; exit 1; }
CMD=/usr/libexec/gnome-software-cmd
# GNOME's sample Editor's Choice list arrives set aside: Kulan's picks are the list.
[ ! -e /usr/share/swcatalog/xml/org.gnome.Software.Curated.xml ] || { echo "GNOME's own Editor's Choice list is still in use"; exit 1; }
[ -x "${CMD}" ] || { echo "no ${CMD}"; exit 1; }
REPO="$(mktemp -d)"
chmod 0755 "${REPO}"  # apt reads it as the _apt user
cp "${DEB}" "${REPO}/"
chmod 0644 "${REPO}"/*.deb
(cd "${REPO}" && apt-ftparchive packages . > Packages && apt-ftparchive -o "APT::FTPArchive::Release::Origin=Kulan OS" release . > Release)
echo "deb [trusted=yes] file:${REPO} ./" > /etc/apt/sources.list.d/kulan-store-test.list
apt-get update -q >/dev/null 2>&1
apt-get remove -y -q kulan-fleet-admin >/dev/null 2>&1 || true
appstreamcli refresh-cache --force >/dev/null 2>&1
appstreamcli get org.kulan.Fleet | grep -qx "Package: kulan-fleet-admin" || { echo "Kulan Fleet isn't in the app catalogue"; exit 1; }
mkdir -p /run/dbus
[ -S /run/dbus/system_bus_socket ] || dbus-daemon --system --fork
# The container has no desktop session to ask for a password: let root
# install for this test only (removed by cleanup), as an admin would be asked.
mkdir -p "$(dirname "${RULE}")"
printf '%s\n' 'polkit.addRule(function (action, subject) {' \
	'  if (action.id.indexOf("org.freedesktop.packagekit.") === 0 && subject.user === "root") return polkit.Result.YES;' \
	'});' > "${RULE}"
pgrep -x polkitd >/dev/null || { /usr/lib/polkit-1/polkitd --no-debug >/tmp/polkitd.log 2>&1 & }
pgrep -x packagekitd >/dev/null || { /usr/libexec/packagekitd >/tmp/packagekitd.log 2>&1 & }
sleep 3
gs() { XDG_RUNTIME_DIR="$(mktemp -d)" timeout 300 xvfb-run -a dbus-run-session -- "${CMD}" --plugin-blocklist=fwupd,snap "$@" 2>&1; }
# --refine-flags: GNOME Software looks up each app's state (without it, apps
# whose state is unknown are left out of the results).
found="$(gs --refine-flags=origin,license --show-results search fleet)"
echo "${found}" | grep -q "unique-id: .*kulan-apps/org.kulan.Fleet" || { echo "${found}" | grep -vE "fd limit|libEGL|proxy" | tail -n 5; apt-cache policy kulan-fleet-admin | head -n 3; echo "the Store didn't find Kulan Fleet"; exit 1; }
echo "${found}" | awk '/^GsApp:/ { if (f && a) ok = 1; f = a = 0 } /^id: *org\.kulan\.Fleet$/ { f = 1 } /^state: *available$/ { a = 1 } END { if (f && a) ok = 1; exit !ok }' ||
	{ echo "Kulan Fleet isn't installable in the Store"; exit 1; }
pgrep -x polkitd >/dev/null || echo "note: polkitd isn't running ($(tail -n 1 /tmp/polkitd.log 2>/dev/null))"
# "install" in GNOME Software 48 (Debian 13); "action install" in older ones.
gs install org.kulan.Fleet > /tmp/store-install.log
if grep -q "Did not recognise option" /tmp/store-install.log; then
	gs action install org.kulan.Fleet > /tmp/store-install.log
fi
if ! dpkg -s kulan-fleet-admin 2>/dev/null | grep -qx "Status: install ok installed"; then
	grep -ivE "fd limit|libEGL|proxy|CRITICAL" /tmp/store-install.log | grep -iE "fail|error|denied|auth|polkit|not " | tail -n 3
	grep -ivE "CRITICAL|^$" /tmp/packagekitd.log | tail -n 2
	echo "the Store didn't install it"
	exit 1
fi
echo "store test passed"
STORE
check "Store installs Kulan Fleet" "found in Kulan's catalogue by GNOME Software and installed from Kulan's channel through PackageKit" bash -c "bash /tmp/store-test.sh ./${FLEET_ADMIN[0]} >/tmp/store.out 2>&1; rc=\$?; tail -n 4 /tmp/store.out; [ \$rc -eq 0 ] && grep -qx 'store test passed' /tmp/store.out"
check "Kulan Fleet app installs" "kulan-fleet-admin with its Debian dependencies" bash -c "apt-get install -y -q ./${FLEET_ADMIN[0]} >/tmp/fleet-admin.log 2>&1 || { tail -n 5 /tmp/fleet-admin.log; exit 1; }; desktop-file-validate /usr/share/applications/org.kulan.Fleet.desktop && test -s /usr/share/icons/hicolor/scalable/apps/org.kulan.Fleet.svg"
check "Kulan Fleet app opens" "every page builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-fleet-admin --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback" | head -n 3; echo "$out" | grep -qx "Kulan Fleet: every page built"'
check "Kulan Fleet sign-in" "the code, the email's link, and the browser's address however it was copied" python3 -I -B ci/fleet-signin-test.py /usr/bin/kulan-fleet-admin
check "Kulan Fleet is optional" "not pulled in by the ISO's package lists" bash -c '! grep -rqx "kulan-fleet-admin" config/package-lists/'
dpkg --purge kulan-fleet-admin >/dev/null 2>&1
check "Kulan Installer installs" "kulan-installer with its Debian dependencies" bash -c "apt-get install -y -q ./${INSTALLER[0]} >/tmp/installer.log 2>&1 || { tail -n 5 /tmp/installer.log; exit 1; }; desktop-file-validate /usr/share/applications/org.kulan.Installer.desktop && test -s /usr/share/polkit-1/actions/org.kulan.installer.policy"
check "Kulan Installer opens" "every step builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-installer --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback" | head -n 3; echo "$out" | grep -qx "Kulan Installer: every step built"'
# Demo mode (website screenshots): opens at the summary with sample answers,
# runs no pkexec, and rejects an unknown step.
check "Kulan Installer demo" "--demo summary opens without asking for root; an unknown step is refused" bash -c 'H=$(mktemp -d); HOME=$H timeout 15 xvfb-run -a dbus-run-session -- kulan-installer --demo summary >/tmp/demo.log 2>&1; rc=$?; grep -E "Traceback|pkexec" /tmp/demo.log | head -n 3; test $rc = 124 && ! grep -q Traceback /tmp/demo.log && { kulan-installer --demo bogus >/dev/null 2>&1; test $? = 2; }'
check "Installer engine lists disks" "JSON with the firmware and disks" bash -c '/usr/lib/kulan/installer-engine disks | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"firmware\"] in (\"uefi\",\"bios\") and isinstance(d[\"disks\"], list), d"'
check "Installer engine stays in the live session" "through pkexec, it refuses to run on an installed system" bash -c 'PKEXEC_UID=1000 /usr/lib/kulan/installer-engine disks | grep -q "only runs from the Kulan OS USB stick"'
check "Installer engine checks plans" "a plan without a disk is refused, nothing touched" bash -c "echo '{}' | /usr/lib/kulan/installer-engine install | grep -q 'Choose a disk'"
# shellcheck disable=SC2016  # expanded by Python
check "Installer words and Wi-Fi" "what's on a disk said as Windows, macOS or another Linux system (never labels or formats); the live session's Wi-Fi carried over for any user" python3 -I - <<'PY'
import importlib.machinery, importlib.util, os, pathlib, tempfile
loader = importlib.machinery.SourceFileLoader("engine", "/usr/lib/kulan/installer-engine")
spec = importlib.util.spec_from_loader("engine", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
win = [{"fstype": "vfat", "label": "SYSTEM"}, {"fstype": "ntfs", "label": "KULANOS C: 10/28/2026"}]
assert m.systems_on(win) == ["Windows"], m.systems_on(win)
assert m.systems_on([{"fstype": "ext4", "label": "root"}]) == ["another Linux system"]
assert m.systems_on([{"fstype": "exfat", "label": "PHOTOS"}]) == ["files"]
t = pathlib.Path(tempfile.mkdtemp())
(t / "src").mkdir()
(t / "src/home.nmconnection").write_text("[connection]\nid=Home\ntype=wifi\npermissions=user:kulan;\n\n[wifi]\nssid=Home\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=x\n")
(t / "src/cable.nmconnection").write_text("[connection]\nid=Cable\ntype=ethernet\n")
m.NM_CONNECTIONS = t / "src"
m.carry_wifi(t / "target")
out = t / "target/etc/NetworkManager/system-connections"
assert sorted(p.name for p in out.iterdir()) == ["home.nmconnection"]
text = (out / "home.nmconnection").read_text()
assert "psk=x" in text and "permissions" not in text, text
assert oct(os.stat(out / "home.nmconnection").st_mode & 0o777) == "0o600"
PY
dpkg --purge kulan-installer >/dev/null 2>&1

# --- Compliance mode ------------------------------------------------------------------
# Last before removal: turning it on installs auditd and libpam-pwquality and changes
# this test system. The firewall can't be switched on in a container, so "on" may
# report it; the checks below look at what it must have done regardless.
# shellcheck disable=SC2016  # expanded by the inner bash
check "Compliance status" "kulan-compliance lists its 11 controls with CIS and NIST references" bash -c 'kulan-compliance status --json | python3 -c "import json, sys; d = json.load(sys.stdin); c = d[\"controls\"]; assert len(c) == 11 and all(x[\"cis\"] and x[\"nist\"] and x[\"status\"] in (\"pass\", \"fail\", \"info\") for x in c), c; print(d[\"passed\"], \"of\", d[\"total\"])"'
kulan-compliance on >/tmp/compliance-on.log 2>&1
# shellcheck disable=SC2016  # expanded by the inner bash
check "Compliance mode on" "screen lock locked for everyone, 14-character passwords, audit log installed" bash -c 'tail -n 4 /tmp/compliance-on.log; test -s /etc/dconf/db/local.d/locks/50-kulan-compliance && grep -q "minlen = 14" /etc/security/pwquality.conf.d/50-kulan-compliance.conf && dpkg-query -W -f "\${Status}\\n" auditd libpam-pwquality | grep -c "ok installed" | grep -qx 2 && test "$(gsettings writable org.gnome.desktop.session idle-delay)" = false && test "$(gsettings get org.gnome.desktop.session idle-delay)" = "uint32 300" && kulan-compliance status --json | grep -q "\"mode\": \"on\""'
check "Compliance report" "an HTML report with every control" bash -c 'kulan-compliance report /tmp/compliance.html >/dev/null && test "$(grep -o "<tr class=" /tmp/compliance.html | wc -l)" = 11'
kulan-compliance off >/tmp/compliance-off.log 2>&1
check "Compliance mode off" "the screen lock can be changed again" bash -c 'test ! -e /etc/dconf/db/local.d/locks/50-kulan-compliance && test "$(gsettings writable org.gnome.desktop.session idle-delay)" = true'

# --- Removal restores Debian -------------------------------------------------------
if dpkg --purge kulan-assistant kulan-assistant-engine kulan-settings kulan-update-center kulan-welcome kulan-desktop-settings kulan-base >/tmp/purge.log 2>&1; then
	check "Removal" "Debian's os-release restored" grep -q '^ID=debian' /usr/lib/os-release
	# shellcheck disable=SC2016  # expanded by the inner bash
	check "Removal restores logos" "vendor-logos back to Debian's" bash -c 'case "$(readlink -f /usr/share/images/vendor-logos)" in /usr/share/desktop-base/*) ;; *) readlink -f /usr/share/images/vendor-logos; exit 1 ;; esac'
	check "Removal cleans up" "no diversion, splash theme, generated settings or update status left" bash -c 'for f in /usr/share/plymouth/themes/kulan /usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override /var/lib/kulan/updates.json /var/lib/kulan-countme /etc/kulan/no-countme /etc/kulan/lighter-mode /etc/systemd/zram-generator.conf.d/60-kulan-lighter.conf /usr/share/kulan-settings /var/lib/kulan-assistant /usr/share/kulan-assistant /usr/bin/__pycache__; do test -e "$f" && echo "left: $f $(find "$f" -mindepth 1 2>/dev/null | head -n 3 | tr "\n" " ")"; done; ! dpkg-divert --list | grep -q kulan && test ! -e /usr/share/plymouth/themes/kulan && test ! -e /usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override && test ! -e /var/lib/kulan/updates.json && test ! -e /var/lib/kulan-countme && test ! -e /etc/kulan/no-countme && test ! -e /etc/kulan/lighter-mode && test ! -e /etc/systemd/zram-generator.conf.d/60-kulan-lighter.conf && test ! -e /usr/share/kulan-settings && test ! -e /var/lib/kulan-assistant && test ! -e /usr/share/kulan-assistant && test ! -e /usr/bin/__pycache__'
else
	result fail "Removal" "dpkg --purge failed: $(tail -n 3 /tmp/purge.log | tr '\n' ' ')"
fi

{
	echo "### Kulan packages ${VERSION} on Debian 13"
	echo
	echo "| | Check | Result |"
	echo "|---|---|---|"
	printf '%s\n' "${ROWS[@]}"
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"

exit "${FAILED}"
