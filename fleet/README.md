# Kulan fleet server

The cloud side of fleet management, on Supabase (project `kulan-fleet`,
us-east-2). Computers talk to it with `/usr/bin/kulan-fleet` (kulan-base).

- `supabase/functions/fleet/index.ts`: the Edge Function, protocol v1:
  `POST …/functions/v1/fleet/v1/{enrol,report,actions/<id>,leave}`.
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

The dashboard (Vercel) comes later and will use the same tables.
