#!/usr/bin/env python3
"""Tests /usr/lib/kulan/browser-look (2.12): Chrome-type browsers take
Kulan's look (the GTK theme) once, before they're first opened or in
profiles where no theme was chosen, never over a choice, never while open.

  python3 -I -B ci/browser-look-test.py [path/to/browser-look]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = sys.argv[1] if len(sys.argv) > 1 else "/usr/lib/kulan/browser-look"
tmp = Path(tempfile.mkdtemp())
home, system_fp, user_fp, root = tmp / "home", tmp / "fp-system", tmp / "fp-user", tmp / "root"
for d in (home, system_fp, user_fp, root):
    d.mkdir()
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
       "KULAN_FLATPAK_DIRS": f"{system_fp}:{user_fp}", "KULAN_ROOT": str(root)}
STATE = home / ".local/state/kulan/browser-look"


def run() -> str:
    out = subprocess.run([sys.executable, "-I", "-B", SCRIPT], env=ENV, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out
    assert not out.stderr, out.stderr
    return out.stdout


def install(where: Path, app: str) -> Path:
    (where / app / "current").mkdir(parents=True)
    return home / ".var/app" / app / "config"


def prefs(folder: Path, profile: str = "Default") -> dict:
    return json.loads((folder / profile / "Preferences").read_text())


def used(folder: Path, data: dict, profile: str = "Default") -> Path:
    (folder / profile).mkdir(parents=True)
    f = folder / profile / "Preferences"
    f.write_text(json.dumps(data))
    f.chmod(0o600)
    return f


def check(cond: bool, what: str) -> None:
    if not cond:
        print(f"browser-look: FAILED: {what}")
        sys.exit(1)


# Nothing installed: nothing made.
run()
check(not (home / ".var").exists() and not (home / ".config").exists() and not STATE.exists(), "nothing installed, nothing made")

# Chrome from the Store, never opened: its first profile starts with GTK.
chrome = install(system_fp, "com.google.Chrome") / "google-chrome"
# Brave (installed for this person only), used, no theme chosen; a second profile too.
brave = install(user_fp, "com.brave.Browser") / "BraveSoftware/Brave-Browser"
brave_prefs = used(brave, {"browser": {"window_placement": {"left": 5}}, "extensions": {"settings": {"x": {}}}})
used(brave, {"extensions": {"theme": {"id": ""}}}, "Profile 1")
# Edge: Classic chosen. Chromium: a Web Store theme. Ungoogled: open.
edge = install(system_fp, "com.microsoft.Edge") / "microsoft-edge"
edge_text = json.dumps({"extensions": {"theme": {"system_theme": 0}}})
used(edge, json.loads(edge_text))
chromium = install(system_fp, "org.chromium.Chromium") / "chromium"
used(chromium, {"extensions": {"theme": {"id": "abcdefghijklmnop"}}})
# Chrome as a package, with a broken Preferences file.
(root / "opt/google/chrome").mkdir(parents=True)
(root / "opt/google/chrome/chrome").write_text("")
native = home / ".config/google-chrome"
used(native, {}).write_text("{not json")
# Brave as a package too, open right now.
(root / "opt/brave.com/brave").mkdir(parents=True)
(root / "opt/brave.com/brave/brave").write_text("")
open_one = home / ".config/BraveSoftware/Brave-Browser"
used(open_one, {"a": 1})
os.symlink("thinkpad-1234", open_one / "SingletonLock")

out = run()
check(prefs(chrome) == {"extensions": {"theme": {"system_theme": 1}}}, "new Chrome starts with GTK")
check((chrome / "Default/Preferences").stat().st_mode & 0o777 == 0o600, "new Preferences is private")
p = prefs(brave)
check(p["extensions"]["theme"] == {"system_theme": 1} and p["browser"]["window_placement"]["left"] == 5
      and p["extensions"]["settings"] == {"x": {}}, "used Brave gets GTK, keeps everything else")
check(brave_prefs.stat().st_mode & 0o777 == 0o600, "permissions kept")
check(prefs(brave, "Profile 1")["extensions"]["theme"]["system_theme"] == 1, "every profile")
check((edge / "Default/Preferences").read_text() == edge_text, "Classic chosen: left alone")
check(prefs(chromium)["extensions"]["theme"] == {"id": "abcdefghijklmnop"}, "Web Store theme: left alone")
check((native / "Default/Preferences").read_text() == "{not json", "broken file: left alone")
check(prefs(open_one) == {"a": 1}, "open browser: left alone")
check(not list(brave.glob("*/.kulan-*")), "no temporary files left")
done = STATE.read_text().splitlines()
check(sorted(done) == sorted(map(str, (chrome, brave, edge, chromium))), f"done list: {done}")
check(out.count("uses Kulan's look") == 4, out)

# Once done, never again: choosing Classic later sticks, even after "Reset".
(chrome / "Default/Preferences").write_text("{}")
# The open one closes, the broken one gets fixed by the browser.
(open_one / "SingletonLock").unlink()
(native / "Default/Preferences").write_text(json.dumps({"profile": {"name": "Me"}}))
run()
check(prefs(chrome) == {}, "not changed back once done")
check(prefs(open_one) == {"a": 1, "extensions": {"theme": {"system_theme": 1}}}, "done once closed")
check(prefs(native)["extensions"]["theme"]["system_theme"] == 1 and prefs(native)["profile"]["name"] == "Me", "done once readable")
check(len(STATE.read_text().splitlines()) == 6, "everything done once")
check(run() == "", "nothing more to do")
print("browser-look: every case passed")
