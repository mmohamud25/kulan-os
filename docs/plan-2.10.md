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
