#!/usr/bin/python3
"""Every case of People on a computer (/usr/share/kulan-fleet/people.py), with
real accounts: create, conflicts and bad input refused, the setup code,
passwords both ways, promote, rename, disable, re-enable, remove with and
without files. Run as root in a throwaway system (CI's container).

    ci/test-fleet-people.py [MODULE_DIR]   (default /usr/share/kulan-fleet)
"""
import subprocess, sys, time, os, pwd, grp, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "/usr/share/kulan-fleet")
sys.dont_write_bytecode = True
import people as P
log = []
def run(argv, timeout=60):
    log.append(argv[0])
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))
def h(pw): return subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=pw, capture_output=True, text=True, check=True).stdout.strip()
def shadow(u): return [l for l in open("/etc/shadow") if l.startswith(u + ":")][0].split(":")
tmp = Path(tempfile.mkdtemp()); state_path = tmp / "state.json"; left = tmp / "left"
for u in ("zzamina", "zzomar", "zztaken"):
    subprocess.run(["userdel", "-r", u], capture_output=True)
subprocess.run(["useradd", "zztaken"], check=True)  # someone already on this computer
try:
    code = h("K7Q4-9XPA")
    accts = [{"username": "zzamina", "full_name": "Amina Yusuf", "uid": 71901, "role": "employee", "password_hash": code, "must_change": True, "password_changed_at": 100},
             {"username": "zzomar", "full_name": "Omar: Ali", "uid": 71902, "role": "admin", "password_hash": h("omar-pass"), "password_changed_at": 100},
             {"username": "zztaken", "full_name": "X", "uid": 71903},
             {"username": "root", "uid": 71904}, {"username": "bad:name", "uid": 71905}, {"username": "zzbaduid", "uid": 1000},
             {"username": "zzbadhash", "uid": 71906, "password_hash": "x:0:0"}]
    st, status = P.apply(accts, run, state={"people": {}}, left=left)
    assert status["zzamina"] == "created" and status["zzomar"] == "created", status
    assert status["zztaken"].startswith("conflict"), status
    assert "root" not in status and "bad:name" not in status and "zzbaduid" not in status, status
    assert pwd.getpwnam("zzamina").pw_uid == 71901 and pwd.getpwnam("zzomar").pw_gecos == "Omar  Ali"
    assert shadow("zzamina")[1] == code and shadow("zzamina")[2] == "0", "setup code, change at first sign-in"
    assert "zzomar" in grp.getgrnam("sudo").gr_mem and "zzamina" not in grp.getgrnam("sudo").gr_mem
    assert Path("/home/zzamina").is_dir()
    # Nothing to send yet: the setup code isn't a real password.
    assert P.changed_passwords(st) == []
    # Amina signs in and chooses her password (as passwd would).
    new = h("amina-own-pass")
    subprocess.run(["usermod", "-p", new, "zzamina"], check=True)
    subprocess.run(["chage", "-d", time.strftime("%Y-%m-%d"), "zzamina"], check=True)
    ch = P.changed_passwords(st, now=200)
    assert ch == [{"username": "zzamina", "password_hash": new, "changed_at": 200}], ch
    P.sent(st, ch)
    assert P.changed_passwords(st) == []
    # The same report again: nothing changes (her own password isn't overwritten by the old code).
    accts[0]["password_changed_at"] = 100
    st, status = P.apply(accts, run, state=st, left=left)
    assert status["zzamina"] == "up to date" and shadow("zzamina")[1] == new, (status, shadow("zzamina")[1])
    # She changed it on another computer: newer, so it comes here.
    newer = h("changed-elsewhere")
    accts[0].update(password_hash=newer, must_change=False, password_changed_at=300)
    # Promoted, renamed, and Omar disabled.
    accts[0].update(role="admin", full_name="Amina Y. Yusuf")
    accts[1]["enabled"] = False
    st, status = P.apply(accts, run, state=st, left=left)
    assert shadow("zzamina")[1] == newer and "zzamina" in grp.getgrnam("sudo").gr_mem, status
    assert pwd.getpwnam("zzamina").pw_gecos == "Amina Y. Yusuf", status
    assert shadow("zzomar")[1].startswith("!") and shadow("zzomar")[7] == "0", ("locked and expired", shadow("zzomar"))
    assert status["zzomar"] == "updated: disabled", status
    # Re-enabled.
    # A new setup code while he's disabled doesn't unlock him.
    omar_code = h("NEW-CODE-1")
    accts[1].update(password_hash=omar_code, must_change=True)
    st, status = P.apply(accts, run, state=st, left=left)
    assert shadow("zzomar")[1] == "!" + omar_code and status["zzomar"] == "updated: password", (status, shadow("zzomar"))
    accts[1]["enabled"] = True
    st, status = P.apply(accts, run, state=st, left=left)
    assert not shadow("zzomar")[1].startswith("!") and shadow("zzomar")[7] == "", shadow("zzomar")
    assert shadow("zzomar")[1] == omar_code and shadow("zzomar")[2] == "0", "the new code asks for a new password"
    # Amina changes hers here but the server doesn't take it (a reply still with
    # the hash it had): hers stays, by comparing hashes, whatever the clocks say.
    mine_now = h("amina-third")
    subprocess.run(["usermod", "-p", mine_now, "zzamina"], check=True)
    ch = P.changed_passwords(st, now=1)
    assert [c["password_hash"] for c in ch] == [mine_now], ch
    P.sent(st, ch)
    st, status = P.apply(accts, run, state=st, left=left)
    assert shadow("zzamina")[1] == mine_now and status["zzamina"] == "up to date", (status, shadow("zzamina")[1])
    # The server now has a different person called zzomar (another uid): his
    # account here isn't handed to them.
    other = [dict(accts[0]), dict(accts[1], uid=71950)]
    st, status = P.apply(other, run, state=st, left=left)
    assert status["zzomar"].startswith("conflict") and pwd.getpwnam("zzomar").pw_uid == 71902, status
    # Removed: Omar keeping his files, Amina with hers deleted.
    Path("/home/zzomar/report.txt").write_text("handover")
    accts = [{"username": "zzomar", "uid": 71902, "removed": True, "remove_files": False},
             {"username": "zzamina", "uid": 71901, "removed": True, "remove_files": True}]
    st, status = P.apply(accts, run, state=st, left=left)
    kept = list(left.glob("zzomar-*"))
    assert kept and (kept[0] / "report.txt").read_text() == "handover" and oct(left.stat().st_mode)[-3:] == "700", (status, kept)
    assert status["zzamina"] == "removed with their files" and not Path("/home/zzamina").exists(), status
    for u in ("zzamina", "zzomar"):
        try:
            pwd.getpwnam(u); raise AssertionError(f"{u} still there")
        except KeyError:
            pass
    assert st["people"] == {} and pwd.getpwnam("zztaken"), "the computer's own account is left alone"
    # State file is root-only.
    P.save_state(st, state_path)
    assert oct(state_path.stat().st_mode)[-3:] == "600"
    print("people test passed")
finally:
    for u in ("zzamina", "zzomar", "zztaken"):
        subprocess.run(["userdel", "-r", u], capture_output=True)
