# Kulan OS 2.8 plan

One headline, agreed with Mohamud on 24 October 2026: People in Kulan Fleet
(Fleet accounts). Everything else that was here moved to docs/plan-2.9.md.
The whole Fleet roadmap: docs/fleet-roadmap.md. Nothing here reaches main or
the update channel until he says "release it".

## People in Kulan Fleet (agreed 24 October 2026)
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
  next report (within the hour) or at once with "Lock now"; the admin keeps
  their files for handover or removes them.
- Every change (created, disabled, made admin) in the activity log, with who
  did it.
- Optional, same form: also create (and later suspend) their Google
  Workspace mailbox through Google's Directory API (works on Business
  Starter; one-time setup in the Google Admin console). Built so another
  mail service can plug in instead (Stalwart or Migadu, both have an API)
  when Kulan has its own mail server.
- Tested with two VMs, then a real laptop (separate from the UTM VM).

Not doing for now: Google Secure LDAP sign-in (needs Business Plus);
Fleet accounts take its place.

## Small Fleet additions
- Shut down, with the same 5-minute notice as Restart.
- Lost mode: nobody can use the computer until an admin releases it
  (between Lock and Erase).
- Already on this branch: the countme server's reply() fix and its test.

