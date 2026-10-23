# Kulan OS 2.7 plan (draft)

Moved from 2.6 with Mohamud, 23 October 2026, so 2.6 ships sooner. Nothing
here reaches main or the update channel until he says "release it".

## Moved from 2.6
- A Kulan Store in Kulan's look: apps from Flathub and Debian in one place,
  with Kulan's own picks; installs and updates through the existing update
  services (no new ways in).
- A native Sharing page in Kulan Settings (remote desktop, files, media).
  Until then, GNOME Settings' Sharing stays linked from Kulan Settings.
- Security: signed, measured start-up files (unified kernel images) so
  someone with the computer for a few minutes can't tamper with /boot; a
  passphrase-protected update signing key with a rotation plan.
- Hardware: NVIDIA driver add-on with Secure Boot (MOK) signing; Broadcom wl.
- Tests: sleep and wake; install next to Windows; a real restore-point
  restore.
- Downloads on Cloudflare R2 before the PC ISO nears GitHub's 2 GiB limit
  (needs a Cloudflare account from Mohamud).

## Proposed additions (23 October 2026; Mohamud likes all of them)
User friendly:
- Reset Kulan OS, keeping your files (like Pop!_OS recovery / Powerwash).
- Move from Windows or Mac: bookmarks and saved passwords, Wi-Fi networks,
  the wallpaper (files already work).
- Choose a desktop layout: Kulan, Windows-like or Mac-like (like Zorin).
- Website to app (like Linux Mint's Web Apps).
Powerful:
- Kulan Shortcuts: routines built from Ask Kulan's actions ("at 9 am:
  Focus on, open Firefox"; "when my backup drive is plugged in: back up").
Secure:
- Find My Computer: Fleet v2's lock and erase for one person, with the same
  consent rules.
- Private folder: an encrypted folder with its own password (like KDE Vaults).
- Battery care: stop charging at 80%.
- Fingerprint sign-in set up in Kulan Settings › Users; the security
  checkup runs weekly and only speaks up when something needs fixing.
- Kulan Store: show each app's permissions and whether its publisher is
  verified before installing (required, not optional).
- Kulan Settings › About: a Graphics line (acceleration on or off), so a
  virtual machine without 3D is obvious.

## Proposed for 2.8
- Gaming: the Steam add-on (already there, with Proton) gains GameMode and
  controller support in the same step (becomes the Gaming edition in 3.0);
  Android apps (Waydroid); live captions (on the computer, with Dictation's
  speech model); apps from other Linux systems (Distrobox). Sending files to
  nearby computers is already the Nearby Share add-on (LocalSend).
- Newer hardware support: a newer kernel from Debian backports.
- Graphics switching for laptops with two graphics chips.
- Offline learning library add-on (Wikipedia, lessons, books; fits Kulan
  Institute and places with poor internet); guest session that's wiped at
  log-out.
- The big safety project: updates you can start the previous version from
  (btrfs snapshots in the start menu, like openSUSE and Fedora Silverblue).

## Smaller follow-ups from 2.6
- Fleet: a short notice on the computer before an erase; send the counting
  week from the computer so a count near midnight on Sunday (UTC) can't be
  counted twice.
