-- Kulan Fleet 2.9c: Get help. Someone at a computer writes what's wrong in
-- Kulan Settings (and may add a screenshot they saw first); it reaches the
-- organisation's admins, who can start remote help from it and mark it
-- done, with a short answer that goes back to that person's screen.
-- Same private "fleet" schema (no Data API access, RLS on with no policies):
-- only the fleet and fleet-admin Edge Functions use it.
--
--   username    the account that asked, as the computer says (it only sends
--               the signed-in person who pressed Send)
--   screenshot  a JPEG or PNG, at most 360 KB; removed after 30 days
--   state       open | done
--   reply       the admin's answer, shown to the person (optional)
create table if not exists fleet.help_requests (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  device_id uuid not null references fleet.devices(id) on delete cascade,
  username text not null check (username ~ '^[a-z_][a-z0-9_.-]{0,31}$'),
  full_name text not null default '' check (char_length(full_name) <= 80),
  body text not null check (char_length(btrim(body)) between 1 and 2000),
  screenshot bytea check (screenshot is null or octet_length(screenshot) <= 368640),
  screenshot_type text check (screenshot_type in ('image/jpeg', 'image/png')),
  state text not null default 'open' check (state in ('open', 'done')),
  reply text check (reply is null or char_length(reply) <= 500),
  created_at timestamptz not null default now(),
  done_at timestamptz,
  done_by text,
  check ((screenshot is null) = (screenshot_type is null)),
  check ((state = 'done') = (done_at is not null))
);
create index if not exists help_requests_org_idx on fleet.help_requests (organisation_id, state, created_at desc);
create index if not exists help_requests_device_idx on fleet.help_requests (device_id, created_at desc);
alter table fleet.help_requests enable row level security;
revoke all on fleet.help_requests from public, anon, authenticated;

-- Screenshots are kept 30 days, requests a year (what was asked and done is
-- in the activity log for good).
create or replace function fleet.prune_help_requests()
returns void
language sql
security invoker
set search_path = ''
as $$
  update fleet.help_requests set screenshot = null, screenshot_type = null
   where screenshot is not null and created_at < now() - interval '30 days';
  delete from fleet.help_requests where created_at < now() - interval '365 days';
$$;
revoke all on function fleet.prune_help_requests() from public, anon, authenticated;
