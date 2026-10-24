# Kulan OS 2.6 plan

Started with Mohamud, 23 October 2026. 2.5 shipped the same day. Nothing here
reaches main or the update channel until he says "release it".

## Counting downloads and installs, privately (agreed)
Kulan OS promises it doesn't track people; counting stays within that.
1. Downloads: GitHub's per-file download counts (PC and Mac ISOs) and
   cookieless website statistics (for example Cloudflare Web Analytics:
   pages and countries, no personal data).
2. How many computers run Kulan OS, Fedora "countme" style: once a week an
   anonymous request with only the version, PC or Mac, and a rough
   install age (this week / months / a year or more). No ID, nothing that
   follows a computer. On by default, said plainly in the Setup Assistant
   and Kulan Settings › Privacy, with an off switch. Received by a small
   Supabase function (RLS on, counts only, no IP addresses stored).
3. Hardware survey, opt-in only (unticked): computer model, graphics,
   Wi-Fi chip, memory, which Kulan features are on. The exact report is
   shown before anything is sent.
Not done: anything tied to a person or a computer, app-by-app usage,
cameras or faces. A privacy policy page on os.kulangroup.com covers 2 and 3.

## Moved from 2.5
- Fleet v2 for Mohamud's own computers: policies (updates, firewall,
  encryption, screen lock), apps and hardware inventory, lock and wipe,
  alerts and an action log.
- More Ask Kulan actions: photos by date, duplicates and large files,
  timers and to-dos, Bluetooth and printer fixes, backups, explain an
  error on screen.
- Sharing page in Kulan Settings (remote desktop, files, media).
- Ask Kulan: top-bar button, microphone and screen-text buttons.

## To solve (from the 23 October inspection)
- Security: signed, measured start-up files (unified kernel images) so an
  "evil maid" can't tamper with /boot; passphrase-protected update signing
  key with a rotation plan.
- Hardware: NVIDIA driver add-on with Secure Boot (MOK) signing; Broadcom wl.
- Tests: upgrade an installed previous release through Software Update and
  restart; sleep and wake; install next to Windows; a real restore-point
  restore.
- Downloads on Cloudflare R2 (download.kulangroup.com) before the PC ISO
  nears GitHub's 2 GiB limit; GitHub Releases stays as a mirror. Needs a
  Cloudflare account from Mohamud.
- Small: clipboard history pastes for you; Quick Note and To-do say when
  saving fails, with undo; "Make private" confirms the new name.

## Chosen with Mohamud (23 October 2026)
- Phone link: files, notifications and texts with an Android phone (KDE
  Connect protocol, through GSConnect), set up from Kulan Settings ›
  Phone; iPhone gets file sharing only.
- One backup and restore page in Kulan Settings: scheduled backups to a
  USB drive or another disk (encrypted, with Déjà Dup / restic), restoring
  everything or a single file, and earlier versions of a file from Files.
- A lighter mode for 4 GB computers: switched on automatically when memory
  is small (and by hand in Kulan Settings › Battery & Speed): fewer
  animations, no search indexing, the assistant's model only on demand,
  zram sized to match. Tested in the low-end start-up test.
- A Kulan Store in Kulan's look: apps from Flathub and Debian in one
  place, with Kulan's own picks, installs and updates through the
  existing update services (no new ways in).

## Not doing until Mohamud says so
- Somali; web answers for Ask Kulan.

## Status (23 October 2026, on branch kulan-os-2.6)
Done and tested: the upgrade test (found a 2.5 bug: a fresh install's first
update skips its restore point; being fixed), private counting (live on
Supabase, privacy policy page), lighter mode, the small fixes, the Backups
page, Phone link, Fleet v2 (server live), more Ask Kulan actions with the
top-bar, microphone and read-the-screen buttons. Version is 2.6, with
release notes. Phone link stays an add-on (not in the image, so it isn't on
for everyone); the Phone page installs it. Kulan Fleet (the admin app) is
listed in Kulan Settings › Add-ons.
Moved to 2.7 with Mohamud (docs/plan-2.7.md): Kulan Store, the native
Sharing page (GNOME Settings' Sharing stays linked), signed start-up files,
the signing key, NVIDIA/Broadcom, the sleep, dual-boot and restore tests,
and downloads on Cloudflare R2.

## 2.6.1 (fixes from testing 2.6, branch kulan-os-2.6.1)
Done, each with a CI test: Kulan Fleet sign-in accepts the address however
copied; the assistant window's name and icon in the dock; smaller restore
points (no Flathub apps, assistant models, Docker data or apt cache); short
app names in the grid; Update History groups one update; Ask Kulan's instant
answers (time, date, battery, free space) without a model; saved passwords
after automatic sign-in (explained in setup, fixed in Settings › Users).
Version 2.6.1 with release notes. Released only when Mohamud says so.

## Order (proposed)
1. Upgrade test 2.5 → 2.6 (protects everyone already on 2.5).
2. Private counting (Supabase) and the privacy policy page.
3. Lighter mode; small fixes.
4. Backup and restore page.
5. Phone link.
6. Fleet v2.
7. Kulan Store.
8. More Ask Kulan actions and buttons; Sharing page.
9. Security (signed start-up files, signing key), NVIDIA/Broadcom, and the
   sleep, dual-boot and restore tests.
If 2.6 gets too big, what's left at the end moves to 2.7, as Fleet v2
did from 2.5.
