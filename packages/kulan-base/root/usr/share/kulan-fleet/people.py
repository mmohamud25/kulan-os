"""People in Kulan Fleet, on this computer: the accounts the organisation
manages (kulan-fleet report runs this as root).

Each report's reply lists the people allowed on this computer:

    {"username": "amina", "full_name": "Amina Yusuf", "uid": 61001,
     "role": "employee" | "admin" | "owner", "enabled": true,
     "password_hash": "$y$...", "must_change": true,
     "password_changed_at": 1792850000}

and, for a while after an admin removes someone, {"username", "uid",
"removed": true, "remove_files": true | false}

and this computer makes its accounts match:
- new person: an account with the organisation's uid (the same on every
  computer), their password hash and, for a setup code, "change at first
  sign-in", so typing the code at the sign-in screen asks for a new password
  at once; admins and the Owner join the sudo group;
- changed: name, role, a password new on the server (chosen on another
  computer, or a new setup code);
- disabled: password locked, account expired, their sessions ended;
- no longer listed: account removed; their files moved to
  /home/.kulan-left/<user>-<date> (only root can open it), or deleted if the
  admin chose that.
It never touches an account Fleet didn't make here: a username already taken
on this computer is left alone and reported as a conflict. Passwords changed
on this computer (a first sign-in, or changing it later) are noticed in
/etc/shadow and sent with the next report, so they follow the person.

Commands go through `run` (kulan-fleet's), so the tests can watch them.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path

STATE = Path("/var/lib/kulan/fleet-people.json")  # root only: who Fleet made here, and their last hash
PASSWD = Path("/etc/passwd")
SHADOW = Path("/etc/shadow")
LEFT = Path("/home/.kulan-left")
UID_MIN, UID_MAX = 61000, 69999
USERNAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# Hashes crypt(3) understands: yescrypt, SHA-512, SHA-256, bcrypt. No ":" or
# newlines, so nothing can spill into /etc/shadow's other fields.
HASH = re.compile(r"^\$(y|gy|6|5|2b|2y|7)\$[./A-Za-z0-9$=,]{8,250}$")
ROLES = ("owner", "admin", "employee")
RESERVED = {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy",
            "www-data", "backup", "list", "irc", "nobody", "admin", "sudo", "kulan", "gdm", "systemd"}
ADMIN_GROUP = "sudo"


def clean(raw) -> list[dict]:
    """Only well-formed accounts, each username and uid once."""
    out, names, uids = [], set(), set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        user, uid = item.get("username"), item.get("uid")
        if not isinstance(user, str) or not USERNAME.match(user) or user in RESERVED or user in names:
            continue
        if not isinstance(uid, int) or isinstance(uid, bool) or not UID_MIN <= uid <= UID_MAX or uid in uids:
            continue
        pw = item.get("password_hash")
        full = item.get("full_name") if isinstance(item.get("full_name"), str) else ""
        full = re.sub(r"[:,=\n\r\t\\]", " ", full).strip()[:80]
        changed = item.get("password_changed_at")
        out.append({
            "username": user, "uid": uid, "full_name": full,
            "role": item.get("role") if item.get("role") in ROLES else "employee",
            "enabled": item.get("enabled") is not False,
            "password_hash": pw if isinstance(pw, str) and HASH.match(pw) else None,
            "must_change": item.get("must_change") is True,
            "password_changed_at": int(changed) if isinstance(changed, (int, float)) and not isinstance(changed, bool) else 0,
            "removed": item.get("removed") is True,
            "remove_files": item.get("remove_files") is True,
        })
        names.add(user)
        uids.add(uid)
    return out


def _table(path: Path, fields: int) -> dict[str, list[str]]:
    rows = {}
    try:
        for line in path.read_text().splitlines():
            parts = line.split(":")
            if len(parts) >= fields:
                rows[parts[0]] = parts
    except OSError:
        pass
    return rows


def local_users(passwd: Path = PASSWD) -> dict[str, int]:
    return {name: int(p[2]) for name, p in _table(passwd, 7).items() if p[2].isdigit()}


def shadow_entry(user: str, shadow: Path = SHADOW) -> tuple[str, str]:
    """(hash, last-change day) from /etc/shadow, with any lock "!" taken off."""
    row = _table(shadow, 3).get(user)
    if not row:
        return "", ""
    return row[1].lstrip("!"), row[2]


def load_state(path: Path = STATE) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) and isinstance(data.get("people"), dict) else {"people": {}}
    except (OSError, ValueError):
        return {"people": {}}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".kulan-new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def _ok(r) -> bool:
    return r.returncode == 0


def _set_role(run, user: str, role: str) -> None:
    if role in ("admin", "owner"):
        run(["usermod", "-aG", ADMIN_GROUP, user])
    else:
        run(["gpasswd", "-d", user, ADMIN_GROUP])  # fails harmlessly if they weren't in it


def _set_hash(run, user: str, pw: str | None, must_change: bool) -> None:
    if pw:
        run(["usermod", "-p", pw, user])
        if must_change:
            run(["chage", "-d", "0", user])  # the setup code: a new password at first sign-in
    else:
        run(["usermod", "-L", user])  # no password yet: can't sign in


def _disable(run, user: str) -> None:
    run(["usermod", "-L", user])
    run(["chage", "-E", "0", user])
    run(["loginctl", "terminate-user", user])


def _enable(run, user: str) -> None:
    run(["usermod", "-U", user])
    run(["chage", "-E", "-1", user])


def _remove(run, user: str, home: str, remove_files: bool, left: Path = LEFT) -> str:
    run(["loginctl", "terminate-user", user])
    if home and Path(home).is_dir() and not remove_files:
        left.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(left, 0o700)
        dest = left / f"{user}-{datetime.date.today().isoformat()}"
        n = 1
        while dest.exists():
            n += 1
            dest = left / f"{user}-{datetime.date.today().isoformat()}-{n}"
        shutil.move(home, dest)
        r = run(["userdel", user])
        return f"removed; files kept in {dest}" if _ok(r) else f"couldn't remove: {r.stderr.strip()[:120]}"
    r = run(["userdel", "-r", user] if remove_files else ["userdel", user])
    return "removed with their files" if remove_files and _ok(r) else (
        "removed" if _ok(r) else f"couldn't remove: {r.stderr.strip()[:120]}")


def apply(raw, run, state: dict | None = None, passwd: Path = PASSWD, shadow: Path = SHADOW,
          left: Path = LEFT) -> tuple[dict, dict]:
    """Make this computer's Fleet accounts match the reply. Returns the new
    state and a status for the report: {username: "created" | ... }."""
    state = state if state is not None else load_state()
    managed: dict = state.setdefault("people", {})
    accounts = clean(raw)
    status: dict[str, str] = {}
    users = local_users(passwd)
    homes = {name: p[5] for name, p in _table(passwd, 7).items()}
    removed = {a["username"]: a for a in accounts if a["removed"]}
    wanted = {a["username"]: a for a in accounts if not a["removed"]}

    for a in wanted.values():
        user, mine = a["username"], managed.get(a["username"])
        if user in users and not mine:
            status[user] = "conflict: this computer already has an account with that name"
            continue
        if mine is not None and mine.get("uid") != a["uid"]:
            status[user] = "conflict: a different person had this username here (their account is kept)"
            continue
        if mine is None and a["uid"] in users.values():
            status[user] = "conflict: this computer already uses that user ID"
            continue
        if user not in users:
            # The system's default shell (zsh on Kulan OS) and /etc/skel.
            args = ["useradd", "--create-home", "--uid", str(a["uid"]), "--user-group", "--comment", a["full_name"], user]
            r = run(args)
            if not _ok(r):
                status[user] = f"couldn't create: {r.stderr.strip()[:120]}"
                continue
            _set_hash(run, user, a["password_hash"], a["must_change"])
            _set_role(run, user, a["role"])
            if not a["enabled"]:
                _disable(run, user)
            managed[user] = {"uid": a["uid"], "role": a["role"], "enabled": a["enabled"],
                             "full_name": a["full_name"], "hash": a["password_hash"] or "",
                             "server_hash": a["password_hash"] or ""}
            status[user] = "created" if a["enabled"] else "created (disabled)"
            continue
        # An account Fleet made here: bring it up to date.
        changes = []
        if a["full_name"] != mine.get("full_name"):
            run(["usermod", "--comment", a["full_name"], user])
            mine["full_name"] = a["full_name"]
            changes.append("name")
        if a["role"] != mine.get("role"):
            _set_role(run, user, a["role"])
            mine["role"] = a["role"]
            changes.append("role")
        # A password the server has newly (a change on another computer, or a
        # new setup code from an admin). Compared with the last one the server
        # sent, not by clocks: a password chosen here and not yet taken by the
        # server is never overwritten by the server's older one.
        local_hash, _day = shadow_entry(user, shadow)
        server_hash = a["password_hash"] or ""
        if server_hash and server_hash != mine.get("server_hash", mine.get("hash")) and server_hash != local_hash:
            _set_hash(run, user, server_hash, a["must_change"])
            if not a["enabled"] and not mine.get("enabled", True):
                run(["usermod", "-L", user])  # a new password doesn't unlock someone disabled
            mine["hash"] = server_hash
            changes.append("password")
        mine["server_hash"] = server_hash
        if a["enabled"] != mine.get("enabled", True):
            (_enable if a["enabled"] else _disable)(run, user)
            mine["enabled"] = a["enabled"]
            changes.append("enabled" if a["enabled"] else "disabled")
        status[user] = "updated: " + ", ".join(changes) if changes else "up to date"

    # Removed by an admin (with their choice about the files), or no longer
    # allowed on this computer (files kept).
    for user in [u for u in managed if u not in wanted]:
        remove_files = removed[user]["remove_files"] if user in removed else False
        status[user] = _remove(run, user, homes.get(user, ""), remove_files, left) if user in users else "removed"
        del managed[user]
    return state, status


def changed_passwords(state: dict, shadow: Path = SHADOW, now: int | None = None) -> list[dict]:
    """Passwords changed on this computer since Fleet last saw them: sent with
    the report so they follow the person to their other computers. A setup
    code that hasn't been replaced yet (must change) isn't sent."""
    out = []
    for user, mine in state.get("people", {}).items():
        local_hash, day = shadow_entry(user, shadow)
        if not local_hash or not HASH.match(local_hash) or local_hash == mine.get("hash") or day == "0":
            continue
        out.append({"username": user, "password_hash": local_hash, "changed_at": now or int(time.time())})
    return out


def sent(state: dict, passwords: list[dict]) -> None:
    """After the server took them: remember them as the current hashes."""
    for p in passwords:
        mine = state.get("people", {}).get(p["username"])
        if mine is not None:
            mine["hash"] = p["password_hash"]
