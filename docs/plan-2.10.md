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
