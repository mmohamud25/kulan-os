-- Kulan Fleet 2.9b: fixes and scripts run on computers, messages to people's
-- screens, and Wi-Fi networks and printers given to a group's computers.
-- Same private "fleet" schema (no Data API access, RLS on with no policies):
-- only the fleet and fleet-admin Edge Functions use it.

alter table fleet.actions drop constraint if exists actions_type_check;
alter table fleet.actions add constraint actions_type_check
  check (type in ('refresh', 'install-updates', 'compliance-on', 'lock-screen', 'restart', 'erase',
                  'shutdown', 'lost-mode-on', 'lost-mode-off', 'install-app', 'remote-help',
                  'run-fix', 'run-script', 'message'));

-- What a fix or script printed, and how it ended (the first 64 KB).
alter table fleet.actions add column if not exists output text
  check (output is null or octet_length(output) <= 65536);
alter table fleet.actions add column if not exists exit_code integer;

-- An organisation's own scripts run only once the Owner allows them.
alter table fleet.organisations add column if not exists allow_scripts boolean not null default false;

-- The organisation's scripts (bash). A request carries the script's SHA-256:
-- the computer runs it only if the script it fetches still matches.
create table if not exists fleet.scripts (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  name text not null check (char_length(btrim(name)) between 1 and 80),
  description text not null default '' check (char_length(description) <= 300),
  body text not null check (octet_length(body) between 1 and 65536),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  created_by text not null,
  updated_at timestamptz not null default now(),
  updated_by text not null
);
create unique index if not exists scripts_org_name_idx on fleet.scripts (organisation_id, lower(name));
alter table fleet.scripts enable row level security;
revoke all on fleet.scripts from public, anon, authenticated;

-- Wi-Fi networks a group's computers get. The password is sealed (AES-GCM,
-- a key derived from FLEET_FACTOR_KEY, bound to the group) and opened only to
-- send it to the group's computers; admins never see it again.
create table if not exists fleet.group_networks (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references fleet.groups(id) on delete cascade,
  ssid text not null check (octet_length(ssid) between 1 and 32),
  security text not null check (security in ('open', 'wpa-psk')),
  secret text check (secret is null or char_length(secret) <= 400),
  hidden boolean not null default false,
  created_at timestamptz not null default now(),
  created_by text not null,
  check ((security = 'open') = (secret is null))
);
create unique index if not exists group_networks_ssid_idx on fleet.group_networks (group_id, ssid);
alter table fleet.group_networks enable row level security;
revoke all on fleet.group_networks from public, anon, authenticated;

-- Printers a group's computers get (driverless: IPP Everywhere).
create table if not exists fleet.group_printers (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references fleet.groups(id) on delete cascade,
  name text not null check (char_length(btrim(name)) between 1 and 60),
  uri text not null check (char_length(uri) <= 300 and uri ~ '^(ipps?|https?)://[A-Za-z0-9.:\[\]_-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/%+-]*)?$'),
  created_at timestamptz not null default now(),
  created_by text not null
);
create unique index if not exists group_printers_name_idx on fleet.group_printers (group_id, lower(name));
alter table fleet.group_printers enable row level security;
revoke all on fleet.group_printers from public, anon, authenticated;

-- Outputs are kept 90 days (the request itself stays in the log).
create or replace function fleet.prune_action_outputs()
returns void
language sql
security invoker
set search_path = ''
as $$
  update fleet.actions set output = null
   where output is not null and created_at < now() - interval '90 days';
$$;
revoke all on function fleet.prune_action_outputs() from public, anon, authenticated;
