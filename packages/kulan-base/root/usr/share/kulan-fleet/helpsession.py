"""Remote help (Kulan Fleet 2.9a): the parts that don't need a screen.

kulan-fleet (root) picks a remote help request up: it finds who is signed in
at this computer (pick_session), claims the session from the server with the
computer's own key, and starts the helper, /usr/lib/kulan/kulan-remote-help,
in that person's session with the session's own key (never the computer's).
The helper asks the person, and only if they allow it shares the screen
(GNOME's portal into GStreamer's WebRTC), straight to the admin.

Here: choosing the session, the server calls the helper makes, its ICE
servers in GStreamer's form, and the admin's mouse and keyboard (only when
the person allowed it), checked and turned into the portal's input events.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
USERNAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")  # a session's key, as the server makes it (32 bytes, base64url)
AGENT = "/usr/lib/kulan/kulan-remote-help"
ASK_S = 115          # the person has 2 minutes to answer (the server's limit), a little less here
MAX_S = 3600         # a session lasts an hour at most
TIMEOUT = 15


# --- who is at the computer ------------------------------------------------------------
def pick_session(sessions: list[dict]) -> tuple[dict | None, str]:
    """The desktop session someone is using at this computer now:
    (session, "") or (None, why there's none). Each session is loginctl's
    show-session properties (Class, Type, Active, Remote, LockedHint, Name, User)."""
    desktops = [s for s in sessions if isinstance(s, dict) and s.get("Class") == "user"
                and s.get("Type") in ("wayland", "x11") and s.get("Remote") != "yes"
                and isinstance(s.get("Name"), str) and USERNAME.match(s["Name"])
                and str(s.get("User", "")).isdigit() and int(s["User"]) >= 1000]
    if not desktops:
        return None, "nobody is signed in on the computer"
    active = [s for s in desktops if s.get("Active") == "yes"]
    if not active:
        return None, "nobody is using the computer now (it's at the sign-in screen)"
    s = active[0]
    if s.get("LockedHint") == "yes":
        return None, "the screen is locked: nobody is at the computer now"
    return s, ""


def sessions_now(run) -> list[dict]:
    r = run(["loginctl", "list-sessions", "--no-legend"])
    out = []
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        sid = line.split()[0] if line.split() else ""
        if not re.match(r"^[A-Za-z0-9]{1,32}$", sid):
            continue
        p = run(["loginctl", "show-session", sid, "-p", "Class", "-p", "Type", "-p", "Active", "-p", "Remote",
                 "-p", "LockedHint", "-p", "Name", "-p", "User", "-p", "State"])
        if p.returncode == 0:
            out.append(dict(line.partition("=")[::2] for line in p.stdout.splitlines() if "=" in line))
    return out


# --- the server -----------------------------------------------------------------------
class ServerError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def call(server: str, session_id: str, token: str, body: dict, timeout: float = TIMEOUT) -> dict:
    """One request to /v1/help/<session> with the session's own key."""
    if not UUID.match(session_id):
        raise ServerError(0, "not a session")
    request = urllib.request.Request(f"{server}/v1/help/{session_id}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                                              "User-Agent": "kulan-remote-help/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            text = reply.read(1_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read(10000) or b"{}").get("error") or e.reason
        except ValueError:
            message = e.reason
        raise ServerError(e.code, str(message)) from e
    except (urllib.error.URLError, OSError) as e:
        raise ServerError(0, f"couldn't reach the server: {getattr(e, 'reason', e)}") from e
    try:
        data = json.loads(text or "{}")
    except ValueError as e:
        raise ServerError(0, "the server's answer wasn't JSON") from e
    return data if isinstance(data, dict) else {}


def clean_start(raw) -> dict | None:
    """What kulan-fleet gives the helper (KULAN_REMOTE_HELP): well-formed only."""
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(info, dict):
        return None
    server, sid, token = info.get("server"), info.get("session_id"), info.get("token")
    if not (isinstance(server, str) and urllib.parse.urlsplit(server).scheme in ("https", "http")
            and not re.search(r"[\s\x00-\x1f]", server)
            and isinstance(sid, str) and UUID.match(sid) and isinstance(token, str) and TOKEN.match(token)):
        return None
    ice = info.get("ice") if isinstance(info.get("ice"), list) else []
    if len(json.dumps(ice)) > 8000:
        ice = []
    admin = info.get("admin") if isinstance(info.get("admin"), str) else "An admin"
    org = info.get("organisation") if isinstance(info.get("organisation"), str) else "your organisation"
    return {"server": server, "session_id": sid, "token": token,
            "admin": re.sub(r"[^\w@.+' -]", "", admin)[:120] or "An admin",
            "organisation": re.sub(r"[^\w&.,'() -]", "", org)[:80] or "your organisation",
            "control_asked": info.get("control_asked") is True, "ice": ice[:4]}


# --- ICE servers, as GStreamer's webrtcbin takes them -----------------------------------
ICE_URL = re.compile(r"^(stun|turns?):([a-z0-9.-]+)(?::(\d{1,5}))?(?:\?transport=(udp|tcp))?$", re.I)


def gst_ice(ice) -> tuple[str | None, list[str]]:
    """(stun-server, [turn servers]) for webrtcbin: stun://host:port and
    turn(s)://user:pass@host:port?transport=..., the credentials escaped."""
    stun, turns = None, []
    for server in ice if isinstance(ice, list) else []:
        if not isinstance(server, dict):
            continue
        urls = server.get("urls")
        for url in (urls if isinstance(urls, list) else [urls]):
            m = ICE_URL.match(url) if isinstance(url, str) else None
            if not m:
                continue
            scheme, host, port, transport = m[1].lower(), m[2], m[3], m[4]
            if port == "53":
                continue
            if scheme == "stun":
                stun = stun or f"stun://{host}:{port or 3478}"
                continue
            user, cred = server.get("username"), server.get("credential")
            if not (isinstance(user, str) and isinstance(cred, str)):
                continue
            q = lambda v: urllib.parse.quote(v, safe="")  # noqa: E731
            turns.append(f"{scheme}://{q(user)}:{q(cred)}@{host}:{port or (5349 if scheme == 'turns' else 3478)}"
                         + (f"?transport={transport.lower()}" if transport else ""))
    return stun, turns[:4]


# --- the admin's mouse and keyboard -------------------------------------------------------
BUTTONS = {0: 0x110, 1: 0x112, 2: 0x111}  # the browser's left, middle, right -> BTN_LEFT, BTN_MIDDLE, BTN_RIGHT
# KeyboardEvent.code -> Linux key codes (input-event-codes.h): a US layout's
# physical keys; the computer's own layout decides what they type.
KEYS = {"Escape": 1, "Minus": 12, "Equal": 13, "Backspace": 14, "Tab": 15, "BracketLeft": 26, "BracketRight": 27,
        "Enter": 28, "ControlLeft": 29, "Semicolon": 39, "Quote": 40, "Backquote": 41, "ShiftLeft": 42,
        "Backslash": 43, "Comma": 51, "Period": 52, "Slash": 53, "ShiftRight": 54, "NumpadMultiply": 55,
        "AltLeft": 56, "Space": 57, "CapsLock": 58, "NumLock": 69, "ScrollLock": 70, "Numpad7": 71, "Numpad8": 72,
        "Numpad9": 73, "NumpadSubtract": 74, "Numpad4": 75, "Numpad5": 76, "Numpad6": 77, "NumpadAdd": 78,
        "Numpad1": 79, "Numpad2": 80, "Numpad3": 81, "Numpad0": 82, "NumpadDecimal": 83, "IntlBackslash": 86,
        "F11": 87, "F12": 88, "NumpadEnter": 96, "ControlRight": 97, "NumpadDivide": 98, "PrintScreen": 99,
        "AltRight": 100, "Home": 102, "ArrowUp": 103, "PageUp": 104, "ArrowLeft": 105, "ArrowRight": 106,
        "End": 107, "ArrowDown": 108, "PageDown": 109, "Insert": 110, "Delete": 111, "Pause": 119,
        "MetaLeft": 125, "MetaRight": 126, "ContextMenu": 127}
KEYS.update({f"Digit{d}": 2 + (d - 1) % 10 for d in range(1, 11) if d < 10})
KEYS["Digit0"] = 11
KEYS.update({f"Key{c}": code for c, code in zip("QWERTYUIOP", range(16, 26))})
KEYS.update({f"Key{c}": code for c, code in zip("ASDFGHJKL", range(30, 39))})
KEYS.update({f"Key{c}": code for c, code in zip("ZXCVBNM", range(44, 51))})
KEYS.update({f"F{n}": 58 + n for n in range(1, 11)})


def _unit(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 else None


def input_event(raw: str, width: int, height: int) -> tuple | None:
    """One message from the admin's side (JSON over the data channel) as a
    portal call: ("motion", x, y) in the stream's own size, ("button", code,
    pressed), ("axis", dx, dy) in steps, ("key", code, pressed). None if it
    isn't one."""
    if not isinstance(raw, str) or len(raw) > 300:
        return None
    try:
        m = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(m, dict):
        return None
    t = m.get("t")
    if t == "move":
        x, y = _unit(m.get("x")), _unit(m.get("y"))
        if x is None or y is None or width <= 0 or height <= 0:
            return None
        return ("motion", x * (width - 1), y * (height - 1))
    if t in ("down", "up"):
        b = m.get("b")
        return ("button", BUTTONS[b], t == "down") if b in BUTTONS and not isinstance(b, bool) else None
    if t == "wheel":
        dx, dy = m.get("dx"), m.get("dy")
        if not all(isinstance(v, int) and not isinstance(v, bool) and -10 <= v <= 10 for v in (dx, dy)) or (dx, dy) == (0, 0):
            return None
        return ("axis", dx, dy)
    if t in ("kd", "ku"):
        code = KEYS.get(m.get("code")) if isinstance(m.get("code"), str) else None
        return ("key", code, t == "kd") if code else None
    return None
