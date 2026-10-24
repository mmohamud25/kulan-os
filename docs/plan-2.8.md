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

## Work accounts with Google Workspace (agreed 24 October 2026)
Kulan Group uses Google Workspace. Google is the accounts half, Kulan Fleet
the devices half (Google's device management doesn't cover Linux laptops).
- Sign in with your work account: Kulan Settings › Users › "Join your
  organisation's accounts". Uses Google's Secure LDAP service through SSSD
  (paste the certificate Google gives you); employees sign in with their
  Google Workspace account, their home folder is made on first sign-in, and
  sign-in still works offline after that. Suspending someone in the Google
  Admin console stops their laptop sign-in. Secure LDAP needs Business Plus,
  Enterprise, Frontline, Education or Enterprise Essentials Plus (not
  Business Starter or Standard).
- Fleet sets it up: a computer that joins a Fleet organisation can get the
  Google sign-in pushed to it, so a new laptop is ready in one step.
- Google sign-in for the Kulan Fleet app (admins), instead of the emailed
  one-time code.
- Roles: employees get standard accounts; the owner and IT get
  administrator accounts. Admins added to Fleet's list are named in its
  activity log.
- Tested on a real laptop, separate from the UTM VM.

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
