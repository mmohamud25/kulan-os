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

## Smaller follow-ups from 2.6
- Fleet: a short notice on the computer before an erase; send the counting
  week from the computer so a count near midnight on Sunday (UTC) can't be
  counted twice.
