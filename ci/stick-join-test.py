#!/usr/bin/env python3
"""An organisation's USB stick, from Kulan USB Maker to Kulan Fleet:

  1. the installer engine finds the settings Kulan USB Maker put on a stick
     (and refuses damaged or odd ones, and finds none on a plain stick);
  2. kulan-fleet-autojoin joins with them, once, and stops at the right times.

  python3 ci/stick-join-test.py [STICK]

STICK: a device or file Kulan USB Maker wrote (its helper, with settings
{"fleet": {"server": "https://fleet.example/functions/v1/fleet", "code": "ABCD-EFGH-JK2M"}}).
Without it, a small stand-in is made here, laid out the same way.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIB = 1 << 20


def load(path: Path, name: str) -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


engine = load(ROOT / "packages/kulan-installer/root/usr/lib/kulan/installer-engine", "engine")
autojoin = load(ROOT / "packages/kulan-base/root/usr/lib/kulan/fleet-autojoin", "autojoin")
work = Path(tempfile.mkdtemp(prefix="stick-test-"))
failed = []


def check(what: str, ok: bool, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + what + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failed.append(what)


def block(settings: dict, damage: bool = False) -> bytes:
    """What Kulan USB Maker writes (writer.rs config_block)."""
    body = json.dumps(settings, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest().encode()
    if damage:
        body = body.replace(b"ABCD", b"ABCE")
    return (b"KULAN-STICK-CONFIG 1\n" + digest + b"\n" + body + b"\n").ljust(4096, b"\0")


def stand_in(settings_block: bytes | None, gap_mib: int = 0) -> str:
    """A stick: an ISO 9660 volume of 3 MiB and a bit, then the settings."""
    path = work / f"stick-{len(list(work.iterdir()))}.img"
    blocks = (3 * MIB + 6144) // 2048
    pvd = bytearray(2048)
    pvd[0:7] = b"\x01CD001\x01"
    pvd[80:84] = blocks.to_bytes(4, "little")
    pvd[84:88] = blocks.to_bytes(4, "big")
    pvd[128:130] = (2048).to_bytes(2, "little")
    pvd[40:72] = b"Kulan OS 2.10 amd64".ljust(32)
    pvd[813:830] = b"2026102712000000\0"
    with open(path, "wb") as f:
        f.truncate(80 * MIB)
        f.seek(16 * 2048)
        f.write(pvd)
        if settings_block:
            f.seek(4 * MIB + gap_mib * MIB)
            f.write(settings_block)
    return str(path)


IMAGE = {"volume": "Kulan OS 2.10 amd64", "created": "2026102712000000"}
GOOD = {"fleet": {"server": "https://fleet.example/functions/v1/fleet", "code": "ABCD-EFGH-JK2M"}, "image": IMAGE}
WANT = {"server": "https://fleet.example/functions/v1/fleet", "code": "ABCD-EFGH-JK2M"}

# 1. The installer engine reading the stick.
if len(sys.argv) > 1:
    got = engine.stick_settings(sys.argv[1])
    check("settings on a stick Kulan USB Maker wrote", got == WANT, got)
check("settings found", engine.stick_settings(stand_in(block(GOOD))) == WANT)
check("settings found further on (a bigger image)", engine.stick_settings(stand_in(block(GOOD), gap_mib=20)) == WANT)
check("none on a plain stick", engine.stick_settings(stand_in(None)) is None)
check("damaged settings refused", engine.stick_settings(stand_in(block(GOOD, damage=True))) is None)
odd = [{"fleet": {"server": "http://fleet.example/x", "code": "ABCD-EFGH-JK2M"}, "image": IMAGE},
       {"fleet": {"server": "https://fleet.example/x", "code": "ABCD-EFGH"}, "image": IMAGE},
       {"fleet": {"server": "https://fleet.example/x", "code": "ABCD-EFGH-JK2M; rm -rf /"}, "image": IMAGE},
       {"other": 1, "image": IMAGE}]
check("odd settings refused", all(engine.stick_settings(stand_in(block(o))) is None for o in odd))
lower = {"fleet": {"server": "https://fleet.example/functions/v1/fleet", "code": "abcd-efgh-jk2m"}, "image": IMAGE}
other_image = {**GOOD, "image": {"volume": "Kulan OS 2.9 amd64", "created": "2026101012000000"}}
check("settings for another Kulan OS (written over later) ignored", engine.stick_settings(stand_in(block(other_image))) is None)
check("settings without the image they're for ignored", engine.stick_settings(stand_in(block({"fleet": GOOD["fleet"]}))) is None)
deep = stand_in(None)
with open(deep, "r+b") as f:
    body = b"[" * 1990 + b"]" * 1990  # fits in the 4 KiB block; deeper than Python parses
    f.seek(4 * MIB)
    f.write(b"KULAN-STICK-CONFIG 1\n" + hashlib.sha256(body).hexdigest().encode() + b"\n" + body + b"\n")
check("a deliberately odd block (deeply nested) is refused, not a crash", engine.stick_settings(deep) is None)
check("a lower-case code is taken", engine.stick_settings(stand_in(block(lower))) == WANT)
check("not an ISO: nothing", engine.stick_settings(str(work / "nothing")) is None)

# 2. Joining, with kulan-fleet stood in for.
autojoin.JOIN = work / "fleet-join.json"
autojoin.CONFIG = work / "fleet.json"
calls = []


class Done:
    def __init__(self, code: int, out: str) -> None:
        self.returncode, self.stdout, self.stderr = code, out, ""


def fake(reply_code: int, out: str, joins: bool = False):
    def run(argv, input=None, **_kw):
        calls.append((argv, input))
        if joins:
            autojoin.CONFIG.write_text("{}")
        return Done(reply_code, out)
    return run


def reset(settings=WANT) -> None:
    calls.clear()
    autojoin.CONFIG.unlink(missing_ok=True)
    autojoin.JOIN.write_text(json.dumps(settings))


reset()
autojoin.subprocess.run = fake(1, "Couldn't join: couldn't reach the server: [Errno -3] Temporary failure")
autojoin.main()
check("offline: tried, kept for next time", autojoin.JOIN.exists() and calls[0] == (
    ["/usr/bin/kulan-fleet", "join", WANT["server"]], "ABCD-EFGH-JK2M\n"), calls)
reset()
autojoin.subprocess.run = fake(0, "Joined Kulan", joins=True)
autojoin.main()
check("joined: the code removed", not autojoin.JOIN.exists() and len(calls) == 1)
reset()
autojoin.subprocess.run = fake(1, "Couldn't join: the server said 403 Forbidden")
autojoin.main()
check("code refused: stops trying", not autojoin.JOIN.exists())
reset()
autojoin.CONFIG.write_text("{}")
autojoin.main()
check("already joined: nothing tried, the code removed", not calls and not autojoin.JOIN.exists())
reset({"server": "http://fleet.example", "code": "ABCD-EFGH-JK2M"})
autojoin.main()
check("odd settings: nothing tried", not calls and not autojoin.JOIN.exists())
reset({**WANT, "tries": 2000})
autojoin.main()
check("after 2,000 tries: stops trying", not calls and not autojoin.JOIN.exists())
reset()
old = time.time() - 400 * 86400  # a clock far off at install: not a reason to stop
os.utime(autojoin.JOIN, (old, old))
autojoin.subprocess.run = fake(1, "Couldn't join: the server said 429 Too Many Requests")
autojoin.main()
check("busy server (429) or an old date: kept, tries counted",
      autojoin.JOIN.exists() and json.loads(autojoin.JOIN.read_text()).get("tries") == 1 and len(calls) == 1)
check("still only root can read it", oct(autojoin.JOIN.stat().st_mode & 0o777) == "0o600")

print("stick test: " + ("every check passed" if not failed else f"{len(failed)} failed"))
sys.exit(1 if failed else 0)
