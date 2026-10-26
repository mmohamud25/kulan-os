# Kulan fleet server

The cloud side of fleet management, on Supabase (project `kulan-fleet`,
us-east-2). Computers talk to it with `/usr/bin/kulan-fleet` (kulan-base).

- `supabase/functions/fleet/index.ts`: the Edge Function, protocol v1:
  `POST …/functions/v1/fleet/v1/{enrol,report,actions/<id>,leave}`. Each
  report is answered with waiting actions and the organisation's policy;
  once a day a report carries the computer's apps (names and versions).
- `supabase/functions/fleet-admin/index.ts`: for the Kulan Fleet app and the
  dashboard (signed-in admins only): overview with alerts, policy, apps,
  activity log, join codes and actions, including erase.
- `supabase/functions/countme/index.ts`: the anonymous weekly count of Kulan
  OS computers (`stats` schema; totals only).
- `supabase/migrations/`: the private `fleet` schema (organisations, join
  codes, devices, reports, actions). Not exposed through the Data API; RLS
  on with no policies, so the API roles can't read anything.

Server address for computers:
`https://xegnwqxpegznsufulclx.supabase.co/functions/v1/fleet`

Add an organisation and a join code (SQL editor):

    insert into fleet.organisations (name) values ('Kulan Cyber Advisory') returning id;
    select fleet.new_enrolment_code('<org id>', 72, 10);   -- valid 72 h, for 10 computers

Queue an action for a computer (refresh, install-updates, compliance-on,
lock-screen, restart):

    insert into fleet.actions (device_id, type) values ('<device id>', 'install-updates');

## Fleet v2 (Kulan OS 2.6)

- **Policy** per organisation (`fleet.organisations.policy`): `auto_updates`,
  `firewall`, `screen_lock_minutes` (1-60, a locked setting on the computer;
  compliance mode's stricter one wins) and `require_encryption` (reported
  only). Computers apply it at each report and say, per item, whether it's met.
- **Alerts** (worked out by fleet-admin from the last reports): not seen for 3
  days, security updates waiting, firewall off, not encrypted when required,
  disk under 10% free, compliance checks failing, a policy item not met.
- **Erase** a lost or stolen computer: only when its user allowed it on that
  computer (Kulan Settings › About › Organisation, or when joining) and its
  disk is encrypted; the admin types the computer's name to confirm. The
  computer destroys the encrypted partition's keys (`cryptsetup luksErase`)
  and switches off.
- **Activity log** (`fleet.audit`): who changed the policy, made join codes or
  asked a computer to do something, and when.
- Hourly reports older than 90 days are pruned.

## Secrets and Google Workspace (Kulan OS 2.8d, 2.8e)

- `FLEET_FACTOR_KEY` (32 random bytes, base64): seals people's phone-code
  secrets (2.8d) and organisations' Google service account keys (2.8e).
  Never change or lose it: sealed data can't be opened without it.
- Google Workspace (`supabase/functions/fleet-admin/google.ts`,
  `fleet.mail_connections`): the Owner connects a service account with
  domain-wide delegation for `admin.directory.user`; mailboxes are made,
  suspended, renamed and deleted with People. The guide for organisations
  is `docs/fleet-google-workspace.md`. Fleet's own change never waits for
  Google. What's left is kept on the person (`mailbox_todo`) as an alert.

## Remote help (Kulan OS 2.9a)

- `functions/*/help.ts` and `fleet.help_sessions`: an admin asks to see a
  computer's screen and the person signed in there is asked. Only if they
  allow it does the screen go to the admin's browser, over WebRTC.
- When a direct connection isn't possible, Cloudflare's relay is used, with
  credentials made per session from the `CLOUDFLARE_TURN_KEY_ID` and
  `CLOUDFLARE_TURN_TOKEN` secrets. Without them, only STUN is used.
- Requests wake their computers through a Realtime broadcast topic per
  computer (`fleet.devices.wake_topic`).

## Actions (Kulan OS 2.9b)

- Fixes (`run-fix`): Kulan's own, listed in `functions/*/actions.ts` and in
  the computers' `fixes.py`. Scripts (`run-script`, `fleet.scripts`): an
  organisation's own bash scripts, only while its Owner allows them
  (`organisations.allow_scripts`), and changed or run only by admins who gave
  their authenticator app's code. A request carries the script's SHA-256; the
  computer fetches the script for that request (`/v1/scripts/{action id}`)
  and runs it only if it still matches. Output (the first 64 KB) and the exit
  code are kept on the action for 90 days (`fleet.prune_action_outputs()`).
- Messages (`message`, and `message-person` for every computer a person is
  signed in on).
- Group Wi-Fi networks and printers (`fleet.group_networks`,
  `fleet.group_printers`): passwords are sealed with `FLEET_FACTOR_KEY`,
  bound to the group, and opened only into the reply to that group's
  computers. Computers report how each went (`pushed` in their report).

## Dashboard

`dashboard/` is a static page, published with the website at
os.kulangroup.com/fleet/. Before 2.9a it was published on GitHub Pages from the
`kulan-fleet-dashboard` repository. It signs in with Supabase Auth (an emailed link)
and talks only to the `fleet-admin` function, which allows emails listed in
`fleet.admins`:

    insert into fleet.admins (email) values ('admin@example.com');                 -- every organisation
    insert into fleet.admins (email, organisation_id) values ('it@example.com', '<org id>');

In Supabase > Authentication > URL Configuration, the dashboard's address
must be the Site URL (or listed under Redirect URLs) for sign-in links.
