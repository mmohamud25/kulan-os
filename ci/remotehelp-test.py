#!/usr/bin/python3
"""Kulan Fleet 2.9a on the computer, every case without a screen: who is at
the computer (helpsession.pick_session), what the helper is started with,
the ICE servers for GStreamer, the admin's mouse and keyboard checked and
turned into portal events, kulan-fleet's remote-help request (picked up,
or why not, and the helper started with its key only in the environment),
and the wake listener (wake.py) against a stand-in Realtime server:
handshake, join, a wake, pings, a message in pieces, heartbeats, a server
that refuses, and one that stops answering.

  python3 -I -B ci/remotehelp-test.py [kulan-fleet's folder of modules] [path/to/kulan-fleet]
Prints "remote help: every case passed", or ::error:: lines and exits 1.
"""
import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "packages/kulan-base/root/usr/share/kulan-fleet"
FLEET = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE.parent / "packages/kulan-base/root/usr/bin/kulan-fleet"
sys.path.insert(0, str(LIB))
import helpsession as hs  # noqa: E402
import wake  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
        print(f"::error::remote help: {name} {detail}")


# --- who is at the computer --------------------------------------------------------------
def sess(**kw):
    return {"Class": "user", "Type": "wayland", "Active": "yes", "Remote": "no", "LockedHint": "no", "Name": "amina",
            "User": "70000", **kw}


s, why = hs.pick_session([])
check("nobody signed in", s is None and "nobody is signed in" in why, why)
s, why = hs.pick_session([sess(Class="greeter", Name="gdm", User="120"), sess(Type="tty")])
check("the sign-in screen and a text console aren't someone at the desktop", s is None and "nobody is signed in" in why, why)
s, why = hs.pick_session([sess(Active="no")])
check("signed in but switched away", s is None and "sign-in screen" in why, why)
s, why = hs.pick_session([sess(LockedHint="yes")])
check("a locked screen", s is None and "locked" in why, why)
s, why = hs.pick_session([sess(Remote="yes"), sess(Name="omar", User="70001", Type="x11")])
check("someone at the desktop (not over the network)", s and s["Name"] == "omar", (s, why))
s, why = hs.pick_session([sess(Name="Root!", User="0"), sess(Name="x", User="999")])
check("system accounts and odd names aren't people", s is None, (s, why))


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def fake_loginctl(argv, timeout=60):
    if argv[:2] == ["loginctl", "list-sessions"]:
        return R(0, "  c1 120 gdm seat0 tty1\n   3 70000 amina seat0 tty2\n bad;id 1 x\n")
    if argv[:3] == ["loginctl", "show-session", "c1"]:
        return R(0, "Class=greeter\nType=wayland\nActive=no\nName=gdm\nUser=120\n")
    if argv[:3] == ["loginctl", "show-session", "3"]:
        return R(0, "Class=user\nType=wayland\nActive=yes\nRemote=no\nLockedHint=no\nName=amina\nUser=70000\nState=active\n")
    return R(1)


found = hs.sessions_now(fake_loginctl)
check("sessions read from loginctl", [x.get("Name") for x in found] == ["gdm", "amina"], found)
check("and the one at the desktop chosen", hs.pick_session(found)[0]["Name"] == "amina")

# --- what the helper is started with --------------------------------------------------------
SID = "0b6f9d1e-1c2a-4b3c-8d4e-5f6a7b8c9d0e"
good = {"server": "https://x.supabase.co/functions/v1/fleet", "session_id": SID, "token": "t" * 43,
        "admin": "it@acme.test<script>", "organisation": "Acme & Co\n", "control_asked": True,
        "ice": [{"urls": ["stun:stun.cloudflare.com:3478"]}]}
c = hs.clean_start(json.dumps(good))
check("well-formed start", c and c["admin"] == "it@acme.testscript" and c["organisation"] == "Acme & Co" and
      c["control_asked"] is True and c["ice"] == good["ice"], c)
for bad in ["", "nope", json.dumps([1]), json.dumps({**good, "session_id": "../x"}), json.dumps({**good, "token": "short"}),
            json.dumps({**good, "server": "file:///etc/passwd"})]:
    check(f"refused start: {bad[:40]}", hs.clean_start(bad) is None)
check("control only when asked, exactly", hs.clean_start(json.dumps({**good, "control_asked": "yes"}))["control_asked"] is False)

# --- ICE servers for webrtcbin ------------------------------------------------------------
stun, turns = hs.gst_ice([
    {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"]},
    {"urls": ["turn:turn.cloudflare.com:3478?transport=udp", "turn:turn.cloudflare.com:53?transport=udp",
              "turns:turn.cloudflare.com:5349?transport=tcp", "turn:turn.cloudflare.com:80?transport=tcp", "javascript:x"],
     "username": "user/1", "credential": "p+w=d@x"},
    {"urls": "turn:no-credentials.example:3478"}, "junk"])
check("STUN", stun == "stun://stun.cloudflare.com:3478", stun)
check("TURN with escaped credentials, port 53 left out", turns == [
    "turn://user%2F1:p%2Bw%3Dd%40x@turn.cloudflare.com:3478?transport=udp",
    "turns://user%2F1:p%2Bw%3Dd%40x@turn.cloudflare.com:5349?transport=tcp",
    "turn://user%2F1:p%2Bw%3Dd%40x@turn.cloudflare.com:80?transport=tcp"], turns)
check("nothing usable", hs.gst_ice("x") == (None, []) and hs.gst_ice([]) == (None, []))

# --- the admin's mouse and keyboard ------------------------------------------------------
E = lambda m, w=1920, h=1080: hs.input_event(json.dumps(m), w, h)  # noqa: E731
check("move", E({"t": "move", "x": 0.5, "y": 1}) == ("motion", 959.5, 1079.0), E({"t": "move", "x": 0.5, "y": 1}))
check("move off the screen refused", E({"t": "move", "x": 1.2, "y": 0}) is None and E({"t": "move", "x": True, "y": 0}) is None)
check("buttons", E({"t": "down", "b": 0}) == ("button", 0x110, True) and E({"t": "up", "b": 2}) == ("button", 0x111, False) and
      E({"t": "down", "b": 1}) == ("button", 0x112, True))
check("odd buttons refused", E({"t": "down", "b": 7}) is None and E({"t": "down", "b": True}) is None)
check("wheel", E({"t": "wheel", "dx": 0, "dy": -3}) == ("axis", 0, -3))
check("wheel limits", E({"t": "wheel", "dx": 0, "dy": 50}) is None and E({"t": "wheel", "dx": 0, "dy": 0}) is None and
      E({"t": "wheel", "dx": 0.5, "dy": 1}) is None)
check("keys", E({"t": "kd", "code": "KeyA"}) == ("key", 30, True) and E({"t": "ku", "code": "Enter"}) == ("key", 28, False))
check("every key code is a Linux one", hs.KEYS["Digit1"] == 2 and hs.KEYS["Digit9"] == 10 and hs.KEYS["Digit0"] == 11 and
      hs.KEYS["KeyQ"] == 16 and hs.KEYS["KeyP"] == 25 and hs.KEYS["KeyL"] == 38 and hs.KEYS["KeyM"] == 50 and
      hs.KEYS["F1"] == 59 and hs.KEYS["F10"] == 68 and hs.KEYS["F12"] == 88 and len(hs.KEYS) >= 100 and
      len(set(hs.KEYS.values())) == len(hs.KEYS), len(hs.KEYS))
for bad in ["", "{", "[]", json.dumps({"t": "kd", "code": "Power"}), json.dumps({"t": "type", "text": "rm -rf"}),
            json.dumps({"t": "move", "x": 0, "y": 0, "pad": "x" * 400})]:
    check(f"not input: {bad[:30]}", hs.input_event(bad, 1920, 1080) is None)
check("no size, no move", E({"t": "move", "x": 0.5, "y": 0.5}, 0, 0) is None)

# --- kulan-fleet picks a request up ---------------------------------------------------------
loader = importlib.machinery.SourceFileLoader("kulan_fleet", str(FLEET))
spec = importlib.util.spec_from_loader("kulan_fleet", loader)
kf = importlib.util.module_from_spec(spec)
loader.exec_module(kf)
posts, started = [], []
kf.read_json = lambda path, default: {"server": "https://fleet.test/fleet", "device_id": "dev-1", "token": "devkey"} \
    if path == kf.CONFIG else default


def fake_post(server, path, body, token=None):
    posts.append((path, body, token))
    if body.get("op") == "claim":
        return {"token": "s" * 43, "admin": "it@acme.test", "control_asked": True, "organisation": "Acme",
                "ice": [{"urls": ["stun:stun.cloudflare.com:3478"]}]}
    return {}


kf.post = fake_post
agent_there = {"yes": True}
kf.Path = lambda p: type("P", (), {"exists": lambda self: agent_there["yes"]})() if p == hs.AGENT else Path(p)
sessions = {"now": [sess()]}
kf.helpsession.sessions_now = lambda run: sessions["now"]
real_run = subprocess.run


def fake_subprocess_run(argv, **kw):
    started.append((argv, kw))
    return subprocess.CompletedProcess(argv, 0, "", "")


kf.subprocess.run = fake_subprocess_run
try:
    ok, detail = kf.do_action("remote-help", {"session_id": SID})
    check("picked up and the helper started", ok and detail == "asked amina", (ok, detail))
    check("claimed with the computer's key, for who's there",
          posts == [(f"/v1/help/{SID}", {"device_id": "dev-1", "op": "claim", "user": "amina"}, "devkey")], posts)
    argv, kw = started[0]
    check("as them, in their own systemd", argv[:6] == ["runuser", "-u", "amina", "--", "systemd-run", "--user"] and
          argv[-1] == hs.AGENT and "--setenv=KULAN_REMOTE_HELP" in argv, argv)
    check("the session's key never on a command line", not any("s" * 43 in a or "devkey" in a for a in argv), argv)
    env_start = json.loads(kw["env"]["KULAN_REMOTE_HELP"])
    check("the helper gets the session's key, never the computer's", env_start["token"] == "s" * 43 and
          "devkey" not in kw["env"]["KULAN_REMOTE_HELP"] and env_start["server"] == "https://fleet.test/fleet" and
          env_start["session_id"] == SID, env_start)
    check("their session bus", kw["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/70000/bus" and
          kw["env"]["XDG_RUNTIME_DIR"] == "/run/user/70000", kw["env"])
    posts.clear()
    sessions["now"] = [sess(LockedHint="yes")]
    ok, detail = kf.do_action("remote-help", {"session_id": SID})
    check("a locked screen: not picked up, and why", not ok and posts == [(f"/v1/help/{SID}", {
        "device_id": "dev-1", "op": "unavailable", "detail": detail}, "devkey")] and "locked" in detail, (detail, posts))
    posts.clear()
    agent_there["yes"] = False
    ok, detail = kf.do_action("remote-help", {"session_id": SID})
    check("no helper installed: said so", not ok and "isn't installed" in detail and posts[0][1]["op"] == "unavailable", detail)
    agent_there["yes"] = True
    posts.clear()
    for bad in [None, {}, {"session_id": "../../etc"}, {"session_id": SID.upper()}]:
        ok, detail = kf.do_action("remote-help", bad)
        check(f"not a request: {bad}", not ok and not posts, (detail, posts))
    check("remote-help is an action this computer takes, and says so", "remote-help" in kf.ALLOWED_ACTIONS)
finally:
    kf.subprocess.run = real_run

# --- the wake info ----------------------------------------------------------------------
T = "wake-" + "ab" * 24
W = {"topic": T, "url": "wss://x.supabase.co/realtime/v1/websocket", "key": "sb_publishable_" + "k" * 30}
check("wake info kept", wake.clean_info(W, "https://x.supabase.co/functions/v1/fleet") == W)
for name, bad in [("another host", {**W, "url": "wss://evil.example/realtime/v1/websocket"}),
                  ("not wss", {**W, "url": "ws://x.supabase.co/realtime/v1/websocket"}),
                  ("a query", {**W, "url": W["url"] + "?x=1"}), ("odd topic", {**W, "topic": "wake-xyz"}),
                  ("odd key", {**W, "key": "a b"}), ("not a dict", [W])]:
    check(f"wake info refused: {name}", wake.clean_info(bad, "https://x.supabase.co/functions/v1/fleet") is None)
check("ws only for a local test server",
      wake.clean_info({**W, "url": "ws://127.0.0.1:4000/realtime/v1/websocket"}, "http://127.0.0.1:4000/fleet") is not None)


# --- the listener against a stand-in Realtime -------------------------------------------------
def frame(opcode, data, fin=True):
    n = len(data)
    head = bytes([(0x80 if fin else 0) | opcode])
    head += bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n)
    return head + data


def read_frame(conn):
    b1, b2 = conn.recv(2, socket.MSG_WAITALL)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", conn.recv(2, socket.MSG_WAITALL))[0]
    mask = conn.recv(4, socket.MSG_WAITALL)
    data = conn.recv(n, socket.MSG_WAITALL) if n else b""
    return b1 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data)), b2 & 0x80


def serve(behaviour):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = {"path": None, "sent": [], "masked": True}

    def run():
        conn, _ = srv.accept()
        conn.settimeout(10)
        req = b""
        while b"\r\n\r\n" not in req:
            req += conn.recv(4096)
        lines = req.decode().split("\r\n")
        seen["path"] = lines[0].split()[1]
        key = next(line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("sec-websocket-key"))
        if behaviour == "refuse-http":
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            conn.close()
            return
        accept = base64.b64encode(hashlib.sha1((key + wake.GUID).encode()).digest()).decode()
        conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        try:
            while True:
                op, data, masked = read_frame(conn)
                seen["masked"] = seen["masked"] and bool(masked)
                if op == 0x8:
                    break
                if op == 0xA:
                    seen["pong"] = data
                    continue
                msg = json.loads(data)
                seen["sent"].append(msg)
                if msg["event"] == "phx_join":
                    status = "error" if behaviour == "refuse-join" else "ok"
                    conn.sendall(frame(1, json.dumps({"topic": msg["topic"], "event": "phx_reply", "ref": msg["ref"],
                                                      "payload": {"status": status, "response": {}}}).encode()))
                    if behaviour == "normal":
                        conn.sendall(frame(0x9, b"hi"))  # a ping
                        other = json.dumps({"topic": "realtime:wake-other", "event": "broadcast", "payload": {"event": "wake"}})
                        conn.sendall(frame(1, other.encode()))  # someone else's topic: nothing
                        w = json.dumps({"topic": msg["topic"], "event": "broadcast", "payload": {"event": "wake", "payload": {}}}).encode()
                        conn.sendall(frame(1, w[:10], fin=False) + frame(0, w[10:]))  # in two pieces
                if msg["event"] == "heartbeat" and behaviour == "normal":
                    conn.sendall(frame(1, json.dumps({"topic": "phoenix", "event": "phx_reply", "ref": msg["ref"],
                                                      "payload": {"status": "ok"}}).encode()))
        except (OSError, ValueError):
            pass
        conn.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], seen


port, seen = serve("normal")
info = {"topic": T, "url": f"ws://127.0.0.1:{port}/realtime/v1/websocket", "key": "eyJhbGciOi.eyJyb2xlIjoiYW5vbiJ9.sig"}
wakes, beats = [], {"n": 0}


def current():
    beats["n"] += 1
    return beats["n"] < 3


result = wake.listen(info, lambda: wakes.append(1), still_current=current, heartbeat_s=0.3)
check("joined its own topic", seen["sent"][0]["event"] == "phx_join" and seen["sent"][0]["topic"] == f"realtime:{T}" and
      seen["sent"][0]["payload"]["config"]["private"] is False and seen["sent"][0]["payload"]["access_token"] == info["key"],
      seen["sent"][:1])
check("the key in the address, as Realtime wants", seen["path"].startswith("/realtime/v1/websocket?apikey=eyJ") and "vsn=1.0.0" in seen["path"],
      seen["path"])
check("one wake: its own, in two pieces, after a ping (answered)", wakes == [1] and seen.get("pong") == b"hi", (wakes, seen.get("pong")))
check("heartbeats", sum(1 for m in seen["sent"] if m["event"] == "heartbeat") >= 2, [m["event"] for m in seen["sent"]])
check("the client masks what it sends", seen["masked"])
check("info changed: stops to start again", result == "changed", result)
check("a key that isn't a JWT isn't sent as one", "access_token" not in (lambda: (
    lambda p, s: (wake.listen({**info, "url": f"ws://127.0.0.1:{p}/realtime/v1/websocket", "key": "sb_publishable_" + "k" * 30},
                              lambda: None, still_current=lambda: False, heartbeat_s=0.2), s["sent"][0]["payload"])[1])(*serve("normal")))())

for behaviour, want in [("refuse-join", "didn't let"), ("refuse-http", "403"), ("silent", "stopped answering")]:
    port, _ = serve(behaviour)
    t0 = time.monotonic()
    try:
        wake.listen({**info, "url": f"ws://127.0.0.1:{port}/realtime/v1/websocket"}, lambda: None, heartbeat_s=0.2)
        check(f"{behaviour}: stops", False)
    except wake.Closed as e:
        if behaviour == "silent":  # joined, then nothing more: given up on after 3 heartbeats
            check(f"{behaviour}: stops, and why", want in str(e) and time.monotonic() - t0 < 5, str(e))
        else:
            check(f"{behaviour}: stops, and why", want in str(e), str(e))
    except OSError as e:
        check(f"{behaviour}: stops cleanly", False, repr(e))

if failures:
    sys.exit(1)
print("remote help: every case passed")
