# Kulan OS 2.11: smooth, connected, dependable

Status: plan, not started. No new features: 2.11 makes what's there feel like one
finished product. Built from a read-through of every screen, message and start-up
step on main (2.10.2), and the test machines' start-up logs.

The test for every item: would a person who has never seen Linux notice it, get
stuck, or lose something? Ordered by that.

## 1. Never lose work, never end up broken (must have)

Status: done in branch 211-recovery, except where noted. Tests:
ci/update-recovery-test.sh (in ci/test-packages.sh, "Updates can't leave it broken").

1. **Updates survive closing the window, signing out, sleep and power cuts.**
   Done. update-run runs its long changes (install, install-devkit,
   install-apps, add-ons, cleanup, undo-update, repair) as their own system
   service, kulan-update.service (systemd-run, gone when done), under
   systemd-inhibit (shutdown, sleep and the lid held off). Whoever started it
   (Kulan Settings, `kulan update`, Kulan Fleet) only follows it: the same
   progress lines, "say:" messages and exit status as before, from
   /run/kulan/update-run.out and .err. If the caller goes away, the change
   carries on; one change at a time ("Another change is still being made").
   Kulan Settings asks before closing while one runs ("Updates are
   installing. Kulan Settings can close when they're done", Keep Open or
   Hide; hidden, it sends a notification when done and closes), and shows an
   install it didn't start, with its progress, when opened. Ctrl+C in
   `kulan update` stops watching, not the install. Fleet's install-updates
   waits up to 3 hours and then reports "still installing" instead of
   killing apt. Without systemd (containers, the ISO build) it runs as before.
2. **An interrupted update finishes itself at the next start-up.** Done:
   kulan-update-repair.service (only when dpkg was left half-way: `update-run
   needs-repair`), `dpkg --configure -a` then `apt-get -f install` with
   confold, waiting for the lock, before the daily update jobs, nothing
   (the sign-in screen included) waiting for it; one hour at most. Software
   Update shows "An update was interrupted" with Finish Now (`update-run
   repair`). updates.json: state "interrupted"; "running" now records the
   process that's installing (running_pid, running_start), so a crashed
   install never shows as running, and a failed apt simulation keeps the last
   list instead of leaving "Installing…" for 3 hours. Not while dpkg runs
   (its lock is checked).
3. **A broken update can always be undone, even if the desktop won't start.**
   Done for GRUB: /etc/grub.d/01_kulan-boot-check (GRUB notes each start-up
   with save_env, where it can write: ext2/3/4 or FAT, no LVM/RAID/LUKS
   under /boot) and kulan-boot-ok.service (notes it too, and clears it once
   a desktop session has lasted a minute). After a start-up that didn't get
   that far, the menu shows for 10 seconds: each Linux on its own line
   (GRUB_DISABLE_SUBMENU, no recovery entries: they need a root password),
   then "Undo the last update" (/etc/grub.d/12_kulan-undo, only while a
   pre-update restore point exists: kulan_undo in grubenv, kept by
   restore-points), which undoes before the sign-in screen
   (kulan-undo-at-start.service). Signed start-up (shim + systemd-boot +
   UKIs): boot counting, new files are kulan-<version>+3.efi; boot-ok removes
   the counter once the desktop worked (as systemd-bless-boot does, without
   installing the systemd-boot package, whose kernel-install hooks would add
   unsigned entries); systemd-boot starts the previous version after three
   failed tries. fast-restart (ARM VMs) restarts the running Linux, not a
   newer one, until the start-up has reached the desktop.
   Left: with signed start-up there's no "Undo the last update" entry (a
   signed UKI's kernel options can't be changed at start-up; it would need a
   second signed UKI per kernel, which doesn't fit the EFI partition); and
   choosing the earlier Linux in the menu works for that start only (GRUB
   keeps starting the newest by default).
4. **Automatic updates stay out of the way.** Done. The restore point is taken
   in apt-daily-upgrade.service's ExecCondition, before unattended-upgrades
   takes the lock (not in DPkg::Pre-Invoke): only when automatic updates are
   on, due and have something to install; the once-a-day stamp only when it
   was made; free space re-checked after it (exit 75 skips that run). During
   automatic updates and Kulan's own installs, the status is refreshed once
   at the end (KULAN_DEFER_STATUS), not after every dpkg step. update-run says
   "Waiting for automatic updates to finish" (or "another installation") and
   waits up to an hour instead of failing after 5 minutes. Timers: restore
   points 50 minutes after start-up then daily, app updates 1 hour after
   then daily, automatic updates at midday or 1 hour after start-up; none
   catches up right after switching on.
5. **No job can hang start-up or shutdown**: done. TimeoutStartSec on app
   updates (and Flatpak stopped after an hour), restore points (2 h), their
   tidy-up (1 h), the status refresh, signed start-up (30 min), the repair (1 h)
   and the start-up undo (2 h).
6. **"Undo the last update"** needs the charger or a battery at least 30% full,
   and keeps the computer on while it runs. Done.
7. **Checking for updates** shows Debian's and Kulan's updates first (apt-get
   update), then looks for app and firmware updates. Done.
8. **Update History** names updates as the update list does; package names
   only under "Technical details". Done.

Not automated yet: a power cut during an update in a VM (section 7). How:
in ci/upgrade-test.sh's VM, start `update-run install` from the test
service, watch the serial log for "pmstatus:...:Unpacking" from a
deliberately slow package (a test .deb whose preinst sleeps 60 s), then
`kill -9` QEMU (a real power cut, not a shutdown); start the VM again and
check, from the boot report, that kulan-update-repair.service ran, `dpkg
--audit` is empty, updates.json says "idle" and the start-up menu was shown
(the GRUB screen in the screenshots, kulan_boot_pending was 1). And a broken
kernel: install a kernel package whose initrd is empty, restart, check the
menu shows and the earlier Linux starts.

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
