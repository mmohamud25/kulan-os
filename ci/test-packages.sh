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

result() { # result <pass|fail> <check> <detail>
	if [[ "$1" == pass ]]; then
		echo "::notice title=Packages: $2::$3"
		ROWS+=("| ✅ | $2 | $3 |")
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

shopt -s nullglob
# Everything that goes into the ISO; kulan-devkit is an optional install,
# checked separately below.
DEBS=()
for DEB in dist/debs/kulan-*.deb; do
	[[ "${DEB}" == */kulan-devkit_* ]] || DEBS+=("${DEB}")
done
DEVKIT=(dist/debs/kulan-devkit_*.deb)
[[ ${#DEBS[@]} -eq 4 && ${#DEVKIT[@]} -eq 1 ]] || {
	echo "::error::expected 4 ISO packages and kulan-devkit in dist/debs, found ${#DEBS[@]} and ${#DEVKIT[@]}"
	exit 1
}
VERSION="$(cat dist/debs/VERSION)"

# --- Install, as the ISO build does (recommends included) -----------------------
# htop and gnome-tour are installed too, so the "Clutter hidden" check has
# real launchers to hide.
if apt-get install -y -q "${DEBS[@]/#/./}" htop gnome-tour >/tmp/install.log 2>&1; then
	result pass "Install" "kulan-base, kulan-desktop-settings, kulan-welcome, kulan-update-center ${VERSION} with Debian dependencies"
else
	result fail "Install" "apt failed: $(grep -E '^E:|error' /tmp/install.log | tail -n 3 | tr '\n' ' ')"
	tail -n 40 /tmp/install.log
	exit 1
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
check "Kulan Shell files" "GNOME 48 listed; dark and light stylesheets present" python3 - <<'PY'
import json, os
d = "/usr/share/gnome-shell/extensions/kulan-shell@kulangroup.com"
meta = json.load(open(f"{d}/metadata.json"))
assert "48" in meta["shell-version"], meta["shell-version"]
for f in ("extension.js", "stylesheet-dark.css", "stylesheet-light.css"):
    assert os.path.getsize(f"{d}/{f}") > 0, f
PY
check "Cursor" "Bibata-Modern-Classic set and installed" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.interface cursor-theme)\" = \"'Bibata-Modern-Classic'\" && test -d /usr/share/icons/Bibata-Modern-Classic"
# shellcheck disable=SC2016  # expanded by the inner bash
check "App folders" "Kulan folders are the default for new users" bash -c 'HOME=$(mktemp -d) XDG_CONFIG_HOME="" dconf read /org/gnome/desktop/app-folders/folder-children | grep -q Kulan-Office'
check "Accent color" "GNOME 48 accent = teal" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.interface accent-color)\" = \"'teal'\""
check "Fonts and icons" "Inter + Papirus-Dark" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.interface font-name)\" = \"'Inter 11'\" && test \"\$(gs org.gnome.desktop.interface icon-theme)\" = \"'Papirus-Dark'\""
check "Privacy defaults" "old files cleared after 30 days, no usage reports" bash -c "$(declare -f gs); test \"\$(gs org.gnome.desktop.privacy old-files-age)\" = 'uint32 30' && test \"\$(gs org.gnome.desktop.privacy send-software-usage-stats)\" = false"
check "One welcome tour" "GNOME's own welcome popup suppressed" bash -c "$(declare -f gs); test \"\$(gs org.gnome.shell welcome-dialog-last-shown-version)\" = \"'999'\""
check "Clutter hidden" "Tour, Htop and KDE System Settings hidden from the app grid" python3 - <<'PY'
import os, subprocess
code = "from gi.repository import Gio; print(sorted(a.get_id() for a in Gio.AppInfo.get_all() if a.should_show()))"
env = dict(os.environ, XDG_DATA_DIRS="/usr/share/kulan/xdg:/usr/local/share:/usr/share")
shown = subprocess.run(["python3", "-c", code], env=env, capture_output=True, text=True, check=True).stdout
hidden = ["org.gnome.Tour.desktop", "htop.desktop", "systemsettings.desktop", "org.kde.systemsettings.desktop"]
leaked = [h for h in hidden if f"'{h}'" in shown]
assert open("/usr/lib/environment.d/60-kulan-os.conf").read().count("/usr/share/kulan/xdg") == 1
raise SystemExit(f"still shown: {leaked}" if leaked else 0)
PY
check "Boot splash" "Plymouth theme kulan built and selected" bash -c 'test -f /usr/share/plymouth/themes/kulan/kulan.plymouth && grep -qx "Theme=kulan" /etc/plymouth/plymouthd.conf'

# --- Security and updates ---------------------------------------------------------
check "Boot speed" "network-wait startup step switched off once" test -e /var/lib/kulan/network-wait-disabled
check "Firewall" "ufw enabled on first install" grep -qx 'ENABLED=yes' /etc/ufw/ufw.conf
check "Auto updates" "Debian security + Kulan origins allowed" bash -c 'apt-config dump | grep -q "origin=Kulan OS" && apt-config dump | grep -q "label=Debian-Security"'
check "Firefox privacy" "policies.json valid" python3 -c 'import json; json.load(open("/etc/firefox/policies/policies.json"))'
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
check "Release notes" "notes exist for this version" python3 -c 'import json, shlex; v = [shlex.split(l.split("=",1)[1])[0] for l in open("/usr/lib/os-release") if l.startswith("VERSION_ID=")][0]; assert any(n["version"] == v for n in json.load(open("/usr/share/kulan/release-notes.json"))), v'
check "Update Center compiles" "python3 -m py_compile" python3 -m py_compile /usr/bin/kulan-updates /usr/lib/kulan/update-status
check "Developer kit" "kulan-devkit installs cleanly (simulated)" apt-get -s -q install "./${DEVKIT[0]}"

# --- Welcome app against Debian's real GTK and libadwaita ------------------------------
check "Welcome app compiles" "python3 -m py_compile" python3 -m py_compile /usr/bin/kulan-welcome
check "Welcome app APIs" "every GTK/libadwaita class it uses exists" python3 - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
needed = {
    Adw: ["Application", "ApplicationWindow", "ToolbarView", "HeaderBar", "ToastOverlay", "Toast", "Carousel",
          "CarouselIndicatorDots", "StatusPage", "Clamp", "PreferencesGroup", "ActionRow", "SwitchRow",
          "PreferencesPage", "ExpanderRow"],
    Gtk: ["UriLauncher", "CenterBox", "Picture", "ContentFit", "Spinner", "CheckButton", "ToggleButton", "CssProvider",
          "ProgressBar", "Image"],
}
missing = [f"{lib.__name__}.{n}" for lib, names in needed.items() for n in names if not hasattr(lib, n)]
print(f"libadwaita {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}, GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}")
raise SystemExit(f"missing: {missing}" if missing else 0)
PY
check "Welcome app runs" "--help" kulan-welcome --help

# --- Removal restores Debian -------------------------------------------------------
if dpkg --purge kulan-update-center kulan-welcome kulan-desktop-settings kulan-base >/tmp/purge.log 2>&1; then
	check "Removal" "Debian's os-release restored" grep -q '^ID=debian' /usr/lib/os-release
	check "Removal cleans up" "no diversion, splash theme, generated settings or update status left" bash -c '! dpkg-divert --list | grep -q kulan && test ! -e /usr/share/plymouth/themes/kulan && test ! -e /usr/share/glib-2.0/schemas/98_kulan-os-generated.gschema.override && test ! -e /var/lib/kulan/updates.json'
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
