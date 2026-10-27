# Kulan USB Maker

A small app for Macs and Windows PCs that makes a Kulan OS USB stick: pick
the stick, click Make. It downloads the newest Kulan OS, uses it only if its
SHA-256 is in a `SHA256SUMS` signed with the update channel's key (built in
from `packages/kulan-archive-keyring.gpg`), writes it as an administrator,
reads it all back to check it, and shows the start-up menu key for the
computer's make. Only USB and SD card drives are ever listed.

For an organisation, a Kulan Fleet join code goes in a 4 KiB block on the
stick after Kulan OS (`KULAN-STICK-CONFIG 1`, its SHA-256, the settings as
JSON, on the first megabyte boundary after the image; the 64 MiB there are
cleared first). Kulan Installer copies it to the new system, and
`kulan-fleet-autojoin` joins once the computer is online.

- `src-tauri/`: the Rust side (Tauri 2). `--write-helper` is the part that
  runs as an administrator (macOS asks for the password through
  `osascript`, Windows through its "allow changes" prompt).
- `ui/`: the window (plain HTML, CSS and JavaScript, no build step).
- `test/ui.test.cjs`: every screen in a browser, with Tauri stood in for.
- `test/write_test.py`: writing for real to a stand-in stick; needs a build
  with `--features test-devices`, which no released app has.

Build: `npm ci && npx tauri build` (a .dmg on a Mac, an installer .exe on
Windows). `.github/workflows/usb-maker.yml` builds and tests both. The apps
aren't signed yet, so macOS and Windows warn the first time they're opened.
