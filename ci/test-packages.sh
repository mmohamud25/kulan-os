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
		result fail "${name}" "${detail} - $(tail -n 3 /tmp/check.out | tr '\n' ' ' | cut -c1-200)"
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
check "Identity is upgrade-safe" "os-release diverted by kulan-base" bash -c 'dpkg-divert --list /usr/lib/os-release | grep -q "by kulan-base$"'
check "Debian's copy kept" "os-release.debian is Debian's" grep -q '^ID=debian' /usr/lib/os-release.debian
check "Build number" "os-release has BUILD_ID" grep -q '^BUILD_ID=' /usr/lib/os-release
check "Symlink" "/etc/os-release points at the Kulan file" test "$(readlink /etc/os-release)" = ../usr/lib/os-release

# --- Desktop (real Debian extensions and GNOME 48 schemas) ---------------------------
check "Extensions (strict)" "dock and blur found under their real Debian IDs" env KULAN_STRICT=1 /usr/lib/kulan/desktop-setup
check "Dock settings icon" "the dock opens Kulan Settings; GNOME Settings is in the System folder" bash -c "grep -q \"org.kulan.Settings.desktop'\]\" /usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override && grep -q \"excluded-apps=\['org.kulan.Settings.desktop'\" /etc/dconf/db/local.d/10-kulan-app-folders"
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
check "Firefox window buttons" "Kulan's round buttons loaded by Firefox's administrator configuration" bash -c 'grep -q "general.config.filename\", \"kulan.cfg\"" /usr/lib/firefox-esr/defaults/pref/kulan-autoconfig.js && grep -q "file:///usr/share/kulan/firefox/titlebar.css" /usr/lib/firefox-esr/kulan.cfg && grep -q "titlebar-close" /usr/share/kulan/firefox/titlebar.css && test "$(stat -c %U /usr/lib/firefox-esr/kulan.cfg)" = root'
if [[ -f /etc/apt/sources.list.d/kulan.sources ]]; then
	check "Update channel" "kulan.sources + key present" test -s /usr/share/keyrings/kulan-archive-keyring.gpg
	# An upgrade must move computers to this build's address (kulan.conf), even
	# when the released version pointed somewhere else.
	check "Update channel address" "kulan.sources uses ${CHANNEL}" grep -qx "URIs: ${CHANNEL}" /etc/apt/sources.list.d/kulan.sources
fi

# --- Update Center ------------------------------------------------------------------
check "Update status" "status file written by the package" python3 -c 'import json; d = json.load(open("/var/lib/kulan/updates.json")); assert d["state"] == "idle" and isinstance(d["available"], list), d'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Update hooks" "apt keeps the status current (3 hooks)" bash -c 'test "$(apt-config dump | grep -c /usr/lib/kulan/update-status)" -eq 3'
check "Update status refresh" "refresh runs and stays idle" bash -c '/usr/lib/kulan/update-status refresh && grep -q "\"state\": \"idle\"" /var/lib/kulan/updates.json'
check "Auto-update switch" "update-run auto-off then auto-on" bash -c '/usr/lib/kulan/update-run auto-off && apt-config dump APT::Periodic::Unattended-Upgrade | grep -q "\"0\"" && /usr/lib/kulan/update-run auto-on && apt-config dump APT::Periodic::Unattended-Upgrade | grep -q "\"1\""'
check "Password policy" "polkit action runs only update-run" bash -c 'command -v pkexec >/dev/null && grep -q "<annotate key=\"org.freedesktop.policykit.exec.path\">/usr/lib/kulan/update-run</annotate>" /usr/share/polkit-1/actions/org.kulan.updates.policy'
check "Restore points timer" "kulan-restore-points.timer enabled" test -L /etc/systemd/system/timers.target.wants/kulan-restore-points.timer
check "Updates in one place" "GNOME Software's Updates page off; app updates timer on; app updates listed in Software Update" bash -c "$(declare -f gs); test -L /etc/systemd/system/timers.target.wants/kulan-app-updates.timer && grep -qx 'allow-updates=false' /usr/share/glib-2.0/schemas/91_kulan-os-software.gschema.override && { ! test -f /usr/share/glib-2.0/schemas/org.gnome.software.gschema.xml || test \"\$(gs org.gnome.software allow-updates)\" = false; }"
check "App updates in the status" "Flatpak updates appear with Debian's" python3 -I - <<'PY'
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
check "Memory guard" "systemd-oomd and Kulan's notice enabled; user apps closed under memory pressure" bash -c 'systemctl is-enabled systemd-oomd.service && systemctl is-enabled kulan-oom-notice.service && grep -qx "ManagedOOMMemoryPressure=kill" /usr/lib/systemd/system/user@.service.d/50-kulan-oomd.conf'
check "Memory notice" "names the app that was closed" python3 -I - <<'PY'
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
	KULAN_RESTORE_FORCE=1 KULAN_RESTORE_SNAPSHOTS="$D/snaps" KULAN_RESTORE_USAGE="$D/u.json" KULAN_PAUSE_FILE="$D/pause" bash "$D/rp" undo-update >/dev/null
	grep -q -- "--create --scripted --comments Before undoing an update" "$D/calls"
	grep -q -- "--restore --snapshot 2026-01-03_00-00-00 --scripted --yes --skip-grub" "$D/calls"
	grep -q "systemctl reboot" "$D/calls"
	grep -q "Unattended-Upgrade \"0\"" "$D/pause" && grep -qE "Until: [0-9]+" "$D/pause"'
check "Kulan Store" "GNOME Software shown as Kulan Store with its icon; Kulan picks in the catalogue" bash -c 'f=/usr/share/kulan/xdg/applications/org.gnome.Software.desktop; if [ -f /usr/share/applications/org.gnome.Software.desktop ]; then grep -qx "Name=Kulan Store" "$f" && grep -qx "Icon=kulan-store" "$f" && [ "$(grep -c "^Name" "$f")" -eq 1 ] && desktop-file-validate "$f"; fi; test -s /usr/share/icons/hicolor/scalable/apps/kulan-store.svg && grep -q "GnomeSoftware::popular" /usr/share/swcatalog/xml/kulan-picks.xml'
check "Tags in Files" "Tags menu, dots and search are installed; tag words understood" bash -c "$(declare -f pycheck); pycheck /usr/share/nautilus-python/extensions/kulan-tags.py /usr/share/kulan/python/kulan_tags.py /usr/lib/kulan/tags-search-provider && test -s /usr/share/icons/Kulan/scalable/emblems/emblem-kulan-tag-red.svg && grep -qx 'DesktopId=org.gnome.Nautilus.desktop' /usr/share/gnome-shell/search-providers/org.kulan.Tags.search-provider.ini && python3 -c 'import sys; sys.path.insert(0, \"/usr/share/kulan/python\"); import kulan_tags as k; assert k.tag_for_words([\"tag\", \"Gray\"]) == \"grey\" and k.tag_for_words([\"red\", \"car\"]) is None and k._clean([\"Blue\", \"x\", \"red\"]) == [\"red\", \"blue\"]'"
check "Tag folders" "each tag in use gets a folder of links for Files' sidebar; real files there are kept" python3 -I - <<'PY'
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
check "Kulan Settings opens" "every page builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-settings --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback|Error:" | head -n 3; echo "$out" | grep -qxE "(2[1-9]|[3-9][0-9])/\1 pages built" && ! echo "$out" | grep -q Traceback'
check "Update Center moved" "kulan-updates opens Kulan Settings; old shortcut hidden" bash -c 'grep -q "kulan-settings updates" /usr/bin/kulan-updates && grep -qx "NoDisplay=true" /usr/share/applications/org.kulan.Updates.desktop && dpkg -S /usr/bin/kulan-updates | grep -q "^kulan-settings:"'
check "Store pages" "Kulan apps have valid AppStream pages" appstreamcli validate --no-net /usr/share/metainfo/org.kulan.Settings.metainfo.xml /usr/share/metainfo/org.kulan.Welcome.metainfo.xml
check "Kulan options" "org.kulan.desktop schema installed with Kulan defaults" bash -c "$(declare -f gs); test \"\$(gs org.kulan.desktop animation-speed)\" = \"'normal'\" && test \"\$(gs org.kulan.desktop kulan-menu)\" = true && test \"\$(gs org.kulan.desktop automatic-style)\" = false"
check "Sound theme" "Kulan chimes installed and chosen" bash -c "$(declare -f gs); test -f /usr/share/sounds/kulan/index.theme && test -s /usr/share/sounds/kulan/stereo/message-new-instant.oga && test \"\$(gs org.gnome.desktop.sound theme-name)\" = \"'kulan'\""
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
check "One search" "Kulan Settings' search finds its pages and GNOME's (Printers, Sharing); GNOME Settings' own source off; Ask Kulan result" bash -c 'python3 - <<"PY" && gsettings get org.gnome.desktop.search-providers disabled | grep -q "org.gnome.Settings.desktop"
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("sp", "/usr/lib/kulan/settings-search-provider")
spec = importlib.util.spec_from_loader("sp", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
pages = m.load_pages()
assert m.search(pages, ["printer"]) == ["gnome:printers"], m.search(pages, ["printer"])
assert "appearance" in m.search(pages, ["dark", "mode"]) and "wifi" in m.search(pages, ["wi-fi"])
sys.path.insert(0, "/usr/share/kulan-assistant")
from kulan_assistant import search
meta = search.Provider.meta(search.ASK_PREFIX + "make the text bigger")
assert meta["name"].unpack() == "Ask Kulan" and meta["description"].unpack() == "make the text bigger"
PY'
# shellcheck disable=SC2016  # expanded by the inner python
check "Printers page" "reads CUPS's printer list, default and waiting jobs; finds network and USB printers" python3 -c 'import sys; sys.path.insert(0, "/usr/share/kulan-settings"); import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from kulan_settings import printers as p; got = p.parse_printers("printer A disabled since x -\n\tUnplugged\n\tDescription: Office\nprinter B is idle.  enabled since x\n\tDescription: Home\n", "system default destination: B", "B-3 me 1 x\n"); assert [(x["description"], x["state"], x["reason"], x["default"], x["jobs"]) for x in got] == [("Office", "disabled", "Unplugged", False, 0), ("Home", "idle", "", True, 1)], got; f = p.parse_found("Device: uri = usb://Brother/HL?serial=1\n        info = Brother HL\nDevice: uri = file:/dev/null\n", set()); assert f == [{"uri": "usb://Brother/HL?serial=1", "name": "Brother HL", "where": "Plugged in (USB)"}], f'
check "kulan command" "help, info, doctor and settings work; mistakes get a usage message" bash -c 'kulan help | grep -q "kulan doctor" && kulan info | grep -q "^  Memory" && { kulan doctor; rc=$?; [ "$rc" -le 1 ]; } && { kulan settings dark-mode maybe 2>/dev/null; [ $? -eq 64 ]; } && { kulan nonsense 2>/dev/null; [ $? -eq 64 ]; } && { kulan why 2>/dev/null; [ $? -eq 1 ]; }'
check "Terminal questions" "kulan ask and kulan why use the Terminal instructions (Debian, apt, nothing run)" python3 -c 'import sys; sys.path.insert(0, "/usr/share/kulan-assistant"); from kulan_assistant import tasks; a = tasks.messages_for("answer", "How do I list files?"); w = tasks.messages_for("terminal-error", "Command: lss\nExit code: 127"); assert "Debian 13" in a[0]["content"] and "Debian 13" in w[0]["content"] and "writing helper" not in a[0]["content"], a'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Projects and templates" "first sign-in makes Projects (in the sidebar) and the templates, once" bash -c 'h=$(mktemp -d) && desktop-file-validate /etc/xdg/autostart/org.kulan.UserSetup.desktop && HOME=$h XDG_CONFIG_HOME= XDG_STATE_HOME= sh /usr/lib/kulan/user-setup && test -d $h/Projects && grep -qx "file://$h/Projects" $h/.config/gtk-3.0/bookmarks && t=$(HOME=$h xdg-user-dir TEMPLATES) && test -f "$t/Document.odt" && rm "$t/Document.odt" && HOME=$h XDG_CONFIG_HOME= XDG_STATE_HOME= sh /usr/lib/kulan/user-setup && test ! -e "$t/Document.odt" && test "$(wc -l < $h/.config/gtk-3.0/bookmarks)" -eq 1 && test "$(readlink $h/.local/share/org.gnome.Ptyxis/palettes/Kulan.palette)" = /usr/share/kulan/ptyxis/Kulan.palette'
check "Add-on list" "every add-on names its apps; the helper refuses anything else" python3 - <<'PY'
import json, re, subprocess
addons = json.load(open("/usr/share/kulan/addons.json"))
ids = [a["id"] for a in addons]
assert len(ids) == len(set(ids)), ids
assert {"apps", "chromium", "devkit", "assistant", "learning-pack", "steam"} <= set(ids), ids
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
check "Package names" "add-on and security packages exist in Debian 13" bash -c 'missing=""; for p in $(python3 -c "import json; print(\" \".join(p for a in json.load(open(\"/usr/share/kulan/addons.json\")) for p in a.get(\"packages\", []) if not p.startswith(\"kulan-\")))") keepassxc metadata-cleaner network-manager-openvpn-gnome apparmor-profiles-extra lynis gnome-sushi gnome-characters gnome-weather gnome-clocks baobab gnome-shell-extension-tiling-assistant tesseract-ocr tesseract-ocr-eng power-profiles-daemon gir1.2-dbusmenu-glib-0.4 systemd-zram-generator gnome-shell-extension-desktop-icons-ng python3-nautilus poppler-utils tracker-miner-fs gir1.2-tsparql-3.0 ptyxis zsh zsh-autosuggestions zsh-syntax-highlighting fzf ripgrep fd-find bat eza zoxide btop qt5-gtk-platformtheme qt6-gtk-platformtheme; do apt-cache show "$p" >/dev/null 2>&1 || missing="$missing $p"; done; [ -z "$missing" ] || { echo "missing:$missing"; exit 1; }'
check "Developer kit" "kulan-devkit installs cleanly (simulated)" apt-get -s -q install "./${DEVKIT[0]}"
check "Office and media apps" "kulan-apps installs cleanly (simulated)" apt-get -s -q install "./${APPS[0]}"
# shellcheck disable=SC2016  # ${Depends} and ${Status} are dpkg-query fields
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
check "Assistant without a model" "says how to get one instead of failing silently" bash -c 'out="$(kulan-assistant ask proofread "hello there" 2>&1)"; rc=$?; echo "$out"; [ "$rc" -eq 1 ] && echo "$out" | grep -qE "No model yet|engine isn.t installed"'
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
	check "Assistant refuses the impossible" "\"delete all my files\" gives no steps, only an explanation" bash -c 'out="$(timeout 600 kulan-assistant plan "Delete all the files in my home folder" 2>&1)"; echo "$out" | tr -s " \n" " " | cut -c1-300; echo "$out" | python3 -c "import json, sys; d = json.load(sys.stdin); assert d[\"steps\"] == [] and d[\"reply\"], d"'
	check "Do it for me window" "the window builds with Debian's GTK 4 and libadwaita" bash -c 'HOME=$(mktemp -d) timeout 60 xvfb-run -a dbus-run-session -- python3 -c "import sys; sys.path.insert(0, \"/usr/share/kulan-assistant\"); import gi; gi.require_version(\"Gtk\", \"4.0\"); gi.require_version(\"Adw\", \"1\"); from gi.repository import Adw; Adw.init(); from kulan_assistant import agent_window; app = Adw.Application(application_id=\"org.kulan.Assistant.Test\"); w = agent_window.AgentWindow(app, \"\"); print(\"built\")" 2>&1 | grep -qx built'
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
check "Setup Assistant opens" "every step builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-welcome --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback|Error:" | head -n 3; echo "$out" | grep -qx "10/10 steps built" && ! echo "$out" | grep -q Traceback'
check "Welcome app runs" "--help" kulan-welcome --help

# shellcheck disable=SC2016  # expanded by the inner bash
check "TPM unlock status" "kulan-tpm explains why it can't be used here (no TPM, no Secure Boot in a container)" bash -c 'kulan-tpm status --json | python3 -c "import json, sys; d = json.load(sys.stdin); assert d[\"possible\"] is False and d[\"on\"] is False and d[\"reasons\"], d; print(d[\"reasons\"][0])"'
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
check "Fleet: HTTPS only" "a plain http:// address elsewhere is refused" bash -c '! kulan-fleet join http://example.com </dev/null'
kulan-fleet leave >/tmp/fleet-leave.log 2>&1
check "Fleet leave" "the server is told; key and status removed" bash -c 'test ! -e /etc/kulan/fleet.json && test ! -e /var/lib/kulan/fleet-public.json && grep -q /v1/leave /tmp/fleet-log.json'
kill "${FLEET_MOCK}" 2>/dev/null

# The administrators' app: installs on its own and builds every page (sample data, no network).
check "Kulan Fleet app installs" "kulan-fleet-admin with its Debian dependencies" bash -c "apt-get install -y -q ./${FLEET_ADMIN[0]} >/tmp/fleet-admin.log 2>&1 || { tail -n 5 /tmp/fleet-admin.log; exit 1; }; desktop-file-validate /usr/share/applications/org.kulan.Fleet.desktop && test -s /usr/share/icons/hicolor/scalable/apps/org.kulan.Fleet.svg"
check "Kulan Fleet app opens" "every page builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-fleet-admin --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback" | head -n 3; echo "$out" | grep -qx "Kulan Fleet: every page built"'
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
	check "Removal cleans up" "no diversion, splash theme, generated settings or update status left" bash -c '! dpkg-divert --list | grep -q kulan && test ! -e /usr/share/plymouth/themes/kulan && test ! -e /usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override && test ! -e /var/lib/kulan/updates.json && test ! -e /usr/share/kulan-settings && test ! -e /var/lib/kulan-assistant && test ! -e /usr/share/kulan-assistant && test ! -e /usr/bin/__pycache__'
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
