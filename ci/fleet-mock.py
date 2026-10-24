#!/usr/bin/python3
"""A stand-in fleet server for the package tests (kulan-fleet protocol v1).

  fleet-mock.py PORT LOG.json      (runs until killed)

Accepts the one-time code TEST-CODE, gives out a device ID and key, answers
the first report with a "refresh", an action Kulan OS doesn't allow
("run-command") and an "erase" this computer hasn't allowed, always sends
the organisation's policy, and writes every request it gets to LOG.json.
People: every report lists one person, fleetamina (uid 61901, setup code
SETUP-CODE); once a report brings her new password, she's sent back with it,
and the report after that removes her (keeping her files).
"""
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
TOKEN = "test-device-key"
POLICY = {"auto_updates": True, "firewall": True, "screen_lock_minutes": 5, "require_encryption": True,
          "run_script": "rm -rf /"}  # an unknown key: ignored by the computer
seen = []
reports = 0
SETUP_HASH = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input="SETUP-CODE", capture_output=True,
                            text=True, check=True).stdout.strip()
amina = {"username": "fleetamina", "full_name": "Amina Yusuf", "uid": 61901, "role": "employee",
         "password_hash": SETUP_HASH, "must_change": True, "password_changed_at": 1}
people_round = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        global reports
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        authorised = self.headers.get("Authorization") == f"Bearer {TOKEN}"
        seen.append({"path": self.path, "authorised": authorised, "body": body})
        with open(LOG, "w") as fh:
            json.dump(seen, fh, indent=1)
        if self.path == "/v1/enrol":
            if body.get("code") != "TEST-CODE":
                return self.reply(403, {"error": "wrong code"})
            return self.reply(200, {"device_id": "dev-1", "token": TOKEN, "organisation": "Kulan Test Org"})
        if not authorised:
            return self.reply(401, {"error": "no key"})
        if self.path == "/v1/report":
            reports += 1
            actions = ([{"id": "a1", "type": "refresh"}, {"id": "a2", "type": "run-command"},
                        {"id": "a3", "type": "erase"}] if reports == 1 else [])
            for p in body.get("passwords") or []:
                if p.get("username") == "fleetamina":
                    amina.update(password_hash=p["password_hash"], must_change=False,
                                 password_changed_at=p["changed_at"])
                    people_round["n"] = 1
            remove_now = people_round["n"] >= 2
            if people_round["n"] == 1:
                people_round["n"] = 2  # the next report removes her
            accounts = ([{"username": "fleetamina", "uid": 61901, "removed": True, "remove_files": False}]
                        if remove_now else [amina])
            return self.reply(200, {"actions": actions, "policy": POLICY, "accounts": accounts})
        return self.reply(200, {})


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
