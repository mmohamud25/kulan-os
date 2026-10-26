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
