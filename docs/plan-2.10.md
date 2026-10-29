# Kulan OS 2.10 plan: the quality release

Decided 26 October 2026: after 2.9 (2.9a remote help, 2.9b actions, 2.9c
get help) and before 3.0, one release with no new features. Kulan OS has to
hold up as a finished product: no rough edges, nothing that looks unfinished,
security that can be defended to a customer or an auditor. 3.0 starts only
when this list is empty.

## 1. Use it like a customer
- Fresh installs on real hardware (the ThinkPad) and in UTM and VMware.
- Every screen of every app reviewed by hand: Kulan Settings, Welcome, the
  installer, Software Update, Kulan Fleet and its dashboard, Ask Kulan.
- One list of every bug and rough edge found; all fixed.
- No developer tools or leftovers where people look (e.g. the Lynis entry in
  the app grid); the try-out session made obvious (Storage, About).
- Found so far:
  - Apps installed from outside Kulan OS (Claude Desktop, say) didn't show
    in the app grid; only search found them.
  - Apps that ask for desktop-wide shortcuts are refused without asking
    (the GlobalShortcuts portal).
  - In libadwaita forms (Kulan Fleet's Add a person), the line under each
    field stops halfway across: a theme glitch.
  - `lsb_release -d` says "Kulan OS" without the version.
  - Two notifications on the lock screen titled "gnome-settings-daemon":
    system notifications must carry a name people know. GNOME's own USB
    protection is on without USBGuard (Kulan Fleet controls USB drives);
    switch it off. Two media-key shortcuts (hibernate, repeat) fail to bind
    at every sign-in.
  - File and media sharing (GNOME's, linked from Sharing) listen on ports
    that change each time, so Kulan's firewall blocks them. Give them fixed
    ports the firewall allows from local networks, then put their switches
    on the Sharing page.
  - Status (27 Oct 2026):
    - Fixed: file and media sharing are on the Sharing page, per network,
      on ports 30301 and 30302 (gnome-user-share rebuilt to take a fixed
      port, ci/build-user-share.sh; Rygel's RYGEL_PORT), let in from local
      networks only; the Public folder always needs a password to change
      files (GNOME's default let anyone on the network change them).
    - Fixed: app folders list their apps by name, so apps installed later
      stay loose in the grid (they fell into Utilities by category); Files
      and Ask Kulan out of Utilities; htop, btop, Vim and Lynis hidden.
    - Fixed: GNOME's USB protection off; the hibernate and repeat keys
      no longer bound.
    - lsb_release -d: the 2.9c ISO says "Kulan OS 2.9c"; now checked in
      verify-iso.
    - Still to check by hand on the ThinkPad and in a VM (they need a real
      session): global shortcuts, the line under fields in libadwaita forms,
      and that no gnome-settings-daemon notification is left.
- Found on the ThinkPad T490 (28 Oct 2026, live session; Wi-Fi, sound,
  keys, touchpad, TrackPoint, camera and sleep all work):
  - The stick's start menu: the entries can't be seen in their box, and the
    countdown line is drawn over the Kulan OS logo.
  - The Wi-Fi password box uses GNOME's stock words ("Passwords or encryption
    keys are required…", the WPS line).
  - The clock was wrong when the desktop came up and wrong again after Wi-Fi
    connected (1:24 PM, then 8:31 PM).
  - Calamares sits in the app grid as "Advanced install" next to Install
    Kulan OS, and was the one opened; its welcome picture says "Version 2.8a"
    (drawn into welcome.png).
  - Install Kulan OS names Windows by its partition labels and formats
    ("KULANOS C: 10/28/2026, ntfs, vfat"); the Windows check looks for
    "Windows" in those names, so the Disk Management advice never shows.
  - "Restart now" on the last page didn't restart.
  - The installed system doesn't have the Wi-Fi network joined in the live
    session, and nothing says so.
  - An add-on install with no internet carried on after the package lists
    failed and showed apt's "Package 'whois' has no installation candidate".
  - Tor Browser's first start: its own old-style window and "Are you connected
    to the internet?" with no Kulan wording.
  - Status (29 Oct 2026): all fixed, with tests. The start menu used Debian's
    800x600 screen size; it now uses the screen's own size (checked at
    1920x1080 with Secure Boot from a USB stick, ci/boot-menu-test.sh). The
    restart froze on live-tools' hidden "remove the stick, press Enter"
    (noprompt; the boot test now restarts the live system). Advanced install
    is only in the installer's More choices; the installer says Windows,
    macOS or another Linux system; the live session's Wi-Fi is carried over;
    add-ons and Tor Browser say "not connected" plainly; the Wi-Fi password
    box has Kulan's words; the live session uses the installer's time zone
    guess (decided: no automatic time zone, for privacy); Ask Kulan names its
    own engine and says how full memory is.
- Kulan Fleet sign-in emails from kulangroup.com through our own mail
  service: a 6-digit code to type as well as the link, no low hourly limit,
  and the Supabase Site URL set to the dashboard.

## 1b. A new design for Kulan Fleet
- The web dashboard and the Fleet app get one shared design, so they read as
  one product. Today the dashboard is one long page of look-alike cards.
- Direction chosen (26 Oct 2026): A, "Console". Light, a sidebar of
  sections and the organisation switcher; a fleet health bar; tables with
  tabs for what needs attention; a computer's details beside the list, and a
  full page per computer (what needs attention with a button each, the
  compliance checks, recent requests, about, who uses it, lost or stolen).
  Kulan's teal, navy and amber; Manrope headings, Inter text. The mockups
  are on the "Kulan Fleet redesign" design canvas.
- Then both are rebuilt in it: navigation by section (Computers, People,
  Groups, Policy, Scripts, Activity, Settings), list and detail views,
  search, and the same words and icons on the web and in the app.
- Done (27 Oct 2026): the web dashboard (sections, fleet health, the
  computers' tabs and search, a computer's details beside the list, light and
  dark, phones) and the Fleet app (the same sections in the sidebar, fleet
  health, tabs and search, a computer's page with its health and quick
  buttons, light and dark). Both tests pass.

## 1c. Installing it should be easy
Today a new laptop takes a download, Etcher, the boot key, a long installer
and a join code afterwards. Agreed (26 Oct 2026) to cut it to: make the USB
stick, press the boot key, click Install.
- Kulan USB Maker, a small app for Mac and Windows: pick the stick, click
  Make. It downloads Kulan OS, checks it and writes the stick. (A web page
  can't write to a USB stick, so it has to be an app.)
- Sticks made for an organisation: "Make an installer" in Kulan Fleet gives
  a join code the USB Maker puts on the stick, and every computer installed
  from it joins Fleet by itself on its first start.
- An installer that asks almost nothing: the whole disk, encryption on,
  language and time zone guessed, one Install button. The account is made
  in the welcome app after the restart.
- The boot key: the USB Maker asks the laptop's brand and shows its key.
- Later (3.0 era): laptops sold with Kulan OS on them, installing over a
  business's network.
- Done so far (27 Oct 2026):
  - Kulan Installer is one screen: where, your name and one password (it
    signs you in and unlocks the encrypted disk), Install. The rest is
    guessed, under More choices. (Agreed: one password for both.)
  - Kulan USB Maker (usb-maker/, Tauri: Rust and a web page), unsigned for
    now (agreed): lists only USB and SD drives, downloads the newest Kulan
    OS and only uses it if its SHA-256 is in a SHA256SUMS signed with the
    update channel's key, writes it as an administrator (the partition
    table last, so nothing mounts half-written), reads it all back, and
    shows the boot key for the computer's make. Built and tested for real
    on Macs and Windows by .github/workflows/usb-maker.yml (a RAM disk and a
    virtual disk stand in for the stick).
  - Organisation sticks: a Kulan Fleet join code typed into the USB Maker
    goes in a small checked block after Kulan OS on the stick; the
    installer copies it to the new system, and kulan-fleet-autojoin joins
    once online (stopping when joined, refused, or after 14 days).
  - Still to do at release: put the USB Maker on the release, and the
    install guide's step 1 on the website.

## 2. Security
- An independent review of each part: the base system, Kulan Fleet (server,
  computers, dashboard), Ask Kulan, the installer, the update channel.
- Signed, measured start-up files (unified kernel images) so a few minutes
  with the computer can't tamper with /boot.
- A passphrase-protected update signing key, with a rotation plan.
- Vulnerability scans with nothing known left open; a published way to report
  security problems (security.txt, a security page).
- Status (27 Oct 2026): three independent reviews done (Kulan Fleet's server;
  the privileged code on the computer; updates, the build and Ask Kulan).
  Fixed:
  - Kulan Fleet: a computer that joins with a code from an admin other than
    the Owner waits for the Owner's approval, and gets no accounts, Wi-Fi or
    printers until then; join codes listed and withdrawn; Remove a computer;
    an admin's or the Owner's password or second step taken only from a
    computer they were signed in on; only the Owner moves an admin's or the
    Owner's account; limits on reports and request sizes; the dashboard under
    a Content Security Policy.
  - Scripts the Owner signed (agreed): computers run only scripts signed with
    the Owner's script signing code, which never reaches the server.
  - The update channel: computers take only Kulan's own packages from it;
    the signing key for main only, a locked backup and a yearly subkey
    (docs/signing-key.md; three steps for you to run on the Mac).
  - Smaller fixes on the computer, in the build and in Ask Kulan.
  - Signed start-up (agreed 28 Oct 2026, "signed, with one screen"): each
    computer's own key signs its start-up files (kernel, start-up image and
    options in one file); shim enrolls the key on a blue screen at the first
    restart, with a code the installer shows; then Debian's signed
    systemd-boot starts only the signed file, GRUB is removed, and TPM unlock
    is tied to it. New encrypted UEFI installs get it; others in Kulan
    Settings › Security. About 3 MB of tools, only where it's on. Not where
    the EFI partition is too small (some laptops shared with Windows).
    Tested end to end in a VM (enrolling, starting signed, TPM unlock, GRUB's
    updates). That test found GRUB's updates installing themselves in
    EFI/kulan (named after "Kulan OS") and taking over; Kulan's grub-install
    now keeps them in EFI/debian, and away while signed start-up is on.
  - Still to do: the steps in docs/signing-key.md and protecting main on
    GitHub (yours).

## 3. Reliability
- Sleep and wake; installing next to Windows; restoring a restore point;
  upgrades from every earlier release; computers with little memory.

## 4. Code a team would ship
- One style everywhere, enforced in CI (ruff for Python, shellcheck, eslint).
- Short, useful comments; no leftover notes or narration.
- A contributor guide and a changelog.

## 5. Speed
- Start-up time, memory use and app launch times measured and improved.

## 6. Release candidate
- A release-candidate build tried end to end before 2.10 is released.
