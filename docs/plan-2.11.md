# Kulan OS 2.11: smooth, connected, dependable

Status: plan, not started. No new features: 2.11 makes what's there feel like one
finished product. Built from a read-through of every screen, message and start-up
step on main (2.10.2), and the test machines' start-up logs.

The test for every item: would a person who has never seen Linux notice it, get
stuck, or lose something? Ordered by that.

## 1. Never lose work, never end up broken (must have)

1. **Updates survive closing the window, signing out, sleep and power cuts.**
   Today Install runs inside the Kulan Settings window: closing it, signing out
   or closing the lid stops apt half-way. Run installs as their own system
   service that Settings only watches, keep the computer awake while it runs,
   and say so if you try to close it. Same for Fleet's "install updates" and
   "Undo update".
2. **An interrupted update finishes itself at the next start-up.** Nothing
   repairs dpkg at boot today; after a power cut the computer can't install
   anything until someone presses Install. Add a start-up repair service, and a
   plain "An update was interrupted — Finish now" in Software Update.
3. **A broken update can always be undone, even if the desktop won't start.**
   The start menu is hidden with no fallback. Count successful start-ups; after
   a failed one, show the menu with the previous version and "Restore points".
   Signed start-up gets the same through boot counting.
4. **Automatic updates stay out of the way.** The daily restore point runs while
   holding the update lock (so Install waits 5 minutes and fails), and every
   small update step re-runs a full update check. Take the restore point before
   updates start, check updates once at the end, re-check disk space after the
   snapshot, and start daily jobs mid-day instead of right after you turn the
   computer on.
5. **No job can hang start-up or shutdown**: timeouts on app updates, restore
   points and signed start-up.

## 2. Nothing freezes (must have)

Pages that freeze Kulan Settings for 0.2–1.5 s (up to 25–60 s when something is
slow) because they wait on the window's own thread: About (also from the top
bar), Security and its report, Sharing (keyring), Add-ons, Date and time,
Language and region, Users, App permissions, Software Update's history.
Kulan Fleet waits on the keyring before its window appears. Ask Kulan's Undo
can freeze with no time limit. Move all of it off the window's thread, show the
page at once and fill it in.

The desktop itself is fine (nothing slow runs in the top bar), apart from small
file writes and the optional System widget; make those asynchronous too.

**Done (branch 211-freezes).** Pages open at once and fill in when the answers
come (common.py's new `in_thread()` and `run_async()`, which has a time limit):
- About: processor, graphics, 3D acceleration and desktop version fill in from
  a thread, found once per run.
- Security: the protection cards (systemctl, findmnt, lsblk, apt-config,
  dpkg-query) on a thread, at every visit and after each fix; only the newest
  answer is shown. Saving the compliance report no longer waits (90 s limit).
- Sharing: NetworkManager and the keyring (the Public folder's password, a new
  remote desktop password) on a thread, as the page already did for remote desktop.
- Add-ons: one dpkg-query and one flatpak list for all add-ons, on a thread.
- Date and time, Language and region: timedated, localed and AccountsService
  asked without waiting; `locale -a` on a thread; the format example is worked
  out in the app itself (no `date`/`printf`).
- Users: AccountsService on a thread; the names in use found once per Add
  dialog, not at every key; the password hash and the new account's details
  on a thread; moving saved passwords after turning off automatic sign-in on a
  thread, as "stop asking" already was.
- App permissions, Privacy's "What's sent", Battery's lighter mode: no waiting.
- Kulan Fleet: the window appears at once; the keyring is read in the background.
- Ask Kulan: Undo on a worker thread; wpctl and nmcli have a 15 s limit, and an
  undo that fails says so.
- Kulan Shell: the quick note, to-do list, open apps, effects state, the GTK
  styles and the "done once" markers are written without the desktop waiting
  (files.js: still written whole and renamed into place, in order, newest
  wins; what was just written reads back at once). The System widget updates
  its rows in place every 10 s (was 4 s, rebuilding every row), reads the disk
  and batteries without waiting, and pauses while it can't be seen (overview,
  lock screen, a maximized or full-screen window over it).
- The self-test opens every page with slow stand-ins for those programs and
  fails if that takes long, and fails on mistakes in answers that arrive later.

**Left:** Software Update's history (updates.py, done with section 1's work on
updates). The Shell still reads its own small files directly (the note, the
to-do list, open apps at start-up): reading them is quick and keeps the code simple.

## 3. Connected the first time (must have)

1. **Setup gets you online**: a Wi-Fi step, and the apps page refreshes by
   itself once connected.
2. **Joining an organisation asks only for the code** (and accepts a join link):
   Kulan's server is filled in. Today people must type a long server address.
3. **Wi-Fi: forget a network and change its password.** Today a wrong saved
   password is reused forever.
4. **Email**: ship a mail app (Geary or Thunderbird), the default for email
   links. Work email from Fleet currently has nowhere to open.
5. **Accounts**: one Kulan Settings page for Google and Microsoft accounts;
   Microsoft for Fleet work email too.
6. **Printers and scanners**: every administrator can add printers (today only
   the first account can); ship Document Scanner and point to it.
7. **Phone link** works without signing out, with one set of instructions.
8. **Ask Kulan**: Setup offers the model download; anyone (not only an
   administrator) can download it; computers with too little memory are told
   plainly instead of downloading a model that can't run.
9. **Add-ons that are already there show as installed** ("Office and media
   apps" offers to install what's already installed, and removing it would
   remove the built-in apps). Developer and learning add-ons do their own group
   set-up instead of telling you to run `sudo usermod`.

## 4. One product, one voice (must have)

1. **No program output anywhere.** Settings is done; the rest still shows raw
   errors: Ask Kulan ("error 500 {…}", "[Errno 111] Connection refused"), Setup,
   the installer's disk step, Kulan Fleet and the web dashboard ("Failed to
   fetch"), the to-do and Live Text notifications. And no "sudo apt install …"
   or package names in messages: offer the fix or point to Software Update.
2. **Paths that lead somewhere**: "Settings › Battery" (it's Battery and power),
   "Settings › Wi-Fi", "Settings > Apps > Default Apps", "the Settings app" said
   inside Kulan Settings. A check in CI that every "Kulan Settings › …" path in
   the code names a real page.
3. **One name per thing**: sign out (not log out), Ask Kulan (the thing you
   talk to) vs Kulan Assistant (the add-on), Super key (one way to write
   shortcuts everywhere: website, release notes, installer tip says "Cmd" on a
   PC), Store (not Software), Phone link, saved passwords (not keyring), sign-in
   screen (not login), start-up, Arm.
4. **One style**: sentence case for titles and buttons, curly apostrophes, no
   full stop on short messages. Enforced by a CI check so it stays that way.
5. **Get help › Ask Kulan opens Ask Kulan** (today it opens the Assistant
   settings page). The "Kulan OS was updated" notification opens what's new.
   Update History names updates the way the update list does.
6. **Fleet dashboard** uses its own dialogs, not the browser's confirm boxes,
   for Erase and Remove.

## 5. Fast to start (should have)

The test machines are slow virtual computers; measure on the ThinkPad first
(`systemd-analyze blame` and `critical-chain`). From the logs:
- The login screen waits ~2 s on the firmware-update service; start it later.
- The first start-up after installing spends ~11 s preparing AppArmor; do it
  during installation.
- Setup's autostart loads Python and GTK at every sign-in just to exit; skip it
  once done.
- The memory-warning service keeps 20–30 MB running; make it lighter.
Target: login screen within 15 s of the Kulan logo on the ThinkPad.

## 6. Secure, quietly (should have)

- "Allow erasing" can't be switched on without an encrypted disk.
- Hotspot keeps one password and shows it (with a QR code).
- "Block ads and trackers" works offline on a new computer (ship
  systemd-resolved).
- Setup hides administrator-only switches from standard accounts, and says
  "no personal data" (the weekly count is anonymous but is data).
- Check that Chrome (Flatpak) actually gets Kulan's privacy policy.

## 7. How we know it's done

1. **Daily-driver week on the ThinkPad**: real work, sleep and wake, a second
   screen, a video call, printing, Bluetooth headphones, updates while
   working, a deliberate power cut during an update. Every rough edge is logged;
   2.11 ships when the list is empty.
2. **New automatic tests**: power cut during an update (the VM is killed
   mid-dpkg, then must start and finish by itself); a broken kernel update
   (must fall back); every Settings page opens in under 100 ms with slow
   helpers; the words and paths checks above.
3. **Lint in CI**: ruff, shellcheck and eslint on every build (none run today).

## Not in 2.11

New features wait for 3.0: Editions, joining an organisation during
installation, NVIDIA and Broadcom driver add-ons, cloud backups to Google Drive
or OneDrive, "Charge to 100% once", Ask Kulan in Somali and on the web.

## Owner's own steps (open since 2.10)

- Protect main on GitHub, so nothing reaches computers without passing the tests.
- The steps in docs/signing-key.md (backing up the update channel's key).
- Delete the old branch probe/signed-start.
