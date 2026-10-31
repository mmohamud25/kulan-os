"""Work email on this computer (Kulan Fleet 2.8e): the mailbox the
organisation made for each person it manages here, and whether they've
connected it.

Each report's reply gives, per person with a working mailbox,
"mail": {address, provider}. Provider "google" (Google Workspace) is
connected with GNOME Online Accounts (mail in Geary, Kulan's mail app and
the default for email, calendar, contacts, Google Drive in Files).
Provider "microsoft" (Microsoft 365, 2.12) opens in Outlook on the web,
signed in as that address: GNOME's Microsoft 365 account gives apps only the
address, not a way to read the mail, so Geary can't use it. It counts as
connected once the person has opened it from here (work-email-opened). A
later provider only adds a branch here.

  /var/lib/kulan/fleet-mail/<username>   {address, provider}: each person's own,
                                         readable only by them (kept by kulan-fleet)

Run as the person themselves (fleet-work-email at sign-in, Kulan Settings):
mine(), connected() and ask_today() read their own files only.
"""
from __future__ import annotations

import configparser
import json
import os
import pwd
import re
import shutil
import time
import urllib.parse
from pathlib import Path

PUBLIC = Path("/var/lib/kulan/fleet-mail")
PROVIDERS = ("google", "microsoft")
OUTLOOK = "https://outlook.office.com/mail/"
ADDRESS = re.compile(r"^[a-z0-9](?:[a-z0-9._'-]{0,62}[a-z0-9])?@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
USERNAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def from_accounts(accounts, managed) -> dict[str, dict]:
    """{username: {address, provider}} for the accounts Fleet made here (never
    someone's own account with the same name), well-formed only."""
    out = {}
    for a in accounts if isinstance(accounts, list) else []:
        if not isinstance(a, dict) or a.get("removed") or not a.get("enabled", True):
            continue
        user, mail = a.get("username"), a.get("mail")
        if not isinstance(user, str) or not USERNAME.match(user) or user not in managed or not isinstance(mail, dict):
            continue
        address, provider = mail.get("address"), mail.get("provider")
        if isinstance(address, str) and ADDRESS.match(address) and provider in PROVIDERS:
            out[user] = {"address": address, "provider": provider}
    return out


def _write_own(path: Path, text: str, uid: int, gid: int) -> None:
    """A file only its person can read (root writes it; never through a link)."""
    tmp = path.with_name("." + path.name + ".kulan-new")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chown(tmp, uid, gid)
    os.replace(tmp, path)


def apply(accounts, managed, public: Path | None = None, owner=None) -> dict[str, dict]:
    """After a report: each person with a work mailbox here gets their own
    file; nobody else's are kept. owner(username) -> (uid, gid) or None (the
    tests' stand-in for the password database)."""
    public = public or PUBLIC
    owner = owner or _owner
    mail = from_accounts(accounts, managed)
    public.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(public, 0o755)
    written = set()
    for user, entry in mail.items():
        ids = owner(user)
        if ids is None:  # no account here yet: next report
            continue
        text = json.dumps(entry, sort_keys=True)
        path = public / user
        try:
            same = path.read_text() == text and path.stat().st_uid == ids[0]
        except OSError:
            same = False
        if not same:
            _write_own(path, text, *ids)
        written.add(user)
    for f in public.iterdir():
        if f.name not in written:
            f.unlink(missing_ok=True)
    return {u: mail[u] for u in written}


def _owner(user: str):
    try:
        p = pwd.getpwnam(user)
        return p.pw_uid, p.pw_gid
    except KeyError:
        return None


def remove_all(public: Path | None = None) -> None:
    """Leaving the organisation: no work email offered (people's own Online Accounts stay theirs)."""
    public = public or PUBLIC
    if public.is_dir():
        for f in public.iterdir():
            f.unlink(missing_ok=True)
        public.rmdir()


def mine(username: str, public: Path | None = None) -> dict | None:
    try:
        entry = json.loads(((public or PUBLIC) / username).read_text())
    except (OSError, ValueError):
        return None
    if isinstance(entry, dict) and isinstance(entry.get("address"), str) and ADDRESS.match(entry["address"]) and \
            entry.get("provider") in PROVIDERS:
        return entry
    return None


def _opened_note(home: Path) -> Path:
    return home / ".local" / "state" / "kulan" / "work-email-opened"


def connected(mail: dict, home: Path) -> bool:
    """Whether this person has connected that mailbox: a GNOME Online Accounts Google account for it, or
    (Microsoft 365) opened it in Outlook on the web from here."""
    if mail.get("provider") == "microsoft":
        try:
            return mail.get("address", "").lower() in _opened_note(home).read_text().split()
        except OSError:
            return False
    conf = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        conf.read(home / ".config" / "goa-1.0" / "accounts.conf", encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return False
    want = mail.get("address", "").lower()
    for section in conf.sections():
        s = conf[section]
        if section.startswith("Account ") and s.get("Provider") == mail.get("provider") and \
                want in (s.get("Identity", "").lower(), s.get("PresentationIdentity", "").lower()):
            return True
    return False


def outlook_url(mail: dict) -> str:
    """Outlook on the web, with the work address filled in at Microsoft's sign-in."""
    return OUTLOOK + "?" + urllib.parse.urlencode({"login_hint": mail.get("address", "")})


def connect_command(mail: dict) -> list[str]:
    """What opens the place to connect it: Settings › Online Accounts › Google;
    for Microsoft 365, the mailbox itself in Outlook on the web (see connect())."""
    if mail.get("provider") == "microsoft":
        return ["gio", "open", outlook_url(mail)]
    return ["gnome-control-center", "online-accounts", "add", "google"]


def open_command(mail: dict) -> list[str]:
    """What opens the mailbox once it's connected: the mail app (Geary, Kulan's
    default for email), which reads the Online Accounts account by itself.
    Without it, the account in Online Accounts. Microsoft 365: Outlook on the web."""
    if mail.get("provider") == "microsoft":
        return ["gio", "open", outlook_url(mail)]
    if shutil.which("geary"):
        return ["geary"]
    return ["gnome-control-center", "online-accounts"]


def connect(mail: dict, home: Path, popen=None) -> bool:
    """Connect: opens connect_command() (as the person); for Microsoft 365, notes
    that it was opened, which is all connecting it takes. popen: the tests' stand-in."""
    import subprocess
    popen = popen or (lambda argv: subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                    start_new_session=True))
    try:
        popen(connect_command(mail))
    except OSError:
        return False
    if mail.get("provider") == "microsoft":
        note = _opened_note(home)
        try:
            have = note.read_text().split() if note.exists() else []
            if mail["address"].lower() not in have:
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text("\n".join([*have, mail["address"].lower()]) + "\n")
        except OSError:
            pass
    return True


def ask_today(username: str, home: Path, public: Path | None = None, now: float | None = None) -> dict | None:
    """At sign-in: this person's work mailbox if it isn't connected yet and
    they haven't been asked today (noted, so it's once a day); else None."""
    mail = mine(username, public)
    if not mail or connected(mail, home):
        return None
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    note = home / ".local" / "state" / "kulan" / "work-email-asked"
    try:
        if note.read_text().split() == [day, mail["address"]]:
            return None
    except OSError:
        pass
    try:
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"{day} {mail['address']}\n")
    except OSError:
        pass
    return mail
