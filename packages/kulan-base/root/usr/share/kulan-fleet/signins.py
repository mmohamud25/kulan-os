"""Who uses this computer, for its organisation (Kulan Fleet 2.8c): each
account's username, when it last signed in, and whether it's signed in now.
Nothing else: not what they did, not from where.

Sign-ins come from systemd-logind's journal ("New session 7 of user amina."),
read from where the last report stopped, and from the sessions open now
(loginctl); lastlog2's database is read too if it's installed. What's found
is kept in /var/lib/kulan/fleet-sign-ins.json (root only), so a sign-in
between two hourly reports isn't missed. Only accounts with a uid of 1000 or
more that still exist are reported (people's own accounts, and the
70000s Kulan Fleet gives its people; not systemd's dynamic users).
"""
from __future__ import annotations

import json
import os
import pwd
import re
import sqlite3
import time
from pathlib import Path

STATE = Path("/var/lib/kulan/fleet-sign-ins.json")
LASTLOG2 = Path("/var/lib/lastlog/lastlog2.db")
NEW_SESSION = re.compile(r"^New session \S+ of user ([a-z_][a-z0-9_-]{0,31})\.$")
USERNAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
FIRST_LOOK_S = 7 * 86400  # the first time: the last week of the journal
MAX = 200


def _person(name: str) -> bool:
    """A person's account: uid 1000 and up, including the 70000s Kulan Fleet
    gives its people, but not systemd's dynamic users (60000-65535, nobody)."""
    try:
        uid = pwd.getpwnam(name).pw_uid
    except KeyError:
        return False
    return 1000 <= uid < 60000 or 65536 <= uid < 4294967294


def from_journal(run, since: float) -> dict[str, int]:
    r = run(["journalctl", "-q", "-u", "systemd-logind.service", "--since", f"@{int(since)}", "-o", "json",
             "--output-fields=MESSAGE"], timeout=60)
    found: dict[str, int] = {}
    if r.returncode != 0:
        return found
    for line in r.stdout.splitlines():
        try:
            entry = json.loads(line)
            m = NEW_SESSION.match(entry.get("MESSAGE") or "")
            at = int(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000)
        except (ValueError, KeyError, TypeError):
            continue
        if m:
            found[m[1]] = max(found.get(m[1], 0), at)
    return found


def signed_in_now(run) -> set[str]:
    r = run(["loginctl", "list-sessions", "--json=short"])
    now: set[str] = set()
    if r.returncode != 0:  # an older loginctl: "SESSION UID USER ..." without the headings
        r = run(["loginctl", "list-sessions", "--no-legend"])
        for line in r.stdout.splitlines() if r.returncode == 0 else []:
            parts = line.split()
            if len(parts) >= 3 and parts[1].isdigit() and int(parts[1]) >= 1000:
                now.add(parts[2])
        return now
    try:
        sessions = json.loads(r.stdout or "[]")
    except ValueError:
        return now
    for s in sessions if isinstance(sessions, list) else []:
        if isinstance(s, dict) and s.get("class") in ("user", None) and isinstance(s.get("user"), str) \
                and int(s.get("uid") or 0) >= 1000:
            now.add(s["user"])
    return now


def from_lastlog2(path: Path = LASTLOG2) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
            return {name: int(t) for name, t in db.execute("select Name, Time from Lastlog2") if isinstance(name, str) and t}
    except (sqlite3.Error, ValueError, TypeError):
        return {}


def collect(run, state_path: Path = STATE, lastlog2: Path = LASTLOG2, is_person=_person,
            now: float | None = None) -> list[dict]:
    """[{username, last, now}] for the report; the record is kept up to date."""
    now = time.time() if now is None else now
    try:
        state = json.loads(state_path.read_text())
        if not isinstance(state, dict):
            raise ValueError
    except (OSError, ValueError):
        state = {}
    last: dict[str, int] = {k: v for k, v in (state.get("last") or {}).items()
                            if isinstance(k, str) and isinstance(v, int)}
    since = state.get("read_until") if isinstance(state.get("read_until"), (int, float)) else now - FIRST_LOOK_S
    current = signed_in_now(run)
    for source in (from_journal(run, since - 60), from_lastlog2(lastlog2), {u: int(now) for u in current}):
        for user, at in source.items():
            if at > last.get(user, 0):
                last[user] = at
    last = {u: t for u, t in last.items() if USERNAME.match(u) and is_person(u)}
    keep = dict(sorted(last.items(), key=lambda kv: -kv[1])[:MAX])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + ".kulan-new")
    tmp.write_text(json.dumps({"last": keep, "read_until": int(now)}))
    os.chmod(tmp, 0o600)
    os.replace(tmp, state_path)
    return [{"username": u, "last": t, "now": u in current} for u, t in keep.items()]


def forget(state_path: Path = STATE) -> None:
    """Leaving the organisation: the record goes."""
    state_path.unlink(missing_ok=True)
