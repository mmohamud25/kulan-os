# Kulan OS 2.13: polish and hardening

No new features. Everything found by the 2.13 review of the whole code base
(security, Fleet server, apps, updates/start-up/installer, wording), checked
against the code before being listed. "Every app looks like Kulan" moves to
2.14.

Order: must-fix first, then the bugs people would notice, then wording.
Each fix gets a test where the risky path had none.

## Must fix

### Undo and start-up
- **U1** ARM VMs: the undo start-up passes `kulan.undo-update` on to the
  fast restart (kexec copies /proc/cmdline), so the undo runs again at every
  restart. fast-restart drops one-shot options; undo-at-start keeps a done
  note per start.
- **U2** kulan-signed-start deletes the file it replaces (the last one known
  to work) before the new one has started; `rebuild()` does that for every
  kernel. Keep the previous file until boot-ok blesses the new one; never
  leave a kernel with no known-good file.
- **U3** Files on the EFI partition (UKIs, loader.conf, BOOT*.CSV) written
  without fsync or in place: temp file, fsync, rename, fsync the folder;
  unchanged files not rewritten.
- **U4** `kulan-signed-start off` ignores update-grub failing and then
  removes the working start-up: check grub.cfg first.
- **U5** Undo on a nearly full disk: no safety point, no space check for the
  restore, set -e stops it half done. Refuse unless the safety point was made.
- **U6** Undo takes the newest "Before installing updates" point whatever its
  age; skipped points (low space, lock) make it go back weeks. Record which
  point protects the last update; offer undo only for that one, with its
  date; enforce_cap never deletes it.
- **U7** Undo brings back old accounts, passwords, sudo membership, Fleet
  enrolment, lost mode, a pending erase, firewall and USB rules, and
  signed-start.json (GRUB comes back). Keep identity and security state
  across an undo; refuse undo at start while lost mode or an erase is
  pending.
- **U8** kulan-signed-start.service can run during an undo start-up: not on
  `kulan.undo-update`, after kulan-undo-at-start.
- **U9** (found by the 2.13 CI run, also in 2.12) With signed start-up, a new
  signed file (still counted: after an update, TPM, any rebuild) starts
  "Undo the last update" by itself: systemd-boot 257.13 drops "@undo" from
  the entry's ID while the file has its try counter, and the undo entry was
  listed first. The undo entry gets its own os-release sort key so it always
  comes after every normal entry; files made again when updating from 2.12.

### Updates
- **D1** First restore point on a small disk (20 GB installs) leaves no room
  for updates, for good. Estimate the point's size before taking it; skip
  the point rather than the updates; say so in Software Update.
- **D2** /boot and the EFI partition are never checked for space, and old
  kernels are never removed. Check them; remove kernels no longer needed
  (keep the running and newest).
- **D3** Finishing an interrupted update doesn't say which disk is full.

### Installer
- **I1** The target disk is passed by path only: a USB stick plugged in or
  out shifts /dev/sdX. Pass serial/WWN, model and size; refuse on mismatch.
- **I2** BIOS installs don't record grub-pc/install_devices.

### Password prompts and Fleet
- **S1** One polkit action with auth_admin_keep for every update-run command:
  the password counts for the whole session for 5 minutes. Dangerous
  commands (Fleet join, TPM, signed start-up off, compliance off, opening the
  firewall, remote desktop, allow erase) ask every time, in their own words.
- **S2** kulan-fleet accepts plain http:// to localhost in production.
- **F1** A computer vouches for itself that an admin or the Owner signed in
  on it (last_report.sign_ins), so it can change their password and second
  steps everywhere. Not from computers for admin/owner accounts.
- **F2** edit-group/remove-group change where admin and Owner accounts may
  sign in without the Owner.
- **F3** Any admin can reset the password of, suspend or delete the mailbox
  of another Fleet admin (or the Owner) and sign in to Fleet as them.
- **F4** The server alone can make a sudo account with a known password and
  second step, or reset an existing admin's password: admin/owner account
  changes need the Owner's signature, like scripts.
- **F5** Owner-signed scripts aren't bound to a computer, action or time, and
  the first script key is trusted blindly.

## Bugs people would notice
- **B1** Printers: an opened printer folds shut every 5 s.
- **B2** Bluetooth: rows rebuilt several times a second while searching; clicks lost.
- **B3** Wi-Fi: rows rebuilt on every scan result; the ⋯ menu vanishes.
- **B4** Fingerprint: closing Add while the reader is being claimed leaves it claimed.
- **B5** Ask Kulan: a deleted conversation comes back; switching conversations loses Undo and can overwrite newer messages.
- **B6** Freezes: Software Update (apt-config, history), Setup (apt-config, lsblk, flatpak), Ask Kulan (engine check, conversation list), Storage › Clear, two-step QR code, Bluetooth agent.
- **B7** Wi-Fi passwords: 64 hex digits refused; WPA3 held to WPA2 limits.
- **B8** Appearance: a non-UTF-8 gtk.css breaks the page; gtk.css written in place.
- **B9** Battery samples apps forever after the first visit.
- **B10** Changing your password can sit silent for 35 s; Privacy shows a raw error; lpstat without a timeout.
- **B11** Undo starts the same (maybe broken) newest kernel.
- **B12** A firmware update can stop TPM unlock without warning.
- **B13** Undo on ARM VMs, installer retry after a failed close, timeshift check failure skipping the tidy-up, small files written in place.

## Wording and stale content
- **W1** One name each: the Store (not Software / the app store), Ask Kulan, Setup Assistant.
- **W2** Sentence-case buttons; "sign-in" not "login"; "›" in paths, one form of each Settings path.
- **W3** No commands to type, no raw errors (Ask Kulan PDFs, USB Maker, Fleet admin).
- **W4** Curly apostrophes, British spelling outliers, username, shortcuts written one way, ARM and Apple silicon.
- **W5** No Mac comparisons ("like a Mac", "Mac dots", "Finder-style", "like AirDrop").
- **W6** Stale: website screenshot and Help page, README, man pages, metainfo, USB size, Settings app references, old "2.8e" labels.
- **W7** Release notes: no repeats, shorter, nothing technical.

## Can wait (done if cheap)
Fleet report size limits, look-alike letters in join codes, 500s on bad
input, markup in the Fleet app's titles, cut-off mailbox lists, countme
body size, boot-report /tmp names, sandboxing for oom-notice and autojoin,
UID check before Fleet changes an account, USB Maker drive fingerprint on
Mac.

## Done (branch kulan-os-2.13)

Everything under Must fix, Bugs and Wording above, except:

- **F4, F5 (not fixed: a feature, not a fix).** The server can still make an
  administrator account on the computers, and a computer trusts the first
  script key it's given. Fixing it needs a new Owner signing step in Kulan
  Fleet. Written down in fleet/README.md ("What the server is trusted
  with"). Owner-signed scripts are approved once and then run as often as
  needed, so a signature without an expiry is the design, not a replay hole.
- **F1, as built.** A computer never weakens an admin's or the Owner's
  account (no password from a computer, no second step removed); a second
  step they set up there is still taken, as it only asks for more.
- **Can wait:** done where cheap (Fleet join over HTTPS only outside tests,
  the installer releasing the disk); the rest stays for later.
- **2.12 screenshot** on the website is the real 2.12 live desktop from CI
  (site-screenshots, run 41257723774), Appearance page.

## Tests added
- ci/update-recovery-test.sh: undo never twice; keeps accounts, passwords,
  Fleet, lost mode, signed start-up and Wi-Fi; no safety point, no undo;
  back to the safety point if the restore stops; not in lost mode; only the
  last update's point; the point recorded, a skipped one not offered; the
  point removed when updates need the room; the menu's undo starts the
  kernel from before the update; ~prev removed once the new file works.
- ci/signed-start-test.sh: a new (counted) signed file starts normally;
  the undo entry starts only when chosen.
- ci/signed-start-files-test.py (new): the file that worked is kept to fall
  back on; failed builds change nothing; low room; after undo; removed
  kernels; loader.conf not rewritten; off keeps signed start-up without
  GRUB's menu.
- ci/install-test.sh: a disk that changed since it was chosen is refused.
- ci/test-packages.sh: security changes ask every time (three lists equal,
  the gate); Fleet no plain HTTP; signed start-up files.
- fleet/test/functions.test.mjs: an admin's password and second steps not
  changed from a computer; group computers Owner-only with an admin in it;
  a Kulan Fleet admin's mailbox Owner-only.
- Kulan Settings self-test: fingerprint closed while claiming; Wi-Fi
  passwords. Ask Kulan self-test: Undo kept across conversations; deleted
  stays deleted.

## Releasing
The Fleet server part (fleet and fleet-admin functions; no migration) is
deployed with the release. Set the 2.13 release dates in the two metainfo
files then.
