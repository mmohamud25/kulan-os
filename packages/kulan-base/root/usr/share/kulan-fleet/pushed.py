"""Wi-Fi networks and printers a group gives its computers (Kulan Fleet 2.9b).

Each report's reply lists them ("networks", "printers"); this keeps the
computer to that list: what Kulan Fleet added and is no longer listed is
taken away again, and nothing anyone added themselves is touched.

  Wi-Fi     /etc/NetworkManager/system-connections/kulan-fleet-<id>.nmconnection
            (root only, as NetworkManager wants; the password never on a command line)
  Printers  CUPS queues made with lpadmin, driverless (IPP Everywhere); the ones
            Kulan Fleet made are kept in /var/lib/kulan/fleet-printers.json
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

CONNECTIONS = Path("/etc/NetworkManager/system-connections")
PREFIX = "kulan-fleet-"
PRINTERS = Path("/var/lib/kulan/fleet-printers.json")
ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PRINTER_URI = re.compile(r"^(ipps?|https?)://[A-Za-z0-9.:\[\]_-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/%+-]*)?$")
NAMESPACE = uuid.UUID("6f1c2a7e-2d47-4b8e-9a51-2f0c7c1d9e33")


# --- Wi-Fi ---------------------------------------------------------------------------------
def clean_networks(raw) -> list[dict]:
    out, seen = [], set()
    for n in raw if isinstance(raw, list) else []:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not ID.match(n["id"]):
            continue
        ssid = n.get("ssid")
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid) or ssid in seen:
            continue
        security, password = n.get("security"), n.get("password")
        if n.get("unavailable") is True and security in ("open", "wpa-psk"):
            # The server couldn't open its password just now: keep what this computer has.
            seen.add(ssid)
            out.append({"id": n["id"], "ssid": ssid, "security": security, "password": None, "hidden": n.get("hidden") is True,
                        "unavailable": True})
            continue
        if security == "open":
            password = None
        elif security != "wpa-psk" or not isinstance(password, str) or not (
                re.fullmatch(r"[\x20-\x7e]{8,63}", password) or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
            continue
        seen.add(ssid)
        out.append({"id": n["id"], "ssid": ssid, "security": security, "password": password, "hidden": n.get("hidden") is True})
    return out[:50]


def _escape(value: str) -> str:
    """A value as GKeyFile (NetworkManager's keyfile) reads it back exactly."""
    out = value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    # \s for a space at either end: GLib reads it back anywhere, and nothing trims it.
    if out.startswith(" "):
        out = "\\s" + out[1:]
    if out.endswith(" "):
        out = out[:-1] + "\\s"
    return out


def keyfile(n: dict) -> str:
    name = "".join(c if c.isprintable() and c not in "[]=" else " " for c in n["ssid"]).strip() or "Wi-Fi"
    lines = ["[connection]", f"id={_escape(name)}", f"uuid={uuid.uuid5(NAMESPACE, n['id'])}", "type=wifi",
             "autoconnect=true", "", "[wifi]", "mode=infrastructure",
             # As bytes: any name, nothing to escape.
             "ssid=" + "".join(f"{b};" for b in n["ssid"].encode()), f"hidden={'true' if n['hidden'] else 'false'}", ""]
    if n["security"] == "wpa-psk":
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={_escape(n['password'])}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "addr-gen-mode=default", "method=auto", ""]
    return "\n".join(lines)


def apply_networks(raw, run, folder: Path | None = None) -> dict[str, str]:
    folder = folder or CONNECTIONS
    wanted = {n["id"]: n for n in clean_networks(raw)}
    status, changed = {}, False
    if not folder.is_dir():
        return {nid: "NetworkManager isn't installed" for nid in wanted}
    for nid, n in wanted.items():
        path = folder / f"{PREFIX}{nid}.nmconnection"
        if n.get("unavailable"):
            status[nid] = "added" if path.exists() else "waiting for the server"
            continue
        text = keyfile(n)
        try:
            same = path.read_text() == text
        except OSError:
            same = False
        if not same:
            tmp = folder / f".{PREFIX}{nid}.tmp"
            tmp.unlink(missing_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
            os.replace(tmp, path)
            changed = True
        status[nid] = "added"
    for f in folder.glob(f"{PREFIX}*.nmconnection"):
        if f.name[len(PREFIX):-len(".nmconnection")] not in wanted:
            f.unlink(missing_ok=True)
            changed = True
    if changed:
        run(["nmcli", "connection", "reload"], timeout=30)
    return status


# --- printers ------------------------------------------------------------------------------
def clean_printers(raw) -> list[dict]:
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not ID.match(p["id"]):
            continue
        name, uri = p.get("name"), p.get("uri")
        if not isinstance(name, str) or not name.strip() or len(name) > 60 or any(ord(c) < 32 for c in name):
            continue
        if not isinstance(uri, str) or len(uri) > 300 or not PRINTER_URI.match(uri):
            continue
        out.append({"id": p["id"], "name": name.strip(), "uri": uri})
    return out[:50]


def queue_name(p: dict) -> str:
    """A CUPS queue name: letters, digits and _, from the printer's name, kept unique by its id."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", p["name"]).strip("_")[:40] or "Printer"
    return f"{slug}_{p['id'][:4]}"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


RETRY_S = 3600  # a printer that couldn't be reached is tried again an hour later, not at every report


def _save(path: Path, data: dict) -> None:
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".kulan-new")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def apply_printers(raw, run, state: Path | None = None) -> dict[str, str]:
    state = state or PRINTERS
    tried_path = state.with_name(state.stem + "-tried.json")
    made = _load(state)  # {printer id: {"queue", "uri", "name"}}
    tried = _load(tried_path)  # {printer id: when it last couldn't be added}
    wanted = {p["id"]: p for p in clean_printers(raw)}
    status = {}
    if wanted and run(["lpadmin", "-h"], timeout=10).returncode == 127:
        return {pid: "printing isn't installed" for pid in wanted}
    now = time.time()
    for pid, p in wanted.items():
        queue = queue_name(p)
        old = made.get(pid)
        if old and old.get("uri") == p["uri"] and old.get("name") == p["name"] and \
                run(["lpstat", "-p", queue], timeout=10).returncode == 0:
            status[pid] = "added"
            continue
        if isinstance(tried.get(pid), (int, float)) and now - tried[pid] < RETRY_S:
            status[pid] = "couldn't reach the printer; trying again within the hour"
            continue
        if old and old.get("queue") != queue and _remove_queue(run, old["queue"]):
            made.pop(pid, None)
        r = run(["lpadmin", "-p", queue, "-E", "-v", p["uri"], "-m", "everywhere", "-D", p["name"],
                 "-L", "Added by your organisation"], timeout=30)
        if r.returncode == 0:
            made[pid] = {"queue": queue, "uri": p["uri"], "name": p["name"]}
            tried.pop(pid, None)
            status[pid] = "added"
        else:
            tried[pid] = now
            status[pid] = "couldn't reach the printer; trying again within the hour"
    for pid in [k for k in made if k not in wanted]:
        if _remove_queue(run, made[pid]["queue"]):
            del made[pid]  # kept, and tried again next time, if CUPS couldn't remove it now
    for pid in [k for k in tried if k not in wanted]:
        del tried[pid]
    _save(state, made)
    _save(tried_path, tried)
    return status


def _remove_queue(run, queue: str) -> bool:
    """Whether the queue is gone (removed now, or not there)."""
    return run(["lpadmin", "-x", queue], timeout=30).returncode == 0 or run(["lpstat", "-p", queue], timeout=10).returncode != 0


def remove_all(run, folder: Path | None = None, state: Path | None = None) -> None:
    """Leaving the organisation: its networks and printers go; people's own stay."""
    apply_networks([], run, folder)
    apply_printers([], run, state)  # the state file stays only for queues CUPS couldn't remove yet
