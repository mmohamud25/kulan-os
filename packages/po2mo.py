#!/usr/bin/python3
"""Compile a .po file to a .mo file (GNU gettext's format), with no tools beyond Python.

Only what Kulan's word changes need: msgid, msgstr and msgctxt, one line or
several, no plural forms.

    po2mo.py <in.po> <out.mo>
"""
from __future__ import annotations

import ast
import struct
import sys


def parse(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    current: dict[str, str] = {}
    key = None

    def finish() -> None:
        if "msgid" in current and current.get("msgstr"):
            msgid = current["msgid"]
            if "msgctxt" in current:
                msgid = current["msgctxt"] + "\x04" + msgid
            entries[msgid] = current["msgstr"]

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, _sp, rest = line.partition(" ")
        if word in ("msgctxt", "msgid", "msgstr"):
            if word in ("msgctxt", "msgid") and "msgstr" in current:
                finish()
                current = {}
            key = word
            current[key] = ast.literal_eval(rest)
        elif line.startswith('"') and key:
            current[key] += ast.literal_eval(line)
        else:
            raise ValueError(f"can't read: {line}")
    finish()
    return entries


def write(entries: dict[str, str], path: str) -> None:
    entries = dict(entries)
    entries.setdefault("", "Content-Type: text/plain; charset=UTF-8\n")
    keys = sorted(entries)
    ids = b"".join(k.encode() + b"\0" for k in keys)
    strs = b"".join(entries[k].encode() + b"\0" for k in keys)
    start = 7 * 4 + 16 * len(keys)
    table_ids, table_strs, offset = [], [], 0
    for k in keys:
        table_ids += [len(k.encode()), start + offset]
        offset += len(k.encode()) + 1
    offset = 0
    for k in keys:
        table_strs += [len(entries[k].encode()), start + len(ids) + offset]
        offset += len(entries[k].encode()) + 1
    head = struct.pack("Iiiiiii", 0x950412DE, 0, len(keys), 7 * 4, 7 * 4 + 8 * len(keys), 0, 0)
    with open(path, "wb") as out:
        out.write(head + struct.pack(f"{len(table_ids)}i", *table_ids) + struct.pack(f"{len(table_strs)}i", *table_strs)
                  + ids + strs)


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as po:
        write(parse(po.read()), sys.argv[2])
