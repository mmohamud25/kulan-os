#!/usr/bin/env python3
"""Tests kulan-signed-start's start-up files on the EFI partition (2.13), with
a stand-in EFI partition and stand-ins for ukify, sbverify, dracut and
update-grub (no VM): the file that worked is kept to fall back on until the
new one has worked, a failed build leaves everything as it was, files after
an undo, a removed kernel, unchanged files not rewritten, and "off" keeping
signed start-up when GRUB's menu wasn't made.

  python3 -I -B ci/signed-start-files-test.py [path/to/kulan-signed-start]
"""
import importlib.machinery
import importlib.util
import sys
import tempfile
import types
from pathlib import Path

SCRIPT = sys.argv[1] if len(sys.argv) > 1 else "/usr/bin/kulan-signed-start"
loader = importlib.machinery.SourceFileLoader("kss", SCRIPT)
spec = importlib.util.spec_from_loader("kss", loader)
kss = importlib.util.module_from_spec(spec)
loader.exec_module(kss)

tmp = Path(tempfile.mkdtemp())
kss.ESP = tmp / "esp"
boot = tmp / "boot"
(kss.ESP / "EFI/Linux").mkdir(parents=True)
(kss.ESP / "loader").mkdir()
boot.mkdir()
kss.CMDLINE = tmp / "cmdline"
kss.CMDLINE.write_text("root=UUID=1 ro quiet\n")
kss.cmdline = lambda: "root=UUID=1 ro quiet"
kss.names = lambda: ("x64", "X64", "x86_64")
V1, V2 = "6.12.48+deb13-amd64", "6.12.57+deb13-amd64"
kss.kernels = lambda: [V1, V2]
kss.esp_free_mb = lambda: 10_000
LINUX = kss.ESP / "EFI/Linux"

calls: list[list[str]] = []
fail = {"ukify": False, "update-grub": False}


def fake_run(argv, input_text=None, env=None, timeout=None):
    calls.append(argv)
    out = next((a.split("=", 1)[1] for a in argv if a.startswith("--output=")), None)
    rc = 0
    if argv[0] == "ukify":
        if fail["ukify"]:
            rc = 1
        elif out:
            Path(out).write_text("uki " + next(a for a in argv if a.startswith("--uname=")))
    if argv[0] == "update-grub":
        rc = 1 if fail["update-grub"] else 0
    return types.SimpleNamespace(returncode=rc, stdout="", stderr="ukify said no" if rc else "")


kss.run = fake_run
real_path = kss.Path


def boot_path(p, *rest):  # /boot/vmlinuz-*, /boot/initrd.img-*: in the stand-in /boot
    s = str(p)
    if s.startswith("/boot/") and not s.startswith("/boot/efi"):
        return real_path(str(boot) + s[len("/boot"):], *rest)
    return real_path(p, *rest)


kss.Path = boot_path
for v in (V1, V2):
    (boot / f"vmlinuz-{v}").write_text("k")
    (boot / f"initrd.img-{v}").write_text("i")


def files() -> list[str]:
    return sorted(f.name for f in LINUX.iterdir())


def check(cond: bool, what: str) -> None:
    if not cond:
        print(f"signed start-up files: FAILED: {what}: {files()}")
        sys.exit(1)


# First files: counted, nothing to fall back on yet.
kss.rebuild()
check(files() == [f"kulan-{V1}+3.efi", f"kulan-{V2}+3.efi"], "first files")
check((kss.ESP / "loader/loader.conf").exists() is False, "loader.conf made by rebuild (only fixed)")
# They worked (boot-ok's rename).
for v in (V1, V2):
    (LINUX / f"kulan-{v}+3.efi").rename(LINUX / f"kulan-{v}.efi")
# Signed again (kulan-tpm, an update): the newest kernel's file that worked is kept
# to fall back on (systemd-boot starts the newest); an older kernel is a fallback itself.
kss.rebuild()
check(files() == sorted([f"kulan-{V1}+3.efi", f"kulan-{V2}+3.efi", f"kulan-{V2}~prev.efi"]),
      "kept to fall back on, for the newest kernel only")
check(kss.version_key("6.12.9") < kss.version_key("6.12.10"), "versions in their natural order")
check(kss.uki_version(f"kulan-{V1}~prev.efi") == V1 and kss.uki_version(f"kulan-{V1}+2-1.efi") == V1, "versions")
# Again, before the new ones worked: the one kept is still the one that worked.
(LINUX / f"kulan-{V2}+3.efi").rename(LINUX / f"kulan-{V2}+1-2.efi")
kss.rebuild(V2)
check(f"kulan-{V2}~prev.efi" in files() and f"kulan-{V2}+1-2.efi" not in files() and f"kulan-{V2}+3.efi" in files(),
      "untested file replaced, the one that worked kept")
# A build that fails changes nothing.
before = files()
fail["ukify"] = True
try:
    kss.rebuild(V1)
    check(False, "a failed build reported success")
except RuntimeError:
    pass
fail["ukify"] = False
check(files() == before, "a failed build changed the files")
check(not list(LINUX.glob("*.new")), "temporary file left")
# Short of room: other kernels' files kept to fall back on go first.
(LINUX / f"kulan-{V1}+3.efi").rename(LINUX / f"kulan-{V1}.efi")
kss.esp_free_mb = lambda: 10
kss.rebuild(V1)
check(files() == sorted([f"kulan-{V1}+3.efi", f"kulan-{V2}+3.efi"]), "low on room")
kss.esp_free_mb = lambda: 10_000
# After an undo: nothing of the undone system's kept, not even the files that worked.
for v in (V1, V2):
    (LINUX / f"kulan-{v}+3.efi").rename(LINUX / f"kulan-{v}.efi")
(LINUX / f"kulan-{V2}~prev.efi").write_text("old")
kss.rebuild(after_undo=True)
check(files() == sorted([f"kulan-{V1}+3.efi", f"kulan-{V2}+3.efi"]), "after undo")
# A removed kernel: all its files go.
kss.remove_kernel(V1)
check(not [f for f in files() if V1 in f], "removed kernel")
# loader.conf: written once, never rewritten when unchanged.
kss.write_file(kss.ESP / "loader/loader.conf", kss.LOADER_CONF)
mtime = (kss.ESP / "loader/loader.conf").stat().st_mtime_ns
kss.fix_loader_conf()
check((kss.ESP / "loader/loader.conf").stat().st_mtime_ns == mtime, "unchanged loader.conf rewritten")
(kss.ESP / "loader/loader.conf").write_text("default kulan-*\n")
kss.fix_loader_conf()
check((kss.ESP / "loader/loader.conf").read_text() == kss.LOADER_CONF, "old loader.conf not corrected")
# "off" with GRUB's menu not made: signed start-up stays on, nothing removed.
kss.need_root = lambda: None
kss.tpm_on = lambda: False
kss.arch = lambda: "amd64"
kss.load_state = lambda: {"phase": "on"}
saved = []
kss.save_state = saved.append
kss.kulan_entry = lambda: "0003"
first = []
kss.put_first = first.append
kss.say = lambda _t: None
(kss.ESP / "EFI/kulan").mkdir(parents=True)
fail["update-grub"] = True
before = files()
rc = kss.turn_off()
check(rc == 1 and files() == before and (kss.ESP / "EFI/kulan").is_dir() and not saved, "off without GRUB's menu")
check(first == ["0003"], "Kulan OS not put first again")
print("signed start-up files: every case passed")
