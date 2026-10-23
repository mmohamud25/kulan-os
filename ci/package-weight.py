#!/usr/bin/python3
"""How much each of the biggest installed packages really adds to the ISO.

dpkg's Installed-Size is uncompressed; the live system is xz-compressed, so a
230 MB package may cost 15 MB of download or 200. This extracts each
package's files from the squashfs and compresses them the way the image is
(xz, 1 MB blocks), to show where trimming would actually help.

  ci/package-weight.py SQUASHFS STATUS [COUNT]

Prints one line: "pkg ~N MB (of M MB); ...", heaviest in the ISO first.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

why = import_module("why-installed")


def main():
    squashfs, status = sys.argv[1], sys.argv[2]
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 25
    pkgs = why.parse(status)
    biggest = sorted(pkgs, key=lambda n: -int(pkgs[n].get("Installed-Size", "0") or 0))[:count]

    listing = subprocess.run(["unsquashfs", "-lls", "-n", squashfs], capture_output=True, text=True).stdout
    files = set()
    for line in listing.splitlines():
        m = re.match(r"^([-l])\S+\s+\S+\s+\d+\s+\S+\s+\S+\s+squashfs-root(/.*?)(?: -> .*)?$", line)
        if m:
            files.add(m.group(2))

    work = tempfile.mkdtemp(prefix="weight-")
    try:
        info = os.path.join(work, "info")
        lists = [f"var/lib/dpkg/info/{n}.list" for n in biggest]
        lists += [f"var/lib/dpkg/info/{n}:{a}.list" for n in biggest for a in ("amd64", "arm64", "all")]
        subprocess.run(["unsquashfs", "-q", "-n", "-d", info, squashfs, *lists], capture_output=True)
        results = []
        for name in biggest:
            paths = []
            for suffix in ("", ":amd64", ":arm64", ":all"):
                p = os.path.join(info, "var/lib/dpkg/info", f"{name}{suffix}.list")
                if os.path.exists(p):
                    paths += [x.strip() for x in open(p, errors="replace") if x.strip() in files]
            if not paths:
                continue
            ef = os.path.join(work, "ef")
            with open(ef, "w") as fh:
                fh.write("\n".join(x.lstrip("/") for x in paths) + "\n")
            out = os.path.join(work, "pkg")
            subprocess.run(["unsquashfs", "-q", "-n", "-d", out, "-ef", ef, squashfs], capture_output=True)
            tar = subprocess.Popen(["tar", "-C", out, "-cf", "-", "."], stdout=subprocess.PIPE)
            xz = subprocess.run(["xz", "-T0", "-6", "--block-size=1MiB", "-c"], stdin=tar.stdout,
                                capture_output=True)
            tar.wait()
            shutil.rmtree(out, ignore_errors=True)
            size = int(pkgs[name].get("Installed-Size", "0") or 0) // 1024
            results.append((len(xz.stdout), name, size))
        results.sort(reverse=True)
        print("; ".join(f"{n} ~{c / 1048576:.0f} MB (of {s} MB)" for c, n, s in results))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
