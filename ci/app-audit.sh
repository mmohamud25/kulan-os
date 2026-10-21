#!/bin/bash
# App audit: opens every app Kulan OS shows in the app grid, inside a real
# GNOME Shell (Debian 13 with the Kulan packages), and screenshots each in
# light and dark mode. Also notes which toolkit each app is built with, the
# main reason an app looks "foreign" next to the others.
#
# Runs as root inside a debian:trixie container. Output in audit-out/:
# <app>-light.jpg, <app>-dark.jpg and report.md.
#
#   ci/app-audit.sh
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
OUT="${PWD}/audit-out"
mkdir -p "${OUT}"
find "${OUT}" -mindepth 1 ! -name audit.log -delete
# Logs go with the results, so a failure can be read from the results branch.
trap 'cp /tmp/audit-*.log "${OUT}/" 2>/dev/null; true' EXIT
export DEBIAN_FRONTEND=noninteractive
# The same archive areas as the ISO (Tor Browser's launcher is in contrib,
# firmware in non-free-firmware).
sed -i -E 's/^Components: .*/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources

# --- Install Kulan OS's desktop as the ISO does ---------------------------------
apt-get update -q
apt-get install -y -q --no-install-recommends ca-certificates dpkg-dev git >/tmp/audit-tools.log 2>&1 ||
	{ echo "::error title=App audit::couldn't install the build tools"; exit 1; }
bash packages/build-debs.sh >/tmp/audit-debs.log 2>&1 || { tail -n 20 /tmp/audit-debs.log; echo "::error title=App audit::package build failed"; exit 1; }
mapfile -t LIST < <(grep -vE '^[[:space:]]*(#|$)' config/package-lists/kulan-desktop.list.chroot |
	grep -vxE 'kulan-.*|plymouth.*|spice-vdagent|qemu-guest-agent|linux-image.*')
DEBS=(dist/debs/kulan-base_*.deb dist/debs/kulan-desktop-settings_*.deb dist/debs/kulan-settings_*.deb
	dist/debs/kulan-update-center_*.deb dist/debs/kulan-welcome_*.deb dist/debs/kulan-assistant_*.deb)
apt-get install -y -q "${DEBS[@]/#/./}" "${LIST[@]}" xvfb xauth imagemagick dbus-x11 xwayland sudo python3-dbusmock \
	>/tmp/audit-install.log 2>&1 || { tail -n 30 /tmp/audit-install.log; echo "::error title=App audit::install failed"; exit 1; }
# The installer as the ISO has it (Kulan branding, stylesheet and settings).
sh config/hooks/live/0300-kulan-installer.hook.chroot >/tmp/audit-installer.log 2>&1 ||
	echo "::warning title=App audit::installer branding failed: $(tail -n 2 /tmp/audit-installer.log | tr '\n' ' ')"

# A person's account, as after installing (apps such as VLC refuse root).
adduser --disabled-password --gecos Kulan kulan >/dev/null
echo 'kulan ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/kulan
mkdir -p /run/dbus /run/user/1000 && chown kulan:kulan /run/user/1000 && chmod 700 /run/user/1000
dbus-daemon --system --fork
# GNOME Shell needs the login service (systemd-logind), which a container
# doesn't run; GNOME's own tests use this stand-in for it.
python3 -m dbusmock --system --template logind >/tmp/audit-logind.log 2>&1 &
for _ in $(seq 1 20); do
	dbus-send --system --print-reply --dest=org.freedesktop.login1 /org/freedesktop/login1 \
		org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 && break
	sleep 0.5
done

# --- Which apps a person sees in the app grid -----------------------------------
python3 - > /tmp/apps.tsv <<'PY'
import configparser, glob, os, shlex, shutil, subprocess
hidden_dir = "/usr/share/kulan/xdg/applications"
for path in sorted(glob.glob("/usr/share/applications/*.desktop")):
    app_id = os.path.basename(path)
    c = configparser.RawConfigParser(strict=False, interpolation=None)
    c.optionxform = str
    try:
        c.read(path, encoding="utf-8")
        e = c["Desktop Entry"]
    except Exception:
        continue
    if e.get("Type", "Application") != "Application" or e.get("NoDisplay", "false") == "true" \
            or e.get("Hidden", "false") == "true" or e.get("Terminal", "false") == "true":
        continue
    only = e.get("OnlyShowIn", "")
    if only and "GNOME" not in only.split(";"):
        continue
    if "GNOME" in e.get("NotShowIn", "").split(";"):
        continue
    if os.path.exists(os.path.join(hidden_dir, app_id)):
        continue  # Kulan hides it
    if app_id.startswith("display-im"):
        continue  # ImageMagick, installed only for these screenshots
    exec_line = " ".join(t for t in shlex.split(e.get("Exec", "")) if not (t.startswith("%") and len(t) == 2))
    binary = shutil.which(shlex.split(exec_line)[0]) if exec_line else None
    toolkit = "?"
    real = os.path.realpath(binary) if binary else ""
    name = os.path.basename(real)
    try:
        head = open(real, "rb").read(4096) if real else b""
    except OSError:
        head = b""
    if "firefox" in name:
        toolkit = "Firefox (its own look, GTK 3 underneath)"
    elif "soffice" in name or "libreoffice" in real:
        toolkit = "LibreOffice (its own look, GTK 3 underneath)"
    elif head.startswith(b"#!"):
        text = open(real, encoding="utf-8", errors="replace").read()
        script_dirs = " ".join(glob.glob("/usr/share/" + name + "*") + glob.glob("/usr/share/" + name.replace("-", "_") + "*"))
        more = subprocess.run(f"grep -rhs 'require_version' {script_dirs} | head -20", shell=True,
                              capture_output=True, text=True).stdout if script_dirs else ""
        allt = text + more
        if "'Adw'" in allt or '"Adw"' in allt:
            toolkit = "GTK 4 + libadwaita (native)"
        elif "'Gtk', '3.0'" in allt or '"Gtk", "3.0"' in allt:
            toolkit = "GTK 3"
        elif "Gtk" in allt:
            toolkit = "GTK (script)"
        else:
            toolkit = "script: " + head.split(b"\n")[0].decode(errors="replace")[2:40]
    elif real:
        libs = subprocess.run(["ldd", real], capture_output=True, text=True).stdout
        if "libadwaita-1" in libs:
            toolkit = "GTK 4 + libadwaita (native)"
        elif "libgtk-4" in libs:
            toolkit = "GTK 4 (no libadwaita)"
        elif "libgtk-3" in libs:
            toolkit = "GTK 3"
        elif "libQt6" in libs:
            toolkit = "Qt 6"
        elif "libQt5" in libs:
            toolkit = "Qt 5"
        elif "libgtk-x11-2.0" in libs:
            toolkit = "GTK 2"
        else:
            toolkit = "other"
    if toolkit == "other" or toolkit.startswith(("script", "GTK (script)")):
        # From what the app's package needs (scripts, launchers, GJS apps).
        owner = subprocess.run(["dpkg", "-S", path], capture_output=True, text=True).stdout.split(":")[0]
        deps = subprocess.run(["dpkg-query", "-W", "-f=${Depends}", owner], capture_output=True, text=True).stdout \
            if owner else ""
        if "libadwaita-1" in deps or "gir1.2-adw-1" in deps:
            toolkit = "GTK 4 + libadwaita (native)"
        elif "libgtk-4" in deps or "gir1.2-gtk-4.0" in deps:
            toolkit = "GTK 4 (no libadwaita)"
        elif "libgtk-3" in deps or "gir1.2-gtk-3.0" in deps:
            toolkit = "GTK 3"
        elif "libqt6" in deps:
            toolkit = "Qt 6"
        elif "libqt5" in deps:
            toolkit = "Qt 5"
        elif "libgtk2" in deps:
            toolkit = "GTK 2"
    print("\t".join([app_id, e.get("Name", app_id), exec_line, toolkit]))
PY
echo "Apps to check: $(wc -l < /tmp/apps.tsv)"

# --- Screenshots inside GNOME Shell ----------------------------------------------
cat > /tmp/audit-inner.sh <<'IN'
#!/bin/bash
set -u
ev() { gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell --method org.gnome.Shell.Eval "$1" 2>/dev/null; }
export XDG_RUNTIME_DIR=/run/user/1000 MUTTER_DEBUG_DUMMY_MODE_SPECS=1440x900 NO_AT_BRIDGE=1
# The session's environment (systemd reads these on a real login).
export XDG_DATA_DIRS="/usr/share/kulan/xdg:/usr/local/share:/usr/share"
grep -q '^QT_QPA_PLATFORMTHEME=' /usr/lib/environment.d/60-kulan-os.conf 2>/dev/null && export QT_QPA_PLATFORMTHEME=gtk3
gnome-shell --nested --wayland --unsafe-mode >/tmp/audit-shell.log 2>&1 &
SHELL_PID=$!
UP=0
for _ in $(seq 1 60); do ev "1" | grep -q true && { UP=1; break; }; sleep 1; done
if [ "$UP" != 1 ]; then echo "GNOME Shell didn't start" > "$OUT_DIR/SHELL-FAILED"; exit 1; fi
sleep 8
ev "Main.overview.hide(); Main.messageTray._bannerBin?.hide(); 'ok'" >/dev/null
export WAYLAND_DISPLAY
WAYLAND_DISPLAY="$(basename "$(ls -t "${XDG_RUNTIME_DIR}"/wayland-* 2>/dev/null | grep -v lock | head -n 1)")"
# Screenshots come from the virtual screen; apps that only speak X11 go to
# the shell's own Xwayland, so they get GNOME's window frames as on a PC.
SCREEN="${DISPLAY}"
# Which icon theme the session uses, and which file GTK picks for a folder.
{
	echo "icon-theme: $(gsettings get org.gnome.desktop.interface icon-theme)"
	python3 - <<'PYI'
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk
Gtk.init()
theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
print("gtk theme:", theme.get_theme_name())
for name in ("folder", "folder-download", "inode-directory"):
    icon = theme.lookup_icon(name, None, 96, 1, Gtk.TextDirection.NONE, 0)
    print(name, "->", icon.get_file().get_path() if icon.get_file() else icon.get_icon_name())
PYI
	ls -la /usr/share/icons/Kulan/
} > "$OUT_DIR/icons.txt" 2>&1
XWAYLAND="$(sed -n 's/.*Using public X11 display \(:[0-9]*\).*/\1/p' /tmp/audit-shell.log | tail -n 1)"
# Apps started by D-Bus (Files, Settings, Maps...) get the session bus's
# environment: point it at this shell too, not at the virtual screen.
dbus-update-activation-environment WAYLAND_DISPLAY="${WAYLAND_DISPLAY}" DISPLAY="${XWAYLAND:-$SCREEN}" \
	XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" XDG_DATA_DIRS="${XDG_DATA_DIRS}" QT_QPA_PLATFORMTHEME="${QT_QPA_PLATFORMTHEME:-}" 2>/dev/null
echo "wayland=${WAYLAND_DISPLAY} xwayland=${XWAYLAND:-none} screen=${SCREEN}" > "$OUT_DIR/displays.txt"
for scheme in light dark; do
	if [ "$scheme" = dark ]; then gsettings set org.gnome.desktop.interface color-scheme prefer-dark; else gsettings set org.gnome.desktop.interface color-scheme default; fi
	sleep 2
	while IFS=$'\t' read -r id name exec toolkit; do
		base="${id%.desktop}"
		wait_s=7
		case "$id" in *firefox*|*libreoffice*|*gimp*|*calamares*|*torbrowser*) wait_s=25 ;; esac
		if [[ "$id" == *calamares* ]]; then
			DISPLAY="${XWAYLAND:-$SCREEN}" setsid sudo -E calamares >/dev/null 2>&1 &
		else
			DISPLAY="${XWAYLAND:-$SCREEN}" setsid bash -c "exec $exec" >/dev/null 2>&1 &
		fi
		APP=$!
		sleep "$wait_s"
		# GNOME 48's Eval returns JSON: a number comes back as '2'.
		wins="$(ev "global.get_window_actors().filter(a => !a.meta_window.is_override_redirect() && a.meta_window.get_window_type() === 0).length" | sed -n "s/.*'\"\{0,1\}\([0-9]*\)\"\{0,1\}'.*/\1/p")"
		ev "Main.overview.hide(); 'ok'" >/dev/null
		sleep 1
		DISPLAY="$SCREEN" import -window root "/tmp/shot.png" 2>/dev/null && convert /tmp/shot.png -quality 82 "$OUT_DIR/${base}-${scheme}.jpg"
		echo -e "${id}\t${scheme}\t${wins:-0}" >> "$OUT_DIR/windows.tsv"
		ev "global.get_window_actors().forEach(a => a.meta_window.delete(global.get_current_time())); 'ok'" >/dev/null
		sleep 2
		pkill -KILL -s "$APP" 2>/dev/null
		sudo pkill -KILL calamares 2>/dev/null
		bin="$(basename "$(echo "$exec" | awk '{print $1}')")"
		[ -n "$bin" ] && pkill -KILL -u kulan -x "${bin:0:15}" 2>/dev/null
		ev "global.get_window_actors().forEach(a => a.meta_window.kill()); 'ok'" >/dev/null
		sleep 1
	done < /tmp/apps.tsv
done
kill "$SHELL_PID" 2>/dev/null
IN
chmod 755 /tmp/audit-inner.sh
chown -R kulan:kulan "${OUT}"
su kulan -s /bin/bash -c "export OUT_DIR='${OUT}'; cd ~; xvfb-run -a -s '-screen 0 1440x900x24' dbus-run-session -- /tmp/audit-inner.sh" 2>/tmp/audit-run.log
if [ -e "${OUT}/SHELL-FAILED" ]; then
	echo "::error title=App audit::GNOME Shell didn't start: $(grep -m 3 -E 'JS ERROR|CRITICAL|Failed' /tmp/audit-shell.log | tr '\n' ' ' | cut -c1-300)"
	cp /tmp/audit-shell.log "${OUT}/" 2>/dev/null
	exit 1
fi
grep -iE "error|critical" /tmp/audit-shell.log 2>/dev/null | grep -i kulan | head -n 20 > "${OUT}/shell-kulan-errors.txt" || true

# --- Report ------------------------------------------------------------------------
{
	echo "# Kulan OS app audit"
	echo
	echo "Every app shown in the app grid, opened in GNOME Shell on Debian 13 with the Kulan packages."
	echo
	echo "| App | Toolkit | Window opened (light/dark) | Light | Dark |"
	echo "|---|---|---|---|---|"
	while IFS=$'\t' read -r id name _exec toolkit; do
		base="${id%.desktop}"
		l="$(awk -F'\t' -v i="$id" '$1==i && $2=="light"{print $3}' "${OUT}/windows.tsv" 2>/dev/null)"
		d="$(awk -F'\t' -v i="$id" '$1==i && $2=="dark"{print $3}' "${OUT}/windows.tsv" 2>/dev/null)"
		echo "| ${name} (\`${id}\`) | ${toolkit} | ${l:-?} / ${d:-?} | ![](${base}-light.jpg) | ![](${base}-dark.jpg) |"
	done < /tmp/apps.tsv
} > "${OUT}/report.md"
cp /tmp/apps.tsv "${OUT}/apps.tsv"
echo "Screenshots: $(find "${OUT}" -name "*.jpg" | wc -l)"
cut -f2,4 /tmp/apps.tsv | sort -t$'\t' -k2 | sed 's/^/  /'
