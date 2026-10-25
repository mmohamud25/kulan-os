# Kulan OS 2.8 plan

2.8 is Kulan Fleet, all of it (agreed with Mohamud, 24 October 2026): the
roadmap in docs/fleet-roadmap.md, released in steps 2.8a, 2.8b, ... until it
is done, instead of one Fleet stage per version number. Each step is tested
fully and reaches main and the update channel only when he says "release
it". Other work stays in docs/plan-2.9.md.

## The steps
Shown as 2.8a, 2.8b... in Kulan Settings and on the website; inside, the
packages are 2.8.1, 2.8.2... (apt versions and the weekly count take plain
numbers; the count still says 2.8).
- 2.8a People: accounts, setup codes, roles with the Owner and recovery
  code, disable, remove; Shut down and Lost mode; in the Kulan Fleet app
  and the web dashboard. (Built and tested; below.)
- 2.8b Groups and policies: groups of people and computers; a policy per
  group on top of the organisation's (the stricter rule wins); USB storage
  blocked, or approved drives only; required and blocked apps; install an
  app on a group once.
- 2.8c Security, activity and computers: a weekly security report;
  "who still has admin rights?" reviews; the activity log exported for
  auditors; who uses which computer.
- 2.8d Stronger sign-in: two-step sign-in on the computers (phone code or
  security key); security keys for the Owner and admins in Kulan Fleet.
- 2.8e Organisation: Google Workspace mailboxes made (and suspended) with
  People through the Directory API, and the email connection settings,
  built so Kulan's own mail server can take Google's place later.
Not in 2.8: the Work edition (Fleet set up while Kulan OS installs) stays
3.0; single sign-on to other apps stays 3.x.

Every rule is off until the organisation turns it on, and only computers
that joined an organisation (with the person seeing what's shared) are
affected; leaving removes every rule.

## 2.8a People in Kulan Fleet (agreed 24 October 2026)
Mohamud's employees use Kulan OS laptops with accounts managed in Kulan
Fleet: an Entra ID and Intune of Kulan's own, for Kulan OS. Email stays on
Google Workspace (Business Starter); his own mail server comes later.
- People in Kulan Fleet: name, username, role (employee or admin) and which
  computers (all of the organisation's, or chosen ones).
- First sign-in: their name is on the sign-in screen of every computer they
  may use; a one-time setup code (from Fleet, handed over by the admin), then
  they choose their own password.
- The password follows them: Fleet keeps only a one-way hash (yescrypt) and
  sends it only to the computers they may use, so sign-in works offline; a
  change on one computer reaches the others at their next report. Safe
  because Fleet requires encrypted disks.
- Roles: Owner (one; only the Owner adds or removes admins or deletes the
  organisation; a recovery code printed when the organisation is made),
  admins (administrator rights on the computers, manage people and
  computers) and employees (standard accounts).
- Leaving: disable in Fleet and their account is locked everywhere at the
  next report (within the hour; computers only hear from Fleet when they
  report); the admin keeps their files for handover or removes them.
- Every change (created, disabled, made admin) in the activity log, with who
  did it.
- Google Workspace mailboxes: moved to 2.8e.
- Tested with two VMs, then a real laptop (separate from the UTM VM).

Not doing for now: Google Secure LDAP sign-in (needs Business Plus);
Fleet accounts take its place.

## How People works (design)
Server (Supabase, private fleet schema):
- fleet.people: organisation, username, full name, uid (70000-79999, given
  by the server so a person has the same uid on every computer), role
  (owner, admin, employee), enabled, password_hash, must_change,
  password_changed_at, all_computers; fleet.person_computers for chosen
  computers. The Owner is a person with role owner; only one per
  organisation.
- Each report's reply carries "accounts": the people allowed on that
  computer (never anyone else's hash).
- A report may carry "passwords": hashes changed on that computer (after a
  first sign-in or a password change); the newest wins.
Setup codes are made in the Kulan Fleet app, never on the server: the app
makes the code, hashes it (yescrypt, else SHA-512 crypt) and sends only the
hash with must_change. The admin sees the code once to hand over.
On each computer (kulan-fleet report, as root):
- Create: useradd with the server's uid, the hash, and "change at first
  sign-in" (chage -d 0), so typing the setup code at the sign-in screen
  asks for a new password at once. Admins join the sudo group.
- Update: name, role, a newer hash from another computer.
- Disable: lock the password, expire the account, end their sessions.
- No longer allowed here: account removed; files moved to
  /home/.kulan-left/<user>-<date> (root only) unless the admin chose to
  delete them.
- Never touches accounts Fleet didn't make; a username already used by
  someone else on that computer is left alone and reported.
- Local password changes are noticed in /etc/shadow and sent with the next
  report.
Kulan Fleet app: a People page (add, edit role and computers, disable,
re-enable, remove with or without files, new setup code), Owner-only admin
changes, every change in the activity log.

## Small Fleet additions
- Shut down, with the same 5-minute notice as Restart.
- Lost mode: nobody can use the computer until an admin releases it
  (between Lock and Erase). Built on /etc/nologin (GDM and the console
  honour it, checked on the ISO) with a sign-in screen banner; anyone
  signed in is signed out after a 20-second notice; it keeps reporting.
- Already on this branch: the countme server's reply() fix and its test.

## Built so far (branch kulan-os-2.8)
- Computer: people.py (accounts, setup code, passwords both ways by
  comparing hashes rather than clocks, roles, disable, remove), shut down
  and lost mode in kulan-fleet.
- Server: migration 20261024120000_fleet_people.sql; fleet (accounts in
  each reply, passwords taken only from a computer the person may use)
  and fleet-admin (People, admins, the Owner, recovery code). uids
  70000-79999: clear of Debian's local accounts, systemd's ranges and
  container subuids.
- Kulan Fleet app: People page, person page, add (setup code made and
  hashed on the admin's computer, shown once), admins and the Owner,
  recovery, shut down and lost mode.
- Web dashboard: People, admins and the Owner, recovery, shut down and
  lost mode; setup codes hashed in the browser (sha512crypt.js, matches
  openssl).
- Tests: fleet/test against real Postgres (Fleet server workflow), the
  package tests, the app's self-test, People across two computers
  (ci/fleet-two-computers.sh, required before publishing), the test
  server set up and removed in CI. fleet/test-server runs the real
  functions on a test computer for trying it on VMs.
- Still to see by eye: the sign-in screen with a setup code, on a VM or
  the test laptop (a used ThinkPad T490).
- At release, with Mohamud's go-ahead: apply the migration, deploy fleet
  and fleet-admin, then release the packages (old computers ignore the
  new reply fields).
