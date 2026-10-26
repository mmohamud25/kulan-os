# Kulan OS 2.7 plan

Agreed 24 October 2026: 2.7 is the three things from the 2.6.1
handover plus two small items. Everything else that was listed here moved to
2.8 (docs/plan-2.8.md) so 2.7 ships sooner. Nothing here reaches main or the
update channel until the maintainer says "release it".

## In 2.7
1. **The Ask Kulan app.** Its own window with a conversation (history kept
   on this computer), answering questions and doing things on the computer
   with Ask Kulan's actions and the instant answers. Changes still wait for
   "Do it" and can be undone. Still on hold: web answers and Somali.
2. **Kulan Store, with Kulan's own apps.** Apps from Flathub and Debian in
   one place, in Kulan's look, with Kulan's picks; installs and updates
   through the existing update services (no new ways in). Before installing,
   each app's permissions and whether its publisher is verified (required).
   Kulan's own apps listed too, starting with Kulan Fleet: Store listings
   (name, summary, icon, screenshot) in the packages, Kulan's download
   server publishing them (AppStream), and a build test that the Store
   finds and installs Kulan Fleet. Add-ons keeps listing it as well.
3. **Plain app names everywhere**: the dock, search and Settings match the
   short names the app grid got in 2.6.1.

Small items:
- Fleet follow-ups from 2.6: a short notice on the computer before an erase;
  send the counting week from the computer so a count near midnight on
  Sunday (UTC) can't be counted twice.
- Kulan Settings › About: a Graphics line (acceleration on or off), so a
  virtual machine without 3D is obvious.

## Order
Ask Kulan app → Kulan Store and Kulan Fleet's listing → plain names → the
small items → version, release notes, build and test.

## Status (24 October 2026, on branch kulan-os-2.7)
Done, each with tests: the Ask Kulan app (replaces "Do it for me"); the Store
(GNOME Software, named "Store", with Kulan's picks as Editor's
Choice and Kulan Fleet listed and installed from Kulan's channel; CI
installs it through GNOME Software and PackageKit); plain names (Terminal,
Disk Usage, Firewall; Software Update uses them); the erase notice; the
counting week; the 3D acceleration line. Version 2.7 with release notes.

To do at release (once approved): deploy the countme function
(fleet/supabase/functions/countme, now with week.ts) to Supabase. Either
order is safe: the server takes the old three fields, and 2.7 computers fall
back to them on a 400.

## Not doing yet
- Somali; web answers for Ask Kulan.
