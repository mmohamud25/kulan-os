#!/usr/bin/python3
"""Kulan Fleet 2.8e on the computer (workmail.py): who has a work mailbox,
whether they've connected it, and the once-a-day offer, every case, with
made-up files only. Then, if given a Kulan Settings source folder, its
Work email row (run that part under xvfb-run).

  python3 -I -B ci/workmail-test.py [path/to/workmail.py] [path/to/kulan-settings]
Prints "workmail: every case passed", or ::error:: lines and exits 1.
"""
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "packages/kulan-base/root/usr/share/kulan-fleet/workmail.py"
spec = importlib.util.spec_from_file_location("workmail", SRC)
wm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wm)

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
        print(f"::error::workmail: {name} {detail}")


def acct(user, mail=None, **kw):
    a = {"username": user, "enabled": True, **kw}
    if mail is not None:
        a["mail"] = mail
    return a


G = lambda address: {"address": address, "provider": "google"}  # noqa: E731

with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    public = t / "fleet-mail"
    import os
    me = (os.getuid(), os.getgid())
    owner = lambda u: me if u in ("amina", "omar") else None  # noqa: E731  (accounts that exist here)
    accounts = [acct("amina", G("amina@acme.test")), acct("omar"), acct("local", G("boss@acme.test")),
                acct("gone", G("gone@acme.test"), removed=True), acct("off", G("off@acme.test"), enabled=False),
                acct("bad", {"address": "not an address", "provider": "google"}),
                acct("other", {"address": "o@acme.test", "provider": "exchange"}),
                acct("Bad:Name", G("x@acme.test")), "junk"]
    got = wm.apply(accounts, {"amina", "omar", "gone", "off", "bad", "other", "Bad:Name"}, public, owner)
    check("only managed, enabled accounts with a well-formed google mailbox", got == {"amina": G("amina@acme.test")}, got)
    check("her own file, only she can read it", sorted(f.name for f in public.iterdir()) == ["amina"] and
          oct((public / "amina").stat().st_mode & 0o777) == "0o600" and (public / "amina").stat().st_uid == me[0] and
          json.loads((public / "amina").read_text()) == G("amina@acme.test"))
    check("mine", wm.mine("amina", public) == G("amina@acme.test") and wm.mine("omar", public) is None)
    check("no account here yet: no file until there is", wm.apply([acct("new", G("new@acme.test"))], {"new"}, public, owner) == {}
          and not (public / "new").exists() and not (public / "amina").exists())
    wm.apply([acct("amina")], {"amina"}, public, owner)
    check("nobody with a mailbox: no files", list(public.iterdir()) == [])
    check("mine with no file", wm.mine("amina", public) is None)
    (public / "amina").write_text("not json")
    check("mine with a broken file", wm.mine("amina", public) is None)
    wm.apply(accounts, {"amina"}, public, owner)
    check("a broken file is replaced", wm.mine("amina", public) == G("amina@acme.test"))

    # Connected: a GNOME Online Accounts Google account for that address.
    home = t / "home"
    goa = home / ".config" / "goa-1.0" / "accounts.conf"
    check("nothing set up: not connected", not wm.connected(G("amina@acme.test"), home))
    goa.parent.mkdir(parents=True)
    goa.write_text("[Account account_1_0]\nProvider=google\nIdentity=amina.personal@gmail.com\n"
                   "PresentationIdentity=amina.personal@gmail.com\nMailEnabled=true\n")
    check("her own Gmail isn't her work email", not wm.connected(G("amina@acme.test"), home))
    goa.write_text(goa.read_text() + "\n[Account account_2_0]\nProvider=ms365\nIdentity=amina@acme.test\n")
    check("another provider with the same address isn't it", not wm.connected(G("amina@acme.test"), home))
    goa.write_text(goa.read_text() + "\n[Account account_3_0]\nProvider=google\nIdentity=Amina@Acme.test\n"
                   "PresentationIdentity=Amina@Acme.test\n")
    check("connected (whatever the capitals)", wm.connected(G("amina@acme.test"), home))
    goa.write_bytes(b"\xff\xfe[broken")
    check("an unreadable accounts file: not connected, no crash", not wm.connected(G("amina@acme.test"), home))
    goa.write_text("[Account account_3_0\nbroken")
    check("a malformed accounts file: not connected, no crash", not wm.connected(G("amina@acme.test"), home))
    goa.unlink()
    check("connect opens Settings › Online Accounts › Google",
          wm.connect_command(G("amina@acme.test")) == ["gnome-control-center", "online-accounts", "add", "google"])

    # Asked once a day, until connected.
    day1 = time.mktime((2026, 10, 26, 9, 0, 0, 0, 0, -1))
    check("first sign-in of the day: asked", wm.ask_today("amina", home, public, day1) == G("amina@acme.test"))
    check("again that day: not asked", wm.ask_today("amina", home, public, day1 + 3600) is None)
    check("the next day: asked again", wm.ask_today("amina", home, public, day1 + 86400) == G("amina@acme.test"))
    check("someone without a mailbox: never asked", wm.ask_today("omar", home, public, day1) is None)
    goa.write_text("[Account account_3_0]\nProvider=google\nIdentity=amina@acme.test\n")
    check("connected: never asked", wm.ask_today("amina", home, public, day1 + 2 * 86400) is None)
    goa.unlink()
    note = home / ".local" / "state" / "kulan" / "work-email-asked"
    note.unlink()
    note.mkdir()  # can't be written: still asks, never fails
    check("a note that can't be kept: still asks, no crash", wm.ask_today("amina", home, public, day1) == G("amina@acme.test"))

    wm.remove_all(public)
    check("leaving: no work email offered", not public.exists() and wm.mine("amina", public) is None)
    public.mkdir()
    (t / "elsewhere").write_text("keep")
    (public / "amina").symlink_to(t / "elsewhere")
    wm.apply(accounts, {"amina"}, public, owner)
    check("a link put in its place is replaced, never written through", (t / "elsewhere").read_text() == "keep" and
          not (public / "amina").is_symlink() and wm.mine("amina", public) == G("amina@acme.test"))
    wm.remove_all(public)  # twice is fine

    # Kulan Settings' row (under xvfb-run, when given its source).
    if len(sys.argv) > 2:
        import getpass
        sys.path.insert(0, sys.argv[2])
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from kulan_settings import about
        about.AboutPage.WORKMAIL = SRC
        loaded = about.AboutPage.workmail()
        check("Settings loads the module", loaded is not None)
        public.mkdir(exist_ok=True)
        loaded.PUBLIC = public
        about.AboutPage.workmail = classmethod(lambda cls: loaded)
        page = about.AboutPage.__new__(about.AboutPage)
        me = getpass.getuser()
        (public / me).write_text(json.dumps(G("me@acme.test")))
        row = page.work_email_row()
        check("Settings: not connected yet, with a Connect button",
              row is not None and "Not connected" in row.get_subtitle() and "me@acme.test" in row.get_subtitle(),
              row and row.get_subtitle())
        (public / me).unlink()
        check("Settings: no row without a work mailbox", page.work_email_row() is None)

if failures:
    sys.exit(1)
print("workmail: every case passed")
