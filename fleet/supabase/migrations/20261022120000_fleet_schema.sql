-- Kulan OS fleet management (protocol v1). A private schema: not exposed through
-- the Data API; only the fleet Edge Function (direct Postgres connection) uses it.
-- RLS is on with no policies on purpose: the API roles get nothing.
create schema if not exists fleet;
revoke all on schema fleet from public, anon, authenticated;

create table fleet.organisations (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(name) between 1 and 80),
  created_at timestamptz not null default now()
);

-- One-time (or few-time) join codes; only their SHA-256 is kept.
create table fleet.enrolment_codes (
  code_hash text primary key,
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  uses_left integer not null default 1 check (uses_left >= 0),
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index enrolment_codes_organisation_idx on fleet.enrolment_codes (organisation_id);

create table fleet.devices (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  token_hash text unique,                 -- null once the computer has left
  hostname text,
  os text,
  kulan_version text,
  enrolled_at timestamptz not null default now(),
  last_seen timestamptz,
  left_at timestamptz,
  last_report jsonb
);
create index devices_organisation_idx on fleet.devices (organisation_id);

create table fleet.reports (
  id bigint generated always as identity primary key,
  device_id uuid not null references fleet.devices(id) on delete cascade,
  received_at timestamptz not null default now(),
  report jsonb not null
);
create index reports_device_time_idx on fleet.reports (device_id, received_at desc);

-- Requests to a computer: only the actions Kulan OS itself allows.
create table fleet.actions (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references fleet.devices(id) on delete cascade,
  type text not null check (type in ('refresh', 'install-updates', 'compliance-on', 'lock-screen', 'restart')),
  status text not null default 'pending' check (status in ('pending', 'sent', 'done', 'failed')),
  detail text,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  finished_at timestamptz
);
create index actions_pending_idx on fleet.actions (device_id) where status = 'pending';

alter table fleet.organisations enable row level security;
alter table fleet.enrolment_codes enable row level security;
alter table fleet.devices enable row level security;
alter table fleet.reports enable row level security;
alter table fleet.actions enable row level security;

-- Admin helper: a join code for an organisation, e.g.
--   select fleet.new_enrolment_code('<org id>', 72, 10);   -- 72 hours, 10 computers
-- Returns the code once; only its hash is stored. Not executable by the API roles.
create or replace function fleet.new_enrolment_code(org uuid, valid_hours integer default 72, uses integer default 1)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  code text := upper(substr(translate(encode(extensions.gen_random_bytes(12), 'base64'), '+/=0O1Il', ''), 1, 4)
                || '-' || substr(translate(encode(extensions.gen_random_bytes(12), 'base64'), '+/=0O1Il', ''), 1, 4)
                || '-' || substr(translate(encode(extensions.gen_random_bytes(12), 'base64'), '+/=0O1Il', ''), 1, 4));
begin
  insert into fleet.enrolment_codes (code_hash, organisation_id, uses_left, expires_at)
  values (encode(extensions.digest(code, 'sha256'), 'hex'), org, uses, now() + make_interval(hours => valid_hours));
  return code;
end;
$$;
revoke all on function fleet.new_enrolment_code(uuid, integer, integer) from public, anon, authenticated;
