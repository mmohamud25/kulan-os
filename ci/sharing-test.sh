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
PY
# GNOME Remote Desktop itself reads the same username and password.
out="$(grdctl status --show-credentials 2>/dev/null)"
grep -q "Username: amina" <<<"${out}" && grep -qF "Password: Pa'ss {x} \"y\"" <<<"${out}" || { echo "grdctl doesn't see them: ${out}"; exit 1; }
echo "sharing self-test passed"
