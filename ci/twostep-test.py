#!/usr/bin/python3
"""Kulan Fleet 2.8d on the computer (twostep.py), every case, without
touching this machine: made-up file locations and a pretend `run` for the
group commands.

  python3 -I -B ci/twostep-test.py [path/to/twostep.py]
Prints "twostep: every case passed", or ::error:: lines and exits 1.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "packages/kulan-base/root/usr/share/kulan-fleet/twostep.py"
spec = importlib.util.spec_from_file_location("twostep", SRC)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
        print(f"::error::twostep: {name} {detail}")


class Box:
    def __init__(self):
        self.group = None  # None: no group yet
        self.calls = []

    def run(self, argv, timeout=60):
        self.calls.append(argv)
        if argv[:2] == ["getent", "group"]:
            return subprocess.CompletedProcess(argv, 0 if self.group is not None else 2, "", "")
        if argv[0] == "groupadd":
            self.group = []
        if argv[:2] == ["gpasswd", "-M"]:
            self.group = [m for m in argv[2].split(",") if m]
        return subprocess.CompletedProcess(argv, 0, "", "")


KEY = "kh0123456789abcdefKH,pk0123456789abcdefPK+/=,es256,+presence"
KEY2 = "kh9999999999abcdefKH,pk9999999999abcdefPK+/=,eddsa,+presence+pin"
SECRET = "3132333435363738393031323334353637383930"
ID = lambda n: f"00000000-0000-4000-8000-00000000000{n}"  # noqa: E731


def acct(user, required, *factors, removed=False):
    return {"username": user, "removed": removed, "two_step": {"required": required, "factors": list(factors)}}


with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    kw = dict(state_path=t / "state.json", directory=t / "two-step", public=t / "public.json")
    box = Box()

    # The server's lists: amina has a key and a code; omar is asked but hasn't set one up;
    # "local" has the same name as someone's own account here, so it's never touched.
    accounts = [acct("amina", True, {"id": ID(1), "kind": "key", "label": "YubiKey", "data": KEY},
                     {"id": ID(2), "kind": "code", "label": "Phone", "data": SECRET}),
                acct("omar", True), acct("mo", False),
                acct("local", True, {"id": ID(3), "kind": "key", "label": "x", "data": KEY2}),
                acct("gone", True, removed=True),
                acct("bad", True, {"id": ID(4), "kind": "key", "label": "x", "data": "nope"},
                     {"id": "x", "kind": "code", "label": "x", "data": SECRET})]
    status = ts.apply(box.run, accounts, {"amina", "omar", "mo", "bad"}, **kw)
    check("status per person", status == {"amina": "on", "omar": "asked, not set up", "mo": "off", "bad": "asked, not set up"}, status)
    u2f = (t / "two-step" / "u2f_keys").read_text()
    oath = (t / "two-step" / "users.oath").read_text()
    check("pam_u2f's file: amina's key, nobody else's", u2f == f"amina:{KEY}\n", u2f)
    check("pam_oath's file: amina's code", oath == f"HOTP/T30/6\tamina\t-\t{SECRET}\n", oath)
    check("root only", oct((t / "two-step" / "u2f_keys").stat().st_mode & 0o777) == "0o600" and
          oct((t / "two-step").stat().st_mode & 0o777) == "0o700" and oct((t / "state.json").stat().st_mode & 0o777) == "0o600")
    check("the group made, with only who has set one up", ["groupadd", "--system", "kulan-two-step"] in box.calls and
          box.group == ["amina"], box.group)
    public = json.loads((t / "public.json").read_text())
    check("anyone can see who's asked, never secrets", public["omar"] == {"required": True, "ready": False, "factors": []} and
          public["amina"]["factors"][0] == {"id": ID(1), "kind": "key", "label": "YubiKey"} and SECRET not in json.dumps(public) and
          KEY not in json.dumps(public), public)

    # pam_oath's own record (the last code used) is kept across reports.
    used = f"HOTP/T30/6\tamina\t-\t{SECRET}\t123456\t2026-10-25T10:00:00L\n"
    (t / "two-step" / "users.oath").write_text(used)
    ts.apply(box.run, accounts, {"amina", "omar", "mo", "bad"}, **kw)
    check("the last code used stays recorded", (t / "two-step" / "users.oath").read_text() == used)

    # omar sets up a phone code here: it works at once, and goes with the next report.
    new = ts.add(box.run, "omar", "code", "Pixel", SECRET.replace("31", "41"), **kw)
    check("set up here: in the files at once", "omar" in (t / "two-step" / "users.oath").read_text() and box.group == ["amina", "omar"], box.group)
    adds, removes = ts.pending(kw["state_path"])
    check("pending for the report", [a["id"] for a in adds] == [new] and adds[0]["username"] == "omar" and removes == [], adds)
    for bad in (("omar", "key", "x", "not a key"), ("local", "code", "x", SECRET), ("nobody", "key", "x", KEY)):
        try:
            ts.add(box.run, *bad, **kw)
            check(f"refused {bad[:2]}", False)
        except ValueError:
            pass
    # Not on the server yet: a report that doesn't have it keeps it pending, and in the files.
    ts.apply(box.run, accounts, {"amina", "omar", "mo", "bad"}, **kw)
    check("not on the server yet: still pending and working", ts.pending(kw["state_path"])[0] and box.group == ["amina", "omar"], box.group)
    # The server has it now: no longer pending.
    accounts[1] = acct("omar", True, {"id": new, "kind": "code", "label": "Pixel", "data": SECRET.replace("31", "41")})
    ts.apply(box.run, accounts, {"amina", "omar", "mo", "bad"}, **kw)
    check("on the server: no longer pending", ts.pending(kw["state_path"]) == ([], []))

    # amina removes her key here: gone at once, pending for the server, then done.
    ts.remove(box.run, "amina", ID(1), **kw)
    check("removed here at once", (t / "two-step" / "u2f_keys").read_text() == "" and ts.pending(kw["state_path"])[1] ==
          [{"username": "amina", "id": ID(1)}])
    try:
        ts.remove(box.run, "amina", ID(9), **kw)
        check("removing something that isn't there is refused", False)
    except ValueError:
        pass
    accounts[0] = acct("amina", True, {"id": ID(2), "kind": "code", "label": "Phone", "data": SECRET})
    ts.apply(box.run, accounts, {"amina", "omar", "mo", "bad"}, **kw)
    check("the server dropped it: nothing pending", ts.pending(kw["state_path"]) == ([], []))

    # The server couldn't open amina's phone code this time: this computer keeps the one it has.
    flagged = [dict(accounts[0]), *accounts[1:]]
    flagged[0] = {**accounts[0], "two_step": {"required": True, "factors": [], "codes_unavailable": True}}
    ts.apply(box.run, flagged, {"amina", "omar", "mo", "bad"}, **kw)
    check("codes the server couldn't open are kept, not dropped", "amina" in (t / "two-step" / "users.oath").read_text()
          and "amina" in box.group, box.group)

    # An admin reset (the server sends none): out of the group, signs in with the password alone.
    accounts[0] = acct("amina", True)
    status = ts.apply(box.run, accounts, {"amina", "omar", "mo", "bad"}, **kw)
    check("reset: back to the password, and asked again", status["amina"] == "asked, not set up" and box.group == ["omar"], box.group)

    # Leaving the organisation.
    ts.remove_all(box.run, **kw)
    check("leaving: nobody asked, files gone", box.group == [] and not (t / "two-step" / "u2f_keys").exists() and
          not (t / "public.json").exists() and not (t / "state.json").exists())

if failures:
    sys.exit(1)
print("twostep: every case passed")
