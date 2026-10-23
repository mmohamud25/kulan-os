# Kulan OS 2.5 plan

Agreed with Mohamud, October 2026. Nothing here reaches main or the update
channel until he says "release it". 2.4.1 (minimal Software Update, website)
ships first.

## Public launch
- Website live at https://os.kulangroup.com: Home, Download, Install guide,
  What's new (one anchor per version, `#v2.5`), Help; Security, Privacy,
  Contact (support@ and security@kulangroup.com) in the footer.
- ISOs on GitHub Releases (no login, no expiry); download buttons always
  point at the newest version. Real screenshots from CI (site-screenshots).

## Fleet v2 (for Mohamud's own computers first)
- Policies: screen lock, firewall, automatic updates, compliance mode.
- Apps and inventory: what's installed; install or remove from Fleet.
- Lock and wipe for lost or stolen computers.
- Alerts, an action log, and roles for helpers.

## Ask Kulan (built in, like Siri; no separate app in the dock)
- Shift+Cmd+Space, a top-bar icon, or "Ask Kulan" as a search result.
- Ask (answer) or Do it (plan, Do it, Undo); mic and screen-text buttons.
- Short answers stay in the bar; longer chats open a small Kulan Assistant
  window with history.
- Right-click entries in Files and Kulan's apps: Summarise, Rewrite, Ask
  Kulan about this.
- Runs on the computer: no internet, no cost. No web search or browsing
  (Mohamud: wait on that).

## Assistant actions (each shows a plan, Do it, Undo)
- Files: tidy Downloads into folders, rename photos by date, find
  duplicates and large files, find files by description.
- Time: reminders, timers and alarms, to-do list and calendar entries,
  Focus for a while.
- Fixing problems: Wi-Fi, sound, Bluetooth, printer, dim screen, slow
  computer (check likely causes, explain, offer the fix).
- Also: free up space, check and install updates, set up backups, battery
  and power settings, explain an error on screen.

## Search
- One search for apps, files, settings and sums: Cmd+Space or typing in the
  app grid. Results grouped: Top hit, Apps, Files, Settings, Ask Kulan.
- The dock stays as it is (no Start button; the app-grid button stays).

## Kulan Settings
- New native Printers (CUPS) and Sharing (remote desktop, files, media) pages.
- Online accounts keeps opening GNOME's page (Google and Microsoft sign-in
  only work through approved apps).

## Speed and smoothness
- CI measures time to the desktop and time to open Files, Settings,
  Terminal and Firefox; a slower build fails.
- Warm the most-used apps into memory after sign-in, while idle.
- GNOME 48's smoother frame drawing; check animations don't drop frames.
- Start printing, modem and firmware services on demand, not at boot.
- Kulan Settings and Software Update build the asked-for page first.
- Performance when plugged in, efficient on battery, automatically.

## Terminal
- fzf (Ctrl+R), ripgrep, fd, bat, eza, zoxide, btop, tldr.
- A clean Kulan prompt (folder, Git branch, last command's result).
- `kulan` command: update, doctor, info, settings.
- `kulan ask`, and one key to explain a failed command, on the computer.
- Missing-command help (which package, offer to install).
- Distrobox for other distributions' tools.
- Names stay plain: Files, Terminal.

## TPM unlock (held back in 2.4)
- CI logs (ci-diagnostics, run 39840064431): after `kulan-tpm on` the
  test machine never restarted: no "reboot: Restarting system", no console
  output, black screen. Find why the restart hangs (the setup step calls
  `systemctl reboot` from a boot-time oneshot after installing dracut),
  then confirm the disk opens by the TPM. Turn it on for users, and make
  the CI TPM test blocking again, only when it passes.

## Undecided
- The assistant's name: Kulan Assistant, or something like Caawiye.

## Status (23 October 2026)
Proposed to Mohamud (not confirmed yet): 2.5 is "Ask Kulan, and everything underneath made
solid". Fleet v2 and the rest of the assistant actions move to 2.6.

Done on kulan-os-2.5 (tested in CI, waiting for "release it"):
- Ask Kulan panel (Shift+Cmd+Space) and service; actions: tidy Downloads or
  the Desktop, reminders (listed and cancellable in Settings › Assistant),
  fix Wi-Fi, fix sound, check storage; answers from this computer's facts.
- Inspection of every app and the system (Oct 23), fixed:
  - updates published only after every test; interrupted updates finished
    first; space checked; lock waited for; restore point before automatic
    updates; Undo warns about later updates; restart notice after
    automatic updates; one update count everywhere; firmware updates (fwupd)
  - a failing desktop setup step no longer blocks apt
  - TPM unlock with a start-up PIN (recommended), tested in CI with a PIN
  - assistant: readiness check with a button everywhere, no frozen window
  - installer: next-to-Windows explained, keyboard test, background disk
    scan, honest failures, failed partitions removed, Try Again keeps answers
  - search "No results"; Fleet stays signed in offline; Setup Assistant
    handoffs and claims; Hibernate only when possible; Live Text languages
- Speed: apps warmed after sign-in; CI speed budget.
- Terminal: power tools, kulan command, Alt+K, missing-command help.
- Printers page; one search.

Still to do for 2.5: trim the PC ISO under ~1.8 GB (measuring); review the
real-desktop screenshots of every Settings page.

Proposed for 2.6: Fleet v2; more assistant actions (photos by date, duplicates,
timers, to-dos, Bluetooth/printer fixes, backups); Sharing page; NVIDIA
driver add-on with Secure Boot signing; Broadcom wl; an upgrade test from
the previous release on an installed VM; sleep/wake and dual-boot tests;
Valid-Until on the update channel (needs regular re-signing).
