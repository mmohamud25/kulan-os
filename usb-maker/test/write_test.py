#!/usr/bin/env python3
"""Kulan USB Maker's writing part, for real, on a stand-in USB stick (a loop
device, a RAM disk or a virtual disk), as an administrator:

  python3 write_test.py BINARY DRIVE_ID DEVICE_PATH [SYSTEM_DRIVE_ID]

The binary must be built with the test-devices feature, and run with
KULAN_USB_MAKER_TEST_DEVICES=DRIVE_ID, so the stand-in counts as a USB stick.
Checks: an image written and read back the same; the organisation's
settings after it; the area after it cleared of an earlier stick's; a stop
honoured; and SYSTEM_DRIVE_ID (the computer's own disk) refused.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

MIB = 1 << 20
BINARY, DRIVE, DEVICE = sys.argv[1], sys.argv[2], sys.argv[3]
SYSTEM = sys.argv[4] if len(sys.argv) > 4 else None
work = tempfile.mkdtemp(prefix="kum-test-")


def helper(image, drive, config=None, stop_after=None):
    status, stop = os.path.join(work, "status"), os.path.join(work, "stop")
    for f in (status, stop):
        if os.path.exists(f):
            os.remove(f)
    args = [BINARY, "--write-helper", image, drive, status, stop] + ([config] if config else [])
    proc = subprocess.Popen(args)
    if stop_after:
        while not os.path.exists(status) or "write" not in open(status).read():
            time.sleep(0.05)
        open(stop, "w").write("stop")
    code = proc.wait(timeout=1800)
    return code, json.loads(open(status).read()) if os.path.exists(status) else {}


def read(offset, length):
    """From the device, in whole 4 KiB blocks (raw disks need them)."""
    start = offset - offset % 4096
    end = -(-(offset + length) // 4096) * 4096
    with open(DEVICE, "rb", buffering=0) as f:
        f.seek(start)
        data = b""
        while len(data) < end - start:
            chunk = f.read(min(4 * MIB, end - start - len(data)))
            if not chunk:
                break
            data += chunk
    return data[offset - start:offset - start + length]


def fail(text):
    print(f"FAIL {text}")
    sys.exit(1)


# An "ISO" of 200 MiB and a bit (not a whole number of megabytes or sectors).
image = os.path.join(work, "kulan.iso")
size = 200 * MIB + 1234
with open(image, "wb") as f:
    f.write(os.urandom(size))
digest = hashlib.sha256(open(image, "rb").read()).hexdigest()
offset = -(-size // MIB) * MIB

# What an earlier organisation's stick left after its Kulan OS.
with open(DEVICE, "r+b", buffering=0) as f:
    f.seek(offset + 8 * MIB)
    f.write(b"KULAN-STICK-CONFIG 1\n" + b"\0" * (4096 - 21))

# 1. With an organisation's settings.
config = os.path.join(work, "org.json")
open(config, "w").write(json.dumps({"fleet": {"server": "https://fleet.example/functions/v1/fleet", "code": "ABCD-EFGH-JK2M"}}))
code, status = helper(image, DRIVE, config)
if code != 0 or not status.get("finished"):
    fail(f"writing: exit {code}, {status}")
if hashlib.sha256(read(0, size)).hexdigest() != digest:
    fail("what's on the stick isn't the image")
block = read(offset, 4096)
lines = block.rstrip(b"\0").split(b"\n")
if lines[0] != b"KULAN-STICK-CONFIG 1" or hashlib.sha256(lines[2]).hexdigest().encode() != lines[1]:
    fail(f"the settings block: {block[:120]!r}")
if json.loads(lines[2])["fleet"]["code"] != "ABCD-EFGH-JK2M":
    fail("the settings")
if read(offset + 8 * MIB, 4096) != b"\0" * 4096:
    fail("the earlier stick's settings are still there")
print("ok: written, read back, with the organisation's settings, earlier ones cleared")

# 2. Without: the settings block is cleared too.
code, status = helper(image, DRIVE)
if code != 0 or read(offset, 4096) != b"\0" * 4096:
    fail(f"writing without settings: exit {code}, {status}")
print("ok: without settings, none left")

# 3. Stop.
code, status = helper(image, DRIVE, stop_after=True)
if code != 3 or status.get("error") != "stopped":
    fail(f"stopping: exit {code}, {status}")
print("ok: stopped when asked")

# 4. Never the computer's own disk (nor one that isn't there).
for other in [d for d in (SYSTEM, "nonexistent9") if d]:
    code, status = helper(image, other)
    if code == 0 or "isn't plugged in" not in status.get("error", ""):
        fail(f"{other} wasn't refused: exit {code}, {status}")
print("ok: the computer's own disk refused")
print("write test: every check passed")
