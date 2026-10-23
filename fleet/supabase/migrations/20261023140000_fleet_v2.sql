-- Kulan OS fleet management v2: policies, app inventory, erase, alerts and an
-- activity log. Same private "fleet" schema (no Data API access, RLS on with
-- no policies): only the fleet and fleet-admin Edge Functions use it.

-- What an organisation asks of its computers. Validated by fleet-admin:
--   auto_updates bool, firewall bool, require_encryption bool,
--   screen_lock_minutes int (0 = not set, 1-60)
alter table fleet.organisations
  add column if not exists policy jsonb not null default '{}'::jsonb
    check (jsonb_typeof(policy) = 'object' and pg_column_size(policy) < 2048);

-- The apps on each computer (sent once a day), kept apart from the hourly reports.
alter table fleet.devices
  add column if not exists apps jsonb check (apps is null or jsonb_typeof(apps) = 'array'),
  add column if not exists apps_at timestamptz;

-- Who asked for each action (the admin's email), and erasing as an action.
alter table fleet.actions add column if not exists requested_by text;
alter table fleet.actions drop constraint if exists actions_type_check;
alter table fleet.actions add constraint actions_type_check
  check (type in ('refresh', 'install-updates', 'compliance-on', 'lock-screen', 'restart', 'erase'));

-- Everything admins change, for the activity log.
create table if not exists fleet.audit (
  id bigint generated always as identity primary key,
  organisation_id uuid references fleet.organisations(id) on delete cascade,
  device_id uuid references fleet.devices(id) on delete set null,
  email text not null,
  what text not null check (char_length(what) between 1 and 80),
  detail jsonb,
  at timestamptz not null default now()
);
create index if not exists audit_organisation_time_idx on fleet.audit (organisation_id, at desc);
create index if not exists audit_device_idx on fleet.audit (device_id);
alter table fleet.audit enable row level security;
revoke all on fleet.audit from public, anon, authenticated;

-- Keep the hourly reports from growing without end: 90 days is plenty.
create or replace function fleet.prune_reports()
returns void
language sql
security invoker
set search_path = ''
as $$
  delete from fleet.reports where received_at < now() - interval '90 days';
$$;
revoke all on function fleet.prune_reports() from public, anon, authenticated;

-- An action can be cancelled while it waits, and an erase that waits more than
-- 30 days expires (a lost computer found again doesn't erase itself weeks later).
alter table fleet.actions drop constraint if exists actions_status_check;
alter table fleet.actions add constraint actions_status_check
  check (status in ('pending', 'sent', 'done', 'failed', 'cancelled', 'expired'));
