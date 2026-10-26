#!/usr/bin/python3
"""Kulan Fleet 2.9a: the helper in the person's session (kulan-remote-help),
on this system's own GStreamer and Python, against a stand-in server.

1. Allowed with control: it answers, builds the pipeline (test picture,
   VP8, webrtcbin), and offers the screen (sent only) and the data channel
   for the mouse and keyboard, with its network candidates; when the server
   says the admin ended it, it stops and exits.
2. Declined: it says no and exits, sharing nothing.
3. Nobody answers: it ends the session itself ("nobody answered").
4. Its windows (needs a display: run under xvfb-run): the question with
   Allow and Don't allow (focus on Don't allow), the tick box for control
   only when asked, and the Stop sharing window.

  python3 -I -B ci/remotehelp-helper-test.py [path/to/kulan-remote-help] [kulan-fleet's folder of modules]
Prints "remote help helper: every case passed", or ::error:: lines and exits 1.
"""
import http.server
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "packages/kulan-settings/root/usr/lib/kulan/kulan-remote-help"
LIB = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE.parent / "packages/kulan-base/root/usr/share/kulan-fleet"
SID = "0b6f9d1e-1c2a-4b3c-8d4e-5f6a7b8c9d0e"
TOKEN = "s" * 43
failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
        print(f"::error::remote help helper: {name} {str(detail)[:1500]}")


class Stand:
    """The server's side of /v1/help/<id>, as the fleet function answers it."""

    def __init__(self, end_after_offer=True):
        self.calls, self.state, self.offer, self.candidates = [], "asking", None, []
        self.end_after_offer = end_after_offer
        stand = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                ok_key = self.headers.get("Authorization") == f"Bearer {TOKEN}" and self.path == f"/fleet/v1/help/{SID}"
                stand.calls.append(body)
                out, code = stand.answer(body) if ok_key else ({"error": "unknown session or key"}, 401)
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/fleet"

    def answer(self, body):
        op = body.get("op")
        if op == "answer":
            self.state = "allowed" if body.get("allow") else "declined"
            return {"state": self.state, "control": bool(body.get("allow") and body.get("control"))}, 200
        if op == "signal":
            if body.get("offer"):
                self.offer = body["offer"]
            self.candidates += body.get("candidates", [])
            if self.offer and self.candidates and self.end_after_offer and self.state == "allowed" and len(self.calls) > 4:
                self.state = "ended"
            if self.state not in ("allowed", "connected"):
                return {"state": self.state, "detail": "the admin ended it"}, 200
            return {"state": self.state, "control": True, "answer": None, "candidates": [], "count": 0}, 200
        if op in ("end", "failed"):
            self.state = "ended" if op == "end" else "failed"
            return {"state": self.state}, 200
        return {"error": "unknown op"}, 400


tmp = Path(tempfile.mkdtemp(prefix="kulan-help-test-"))
agent = tmp / "kulan-remote-help"
agent.write_text(AGENT.read_text().replace('"/usr/share/kulan-fleet"', json.dumps(str(LIB))))


def run_helper(stand, answer, extra=None, timeout=60):
    start = {"server": stand.url, "session_id": SID, "token": TOKEN, "admin": "it@acme.test", "organisation": "Acme",
             "control_asked": True, "ice": [{"urls": ["stun:127.0.0.1:3478"]}]}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "KULAN_REMOTE_HELP": json.dumps(start),
           "KULAN_REMOTE_HELP_TEST": answer, **(extra or {})}
    try:
        p = subprocess.run([sys.executable, "-I", str(agent)], env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        return None, (e.stdout or b"").decode() + (e.stderr or b"").decode() if isinstance(e.stdout, bytes) else str(e)


try:
    # 1. Allowed with control.
    stand = Stand()
    code, out = run_helper(stand, "allow-control")
    check("allowed: exits by itself when the admin ends it", code == 0, out[-800:])
    check("allowed: asked first", out.startswith("asked it@acme.test Acme control"), out[:200])
    check("allowed, with control", stand.calls[0] == {"op": "answer", "allow": True, "control": True}, stand.calls[:1])
    sdp = (stand.offer or {}).get("sdp", "")
    check("the offer: the screen, sent only, as VP8", "m=video" in sdp and "VP8/90000" in sdp and "a=sendonly" in sdp, sdp[:600])
    check("the offer: the data channel for the mouse and keyboard", "m=application" in sdp and "webrtc-datachannel" in sdp, sdp[-600:])
    real = [c["candidate"] for c in stand.candidates if c["candidate"]]
    check("network candidates sent", real and all(c.startswith("candidate:") for c in real), stand.candidates)
    check("told the person it ended", "done Remote help ended: the admin ended it" in out, out[-300:])
    check("nothing failed", not any(c.get("op") == "failed" for c in stand.calls), [c.get("op") for c in stand.calls])

    # 2. Declined.
    stand = Stand()
    code, out = run_helper(stand, "decline")
    check("declined: says no and exits", code == 0 and stand.calls == [{"op": "answer", "allow": False, "control": False}],
          (code, stand.calls, out[-300:]))
    check("declined: nothing shared", "watching" not in out and stand.offer is None)

    # 3. Nobody answers (the time to answer made short for the test).
    stand = Stand()
    slow = tmp / "kulan-remote-help-slow"
    slow.write_text(agent.read_text().replace("self.asking_timer = GLib.timeout_add_seconds(helpsession.ASK_S,",
                                              "self.asking_timer = GLib.timeout_add_seconds(1,")
                    .replace("GLib.timeout_add(200, lambda: answered(", "GLib.timeout_add(60000, lambda: answered("))
    p = subprocess.run([sys.executable, "-I", str(slow)], capture_output=True, text=True, timeout=60, env={
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "KULAN_REMOTE_HELP_TEST": "allow",
        "KULAN_REMOTE_HELP": json.dumps({"server": stand.url, "session_id": SID, "token": TOKEN, "ice": []})})
    check("nobody answered: it ends the session itself", p.returncode == 0 and stand.calls == [
        {"op": "end", "detail": "nobody answered on the computer"}], (stand.calls, p.stdout[-300:], p.stderr[-300:]))

    # 4. Its windows.
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        os.environ.pop("KULAN_REMOTE_HELP_TEST", None)
        sys.path.insert(0, str(LIB))
        loader = importlib.machinery.SourceFileLoader("kulan_remote_help", str(agent))
        spec = importlib.util.spec_from_loader("kulan_remote_help", loader)
        rh = importlib.util.module_from_spec(spec)
        loader.exec_module(rh)
        from gi.repository import Gtk  # noqa: E402

        app = rh.Adw.Application(application_id="org.kulan.RemoteHelpTest", flags=rh.Gio.ApplicationFlags.NON_UNIQUE)
        results = {}

        def walk(w):
            yield w
            c = w.get_first_child()
            while c:
                yield from walk(c)
                c = c.get_next_sibling()

        def activate(a):
            a.hold()
            win = rh.Window(a)
            answers = []
            win.ask("it@acme.test", "Acme", True, lambda allow, control: answers.append((allow, control)))
            widgets = list(walk(win.win))
            texts = " ".join(w.get_text() for w in widgets if isinstance(w, Gtk.Label))
            buttons = {w.get_label(): w for w in widgets if isinstance(w, Gtk.Button) and w.get_label()}
            ticks = [w for w in widgets if isinstance(w, Gtk.CheckButton)]
            results["question"] = "it@acme.test, from Acme, is asking to see your screen" in texts and "an hour" in texts
            results["buttons"] = {"Allow", "Don't allow"} <= set(buttons)
            results["focus"] = buttons.get("Don't allow") is not None and win.win.get_focus() is buttons["Don't allow"]
            results["tick"] = len(ticks) == 1 and "mouse and keyboard" in ticks[0].get_label()
            ticks[0].set_active(True)
            buttons["Allow"].emit("clicked")
            results["answer"] = answers == [(True, True)]
            win2 = rh.Window(a)
            win2.ask("it@acme.test", "Acme", False, lambda allow, control: answers.append((allow, control)))
            results["no tick"] = not [w for w in walk(win2.win) if isinstance(w, Gtk.CheckButton)]
            win2.win.close()  # closing the question says no
            results["closed is no"] = answers[-1] == (False, False)
            stopped = []
            win.watching("it@acme.test", True, "Connecting…", lambda: stopped.append(1))
            labels = " ".join(w.get_text() for w in walk(win.win) if isinstance(w, Gtk.Label))
            stop = [w for w in walk(win.win) if isinstance(w, Gtk.Button) and w.get_label() == "Stop sharing"]
            results["watching"] = "it@acme.test can see your screen and use your mouse and keyboard" in labels and len(stop) == 1
            stop[0].emit("clicked")
            results["stop"] = stopped == [1]
            win.done(None)
            a.release()
            a.quit()

        app.connect("activate", activate)
        app.run([])
        for name in ["question", "buttons", "focus", "tick", "answer", "no tick", "closed is no", "watching", "stop"]:
            check(f"window: {name}", results.get(name) is True, results)
    else:
        print("(no display: the windows weren't checked; run under xvfb-run)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if failures:
    sys.exit(1)
print("remote help helper: every case passed")
