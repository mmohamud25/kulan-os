"""What the assistant knows about this computer right now, for questions like
"why is my laptop slow?" or "am I connected?". Read on this computer, in a
second or two, and only put in the model's instructions (nothing leaves the
computer). Every reading is optional: a missing tool or file just leaves its
line out.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
from pathlib import Path

TIMEOUT = 3


def _out(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, check=False,
                              env={**os.environ, "LC_ALL": "C.UTF-8"}).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _read(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return ""


def gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def memory() -> tuple[int, int]:
    fields = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _s, rest = line.partition(":")
        parts = rest.split()
        if parts:
            fields[key] = int(parts[0]) * 1024
    return fields.get("MemTotal", 0), fields.get("MemAvailable", 0)


def top_apps(count: int = 3) -> list[tuple[str, int]]:
    """The programs using the most memory, added up by name (Firefox's many
    processes count as one)."""
    totals: dict[str, int] = {}
    for line in _out(["ps", "-eo", "rss=,comm="]).splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            name = parts[1].strip()
            name = {"Isolated Web Co": "firefox-esr", "Web Content": "firefox-esr", "WebExtensions": "firefox-esr",
                    "RDD Process": "firefox-esr", "Socket Process": "firefox-esr", "Privileged Cont": "firefox-esr",
                    "Utility Process": "firefox-esr"}.get(name, name)
            totals[name] = totals.get(name, 0) + int(parts[0]) * 1024
    return sorted(totals.items(), key=lambda kv: -kv[1])[:count]


def busiest(count: int = 3) -> list[tuple[str, float]]:
    """Programs using the processor noticeably (not this one)."""
    rows = []
    for line in _out(["ps", "-eo", "pid=,pcpu=,comm=", "--sort=-pcpu"]).splitlines():
        parts = line.split(None, 2)
        try:
            pid, pct, name = int(parts[0]), float(parts[1]), parts[2].strip()
        except (IndexError, ValueError):
            continue
        if pid != os.getpid() and pct >= 5:
            rows.append((name, pct))
        if len(rows) >= count:
            break
    return rows


def battery() -> str:
    for supply in sorted(Path("/sys/class/power_supply").glob("*")):
        if _read(str(supply / "type")) == "Battery":
            capacity = _read(str(supply / "capacity"))
            state = _read(str(supply / "status")).lower()
            if capacity:
                return f"{capacity}% ({state or 'unknown'})"
    return ""


def network() -> str:
    if not shutil.which("nmcli"):
        return ""
    connectivity = _out(["nmcli", "-t", "-f", "CONNECTIVITY", "general"]) or "unknown"
    wifi = _out(["nmcli", "-t", "-f", "WIFI", "general"]) or "unknown"
    active = [line.split(":") for line in _out(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show",
                                                 "--active"]).splitlines()]
    names = [f"{n[0]} ({n[1].replace('802-11-wireless', 'Wi-Fi').replace('802-3-ethernet', 'cable')})"
             for n in active if len(n) >= 2 and n[1] not in ("loopback", "bridge")]
    words = {"full": "internet works", "none": "no internet", "limited": "limited (no internet)",
             "portal": "a Wi-Fi sign-in page is waiting", "unknown": "not checked"}
    return f"{words.get(connectivity, connectivity)}; Wi-Fi {wifi}; connected to: {', '.join(names) or 'nothing'}"


def sound() -> str:
    if not shutil.which("wpctl"):
        return ""
    volume = _out(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    if not volume:
        return "no speakers or headphones found"
    parts = volume.split()
    level = ""
    try:
        level = f"{round(float(parts[1]) * 100)}%"
    except (IndexError, ValueError):
        pass
    return f"volume {level}{', muted' if 'MUTED' in volume else ''}".strip()


def updates() -> str:
    try:
        data = json.loads(Path("/var/lib/kulan/updates.json").read_text())
        waiting = data.get("available") or []
    except (OSError, ValueError, AttributeError):
        return ""
    security = sum(1 for u in waiting if isinstance(u, dict) and u.get("security"))
    restart = Path("/run/kulan/restart-required").exists()
    text = f"{len(waiting)} waiting ({security} security)" if waiting else "up to date"
    return text + ("; a restart is needed to finish updating" if restart else "")


def snapshot() -> str:
    """A few short lines about this computer, now."""
    now = datetime.datetime.now()
    lines = [f"Now: {now:%A %-d %B %Y, %H:%M}"]
    rel = {}
    for line in _read("/etc/os-release").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            rel[key] = value.strip().strip('"')
    if rel:
        lines.append(f"System: {rel.get('PRETTY_NAME', 'Kulan OS')}")
    total, available = memory()
    if total:
        apps = ", ".join(f"{name} {gb(size)}" for name, size in top_apps() if size >= 100 * 1024 ** 2)
        lines.append(f"Memory: {gb(total - available)} used of {gb(total)}" + (f"; most used by {apps}" if apps else ""))
    cpu = busiest()
    lines.append("Busiest programs: " + (", ".join(f"{name} {pct:.0f}% of one core" for name, pct in cpu)
                                         if cpu else "nothing is using the processor much"))
    load = _read("/proc/loadavg").split()
    if load:
        lines.append(f"Processor load (1 min): {load[0]} on {os.cpu_count()} cores")
    try:
        disk = shutil.disk_usage("/")
        lines.append(f"Disk: {gb(disk.free)} free of {gb(disk.total)} ({disk.free * 100 // max(disk.total, 1)}% free)")
    except OSError:
        pass
    for label, value in (("Battery", battery()), ("Network", network()), ("Sound", sound()),
                         ("Updates", updates())):
        if value:
            lines.append(f"{label}: {value}")
    uptime = _read("/proc/uptime").split()
    if uptime:
        hours = float(uptime[0]) / 3600
        lines.append(f"On for: {hours:.0f} hours" if hours >= 1 else "On for: under an hour")
    return "\n".join(lines)


if __name__ == "__main__":
    print(snapshot())
