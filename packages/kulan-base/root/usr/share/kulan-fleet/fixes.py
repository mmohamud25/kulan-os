"""Fixes and scripts an organisation runs on this computer (Kulan Fleet 2.9b).

Fixes are Kulan's own: each does one well-known thing (FIXES). Scripts are
the organisation's own, and run only while its Owner allows them (the
"scripts" policy) and only if the text fetched has the SHA-256 the request
was made with. Both run as root with a time limit; their output (the first
64 KB) and exit code go back to the admin. Whoever is using the computer is
told what ran.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

FIXES = {
    "free-space": "Free up disk space",
    "reset-wifi": "Reset Wi-Fi",
    "repair-updates": "Repair updates",
    "restart-printing": "Restart printing",
    "check-disk": "Check the disk",
}
OUTPUT_MAX = 65536
FIX_TIMEOUT_S = 600
SCRIPT_TIMEOUT_S = 600
UPDATE_RUN = Path("/usr/lib/kulan/update-run")
SCRIPT_DIR = Path("/run/kulan/fleet-scripts")


def clip(text: str) -> str:
    data = text.encode("utf-8", "replace")
    return data[:OUTPUT_MAX].decode("utf-8", "ignore") if len(data) > OUTPUT_MAX else text


def _free_kb(path: str = "/") -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize // 1024


def _steps(run, steps: list[tuple[str, list[str], int]]) -> tuple[bool, list[str]]:
    """Runs each (label, command, timeout); the log lines and whether every step worked."""
    log, ok = [], True
    for label, argv, timeout in steps:
        if not shutil.which(argv[0]) and not Path(argv[0]).exists():
            log.append(f"== {label}: skipped ({argv[0]} isn't installed)")
            continue
        r = run(argv, timeout=timeout)
        log.append(f"== {label}: {'done' if r.returncode == 0 else f'failed ({r.returncode})'}")
        out = (r.stdout + r.stderr).strip()
        if out:
            log.append(out[-8000:])
        ok = ok and r.returncode == 0
    return ok, log


def run_fix(name: str, run) -> tuple[bool, str, str, int]:
    """(ok, short result, output, exit code) for one of Kulan's fixes."""
    if name not in FIXES:
        return False, "not one of Kulan's fixes", "", 2
    if name == "free-space":
        before = _free_kb()
        if UPDATE_RUN.exists():
            steps = [("Downloaded update files", [str(UPDATE_RUN), "clean-cache"], 300),
                     ("Old system files", [str(UPDATE_RUN), "cleanup"], 900),
                     ("System logs older than two weeks", [str(UPDATE_RUN), "clean-logs"], 300)]
        else:
            steps = [("Downloaded update files", ["apt-get", "clean"], 300),
                     ("System logs older than two weeks", ["journalctl", "--vacuum-time=2weeks"], 300)]
        ok, log = _steps(run, steps)
        freed = max(0, _free_kb() - before) // 1024
        return ok, f"freed {freed} MB" if ok else "some of it couldn't be done", clip("\n".join(log)), 0 if ok else 1
    if name == "reset-wifi":
        ok, log = _steps(run, [("Wi-Fi off", ["nmcli", "radio", "wifi", "off"], 30),
                               ("Wi-Fi on", ["nmcli", "radio", "wifi", "on"], 30),
                               ("Network service restarted", ["systemctl", "restart", "NetworkManager.service"], 60)])
        # Back online before the result is sent.
        for _ in range(30):
            if run(["nm-online", "-q", "-t", "2"], timeout=10).returncode == 0:
                break
            time.sleep(1)
        return ok, "Wi-Fi reset" if ok else "Wi-Fi couldn't be reset", clip("\n".join(log)), 0 if ok else 1
    if name == "repair-updates":
        env_steps = [("Finish interrupted installs", ["dpkg", "--configure", "-a"], 900),
                     ("Fix missing pieces", ["apt-get", "-y", "-o", "DPkg::Lock::Timeout=120", "-f", "install"], 900),
                     ("Refresh the update lists", ["apt-get", "-o", "DPkg::Lock::Timeout=120", "update"], 300)]
        ok, log = _steps(lambda argv, timeout=60: run(["env", "DEBIAN_FRONTEND=noninteractive"] + argv, timeout=timeout),
                         env_steps)
        return ok, "updates repaired" if ok else "updates still need attention", clip("\n".join(log)), 0 if ok else 1
    if name == "restart-printing":
        ok, log = _steps(run, [("Printing service restarted", ["systemctl", "restart", "cups.service"], 60)])
        r = run(["lpstat", "-p"], timeout=20)
        for line in r.stdout.splitlines():  # "printer Front_desk disabled since ..."
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "printer" and "disabled" in line:
                e = run(["cupsenable", parts[1]], timeout=20)
                log.append(f"== {parts[1]} turned back on: {'done' if e.returncode == 0 else 'failed'}")
        return ok, "printing restarted" if ok else "printing couldn't be restarted", clip("\n".join(log)), 0 if ok else 1
    # check-disk: read only.
    log = []
    df = run(["df", "-h", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay"], timeout=30)
    log += ["== Space", df.stdout.strip()]
    errors = run(["journalctl", "-k", "-p", "err", "--since", "-7d", "-q", "--no-pager", "-g",
                  "I/O error|EXT4-fs error|BTRFS error|nvme.*(error|timeout)|ata[0-9].*(error|failed)"], timeout=60)
    found = [line for line in errors.stdout.splitlines() if line.strip()]
    log += ["== Disk errors in the last 7 days", "\n".join(found[-50:]) or "none"]
    healthy = True
    if shutil.which("smartctl"):
        lsblk = run(["lsblk", "-dno", "NAME,TYPE"], timeout=20)
        for line in lsblk.stdout.splitlines():
            name_, _, kind = line.partition(" ")
            if kind.strip() != "disk":
                continue
            s = run(["smartctl", "-H", f"/dev/{name_}"], timeout=60)
            log += [f"== Health of /dev/{name_}", (s.stdout.strip().splitlines() or ["unknown"])[-1]]
            healthy = healthy and "FAILED" not in s.stdout
    ok = healthy and not found
    return True, "no problems found" if ok else "problems found: see the output", clip("\n".join(log)), 0 if ok else 1


def run_script(body: str, sha256: str, allowed: bool) -> tuple[bool, str, str, int]:
    """(ok, short result, output, exit code) for an organisation's script."""
    if not allowed:
        return False, "this organisation doesn't allow scripts", "", 2
    if hashlib.sha256(body.encode()).hexdigest() != sha256:
        return False, "the script isn't the one that was asked for", "", 2
    SCRIPT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="script-", suffix=".sh", dir=SCRIPT_DIR)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root",
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "KULAN_FLEET_SCRIPT": "1"}
        argv = ["/bin/bash", path]
        if Path("/run/systemd/system").is_dir() and shutil.which("systemd-run"):
            # Its own unit: everything it starts is stopped with it, and it can't outlive the time limit.
            argv = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--service-type=exec",
                    f"--unit=kulan-fleet-script-{os.path.basename(path)[7:-3]}",
                    "-p", f"RuntimeMaxSec={SCRIPT_TIMEOUT_S}"] + [f"--setenv={k}={v}" for k, v in env.items()] + argv
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=env, cwd="/", start_new_session=True)
        kept = bytearray()

        def drain() -> None:  # keeps the first part; the rest is read and dropped, so a chatty script can't fill memory
            for chunk in iter(lambda: p.stdout.read(65536), b""):
                if len(kept) < OUTPUT_MAX * 2:
                    kept.extend(chunk[:OUTPUT_MAX * 2 - len(kept)])

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        timed_out = False
        try:
            code = p.wait(timeout=SCRIPT_TIMEOUT_S + 30)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, 9)  # the script and everything it started
            p.wait()
            code, timed_out = 124, True
        reader.join(timeout=10)
        out = bytes(kept).decode("utf-8", "replace") + ("\n(stopped after 10 minutes)" if timed_out else "")
        return code == 0, "finished" if code == 0 else f"ended with code {code}", clip(out), code
    finally:
        Path(path).unlink(missing_ok=True)
