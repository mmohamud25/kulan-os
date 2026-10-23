# Kulan OS 2.6 plan (draft)

Started with Mohamud, 23 October 2026. Nothing here reaches main or the
update channel until he says "release it". 2.5 ships first.

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

## Ideas, to choose
- Phone link (files, notifications, texts; Android best).
- One backup and restore page, including earlier versions of a file.
- A lighter mode for 4 GB computers.
- A Kulan Store in Kulan's look.
- On hold until Mohamud says so: Somali, web answers for Ask Kulan.
