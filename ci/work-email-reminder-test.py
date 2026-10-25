#!/usr/bin/python3
"""Kulan Fleet 2.8e: the work email offer at sign-in (fleet-work-email), on
an installed Kulan OS, as root, inside dbus-run-session. A stand-in
notification service shows it and clicks Connect; a stand-in
gnome-control-center notes what it was opened with.

  dbus-run-session -- python3 -I -B ci/work-email-reminder-test.py
Prints "work email reminder: asked, connected, then not asked again", or fails.
"""
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from gi.repository import Gio, GLib

REMINDER = "/usr/lib/kulan/fleet-work-email"
me = pwd.getpwuid(os.getuid())
PUBLIC = Path("/var/lib/kulan/fleet-mail") / me.pw_name  # this account's own
home = Path(tempfile.mkdtemp())
fakebin = Path(tempfile.mkdtemp())
opened = fakebin / "opened"
gcc = fakebin / "gnome-control-center"
gcc.write_text(f"#!/bin/sh\necho \"$@\" > {opened}\n")
gcc.chmod(0o755)
saved = PUBLIC.read_bytes() if PUBLIC.exists() else None
shown = []

XML = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
<arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/></method>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
</interface></node>"""


def serve():
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]

    def call(conn, _sender, path, iface, method, params, invocation):
        shown.append(params.unpack())
        invocation.return_value(GLib.Variant("(u)", (42,)))

        def click(*_):
            conn.emit_signal(None, path, iface, "ActionInvoked", GLib.Variant("(us)", (42, "connect")))
            return False
        src = GLib.timeout_source_new(500)
        src.set_callback(click)
        src.attach(ctx)

    bus.register_object("/org/freedesktop/Notifications", info, call, None, None)
    bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                  GLib.Variant("(su)", ("org.freedesktop.Notifications", 4)), None, Gio.DBusCallFlags.NONE, -1, None)
    loop = GLib.MainLoop.new(ctx, False)
    loop.run()


threading.Thread(target=serve, daemon=True).start()
time.sleep(1)
env = {**os.environ, "HOME": str(home), "PATH": f"{fakebin}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
# The reminder reads the account's own home from the password database: point
# it at a throwaway one, so nothing of this account is touched.
WRAP = (f"import pwd, runpy\nreal = pwd.getpwuid\n"
        f"def fake(uid):\n    p = real(uid)\n"
        f"    return pwd.struct_passwd((p.pw_name, p.pw_passwd, p.pw_uid, p.pw_gid, p.pw_gecos, {str(home)!r}, p.pw_shell))\n"
        f"pwd.getpwuid = fake\nrunpy.run_path({REMINDER!r}, run_name='__main__')\n")


def reminder(timeout=60):
    return subprocess.run([sys.executable, "-I", "-c", WRAP], env=env, capture_output=True, text=True, timeout=timeout)


try:
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC.write_text(json.dumps({"address": "amina@acme.test", "provider": "google"}))
    r = reminder()
    assert r.returncode == 0, r.stderr
    assert len(shown) == 1, shown
    app, _rid, icon, title, body, actions, _hints, _t = shown[0]
    assert title == "Connect your work email" and "amina@acme.test" in body and "connect" in actions, shown[0]
    for _ in range(50):
        if opened.exists():
            break
        time.sleep(0.1)
    assert opened.exists() and opened.read_text().split() == ["online-accounts", "add", "google"], \
        opened.read_text() if opened.exists() else "not opened"
    # The same day again: not asked.
    r = reminder(30)
    assert r.returncode == 0 and len(shown) == 1, (r.stderr, shown)
    # Nobody with a mailbox: nothing, at once.
    PUBLIC.unlink()
    r = subprocess.run([REMINDER], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and len(shown) == 1, (r.stderr, shown)
finally:
    if saved is None:
        PUBLIC.unlink(missing_ok=True)
    else:
        PUBLIC.write_bytes(saved)
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(fakebin, ignore_errors=True)
print("work email reminder: asked, connected, then not asked again")
