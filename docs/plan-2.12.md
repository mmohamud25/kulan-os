# Kulan OS 2.12: the gaps 2.11 left

What 2.11 left undone, with reasons (docs/plan-2.11.md, "Left"), closed here.

## Done (branch kulan-os-2.12)

1. **Undo the last update with signed start-up.** Each signed start-up file
   (kulan-signed-start, a unified kernel image) has a second profile, "Undo
   the last update": the same kernel and start-up image, with
   `kulan.undo-update` added to its options (ukify `--profile` /
   `--join-profile`, systemd 257). systemd-boot lists it as "Kulan OS (Undo
   the last update)", ID `kulan-<version>.efi@undo`; loader.conf's default is
   now `kulan-*.efi` so it never matches (corrected on older computers at the
   next re-signing). It's signed with the rest: no second file, no room
   needed on the EFI partition. Where ukify or the add-on stub is too old, the
   file is made as before, without it.
   The menu shows after a start-up that didn't reach the desktop, as with
   GRUB: boot-ok sets systemd-boot's one-time timeout
   (LoaderConfigTimeoutOneShot, 10 s) at every start and clears it once the
   desktop has lasted a minute.
   Undoing (from the menu or Software Update) keeps signed start-up's key and
   state (the firmware trusts this key, not the restore point's) and signs
   the start-up files again for the restored system's kernels: before, the
   newest file still started the update's kernel, whose modules the undo had
   taken away.
   Tests: ci/update-recovery-test.sh (menu asked for and cleared; key kept,
   files re-signed after the restore); ci/signed-start-test.sh (the file has
   the entry, the menu was asked for, and starting through the entry runs the
   undo service).
2. **"Block ads and trackers" without a download.** systemd-resolved is in
   the image (hook 0980, after the hooks that need the internet: its package
   points /etc/resolv.conf at its stub, which doesn't exist during the
   build). kulan-resolv-link.service puts the link back at start-up whenever
   resolved is on and /etc/resolv.conf is a plain file. Existing computers
   are unchanged: update-run dns still installs it the first time.
   Tests: boot and install tests report it ("Encrypted DNS ready").
3. **The sign-in screen no longer waits for the firmware service.**
   fwupd.service says Before=display-manager.service; a generator
   (kulan-fwupd-later) gives systemd a copy of Debian's unit without that one
   ordering, at every start-up, so it follows fwupd's updates. It held up the
   sign-in screen by about 2.4 s in the install test.
4. **Join codes**: the dashboard and the Fleet app show only the code (Kulan's
   server is built into Kulan OS), with Copy; for another server, a join link
   (`server#code`) that Join an organisation takes as it is.
5. **Chrome's privacy settings** (checked): Flathub's Chrome links the host's
   /etc/opt/chrome/policies/managed files into its sandbox at every start
   (its chrome.sh, with --filesystem=host-etc), so Kulan's policy applies to
   it as to the .deb.

6. **Secure start-up marks a working start-up again.** boot-ok read EFI
   variables with `tail -c +5 file`, which efivarfs refuses (it can't seek),
   so since 2.11 the signed file never lost its try counter: after three
   restarts systemd-boot could have gone back to the previous Linux. It reads
   them through a pipe now; the signed start-up test checks it.
7. **The live system restarts at once in virtual machines** (and from DVDs).
   live-tools asked to remove the medium behind the splash and waited 90 s:
   it skips that only for `noeject`; Kulan's boot options said `noprompt`,
   which it never read. The boot test now fails if the question shows.

8. **Restart to finish updating, Mac-style.** Before, a restart that updates
   needed was only shown (the top bar's icon, Software Update's page). Now:
   - Installed with Software Update's button: GNOME's restart window opens
     straight away (a 60-second countdown; an app with unsaved work stops it;
     Cancel keeps working). Kulan Settings leaves a note in the runtime
     folder (kulan-restart-asked) so Kulan Shell doesn't ask again at once.
   - Installed any other way (automatic updates, an IT admin's Install
     updates, the terminal): a notice, Restart Now / Later; Later, or no
     answer, asks again 4 hours later (Kulan Shell, restartprompt.js).
   - At night (1 to 5 am), after 30 minutes nobody has used the computer, it
     restarts by itself, like a Mac's Try Tonight: only on mains power, with no
     update running and no app holding back signing out (unsaved work), and
     only when logind allows it without a password (nobody else signed in).
   Tests: Kulan Settings' self-test (asked after an install that needs it,
   not otherwise, not after a check).

10. **Profile** (asked for): the first row of Kulan Settings' sidebar is you
    (your picture and name); its page has a large picture with Change
    picture… and Remove, your name, password and sign-in. Users keeps other
    people, with a link to Profile. Change picture didn't work: the file was
    passed as chosen, which the accounts service (another user) may not be
    able to read, and big photos were refused. Now any picture is made a
    square PNG of at most 512 pixels (the middle of it, turned the right way)
    in a file it can read, then removed. Self-test: a 1600x900 photo in a
    private file becomes 512x512, readable; a text file is refused.
11. **One Settings app** (asked for): GNOME Settings is hidden (NoDisplay, a
    copy of its launcher in Kulan's xdg folder, remade by desktop-setup, so
    it follows gnome-control-center's updates) while Kulan Settings is there.
    Not removed: Kulan Settings opens a few of its pages (Online Accounts,
    Printers, Sharing, Region...). The gear in the top-right menu opens Kulan
    Settings (Kulan Shell swaps the SettingsItem's app, put back on disable;
    GNOME 48's system.js checked).

12. **Kulan Settings crashed at start-up on computers with saved Wi-Fi
    networks** (2.11, seen on the ThinkPad: a segmentation fault in
    wifi.py's saved_connection). NMDevice.filter_connections, handed a list
    from Python, crashes libnm's bindings; the test machines had no saved
    networks. The same check is now plain Python (wifi.saved_for), for Setup
    too; the self-test checks it and that filter_connections stays out. Also
    out as 2.11.1 on its own.
13. **Fingerprint sign-in** (asked for; the ThinkPad T490 has a Synaptics
    reader libfprint supports): fprintd and libpam-fprintd in the image and
    recommended by kulan-settings; pam-auth-update --enable fprintd once
    (kulan-settings postinst and a trigger, then left to the person). Profile
    has Fingerprint (only with a reader): the fingers added, Add a
    fingerprint… (a window that follows each touch: fprintd's EnrollStatus,
    in plain words) and Remove all. The reader is always released. Self-test:
    a stand-in fprintd on the session bus (finding the reader, three touches
    with a bad one, listed, removed, released). The real reader is first
    tried on the ThinkPad.
14. **Chrome-type browsers take Kulan's look** (asked for: Chrome showed its
    own purple and Windows-style buttons on the right). No browser policy
    sets the theme, so kulan-browser-look (a user service at sign-in, and a
    path unit when an app is installed) sets each profile's
    extensions.theme.system_theme to GTK, once per browser folder: before
    first launch, or in used profiles with no theme chosen. Never over a
    choice (Classic, Qt, a Web Store theme), never while open (SingletonLock),
    never again once done. Chrome, Chromium, Brave, Edge, from the Store or
    as packages. Checked with real Chromium (colours follow GTK); the button
    side on Wayland/Flatpak is checked on the ThinkPad. The rest of the apps
    (Electron, Qt, Flatpak colours) is 2.13's "Every app looks like Kulan".

Known, left: AppArmor's profiles are still compiled at a new computer's first
start (about 11 s in the install test's virtual machine, once). The
installer prepares them, but for the live system's kernel features; the
installed system's kernel (the same version) also reports extended
capabilities (caps/extended), so its cache folder is another one
(979b3ac6.0, not f6da0254.0). The boot report still lists the folders.

9. **Microsoft 365 work email through Fleet.** The Owner connects an app
   registered in Microsoft Entra (tenant ID, client ID, a client secret;
   Microsoft Graph application permissions User.ReadWrite.All,
   User-PasswordProfile.ReadWrite.All, Organization.Read.All,
   RoleManagement.Read.Directory, GroupMember.Read.All, admin consent). fleet-admin's microsoft.ts
   has the same functions as google.ts; mail.ts picks one per organisation,
   and every message names the service connected. New mailboxes get the
   first licence with Exchange Online (chosen when connecting, its free count
   checked first; a user whose licence is refused is deleted again, so none
   is left without a mailbox) and the country Microsoft needs. Disabling
   blocks sign-in and revokes sessions; Entra admin roles mean Owner only, as
   Google admins do. One service at a time; switching needs the old
   service's mailboxes unlinked first, so nothing is ever done to the wrong
   account. Migration 20261102120000 (provider "microsoft", tenant_id,
   licence, licence_name, usage_location). Dashboard and Fleet app: Work
   email, Connect Microsoft 365, the wording per service. Computers: the
   mailbox opens in Outlook on the web with the address filled in (GNOME's
   Microsoft 365 account gives apps only the address, so Geary can't read it,
   and computers aren't made to install Geary for it); opening it counts as
   connected. Guide: docs/fleet-microsoft-365.md. Test server: a stand-in
   Microsoft. Tests: functions.test.mjs (a stand-in Entra and Graph:
   connecting, every refusal, mailboxes made with a licence and country,
   blocked, renamed, new passwords, admin accounts, no free licence, Microsoft
   down, removed, the log, switching services), dashboard.test.cjs, the Fleet
   app's self-test, workmail-test.py, work-email-reminder-test.py.
   A separate review then found, and these were fixed (each with a test):
   any SKU with an EXCHANGE_S_ plan counted as a mailbox licence (Power BI
   and Visio carry EXCHANGE_S_FOUNDATION, which gives none; now only the
   plans with a mailbox, enabled SKUs, and the Owner can name one); admins by
   PIM eligibility or through a role-assignable group weren't seen (now:
   roleAssignments, roleEligibilityScheduleInstances, transitiveMemberOf,
   GroupMember.Read.All, every page, unknown = admin); Graph's delay after
   making a user (404s, now asked again); a licensed user could be left
   behind (try/finally, and deleted when People can't keep it); Microsoft's
   refusal for admin accounts read as a broken connection (now explained:
   the app would need Privileged Authentication Administrator); addresses
   that aren't the sign-in name (mail vs userPrincipalName, looked up with
   a filter, apostrophes doubled); each person's mailbox now records its
   service (mailbox_provider) and is never looked for in the other.
   Not tried against a real Microsoft 365 tenant yet.

## Releasing

The Fleet server part (migration 20261102120000 and the fleet and
fleet-admin functions) is deployed with the release, before computers get
2.12: computers before 2.12 ignore a "microsoft" mailbox.
