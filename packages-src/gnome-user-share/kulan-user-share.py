#!/usr/bin/env python3
"""Kulan's changes to gnome-user-share's source (see ci/build-user-share.sh).

  python3 kulan-user-share.py SOURCE_DIR

Each change finds exactly what it expects in the source, or fails, so a new
Debian version that no longer fits is noticed rather than built unchanged.
"""
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
http = src / "src/http.rs"
conf = src / "data/dav_user_2.4.conf"
text = http.read_text()

# 1. The port: GNOME_USER_SHARE_PORT when it's set (1-65535), else a random one as before.
old = re.search(r"static HTTPD_PORT: LazyLock<u16> = LazyLock::new\(\|\| \{\n(\tSOCKET\n.*?\n)\}\);\n", text, re.S)
if not old or text.count("static HTTPD_PORT") != 1:
    sys.exit("HTTPD_PORT isn't where it was")
new = ("static HTTPD_PORT: LazyLock<u16> = LazyLock::new(|| {\n"
       "\t// Kulan OS: a fixed port when one is given, so a firewall can let it in.\n"
       "\tif let Some(port) = std::env::var(\"GNOME_USER_SHARE_PORT\")\n"
       "\t\t.ok()\n"
       "\t\t.and_then(|p| p.trim().parse::<u16>().ok())\n"
       "\t\t.filter(|p| *p > 0)\n"
       "\t{\n"
       "\t\treturn port;\n"
       "\t}\n"
       + old.group(1) +
       "});\n")
text = text.replace(old.group(0), new)

# 2. The realm: the one gnome-control-center (and Kulan) make the password file with.
REALM = "Please log in as the user guest"
conf_text = conf.read_text()
if 'AuthName "${GUS_LOGIN_LABEL}"' in conf_text:
    conf_text = conf_text.replace('AuthName "${GUS_LOGIN_LABEL}"', f'AuthName "{REALM}"')
    conf.write_text(conf_text)
if conf_text.count(f'AuthName "{REALM}"') != 2:
    sys.exit("the password realm isn't where it was")

http.write_text(text)
print("gnome-user-share: fixed port and password realm")
