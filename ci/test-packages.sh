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
		*/kulan-devkit_* | */kulan-apps_*) ;;
		*) DEBS+=("${DEB}") ;;
	esac
done
DEVKIT=(dist/debs/kulan-devkit_*.deb)
APPS=(dist/debs/kulan-apps_*.deb)
[[ ${#DEBS[@]} -eq 6 && ${#DEVKIT[@]} -eq 1 && ${#APPS[@]} -eq 1 ]] || {
	echo "::error::expected 6 core packages, kulan-devkit and kulan-apps in dist/debs, found ${#DEBS[@]}, ${#DEVKIT[@]} and ${#APPS[@]}"
	exit 1
}
# Kulan Assistant's engine (ci/build-engine.sh), when CI built it for PCs.
ENGINE=(dist/engine/kulan-assistant-engine_*_amd64.deb)
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
if apt-get install -y -q "${DEBS[@]/#/./}" "${ENGINE[@]/#/./}" htop gnome-tour gdm3 desktop-base xvfb xauth dbus-x11 appstream \
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
          "/usr/share/kulan/gtk/dots/gtk-4.0.css", "/usr/share/kulan/gtk/dots/gtk-3.0.css"):
    assert os.path.getsize(f) > 0, f
PY
check "Cursor" "Bibata-Modern-Classic set and installed" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.interface cursor-theme)\" = \"'Bibata-Modern-Classic'\" && test -d /usr/share/icons/Bibata-Modern-Classic"
# shellcheck disable=SC2016  # expanded by the inner bash
check "App folders" "only Utilities and System are folders for new users" bash -c 'test "$(HOME=$(mktemp -d) XDG_CONFIG_HOME="" dconf read /org/gnome/desktop/app-folders/folder-children)" = "['"'"'Kulan-Utilities'"'"', '"'"'Kulan-System'"'"']"'
check "Calmer dock" "40 px icons, quick animations" bash -c "$(declare -f gs); test \"\$(gs org.gnome.shell.extensions.dash-to-dock dash-max-icon-size)\" = 40 && test \"\$(gs org.gnome.shell.extensions.dash-to-dock animation-time)\" = 0.125"
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
check "Firefox privacy" "policies.json valid; Firefox uses the system window buttons and the system (encrypted) DNS" python3 -c 'import json; p = json.load(open("/etc/firefox/policies/policies.json"))["policies"]; assert p["Preferences"]["widget.gtk.non-native-titlebar-buttons.enabled"]["Value"] is False; assert p["DNSOverHTTPS"] == {"Enabled": False, "Locked": True}'
if [[ -f /etc/apt/sources.list.d/kulan.sources ]]; then
	check "Update channel" "kulan.sources + key present" test -s /usr/share/keyrings/kulan-archive-keyring.gpg
fi

# --- Update Center ------------------------------------------------------------------
check "Update status" "status file written by the package" python3 -c 'import json; d = json.load(open("/var/lib/kulan/updates.json")); assert d["state"] == "idle" and isinstance(d["available"], list), d'
# shellcheck disable=SC2016  # expanded by the inner bash
check "Update hooks" "apt keeps the status current (3 hooks)" bash -c 'test "$(apt-config dump | grep -c /usr/lib/kulan/update-status)" -eq 3'
check "Update status refresh" "refresh runs and stays idle" bash -c '/usr/lib/kulan/update-status refresh && grep -q "\"state\": \"idle\"" /var/lib/kulan/updates.json'
check "Auto-update switch" "update-run auto-off then auto-on" bash -c '/usr/lib/kulan/update-run auto-off && apt-config dump APT::Periodic::Unattended-Upgrade | grep -q "\"0\"" && /usr/lib/kulan/update-run auto-on && apt-config dump APT::Periodic::Unattended-Upgrade | grep -q "\"1\""'
check "Password policy" "polkit action runs only update-run" bash -c 'command -v pkexec >/dev/null && grep -q "<annotate key=\"org.freedesktop.policykit.exec.path\">/usr/lib/kulan/update-run</annotate>" /usr/share/polkit-1/actions/org.kulan.updates.policy'
check "Restore points timer" "kulan-restore-points.timer enabled" test -L /etc/systemd/system/timers.target.wants/kulan-restore-points.timer
check "Status refresh after start-up" "a timer a minute after start-up, not in the way of the login screen" bash -c 'test -L /etc/systemd/system/timers.target.wants/kulan-update-status.timer && ! test -e /etc/systemd/system/multi-user.target.wants/kulan-update-status.service'
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
check "Kulan Settings opens" "every page builds with Debian's GTK 4 and libadwaita" bash -c 'out="$(HOME=$(mktemp -d) timeout 120 xvfb-run -a dbus-run-session -- kulan-settings --self-test 2>&1)"; echo "$out" | grep -E "FAIL|Traceback|Error:" | head -n 3; echo "$out" | grep -qx "17/17 pages built" && ! echo "$out" | grep -q Traceback'
check "Update Center moved" "kulan-updates opens Kulan Settings; old shortcut hidden" bash -c 'grep -q "kulan-settings updates" /usr/bin/kulan-updates && grep -qx "NoDisplay=true" /usr/share/applications/org.kulan.Updates.desktop && dpkg -S /usr/bin/kulan-updates | grep -q "^kulan-settings:"'
check "Store pages" "Kulan apps have valid AppStream pages" appstreamcli validate --no-net /usr/share/metainfo/org.kulan.Settings.metainfo.xml /usr/share/metainfo/org.kulan.Welcome.metainfo.xml
check "Kulan options" "org.kulan.desktop schema installed with Kulan defaults" bash -c "$(declare -f gs); test \"\$(gs org.kulan.desktop animation-speed)\" = \"'normal'\" && test \"\$(gs org.kulan.desktop kulan-menu)\" = true && test \"\$(gs org.kulan.desktop automatic-style)\" = false"
check "Sound theme" "Kulan chimes installed and chosen" bash -c "$(declare -f gs); test -f /usr/share/sounds/kulan/index.theme && test -s /usr/share/sounds/kulan/stereo/message-new-instant.oga && test \"\$(gs org.gnome.desktop.sound theme-name)\" = \"'kulan'\""
check "Mac shortcuts" "Super+Space search, Super+Shift+3/4/5 screenshots, Kulan Live Text and clipboard keys" bash -c "$(declare -f gs); gs org.gnome.shell.keybindings toggle-overview | grep -q \"'<Super>space'\" && gs org.gnome.shell.keybindings screenshot | grep -q '<Shift><Super>3' && gs org.gnome.shell.keybindings show-screenshot-ui | grep -q '<Shift><Super>4' && gs org.kulan.desktop live-text-shortcut | grep -q '<Shift><Super>t' && test \"\$(gs org.kulan.desktop clipboard-history)\" = false && test \"\$(gs org.gnome.desktop.interface icon-theme)\" = \"'Kulan'\" && test -f /usr/share/icons/Kulan/index.theme"
# shellcheck disable=SC2016  # expanded by the inner bash
check "Helper refuses bad input" "computer name, DNS and add-on values are checked before anything runs as root" bash -c 'for args in "set-hostname Bad_Name" "set-hostname -x" "set-hostname a.b"; do out="$(/usr/lib/kulan/update-run $args 2>&1)"; echo "$out" | grep -q "letters, numbers and dashes" || { echo "not refused by the check: $args"; exit 1; }; done; for args in "dns bogus" "dns" "addon-install ../x" "addon-remove nope" "nonsense"; do if /usr/lib/kulan/update-run $args >/dev/null 2>&1; then echo "accepted: $args"; exit 1; fi; done'
check "Dock default" "always visible" bash -c "$(declare -f gs); test \"\$(gs org.gnome.shell.extensions.dash-to-dock dock-fixed)\" = true"
# shellcheck disable=SC2016  # expanded by the inner bash
check "Pointer in VMs" "GNOME draws the pointer itself in a virtual machine (generator installed and runs)" bash -c 'g=/usr/lib/systemd/user-environment-generators/60-kulan-vm-cursor; test -x "$g" && out="$("$g")" && { [ -z "$out" ] || [ "$out" = MUTTER_DEBUG_DISABLE_HW_CURSORS=1 ]; }'
check "Files defaults" "large icons, folders first, readable dates, Delete permanently, Bin in the dock" bash -c "$(declare -f gs); test \"\$(gs org.gnome.nautilus.preferences default-folder-viewer)\" = \"'icon-view'\" && test \"\$(gs org.gnome.nautilus.icon-view default-zoom-level)\" = \"'large'\" && test \"\$(gs org.gnome.nautilus.preferences date-time-format)\" = \"'simple'\" && test \"\$(gs org.gnome.nautilus.preferences show-delete-permanently)\" = true && test \"\$(gs org.gtk.gtk4.Settings.FileChooser sort-directories-first)\" = true && test \"\$(gs org.gnome.shell.extensions.dash-to-dock show-trash)\" = true"
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
# shellcheck disable=SC2016  # expanded by the inner bash
check "Projects and templates" "first sign-in makes Projects (in the sidebar) and the templates, once" bash -c 'h=$(mktemp -d) && desktop-file-validate /etc/xdg/autostart/org.kulan.UserSetup.desktop && HOME=$h XDG_CONFIG_HOME= XDG_STATE_HOME= sh /usr/lib/kulan/user-setup && test -d $h/Projects && grep -qx "file://$h/Projects" $h/.config/gtk-3.0/bookmarks && t=$(HOME=$h xdg-user-dir TEMPLATES) && test -f "$t/Document.odt" && rm "$t/Document.odt" && HOME=$h XDG_CONFIG_HOME= XDG_STATE_HOME= sh /usr/lib/kulan/user-setup && test ! -e "$t/Document.odt" && test "$(wc -l < $h/.config/gtk-3.0/bookmarks)" -eq 1 && test "$(readlink $h/.local/share/org.gnome.Ptyxis/palettes/Kulan.palette)" = /usr/share/kulan/ptyxis/Kulan.palette'
check "Add-on list" "every add-on names its apps; the helper refuses anything else" python3 - <<'PY'
import json, re, subprocess
addons = json.load(open("/usr/share/kulan/addons.json"))
ids = [a["id"] for a in addons]
assert len(ids) == len(set(ids)), ids
assert {"apps", "devkit", "assistant", "learning-pack", "steam"} <= set(ids), ids
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
check "Package names" "add-on and security packages exist in Debian 13" bash -c 'missing=""; for p in $(python3 -c "import json; print(\" \".join(p for a in json.load(open(\"/usr/share/kulan/addons.json\")) for p in a.get(\"packages\", []) if not p.startswith(\"kulan-\")))") keepassxc metadata-cleaner network-manager-openvpn-gnome apparmor-profiles-extra lynis gnome-sushi gnome-characters gnome-weather gnome-clocks baobab gnome-shell-extension-tiling-assistant tesseract-ocr tesseract-ocr-eng power-profiles-daemon gir1.2-dbusmenu-glib-0.4 systemd-zram-generator gnome-shell-extension-desktop-icons-ng python3-nautilus poppler-utils tracker-miner-fs gir1.2-tsparql-3.0 ptyxis zsh zsh-autosuggestions zsh-syntax-highlighting qt5-gtk-platformtheme qt6-gtk-platformtheme; do apt-cache show "$p" >/dev/null 2>&1 || missing="$missing $p"; done; [ -z "$missing" ] || { echo "missing:$missing"; exit 1; }'
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
print(f"{len(names)} icon names checked")
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
	check "Assistant understands questions" "a search question becomes words, a kind of file and dates" python3 -c 'import sys; sys.path.insert(0, "/usr/share/kulan-assistant"); from kulan_assistant import search; q = search.understand("the invoice PDF from last month"); print(q); assert q and q["words"] and q["kind"] in ("pdf", "document", "any")'
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
