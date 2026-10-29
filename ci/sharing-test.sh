#!/bin/bash
# Remote desktop on Debian 13, the way Kulan Settings › Sharing sets it up:
# GNOME Remote Desktop's settings and user service are there, the username
# and password are kept in the keyring exactly as GNOME Remote Desktop reads
# them (checked with its own grdctl), and a certificate is made. Run inside
# dbus-run-session, with gnome-keyring installed.
set -u
eval "$(printf 'test\n' | gnome-keyring-daemon --unlock --components=secrets 2>/dev/null)" || true
export GSETTINGS_BACKEND=memory
python3 -I -B - <<'PY' || exit 1
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "/usr/share/kulan-settings")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio
from kulan_settings import sharing as s
source = Gio.SettingsSchemaSource.get_default()
schema = source.lookup(s.SCHEMA_ID, True)
assert schema is not None, "no GNOME Remote Desktop settings"
missing = [k for k in ("enable", "tls-cert", "tls-key", "view-only", "port", "negotiate-port") if not schema.has_key(k)]
assert not missing, f"settings missing: {missing}"
assert s.SERVICE_FILE.exists(), f"no {s.SERVICE_FILE}"
from gi.repository import GLib
rdp = s.settings(s.SCHEMA_ID)
rdp.set_value("port", GLib.Variant("q", 4000))
rdp.set_value("port", GLib.Variant("q", s.PORT))
assert rdp.get_value("port").unpack() == s.PORT, rdp.get_value("port")
assert s.load_credentials() is None, "credentials before any were kept"
assert s.store_credentials("amina", "Pa'ss {x} \"y\""), "the keyring didn't keep them"
assert s.load_credentials() == ("amina", "Pa'ss {x} \"y\""), s.load_credentials()
made = s.make_certificate(Path(tempfile.mkdtemp()) / "grd")
assert made and made[1].stat().st_mode & 0o777 == 0o600, made
assert len(s.fingerprint(made[0])) == 95, s.fingerprint(made[0])
assert len(s.new_password()) == 19 and s.parse_credentials("nonsense") is None
print("sharing: settings, keyring and certificate ok")
# File and media sharing: gnome-settings-daemon's per-network lists, and the password file.
import hashlib, os
for service in (s.FILES_SERVICE, s.MEDIA_SERVICE):
    g = s.sharing_settings(service)
    assert g is not None, f"no {service} service or no sharing settings"
    s.set_shared_on(g, "uuid-home", True)
    s.set_shared_on(g, "uuid-home", True)
    s.set_shared_on(g, "uuid-office", True)
    assert g.get_strv("enabled-connections") == ["uuid-home", "uuid-office"], g.get_strv("enabled-connections")
    s.set_shared_on(g, "uuid-home", False)
    assert s.shared_on(g, "uuid-office") and not s.shared_on(g, "uuid-home") and not s.shared_on(g, None)
assert s.settings(s.FILE_SHARING_SCHEMA).get_string("require-password") == "never", "GNOME's default changed"
line = s.passwd_line("pw:x")
assert line == "guest:Please log in as the user guest:" + hashlib.md5(b"guest:Please log in as the user guest:pw:x").hexdigest() + "\n"
p = Path(tempfile.mkdtemp()) / "user-share" / "passwd"
assert s.write_passwd("secret", p) and p.read_text() == s.passwd_line("secret") and p.stat().st_mode & 0o777 == 0o600
assert s.store_files_password("abcd-EFGH") and s.files_password() == "abcd-EFGH"
assert s.FILES_PASSWD.read_text() == s.passwd_line("abcd-EFGH")
print("sharing: file and media sharing settings ok")
PY
# GNOME Remote Desktop itself reads the same username and password.
out="$(grdctl status --show-credentials 2>/dev/null)"
grep -q "Username: amina" <<<"${out}" && grep -qF "Password: Pa'ss {x} \"y\"" <<<"${out}" || { echo "grdctl doesn't see them: ${out}"; exit 1; }
echo "sharing self-test passed"
