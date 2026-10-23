#!/usr/bin/python3
"""For the biggest installed packages, say which installed packages pull them
in (Depends/Pre-Depends, or Recommends), so the ISO can be kept small.

  ci/why-installed.py STATUS [COUNT]   (STATUS = a dpkg status file)

Prints one line: "pkg 230 MB <- a, b (rec: c); pkg2 ...".
"""
import re
import sys


def parse(path):
    pkgs = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for block in fh.read().split("\n\n"):
            fields, key = {}, None
            for line in block.splitlines():
                if line[:1] in (" ", "\t") and key:
                    fields[key] += " " + line.strip()
                elif ":" in line:
                    key, _, value = line.partition(":")
                    fields[key] = value.strip()
            if "Package" in fields and "installed" in fields.get("Status", ""):
                pkgs[fields["Package"]] = fields
    return pkgs


def names(field):
    """'a (>= 1), b | c:any' -> {'a', 'b', 'c'}"""
    out = set()
    for alt in re.split(r"[,|]", field or ""):
        name = alt.strip().split(" ")[0].split(":")[0]
        if name:
            out.add(name)
    return out


def main():
    pkgs = parse(sys.argv[1])
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    provides = {}
    for name, f in pkgs.items():
        for virt in names(f.get("Provides")):
            provides.setdefault(virt, set()).add(name)
    hard, soft = {}, {}
    for name, f in pkgs.items():
        for kind, target in (("Depends", hard), ("Pre-Depends", hard), ("Recommends", soft)):
            for dep in names(f.get(kind)):
                for real in {dep} | provides.get(dep, set()):
                    target.setdefault(real, set()).add(name)
    biggest = sorted(pkgs, key=lambda n: -int(pkgs[n].get("Installed-Size", "0") or 0))[:count]
    parts = []
    for name in biggest:
        size = int(pkgs[name].get("Installed-Size", "0") or 0) // 1024
        dep = sorted(hard.get(name, set()) - {name})
        rec = sorted(soft.get(name, set()) - {name} - set(dep))
        why = ", ".join(dep[:4]) + (" …" if len(dep) > 4 else "")
        if rec:
            why += (" " if why else "") + "(rec: " + ", ".join(rec[:4]) + (" …" if len(rec) > 4 else "") + ")"
        parts.append(f"{name} {size} MB <- {why or 'nothing (listed directly)'}")
    print("; ".join(parts))


if __name__ == "__main__":
    main()
