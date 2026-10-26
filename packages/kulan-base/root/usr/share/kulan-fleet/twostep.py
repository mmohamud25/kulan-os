"""Two-step sign-in on this computer (Kulan Fleet 2.8d): after the password,
a security key (pam_u2f) or a phone code (pam_oath), for the people Kulan
Fleet manages here, when the organisation's policy asks for it.

Each report's reply gives, per person, "two_step": {"required", "factors":
[{id, kind: key|code, label, data}]}. A key's data is its pam_u2f credential
(a public key); a code's is its secret, which only root can read here:

  /etc/kulan/two-step/u2f_keys     user:credential[:credential...]     (pam_u2f authfile)
  /etc/kulan/two-step/users.oath   HOTP/T30/6 user - secret            (pam_oath usersfile)

PAM (pam-configs/kulan-two-step) skips the second step only for sudo
(your own password, after a sign-in that had it) and what only root runs
(runuser, cron). su and polkit's prompts (another person's password) ask
for that person's second step too, so knowing a password is never enough.
It asks for a second step only from members
of the kulan-two-step group: people who have set one up (asked by the
policy or not: once set up, it's used). Everyone else signs in as before,
and someone the policy asks who hasn't set one up is reminded at each
sign-in (fleet-two-step-reminder) rather than locked out.

A person sets one up here with /usr/lib/kulan/fleet-two-step (their own
password, through polkit): it works here at once, and goes to Kulan Fleet
with the next report (pending until the server has it), and from there to
every computer the person may use.

Commands go through `run` (kulan-fleet's), so the tests can watch them.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

DIR = Path("/etc/kulan/two-step")
STATE = Path("/var/lib/kulan/fleet-two-step.json")          # root only: the server's lists and what's pending
PUBLIC = Path("/var/lib/kulan/fleet-two-step-public.json")  # anyone: who's asked, and labels (no secrets)
GROUP = "kulan-two-step"
ORIGIN = "pam://kulan-fleet"  # the same on every computer, so a key set up on one works on all
U2F = re.compile(r"^[A-Za-z0-9+/=_-]{16,1024},[A-Za-z0-9+/=_-]{16,1024},(es256|eddsa|rs256),[+a-z]{0,40}$")
SECRET = re.compile(r"^[0-9a-f]{40}$")
USERNAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_FACTORS = 10


def _write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.name == DIR.name:
        os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".kulan-new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load(path: Path = STATE) -> dict:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.setdefault("server", {})
            data.setdefault("adds", [])
            data.setdefault("removes", [])
            return data
    except (OSError, ValueError):
        pass
    return {"server": {}, "adds": [], "removes": []}


def save(state: dict, path: Path = STATE) -> None:
    _write(path, json.dumps(state), 0o600)


def clean_factor(f) -> dict | None:
    if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not ID.match(f["id"]):
        return None
    kind, data = f.get("kind"), f.get("data")
    if kind == "key" and isinstance(data, str) and U2F.match(data):
        pass
    elif kind == "code" and isinstance(data, str) and SECRET.match(data):
        pass
    else:
        return None
    label = re.sub(r"[^\w .,'()+-]", "", f.get("label") if isinstance(f.get("label"), str) else "").strip()[:60]
    return {"id": f["id"], "kind": kind, "label": label, "data": data}


def from_accounts(accounts) -> dict[str, dict]:
    """{username: {required, factors}} from a report's reply (removed people left out)."""
    out = {}
    for a in accounts if isinstance(accounts, list) else []:
        if not isinstance(a, dict) or a.get("removed") or not isinstance(a.get("username"), str) \
                or not USERNAME.match(a["username"]):
            continue
        ts = a.get("two_step") if isinstance(a.get("two_step"), dict) else {}
        factors = [c for c in (clean_factor(f) for f in (ts.get("factors") or [])[:MAX_FACTORS]) if c]
        out[a["username"]] = {"required": ts.get("required") is True, "factors": factors,
                              "codes_unavailable": ts.get("codes_unavailable") is True}
    return out


def effective(state: dict) -> dict[str, dict]:
    """The server's lists, with what was set up or removed here and isn't on the server yet."""
    people = {u: {"required": v.get("required") is True, "factors": list(v.get("factors") or [])}
              for u, v in state.get("server", {}).items()}
    gone = {(r["username"], r["id"]) for r in state.get("removes", [])}
    for user, p in people.items():
        p["factors"] = [f for f in p["factors"] if (user, f["id"]) not in gone]
    for add in state.get("adds", []):
        p = people.get(add["username"])
        if p is not None and not any(f["id"] == add["id"] for f in p["factors"]) and (add["username"], add["id"]) not in gone:
            p["factors"].append({k: add[k] for k in ("id", "kind", "label", "data")})
    return people


def oath_text(people: dict[str, dict], current: str) -> str:
    """users.oath, keeping pam_oath's own record (the last code used, so it
    can't be used twice) for secrets that haven't changed."""
    kept = {}
    for line in current.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            kept[(parts[1], parts[3].lower())] = line
    lines = []
    for user in sorted(people):
        for f in people[user]["factors"]:
            if f["kind"] == "code":
                lines.append(kept.get((user, f["data"]), f"HOTP/T30/6\t{user}\t-\t{f['data']}"))
    return "".join(line + "\n" for line in lines)


def write_files(run, people: dict[str, dict], directory: Path = DIR, public: Path = PUBLIC) -> list[str]:
    """The PAM files and group for these people; returns who must use a second step."""
    u2f = directory / "u2f_keys"
    oath = directory / "users.oath"
    keys = {u: [f["data"] for f in p["factors"] if f["kind"] == "key"] for u, p in people.items()}
    _write(u2f, "".join(f"{u}:{':'.join(k)}\n" for u, k in sorted(keys.items()) if k), 0o600)
    try:
        current = oath.read_text()
    except OSError:
        current = ""
    text = oath_text(people, current)
    if text != current or not oath.exists():
        _write(oath, text, 0o600)
    members = sorted(u for u, p in people.items() if p["factors"])
    if run(["getent", "group", GROUP]).returncode != 0:
        run(["groupadd", "--system", GROUP])
    run(["gpasswd", "-M", ",".join(members), GROUP])
    _write(public, json.dumps({u: {"required": p["required"], "ready": bool(p["factors"]),
                                   "factors": [{k: f[k] for k in ("id", "kind", "label")} for f in p["factors"]]}
                               for u, p in people.items()}), 0o644)
    return members


def apply(run, accounts, managed, state_path: Path = STATE, directory: Path = DIR,
          public: Path = PUBLIC) -> dict[str, str]:
    """After a report: the server's lists, for the accounts Fleet made here
    only (never someone's own account with the same name); what the server
    now has is no longer pending."""
    state = load(state_path)
    server = {u: v for u, v in from_accounts(accounts).items() if u in managed}
    # The server couldn't open someone's phone codes this time (its key is
    # missing or changed): keep the ones this computer has, rather than drop
    # them and quietly fall back to the password alone.
    for user, p in server.items():
        if p.pop("codes_unavailable", False):
            have = {f["id"] for f in p["factors"]}
            p["factors"] += [f for f in state["server"].get(user, {}).get("factors", [])
                             if f["kind"] == "code" and f["id"] not in have]
    state["server"] = server
    on_server = {(u, f["id"]) for u, p in server.items() for f in p["factors"]}
    state["adds"] = [a for a in state["adds"] if (a["username"], a["id"]) not in on_server and a["username"] in server]
    state["removes"] = [r for r in state["removes"] if (r["username"], r["id"]) in on_server]
    save(state, state_path)
    people = effective(state)
    write_files(run, people, directory, public)
    return {u: ("on" if p["factors"] else "asked, not set up" if p["required"] else "off") for u, p in people.items()}


def pending(state_path: Path = STATE) -> tuple[list[dict], list[dict]]:
    """For the report: second steps set up and removed here, not yet on the server."""
    state = load(state_path)
    return ([{k: a[k] for k in ("username", "id", "kind", "label", "data")} for a in state["adds"]],
            [{"username": r["username"], "id": r["id"]} for r in state["removes"]])


def add(run, user: str, kind: str, label: str, data: str, state_path: Path = STATE, directory: Path = DIR,
        public: Path = PUBLIC) -> str:
    """Set one up here (fleet-two-step, for the person themselves). Returns its id."""
    state = load(state_path)
    if user not in state["server"]:
        raise ValueError("Only accounts your organisation manages can use this")
    f = clean_factor({"id": str(uuid.uuid4()), "kind": kind, "label": label, "data": data})
    if f is None:
        raise ValueError("That security key or phone code can't be used")
    if len(effective(state)[user]["factors"]) >= MAX_FACTORS:
        raise ValueError(f"You can have up to {MAX_FACTORS}. Remove one first")
    state["adds"].append({"username": user, **f})
    save(state, state_path)
    write_files(run, effective(state), directory, public)
    return f["id"]


def remove(run, user: str, factor_id: str, state_path: Path = STATE, directory: Path = DIR,
           public: Path = PUBLIC) -> None:
    state = load(state_path)
    if not any(f["id"] == factor_id for f in effective(state).get(user, {}).get("factors", [])):
        raise ValueError("That second step wasn't found")
    state["adds"] = [a for a in state["adds"] if not (a["username"] == user and a["id"] == factor_id)]
    if any(f["id"] == factor_id for f in state["server"].get(user, {}).get("factors", [])):
        state["removes"].append({"username": user, "id": factor_id})
    save(state, state_path)
    write_files(run, effective(state), directory, public)


def remove_all(run, state_path: Path = STATE, directory: Path = DIR, public: Path = PUBLIC) -> None:
    """Leaving the organisation: passwords alone again."""
    if run(["getent", "group", GROUP]).returncode == 0:
        run(["gpasswd", "-M", "", GROUP])
    for path in (directory / "u2f_keys", directory / "users.oath", state_path, public):
        path.unlink(missing_ok=True)
