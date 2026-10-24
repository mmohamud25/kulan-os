# Kulan OS 2.8 plan (draft)

Moved from 2.7 with Mohamud, 24 October 2026, so 2.7 ships sooner (see
docs/plan-2.7.md). He liked all of these on 23 October. Nothing here reaches
main or the update channel until he says "release it".

## Moved from 2.7 (first moved from 2.6)
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

## The headline: Fleet accounts (agreed 24 October 2026)
Mohamud's employees use Kulan OS laptops with accounts managed in Kulan
Fleet: an Entra ID and Intune of Kulan's own, for Kulan OS. Email stays on
Google Workspace (Business Starter); his own mail server comes later.
- People in Kulan Fleet: name, username, role (employee or admin) and which
  computers (all of the organisation's, or chosen ones).
- First sign-in: their name is on the sign-in screen of every computer they
  may use; a one-time setup code (from Fleet, handed over by the admin), then
  they choose their own password.
- The password follows them: Fleet keeps only a one-way hash (yescrypt) and
  sends it only to the computers they may use, so sign-in works offline; a
  change on one computer reaches the others at their next report. Safe
  because Fleet requires encrypted disks.
- Roles: employees get standard accounts; admins get administrator rights.
- Leaving: disable in Fleet and their account is locked everywhere at the
  next report (within the hour) or at once with "Lock now"; the admin keeps
  their files for handover or removes them.
- Every change (created, disabled, made admin) in the activity log, with who
  did it.
- Optional, same form: also create (and later suspend) their Google
  Workspace mailbox through Google's Directory API (works on Business
  Starter; one-time setup in the Google Admin console). Built so another
  mail service can plug in instead (Stalwart or Migadu, both have an API)
  when Kulan has its own mail server.
- Tested with two VMs, then a real laptop (separate from the UTM VM).

Not doing for now: Google Secure LDAP sign-in (needs Business Plus);
Fleet accounts take its place.

## Proposed additions (23 October 2026)
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

## After 2.8 (was proposed for 2.8; moves back one release)
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
