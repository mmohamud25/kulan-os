# Kulan OS 2.9 plan

2.9's headline, chosen on 26 October 2026 when 2.8 (all of
Kulan Fleet) was done: **Remote help and actions**. Released in steps
2.9a, 2.9b, 2.9c like 2.8 (packages 2.9.1, 2.9.2...). Nothing here reaches
main or the update channel until the maintainer says "release it".

Agreed 26 October 2026: the relay is Cloudflare's (WebRTC, Cloudflare
Realtime TURN: the first 1,000 GB a month free, then $0.05/GB; the same
Cloudflare account later hosts big ISO downloads on R2); running a fix
(a script) is off until the Owner turns it on.

## The steps
(After 2.9c comes 2.10, the quality release: docs/plan-2.10.md. Then 3.0.)
- 2.9a Remote help: an admin sees, and with permission controls, the
  screen of someone who asked for help, from the Kulan Fleet app or the
  web dashboard (now hosted at os.kulangroup.com/fleet/, so it works on a
  phone too).
- 2.9b Actions: run a fix on a computer or a group (Kulan's ready-made
  fixes, or the organisation's own scripts once the Owner allows them),
  send a message to people's screens, Wi-Fi networks and printers pushed
  to a group's computers.
- 2.9c Get help: a Get help button in Kulan Settings (what's wrong, an
  optional screenshot) that reaches the organisation's admins in Kulan
  Fleet, with Start remote help next to it; and the native Sharing page
  (remote desktop, file and media sharing) in Kulan Settings.

## 2.9a Remote help: how it works (design)
Consent first, every time:
- Only while someone is signed in, and only after they press Allow in a
  dialog on their screen saying which admin wants to see it. GNOME then
  asks once more which screen to share (its own portal dialog).
- View only, unless the person also ticks "let them use my mouse and
  keyboard". While it's on, a bar at the top says who is watching, with
  Stop; it ends by itself after an hour, or when the admin closes it.
- Nobody signed in, or nobody answers within 2 minutes: no session.
  (Help at the sign-in screen, for a computer nobody can use, is later.)
- Every request, allow, decline and end is in the activity log; the screen
  itself is never recorded or kept.

How the picture gets there (as built):
- The computer: the request arrives with a report; kulan-fleet finds who is
  at the computer (an active, unlocked desktop session; otherwise the admin
  is told "nobody is signed in", "the screen is locked"...), picks the
  request up with the computer's own key (the server then makes a key for
  this session only), and starts kulan-remote-help in that person's own
  systemd with only the session's key, passed in its environment (never on
  a command line). The helper asks; if allowed it shares the screen through
  GNOME's portal (ScreenCast, or RemoteDesktop for control) into GStreamer's
  webrtcbin (VP8, up to 1920 wide, 15 frames a second), the mouse and
  keyboard coming back over a data channel and done with the portal, only
  when allowed (checked again on the computer). Lives in kulan-settings
  (GTK and GStreamer); the checks without a screen in kulan-base
  (helpsession.py).
- Signalling (offer, answer, network candidates) goes through Kulan Fleet's
  functions (fleet.help_sessions, at most 60 candidates a side): the
  computer makes the offer once allowed, the admin's side answers; both ask
  every second while connecting.
- Reaching the computer in seconds: each computer listens on a Realtime
  broadcast topic of its own (kulan-fleet listen, the kulan-fleet-listen
  service: one WebSocket, the standard library only). fleet-admin wakes it
  for remote help and for every other request (actions, group actions); a
  wake only makes it report now. Without the listener, the next hourly
  report.
- Connections go direct when they can; otherwise through Cloudflare's
  relay, with credentials made by the server for that session only (62
  minutes). The relay sees only encrypted traffic (DTLS-SRTP).
- Admins: the dashboard, now also at os.kulangroup.com/fleet/ (any browser,
  phones included; remotehelp.js); the Kulan Fleet app opens it there at
  that computer (#help=<computer>). Only the admin who asked follows the
  session (the person allowed them, by name); one admin at a time on a
  computer.
- Needed before release: a Cloudflare account with a Realtime TURN key (its
  id and API token become Supabase secrets CLOUDFLARE_TURN_KEY_ID and
  CLOUDFLARE_TURN_TOKEN), and os.kulangroup.com/fleet/ added to Supabase
  Auth's Redirect URLs. Without the relay, remote help still works where the
  computers can reach each other directly (the same network, most home
  routers).
- Tested: the functions against Postgres (every state, who may do what,
  expiry, the relay's credentials); end to end with the real functions,
  Chromium as the admin and webrtcbin with a test picture (the picture, the
  mouse and keyboard only once allowed, either side ending it, declined);
  the helper on Debian's own GStreamer in the package test, with its
  windows; the wake listener against a stand-in Realtime; the two-computer
  test (woken over TLS through the test server, "nobody is signed in").
- Later: help at the sign-in screen (nobody signed in), and choosing which
  screen in the admin's view when there are several.

## 2.9b Actions: how it works (design)
- Run a fix: Kulan's ready-made fixes (free up disk space, reset Wi-Fi,
  repair updates, restart the printer queue, clear the app cache...), and
  the organisation's own scripts (bash, up to 64 KB) once the Owner turns
  scripts on. Only admins who passed their authenticator app code may run
  one; each run is logged with who ran it, the script, and its output
  (first 64 KB) and exit code; a run stops after 10 minutes. The person
  signed in is told "Your organisation ran: Free up disk space".
- Message people: a notice on the screens of a person, a computer or a
  group ("Restart tonight for updates"), logged.
- Wi-Fi networks and printers pushed to a group's computers (Wi-Fi
  passwords kept sealed like the other secrets; removed again when a
  computer leaves the group).

## 2.9c Get help and Sharing: how it works (design)
- Kulan Settings › Get help (for computers in an organisation): what's
  wrong, and optionally a screenshot the person sees first. It reaches
  Kulan Fleet as a help request (an alert), with Start remote help.
- Kulan Settings › Sharing: remote desktop (GNOME Remote Desktop), file
  sharing and media sharing switches, natively, replacing the link to
  GNOME Settings.

## Not in 2.9's headline (moved to 2.10 or later unless decided otherwise)
## Moved from 2.8 (first moved from 2.6, then 2.7)
- (The native Sharing page is now 2.9c.)
- Security: signed, measured start-up files (unified kernel images) so
  someone with the computer for a few minutes can't tamper with /boot; a
  passphrase-protected update signing key with a rotation plan.
- Hardware: NVIDIA driver add-on with Secure Boot (MOK) signing; Broadcom wl.
- Tests: sleep and wake; install next to Windows; a real restore-point
  restore.
- Downloads on Cloudflare R2 before the PC ISO nears GitHub's 2 GiB limit
  (needs a Cloudflare account).

## Ideas to consider (23 October 2026)
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

## Later
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
