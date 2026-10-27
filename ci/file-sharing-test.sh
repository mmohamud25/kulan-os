#!/bin/bash
# File sharing for real: Apache started exactly as gnome-user-share starts it
# (its own config, Kulan's port 30301), with the password file Kulan Settings
# writes. Anyone may read the Public folder; adding files needs the password;
# with "ask for the password to open it too", reading does as well. Runs as
# root in a test container (Apache itself runs as a normal user, as it does
# on a desktop).
set -u
command -v apache2 >/dev/null || { echo "apache2 isn't installed (gnome-user-share brings it)"; exit 1; }
CONF=/usr/share/gnome-user-share/dav_user_2.4.conf
[[ -s "${CONF}" ]] || { echo "no ${CONF}"; exit 1; }
id kshare >/dev/null 2>&1 || useradd -m kshare
H=/home/kshare
install -d -o kshare "${H}/Public" "${H}/.config/user-share"
echo hello > "${H}/Public/a.txt"
chown kshare "${H}/Public/a.txt"
runuser -u kshare -- env HOME="${H}" XDG_CONFIG_HOME="${H}/.config" python3 -I -B -c '
import sys; sys.path.insert(0, "/usr/share/kulan-settings")
import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from pathlib import Path
from kulan_settings import sharing as s
assert s.write_passwd("pw-1234", Path.home() / ".config/user-share/passwd")' || { echo "the password file wasn't written"; exit 1; }

serve() {  # $1: RequirePasswordOnWrite or RequirePasswordAlways
	runuser -u kshare -- pkill -u kshare -x apache2 2>/dev/null; sleep 1
	runuser -u kshare -- env HOME="${H}" XDG_PUBLICSHARE_DIR="${H}/Public" XDG_CONFIG_HOME="${H}/.config" \
		GUS_SHARE_NAME="Test's public files" HTTP_MODULES_PATH=/usr/lib/apache2/modules LANG=C \
		bash -c "cd '${H}' && apache2 -f '${CONF}' -C 'Listen 30301' -D '$1'" || { echo "Apache didn't start"; cat "${H}/.config/user-share/log" 2>/dev/null; exit 1; }
	for _ in $(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:30301/ && return 0; sleep 0.5; done
	echo "nothing answers on 30301"; cat "${H}/.config/user-share/log" 2>/dev/null; exit 1
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
fail() { echo "FAIL $*"; runuser -u kshare -- pkill -u kshare -x apache2; exit 1; }

serve RequirePasswordOnWrite
[[ "$(curl -s http://127.0.0.1:30301/a.txt)" == hello ]] || fail "reading without a password"
[[ "$(code -T /etc/hostname http://127.0.0.1:30301/b.txt)" == 401 ]] || fail "adding a file without the password wasn't refused"
[[ "$(code --digest -u guest:wrong -T /etc/hostname http://127.0.0.1:30301/b.txt)" == 401 ]] || fail "a wrong password was taken"
[[ "$(code --digest -u guest:pw-1234 -T /etc/hostname http://127.0.0.1:30301/b.txt)" == 201 ]] || fail "adding a file with the password"
[[ -s "${H}/Public/b.txt" ]] || fail "the added file isn't in Public"
serve RequirePasswordAlways
[[ "$(code http://127.0.0.1:30301/a.txt)" == 401 ]] || fail "reading without the password, when it's asked for"
[[ "$(curl -s --digest -u guest:pw-1234 http://127.0.0.1:30301/a.txt)" == hello ]] || fail "reading with the password"
runuser -u kshare -- pkill -u kshare -x apache2
echo "file sharing test passed"
