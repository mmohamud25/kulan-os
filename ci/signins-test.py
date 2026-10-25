#!/usr/bin/python3
"""Kulan Fleet 2.8c on the computer (signins.py), every case, without touching
this machine: a pretend journalctl and loginctl, a made-up lastlog2
database and a made-up list of accounts.

  python3 -I -B ci/signins-test.py [path/to/signins.py]
Prints "signins: every case passed", or ::error:: lines and exits 1.
"""
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "packages/kulan-base/root/usr/share/kulan-fleet/signins.py"
spec = importlib.util.spec_from_file_location("signins", SRC)
si = importlib.util.module_from_spec(spec)
spec.loader.exec_module(si)

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
        print(f"::error::signins: {name} {detail}")


NOW = 1_800_000_000
PEOPLE = {"amina", "omar", "mo"}  # accounts with a uid of 1000 or more here


def journal(*entries):
    return "\n".join(json.dumps({"MESSAGE": m, "__REALTIME_TIMESTAMP": str(t * 1_000_000)}) for m, t in entries)


class Box:
    def __init__(self, journal_out="", sessions=None, journal_rc=0):
        self.journal_out, self.sessions, self.journal_rc = journal_out, sessions or [], journal_rc
        self.calls = []
        self.old_loginctl = False

    def run(self, argv, timeout=60):
        self.calls.append(argv)
        if argv[0] == "journalctl":
            return subprocess.CompletedProcess(argv, self.journal_rc, self.journal_out, "")
        if argv[0] == "loginctl" and self.old_loginctl:
            if "--json=short" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "loginctl: unrecognized option '--json=short'")
            return subprocess.CompletedProcess(argv, 0, "".join(f"{s['session']} {s['uid']} {s['user']} seat0 tty2\n"
                                                                for s in self.sessions), "")
        if argv[0] == "loginctl":
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.sessions), "")
        return subprocess.CompletedProcess(argv, 127, "", "")


with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    state, ll2 = t / "state.json", t / "lastlog2.db"
    person = lambda u: u in PEOPLE  # noqa: E731

    # The first report: a week of the journal; who's signed in now.
    box = Box(journal(("New session 3 of user amina.", NOW - 7200), ("New session 4 of user amina.", NOW - 3600),
                      ("New session c1 of user gdm.", NOW - 100), ("New session 5 of user root.", NOW - 50),
                      ("Removed session 4.", NOW - 10), ("New session 9 of user ghost.", NOW - 20), ("junk", "x")),
              sessions=[{"session": "7", "uid": 1001, "user": "omar", "class": "user"},
                        {"session": "c1", "uid": 120, "user": "gdm", "class": "greeter"}])
    got = si.collect(box.run, state, ll2, person, NOW)
    by = {g["username"]: g for g in got}
    check("journal sign-ins, latest kept", by.get("amina", {}).get("last") == NOW - 3600 and by["amina"]["now"] is False, got)
    check("signed in now", by.get("omar", {}).get("now") is True and by["omar"]["last"] == NOW, got)
    check("only people's accounts (not gdm, root, or one that's gone)", set(by) == {"amina", "omar"}, got)
    since = [c for c in box.calls if c[0] == "journalctl"][0]
    check("first look: the last week", f"@{NOW - si.FIRST_LOOK_S - 60}" in since, since)
    check("the record is root only", oct(state.stat().st_mode & 0o777) == "0o600")

    # The next report: the journal from where the last one stopped; old sign-ins kept.
    box = Box(journal(("New session 11 of user mo.", NOW + 1800)))
    got = si.collect(box.run, state, ll2, person, NOW + 3600)
    by = {g["username"]: g for g in got}
    since = [c for c in box.calls if c[0] == "journalctl"][0]
    check("reads on from the last report", f"@{NOW - 60}" in since, since)
    check("a sign-in between two reports isn't missed", by.get("mo", {}).get("last") == NOW + 1800, got)
    check("earlier sign-ins kept, nobody signed in now", by.get("amina", {}).get("last") == NOW - 3600 and
          not any(g["now"] for g in got), got)
    check("latest first", [g["username"] for g in got] == ["mo", "omar", "amina"], got)

    # lastlog2, if installed; never an earlier time over a later one.
    with sqlite3.connect(ll2) as db:
        db.execute("create table Lastlog2 (Name text, Time integer, TTY text, RemoteHost text, Service text)")
        db.executemany("insert into Lastlog2 values (?, ?, '', '', 'gdm-password')",
                       [("amina", NOW + 5000), ("omar", NOW - 99999), ("mo", 0)])
    got = si.collect(Box().run, state, ll2, person, NOW + 7200)
    by = {g["username"]: g for g in got}
    check("lastlog2 read", by["amina"]["last"] == NOW + 5000, got)
    check("never an older time over a newer one", by["omar"]["last"] == NOW and by["mo"]["last"] == NOW + 1800, got)

    # Nothing breaks without journald, loginctl or a readable record.
    state.write_text("not json")
    got = si.collect(Box(journal_rc=1).run, state, t / "none.db", person, NOW)
    check("no journal, no sessions, a broken record: an empty list", got == [], got)

    # An account that's been removed drops out.
    si.collect(Box(sessions=[{"user": "amina", "uid": 1000, "class": "user"}]).run, state, ll2, person, NOW)
    got = si.collect(Box().run, state, ll2, lambda u: u == "omar", NOW)
    check("removed accounts drop out", [g["username"] for g in got] == ["omar"], got)

    # An older loginctl without --json.
    box = Box(sessions=[{"session": "2", "uid": 1001, "user": "omar"}, {"session": "c1", "uid": 120, "user": "gdm"}])
    box.old_loginctl = True
    check("older loginctl: still finds who's signed in", si.signed_in_now(box.run) == {"omar"})

    # Leaving the organisation.
    si.forget(state)
    check("leaving forgets the record", not state.exists())

if failures:
    sys.exit(1)
print("signins: every case passed")
