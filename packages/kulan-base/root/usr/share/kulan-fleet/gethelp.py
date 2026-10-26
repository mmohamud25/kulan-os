"""Get help (Kulan Fleet 2.9c), the computer's side.

Someone signed in writes what's wrong in Kulan Settings › Get help, and may
add a screenshot they saw first. Kulan Settings hands it to
/usr/lib/kulan/fleet-get-help (pkexec: only the person at the computer, no
password), which runs "kulan-fleet get-help USER": this checks it and sends
it to the organisation with the computer's own key.

Each report's reply lists the computer's help requests of the last 30 days.
They're kept in a root-only file (fleet-help-state.json); each person's own
are copied to /var/lib/kulan/fleet-help/USER.json (theirs alone, for Kulan
Settings to show; root never reads those back). When an admin marks one done,
its answer is shown on that person's screen once. Other people on the
computer can tell that someone has a file there (the folder can't be listed,
but a name can be tried), never what's in it.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import pwd
import re
import time
from pathlib import Path

STATE = Path("/var/lib/kulan/fleet-help-state.json")  # every person's requests (root only)
LOCK = Path("/run/kulan/fleet-help.lock")
DIR = Path("/var/lib/kulan/fleet-help")              # USER.json, each readable by that person only (the folder can't be listed)
SENT = Path("/var/lib/kulan/fleet-help-sent.json")   # when requests were sent here (root only)
SEEN = Path("/var/lib/kulan/fleet-help-seen.json")   # answers already shown (root only)
TEXT_MAX = 2000
REPLY_MAX = 500
SCREENSHOT_MAX = 368640  # bytes, as the server allows
PER_HOUR = 5        # from this computer
PER_PERSON_HOUR = 3  # from one person on it, so nobody uses up everyone's
USERNAME = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
REQUEST_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _clean_text(value, limit: int, lines: bool = True) -> str:
    if not isinstance(value, str):
        return ""
    text = CONTROL.sub(" ", value.replace("\r\n", "\n"))
    if not lines:
        text = text.replace("\n", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()[:limit]


def image_type(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def person(username: str) -> pwd.struct_passwd | None:
    """A person's own account (uid 1000 and up, not systemd's dynamic users)."""
    if not USERNAME.match(username or ""):
        return None
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return None
    return entry if (1000 <= entry.pw_uid < 60000 or 65536 <= entry.pw_uid < 4294967294) else None


def clean_request(raw) -> dict:
    """What Kulan Settings sent: {text, screenshot?} -> {text, screenshot (base64) or None}.
    ValueError says what's wrong, in words for the person."""
    if not isinstance(raw, dict):
        raise ValueError("Something went wrong. Try again.")
    text = _clean_text(raw.get("text"), TEXT_MAX)
    if not text:
        raise ValueError("Write what's wrong first.")
    shot = raw.get("screenshot")
    if shot in (None, ""):
        return {"text": text, "screenshot": None}
    if not isinstance(shot, str) or len(shot) > (SCREENSHOT_MAX + 2) // 3 * 4 + 4:
        raise ValueError("The screenshot is too big.")
    try:
        data = base64.b64decode(shot, validate=True)
    except (ValueError, TypeError):
        raise ValueError("The screenshot couldn't be read.") from None
    if len(data) > SCREENSHOT_MAX:
        raise ValueError("The screenshot is too big.")
    if not image_type(data):
        raise ValueError("The screenshot couldn't be read.")
    return {"text": text, "screenshot": base64.b64encode(data).decode()}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, RecursionError):
        return default


@contextlib.contextmanager
def locked():
    """One change to these files at a time (a report and a request sent at
    once). Separate from kulan-fleet's own lock, which a report holds long."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def _write(path: Path, data, mode: int, owner: tuple[int, int] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".kulan-new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, mode)
    if owner:
        os.chown(tmp, *owner)
    os.replace(tmp, path)


def _recent(now: float) -> list[list]:
    """[time, username] of requests sent from here in the last hour."""
    return [x for x in _read(SENT, []) if isinstance(x, list) and len(x) == 2 and isinstance(x[0], (int, float))
            and now - 3600 < x[0] <= now]


def may_send(username: str, now: float | None = None) -> bool:
    """At most 5 an hour from this computer, 3 from one person (the server
    says the same, but the person is told here, before anything is sent)."""
    now = time.time() if now is None else now
    recent = _recent(now)
    return len(recent) < PER_HOUR and sum(1 for _t, u in recent if u == username) < PER_PERSON_HOUR


def note_sent(username: str, request_id: str, text: str, now: float | None = None) -> None:
    """Remember it was sent, and show it to its person straight away (as open)."""
    now = time.time() if now is None else now
    with locked():
        _write(SENT, _recent(now) + [[now, username]], 0o600)
        if not SEEN.exists():
            _write(SEEN, [], 0o600)  # so an answer to this one is shown, even if it comes before the next report
        entry = person(username)
        if not entry or not REQUEST_ID.match(request_id):
            return
        state = _read(STATE, {})
        state = state if isinstance(state, dict) else {}
        requests = [r for r in state.get(username, []) if isinstance(r, dict) and r.get("id") != request_id]
        requests.insert(0, {"id": request_id, "text": _clean_text(text, 200), "state": "open", "reply": None,
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "done_at": None})
        state[username] = requests[:50]
        _write(STATE, state, 0o600)
        _save_person(username, entry, state[username])


def _save_person(username: str, entry: pwd.struct_passwd, requests: list[dict]) -> None:
    DIR.mkdir(mode=0o711, parents=True, exist_ok=True)
    os.chmod(DIR, 0o711)
    _write(DIR / f"{username}.json", {"requests": requests}, 0o600, (entry.pw_uid, entry.pw_gid))


def apply(raw) -> list[tuple[str, str, str, str]]:
    """The help requests from a report's reply: each person's own, kept for
    Kulan Settings. Returns the answers still to show: [(username, request id,
    title, body)]; mark_seen() once one is on their screen, so it's shown once
    (and, if they weren't signed in, at a later report when they are)."""
    if not isinstance(raw, list):
        return []
    with locked():
        return _apply(raw)


def _apply(raw: list) -> list[tuple[str, str, str, str]]:
    by_person: dict[str, list[dict]] = {}
    for r in raw[:50]:
        if not isinstance(r, dict) or not REQUEST_ID.match(str(r.get("id", ""))):
            continue
        username = str(r.get("username", ""))
        if not person(username):
            continue
        state = "done" if r.get("state") == "done" else "open"
        by_person.setdefault(username, []).append({
            "id": r["id"], "text": _clean_text(r.get("text"), 200), "state": state,
            "reply": _clean_text(r.get("reply"), REPLY_MAX) or None,
            "created_at": r.get("created_at") if isinstance(r.get("created_at"), str) else None,
            "done_at": r.get("done_at") if isinstance(r.get("done_at"), str) else None})
    # A request sent in the last minutes may not be in this reply yet (the report
    # was already on its way): kept as it is until the next one.
    kept = _read(STATE, {})
    kept = kept if isinstance(kept, dict) else {}
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    for username, requests in kept.items():
        if not isinstance(requests, list) or not person(username):
            continue
        listed = {r["id"] for r in by_person.get(username, [])}
        fresh = [r for r in requests if isinstance(r, dict) and r.get("id") not in listed
                 and r.get("state") == "open" and str(r.get("created_at") or "") >= cutoff]
        if fresh:
            by_person[username] = fresh + by_person.get(username, [])
    _write(STATE, by_person, 0o600)
    # Files of people with no requests left are removed (their last one is over 30 days old).
    if DIR.is_dir():
        for path in DIR.glob("*.json"):
            if path.stem not in by_person:
                path.unlink(missing_ok=True)
    first = not SEEN.exists()
    seen = set(x for x in _read(SEEN, []) if isinstance(x, str))
    current = {r["id"] for rs in by_person.values() for r in rs}
    done = {r["id"] for rs in by_person.values() for r in rs if r["state"] == "done"}
    # The first time here (just joined, or just updated): nothing old pops up.
    _write(SEEN, sorted((seen | done) if first else (seen & current)), 0o600)
    show = []
    for username, requests in by_person.items():
        _save_person(username, person(username), requests)
        for r in requests:
            if r["state"] == "done" and not first and r["id"] not in seen:
                show.append((username, r["id"], "Your help request is done",
                             f"Your organisation answered: {r['reply']}" if r["reply"] else
                             "Your organisation marked it as done. Ask again in Settings if you still need help."))
    return show


def mark_seen(ids: list[str]) -> None:
    """These answers are on their person's screen now."""
    if ids:
        with locked():
            seen = set(x for x in _read(SEEN, []) if isinstance(x, str))
            _write(SEEN, sorted(seen | set(ids)), 0o600)


def remove_all() -> None:
    """Leaving the organisation: nothing of it stays."""
    with locked():
        if DIR.is_dir():
            for path in DIR.glob("*"):
                path.unlink(missing_ok=True)
            try:
                DIR.rmdir()
            except OSError:
                pass
        for path in (SENT, SEEN, STATE):
            path.unlink(missing_ok=True)
