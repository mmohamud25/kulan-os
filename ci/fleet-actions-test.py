#!/usr/bin/python3
"""Kulan Fleet 2.9b on the computer, every case with stand-ins for the system:
Kulan's fixes (fixes.py), the organisation's scripts (allowed only by the
policy, only unchanged, output kept to 64 KB, stopped after the time limit
with everything they started), Wi-Fi networks and printers from a group
(pushed.py), and kulan-fleet's run_job and messages.

  python3 -I -B ci/fleet-actions-test.py [kulan-fleet's folder of modules] [path/to/kulan-fleet]
Prints "fleet actions: every case passed", or ::error:: lines and exits 1.
"""
import configparser
import hashlib
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "packages/kulan-base/root/usr/share/kulan-fleet"
FLEET = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE.parent / "packages/kulan-base/root/usr/bin/kulan-fleet"
sys.path.insert(0, str(LIB))
import fixes  # noqa: E402
import pushed  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
        print(f"::error::fleet actions: {name} {str(detail)[:1500]}")


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Run:
    """A stand-in for kulan-fleet's run(): records each command, answers from a table."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, argv, timeout=60):
        self.calls.append(argv)
        for key, answer in self.answers.items():
            if " ".join(argv).startswith(key):
                return answer(argv) if callable(answer) else answer
        return R()


tmp = Path(tempfile.mkdtemp(prefix="kulan-actions-"))

# --- Kulan's fixes -----------------------------------------------------------------------
fixes.shutil.which = lambda name: f"/usr/bin/{name}"  # every tool "installed": the stand-in answers
ok, detail, out, code = fixes.run_fix("rm -rf /", Run())
check("only Kulan's fixes", not ok and code == 2, detail)
fixes.UPDATE_RUN = tmp / "no-update-run"
run = Run()
ok, detail, out, code = fixes.run_fix("free-space", run)
check("free-space without Software Update: apt's cache and old logs", ok and run.calls[0] == ["apt-get", "clean"] and
      run.calls[1] == ["journalctl", "--vacuum-time=2weeks"] and detail.startswith("freed ") and code == 0, (run.calls, detail))
fixes.UPDATE_RUN = tmp / "update-run"
fixes.UPDATE_RUN.write_text("#!/bin/sh\n")
run = Run({str(fixes.UPDATE_RUN) + " cleanup": R(1, "", "E: apt is busy")})
ok, detail, out, code = fixes.run_fix("free-space", run)
check("free-space with Software Update's own clean-ups; a failed step is reported", not ok and code == 1 and
      [c[1] for c in run.calls] == ["clean-cache", "cleanup", "clean-logs"] and "E: apt is busy" in out and
      "Old system files: failed (1)" in out, (run.calls, out))
run = Run({"nm-online": R(0)})
ok, detail, out, code = fixes.run_fix("reset-wifi", run)
check("reset-wifi: off, on, the network service, then online again", ok and run.calls[:3] == [
    ["nmcli", "radio", "wifi", "off"], ["nmcli", "radio", "wifi", "on"], ["systemctl", "restart", "NetworkManager.service"]]
    and run.calls[3][0] == "nm-online", run.calls)
run = Run()
ok, detail, out, code = fixes.run_fix("repair-updates", run)
check("repair-updates: interrupted installs, missing pieces, fresh lists, never asking", ok and
      [c[:3] for c in run.calls] == [["env", "DEBIAN_FRONTEND=noninteractive", "dpkg"],
                                     ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"],
                                     ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]] and "-y" in run.calls[1], run.calls)
run = Run({"lpstat -p": R(0, "printer Front_desk disabled since Mon 10:00 -\nprinter Back is idle.  enabled since x\n")})
ok, detail, out, code = fixes.run_fix("restart-printing", run)
check("restart-printing: the service, then stopped printers back on", ok and ["cupsenable", "Front_desk"] in run.calls and
      ["cupsenable", "Back"] not in run.calls, run.calls)
run = Run({"df": R(0, "/dev/vda2 60G 20G 40G 34% /"), "journalctl -k": R(0, ""), "lsblk": R(0, "vda disk\nvda1 part\n"),
           "smartctl": R(0, "SMART overall-health self-assessment test result: PASSED")})
ok, detail, out, code = fixes.run_fix("check-disk", run)
check("check-disk: read only, healthy", ok and detail == "no problems found" and code == 0 and
      not any(c[0] in ("fsck", "e2fsck", "mkfs") for c in run.calls), (detail, run.calls))
run = Run({"df": R(0, ""), "journalctl -k": R(0, "Oct 26 kernel: blk_update_request: I/O error, dev vda, sector 9\n"),
           "lsblk": R(0, "")})
ok, detail, out, code = fixes.run_fix("check-disk", run)
check("check-disk: errors found are shown", ok and code == 1 and "problems found" in detail and "I/O error" in out, (detail, out))
check("a long output is cut to 64 KB", len(fixes.clip("é" * 70000).encode()) <= 65536)

# --- the organisation's scripts -------------------------------------------------------------
fixes.SCRIPT_DIR = tmp / "scripts"
sha = lambda b: hashlib.sha256(b.encode()).hexdigest()  # noqa: E731
body = "echo hello; echo err >&2; exit 3\n"
ok, detail, out, code = fixes.run_script(body, sha(body), allowed=False)
check("scripts not allowed: not run", not ok and code == 2 and "doesn't allow" in detail, detail)
ok, detail, out, code = fixes.run_script(body, sha("something else"), allowed=True)
check("a script that changed: not run", not ok and "isn't the one" in detail, detail)
real_isdir = Path.is_dir
Path.is_dir = lambda self: False if str(self) == "/run/systemd/system" else real_isdir(self)  # no systemd here: plain bash
try:
    ok, detail, out, code = fixes.run_script(body, sha(body), allowed=True)
    check("a script runs; its output and exit code come back", not ok and code == 3 and "hello" in out and "err" in out and
          "code 3" in detail, (ok, detail, out, code))
    check("its file is gone afterwards", not any(fixes.SCRIPT_DIR.iterdir()), list(fixes.SCRIPT_DIR.iterdir()))
    check("only root can read where scripts are put", oct(fixes.SCRIPT_DIR.stat().st_mode & 0o777) == "0o700")
    loud = "head -c 300000 /dev/zero | tr '\\0' 'x'; echo; echo done\n"
    ok, detail, out, code = fixes.run_script(loud, sha(loud), allowed=True)
    check("a chatty script: the first 64 KB kept", ok and len(out.encode()) <= 65536 and out.startswith("x"), (len(out), detail))
    fixes.SCRIPT_TIMEOUT_S = -28  # the limit is SCRIPT_TIMEOUT_S + 30: 2 seconds here
    marker = tmp / "child-alive"
    slow = f"(sleep 4; touch {marker}) & sleep 30\n"
    t0 = time.monotonic()
    ok, detail, out, code = fixes.run_script(slow, sha(slow), allowed=True)
    took = time.monotonic() - t0
    time.sleep(3)
    check("a script that runs too long is stopped, with everything it started", not ok and code == 124 and
          "stopped after 10 minutes" in out and took < 8 and not marker.exists(), (took, out, marker.exists()))
finally:
    Path.is_dir = real_isdir

# --- Wi-Fi networks --------------------------------------------------------------------------
N1, N2, N3 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333"
nets = pushed.clean_networks([
    {"id": N1, "ssid": "Acme Staff", "security": "wpa-psk", "password": " pass\\word=[x] "},
    {"id": N2, "ssid": "Guest ☕", "security": "open", "password": "ignored", "hidden": True},
    {"id": N3, "ssid": "Acme Staff", "security": "open"},                        # a name already listed
    {"id": "nope", "ssid": "x", "security": "open"},
    {"id": "44444444-4444-4444-8444-444444444444", "ssid": "Short", "security": "wpa-psk", "password": "short"},
    {"id": "55555555-5555-4555-8555-555555555555", "ssid": "x" * 33, "security": "open"},
    {"id": "66666666-6666-4666-8666-666666666666", "ssid": "bad\nname", "security": "open"}, "junk"])
check("only well-formed networks", [n["id"] for n in nets] == [N1, N2] and nets[1]["password"] is None and nets[1]["hidden"], nets)
folder = tmp / "system-connections"
folder.mkdir()
(folder / "Home.nmconnection").write_text("[connection]\nid=Home\n")  # someone's own
(folder / f"{pushed.PREFIX}99999999-9999-4999-8999-999999999999.nmconnection").write_text("old")  # no longer listed
run = Run()
status = pushed.apply_networks(nets, run, folder)
check("networks added", status == {N1: "added", N2: "added"}, status)
f1 = folder / f"{pushed.PREFIX}{N1}.nmconnection"
check("root only, as NetworkManager wants", oct(f1.stat().st_mode & 0o777) == "0o600", oct(f1.stat().st_mode))
kf = configparser.ConfigParser(interpolation=None, strict=True)
kf.optionxform = str
kf.read_string(f1.read_text())
check("a proper keyfile: WPA, the name as bytes", kf["connection"]["type"] == "wifi" and kf["wifi-security"]["key-mgmt"] == "wpa-psk"
      and bytes(int(b) for b in kf["wifi"]["ssid"].rstrip(";").split(";")).decode() == "Acme Staff", f1.read_text())
check("the password escaped as GKeyFile reads it back", kf["wifi-security"]["psk"] == "\\spass\\\\word=[x]\\s", kf["wifi-security"]["psk"])
try:  # GLib itself, where it's installed (the CI image): the real reader
    import gi  # noqa: F401
    from gi.repository import GLib
    for pw in [" pass\\word=[x] ", "a b\tc#;", "  two  "]:
        g = GLib.KeyFile()
        text = pushed.keyfile(dict(nets[0], password=pw))
        g.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
        check(f"GLib reads the password back exactly: {pw!r}", g.get_string("wifi-security", "psk") == pw)
        check("GLib reads the name back", bytes(g.get_integer_list("wifi", "ssid")) == b"Acme Staff")
except ImportError:
    pass
k2 = (folder / f"{pushed.PREFIX}{N2}.nmconnection").read_text()
check("an open, hidden network: no security section", "[wifi-security]" not in k2 and "hidden=true" in k2, k2)
check("the old one gone, someone's own kept", not (folder / f"{pushed.PREFIX}99999999-9999-4999-8999-999999999999.nmconnection").exists()
      and (folder / "Home.nmconnection").exists())
check("NetworkManager told", run.calls == [["nmcli", "connection", "reload"]], run.calls)
run = Run()
pushed.apply_networks(nets, run, folder)
check("nothing changed: NetworkManager not bothered", run.calls == [], run.calls)
check("no password on any command line", not any("word" in " ".join(c) for c in run.calls))
check("without NetworkManager: said so", pushed.apply_networks(nets, Run(), tmp / "none") == {
    N1: "NetworkManager isn't installed", N2: "NetworkManager isn't installed"})

# --- printers --------------------------------------------------------------------------------
state = tmp / "fleet-printers.json"
P1, P2 = "aaaaaaaa-1111-4111-8111-111111111111", "bbbbbbbb-2222-4222-8222-222222222222"
prs = [{"id": P1, "name": "Front desk", "uri": "ipp://192.168.1.20/ipp/print"},
       {"id": P2, "name": "Back office!", "uri": "ipps://printer.acme.test:631/ipp/print"},
       {"id": "cccccccc-3333-4333-8333-333333333333", "name": "Bad", "uri": "file:///etc/passwd"},
       {"id": "dddddddd-4444-4444-8444-444444444444", "name": "Bad2", "uri": "ipp://x/;rm -rf"}]
check("only network printers", [p["id"] for p in pushed.clean_printers(prs)] == [P1, P2])
check("queue names CUPS takes", pushed.queue_name(prs[0]) == "Front_desk_aaaa" and pushed.queue_name(prs[1]) == "Back_office_bbbb")
run = Run({"lpadmin -p Back_office_bbbb": R(1, "", "lpadmin: Unable to connect")})
status = pushed.apply_printers(prs, run, state)
check("printers added; one not reachable yet", status[P1] == "added" and status[P2].startswith("couldn't reach") and
      ["lpadmin", "-p", "Front_desk_aaaa", "-E", "-v", "ipp://192.168.1.20/ipp/print", "-m", "everywhere", "-D", "Front desk",
       "-L", "Added by your organisation"] in run.calls, (status, run.calls))
check("what Kulan Fleet made is remembered", set(json.loads(state.read_text())) == {P1})
run = Run({"lpstat -p Front_desk_aaaa": R(0)})
status = pushed.apply_printers(prs, run, state)
check("the next report: the made one left alone, the other tried again", status[P2] == "added" and
      not any(c[:3] == ["lpadmin", "-p", "Front_desk_aaaa"] for c in run.calls), run.calls)
run = Run({"lpstat -p": R(0)})
pushed.apply_printers([prs[1]], run, state)
check("no longer listed: removed", ["lpadmin", "-x", "Front_desk_aaaa"] in run.calls and set(json.loads(state.read_text())) == {P2},
      run.calls)
run = Run()
pushed.remove_all(run, folder, state)
check("leaving: the organisation's networks and printers go, someone's own stay",
      ["lpadmin", "-x", "Back_office_bbbb"] in run.calls and not list(folder.glob(f"{pushed.PREFIX}*")) and
      (folder / "Home.nmconnection").exists() and not state.exists(), run.calls)

# --- kulan-fleet: run_job and messages ----------------------------------------------------------
loader = importlib.machinery.SourceFileLoader("kulan_fleet", str(FLEET))
spec = importlib.util.spec_from_loader("kulan_fleet", loader)
kf_mod = importlib.util.module_from_spec(spec)
loader.exec_module(kf_mod)
told, posts = [], []
kf_mod.notify_users = lambda title, body: told.append((title, body))
policy = {"policy": {}}
kf_mod.read_json = lambda path, default: policy if path == kf_mod.POLICY_PUBLIC else (
    {"organisation": "Acme", "server": "https://fleet.test", "device_id": "d1", "token": "t"} if path == kf_mod.CONFIG else default)
kf_mod.post = lambda server, path, body, token=None: posts.append(path) or {"body": "echo hi\n"}
config = {"organisation": "Acme", "server": "https://fleet.test", "device_id": "d1", "token": "t"}
ok, detail, extra = kf_mod.run_job("run-script", {"sha256": sha("echo hi\n"), "name": "Hi"}, "act-1", config)
check("scripts not allowed by the policy: not even fetched", not ok and not posts and not told, (detail, posts))
policy["policy"]["scripts"] = True
Path.is_dir = lambda self: False if str(self) == "/run/systemd/system" else real_isdir(self)
try:
    ok, detail, extra = kf_mod.run_job("run-script", {"sha256": sha("echo hi\n"), "name": "Hi\x1b[31m"}, "act-1", config)
finally:
    Path.is_dir = real_isdir
check("allowed: fetched for that request, run, the person told", ok and posts == ["/v1/scripts/act-1"] and
      extra["output"].strip() == "hi" and extra["exit_code"] == 0 and told == [("Acme is running a script", "Hi[31m")],
      (detail, posts, extra, told))
told.clear()
kf_mod.fixes.run_fix = lambda name, run: (True, "freed 10 MB", "log", 0)
ok, detail, extra = kf_mod.run_job("run-fix", {"fix": "free-space"}, "act-2", config)
check("a fix: the person told which", ok and told == [("Acme is running a fix", "Free up disk space")] and extra["output"] == "log",
      (told, extra))
ok, detail, extra = kf_mod.run_job("run-fix", {"fix": "../../bin/sh"}, "act-3", config)
check("not one of Kulan's fixes: refused", not ok and extra == {}, detail)
told.clear()
ok, detail = kf_mod.do_action("message", {"title": "Restart tonight\x07", "body": "Save your work.\nThanks"})
check("a message shown on the screens, with who sent it", ok and told == [("Restart tonight (Acme)", "Save your work.\nThanks")], told)
ok, detail = kf_mod.do_action("message", {"body": "no title"})
check("a message needs a title", not ok)
check("all three are actions this computer takes", {"run-fix", "run-script", "message"} <= set(kf_mod.ALLOWED_ACTIONS))
check("the policy keeps scripts only when it says true",
      kf_mod.clean_policy({"scripts": True}).get("scripts") is True and "scripts" not in kf_mod.clean_policy({"scripts": "yes"}))

if failures:
    sys.exit(1)
print("fleet actions: every case passed")
