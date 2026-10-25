-- Kulan Fleet 2.8b: groups of computers and people, a policy per group,
-- approved USB drives, and actions with a payload (install an app once).
-- Same private "fleet" schema (no Data API access, RLS on with no
-- policies): only the fleet and fleet-admin Edge Functions use it.

create table if not exists fleet.groups (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  name text not null check (char_length(btrim(name)) between 1 and 60),
  -- Same keys as the organisation's policy; fleet-admin checks them.
  policy jsonb not null default '{}'::jsonb check (jsonb_typeof(policy) = 'object' and pg_column_size(policy) < 16384),
  created_at timestamptz not null default now(),
  created_by text not null
);
create unique index if not exists groups_org_name_idx on fleet.groups (organisation_id, lower(name));
alter table fleet.groups enable row level security;
revoke all on fleet.groups from public, anon, authenticated;

-- The computers in a group.
create table if not exists fleet.group_devices (
  group_id uuid not null references fleet.groups(id) on delete cascade,
  device_id uuid not null references fleet.devices(id) on delete cascade,
  primary key (group_id, device_id)
);
create index if not exists group_devices_device_idx on fleet.group_devices (device_id);
alter table fleet.group_devices enable row level security;
revoke all on fleet.group_devices from public, anon, authenticated;

-- The people in a group (for finding them, and for "may use this group's
-- computers" below).
create table if not exists fleet.group_people (
  group_id uuid not null references fleet.groups(id) on delete cascade,
  person_id uuid not null references fleet.people(id) on delete cascade,
  primary key (group_id, person_id)
);
create index if not exists group_people_person_idx on fleet.group_people (person_id);
alter table fleet.group_people enable row level security;
revoke all on fleet.group_people from public, anon, authenticated;

-- A person allowed on every computer of a group (now and added later), as
-- well as on any computers chosen one by one.
create table if not exists fleet.person_groups (
  person_id uuid not null references fleet.people(id) on delete cascade,
  group_id uuid not null references fleet.groups(id) on delete cascade,
  primary key (person_id, group_id)
);
create index if not exists person_groups_group_idx on fleet.person_groups (group_id);
alter table fleet.person_groups enable row level security;
revoke all on fleet.person_groups from public, anon, authenticated;

-- USB drives allowed where a policy says "approved drives only".
create table if not exists fleet.approved_drives (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 80),
  vendor_id text not null check (vendor_id ~ '^[0-9a-f]{4}$'),
  product_id text not null check (product_id ~ '^[0-9a-f]{4}$'),
  serial text not null check (serial ~ '^[A-Za-z0-9._-]{4,64}$'),
  created_at timestamptz not null default now(),
  created_by text not null
);
create unique index if not exists approved_drives_idx on fleet.approved_drives (organisation_id, vendor_id, product_id, serial);
alter table fleet.approved_drives enable row level security;
revoke all on fleet.approved_drives from public, anon, authenticated;

-- What an action needs (install-app: which app).
alter table fleet.actions add column if not exists payload jsonb
  check (payload is null or (jsonb_typeof(payload) = 'object' and pg_column_size(payload) < 1024));
alter table fleet.actions drop constraint if exists actions_type_check;
alter table fleet.actions add constraint actions_type_check
  check (type in ('refresh', 'install-updates', 'compliance-on', 'lock-screen', 'restart', 'erase',
                  'shutdown', 'lost-mode-on', 'lost-mode-off', 'install-app'));

-- The organisation's policy may now hold the new keys too (usb, apps):
-- room for longer app lists.
alter table fleet.organisations drop constraint if exists organisations_policy_check;
alter table fleet.organisations add constraint organisations_policy_check
  check (jsonb_typeof(policy) = 'object' and pg_column_size(policy) < 16384);
